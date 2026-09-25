"""How long will he actually play? -- the one thing worth guessing.

WHY THIS EXISTS
Every projection in this app used to be a season per-game average. That
average silently assumes tonight looks like the average night, and the
biggest way a night differs is minutes: a starter on 34 and the same
starter on 22 are two different players.

Measured on the honest 70,944-game backtest population
(minutes_model_sweep.py, 19 Sep 2026), against the season average:

    knowing his ACTUAL minutes      points error -14.0%
    projecting minutes in advance   points error  -0.9%, direction
                                    50.5% -> 53.3% (balanced 51.0 -> 54.1)

So the error barely moves -- most of the value of minutes is locked
behind knowing them -- but the DIRECTION of the call improves by about
three points, and direction is what a reader comparing a projection to a
line actually cares about.

WHY NOT JUST RECENT FORM
The sweep tested a control: blend the player's recent per-game stat line
into the average, no minutes involved. On raw direction it looks better
(54.2% vs 53.3%). On BALANCED accuracy it is worse (53.7% vs 54.1%), and
on error much worse (-0.57% vs -0.9%). Recent form shifts predictions
toward whatever just happened, which flatters raw direction through the
base rate rather than through skill. The minutes model wins once that is
corrected for, which is why this module exists and a form-blend one does
not.

WHAT IT COMPUTES
    mpg      = mean minutes over prior played games this season
    recent   = mean minutes over the last RECENT_WINDOW played games
    projected = WEIGHT * recent + (1 - WEIGHT) * mpg
    rate_s   = total of stat s over prior games / total minutes
    line_s   = rate_s * projected

Config is min_3_0.5, chosen leave-one-season-out on pooled relative MAE.

WHAT IT DOES NOT DO
It knows nothing about tonight: no injury report, no rest, no blowout
risk, no starter/bench change. It is a read on a player's recent
workload, not a forecast of his role. A player whose minutes are about
to change for a reason the log cannot see will be projected wrong, and
confidently so.

It also does not clear the vig. 53.3% direction is below the 53.5%
break-even at -115. This is a better number, not a winning one, and
nothing in the app should imply otherwise.

ONE KNOWN SEAM
The model was fitted on box-score minutes, which are accurate to the
second (27.783), and runs live on game-log minutes, which the NBA
returns as whole numbers (28). Measured over the 29,914 games both
harnesses cover, that rounding moves the projection by a median of 0.18%
and 0.55% at the 95th percentile -- far inside the noise of the thing
being projected, and not worth "fixing" by throwing away the more
accurate source on the fitting side.
"""

import pandas as pd

# min_3_0.5, chosen leave-one-season-out on pooled relative MAE across
# nine stats. See minutes_model_sweep_results.csv for the alternatives.
RECENT_WINDOW = 3
RECENT_WEIGHT = 0.5

# Below this many prior games the rate and the recent window are both
# too thin to lean on, and the plain average is the honest answer. Same
# threshold the calibrated distributions use.
MIN_PRIOR_GAMES = 5

MINUTES_COLUMN = "MIN"
DATE_COLUMN = "GAME_DATE"


def _played(df):
    """Prior games he actually played, newest first.

    Sorted here rather than trusted from the caller: app.py hands over a
    newest-first log and the backtest hands over whatever order the cache
    had, and a silent disagreement about ordering would quietly turn
    "his last three games" into "his first three"."""
    if df is None or len(df) == 0 or MINUTES_COLUMN not in df.columns:
        return None
    out = df.copy()
    out[MINUTES_COLUMN] = pd.to_numeric(out[MINUTES_COLUMN], errors="coerce")
    out = out[out[MINUTES_COLUMN] > 0]
    if len(out) == 0:
        return None
    if DATE_COLUMN in out.columns:
        parsed = pd.to_datetime(out[DATE_COLUMN], errors="coerce")
        if parsed.notna().all():
            out = out.assign(_d=parsed).sort_values("_d", ascending=False).drop(columns="_d")
    return out.reset_index(drop=True)


def projected_minutes(df):
    """Minutes to project for the next game, or None when the log is too
    thin to say. Half his recent workload, half his season."""
    played = _played(df)
    if played is None or len(played) < MIN_PRIOR_GAMES:
        return None
    minutes = played[MINUTES_COLUMN]
    mpg = float(minutes.mean())
    recent = float(minutes.head(RECENT_WINDOW).mean())
    return RECENT_WEIGHT * recent + (1.0 - RECENT_WEIGHT) * mpg


def why_not(df, stat_columns):
    """Why the minutes model can't be used here, or None when it can.

    This exists because the first deploy of it shipped a page that
    explained a per-minute rate underneath numbers that were still flat
    averages: the model declined silently and the prose carried on
    regardless. A silent fallback behind confident prose is worse than
    no model at all, so the reason is now a value the page can read and
    refuse to lie about."""
    played = _played(df)
    if played is None:
        return "no usable minutes in the game log"
    if len(played) < MIN_PRIOR_GAMES:
        return f"only {len(played)} played games (needs {MIN_PRIOR_GAMES})"
    if float(played[MINUTES_COLUMN].sum()) <= 0:
        return "no minutes recorded"
    missing = [col for col, _ in stat_columns if col not in played.columns]
    if missing:
        return f"game log is missing {', '.join(missing)}"
    projected = projected_minutes(df)
    if projected is None or projected <= 0:
        return "projected minutes came out at zero"
    return None


