"""How often does a player in this spot barely play? A measured rate.

WHY THIS IS NOT AN ADJUSTMENT LAYER
It was tested as one and it failed. availability_sweep.py measured
three availability signals against production's own projection: best
pooled rel-MAE 0.9998, and coverage of the 80% range already sits
between 78% and 89% in every bucket, because a player with erratic
recent minutes already has a wide prior spread and the distribution
widens on its own.

So these signals change neither our number nor our range, and nothing
here touches either. What they do is separate, and hard: against a
14.1% base rate of sub-ten-minute games,

    under 10 minutes in 2 of his last 3      61.8%   4.4x
    under 10 minutes in 1 of his last 3      27.7%   2.0x
    none of the above (52,298 player-games)   3.9%   0.3x

A reader deciding whether to take a prop wants to know that. It is the
difference between "this projection assumes he plays" and knowing that
six times in ten, players in this exact spot did not. The app can say
it honestly because it measured it, on 70,944 player-games across
three seasons -- which is precisely what the competition does not do.

WHAT THIS IS, PRECISELY
A historical frequency with its sample size and a Wilson interval, not
a prediction about tonight. The wording it produces says "players in
this spot", never "he will", because the first is what the data
supports and the second is not.

The rates live in engine/availability_rates.json, written by
`python3 availability_sweep.py --write-rates` from the point-in-time
backtest population. Regenerate it when the population is rebuilt; do
not hand-edit it.
"""

import json
import os

import pandas as pd

RATES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "availability_rates.json")

# Below this a bucket is not worth saying anything about: it is at or
# under the base rate, and a note that fires on every player is noise
# that trains a reader to ignore the ones that matter.
MIN_LIFT_TO_MENTION = 1.5


def _load():
    try:
        with open(RATES_PATH) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        # A missing table means no note, never a guessed one.
        return None


RATES = _load()


def _minutes(value):
    """Minutes from a game-log row, which the NBA writes several ways."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    if ":" in text:                       # "34:12"
        parts = text.split(":")
        try:
            return int(parts[0]) + int(parts[1]) / 60.0
        except (ValueError, IndexError):
            return None
    try:
        return float(text)
    except ValueError:
        return None


def recent_short_stints(game_log, appearances=None, threshold=None):
    """How many of his last few appearances were under the threshold.

    game_log is the cached frame or list of rows, NEWEST FIRST, which
    is the order the NBA returns and the order the rest of this app
    assumes. Returns None when there are not enough games to say
    anything -- a player with two games on the season has no recent
    pattern, and inventing one would be worse than silence.
    """
    if RATES is None:
        return None
    appearances = appearances or int(RATES.get("recent_appearances", 3))
    threshold = threshold or float(RATES.get("short_stint_minutes", 10.0))

    if isinstance(game_log, pd.DataFrame):
        frame = game_log
        # Sort rather than trust the order. The NBA returns newest
        # first and most of this app relies on that, but this frame
        # reaches here from several call sites -- a season log, a
        # head-to-head log, a concatenation of two seasons -- and
        # reading the wrong end of it would quietly describe a player's
        # October instead of his March.
        if "GAME_DATE" in frame.columns:
            frame = frame.assign(
                _order=pd.to_datetime(frame["GAME_DATE"], errors="coerce")
            ).sort_values("_order", ascending=False, na_position="last")
        rows = frame.to_dict("records")
    else:
        rows = list(game_log or [])
    if len(rows) < appearances:
        return None

    minutes = [_minutes(row.get("MIN")) for row in rows[:appearances]]
    if any(value is None for value in minutes):
        return None
    return sum(1 for value in minutes if value < threshold)


def _bucket_for(count):
    if count is None:
        return None
    if count == 0:
        return "none short"
    if count == 1:
        return "1 short"
    return "2-3 short"


def risk(game_log):
    """The measured short-night rate for this player's situation.

    Returns None when there is nothing worth saying: not enough games,
    no rates table, or a situation no more risky than average. Silence
    is the common case by design -- 52,298 of the 70,944 player-games
    behind this table are in the quiet bucket.
    """
    if RATES is None:
        return None
    count = recent_short_stints(game_log)
    bucket = _bucket_for(count)
    if bucket is None:
        return None
    row = (RATES.get("by_short_stints") or {}).get(bucket)
    if not row or row.get("lift", 0) < MIN_LIFT_TO_MENTION:
        return None
    return {
        "bucket": bucket,
        "short_games": count,
        "of_last": int(RATES.get("recent_appearances", 3)),
        "threshold": float(RATES.get("short_stint_minutes", 10.0)),
        "rate": row["rate"],
        "low": row["low"],
        "high": row["high"],
        "n": row["n"],
        "lift": row["lift"],
        "overall": (RATES.get("overall") or {}).get("rate"),
        "player_games": RATES.get("player_games"),
        "seasons": RATES.get("seasons"),
    }


def sentence(detail):
    """One line a reader can act on, phrased as what it is.

    "Players in this spot" rather than "he will": the table is a
    historical frequency over other players' games, and saying
    otherwise would claim more than it measured.
    """
    if not detail:
        return None
    threshold = int(detail["threshold"])
    return (f"Under {threshold} minutes in {detail['short_games']} of his last "
            f"{detail['of_last']} games. Historically {detail['rate']:.0%} of "
            f"players in this spot went under {threshold} again "
            f"({detail['n']:,} player-games).")
