"""
Retroactive backtesting driver -- runs
engine/backtest_point_in_time.py's point-in-time prediction engine
across the fixed rotation-player list (top 150 by minutes played, per
season -- see data_cache/player_totals_{season}.json) for all 3
backtested seasons, and writes one combined results CSV.

SCOPE: baseline + opponent_defense ONLY. missing_teammates,
new_teammate, missing_opponents, and defender_matchup are all driven
by user-supplied input in the live app -- a historical replay has no
equivalent signal to recover from a box score alone, so they stay
structurally unexercised (neutral/not-applied), not data-limited.
scheme is excluded entirely (its underlying data sources have no
historical time-series access at all -- see
~/.claude/plans/backtest-engine-plan.md). Any reported "backtested
across N games" number MUST say it measures baseline + opponent_defense
specifically, not all six layers.

Deliberately does NOT catch a missing-gamelog FileNotFoundError per
player and skip -- Phase B1 already verified every player in the fixed
list has a cached gamelog for its season. A miss here means something
is genuinely wrong (wrong id, wrong season key, a real gap Phase B1
missed), and silently skipping would produce a misleading "backtested
N players" claim that's actually short by however many were quietly
dropped. Let it crash loudly instead.

Usage:
    python3 run_backtest.py
"""

import json
from datetime import date

import pandas as pd

from engine.backtest_point_in_time import run_backtest_for_player

SEASONS = [
    ("2023-24", date(2023, 10, 24)),
    ("2024-25", date(2024, 10, 22)),
    ("2025-26", date(2025, 10, 21)),
]
TOP_N = 150
OUTPUT_PATH = "backtest_results.csv"


def load_fixed_player_list(season):
    """The real top-N-by-minutes list for this season, from Phase B0's
    cached LeagueDashPlayerStats totals -- not an assumption."""
    with open(f"data_cache/player_totals_{season}.json") as f:
        raw = json.load(f)
    rows = sorted(raw["data"], key=lambda r: r.get("MIN", 0), reverse=True)[:TOP_N]
    return [(str(r["PLAYER_ID"]), r.get("PLAYER_NAME", "?")) for r in rows]


def main():
    all_rows = []
    for season, season_start in SEASONS:
        players = load_fixed_player_list(season)
        print(f"\n{season}: {len(players)} players")
        for i, (player_id, player_name) in enumerate(players, start=1):
            rows = run_backtest_for_player(player_id, player_name, season, season_start)
            all_rows.extend(rows)
            if i % 25 == 0 or i == len(players):
                print(f"  [{i}/{len(players)}] {player_name} -- {len(all_rows)} total rows so far")

    df = pd.DataFrame(all_rows)
    df.to_csv(OUTPUT_PATH, index=False)
    print(f"\nDone. {len(df)} backtest predictions written to {OUTPUT_PATH}.")
    print("SCOPE: baseline + opponent_defense only -- see this script's module "
          "docstring for why the other 4 layers are structurally unexercised, "
          "and why scheme is excluded entirely.")


if __name__ == "__main__":
    main()
