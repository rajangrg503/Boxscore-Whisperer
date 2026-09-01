"""
Boxscore Whisperer -- local cache refresh script.

RUN THIS LOCALLY (where nba_api actually works), NOT on Streamlit Cloud.
It pre-fetches commonly-needed data and writes it into data_cache/,
in the exact format app.py's cached_or_live() reads. After running
this, commit and push the updated data_cache/ folder to GitHub --
Streamlit Cloud will pick it up on the next redeploy (or trigger a
manual reboot from the app's "Manage app" menu).

Usage:
    python3 refresh_cache.py

Edit PLAYERS_TO_CACHE below to add/remove players you want covered.
Team-wide stats (needed for every prediction, regardless of player)
are always refreshed in full -- that part covers all 30 teams
automatically.
"""

import os
import json
import time
import datetime

import pandas as pd
from nba_api.stats.static import players
from nba_api.stats.endpoints import playergamelog, leaguedashteamstats

CURRENT_SEASON = "2026-27"   # keep in sync with app.py
PREVIOUS_SEASON = "2025-26"
HEAD_TO_HEAD_SEASONS = [CURRENT_SEASON, PREVIOUS_SEASON, "2024-25", "2023-24"]

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_cache")

# Add any players you want the cloud app to reliably support -- their
# names must match nba_api's full-name lookup exactly.
PLAYERS_TO_CACHE = [
    "Shai Gilgeous-Alexander",
    "Giannis Antetokounmpo",
    "Bam Adebayo",
    "Luka Doncic",
    "Nikola Jokic",
    "Jayson Tatum",
    "LeBron James",
    "Stephen Curry",
    "Anthony Edwards",
    "Victor Wembanyama",
]


def _cache_key_to_path(key):
    safe_key = "".join(c if (c.isalnum() or c in "_-") else "_" for c in key)
    return os.path.join(CACHE_DIR, f"{safe_key}.json")


def save_df_cache(key, df):
    os.makedirs(CACHE_DIR, exist_ok=True)
    payload = {
        "cached_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "data": df.to_dict(orient="records"),
    }
    with open(_cache_key_to_path(key), "w") as f:
        json.dump(payload, f)


def fetch_combined_game_log(player_id, season):
    frames = []
    for season_type in ["Regular Season", "Playoffs"]:
        try:
            log = playergamelog.PlayerGameLog(
                player_id=player_id, season=season, season_type_all_star=season_type
            )
            df = log.get_data_frames()[0]
            if not df.empty:
                frames.append(df)
        except Exception as e:
            print(f"    ! {season_type} fetch failed: {e}")
        time.sleep(0.6)  # be polite to the API -- this is a local, one-off bulk job
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def refresh_team_stats():
    print("Refreshing league-wide team stats (all 30 teams, both seasons)...")
    for season in [CURRENT_SEASON, PREVIOUS_SEASON]:
        try:
            stats = leaguedashteamstats.LeagueDashTeamStats(
                season=season, measure_type_detailed_defense="Advanced"
            )
            df = stats.get_data_frames()[0]
            cols = ["TEAM_ID", "TEAM_NAME", "DEF_RATING", "PACE", "GP"]
            df = df[[c for c in cols if c in df.columns]].copy()
            save_df_cache(f"team_stats_advanced_{season}", df)
            print(f"  Cached team_stats_advanced_{season} ({len(df)} teams)")
        except Exception as e:
            print(f"  ! Failed for {season}: {e}")
        time.sleep(0.6)


def refresh_player_game_logs():
    print(f"Refreshing game logs for {len(PLAYERS_TO_CACHE)} players "
          f"across {len(HEAD_TO_HEAD_SEASONS)} seasons each...")
    for name in PLAYERS_TO_CACHE:
        match = players.find_players_by_full_name(name)
        if not match:
            print(f"  ! No player found for '{name}' -- check spelling")
            continue
        player_id = match[0]["id"]
        print(f"  {name} (id {player_id}):")
        for season in HEAD_TO_HEAD_SEASONS:
            df = fetch_combined_game_log(player_id, season)
            if not df.empty:
                save_df_cache(f"gamelog_{player_id}_{season}", df)
                print(f"    Cached gamelog_{player_id}_{season} ({len(df)} games)")
            else:
                print(f"    (no games found for {season} -- skipping)")


if __name__ == "__main__":
    print(f"Cache directory: {CACHE_DIR}\n")
    refresh_team_stats()
    print()
    refresh_player_game_logs()
    print("\nDone. Now commit and push the data_cache/ folder to GitHub "
          "(via GitHub Desktop) so the deployed app picks up this data.")
