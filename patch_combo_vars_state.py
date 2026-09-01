"""
Fixes three more variables with the same NameError-on-rerun bug:
key_player_ids, no_combo_data, and valid_ids -- all tied to the
"check history vs specific opposing player(s)" feature. Set only
during the initial form submission, referenced again later, missing
from session_state.

Run this once, from the same folder as app.py. Requires the two
previous patches (h2h_cutoff, post_change_thin_sample) to already be
applied.

Usage:
    python3 patch_combo_vars_state.py
"""

from pathlib import Path

TARGET = Path("app.py")

OLD_SAVE = '''        "post_change_thin_sample": post_change_thin_sample,
    }'''
NEW_SAVE = '''        "post_change_thin_sample": post_change_thin_sample,
        "key_player_ids": key_player_ids,
        "no_combo_data": no_combo_data,
        "valid_ids": valid_ids,
    }'''

OLD_RESTORE = '''    post_change_thin_sample = r["post_change_thin_sample"]'''
NEW_RESTORE = '''    post_change_thin_sample = r["post_change_thin_sample"]
    key_player_ids = r["key_player_ids"]
    no_combo_data = r["no_combo_data"]
    valid_ids = r["valid_ids"]'''


def main():
    text = TARGET.read_text()

    if '"key_player_ids": key_player_ids' in text and \
       'key_player_ids = r["key_player_ids"]' in text:
        print("Already patched -- no changes needed.")
        return

    missing = []
    if OLD_SAVE not in text:
        missing.append("save block (run the previous two patches first)")
    if OLD_RESTORE not in text:
        missing.append("restore block (run the previous two patches first)")

    if missing:
        print("Could not find expected text for: " + ", ".join(missing))
        print("No changes made.")
        return

    text = text.replace(OLD_SAVE, NEW_SAVE)
    text = text.replace(OLD_RESTORE, NEW_RESTORE)

    TARGET.write_text(text)
    print("Patched app.py successfully.")
    print("key_player_ids, no_combo_data, and valid_ids now survive Streamlit reruns.")


if __name__ == "__main__":
    main()
