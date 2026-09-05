"""Local-to-cloud data cache -- moved from app.py.

nba_api works fine when this app runs locally, but is blocked by the
NBA's unofficial stats site when running on Streamlit Community
Cloud's shared IP range (a known, confirmed limitation). Rather than
the app simply breaking on the cloud, every real nba_api call routes
through cached_or_live(): it tries the live call first (works locally,
and would work on any host nba_api isn't blocking), and falls back to
a cached local copy of the same data if the live call fails.

WORKFLOW: run refresh_all.py locally (which validates each endpoint
via watchdog/ before refreshing it) to populate/update data_cache/*.json
with fresh data, then commit and push that folder to GitHub. The
deployed cloud app reads whatever is in data_cache/ at deploy time --
it never needs to write there itself, since Streamlit Cloud's
filesystem doesn't persist writes between sessions anyway.

Note: cached_or_live() depends on Streamlit's session_state to track
whether a live call has already failed this session (see its
docstring) -- that's a deliberate, unchanged carryover from app.py,
not new coupling introduced by this move.
"""

import datetime
import json
import os

import pandas as pd
import streamlit as st

# CACHE_DIR must resolve to <repo_root>/data_cache regardless of which
# file defines it -- this file lives at <repo_root>/engine/cache.py, so
# going up two directories from here (not one) reaches the repo root.
CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data_cache"
)


def _cache_key_to_path(key):
    safe_key = "".join(c if (c.isalnum() or c in "_-") else "_" for c in key)
    return os.path.join(CACHE_DIR, f"{safe_key}.json")


def _save_df_cache(key, df):
    """Best-effort local cache write -- safe to fail silently (e.g. on
    a read-only filesystem). Caching is a local-machine workflow; the
    deployed cloud app only ever reads these files."""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        payload = {
            "cached_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "data": df.to_dict(orient="records"),
        }
        with open(_cache_key_to_path(key), "w") as f:
            json.dump(payload, f)
    except Exception:
        pass


def _load_df_cache(key):
    """Returns (dataframe, cached_at_string) if a cache file exists for
    this key, else (None, None)."""
    path = _cache_key_to_path(key)
    if not os.path.exists(path):
        return None, None
    try:
        with open(path) as f:
            payload = json.load(f)
        return pd.DataFrame(payload["data"]), payload.get("cached_at")
    except Exception:
        return None, None


def cached_or_live(key, fetch_fn):
    """Try a live nba_api call first; fall back to a cached local copy
    if the live call raises (e.g. nba_api blocked on this host).
    Returns (dataframe, source_label) where source_label is "live" or
    "cached (<timestamp>)", so callers can show which one was actually
    used. Re-raises the live error only if no cached copy exists
    either -- at that point there's genuinely nothing to show.

    Once a live call has failed once in this session, subsequent calls
    skip straight to a cached copy (when one exists) instead of
    re-attempting and re-waiting-out a live call already known to be
    unreachable this session (e.g. on Streamlit Cloud)."""
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
        raise live_error
