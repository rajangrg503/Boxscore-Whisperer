"""
Batch-cache NBA league-wide hustle stats (deflections) for Boxscore Whisperer.

Run this LOCALLY (not on Streamlit Cloud) -- the NBA API blocks requests
from cloud servers, so this has to build the cache on your Mac, then you
commit + push data_cache/ to GitHub so the deployed app can read it.

Unlike rosters (one call per team), LeagueHustleStatsTeam returns ALL 30
teams in a single response per season, so this only needs 2 calls total
(current season + previous season) to cover every possible Man-to-man
scheme calculation, regardless of which team the app is predicting for.

Writes in the _save_df_cache format (a JSON file with "cached_at" and
"data" keys) under the exact cache keys get_synergy_scheme_adjustment()
looks for: hustle_team_stats_<season>.

Usage:
    pip install nba_api
    python batch_cache_hustle_stats.py

Safe to re-run: it skips any season that's already cached. Delete the
relevant data_cache/hustle_team_stats_<season>.json file if you want to
force a re-fetch (e.g. partway through a season, for fresher numbers).
"""

import json
import time
import datetime
from pathlib import Path

import pandas as pd
from nba_api.stats.endpoints import leaguehustlestatsteam

CACHE_DIR = Path("data_cache")
CURRENT_SEASON = "2026-27"   # keep in sync with app.py's CURRENT_SEASON
PREVIOUS_SEASON = "2025-26"  # keep in sync with app.py's PREVIOUS_SEASON
REQUEST_DELAY = 0.6
MAX_RETRIES = 3


def _cache_key_to_path(key: str) -> Path:
    safe_key = "".join(c if (c.isalnum() or c in "_-") else "_" for c in key)
    return CACHE_DIR / f"{safe_key}.json"


def _save_df_cache(key: str, df: pd.DataFrame) -> None:
    path = _cache_key_to_path(key)
    payload = {
        "cached_at": datetime.datetime.now().isoformat(),
        "data": df.to_dict(orient="records"),
    }
    with open(path, "w") as f:
        json.dump(payload, f)


def cache_hustle_stats(season: str) -> str:
    """Fetch and cache one season's league-wide hustle stats. Returns 'cached', 'skipped', or 'failed'."""
    cache_key = f"hustle_team_stats_{season}"
    cache_path = _cache_key_to_path(cache_key)

    if cache_path.exists():
        return "skipped"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            hustle = leaguehustlestatsteam.LeagueHustleStatsTeam(
                season=season, per_mode_time="PerGame", timeout=10
            )
            df = hustle.get_data_frames()[0]
            if df.empty or "DEFLECTIONS" not in df.columns:
                print(f"  WARNING: empty or malformed hustle data for {season} -- not caching")
                return "failed"
            _save_df_cache(cache_key, df)
            return "cached"
        except Exception as e:
            if attempt == MAX_RETRIES:
                print(f"  FAILED {season}: {e}")
                return "failed"
            time.sleep(2 * attempt)  # back off and retry

    return "failed"


def main():
    CACHE_DIR.mkdir(exist_ok=True)

    seasons = [CURRENT_SEASON, PREVIOUS_SEASON]
    print(f"Caching hustle stats for {len(seasons)} season(s). This should take a few seconds.\n")

    counts = {"cached": 0, "skipped": 0, "failed": 0}

    for i, season in enumerate(seasons, start=1):
        result = cache_hustle_stats(season)
        counts[result] += 1
        if result == "cached":
            print(f"[{i}/{len(seasons)}] Cached {season}")
        elif result == "skipped":
            print(f"[{i}/{len(seasons)}] Already cached {season} -- skipping")
        time.sleep(REQUEST_DELAY)

    print(f"\nDone. {counts['cached']} newly cached, {counts['skipped']} already had data, {counts['failed']} failed.")
    if counts["failed"] > 0:
        print("Some seasons failed -- re-run this script to retry just those (already-cached ones are skipped).")
    print("Now commit and push the data_cache/ folder to GitHub so the deployed app picks it up.")


if __name__ == "__main__":
    main()
