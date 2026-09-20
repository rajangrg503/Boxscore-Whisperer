"""Does the game being a likely blowout move a player's line? Measured
against what could be known BEFORE tip-off.

WHY THIS EXISTS
The final margin is the largest driver of a player's minutes found so
far. Conditioned on the ACTUAL margin, a 21-point game costs the losing
side's starters 7% of their minutes and 13% of their scoring, and SGA's
2025-26 regular season puts 8.1 minutes and 5.9 points between a close
game and a blowout win, across a third of his season.

None of that is actionable, because nobody knows the margin in advance.
This asks the question that is: **does the EXPECTED margin move a
player's production?**

The distinction is not pedantic -- it reverses a conclusion. Conditioned
on the actual result, winners' stars appear to BEAT their average in
blowouts (1.015). That bucket mixes two opposite situations: for most
teams a 21-point win is a rare night the star went off, and for a
dominant team it is routine and the star sits the fourth. Only the
pre-game expectation separates them.

THE PROXY, AND ITS LIMIT
The market's forecast is the spread, and we have no historical spreads
-- tools/capture_lines.py only starts on 21 Oct 2026. So this uses what
the cache can supply point-in-time: each team's average scoring margin
in its games strictly BEFORE the target date, plus a home-court term.

That is a weaker forecast than a real spread. Books price injuries,
rest and lineup news; a running point differential does not. So
whatever this measures is a FLOOR on the effect a real spread would
find, and the proxy's own accuracy is reported so the discount is
visible rather than assumed.

TWO CONTAMINATIONS THIS AVOIDS
  * REGULAR SEASON ONLY. Playoff rotations are shorter and playoff
    blowouts do not produce the same resting; the earlier 3,941-game
    version mixed them in.
  * POINT-IN-TIME BASELINES. A player's production is compared against
    his average over prior games only. Dividing by his full-season
    average would leak the target game into its own denominator, and
    would do so hardest for exactly the players who miss time.

WHAT IT FOUND (20 Sep 2026) -- MOSTLY NEGATIVE
57,358 player-games across three regular seasons.

PROXY QUALITY FIRST, because everything else is discounted by it:
correlation 0.550 with the real margin, mean absolute error 14.9
points. Soft. A real spread would do better.

Stars and starters, production against their own point-in-time
baseline:

    big underdog   1,327   MIN 1.000   PTS 0.998  [0.985, 1.036]
    underdog       4,136   MIN 1.005   PTS 0.984  [0.974, 1.002]
    close          9,709   MIN 1.009   PTS 1.002  [0.999, 1.016]
    favourite      4,758   MIN 0.989   PTS 0.997  [0.987, 1.010]
    big favourite  1,826   MIN 0.974   PTS 1.015  [1.002, 1.040]

ONE interval excludes 1.0, and it points the wrong way: a star who is
a big favourite plays 2.6% fewer minutes and scores slightly MORE.
Fewer minutes against a worse defence -- the two cancel on points. The
minutes drop is real and forecastable; the scoring drop is not,
because efficiency rises to meet it.

The underdog side is directionally the "under" thesis at 1.6% down,
but its interval clips 1.0 at 1.0016 and does not clear the bar.

SO THE ACTUAL-MARGIN VERSION WAS A TRAP
Conditioned on the result, the losing side's starters lose 13% of
their scoring in a 21-point game. Conditioned on what could be known
beforehand, almost none of that survives. Same failure as comparing
means to test a centred effect, or comparing unpaired groups with
different composition -- check what the comparison conditions on
before believing it.

WHAT THIS DOES NOT RULE OUT
The proxy is weak, and minutes DO move for big favourites, which
should matter more for assists and rebounds than for points. Real
spreads start arriving 21 Oct 2026 via tools/capture_lines.py and
engine/odds_snapshot.game_context(); rerun this against those before
concluding anything final.

It does, however, argue against spending 3,692 API calls on
period-limited box scores for a garbage-time model. The floor test
does not support it yet.

Usage:
    python3 blowout_sweep.py
"""

import re
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, ".")

from engine.actuals import parse_game_date                        # noqa: E402
from engine.cache import ARCHIVE                                  # noqa: E402

