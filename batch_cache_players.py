"""
Batch-cache NBA player gamelogs for Boxscore Whisperer.

Run this LOCALLY (not on Streamlit Cloud) — the NBA API blocks requests
from cloud servers, so this has to build the cache on your Mac, then you
commit + push data_cache/ to GitHub so the deployed app can read it.

Usage:
    pip install nba_api
    python batch_cache_players.py

Safe to re-run: it skips any player/season pair that's already cached,
so if it gets interrupted partway through, just run it again.
"""

import json
import time
from pathlib import Path

from nba_api.stats.static import players
from nba_api.stats.endpoints import playergamelog

CACHE_DIR = Path("data_cache")
SEASONS = ["2025-26", "2024-25"]  # current + prior season
REQUEST_DELAY = 0.6  # seconds between calls, to avoid getting rate-limited
MAX_RETRIES = 3


def cache_player_season(player_id: int, season: str) -> str:
    """Fetch and cache one player's gamelog for one season.
    Returns 'cached', 'skipped', or 'failed'."""
    cache_file = CACHE_DIR / f"gamelog_{player_id}_{season}.json"

    if cache_file.exists():
        return "skipped"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log = playergamelog.PlayerGameLog(player_id=player_id, season=season)
            data = log.get_normalized_dict()

            if not data.get("PlayerGameLog"):
                return "skipped"  # player didn't play that season

            with open(cache_file, "w") as f:
                json.dump(data, f)
            return "cached"

        except Exception as e:
            if attempt == MAX_RETRIES:
                print(f"  FAILED player {player_id}, season {season}: {e}")
                return "failed"
            time.sleep(2 * attempt)  # back off and retry

    return "failed"


def main():
    CACHE_DIR.mkdir(exist_ok=True)

    active_players = players.get_active_players()
    total = len(active_players)
    print(f"Found {total} active players. This will take a while — grab a coffee.\n")

    counts = {"cached": 0, "skipped": 0, "failed": 0}

    for i, player in enumerate(active_players, start=1):
        for season in SEASONS:
            result = cache_player_season(player["id"], season)
            counts[result] += 1
            if result == "cached":
                print(f"[{i}/{total}] Cached {player['full_name']} ({season})")
            time.sleep(REQUEST_DELAY)

    print(f"\nDone. {counts['cached']} newly cached, {counts['skipped']} already had data, {counts['failed']} failed.")
    print("Now commit and push the data_cache/ folder to GitHub so the deployed app picks it up.")


if __name__ == "__main__":
    main()
