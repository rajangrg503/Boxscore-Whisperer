"""Who a player passes to, and what those passes produce.

WHY THIS EXISTS
Karma, looking at a projection that said Shai scores 31.7 while being
double teamed: "is there a data when he's double teamed? who's
benefitting? maybe Ajay Mitchell and Jalen Williams recently. or Jared
McCain is getting wide open 3s."

There is no double-team statistic. nba_api's LeagueDashPtStats measure
types are SpeedDistance, Rebounding, Possessions, CatchShoot,
PullUpShot, Defense, Drives, Passing, ElbowTouch, PostTouch, PaintTouch
and Efficiency -- none of them counts a double team. That data is
Second Spectrum's and it is not in the free endpoints.

But the CONSEQUENCE is measurable, and that was the better question.
PlayerDashPtPass reports, per teammate, how often he passes to them and
what those passes turn into: assists, field goals, threes. So rather
than guess at a defensive scheme we cannot see, this reports where the
ball actually went.

WHAT THIS DELIBERATELY IS NOT
It is not a layer and it never touches a projection. Nothing here
multiplies anything.

The reason is worth stating because the temptation is obvious. Nothing
in this data says WHY a teammate got open. Tying "McCain shot more
threes" to "Shai was doubled" is an inference from co-occurrence, not a
measurement, and a layer built on it would smuggle a causal claim into
a descriptive dataset -- an invented number wearing a real one's
clothes. What this can honestly do is show the association and let the
reader draw the conclusion, which is what the rest of this app does.

THE SHAPE OF THE DATA IS DIFFERENT FROM EVERYTHING ELSE HERE
Every other source in this repo is a per-game log. This endpoint
returns a season aggregate with optional date filters, so it cannot be
windowed per game the way engine/minutes.py windows a gamelog. The
"recently" view is therefore a second call with DateFrom set, not a
slice of the first.
"""

import datetime

from nba_api.stats.endpoints import playerdashptpass

from engine.cache import cached_or_live

# Below this a teammate's line is noise: two passes that happened to
# become a three tell you nothing about where the ball goes. Chosen to
# be obviously too small rather than tuned, and the count is always
# shown so a reader can judge it themselves.
MIN_PASSES_TO_SHOW = 10

# How far back "recently" reaches. Long enough to cover a handful of
# games, short enough that it is not just the season again.
RECENT_DAYS = 21


def _fetch_passes(player_id, team_id, season, date_from=""):
    def _call():
        dash = playerdashptpass.PlayerDashPtPass(
            player_id=player_id, team_id=team_id, season=season,
            date_from_nullable=date_from, timeout=5,
        )
        df = dash.passes_made.get_data_frame()
        cols = ["PASS_TO", "PASS_TEAMMATE_PLAYER_ID", "FREQUENCY", "PASS",
                "AST", "FGM", "FGA", "FG3M", "FG3A"]
        return df[[c for c in cols if c in df.columns]].copy()

    key = f"passes_made_{player_id}_{season}" + (f"_from_{date_from}" if date_from else "")
    df, source = cached_or_live(key, _call)
    return df, source


def recent_cutoff(today=None):
    """DateFrom for the recent window, in the MM/DD/YYYY the endpoint
    wants. Its own function so a test can pin the format -- an ISO date
    here is silently accepted and silently ignored, which would make
    "recently" quietly identical to the season."""
    today = today or datetime.date.today()
    start = today - datetime.timedelta(days=RECENT_DAYS)
    return start.strftime("%m/%d/%Y")


def feeds(df, roster_ids=None, limit=6):
    """The teammates he passes to most, tidied for the page.

    roster_ids, when given, marks whoever is no longer on the team --
    it does NOT drop them. Early in a season this data is last
    season's, so some of the names are at other clubs now; hiding them
    would misrepresent how much of his passing the table accounts for,
    and dropping them silently is how a panel comes to describe a team
    that does not exist.
    """
    if df is None or len(df) == 0:
        return []

    rows = []
    for _i, r in df.iterrows():
        passes = float(r.get("PASS") or 0)
        if passes < MIN_PASSES_TO_SHOW:
            continue
        pid = r.get("PASS_TEAMMATE_PLAYER_ID")
        rows.append({
            "name": str(r.get("PASS_TO") or "").strip(),
            "player_id": pid,
            "passes": passes,
            "frequency": float(r.get("FREQUENCY") or 0),
            "assists": float(r.get("AST") or 0),
            "fg3m": float(r.get("FG3M") or 0),
            "fg3a": float(r.get("FG3A") or 0),
            "still_here": None if roster_ids is None else (pid in set(roster_ids)),
        })
    rows.sort(key=lambda d: d["passes"], reverse=True)
    return rows[:limit]


def shooting_note(row):
    """What his passes to this teammate turned into, or None when the
    sample cannot support a rate.

    A percentage over four attempts is not a number, it is a rumour --
    the same rule engine/hit_rates.py already applies to badges. The
    attempts are always shown beside it.
    """
    if row["fg3a"] < 5:
        return None
    return f"{row['fg3m']:.0f}/{row['fg3a']:.0f} from three ({row['fg3m'] / row['fg3a']:.0%})"