# Recent NBA home advantage, used only to orient the proxy. The exact
# value barely matters: it shifts every game equally and the buckets
# are wide.
HOME_EDGE = 2.5

# A team needs some season behind it before its point differential
# means anything, and a player needs some games before his own average
# does.
MIN_TEAM_GAMES = 10
MIN_PLAYER_GAMES = 10

# Expected margin from the player's team's point of view: positive
# means his side is favoured.
BUCKETS = [(-999, -10), (-10, -4), (-4, 4), (4, 10), (10, 999)]
BUCKET_LABELS = ["big underdog", "underdog", "close", "favourite", "big favourite"]


def regular_season_logs():
    """{season: {player_id: frame}}, regular season only, oldest first."""
    pattern = re.compile(r"^gamelog_(\d+)_(\d{4}-\d{2})\.json$")
    out = defaultdict(dict)
    for name in ARCHIVE.names():
        match = pattern.match(name)
        if not match:
            continue
        rows = (ARCHIVE.read_json(name) or {}).get("data") or []
        if not rows:
            continue
        frame = pd.DataFrame(rows)
        if "SEASON_ID" not in frame.columns or "GAME_DATE" not in frame.columns:
            continue
        # Playoff game ids start with 4; the regular season starts with 2.
        frame = frame[~frame["SEASON_ID"].astype(str).str.startswith("4")]
        if frame.empty:
            continue
        frame["_date"] = frame["GAME_DATE"].map(parse_game_date)
        frame = frame.dropna(subset=["_date"]).sort_values("_date")
        for column in ("MIN", "PTS", "REB", "AST"):
            if column in frame.columns:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame.dropna(subset=["MIN", "PTS"])
        frame = frame[frame["MIN"] > 0]
        if frame.empty:
            continue
        frame["team"] = frame["MATCHUP"].str.slice(0, 3)
        frame["home"] = frame["MATCHUP"].str.contains(" vs. ")
        out[match.group(2)][match.group(1)] = frame.reset_index(drop=True)
    return out


def team_games(logs):
    """Per team-game: date, points for, points against, margin.

    Team points are the sum of its players' points in the cached logs.
    That is what the cache can answer; a roster with a missing log
    undercounts slightly, and it undercounts BOTH teams, so the margin
    is far more robust than either total.
    """
    rows = []
    for season, by_player in logs.items():
        for frame in by_player.values():
            rows.append(frame[["Game_ID", "_date", "team", "home", "PTS"]]
                        .assign(season=season))
    stacked = pd.concat(rows, ignore_index=True)
    stacked["Game_ID"] = stacked["Game_ID"].astype(str)
    scored = (stacked.groupby(["season", "Game_ID", "team", "home", "_date"])["PTS"]
              .sum().rename("points").reset_index())

    # Pair the two sides of each game.
    paired = scored.merge(scored, on=["season", "Game_ID"], suffixes=("", "_opp"))
    paired = paired[paired["team"] != paired["team_opp"]]
    paired["margin"] = paired["points"] - paired["points_opp"]
    return paired[["season", "Game_ID", "team", "home", "_date",
                   "points", "points_opp", "margin"]]


def running_margin(paired):
    """Each team's average margin over its games STRICTLY BEFORE each
    date -- the strength estimate a forecaster would have had."""
    paired = paired.sort_values(["season", "team", "_date"]).copy()
    grouped = paired.groupby(["season", "team"])["margin"]
    paired["prior_margin"] = (grouped.apply(lambda s: s.shift(1).expanding().mean())
                              .reset_index(level=[0, 1], drop=True))
    paired["prior_games"] = (grouped.cumcount())
    return paired


