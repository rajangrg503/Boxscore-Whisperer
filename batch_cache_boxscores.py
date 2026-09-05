"""
Batch-cache every game's boxscore (BoxScoreTraditionalV3) for
Boxscore Whisperer's missing-teammate scanning feature.

Run this LOCALLY (not on Streamlit Cloud) -- the NBA API blocks requests
from cloud servers, so this has to build the cache on your Mac, then you
commit + push data_cache/ to GitHub so the deployed app can read it.

THIS IS THE BIGGEST PRE-CACHE JOB IN THE PROJECT: a full NBA regular
season has ~1,230 games. Unlike player-vs-player matchups, a boxscore
is SHARED data -- every player who appeared in that game references the
exact same boxscore, and it never changes once the game is final. So
this is one call per game (not per player), which is why it's tractable
at all despite the volume.

Uses LeagueGameLog to pull every game ID for a season in ONE bulk call
(instead of scanning team-by-team), then fetches each game's boxscore
individually.

Writes in the exact _save_df_cache format app.py's cached_or_live()/
_load_df_cache() expect, matching the cache key
get_teammate_availability_adjustment() looks up (after patching):
    f"boxscore_{game_id}"

RUNTIME: expect 30-60+ minutes for a full season at the 0.6s delay used
elsewhere in this project. This is resumable -- each game gets its own
cache file, so Ctrl+C and re-running later picks up exactly where you
left off (already-cached games are skipped instantly).

RATE-LIMITING RISK: this is by far the most sustained hammering of the
API in this project. If you see failures start clustering (not just
occasional retries, but a consistent run of failures), stop and resume
later rather than pushing through -- a longer block is worse than a
slower rollout.

Usage:
    pip install nba_api
    python batch_cache_boxscores.py
"""

import json
import time
import datetime
from pathlib import Path

import pandas as pd
from nba_api.stats.endpoints import leaguegamelog, boxscoretraditionalv3

from data_watchdog.gate import require_valid

CACHE_DIR = Path("data_cache")
CURRENT_SEASON = "2026-27"   # keep in sync with app.py's CURRENT_SEASON
PREVIOUS_SEASON = "2025-26"  # keep in sync with app.py's PREVIOUS_SEASON
SEASONS = [CURRENT_SEASON, PREVIOUS_SEASON]
REQUEST_DELAY = 0.6
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


def get_season_game_ids(season: str) -> list:
    """One bulk call per season -- LeagueGameLog returns one row per
    team per game, so the same GAME_ID appears twice; dedupe it."""
    try:
        log = leaguegamelog.LeagueGameLog(season=season, timeout=15)
        df = log.get_data_frames()[0]
        if df.empty:
            return []
        return sorted(df["GAME_ID"].unique().tolist())
    except Exception as e:
        print(f"  FAILED to fetch game list for {season}: {e}")
        return []


def cache_boxscore(game_id: str) -> str:
    """Fetch and cache one game's boxscore. Returns 'cached', 'skipped', or 'failed'."""
    cache_key = f"boxscore_{game_id}"
    cache_path = _cache_key_to_path(cache_key)
    if cache_path.exists():
        return "skipped"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            box = boxscoretraditionalv3.BoxScoreTraditionalV3(game_id=game_id, timeout=5)
            df = box.get_data_frames()[0]
            if df.empty:
                print(f"  WARNING: empty boxscore for game {game_id} -- not caching an empty result")
                return "failed"
            _save_df_cache(cache_key, df)
            return "cached"
        except Exception as e:
            if attempt == MAX_RETRIES:
                print(f"  FAILED game {game_id}: {e}")
                return "failed"
            time.sleep(2 * attempt)

    return "failed"


def main():
    require_valid("boxscore_traditional_v3")  # blocking pre-flight check -- crashes
    # loudly here, before touching data_cache/, if this endpoint's schema/row-count
    # no longer matches what get_teammate_availability_adjustment() actually expects.
    CACHE_DIR.mkdir(exist_ok=True)

    all_game_ids = []
    for season in SEASONS:
        print(f"Fetching game list for {season}...")
        game_ids = get_season_game_ids(season)
        print(f"  Found {len(game_ids)} games for {season}")
        all_game_ids.extend(game_ids)
        time.sleep(REQUEST_DELAY)

    all_game_ids = sorted(set(all_game_ids))
    total = len(all_game_ids)
    print(f"\nCaching boxscores for {total} total games. This will take a while "
          f"(expect 30-60+ minutes) -- safe to interrupt and resume.\n")

    counts = {"cached": 0, "skipped": 0, "failed": 0}
    for i, game_id in enumerate(all_game_ids, start=1):
        result = cache_boxscore(game_id)
        counts[result] += 1
        if result == "cached":
            print(f"[{i}/{total}] Cached boxscore for game {game_id}")
        elif result == "skipped" and i % 100 == 0:
            print(f"[{i}/{total}] ... skipping already-cached entries ...")
        time.sleep(REQUEST_DELAY)

    print(f"\nDone. {counts['cached']} newly cached, {counts['skipped']} already had data, {counts['failed']} failed.")
    if counts["failed"] > 0:
        print("Some games failed -- re-run this script to retry just those (already-cached ones are skipped).")
    print("Now commit and push the data_cache/ folder to GitHub so the deployed app picks it up.")
    print("\nNote: this will add ~1,000+ files to data_cache/. If git add is slow, that's expected -- let it finish.")


if __name__ == "__main__":
    main()
