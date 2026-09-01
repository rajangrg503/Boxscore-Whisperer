"""
Adds Synergy defensive-scheme caching to refresh_cache.py, so the deployed
app can read scheme data from data_cache/ instead of waiting on a live NBA
API call that reliably fails (and, until the other patch, times out slowly)
on Streamlit Cloud.

Run this once, from the same folder as refresh_cache.py.

Usage:
    python3 patch_refresh_cache_synergy.py

Safe to run: checks all three edits can be made before changing anything.
If any piece doesn't match exactly, it aborts without touching the file.
"""

from pathlib import Path

TARGET = Path("refresh_cache.py")

OLD_IMPORT = 'from nba_api.stats.endpoints import playergamelog, leaguedashteamstats'
NEW_IMPORT = 'from nba_api.stats.endpoints import playergamelog, leaguedashteamstats, synergyplaytypes'

OLD_FUNC_ANCHOR = 'def refresh_player_game_logs():'
NEW_FUNC_BLOCK = '''SYNERGY_PLAY_TYPES = [
    "PRBallHandler",
    "Isolation",
    "PRRollman",
    "OffScreen",
    "Transition",
    "Postup",
]


def refresh_synergy_scheme_data():
    print(f"Refreshing Synergy defensive play-type data for {PREVIOUS_SEASON} "
          f"({len(SYNERGY_PLAY_TYPES)} play types)...")
    for play_type in SYNERGY_PLAY_TYPES:
        try:
            data = synergyplaytypes.SynergyPlayTypes(
                league_id="00",
                per_mode_simple="PerGame",
                player_or_team_abbreviation="T",
                season_type_all_star="Regular Season",
                season=PREVIOUS_SEASON,
                type_grouping_nullable="defensive",
                play_type_nullable=play_type,
                timeout=5,
            )
            df = data.get_data_frames()[0]
            if not df.empty:
                save_df_cache(f"synergy_{play_type}_{PREVIOUS_SEASON}", df)
                print(f"  Cached synergy_{play_type}_{PREVIOUS_SEASON} ({len(df)} teams)")
            else:
                print(f"  (no data returned for {play_type} -- skipping)")
        except Exception as e:
            print(f"  ! Failed for {play_type}: {e}")
        time.sleep(0.6)


def refresh_player_game_logs():'''

OLD_MAIN = '''    refresh_team_stats()
    print()
    refresh_player_game_logs()'''
NEW_MAIN = '''    refresh_team_stats()
    print()
    refresh_synergy_scheme_data()
    print()
    refresh_player_game_logs()'''


def main():
    text = TARGET.read_text()

    if "def refresh_synergy_scheme_data" in text:
        print("Already patched -- no changes needed.")
        return

    missing = []
    if OLD_IMPORT not in text:
        missing.append("import line")
    if OLD_FUNC_ANCHOR not in text:
        missing.append("refresh_player_game_logs() anchor")
    if OLD_MAIN not in text:
        missing.append("__main__ block")

    if missing:
        print("Could not find expected text for: " + ", ".join(missing))
        print("The file may differ from what this patch expects -- ")
        print("open refresh_cache.py and edit it by hand instead. No changes made.")
        return

    text = text.replace(OLD_IMPORT, NEW_IMPORT)
    text = text.replace(OLD_FUNC_ANCHOR, NEW_FUNC_BLOCK)
    text = text.replace(OLD_MAIN, NEW_MAIN)

    TARGET.write_text(text)
    print("Patched refresh_cache.py successfully.")
    print("Added refresh_synergy_scheme_data() and wired it into the main run.")


if __name__ == "__main__":
    main()
