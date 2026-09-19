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
with fresh data, then run tools/pack_cache.py and commit the resulting
data_cache.zip. tools/scheduled_refresh.sh does all of that every
morning. The deployed cloud app reads whatever is in that archive at
deploy time -- it never needs to write there itself, since Streamlit
Cloud's filesystem doesn't persist writes between sessions anyway.

The folder itself is NOT tracked by git any more: 148,684 loose files
made Streamlit Cloud's checkout unreliable, twice deploying a new
app.py beside a stale engine/. engine/cache_archive.py has the
measurements and the reasoning; _resolve_cache_dir() below is the only
place that cares which of the two forms is present.

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

from engine import cache_archive

# <repo_root>/data_cache -- this file lives at <repo_root>/engine/cache.py,
# so going up two directories from here (not one) reaches the repo root.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE_PATH = os.path.join(REPO_ROOT, cache_archive.ARCHIVE_NAME)

# CACHE_DIR stays exactly what it was: the folder writes go to, the
# folder the refresh scripts and the watchdog work in, the folder the
# tests point somewhere else. BW_CACHE_DIR moves it, for a run against
# a cache kept elsewhere.
CACHE_DIR = os.environ.get("BW_CACHE_DIR", "").strip() or os.path.join(
    REPO_ROOT, cache_archive.CACHE_DIR_NAME
)

# The archive is consulted only when that folder isn't there, which in
# practice means only on the deployed app: a development machine has
# the loose files, and reading a build artifact that may be minutes
# behind the folder it was built from -- while the refresh job is
# writing that folder -- is exactly the quiet wrongness this change is
# about. Opening it costs one pass over the zip directory (0.6s here);
# nothing is unpacked and nothing is written.
ARCHIVE = (
    None if cache_archive.has_loose_cache(CACHE_DIR)
    else cache_archive.open_reader(ARCHIVE_PATH)
)


def _cache_key_to_path(key):
    safe_key = "".join(c if (c.isalnum() or c in "_-") else "_" for c in key)
    return os.path.join(CACHE_DIR, f"{safe_key}.json")


def _cache_key_to_name(key):
    safe_key = "".join(c if (c.isalnum() or c in "_-") else "_" for c in key)
    return f"{safe_key}.json"


def read_payload(key):
    """The raw {"cached_at", "data"} payload for a key, or None.

    The one place that knows the cache has two shapes. Everything above
    this -- the loaders, the freshness banner -- asks for a key and gets
    a payload, and never learns whether it came off the filesystem or
    out of the archive.
    """
    path = _cache_key_to_path(key)
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None
    if ARCHIVE is not None:
        return ARCHIVE.read_json(_cache_key_to_name(key))
    return None


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
    """Returns (dataframe, cached_at_string) if this key is cached --
    as a loose file locally, or as an entry in the archive on the
    deployed app -- else (None, None)."""
    payload = read_payload(key)
    if payload is None:
        return None, None
    try:
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
