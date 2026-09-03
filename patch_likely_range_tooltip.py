#!/usr/bin/env python3
"""
patch_likely_range_tooltip.py

Visual polish patch 3/4: adds a hover tooltip explaining what "likely
range" means on each stat card.

Note: this text is rendered as raw HTML inside an f-string (not a
Streamlit widget), so Streamlit's `help=` parameter doesn't apply here.
The native equivalent is an HTML `title` attribute on the div, which
shows a tooltip on hover in desktop browsers. This degrades gracefully
on mobile/touch (no tooltip, but the number is always visible either
way) -- appropriate here since this is explicitly secondary/optional
text, unlike the baseline-blending disclaimer which stays fully visible
by design.

Idempotent: checks for the patch marker first; if already applied, skips.
Verifies exact-text occurrence count before touching the file.
"""

import sys

TARGET_FILE = "app.py"

MARKER = 'patch_likely_range_tooltip'

OLD_LINE = (
    '                f\'<div class="stat-midpoint">likely range '
    '{p["low"]:.0f}-{p["high"]:.0f}</div>\''
)

NEW_LINE = (
    '                f\'<div class="stat-midpoint" '
    'title="Likely range reflects prediction uncertainty -- narrower with more data, '
    'wider with thin samples.">likely range '
    '{p["low"]:.0f}-{p["high"]:.0f}</div><!-- patch_likely_range_tooltip -->\''
)


def main():
    with open(TARGET_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    if MARKER in content:
        print("Already patched (marker found) -- skipping, no changes made.")
        return

    count = content.count(OLD_LINE)
    print(f"found: {count} (expected: 1)")

    if count != 1:
        print("ABORTING -- occurrence count mismatch. No changes made.")
        print("Re-confirm exact live text via `sed -n \"2065,2075p\" app.py` before retrying.")
        sys.exit(1)

    content = content.replace(OLD_LINE, NEW_LINE)

    with open(TARGET_FILE, "w", encoding="utf-8") as f:
        f.write(content)

    print('Patched: added hover tooltip (title attribute) to "likely range" stat-midpoint div.')
    print("Restart Streamlit (Ctrl+C then `streamlit run app.py`) to see the change.")
    print("Note: hover over 'likely range X-Y' on a stat card in a desktop browser to see it.")


if __name__ == "__main__":
    main()
