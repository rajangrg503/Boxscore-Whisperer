"""
Batch-cache ALL primary-defender-vs-offense player matchup data
(LeagueSeasonMatchups) for Boxscore Whisperer -- full league coverage,
not just a curated list.

Run this LOCALLY (not on Streamlit Cloud) -- the NBA API blocks requests
from cloud servers, so this has to build the cache on your Mac, then you
commit + push data_cache/ to GitHub so the deployed app can read it.

KEY INSIGHT: LeagueSeasonMatchups accepts def_player_id_nullable alone
(no off_player_id required) and returns EVERY offensive player that
defender has guarded that season in one response. So instead of querying
every possible (offense, defense) pair -- which would be ~500x500x2
calls, completely impractical -- this makes ONE call per active player
(as the defender) per season, ~500 players x 2 seasons = ~1,000 calls
total, and captures every real matchup that occurred: center-on-PG
switches, wing-on-wing, everything, since it's driven by actual tracking
data rather than assumptions about who "should" guard whom.

Each bulk response is split into individual per-pairing cache files, in
the exact _save_df_cache format app.py's cached_or_live()/_load_df_cache()
expect, using the same key get_defender_matchup_adjustment() already
looks up:
    f"matchup_{off_player_id}_{def_player_id}_{season}"

RESUMABILITY: a single call can cover 0 to 300+ pairings at once, so we
can't tell "fully covered" from "no data" just by checking whether
pairing files exist. A separate marker file per (defender, season) is
written after each successful bulk fetch so re-running the script skips
defenders that are already fully covered, even if some of their pairings
had zero matchup minutes and produced no per-pairing cache file.

This will take a while (roughly 20-30 minutes for the full league) and
hits the API ~1,000 times -- consider running it in one sitting rather
than interrupting repeatedly, to avoid getting rate-limited mid-run
(safe to re-run if interrupted; it picks up where it left off).

Usage:
    pip install nba_api
    python batch_cache_all_matchups.py
"""

import json
import time
import datetime
from pathlib import Path

import pandas as pd
from nba_api.stats.static import players
from nba_api.stats.endpoints import leagueseasonmatchups

from data_watchdog.gate import require_valid

CACHE_DIR = Path("data_cache")
CURRENT_SEASON = "2026-27"   # keep in sync with app.py's CURRENT_SEASON
PREVIOUS_SEASON = "2025-26"  # keep in sync with app.py's PREVIOUS_SEASON
SEASONS = [CURRENT_SEASON, PREVIOUS_SEASON]
REQUEST_DELAY = 0.6          # seconds between calls, to avoid getting rate-limited
MAX_RETRIES = 3


def _cache_key_to_path(key: str) -> Path:
    safe_key = "".join(c if (c.isalnum() or c in "_-") else "_" for c in key)
    return CACHE_DIR / f"{safe_key}.json"


def _save_df_cache(key: str, df: pd.DataFrame) -> None:
    """Matches app.py's _save_df_cache format exactly, so cached_or_live()
    can read this back via _load_df_cache()."""
    CACHE_DIR.mkdir(exist_ok=True)
    payload = {
        "cached_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "data": df.to_dict(orient="records"),
    }
    with open(_cache_key_to_path(key), "w") as f:
        json.dump(payload, f)


def _marker_path(defender_id: int, season: str) -> Path:
    # Leading underscore keeps this visually distinct from real pairing cache files
    return _cache_key_to_path(f"_matchup_bulk_marker_{defender_id}_{season}")


def cache_defender_matchups(defender_id: int, defender_name: str, season: str) -> str:
    """Fetch every matchup for one defender in one season, split into
    per-pairing cache files. Returns 'cached', 'skipped', or 'failed'."""
    marker_path = _marker_path(defender_id, season)
    if marker_path.exists():
        return "skipped"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            data = leagueseasonmatchups.LeagueSeasonMatchups(
                def_player_id_nullable=defender_id,
                season=season,
                timeout=10,
            )
            df = data.get_data_frames()[0]
            break
        except Exception as e:
            if attempt == MAX_RETRIES:
                print(f"  FAILED {defender_name} ({season}): {e}")
                return "failed"
            time.sleep(2 * attempt)  # back off and retry
    else:
        return "failed"

    pairing_count = 0
    if not df.empty:
        for off_player_id, group in df.groupby("OFF_PLAYER_ID"):
            pairing_key = f"matchup_{off_player_id}_{defender_id}_{season}"
            _save_df_cache(pairing_key, group.reset_index(drop=True))
            pairing_count += 1

    # Write the marker regardless of pairing_count -- zero matchups this
    # season is a legitimate result and shouldn't trigger a re-fetch.
    marker_payload = {
        "cached_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "defender_id": defender_id,
        "defender_name": defender_name,
        "season": season,
        "pairing_count": pairing_count,
    }
    with open(marker_path, "w") as f:
        json.dump(marker_payload, f)

    return "cached"


def main():
    require_valid("league_season_matchups")  # blocking pre-flight check -- crashes
    # loudly here, before touching data_cache/, if this endpoint's schema/row-count
    # no longer matches what get_defender_matchup_adjustment() actually expects.
    CACHE_DIR.mkdir(exist_ok=True)

    active_players = players.get_active_players()
    total_calls = len(active_players) * len(SEASONS)
    print(f"Found {len(active_players)} active players. Caching matchups across "
          f"{len(SEASONS)} season(s) -- up to {total_calls} live calls "
          f"(fewer if already cached). This should take a while.\n")

    counts = {"cached": 0, "skipped": 0, "failed": 0}
    call_num = 0
    total_pairings_this_run = 0

    for i, player in enumerate(active_players, start=1):
        defender_id = player["id"]
        defender_name = player["full_name"]

        for season in SEASONS:
            call_num += 1
            result = cache_defender_matchups(defender_id, defender_name, season)
            counts[result] += 1

            if result == "cached":
                print(f"[{call_num}/{total_calls}] ({i}/{len(active_players)} players) "
                      f"Cached matchups for {defender_name} ({season})")
            elif result == "skipped" and call_num % 50 == 0:
                # Don't spam the console for every skip -- just show periodic progress
                print(f"[{call_num}/{total_calls}] ... skipping already-cached entries ...")

            time.sleep(REQUEST_DELAY)

    print(f"\nDone. {counts['cached']} newly cached, {counts['skipped']} already had data, {counts['failed']} failed.")
    if counts["failed"] > 0:
        print("Some defenders failed -- re-run this script to retry just those (already-cached ones are skipped).")
    print("Now commit and push the data_cache/ folder to GitHub so the deployed app picks it up.")
    print("\nNote: this will add a LOT of files to data_cache/ (potentially several thousand).")
    print("If git add data_cache/ is slow, that's expected -- let it finish.")


if __name__ == "__main__":
    main()
