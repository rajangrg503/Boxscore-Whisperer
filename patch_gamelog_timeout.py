"""
Patches app.py to add a 5s timeout to the core PlayerGameLog live call in
fetch_combined_game_log(). This is the SAME bug as the Synergy and
post-roster-change fixes -- a live nba_api call with no timeout set,
which just hangs indefinitely on Streamlit Cloud instead of failing
fast into the cached-data fallback.

This one is more important than the other two: fetch_combined_game_log()
is called for EVERY prediction (via get_season_baseline), not just the
roster-change/combo edge cases -- so an unprotected hang here freezes
even the simplest possible query.

Run this once, from the same folder as app.py.

Usage:
    python3 patch_gamelog_timeout.py
"""

from pathlib import Path

TARGET = Path("app.py")

OLD = '''            log = playergamelog.PlayerGameLog(
                player_id=player_id, season=season, season_type_all_star=season_type
            )'''

NEW = '''            log = playergamelog.PlayerGameLog(
                player_id=player_id, season=season, season_type_all_star=season_type,
                timeout=5,
            )'''


def main():
    text = TARGET.read_text()

    if NEW in text:
        print("Already patched -- no changes needed.")
        return

    if OLD not in text:
        print("Could not find the expected PlayerGameLog() call in app.py.")
        print("The file may differ from what this patch expects -- ")
        print("open app.py and edit it by hand instead. No changes made.")
        return

    TARGET.write_text(text.replace(OLD, NEW))
    print("Patched app.py successfully.")
    print("The core baseline PlayerGameLog call will now time out in 5s")
    print("instead of hanging indefinitely, falling back to cached data.")


if __name__ == "__main__":
    main()
