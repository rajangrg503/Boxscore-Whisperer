"""
Fix: get_team_roster() was failing with a plain ReadTimeout on the second
consecutive commonteamroster call (timeout=5, no retry) -- transient
network flakiness, not a data-availability or team-ID issue.

Fix: retry once with a longer timeout (10s) before giving up. Keeps the
existing behavior (return [] and print the error) if both attempts fail.

Usage:
    python3 patch_roster_retry.py
Run from the same folder as app.py.
"""

import pathlib

APP_PATH = pathlib.Path("app.py")

OLD = '''    try:
        roster = commonteamroster.CommonTeamRoster(
            team_id=team_id, season=CURRENT_SEASON, timeout=5
        )
        df = roster.get_data_frames()[0]
        return list(zip(df["PLAYER_ID"], df["PLAYER"]))
    except Exception as e:
        print(f"[get_team_roster] FAILED for team_id={team_id}: {type(e).__name__}: {e}")
        return []'''

NEW = '''    for attempt_timeout in (5, 10):
        try:
            roster = commonteamroster.CommonTeamRoster(
                team_id=team_id, season=CURRENT_SEASON, timeout=attempt_timeout
            )
            df = roster.get_data_frames()[0]
            return list(zip(df["PLAYER_ID"], df["PLAYER"]))
        except Exception as e:
            print(
                f"[get_team_roster] attempt (timeout={attempt_timeout}) failed "
                f"for team_id={team_id}: {type(e).__name__}: {e}"
            )
    return []'''


def main():
    if not APP_PATH.exists():
        print("ERROR: app.py not found in current directory.")
        return

    text = APP_PATH.read_text()

    if OLD not in text:
        if "for attempt_timeout in (5, 10):" in text:
            print("Already patched. No changes made.")
        else:
            print("ERROR: couldn't find the expected get_team_roster() text to patch.")
            print("Paste the output of: grep -n 'def get_team_roster' -A 14 app.py")
        return

    text = text.replace(OLD, NEW, 1)
    APP_PATH.write_text(text)
    print("Patched. get_team_roster() now retries once (5s, then 10s) before giving up.")
    print("Restart Streamlit and re-run OKC vs Spurs.")


if __name__ == "__main__":
    main()
