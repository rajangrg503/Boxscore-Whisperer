"""
Batch-cache PlayerEstimatedMetrics (league-wide net ratings) for
Boxscore Whisperer.

Run this LOCALLY (not on Streamlit Cloud) -- the NBA API blocks requests
from cloud servers, so this has to build the cache on your Mac, then you
commit + push data_cache/ to GitHub so the deployed app can read it.

This is a bulk, league-wide endpoint -- only 2 live calls total (current
+ previous season), same shape as batch_cache_hustle_stats.py.

Writes in the exact _save_df_cache format app.py's cached_or_live()/
_load_df_cache() expect, matching the cache key
get_opponent_missing_adjustment() looks up (after patching):
    f"player_estimated_metrics_{try_season}"

Usage:
    pip install nba_api
    python batch_cache_player_estimated_metrics.py
"""

import json
import time
import datetime
from pathlib import Path

import pandas as pd
from nba_api.stats.endpoints import playerestimatedmetrics

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
    CACHE_DIR.mkdir(exist_ok=True)
    payload = {
        "cached_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "data": df.to_dict(orient="records"),
    }
    with open(_cache_key_to_path(key), "w") as f:
        json.dump(payload, f)


def cache_metrics(season: str) -> str:
    cache_key = f"player_estimated_metrics_{season}"
    cache_path = _cache_key_to_path(cache_key)
    if cache_path.exists():
        return "skipped"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            metrics = playerestimatedmetrics.PlayerEstimatedMetrics(season=season, timeout=10)
            df = metrics.get_data_frames()[0]
            if df.empty:
                print(f"  WARNING: empty estimated metrics for {season} -- not caching an empty result")
                return "failed"
            _save_df_cache(cache_key, df)
            return "cached"
        except Exception as e:
            if attempt == MAX_RETRIES:
                print(f"  FAILED {season}: {e}")
                return "failed"
            time.sleep(2 * attempt)

    return "failed"


def main():
    require_valid("player_estimated_metrics")  # blocking pre-flight check -- crashes
    # loudly here, before touching data_cache/, if this endpoint's schema/row-count
    # no longer matches what get_opponent_missing_adjustment() etc. actually expect.
    CACHE_DIR.mkdir(exist_ok=True)
    print(f"Caching player estimated metrics for {len(SEASONS)} season(s): {SEASONS}\n")

    counts = {"cached": 0, "skipped": 0, "failed": 0}
    for i, season in enumerate(SEASONS, start=1):
        result = cache_metrics(season)
        counts[result] += 1
        print(f"[{i}/{len(SEASONS)}] {season}: {result}")
        time.sleep(REQUEST_DELAY)

    print(f"\nDone. {counts['cached']} newly cached, {counts['skipped']} already had data, {counts['failed']} failed.")
    print("Now commit and push the data_cache/ folder to GitHub so the deployed app picks it up.")


if __name__ == "__main__":
    main()