def build(logs):
    paired = running_margin(team_games(logs))
    strength = paired.set_index(["season", "Game_ID", "team"])[
        ["prior_margin", "prior_games", "margin", "home"]]

    records = []
    for season, by_player in logs.items():
        for player_id, frame in by_player.items():
            if len(frame) < MIN_PLAYER_GAMES:
                continue
            # Point-in-time player baselines: prior games only.
            prior_min = frame["MIN"].shift(1).expanding().mean()
            prior_pts = frame["PTS"].shift(1).expanding().mean()
            for index in range(len(frame)):
                if index < MIN_PLAYER_GAMES:
                    continue
                row = frame.iloc[index]
                key = (season, str(row["Game_ID"]), row["team"])
                if key not in strength.index:
                    continue
                own = strength.loc[key]
                opp_rows = paired[(paired["season"] == season) &
                                  (paired["Game_ID"] == str(row["Game_ID"])) &
                                  (paired["team"] != row["team"])]
                if opp_rows.empty:
                    continue
                opp = opp_rows.iloc[0]
                if (pd.isna(own["prior_margin"]) or pd.isna(opp["prior_margin"])
                        or own["prior_games"] < MIN_TEAM_GAMES
                        or opp["prior_games"] < MIN_TEAM_GAMES):
                    continue
                expected = (float(own["prior_margin"]) - float(opp["prior_margin"])) / 2.0
                expected += HOME_EDGE if bool(row["home"]) else -HOME_EDGE
                records.append({
                    "season": season, "player_id": player_id,
                    "expected_margin": expected,
                    "actual_margin": float(own["margin"]),
                    "MIN": float(row["MIN"]), "PTS": float(row["PTS"]),
                    "base_min": float(prior_min.iloc[index]),
                    "base_pts": float(prior_pts.iloc[index]),
                })
    frame = pd.DataFrame(records)
    frame = frame[(frame["base_min"] > 0) & (frame["base_pts"] > 0)]
    frame["tier"] = pd.cut(frame["base_pts"], [-1, 6, 12, 18, 100],
                           labels=["deep bench", "rotation", "starter", "star"])
    frame["bucket"] = pd.cut(
        frame["expected_margin"],
        bins=[b[0] for b in BUCKETS] + [BUCKETS[-1][1]],
        labels=BUCKET_LABELS)
    return frame


def main():
    print("reading regular-season game logs ...")
    logs = regular_season_logs()
    print("  " + ", ".join(f"{s}: {len(p)} players" for s, p in sorted(logs.items())))

    frame = build(logs)
    if frame.empty:
        print("nothing scoreable")
        return 1
    print(f"\n{len(frame):,} player-games with a pre-game forecast\n")

    # How good is the proxy at all? Everything below is discounted by this.
    corr = frame["expected_margin"].corr(frame["actual_margin"])
    resid = (frame["actual_margin"] - frame["expected_margin"]).abs().mean()
    print(f"PROXY QUALITY  correlation with the real margin {corr:.3f}, "
          f"mean absolute error {resid:.1f} points")
    print("  (a real spread would do better; treat what follows as a floor)\n")

    print(f"{'expected':16s} {'n':>7s} {'MIN vs own':>11s} {'PTS vs own':>11s}")
    for bucket, group in frame.groupby("bucket", observed=True):
        print(f"{str(bucket):16s} {len(group):7,d} "
              f"{group['MIN'].mean()/group['base_min'].mean():11.4f} "
              f"{group['PTS'].mean()/group['base_pts'].mean():11.4f}")

    print("\nSTARS AND STARTERS ONLY (the props people actually bet)")
    sub = frame[frame["tier"].isin(["starter", "star"])]
    print(f"{'expected':16s} {'n':>7s} {'MIN vs own':>11s} {'PTS vs own':>11s} "
          f"{'PTS 95% CI':>18s}")
    rng = np.random.default_rng(20260920)
    for bucket, group in sub.groupby("bucket", observed=True):
        ratios = group["PTS"] / group["base_pts"]
        boot = [float(ratios.sample(len(ratios), replace=True,
                                    random_state=int(rng.integers(1e9))).mean())
                for _ in range(300)]
        low, high = np.percentile(boot, [2.5, 97.5])
        print(f"{str(bucket):16s} {len(group):7,d} "
              f"{group['MIN'].mean()/group['base_min'].mean():11.4f} "
              f"{group['PTS'].mean()/group['base_pts'].mean():11.4f} "
              f"  [{low:.4f}, {high:.4f}]")

    print("\nA bucket whose interval excludes 1.0 is a real, forecastable")
    print("effect. One that straddles it is not, whatever the actual-margin")
    print("version suggested.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