def minutes_aware_means(df, stat_columns, minutes_override=None):
    """{stat: projected mean} from per-minute rates times projected
    minutes, or None when the log can't support it (why_not says why).

    Returns None rather than falling back internally, so the caller
    decides what the fallback is and the fallback stays visible in one
    place instead of two.

    minutes_override replaces the projected minutes and NOTHING else.
    The per-minute rates still come from his real games; only the
    number they are multiplied by changes. That is the whole reason
    this is a safe control to hand a reader: the worst they can do is
    be wrong about a rotation, which they are often better placed to
    know than we are (preseason, a back-to-back, a blowout, a minutes
    restriction we have no feed for).

    why_not() still governs. An override does not rescue a log too thin
    to give rates -- there would be nothing to multiply. A reader who
    sets minutes on a player with four games has still told us nothing
    about his per-minute production.
    """
    if why_not(df, stat_columns) is not None:
        return None
    played = _played(df)
    total_minutes = float(played[MINUTES_COLUMN].sum())
    projected = projected_minutes(df)
    if minutes_override is not None:
        projected = float(minutes_override)

    means = {}
    for col, _label in stat_columns:
        total = pd.to_numeric(played[col], errors="coerce").sum()
        means[col] = float(total) / total_minutes * projected
    return means


def spread_at_minutes(df, stat_columns, minutes):
    """{stat: std of the outcome} when the minutes are KNOWN to be
    `minutes`, or None when the log can't support it.

    WHY THIS EXISTS, WHICH IS A CORRECTION
    The first version of the minutes override kept the player's raw
    per-game standard deviation and moved only the mean, on the
    reasoning that he is no more consistent because somebody told us his
    minutes. That is half right and stops thinking too early.

    A per-game spread is the spread of games he played for HIS USUAL
    LENGTH. Keep it whole while halving the mean and the range stops
    describing basketball: a 27-point scorer set to 20 minutes came out
    at 15.5 with an 80% range of 3 to 32, and 32 points in 20 minutes is
    1.6 points per minute against an elite rate of about 0.9. The top
    half of that range was games that cannot happen.

    The mistake was treating "don't narrow the range" as automatically
    the honest choice. Asserting minutes removes a real source of
    variation -- how long he plays -- and refusing to reflect that
    manufactures uncertainty just as surely as narrowing without cause
    would manufacture confidence.

    THE MODEL, STATED AS A MODEL
    Points accumulate over time, so the natural assumption is that
    variance grows with minutes: var(stat | m minutes) = sigma^2 * m.
    sigma^2 is estimated from the residuals of the rate model this
    module already uses -- e_i = stat_i - rate * minutes_i -- as
    sum(e_i^2) / sum(minutes_i), which weights a long game more than a
    short one without a threshold anywhere.

    This is an assumption and has NOT been backtested. It is chosen
    because it is the standard model for something counted over an
    interval, because it degrades sensibly (at his usual minutes it
    lands near his real per-game spread), and because it is exact in the
    one case where the answer is knowable: a player whose rate never
    varies has no uncertainty left once his minutes are fixed. Whoever
    backtests the override should check this first. Until then the page
    does not claim the 80% calibration on an overridden projection --
    that figure was measured with the model's own minutes.
    """
    if why_not(df, stat_columns) is not None or minutes is None:
        return None
    played = _played(df)
    minutes_series = pd.to_numeric(played[MINUTES_COLUMN], errors="coerce")
    total_minutes = float(minutes_series.sum())
    if total_minutes <= 0:
        return None

    spreads = {}
    for col, _label in stat_columns:
        values = pd.to_numeric(played[col], errors="coerce").fillna(0.0)
        rate = float(values.sum()) / total_minutes
        residuals = values - rate * minutes_series
        sigma_sq = float((residuals ** 2).sum()) / total_minutes
        spreads[col] = (sigma_sq * float(minutes)) ** 0.5
    return spreads


def describe(df):
    """(projected, mpg, recent) for the page to show its working, or
    None. A reader who can see the minutes assumption can argue with
    it, which is the whole point of showing any of this."""
    played = _played(df)
    if played is None or len(played) < MIN_PRIOR_GAMES:
        return None
    minutes = played[MINUTES_COLUMN]
    mpg = float(minutes.mean())
    recent = float(minutes.head(RECENT_WINDOW).mean())
    return {
        "projected": RECENT_WEIGHT * recent + (1.0 - RECENT_WEIGHT) * mpg,
        "mpg": mpg,
        "recent": recent,
        "window": RECENT_WINDOW,
        "games": len(played),
    }
