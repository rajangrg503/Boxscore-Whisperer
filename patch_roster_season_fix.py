"""
Patch: get_team_roster() was calling commonteamroster without a season=
argument, so it fell back to nba_api's internal default season (stale --
returns old rosters, e.g. still includes departed players like Lu Dort).

Fix: pass season=CURRENT_SEASON, using the same constant already defined
near the top of app.py, so this stays in sync with the rest of the app
instead of hardcoding a second season string somewhere new.

Usage:
    python3 patch_roster_season_fix.py
Run from the same folder as app.py.
"""

import pathlib

APP_PATH = pathlib.Path("app.py")

OLD = '''    try:
        roster = commonteamroster.CommonTeamRoster(team_id=team_id, timeout=5)
        df = roster.get_data_frames()[0]
        return list(zip(df["PLAYER_ID"], df["PLAYER"]))
    except Exception as e:
        print(f"[get_team_roster] FAILED for team_id={team_id}: {type(e).__name__}: {e}")
        return []'''

NEW = '''    try:
        roster = commonteamroster.CommonTeamRoster(
            team_id=team_id, season=CURRENT_SEASON, timeout=5
        )
        df = roster.get_data_frames()[0]
        return list(zip(df["PLAYER_ID"], df["PLAYER"]))
    except Exception as e:
        print(f"[get_team_roster] FAILED for team_id={team_id}: {type(e).__name__}: {e}")
        return []'''


def main():
    if not APP_PATH.exists():
        print("ERROR: app.py not found in current directory.")
        return

    text = APP_PATH.read_text()

    if OLD not in text:
        if "season=CURRENT_SEASON, timeout=5" in text:
            print("Already patched. No changes made.")
        else:
            print("ERROR: couldn't find the expected get_team_roster() text to patch.")
            print("Paste the output of: grep -n 'def get_team_roster' -A 12 app.py")
        return

    text = text.replace(OLD, NEW, 1)
    APP_PATH.write_text(text)
    print("Patched. get_team_roster() now passes season=CURRENT_SEASON to commonteamroster.")
    print("Restart Streamlit and re-run OKC vs Spurs.")
    print("If a roster comes back thin/empty now, that likely means the NBA hasn't")
    print("finalized 2026-27 roster cuts yet -- different issue, not a bug in this patch.")


if __name__ == "__main__":
    main()
