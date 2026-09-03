#!/usr/bin/env python3
"""
patch_session_live_skip.py

Speed fix for Streamlit Cloud: right now, every live nba_api call
independently attempts a live fetch, waits out a 5-10s timeout, THEN
falls back to cache. On Cloud, live calls are guaranteed to fail every
time -- so a single "Predict a full matchup" run (which calls
get_season_baseline -> fetch_combined_game_log for ~15 players per
team x 2 teams = ~30 players, each with up to 2 season attempts x 2
season_types) can end up paying that same timeout dozens of times in a
row for no benefit.

Fix: a st.session_state flag, _live_nba_api_blocked. The first time a
live call fails in a session, this gets set. Every subsequent call
checks the flag first and skips straight to the cached copy (if one
exists) instead of re-attempting a live call already known to be dead
this session. If no cached copy exists for a given key, it still
attempts live anyway (nothing to lose).

This is per-session (st.session_state), not global -- a fresh browser
session/tab starts with the flag unset and will re-verify once, which
is correct: it costs one real timeout per new user session, not per
prediction.

Patches two places:
  1. cached_or_live() -- the general-purpose fetch/cache wrapper used
     by most live nba_api calls in this file.
  2. fetch_combined_game_log() -- has its own inline fetch/cache logic
     (doesn't route through cached_or_live()), so needs the same flag
     check added separately.

Idempotent: checks for the patch marker first; if already applied, skips.
Verifies exact-text occurrence count before touching the file.
"""

import sys

TARGET_FILE = "app.py"
MARKER = "# patch_session_live_skip"

OLD_CACHED_OR_LIVE = '''def cached_or_live(key, fetch_fn):
    """Try a live nba_api call first; fall back to a cached local copy
    if the live call raises (e.g. nba_api blocked on this host).
    Returns (dataframe, source_label) where source_label is "live" or
    "cached (<timestamp>)", so callers can show which one was actually
    used. Re-raises the live error only if no cached copy exists
    either -- at that point there's genuinely nothing to show."""
    try:
        df = fetch_fn()
        _save_df_cache(key, df)
        return df, "live"
    except Exception as live_error:
        cached_df, cached_at = _load_df_cache(key)
        if cached_df is not None:
            label = f"cached copy from {cached_at}" if cached_at else "cached copy"
            return cached_df, label
        raise live_error'''

NEW_CACHED_OR_LIVE = '''def cached_or_live(key, fetch_fn):
    """Try a live nba_api call first; fall back to a cached local copy
    if the live call raises (e.g. nba_api blocked on this host).
    Returns (dataframe, source_label) where source_label is "live" or
    "cached (<timestamp>)", so callers can show which one was actually
    used. Re-raises the live error only if no cached copy exists
    either -- at that point there's genuinely nothing to show.

    Once a live call has failed once in this session, subsequent calls
    skip straight to a cached copy (when one exists) instead of
    re-attempting and re-waiting-out a live call already known to be
    unreachable this session (e.g. on Streamlit Cloud)."""  # patch_session_live_skip
    if st.session_state.get("_live_nba_api_blocked"):
        cached_df, cached_at = _load_df_cache(key)
        if cached_df is not None:
            label = f"cached copy from {cached_at}" if cached_at else "cached copy"
            return cached_df, label
    try:
        df = fetch_fn()
        _save_df_cache(key, df)
        return df, "live"
    except Exception as live_error:
        st.session_state["_live_nba_api_blocked"] = True
        cached_df, cached_at = _load_df_cache(key)
        if cached_df is not None:
            label = f"cached copy from {cached_at}" if cached_at else "cached copy"
            return cached_df, label
        raise live_error'''

