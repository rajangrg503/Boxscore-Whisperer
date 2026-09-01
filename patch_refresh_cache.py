"""
Patches refresh_cache.py to loop over ALL active NBA players instead of
the hardcoded PLAYERS_TO_CACHE list. Run this once, from the same folder
as refresh_cache.py.

Usage:
    python3 patch_refresh_cache.py

Safe to run: if the file doesn't match exactly what this script expects,
it aborts without changing anything, rather than risk a bad edit.
"""

from pathlib import Path

TARGET = Path("refresh_cache.py")

OLD = '''def refresh_player_game_logs():
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
                print(f"    (no games found for {season} -- skipping)")'''

NEW = '''def refresh_player_game_logs():
    active_players = players.get_active_players()
    print(f"Refreshing game logs for {len(active_players)} active players "
          f"across {len(HEAD_TO_HEAD_SEASONS)} seasons each...")
    for player in active_players:
        name = player["full_name"]
        player_id = player["id"]
        print(f"  {name} (id {player_id}):")
        for season in HEAD_TO_HEAD_SEASONS:
            df = fetch_combined_game_log(player_id, season)
            if not df.empty:
                save_df_cache(f"gamelog_{player_id}_{season}", df)
                print(f"    Cached gamelog_{player_id}_{season} ({len(df)} games)")
            else:
                print(f"    (no games found for {season} -- skipping)")'''


def main():
    text = TARGET.read_text()

    if NEW in text:
        print("Already patched -- no changes needed.")
        return

    if OLD not in text:
        print("Could not find the expected refresh_player_game_logs() block.")
        print("The file may differ slightly from what this patch expects --")
        print("open refresh_cache.py and edit it by hand instead. No changes made.")
        return

    TARGET.write_text(text.replace(OLD, NEW))
    print("Patched refresh_cache.py successfully.")
    print("refresh_player_game_logs() now loops over every active NBA player")
    print("instead of the PLAYERS_TO_CACHE list.")


if __name__ == "__main__":
    main()
