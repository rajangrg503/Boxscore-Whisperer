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
from engine.season import CURRENT_SEASON, PREVIOUS_SEASON


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_combined_game_log(player_id, season):
    """Fetch a player's game log for a season, blending regular season
    and playoff games into one combined dataset. Playoffs come from a
    separate season_type query and simply get concatenated on -- a
    missing playoff log is normal (most players didn't make the
    playoffs that year) and isn't treated as an error.

    THE BLEND IS DELIBERATE AND MEASURED -- DO NOT "FIX" IT
    build_backtest_population.py is built from regular-season box
    scores only, so for months every number this project published was
    measured on a baseline the app does not compute. Nobody decided
    that; the two files were written apart and nothing compared them.

    It only reaches a projection in the fallback window -- below five
    current-season games, resolve_season_gamelog() hands back LAST
    season's whole log, playoffs and all -- which is opening night and
    the fortnight after it.

    playoff_blending_sweep.py measured it: 373 player-seasons that had
    a playoff run behind them, each one's first five games of the next
    season projected twice from the same log, once with the playoff
    rows and once without.

        blended / clean MAE   0.9976   95% CI [0.9914, 1.0028]
        30+ mpg players       0.9967   95% CI [0.9909, 1.0091]
        deep runs (17+ games) 1.0058   95% CI [0.9887, 1.0351]

    No effect, including in the subgroup where a shortened rotation
    should bite hardest. The only two intervals that exclude 1.0 (STL
    0.9889, BLK 0.9945) favour KEEPING the playoff games.

    The blended baseline also simply has more games behind it -- a
    median of six more -- and that is not a confound to correct for,
    it is the actual choice: use every row you have, or throw the
    playoff ones away. Measured, keeping them is free or slightly
    better. So they stay, and the seam is now a decision rather than
    an accident.

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


@st.cache_data(ttl=3600, show_spinner=False)
def resolve_season_gamelog(player_id):
    """Tries CURRENT_SEASON first; falls through to PREVIOUS_SEASON both
    when there aren't enough current-season games yet (early in a new
    season) AND when fetch_combined_game_log raises outright (e.g. a
    live nba_api failure with no cached copy for the current season
    specifically -- previous seasons are far more likely to already be
    cached, since a whole season's worth of games existed to fetch).

    Returns (df, season, source_label). Extracted from app.py's
    get_season_baseline() (moved verbatim, not reimplemented) so that
    other callers needing this exact "which single season represents
    this player right now" decision -- currently
    engine/adjustments/teammates.py's get_out_redistribution_adjustment(),
    which needs to know precisely which season's games to check the
    marked-out player's presence within -- share this one source of
    truth instead of re-deriving (and risking silently drifting from)
    the same <5-game fallback rule independently. get_season_baseline()
    itself now just calls this and reduces the df to stats; its own
    return shape and behavior are unchanged.

    Always exactly one season's games -- never a blend of two, never
    open-ended career history. That's a direct consequence of this
    being a pure extraction of logic that already worked this way."""
    try:
        df = fetch_combined_game_log(player_id, CURRENT_SEASON)
    except Exception:
        df = pd.DataFrame()

    if len(df) >= 5:
        return df, CURRENT_SEASON, f"{CURRENT_SEASON} season so far, incl. playoffs ({len(df)} games)"

    df = fetch_combined_game_log(player_id, PREVIOUS_SEASON)  # let this one raise if it fails -- nothing left to fall back to
    return df, PREVIOUS_SEASON, f"{PREVIOUS_SEASON} full season, incl. playoffs ({len(df)} games)"
