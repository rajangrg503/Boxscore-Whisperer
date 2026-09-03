"""
"Protected Text Logger" pass: adds a professional, descriptive label in
front of every raw x{value:.3f} multiplier shown in the breakdown notes,
WITHOUT hiding the number itself (both stay visible, per your choice).

Targets only the genuine raw-formula spots:
  - def_note (opponent defense adjustment)
  - get_opponent_missing_adjustment (missing opponent players)
  - get_synergy_scheme_adjustment (all branches: manual fallback x3, real data)

Deliberately leaves untouched the stat-comparison notes (teammate
availability, new teammate impact, defender matchup) -- those already
read as real transparent trend data ("PTS 24.1 vs 21.0 (+14.8%)"), not
a raw formula, so there's nothing to relabel there.

Matches on the small f-string fragment (e.g. "x{def_adjustment:.3f}")
rather than whole sentences, so this is robust to whatever indentation
surrounds it in your file.

Usage:
    python3 patch_protected_text_logger.py
Run from the same folder as app.py.
"""

import pathlib

APP_PATH = pathlib.Path("app.py")

# (search fragment, replacement fragment, expected occurrence count)
REPLACEMENTS = [
    (
        "x{def_adjustment:.3f}",
        "\U0001F6E1\uFE0F Opponent Defense Layer Applied \u2014 x{def_adjustment:.3f}",
        1,
    ),
    (
        "x{adjustment:.3f}",
        "\U0001F691 Opponent Missing Players Layer Applied \u2014 x{adjustment:.3f}",
        1,
    ),
    (
        "x{manual_value:.3f}",
        "\U0001F9E9 Scheme Layer Applied \u2014 x{manual_value:.3f}",
        3,
    ),
    (
        "x{real_adjustment:.3f}",
        "\U0001F9E9 Scheme Layer Applied \u2014 x{real_adjustment:.3f}",
        1,
    ),
]

ALREADY_PATCHED_MARKER = "Opponent Defense Layer Applied"


def main():
    if not APP_PATH.exists():
        print("ERROR: app.py not found in current directory.")
        return

    text = APP_PATH.read_text()

    if ALREADY_PATCHED_MARKER in text:
        print("Already patched -- 'Opponent Defense Layer Applied' found. No changes made.")
        return

    print("Occurrence check before patching:")
    all_ok = True
    for old, _new, expected in REPLACEMENTS:
        actual = text.count(old)
        status = "OK" if actual == expected else "MISMATCH"
        if actual != expected:
            all_ok = False
        print(f"  {old!r}: found {actual}, expected {expected}  [{status}]")

    if not all_ok:
        print()
        print("Stopping -- occurrence counts don't match what was expected from the")
        print("code we reviewed together. Nothing was changed. Paste this output back")
        print("so we can figure out what's different before patching.")
        return

    for old, new, _expected in REPLACEMENTS:
        text = text.replace(old, new)

    APP_PATH.write_text(text)
    print()
    print("Patched successfully. All target formulas now show a descriptive label")
    print("in front of the exact number, e.g.:")
    print("  -> \U0001F6E1\uFE0F Opponent Defense Layer Applied \u2014 x0.984")
    print("Restart Streamlit to see it.")


if __name__ == "__main__":
    main()
