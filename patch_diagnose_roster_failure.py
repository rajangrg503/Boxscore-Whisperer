"""
Diagnostic patch: makes get_team_roster() print the real exception to the
terminal instead of silently swallowing it and returning [].

This does NOT fix the underlying bug -- it's purely so the next test run
tells us what actually went wrong for San Antonio's roster pull. Once we
see the real error in the terminal, we can write a targeted fix.

Usage:
    python3 patch_diagnose_roster_failure.py
Run from the same folder as app.py.
"""

import pathlib

APP_PATH = pathlib.Path("app.py")

OLD = '''def get_team_roster(team_id):
    """Pull current live roster for a team via commonteamroster, with the
    same timeout=5 treatment as every other live call in this app.
    Returns a list of (player_id, player_name) tuples, or [] on failure."""
    from nba_api.stats.endpoints import commonteamroster
    try:
        roster = commonteamroster.CommonTeamRoster(team_id=team_id, timeout=5)
        df = roster.get_data_frames()[0]
        return list(zip(df["PLAYER_ID"], df["PLAYER"]))
    except Exception:
        return []'''

NEW = '''def get_team_roster(team_id):
    """Pull current live roster for a team via commonteamroster, with the
    same timeout=5 treatment as every other live call in this app.
    Returns a list of (player_id, player_name) tuples, or [] on failure."""
    from nba_api.stats.endpoints import commonteamroster
    try:
        roster = commonteamroster.CommonTeamRoster(team_id=team_id, timeout=5)
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
        if "[get_team_roster] FAILED" in text:
            print("Already patched with diagnostics. No changes made.")
        else:
            print("ERROR: couldn't find the expected get_team_roster() text to patch.")
            print("The file may differ from what was appended earlier -- paste")
            print("the output of: grep -n 'def get_team_roster' -A 10 app.py")
        return

    text = text.replace(OLD, NEW, 1)
    APP_PATH.write_text(text)
    print("Patched. get_team_roster() will now print the real exception to your terminal.")
    print("Restart Streamlit, re-run OKC vs Spurs, and paste whatever prints in the terminal.")


if __name__ == "__main__":
    main()
