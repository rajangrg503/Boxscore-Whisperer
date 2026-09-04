"""
patch_opponent_missing_caching.py

Fixes get_opponent_missing_adjustment() in app.py so it actually reads
from the cache on Streamlit Cloud, instead of always short-circuiting.

BUG BEING FIXED: the function currently checks
    if st.session_state.get("_live_nba_api_blocked"):
        return 1.0, ("Live NBA data already confirmed unreachable...")
at the very top, before ever attempting PlayerEstimatedMetrics or
PlayerCareerStats. This means it ALWAYS bails out immediately once
blocked, and never gets a chance to check the cache -- even after
pre-caching this data, the function would still never use it.

THE FIX:
  1. Remove that top-level short-circuit entirely.
  2. Route the PlayerEstimatedMetrics call through cached_or_live(),
     keyed by season: f"player_estimated_metrics_{try_season}"
  3. Route the PlayerCareerStats call through cached_or_live(),
     keyed by player: f"career_stats_{pid}"
cached_or_live() already checks _live_nba_api_blocked internally and
falls back to cache first when blocked -- so this function doesn't need
to duplicate that logic, it just needs to actually call cached_or_live()
instead of raw try/except around live-only calls.

Run this from the repo root:
    python3 patch_opponent_missing_caching.py
Then verify with:
    python3 -c "import ast; ast.parse(open('app.py').read())"
Then diff/review before committing:
    git diff app.py
"""

import re
from pathlib import Path

APP_PATH = Path("app.py")

OLD_TOP_GUARD = '''    if not missing_opponents:
        return 1.0, "No missing opponent players specified -- no adjustment."
    if st.session_state.get("_live_nba_api_blocked"):
        return 1.0, ("Live NBA data already confirmed unreachable this session -- "
                      "skipping opponent-missing-player adjustment.")
'''

NEW_TOP_GUARD = '''    if not missing_opponents:
        return 1.0, "No missing opponent players specified -- no adjustment."
'''

OLD_METRICS_FETCH = '''    net_rating_by_id = {}
    metrics_season_used = None
    for try_season in [season, PREVIOUS_SEASON]:
        try:
            metrics = playerestimatedmetrics.PlayerEstimatedMetrics(
                season=try_season, timeout=10
            )
            metrics_df = metrics.get_data_frames()[0]
        except Exception:
            continue
        if not metrics_df.empty:
            net_rating_by_id = dict(zip(metrics_df["PLAYER_ID"], metrics_df["E_NET_RATING"]))
            metrics_season_used = try_season
            break
'''

NEW_METRICS_FETCH = '''    net_rating_by_id = {}
    metrics_season_used = None
    for try_season in [season, PREVIOUS_SEASON]:
        def _fetch_metrics():
            metrics = playerestimatedmetrics.PlayerEstimatedMetrics(
                season=try_season, timeout=10
            )
            return metrics.get_data_frames()[0]

        try:
            metrics_df, _source = cached_or_live(
                f"player_estimated_metrics_{try_season}", _fetch_metrics
            )
        except Exception:
            continue
        if not metrics_df.empty:
            net_rating_by_id = dict(zip(metrics_df["PLAYER_ID"], metrics_df["E_NET_RATING"]))
            metrics_season_used = try_season
            break
'''

OLD_CAREER_FETCH = '''            pid = match[0]["id"]
            career = playercareerstats.PlayerCareerStats(player_id=pid, timeout=5)
            df = career.get_data_frames()[0]
'''

NEW_CAREER_FETCH = '''            pid = match[0]["id"]

            def _fetch_career():
                career = playercareerstats.PlayerCareerStats(player_id=pid, timeout=5)
                return career.get_data_frames()[0]

            df, _source = cached_or_live(f"career_stats_{pid}", _fetch_career)
'''


def apply_patch():
    text = APP_PATH.read_text()

    for label, old, new in [
        ("top-level blocked guard", OLD_TOP_GUARD, NEW_TOP_GUARD),
        ("PlayerEstimatedMetrics fetch", OLD_METRICS_FETCH, NEW_METRICS_FETCH),
        ("PlayerCareerStats fetch", OLD_CAREER_FETCH, NEW_CAREER_FETCH),
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
