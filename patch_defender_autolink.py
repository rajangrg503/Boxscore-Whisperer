"""
When a "Primary defender assigned" is selected, this patch also folds
them into the "Also check history vs. specific opposing player(s)"
comparison -- so you get both the real matchup-camera data (existing
behavior) AND the full head-to-head game log + hit-rate tables,
without typing the same name into two separate fields.

The visible multiselect widget itself is untouched (it'll still show
empty if you didn't type anything into it) -- only the underlying
calculation and rendering loops are pointed at a merged list instead
of the raw widget value.

Usage:
    python3 patch_defender_autolink.py
Run from the same folder as app.py.
"""

import pathlib

APP_PATH = pathlib.Path("app.py")

MARKER = "effective_key_players_input"

ANCHOR = """    # Resolve every selected key player to an ID up front.
    key_player_ids = {}"""

INSERTED = """    # Fold the primary defender into the "vs specific player(s)"
    # comparison too, so picking one field doesn't leave the other's
    # real-game head-to-head data and hit-rate tables empty -- no need
    # to type the same name twice. The visible multiselect widget above
    # is untouched; this only affects what gets calculated and shown.
    effective_key_players_input = list(key_players_input)
    if defender_input and defender_input not in effective_key_players_input:
        effective_key_players_input.append(defender_input)

    # Resolve every selected key player to an ID up front.
    key_player_ids = {}"""

OLD_FOR_LOOP = "for name in key_players_input:"
NEW_FOR_LOOP = "for name in effective_key_players_input:"

OLD_IF = "    if key_players_input:"
NEW_IF = "    if effective_key_players_input:"


def main():
    if not APP_PATH.exists():
        print("ERROR: app.py not found in current directory.")
        return

    text = APP_PATH.read_text()

    if MARKER in text:
        print("Already patched -- 'effective_key_players_input' found. No changes made.")
        return

    anchor_count = text.count(ANCHOR)
    for_count = text.count(OLD_FOR_LOOP)
    if_count = text.count(OLD_IF)

    print(f"Anchor found: {anchor_count} occurrence(s) (expected 1)")
    print(f"'for name in key_players_input:' found: {for_count} occurrence(s) (expected 2)")
    print(f"'if key_players_input:' found: {if_count} occurrence(s) (expected 1)")

    if anchor_count != 1 or for_count != 2 or if_count != 1:
        print()
        print("Stopping -- counts don't match. Nothing was changed.")
        print("Paste this output back so we can figure out what's different.")
        return

    text = text.replace(ANCHOR, INSERTED, 1)
    text = text.replace(OLD_FOR_LOOP, NEW_FOR_LOOP)  # replaces both occurrences
    text = text.replace(OLD_IF, NEW_IF, 1)

    APP_PATH.write_text(text)
    print()
    print("Patched successfully. Selecting a primary defender now also")
    print("populates the head-to-head comparison and hit-rate tables for")
    print("that player automatically -- no need to add them twice.")
    print("Restart Streamlit to see it.")


if __name__ == "__main__":
    main()
