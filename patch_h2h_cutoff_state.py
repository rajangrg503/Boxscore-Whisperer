"""
Fixes a NameError crash in the Head-to-Head section of app.py.

The bug: h2h_cutoff and roster_change_active were computed only when the
"Predict statline" form was first submitted, but never saved into
st.session_state["results"]. On any LATER Streamlit rerun (e.g. touching
the "Stat to chart" dropdown), the script restores everything else from
session_state but these two variables were never recreated -- so the
Head-to-Head section crashes with NameError: name 'h2h_cutoff' is not
defined.

This patch adds both variables to the save dict and the restore block.

Run this once, from the same folder as app.py.

Usage:
    python3 patch_h2h_cutoff_state.py
"""

from pathlib import Path

TARGET = Path("app.py")

OLD_SAVE = '''        "game_log": game_log_for_hitrate,
        "using_h2h": using_h2h,
    }'''
NEW_SAVE = '''        "game_log": game_log_for_hitrate,
        "using_h2h": using_h2h,
        "h2h_cutoff": h2h_cutoff,
        "roster_change_active": roster_change_active,
    }'''

OLD_RESTORE = '''    game_log_for_hitrate = r["game_log"]
    using_h2h = r["using_h2h"]'''
NEW_RESTORE = '''    game_log_for_hitrate = r["game_log"]
    using_h2h = r["using_h2h"]
    h2h_cutoff = r["h2h_cutoff"]
    roster_change_active = r["roster_change_active"]'''


def main():
    text = TARGET.read_text()

    if '"h2h_cutoff": h2h_cutoff' in text and 'h2h_cutoff = r["h2h_cutoff"]' in text:
        print("Already patched -- no changes needed.")
        return

    missing = []
    if OLD_SAVE not in text:
        missing.append("save block")
    if OLD_RESTORE not in text:
        missing.append("restore block")

    if missing:
        print("Could not find expected text for: " + ", ".join(missing))
        print("The file may differ from what this patch expects -- ")
        print("open app.py and edit it by hand instead. No changes made.")
        return

    text = text.replace(OLD_SAVE, NEW_SAVE)
    text = text.replace(OLD_RESTORE, NEW_RESTORE)

    TARGET.write_text(text)
    print("Patched app.py successfully.")
    print("h2h_cutoff and roster_change_active now survive Streamlit reruns.")


if __name__ == "__main__":
    main()
