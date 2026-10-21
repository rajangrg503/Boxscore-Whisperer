#!/usr/bin/env python3
"""Project every rostered player in a night's games, in one pass.

    python3 tools/slate_projections.py MIN MIA GSW LAL
    python3 tools/slate_projections.py --min-points 10 --out slate.json ATL ORL

WHY THIS EXISTS
tools/capture_projections.py projects a list of player ids, which is the
right shape when a line snapshot has already told you who is playing. On
a night when the capture job has not run -- or before the snapshot
exists -- the question is the other way round: here are the teams, who is
on them, and what does the app say about each one.

Doing that through the Streamlit page costs one page load per player. On
an eleven-game slate that is upwards of two hundred loads, which is why
this was written: 233 players in a single pass over the cached logs.

SAME SEAM AS THE PAGE
engine/baseline_stats.stats_from_gamelog -> engine/distribution for the
calibrated distribution -> quantile(0.5). The app publishes medians (PR
#80), so medians are what comes out here. The mean is carried alongside
because the two differ by about half a point on a scorer and anyone
comparing this against a line needs to know which one they are holding.

SEASON SELECTION
Matches app.py: the current season once a player has five games in it,
otherwise the previous one. In October that means everybody is on last
season, and every number here inherits last season's roster -- which is
why prior_teams is reported per player rather than left implicit. A
player whose log is for another team is not wrong, but he is a different
question from one who stayed.

PRA
Computed per game from the log and carried as a minutes-adjusted
empirical median -- never as the sum of the three component medians,
which reads low by 1.5-2.0 on a high-usage player because medians do not
add. It is NOT calibrated: engine/stat_distribution.json has no PRA
entry until calibration_sweep.py is rerun with the column, so there is
no fitted quantile and no honest interval. Marked calibrated=False so a
caller cannot mistake it for a first-class stat.
"""
import argparse
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.baseline_stats import stats_from_gamelog           # noqa: E402
from engine.cache import read_payload                          # noqa: E402
from engine.distribution import distribution_for               # noqa: E402
from engine.minutes import minutes_aware_means                 # noqa: E402
from engine.season import CURRENT_SEASON, PREVIOUS_SEASON      # noqa: E402
from engine.stat_columns import STAT_COLUMNS                   # noqa: E402
from engine.team_ids import TEAM_ID_BY_ABBR                    # noqa: E402

# app.py uses five as the point where the current season replaces the
# previous one, and engine/distribution.py refuses to fit below it.
MIN_PRIOR_GAMES = 5


def roster(abbr):
    """(name, player_id) for everyone on a team's cached roster."""
    team_id = TEAM_ID_BY_ABBR.get(abbr)
    if team_id is None:
        return []
    payload = read_payload(f"roster_{team_id}")
    if payload is None:
        return []
    return [(row["PLAYER"], int(row["PLAYER_ID"]))
            for row in (payload.get("data") or [])]


def season_for(player_id):
    """The season the app would build this player's baseline from."""
    current = read_payload(f"gamelog_{player_id}_{CURRENT_SEASON}")
    rows = len(current.get("data") or []) if current else 0
    return CURRENT_SEASON if rows >= MIN_PRIOR_GAMES else PREVIOUS_SEASON


def prior_teams(frame):
    """Which team(s) the log belongs to, commonest first.

    The mode, not the first row. A midseason trade puts two teams in one
    log, and reading only the top row calls a settled player a mover or a
    mover settled depending on how the log happens to be sorted.
    """
    if "MATCHUP" in frame.columns and len(frame):
        teams = frame["MATCHUP"].astype(str).str.split().str[0]
    elif "TEAM_ABBREVIATION" in frame.columns and len(frame):
        teams = frame["TEAM_ABBREVIATION"].astype(str)
    else:
        return []
    return [(str(t), int(c)) for t, c in teams.value_counts().items()]


def pra_projection(frame):
    """The middle of PRA, correlation included, bias excluded.

    Sums the three columns per game before taking any middle, so the
    correlation between them is observed rather than assumed, then moves
    the observed median by the same minutes adjustment the mean gets.
    """
    if not all(col in frame.columns for col in ("PTS", "REB", "AST")):
        return None
    pra = frame.copy()
    pra["PRA"] = pra["PTS"] + pra["REB"] + pra["AST"]
    raw_mean = float(pra["PRA"].mean())
    raw_median = float(pra["PRA"].median())
    adjusted = float(minutes_aware_means(pra, [("PRA", "PRA")])["PRA"])
    shifted = raw_median * (adjusted / raw_mean) if raw_mean else raw_median
    return {
        "median": round(shifted, 2),
        "mean_minutes_aware": round(adjusted, 2),
        "empirical_median": round(raw_median, 2),
        "raw_mean": round(raw_mean, 2),
        "calibrated": False,
    }


def project(player_id, season=None):
    """The app's baseline projection for one player, or None.

    None rather than a guess when the log is missing or too short: a
    slate report that silently invents a number for a player nobody has
    seen play is worse than one that leaves him out.
    """
    season = season or season_for(player_id)
    payload = read_payload(f"gamelog_{player_id}_{season}")
    if payload is None:
        return None
    frame = pd.DataFrame(payload.get("data") or [])
    if len(frame) < MIN_PRIOR_GAMES:
        return None

    stats, n_games = stats_from_gamelog(frame)
    teams = prior_teams(frame)
    out = {
        "season": season,
        "n_prior_games": int(n_games),
        "prior_team": teams[0][0] if teams else None,
        "prior_teams": teams,
    }
    for col, _label in STAT_COLUMNS:
        mean, spread = stats[col]
        if pd.isna(mean):
            continue
        dist = distribution_for(col, mean, spread, n_games)
        out[col] = {
            "mean": round(float(mean), 2),
            "median": round(float(dist.quantile(0.5)), 2) if dist else None,
            "calibrated": dist is not None,
        }
    pra = pra_projection(frame)
    if pra is not None:
        out["PRA"] = pra
    return out


def build(team_abbrs, min_points):
    result = {}
    for abbr in team_abbrs:
        players = {}
        for name, player_id in roster(abbr):
            projection = project(player_id)
            if projection is None:
                continue
            points = (projection.get("PTS") or {}).get("median")
            # Below this nobody posts a two-sided market, so the row
            # could never be scored against anything.
            if points is None or points < min_points:
                continue
            projection["name"] = name
            projection["id"] = player_id
            projection["roster_team"] = abbr
            players[name] = projection
        result[abbr] = players
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("teams", nargs="+", help="team abbreviations, e.g. BOS DET")
    parser.add_argument("--min-points", type=float, default=6.0,
                        help="skip players projected below this (default 6)")
    parser.add_argument("--out", default=None,
                        help="write JSON here instead of stdout")
    args = parser.parse_args(argv)

    unknown = [t for t in args.teams if t not in TEAM_ID_BY_ABBR]
    if unknown:
        parser.error(f"unknown team abbreviation(s): {', '.join(unknown)}")

    result = build(args.teams, args.min_points)
    body = json.dumps(result, separators=(",", ":"), sort_keys=True)
    if args.out:
        with open(args.out, "w") as handle:
            handle.write(body)
        total = sum(len(v) for v in result.values())
        print(f"{total} player(s) across {len(result)} team(s) -> {args.out}")
    else:
        print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
