"""
Fixes get_new_teammate_impact_adjustment() to check PREVIOUS_SEASON as a
fallback when CURRENT_SEASON doesn't have enough games logged yet --
same pattern get_season_baseline() already uses. Without this, early
in a new season the function wrongly reports "no shared history found"
for real teammates just because the current season's game log is too
thin, even though last season's shared games would show real signal.

Run this once, from the same folder as app.py. Requires
patch_new_teammate_feature.py to already be applied.

Usage:
    python3 patch_new_teammate_season_fallback.py
"""

from pathlib import Path

TARGET = Path("app.py")

OLD = '''        new_teammate_adj, new_teammate_note = get_new_teammate_impact_adjustment(
            player_id, new_teammate_input, CURRENT_SEASON
        )'''

NEW = '''        new_teammate_adj, new_teammate_note = get_new_teammate_impact_adjustment(
            player_id, new_teammate_input, CURRENT_SEASON
        )
        if new_teammate_input and new_teammate_adj == 1.0 and "No shared game history" in new_teammate_note:
            # Current season likely too thin (early in the year) -- try
            # last season before concluding there's genuinely no shared
            # history between these two players.
            prev_adj, prev_note = get_new_teammate_impact_adjustment(
                player_id, new_teammate_input, PREVIOUS_SEASON
            )
            if prev_adj != 1.0 or "No shared game history" not in prev_note:
                new_teammate_adj, new_teammate_note = prev_adj, prev_note'''


def main():
    text = TARGET.read_text()

    if 'prev_adj, prev_note = get_new_teammate_impact_adjustment(' in text:
        print("Already patched -- no changes needed.")
        return

    if OLD not in text:
        print("Could not find the expected function call in app.py.")
        print("Make sure patch_new_teammate_feature.py was run first.")
        print("No changes made.")
        return

    TARGET.write_text(text.replace(OLD, NEW))
    print("Patched app.py successfully.")
    print("The new-teammate adjustment now falls back to last season if the")
    print("current season doesn't have enough shared games logged yet.")


if __name__ == "__main__":
    main()
