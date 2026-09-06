"""Player game-log fetching -- moved from app.py.

Required to move engine/adjustments/teammates.py (both of its
functions call this), same reason engine/cache.py exists: importing
this from app.py into engine/ would be circular, since app.py imports
FROM engine/. The other 7 call sites of this function that stay in
app.py for now are unaffected -- they'll resolve it via the import
this move leaves behind, exactly as before.
"""

import streamlit as st
import pandas as pd
from nba_api.stats.endpoints import playergamelog

from engine.cache import _load_df_cache, _save_df_cache


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_combined_game_log(player_id, season):
    """Fetch a player's game log for a season, blending regular season
    and playoff games into one combined dataset. Playoffs come from a
    separate season_type query and simply get concatenated on -- a
    missing playoff log is normal (most players didn't make the
    playoffs that year) and isn't treated as an error.

    A failure on the Regular Season call specifically is a different
    story -- every rostered player has some current/recent regular
    season log, so that failing means the live API call itself broke
    (e.g. nba_api blocked on this host). In that case this falls back
    to a cached local copy via _load_df_cache() instead of silently
    returning an empty, columnless DataFrame that breaks every
    downstream stat lookup with a confusing KeyError.

    Once a live call has failed once this session, subsequent calls
    skip the live attempt entirely and go straight to a cached copy if
    one exists."""
    cache_key = f"gamelog_{player_id}_{season}"

    if st.session_state.get("_live_nba_api_blocked"):
        cached_df, _cached_at = _load_df_cache(cache_key)
        if cached_df is not None:
            return cached_df
        raise ConnectionError("Live NBA data fetch skipped -- already confirmed unreachable this session.")

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
    return combined
