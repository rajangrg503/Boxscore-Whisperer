#!/usr/bin/env python3
"""
patch_hero_breathing_room.py

Visual polish patch 1/4: adds vertical breathing room between the hero
header/subtitle and the form card below it, by increasing
.hero-subtitle's margin-bottom from 32px to 48px.

Idempotent: checks for the target 48px value first; if already applied, skips.
Verifies exact-text occurrence count before touching the file.
"""

import sys

TARGET_FILE = "app.py"

MARKER = "margin-bottom: 48px;  /* patch_hero_breathing_room */"

OLD_BLOCK = """.hero-subtitle {
    font-size: 16px;
    color: #9ca3af;
    text-align: center;
    margin-bottom: 32px;
}"""

NEW_BLOCK = """.hero-subtitle {
    font-size: 16px;
    color: #9ca3af;
    text-align: center;
    margin-bottom: 48px;  /* patch_hero_breathing_room */
}"""


def main():
    with open(TARGET_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    if MARKER in content:
        print("Already patched (marker found) — skipping, no changes made.")
        return

    count = content.count(OLD_BLOCK)
    print(f"found: {count} (expected: 1)")

    if count != 1:
        print("ABORTING — occurrence count mismatch. No changes made.")
        print("Re-run `sed -n '1324,1330p' app.py` and confirm exact live text before retrying.")
        sys.exit(1)

    content = content.replace(OLD_BLOCK, NEW_BLOCK)

    with open(TARGET_FILE, "w", encoding="utf-8") as f:
        f.write(content)

    print("Patched .hero-subtitle margin-bottom: 32px -> 48px")
    print("Restart Streamlit (Ctrl+C then `streamlit run app.py`) to see the change.")


if __name__ == "__main__":
    main()
