"""Does last season's PLAYOFF run belong in this season's baseline?

WHY THIS EXISTS
engine/game_log.fetch_combined_game_log asks the NBA for "Regular
Season" and "Playoffs" and concatenates them into one frame. That
frame is what the live page averages.

build_backtest_population.py does not. It is built from the cached box
scores of "all 1,230 regular-season games per season" -- its own
docstring -- so every number this project has published (baseline
error, the opponent-defence layer, strong-lean accuracy, the range
calibration) was measured on a REGULAR-SEASON-ONLY baseline that the
app does not compute.

Nobody decided that. The two were written months apart and nothing
compares them, which is the same shape as the n_prior bug: a seam
where two files quietly disagree and no test asks.

WHERE IT CAN POSSIBLY MATTER
Only in the fallback window. Once a player has five games of the
current season the app uses those, and mid-season there are no playoff
games to include. But below five games
(engine/game_log.resolve_season_gamelog) the whole baseline is LAST
season's log -- and for anyone whose team made the playoffs, that log
now ends with up to 23 games played under a shortened rotation.

That window is opening night and the fortnight after it, which is when
the most people will see the site for the first time.

WHAT IT MEASURES
Perfectly paired by construction. For each player with a cached log in
season S-1 containing playoff games, and a log in season S:

    BLENDED   baseline from the full S-1 log, playoffs included
              -- what the app computes today
    CLEAN     baseline from the S-1 REGULAR SEASON rows only
              -- what every backtest measured

Both project the SAME target games: his first games of season S, the
ones the fallback actually serves. Same player, same games, one
difference. There is no population effect to confuse it with, which is
what went wrong in early_season_sweep.py's first pass.

HOW PLAYOFF ROWS ARE IDENTIFIED
SEASON_ID, not dates. The NBA encodes the season type in its first
digit -- 2 is regular season, 4 is playoffs -- so "22024" and "42024"
are the same season's two halves. Splitting on a date would need a
hard-coded calendar per season and would quietly rot.

NOTE: this cache carries no prefix-5 (play-in) rows, so play-in games
sit on whichever side the feed assigned them. They are a handful of
games and are not separated here.

WHAT IT FOUND (20 Sep 2026)
373 player-seasons with a playoff run behind them, 16,767 paired
observations.

    blended / clean MAE   0.9976   95% CI [0.9914, 1.0028]
    30+ mpg last season   0.9967   95% CI [0.9909, 1.0091]

No effect. Nine of eleven intervals include 1.0, and the two that do
not -- STL 0.9889, BLK 0.9945 -- point at KEEPING the playoff games.

Conditioned on run length, where a shortened rotation should bite
hardest, still nothing: 1-5 games 0.9971, 6-11 0.9932, 12-16 1.0028,
17+ 1.0058, every interval spanning 1.0 and only 49 player-seasons in
the deepest bucket.

So no code changed. The blended baseline stays, and
engine/game_log.py now says why -- the point of this sweep is that a
seam nobody chose is now a decision somebody made.

One thing the number is NOT: a clean test of playoff data quality.
The blended baseline also has more games behind it, a median of six
more, and that is not a confound to strip out. It is the decision the
app actually faces: use every row you have, or throw the playoff ones
away. Keeping them is free.

Usage:
    python3 playoff_blending_sweep.py
    python3 playoff_blending_sweep.py --stat PTS
"""

import argparse
import re
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, ".")

from engine.actuals import parse_game_date                        # noqa: E402
from engine.baseline_stats import stats_from_gamelog              # noqa: E402
from engine.cache import ARCHIVE                                  # noqa: E402
from engine.stat_columns import STAT_COLUMNS                      # noqa: E402

STATS = [col for col, _label in STAT_COLUMNS]

# The app's own threshold: below this many current-season games it
# falls back to last season. Named rather than typed, so the sweep and
# engine/game_log.py cannot drift apart.
FALLBACK_BELOW_GAMES = 5

# NBA SEASON_ID prefixes. 1 preseason, 2 regular, 3 all-star,
# 4 playoffs, 5 play-in.
REGULAR_SEASON_PREFIX = "2"
PLAYOFF_PREFIX = "4"

# A prior season thinner than this is not a baseline worth comparing;
# the same floor early_season_sweep.py uses.
MIN_PRIOR_GAMES = 10

BOOTSTRAP_ROUNDS = 300
RNG = np.random.default_rng(20260920)


def cached_logs():
    """{season: {player_id: DataFrame}}, oldest game first."""
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
        if "GAME_DATE" not in frame.columns or "SEASON_ID" not in frame.columns:
            continue
        frame["_date"] = frame["GAME_DATE"].map(parse_game_date)
        frame = frame.dropna(subset=["_date"]).sort_values("_date")
        if frame.empty:
            continue
        out[match.group(2)][match.group(1)] = frame.reset_index(drop=True)
    return out


def regular_season_only(frame):
    """The rows the backtests were built from."""
    prefix = frame["SEASON_ID"].astype(str).str[0]
    return frame[prefix == REGULAR_SEASON_PREFIX]


def has_playoffs(frame):
    return (frame["SEASON_ID"].astype(str).str[0] == PLAYOFF_PREFIX).any()


def previous(season):
    start = int(season.split("-")[0])
    return f"{start - 1}-{str(start)[-2:]}"


