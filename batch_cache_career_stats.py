"""
Batch-cache PlayerCareerStats for every active player, for
Boxscore Whisperer.

Run this LOCALLY (not on Streamlit Cloud) -- the NBA API blocks requests
from cloud servers, so this has to build the cache on your Mac, then you
commit + push data_cache/ to GitHub so the deployed app can read it.

PlayerCareerStats returns a player's ENTIRE career (all seasons) in one
response, so this needs only 1 call per active player -- no per-season
loop, unlike the matchup/hustle-stats scripts. ~530 active players =
~530 live calls total.

Writes in the exact _save_df_cache format app.py's cached_or_live()/
_load_df_cache() expect, matching the cache key
get_opponent_missing_adjustment() looks up (after patching):
    f"career_stats_{player_id}"

Usage:
    pip install nba_api
    python batch_cache_career_stats.py

Safe to re-run: skips any player already cached. Delete the relevant
data_cache/career_stats_<player_id>.json file to force a re-fetch (e.g.
partway through a season, to pick up updated GP/MIN totals).
"""

import json
import time
import datetime
from pathlib import Path

import pandas as pd
from nba_api.stats.static import players
from nba_api.stats.endpoints import playercareerstats

from data_watchdog.gate import require_valid

CACHE_DIR = Path("data_cache")
REQUEST_DELAY = 0.6
MAX_RETRIES = 3


def _cache_key_to_path(key: str) -> Path:
    safe_key = "".join(c if (c.isalnum() or c in "_-") else "_" for c in key)
    return CACHE_DIR / f"{safe_key}.json"


def _save_df_cache(key: str, df: pd.DataFrame) -> None:
    CACHE_DIR.mkdir(exist_ok=True)
    payload = {
        "cached_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "data": df.to_dict(orient="records"),
    }
    with open(_cache_key_to_path(key), "w") as f:
        json.dump(payload, f)


def cache_career_stats(player_id: int, player_name: str) -> str:
    cache_key = f"career_stats_{player_id}"
    cache_path = _cache_key_to_path(cache_key)
    if cache_path.exists():
        return "skipped"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            career = playercareerstats.PlayerCareerStats(player_id=player_id, timeout=5)
            df = career.get_data_frames()[0]
            if df.empty:
                print(f"  WARNING: empty career stats for {player_name} -- not caching an empty result")
                return "failed"
            _save_df_cache(cache_key, df)
            return "cached"
        except Exception as e:
            if attempt == MAX_RETRIES:
                print(f"  FAILED {player_name}: {e}")
                return "failed"
            time.sleep(2 * attempt)

    return "failed"


def main():
    require_valid("player_career_stats")  # blocking pre-flight check -- crashes
    # loudly here, before touching data_cache/, if this endpoint's schema/row-count
    # no longer matches what get_opponent_missing_adjustment() etc. actually expect.
    CACHE_DIR.mkdir(exist_ok=True)
    active_players = players.get_active_players()
    total = len(active_players)
    print(f"Caching career stats for {total} active players. This will take a while.\n")

    counts = {"cached": 0, "skipped": 0, "failed": 0}
    for i, player in enumerate(active_players, start=1):
        result = cache_career_stats(player["id"], player["full_name"])
        counts[result] += 1
        if result == "cached":
            print(f"[{i}/{total}] Cached career stats for {player['full_name']}")
        elif result == "skipped" and i % 50 == 0:
            print(f"[{i}/{total}] ... skipping already-cached entries ...")
        time.sleep(REQUEST_DELAY)

    print(f"\nDone. {counts['cached']} newly cached, {counts['skipped']} already had data, {counts['failed']} failed.")
    if counts["failed"] > 0:
        print("Some players failed -- re-run this script to retry just those (already-cached ones are skipped).")
    print("Now commit and push the data_cache/ folder to GitHub so the deployed app picks it up.")


if __name__ == "__main__":
    main()
