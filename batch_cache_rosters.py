"""
Batch-cache NBA team rosters for Boxscore Whisperer.

Run this LOCALLY (not on Streamlit Cloud) -- the NBA API blocks requests
from cloud servers, so this has to build the cache on your Mac, then you
commit + push data_cache/ to GitHub so the deployed app can read it.

This is the roster-caching counterpart to batch_cache_players.py. It
writes in the _save_df_cache format (a JSON file with "cached_at" and
"data" keys, "data" being a DataFrame's to_dict(orient="records")) --
NOT the raw-dict format batch_cache_players.py uses for gamelogs, since
get_team_roster() reads back via cached_or_live()/_load_df_cache(),
which expects the DataFrame-wrapped format specifically.

Usage:
    pip install nba_api
    python batch_cache_rosters.py

Safe to re-run: a team whose roster was fetched in the last
batch_freshness.DAILY hours is skipped, so running this twice in a
morning costs nothing and running it tomorrow actually refreshes.

That sentence used to say "already cached today" and the code said

    if cache_path.exists():
        return "skipped"

with no date check in it anywhere. Found on 3 Oct 2026: all thirty
rosters in the cache were from 10 September, because every run since
had skipped every team. Brandon Ingram was still listed as a Raptor,
Kawhi Leonard was not listed at all, and the app had been projecting
that lineup for three weeks while every log line read "skipping" and
every run reported success.

A roster is never finished, so there is no settled version of it to
keep -- unlike boxscore_{game_id}, where skipping on existence is
right, and is why a refresh costs two hours and not two days.
"""

import json
import time
import datetime
from pathlib import Path

import pandas as pd
from nba_api.stats.static import teams
from nba_api.stats.endpoints import commonteamroster

from data_watchdog.gate import require_valid

import batch_freshness
from batch_freshness import cached_within

CACHE_DIR = Path("data_cache")
CURRENT_SEASON = "2026-27"  # keep in sync with app.py's CURRENT_SEASON
REQUEST_DELAY = 0.6  # seconds between calls, to avoid getting rate-limited
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


def cache_team_roster(team_id: int, team_full_name: str) -> str:
    """Fetch and cache one team's roster. Returns 'cached' or 'failed'."""
    cache_key = f"roster_{team_id}"
    cache_path = _cache_key_to_path(cache_key)

    # Fetched recently enough to leave alone. NOT merely present:
    # a roster that exists is not a roster that is right, and the
    # difference was three weeks of the wrong Toronto lineup.
    if cached_within(cache_path, batch_freshness.DAILY):
        return "skipped"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            roster = commonteamroster.CommonTeamRoster(
                team_id=team_id, season=CURRENT_SEASON, timeout=10
            )
            df = roster.get_data_frames()[0]
            if df.empty:
                print(f"  WARNING: empty roster for {team_full_name} -- not caching an empty result")
                return "failed"
            _save_df_cache(cache_key, df)
            return "cached"
        except Exception as e:
            if attempt == MAX_RETRIES:
                print(f"  FAILED {team_full_name} (team_id={team_id}): {e}")
                return "failed"
            time.sleep(2 * attempt)  # back off and retry

    return "failed"


def main():
    require_valid("common_team_roster")  # blocking pre-flight check -- crashes
    # loudly here, before touching data_cache/, if this endpoint's schema/row-count
    # no longer matches what get_team_roster() actually expects.
    CACHE_DIR.mkdir(exist_ok=True)

    all_teams = teams.get_teams()
    total = len(all_teams)
    print(f"Found {total} teams. This should take under a minute.\n")

    counts = {"cached": 0, "skipped": 0, "failed": 0}

    for i, team in enumerate(all_teams, start=1):
        result = cache_team_roster(team["id"], team["full_name"])
        counts[result] += 1
        if result == "cached":
            print(f"[{i}/{total}] Cached {team['full_name']}")
        elif result == "skipped":
            print(f"[{i}/{total}] Already cached {team['full_name']} -- skipping")
        time.sleep(REQUEST_DELAY)

    print(f"\nDone. {counts['cached']} newly cached, {counts['skipped']} still fresh, {counts['failed']} failed.")
    if counts["failed"] > 0:
        print("Some teams failed -- re-run this script to retry just those (already-cached ones are skipped).")
    print("Now run tools/pack_cache.py and commit data_cache.zip so the deployed app picks it up.")


if __name__ == "__main__":
    main()
