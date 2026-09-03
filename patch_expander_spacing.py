#!/usr/bin/env python3
"""
patch_expander_spacing.py

Visual polish patch 2/4: the "Advanced options" expander already uses
st.markdown("---") to separate the roster-change section from what comes
before it, but there is no divider between the roster-change section and
the "key players" (specific opposing player history) section right after
it -- that's the one seam in the expander with no breathing room, causing
the "crowding" the user flagged.

Fix: add a matching st.markdown("---") before key_players_input, using
the exact same separator convention already used elsewhere in this file.

Idempotent: checks for the patch marker first; if already applied, skips.
Verifies exact-text occurrence count before touching the file.
"""

import sys

TARGET_FILE = "app.py"

MARKER = '# patch_expander_spacing'

OLD_BLOCK = '''    key_players_input = st.multiselect(
        "Also check history vs. specific opposing player(s) (optional)",'''

NEW_BLOCK = '''    st.markdown("---")  # patch_expander_spacing
    key_players_input = st.multiselect(
        "Also check history vs. specific opposing player(s) (optional)",'''


def main():
    with open(TARGET_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    if MARKER in content:
        print("Already patched (marker found) -- skipping, no changes made.")
        return

    count = content.count(OLD_BLOCK)
    print(f"found: {count} (expected: 1)")

    if count != 1:
        print("ABORTING -- occurrence count mismatch. No changes made.")
        print("Re-run `sed -n '1705,1720p' app.py` and confirm exact live text before retrying.")
        sys.exit(1)

    content = content.replace(OLD_BLOCK, NEW_BLOCK)

    with open(TARGET_FILE, "w", encoding="utf-8") as f:
        f.write(content)

    print("Patched: added st.markdown(\"---\") divider before key_players_input.")
    print("Restart Streamlit (Ctrl+C then `streamlit run app.py`) to see the change.")


if __name__ == "__main__":
    main()
