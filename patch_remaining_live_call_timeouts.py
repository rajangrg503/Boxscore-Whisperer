"""
Patches app.py to add 5s timeouts to the three remaining live nba_api
calls that were never protected -- same bug as the gamelog fix, just in
the "Advanced options" fields (missing teammates, missing opponent
players, primary defender) rather than the core baseline fetch.

The boxscoretraditionalv2 fix matters most: it's called ONCE PER GAME
in a loop (potentially dozens of times) to check who was on the floor,
so a single hang there can stall the whole prediction for a long time
even though each individual call is "only" unbounded, not infinite.

Run this once, from the same folder as app.py.

Usage:
    python3 patch_remaining_live_call_timeouts.py
"""

from pathlib import Path

TARGET = Path("app.py")

PATCHES = [
    (
        "boxscoretraditionalv2 (per-game loop, missing teammates)",
        '''            box = boxscoretraditionalv2.BoxScoreTraditionalV2(game_id=game_id)''',
        '''            box = boxscoretraditionalv2.BoxScoreTraditionalV2(game_id=game_id, timeout=5)''',
    ),
    (
        "playercareerstats (missing opponent players)",
        '''            career = playercareerstats.PlayerCareerStats(player_id=pid)''',
        '''            career = playercareerstats.PlayerCareerStats(player_id=pid, timeout=5)''',
    ),
    (
        "leagueseasonmatchups (primary defender)",
        '''            data = leagueseasonmatchups.LeagueSeasonMatchups(
                off_player_id_nullable=player_id,
                def_player_id_nullable=defender_id,
                season=try_season,
            )''',
        '''            data = leagueseasonmatchups.LeagueSeasonMatchups(
                off_player_id_nullable=player_id,
                def_player_id_nullable=defender_id,
                season=try_season,
                timeout=5,
            )''',
    ),
]


def main():
    text = TARGET.read_text()
    applied = []
    already_done = []
    missing = []

    for label, old, new in PATCHES:
        if new in text:
            already_done.append(label)
        elif old in text:
            text = text.replace(old, new)
            applied.append(label)
        else:
            missing.append(label)

    if applied:
        TARGET.write_text(text)

    if applied:
        print("Patched:")
        for label in applied:
            print(f"  - {label}")
    if already_done:
        print("Already patched (no change needed):")
        for label in already_done:
            print(f"  - {label}")
    if missing:
        print("Could NOT find expected text for (no change made for these):")
        for label in missing:
            print(f"  - {label}")
        print("The file may differ from what this patch expects -- ")
        print("open app.py and check these by hand.")

    if applied:
        print("\napp.py updated successfully.")


if __name__ == "__main__":
    main()
