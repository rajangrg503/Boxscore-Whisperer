"""
Fixes another NameError crash, same root cause as the h2h_cutoff one:
post_change_thin_sample was computed only during the initial form
submission and never saved into st.session_state["results"], so it's
missing on later reruns.

Run this once, from the same folder as app.py.

Usage:
    python3 patch_post_change_thin_sample_state.py
"""

from pathlib import Path

TARGET = Path("app.py")

OLD_SAVE = '''        "h2h_cutoff": h2h_cutoff,
        "roster_change_active": roster_change_active,
    }'''
NEW_SAVE = '''        "h2h_cutoff": h2h_cutoff,
        "roster_change_active": roster_change_active,
        "post_change_thin_sample": post_change_thin_sample,
    }'''

OLD_RESTORE = '''    h2h_cutoff = r["h2h_cutoff"]
    roster_change_active = r["roster_change_active"]'''
NEW_RESTORE = '''    h2h_cutoff = r["h2h_cutoff"]
    roster_change_active = r["roster_change_active"]
    post_change_thin_sample = r["post_change_thin_sample"]'''


def main():
    text = TARGET.read_text()

    if '"post_change_thin_sample": post_change_thin_sample' in text and \
       'post_change_thin_sample = r["post_change_thin_sample"]' in text:
        print("Already patched -- no changes needed.")
        return

    missing = []
    if OLD_SAVE not in text:
        missing.append("save block (did you run patch_h2h_cutoff_state.py first?)")
    if OLD_RESTORE not in text:
        missing.append("restore block (did you run patch_h2h_cutoff_state.py first?)")

    if missing:
        print("Could not find expected text for: " + ", ".join(missing))
        print("No changes made.")
        return

    text = text.replace(OLD_SAVE, NEW_SAVE)
    text = text.replace(OLD_RESTORE, NEW_RESTORE)

    TARGET.write_text(text)
    print("Patched app.py successfully.")
    print("post_change_thin_sample now survives Streamlit reruns.")


if __name__ == "__main__":
    main()