def rows_for(logs):
    """One row per (player, season, target game, stat), carrying both
    predictions. Paired by construction -- the two baselines differ in
    nothing but which rows built them."""
    records = []
    for season, by_player in sorted(logs.items()):
        prior_season = previous(season)
        if prior_season not in logs:
            continue
        for player_id, frame in by_player.items():
            prior = logs[prior_season].get(player_id)
            if prior is None or not has_playoffs(prior):
                # No playoff rows means the two baselines are identical
                # and the pair carries no information. Including them
                # would dilute every figure below towards 1.0.
                continue
            clean_frame = regular_season_only(prior)
            if len(clean_frame) < MIN_PRIOR_GAMES:
                continue

            blended_stats, n_blended = stats_from_gamelog(prior)
            clean_stats, n_clean = stats_from_gamelog(clean_frame)

            for index in range(min(FALLBACK_BELOW_GAMES, len(frame))):
                actual = frame.iloc[index]
                for col in STATS:
                    if col not in actual:
                        continue
                    truth = actual[col]
                    blended = blended_stats.get(col, (np.nan, np.nan))[0]
                    clean = clean_stats.get(col, (np.nan, np.nan))[0]
                    if pd.isna(truth) or pd.isna(blended) or pd.isna(clean):
                        continue
                    records.append({
                        "season": season, "player_id": player_id,
                        "stat": col, "game_index": index,
                        "actual": float(truth),
                        "blended": float(blended), "clean": float(clean),
                        "n_playoff_rows": int(len(prior) - len(clean_frame)),
                        "prior_mpg": float(clean_frame["MIN"].astype(float).mean()),
                    })
    return pd.DataFrame(records)


def paired_ratio(table, stat=None):
    """Median per-player-season MAE ratio (blended / clean) and a
    cluster-bootstrap CI over player-seasons.

    Above 1.0 means the blended baseline -- what the app computes --
    is WORSE. Bootstrapping over player-seasons rather than rows
    because a player's five target games are not five independent
    observations of anything.
    """
    rows = table if stat is None else table[table["stat"] == stat]
    if rows.empty:
        return None

    rows = rows.assign(
        err_blended=(rows["blended"] - rows["actual"]).abs(),
        err_clean=(rows["clean"] - rows["actual"]).abs())
    per_unit = rows.groupby(["season", "player_id"]).agg(
        blended=("err_blended", "mean"), clean=("err_clean", "mean"))
    per_unit = per_unit[per_unit["clean"] > 0]
    if per_unit.empty:
        return None
    ratios = (per_unit["blended"] / per_unit["clean"]).to_numpy()

    draws = RNG.integers(0, len(ratios), size=(BOOTSTRAP_ROUNDS, len(ratios)))
    medians = np.median(ratios[draws], axis=1)
    return {
        "units": len(ratios),
        "rows": len(rows),
        "median": float(np.median(ratios)),
        "lo": float(np.percentile(medians, 2.5)),
        "hi": float(np.percentile(medians, 97.5)),
        "worse_share": float((ratios > 1.0).mean()),
    }


def report(table):
    print(f"{len(table)} paired observation(s) across "
          f"{table.groupby(['season', 'player_id']).ngroups} player-seasons "
          f"with playoff rows behind them")
    playoff_rows = table.groupby(["season", "player_id"])["n_playoff_rows"].first()
    print(f"playoff games per prior season: median {playoff_rows.median():.0f}, "
          f"max {playoff_rows.max():.0f}\n")

    print("MAE ratio, blended / clean. Above 1.000 means the baseline the")
    print("app actually computes is WORSE than the one every backtest used.\n")
    print(f"{'stat':6} {'units':>6} {'ratio':>8} {'95% CI':>20} {'worse for':>10}")
    overall = paired_ratio(table)
    for stat in STATS + [None]:
        result = paired_ratio(table, stat)
        if result is None:
            continue
        label = stat or "ALL"
        flag = "" if result["lo"] <= 1.0 <= result["hi"] else "   <- excludes 1.0"
        print(f"{label:6} {result['units']:6d} {result['median']:8.4f} "
              f"[{result['lo']:.4f}, {result['hi']:.4f}] "
              f"{100 * result['worse_share']:9.1f}%{flag}")

    # The stars are where the money is, and where the shortened
    # rotation should bite hardest if it bites at all.
    heavy = table[table["prior_mpg"] >= 30]
    if not heavy.empty:
        result = paired_ratio(heavy)
        print(f"\n30+ minutes a game last season ({result['units']} player-seasons):")
        print(f"  ratio {result['median']:.4f}  "
              f"[{result['lo']:.4f}, {result['hi']:.4f}]  "
              f"worse for {100 * result['worse_share']:.1f}%")

    if overall and overall["lo"] <= 1.0 <= overall["hi"]:
        print("\nThe interval includes 1.0: this measurement does not show that")
        print("dropping playoff games from the baseline helps. Leave it alone.")
    elif overall:
        direction = "worse" if overall["median"] > 1 else "better"
        print(f"\nThe interval excludes 1.0: the blended baseline is {direction},")
        print("and the seam between the app and the backtests is worth closing.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stat", default=None, help="report one stat only")
    parser.add_argument("--csv", default="playoff_blending_sweep_results.csv")
    args = parser.parse_args(argv)

    print("reading cached game logs ...")
    logs = cached_logs()
    print(f"  {sum(len(v) for v in logs.values())} player-seasons in "
          f"{len(logs)} season(s)")

    table = rows_for(logs)
    if table.empty:
        print("nothing to compare -- no player has a prior season with playoffs")
        return 1
    table.to_csv(args.csv, index=False)
    print(f"  wrote {args.csv}\n")

    if args.stat:
        result = paired_ratio(table, args.stat)
        print(args.stat, result)
        return 0
    report(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
