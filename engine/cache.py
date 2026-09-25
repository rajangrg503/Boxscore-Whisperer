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
import time

import pandas as pd
import streamlit as st

from engine import cache_archive


# How long a failed live call suppresses further live attempts. Long
# enough that one dead endpoint cannot stall a page thirty times over,
# short enough that a transient blip does not cost the reader the rest
# of their session. Nothing measured this; it is a comfort number, and
# the only thing it trades is how quickly the app notices nba.com came
# back.
LIVE_RETRY_COOLDOWN = 60.0


class LiveSourceUnavailable(RuntimeError):
    """Raised instead of waiting out a timeout we already know will
    time out. Callers that already handle a failed fetch need no
    change: this is that failure, delivered sooner."""

# <repo_root>/data_cache -- this file lives at <repo_root>/engine/cache.py,
# so going up two directories from here (not one) reaches the repo root.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE_PATH = os.path.join(REPO_ROOT, cache_archive.ARCHIVE_NAME)

# CACHE_DIR stays exactly what it was: the folder writes go to, the
# folder the refresh scripts and the watchdog work in, the folder the
# tests point somewhere else. BW_CACHE_DIR moves it, for a run against
# a cache kept elsewhere.
DEFAULT_CACHE_DIR = os.path.join(REPO_ROOT, cache_archive.CACHE_DIR_NAME)
CACHE_DIR = os.environ.get("BW_CACHE_DIR", "").strip() or DEFAULT_CACHE_DIR

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

    The archive answers ONLY for the default cache directory. Anyone who
    has moved CACHE_DIR has said which cache they want, and quietly
    serving a different one from the archive would be worse than a miss:
    a test that points CACHE_DIR at an empty directory to prove a
    missing file raises would instead be handed the real data and pass
    for the wrong reason, and a backtest isolated the same way would
    silently read outside its own fixture.
    """
    path = _cache_key_to_path(key)
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None
    if ARCHIVE is not None and CACHE_DIR == DEFAULT_CACHE_DIR:
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

    THE BREAKER, AND WHY IT USED TO SAVE TIME ONLY WHERE THERE WAS NONE
    TO SAVE. When a live call fails, the breaker trips and later calls
    take a cached copy instead of waiting out an endpoint already known
    to be unreachable. But the old version fell through to a fresh live
    attempt whenever no cached copy existed -- which is exactly the case
    where the wait is unaffordable.

    get_player_team_and_number() walks up to thirty rosters. With
    stats.nba.com timing out and a roster missing from the cache, that
    was thirty more attempts at 5s then 10s each, one prediction
    stalling for over half a minute with nothing on screen. Observed on
    25 Sep: twenty-five consecutive [_load_roster_df] timeouts for a
    single projection, long after the breaker had tripped. The breaker
    was saving time only where a cached copy made the call cheap
    anyway.

    So a tripped breaker now refuses immediately when there is nothing
    cached. The caller sees the same exception it would have seen after
    fifteen seconds of waiting, just sooner, and the fifteen seconds
    buys nothing: the endpoint is down, and it was not going to hand us
    a roster.

    IT HEALS. The trip lasts LIVE_RETRY_COOLDOWN seconds rather than the
    session, because a permanent block turns one transient blip into a
    session that can never fetch anything it does not already have.
    After the cooldown a single call is allowed through to probe, and
    either clears the breaker or re-trips it.
    """
    blocked_until = st.session_state.get("_live_nba_api_blocked_until")
    if blocked_until is None and st.session_state.get("_live_nba_api_blocked"):
        # engine/game_log.py and engine/tracker.py trip the same flag
        # without a cooldown of their own. Adopt it the first time we
        # see it rather than keeping a second opinion about whether the
        # endpoint is up.
        blocked_until = time.monotonic() + LIVE_RETRY_COOLDOWN
        st.session_state["_live_nba_api_blocked_until"] = blocked_until
    still_blocked = blocked_until is not None and time.monotonic() < blocked_until

    if still_blocked:
        cached_df, cached_at = _load_df_cache(key)
        if cached_df is not None:
            label = f"cached copy from {cached_at}" if cached_at else "cached copy"
            return cached_df, label
        # Nothing cached and the endpoint is known-down. Waiting out
        # another timeout cannot produce what isn't there.
        raise LiveSourceUnavailable(
            f"{key}: live source unavailable and no cached copy "
            f"(retrying in {blocked_until - time.monotonic():.0f}s)"
        )

    try:
        df = fetch_fn()
        _save_df_cache(key, df)
        # A success after a trip means the blip is over.
        st.session_state["_live_nba_api_blocked_until"] = None
        st.session_state["_live_nba_api_blocked"] = False
        return df, "live"
    except Exception as live_error:
        st.session_state["_live_nba_api_blocked_until"] = (
            time.monotonic() + LIVE_RETRY_COOLDOWN
        )
        st.session_state["_live_nba_api_blocked"] = True  # kept: read elsewhere
        cached_df, cached_at = _load_df_cache(key)
        if cached_df is not None:
            label = f"cached copy from {cached_at}" if cached_at else "cached copy"
            return cached_df, label
        raise live_error
