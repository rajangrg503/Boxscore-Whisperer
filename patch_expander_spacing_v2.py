#!/usr/bin/env python3
"""
patch_expander_spacing_v2.py

Visual polish patch 2/4 (corrected). Supersedes patch_expander_spacing.py,
which aborted safely with "found: 0" because it guessed 4-space indentation
for key_players_input when the live file actually uses 8 spaces (confirmed
via repr() line-by-line inspection: line 1712 has indent=8, line 1713 has
indent=12).

Fix: add a matching st.markdown("---") divider before key_players_input,
at the correct 8-space indent level, using the same separator convention
already used elsewhere in this file (e.g. before roster_change_checked).

Idempotent: checks for the patch marker first; if already applied, skips.
Verifies exact-text occurrence count before touching the file.
"""

import sys

TARGET_FILE = "app.py"

MARKER = '# patch_expander_spacing'

OLD_BLOCK = (
    '        key_players_input = st.multiselect(\n'
    '            "Also check history vs. specific opposing player(s) (optional)",'
)

NEW_BLOCK = (
    '        st.markdown("---")  # patch_expander_spacing\n'
    '        key_players_input = st.multiselect(\n'
    '            "Also check history vs. specific opposing player(s) (optional)",'
)


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
        print("Re-confirm exact live text/indentation via the repr() inspection method before retrying.")
        sys.exit(1)

    content = content.replace(OLD_BLOCK, NEW_BLOCK)

    with open(TARGET_FILE, "w", encoding="utf-8") as f:
        f.write(content)

    print("Patched: added st.markdown(\"---\") divider before key_players_input.")
    print("Restart Streamlit (Ctrl+C then `streamlit run app.py`) to see the change.")


if __name__ == "__main__":
    main()
