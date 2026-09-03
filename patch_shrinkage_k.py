"""
Lowers blend_baseline_stats()'s default shrinkage_k from 8 to 4.

At k=8, a 5-game head-to-head sample got 38% weight (season dominated
at 62%). At k=4, that same 5-game sample gets 56% weight -- head-to-head
data meaningfully leads once you have it, while a single game (n=1)
still only gets 20% weight, so the safeguard against wild small-sample
swings is still intact, just less conservative.

This is a one-line default-parameter change -- every call site that
doesn't explicitly pass shrinkage_k will pick up the new default
automatically.

Usage:
    python3 patch_shrinkage_k.py
Run from the same folder as app.py.
"""

import pathlib

APP_PATH = pathlib.Path("app.py")

OLD = "def blend_baseline_stats(season_stats, shrinkage_k=8, team_h2h=None, team_h2h_n=0,"
NEW = "def blend_baseline_stats(season_stats, shrinkage_k=4, team_h2h=None, team_h2h_n=0,"


def main():
    if not APP_PATH.exists():
        print("ERROR: app.py not found in current directory.")
        return

    text = APP_PATH.read_text()

    if NEW in text:
        print("Already patched -- shrinkage_k=4 found. No changes made.")
        return

    count = text.count(OLD)
    if count != 1:
        print(f"ERROR: expected exactly 1 occurrence of the function signature, found {count}.")
        print("Paste: grep -n 'def blend_baseline_stats' app.py")
        return

    text = text.replace(OLD, NEW, 1)
    APP_PATH.write_text(text)
    print("Patched. shrinkage_k default is now 4 (was 8).")
    print("Restart Streamlit to see it take effect.")


if __name__ == "__main__":
    main()