OLD_FETCH_GAMELOG = '''def fetch_combined_game_log(player_id, season):
    """Fetch a player's game log for a season, blending regular season
    and playoff games into one combined dataset. Playoffs come from a
    separate season_type query and simply get concatenated on -- a
    missing playoff log is normal (most players didn't make the
    playoffs that year) and isn't treated as an error.

    A failure on the Regular Season call specifically is a different
    story -- every rostered player has some current/recent regular
    season log, so that failing means the live API call itself broke
    (e.g. nba_api blocked on this host). In that case this falls back
    to a cached local copy via cached_or_live() instead of silently
    returning an empty, columnless DataFrame that breaks every
    downstream stat lookup with a confusing KeyError."""
    frames = []
    regular_season_error = None
    for season_type in ["Regular Season", "Playoffs"]:
        try:
            log = playergamelog.PlayerGameLog(
                player_id=player_id, season=season, season_type_all_star=season_type,
                timeout=5,
            )
            df = log.get_data_frames()[0]
            if not df.empty:
                frames.append(df)
        except Exception as e:
            if season_type == "Regular Season":
                regular_season_error = e
            continue

    cache_key = f"gamelog_{player_id}_{season}"

    if regular_season_error is not None and not frames:
        cached_df, _cached_at = _load_df_cache(cache_key)
        if cached_df is not None:
            return cached_df
        raise ConnectionError(
            f"Live NBA data fetch failed for player {player_id}, season {season}, "
            f"and no cached copy exists yet."
        ) from regular_season_error

    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not combined.empty:
        _save_df_cache(cache_key, combined)
    return combined'''

NEW_FETCH_GAMELOG = '''def fetch_combined_game_log(player_id, season):
    """Fetch a player's game log for a season, blending regular season
    and playoff games into one combined dataset. Playoffs come from a
    separate season_type query and simply get concatenated on -- a
    missing playoff log is normal (most players didn't make the
    playoffs that year) and isn't treated as an error.

    A failure on the Regular Season call specifically is a different
    story -- every rostered player has some current/recent regular
    season log, so that failing means the live API call itself broke
    (e.g. nba_api blocked on this host). In that case this falls back
    to a cached local copy via cached_or_live() instead of silently
    returning an empty, columnless DataFrame that breaks every
    downstream stat lookup with a confusing KeyError.

    Once a live call has failed once this session, subsequent calls
    skip the live attempt entirely and go straight to a cached copy if
    one exists -- see cached_or_live()'s docstring for why."""  # patch_session_live_skip
    cache_key = f"gamelog_{player_id}_{season}"

    if st.session_state.get("_live_nba_api_blocked"):
        cached_df, _cached_at = _load_df_cache(cache_key)
        if cached_df is not None:
            return cached_df
        # no cached copy for this specific key -- fall through and try
        # live anyway, nothing to lose.

    frames = []
    regular_season_error = None
    for season_type in ["Regular Season", "Playoffs"]:
        try:
            log = playergamelog.PlayerGameLog(
                player_id=player_id, season=season, season_type_all_star=season_type,
                timeout=5,
            )
            df = log.get_data_frames()[0]
            if not df.empty:
                frames.append(df)
        except Exception as e:
            if season_type == "Regular Season":
                regular_season_error = e
            continue

    if regular_season_error is not None and not frames:
        st.session_state["_live_nba_api_blocked"] = True
        cached_df, _cached_at = _load_df_cache(cache_key)
        if cached_df is not None:
            return cached_df
        raise ConnectionError(
            f"Live NBA data fetch failed for player {player_id}, season {season}, "
            f"and no cached copy exists yet."
        ) from regular_season_error

    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not combined.empty:
        _save_df_cache(cache_key, combined)
    return combined'''


def main():
    with open(TARGET_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    if MARKER in content:
        print("Already patched (marker found) -- skipping, no changes made.")
        return

    c1 = content.count(OLD_CACHED_OR_LIVE)
    c2 = content.count(OLD_FETCH_GAMELOG)
    print(f"cached_or_live found: {c1} (expected: 1)")
    print(f"fetch_combined_game_log found: {c2} (expected: 1)")

    if c1 != 1 or c2 != 1:
        print("ABORTING -- occurrence count mismatch. No changes made.")
        print("Re-confirm exact live text via sed before retrying.")
        sys.exit(1)

    content = content.replace(OLD_CACHED_OR_LIVE, NEW_CACHED_OR_LIVE)
    content = content.replace(OLD_FETCH_GAMELOG, NEW_FETCH_GAMELOG)

    with open(TARGET_FILE, "w", encoding="utf-8") as f:
        f.write(content)

    print("Patched: cached_or_live() and fetch_combined_game_log() now skip")
    print("re-attempting live nba_api calls once one has failed this session.")
    print("Restart Streamlit and test both tabs. Locally this changes nothing")
    print("(your live calls succeed, so the flag never gets set). On Cloud,")
    print("the FIRST prediction in a session still pays one real timeout,")
    print("but every subsequent one in that session should be fast.")


if __name__ == "__main__":
    main()
