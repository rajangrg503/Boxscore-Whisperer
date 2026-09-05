"""
patch_teammate_availability_caching.py

Fixes get_teammate_availability_adjustment() in app.py so it actually
reads from the cache on Streamlit Cloud, instead of always
short-circuiting -- same bug pattern as get_opponent_missing_adjustment()
that was fixed earlier tonight.

BUG BEING FIXED: the function checks
    if st.session_state.get("_live_nba_api_blocked"):
        return neutral, (f"Live NBA data already confirmed unreachable...")
BEFORE the per-game boxscore loop even starts. This means it ALWAYS
bails out immediately once blocked, and never gets a chance to check
the cache -- even after pre-caching every boxscore, this function would
still never use it.

THE FIX:
  1. Remove that early short-circuit entirely.
  2. Route the per-game BoxScoreTraditionalV2 call through
     cached_or_live(), keyed by game_id ALONE (not per-player) --
     a boxscore is shared data, every player in that game references
     the same one, and it never changes once the game is final:
         f"boxscore_{game_id}"
cached_or_live() already checks _live_nba_api_blocked internally and
falls back to cache first when blocked, so this function doesn't need
to duplicate that logic -- it just needs to actually call
cached_or_live() instead of a raw try/except around a live-only call.

Run this from the repo root:
    python3 patch_teammate_availability_caching.py
Then verify with:
    python3 -c "import ast; ast.parse(open('app.py').read())"
Then diff/review before committing:
    git diff app.py
"""

from pathlib import Path

APP_PATH = Path("app.py")

OLD_BLOCKED_GUARD = '''    if st.session_state.get("_live_nba_api_blocked"):
        return neutral, (f"Live NBA data already confirmed unreachable this session -- "
                          f"skipping per-game teammate-availability scan."), 0

    matching_games = []'''

NEW_BLOCKED_GUARD = '''    matching_games = []'''

OLD_BOX_FETCH = '''        try:
            box = boxscoretraditionalv2.BoxScoreTraditionalV2(game_id=game_id, timeout=5)
            box_df = box.get_data_frames()[0]
            players_in_game = set(box_df["PLAYER_NAME"])
        except Exception:
            consecutive_failures += 1
            continue'''

NEW_BOX_FETCH = '''        try:
            def _fetch_box():
                box = boxscoretraditionalv2.BoxScoreTraditionalV2(game_id=game_id, timeout=5)
                return box.get_data_frames()[0]

            box_df, _source = cached_or_live(f"boxscore_{game_id}", _fetch_box)
            players_in_game = set(box_df["PLAYER_NAME"])
        except Exception:
            consecutive_failures += 1
            continue'''


def apply_patch():
    text = APP_PATH.read_text()

    for label, old, new in [
        ("teammate-availability blocked guard", OLD_BLOCKED_GUARD, NEW_BLOCKED_GUARD),
        ("BoxScoreTraditionalV2 fetch", OLD_BOX_FETCH, NEW_BOX_FETCH),
    ]:
        count = text.count(old)
        if count == 0:
            print(f"  SKIP: '{label}' -- exact text not found (already patched, or app.py has changed). "
                  f"No changes made for this section.")
            continue
        if count > 1:
            print(f"  ABORT: '{label}' matched {count} times -- expected exactly 1. "
                  f"Not applying this section to avoid an unintended multi-site edit.")
            continue
        text = text.replace(old, new)
        print(f"  OK: patched '{label}'")

    APP_PATH.write_text(text)
    print("\nDone. Now run:")
    print("  python3 -c \"import ast; ast.parse(open('app.py').read())\"")
    print("  git diff app.py")


if __name__ == "__main__":
    apply_patch()
