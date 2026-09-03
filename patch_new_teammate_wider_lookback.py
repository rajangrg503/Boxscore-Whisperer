"""
Widens the "New teammate arriving" feature's season lookback from just
(current, previous) to the full 4-season HEAD_TO_HEAD_SEASONS window
already used elsewhere in the app.

WHY THIS MATTERS: the original fallback only checked last season before
giving up. That misses real, meaningful pairings where a player was
traded away before last season even started (e.g. Mark Williams playing
alongside LaMelo Ball in Charlotte through 2024-25, then getting traded
to Phoenix in June 2025) -- the two-season check would wrongly report
"no shared history" even though real games exist, just a bit further
back.

Run this once, from the same folder as app.py. Requires
patch_new_teammate_per_stat.py to already be applied.

Usage:
    python3 patch_new_teammate_wider_lookback.py
"""

from pathlib import Path

TARGET = Path("app.py")

OLD = '''        new_teammate_adj_by_stat, new_teammate_note = get_new_teammate_impact_adjustment(
            player_id, new_teammate_input, CURRENT_SEASON
        )
        if new_teammate_input and "No shared game history" in new_teammate_note:
            # Current season likely too thin (early in the year) -- try
            # last season before concluding there's genuinely no shared
            # history between these two players.
            prev_by_stat, prev_note = get_new_teammate_impact_adjustment(
                player_id, new_teammate_input, PREVIOUS_SEASON
            )
            if "No shared game history" not in prev_note:
                new_teammate_adj_by_stat, new_teammate_note = prev_by_stat, prev_note'''

NEW = '''        if new_teammate_input:
            # Check the full multi-season window (same one used for
            # opponent head-to-head elsewhere) rather than stopping
            # after just one fallback season -- a real pairing can sit
            # further back if one of the two players has since been
            # traded away.
            new_teammate_adj_by_stat, new_teammate_note = None, None
            for _try_season in HEAD_TO_HEAD_SEASONS:
                new_teammate_adj_by_stat, new_teammate_note = get_new_teammate_impact_adjustment(
                    player_id, new_teammate_input, _try_season
                )
                if "No shared game history" not in new_teammate_note:
                    break
        else:
            new_teammate_adj_by_stat, new_teammate_note = get_new_teammate_impact_adjustment(
                player_id, new_teammate_input, CURRENT_SEASON
            )'''


def main():
    text = TARGET.read_text()

    if "for _try_season in HEAD_TO_HEAD_SEASONS:" in text:
        print("Already patched -- no changes needed.")
        return

    if OLD not in text:
        print("Could not find the expected call site in app.py.")
        print("Make sure patch_new_teammate_per_stat.py was run first.")
        print("No changes made.")
        return

    TARGET.write_text(text.replace(OLD, NEW))
    print("Patched app.py successfully.")
    print("The new-teammate adjustment now checks the full 4-season window")
    print("(current, previous, and two seasons back) before concluding there's")
    print("genuinely no shared history between two players.")


if __name__ == "__main__":
    main()
