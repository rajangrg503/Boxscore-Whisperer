"""The cache has two shapes; everything above engine/cache.py has one.

Locally data_cache/ is a folder of loose JSON. On the deployed app it
is data_cache.zip and there is no folder at all. read_payload() is the
only function that knows the difference, and these tests pin the three
things that would hurt if it got them wrong:

  * a loose file still wins, so a refresh in progress is never shadowed
    by an archive built before it started
  * the archive is read when the folder isn't there, or the deployed
    app has no data
  * the staleness banner keeps working from the archive -- it is the
    one thing on the page whose whole job is to notice that the cache
    stopped moving
"""

import datetime
import json

import pandas as pd

from engine import cache as cache_module
from engine import cache_archive as ca
from engine import freshness


def payload(rows, cached_at="2026-09-19T08:30:00"):
    return json.dumps({"cached_at": cached_at, "data": rows})


def loose(tmp_path, files):
    d = tmp_path / "data_cache"
    d.mkdir(exist_ok=True)
    for name, body in files.items():
        (d / name).write_text(body)
    return str(d)


def point_at(monkeypatch, cache_dir, archive=None):
    """Stand in for a machine whose default cache dir is this one.

    DEFAULT_CACHE_DIR moves with CACHE_DIR here, because the archive
    only answers for the default directory -- see
    test_moving_the_cache_dir_switches_the_archive_off for the reason,
    and for what happens when the two differ.
    """
    monkeypatch.setattr(cache_module, "CACHE_DIR", str(cache_dir))
    monkeypatch.setattr(cache_module, "DEFAULT_CACHE_DIR", str(cache_dir))
    monkeypatch.setattr(cache_module, "ARCHIVE", archive)


# ---- which source answers -------------------------------------------------
def test_a_loose_file_is_read(tmp_path, monkeypatch):
    d = loose(tmp_path, {"gamelog_1_2025-26.json": payload([{"PTS": 30}])})
    point_at(monkeypatch, d)
    df, at = cache_module._load_df_cache("gamelog_1_2025-26")
    assert list(df["PTS"]) == [30]
    assert at == "2026-09-19T08:30:00"


def test_the_archive_answers_when_there_is_no_folder(tmp_path, monkeypatch):
    src = loose(tmp_path, {"gamelog_1_2025-26.json": payload([{"PTS": 41}])})
    archive_path = str(tmp_path / "c.zip")
    ca.pack(src, archive_path)
    point_at(monkeypatch, tmp_path / "absent", ca.open_reader(archive_path))

    df, _ = cache_module._load_df_cache("gamelog_1_2025-26")
    assert list(df["PTS"]) == [41]


def test_a_loose_file_beats_the_archive(tmp_path, monkeypatch):
    """A development machine has both, and the folder is the live one:
    the archive there is a build artifact that can be a whole refresh
    behind."""
    src = loose(tmp_path, {"gamelog_1_2025-26.json": payload([{"PTS": 1}])})
    archive_path = str(tmp_path / "c.zip")
    ca.pack(src, archive_path)
    (tmp_path / "data_cache" / "gamelog_1_2025-26.json").write_text(payload([{"PTS": 99}]))
    point_at(monkeypatch, src, ca.open_reader(archive_path))

    df, _ = cache_module._load_df_cache("gamelog_1_2025-26")
    assert list(df["PTS"]) == [99]


def test_a_key_in_neither_place_is_a_miss_not_an_error(tmp_path, monkeypatch):
    src = loose(tmp_path, {"a.json": payload([])})
    archive_path = str(tmp_path / "c.zip")
    ca.pack(src, archive_path)
    point_at(monkeypatch, tmp_path / "absent", ca.open_reader(archive_path))
    assert cache_module._load_df_cache("no_such_key") == (None, None)


def test_with_no_archive_at_all_a_miss_is_still_just_a_miss(tmp_path, monkeypatch):
    point_at(monkeypatch, tmp_path / "absent", None)
    assert cache_module._load_df_cache("anything") == (None, None)


def test_moving_the_cache_dir_switches_the_archive_off(tmp_path, monkeypatch):
    """Isolation has to mean isolation.

    A caller who moves CACHE_DIR -- a test proving a missing file
    raises, a backtest confined to its own fixture, anyone running with
    BW_CACHE_DIR -- has said which cache they want. Answering from the
    archive anyway would hand them the real data and let the test pass
    for the wrong reason, which is worse than a miss because nothing
    looks wrong.
    """
    src = loose(tmp_path, {"gamelog_1_2025-26.json": payload([{"PTS": 41}])})
    archive_path = str(tmp_path / "c.zip")
    ca.pack(src, archive_path)
    reader = ca.open_reader(archive_path)

    # Default dir absent + archive present: the archive answers.
    monkeypatch.setattr(cache_module, "CACHE_DIR", str(tmp_path / "absent"))
    monkeypatch.setattr(cache_module, "DEFAULT_CACHE_DIR", str(tmp_path / "absent"))
    monkeypatch.setattr(cache_module, "ARCHIVE", reader)
    assert cache_module.read_payload("gamelog_1_2025-26") is not None

    # Same archive, but CACHE_DIR has been moved elsewhere: silence.
    monkeypatch.setattr(cache_module, "CACHE_DIR", str(tmp_path / "somewhere_else"))
    assert cache_module.read_payload("gamelog_1_2025-26") is None
    assert cache_module._load_df_cache("gamelog_1_2025-26") == (None, None)


def test_keys_are_sanitised_the_same_way_on_both_paths(tmp_path, monkeypatch):
    """The key a caller passes contains characters a filename can't, so
    both lookups must mangle it identically or the archive silently
    misses every key the folder finds."""
    key = "team_stats_since_07/16/2026"
    name = cache_module._cache_key_to_name(key)
    src = loose(tmp_path, {name: payload([{"GP": 5}])})
    archive_path = str(tmp_path / "c.zip")
    ca.pack(src, archive_path)
    point_at(monkeypatch, tmp_path / "absent", ca.open_reader(archive_path))

    df, _ = cache_module._load_df_cache(key)
    assert list(df["GP"]) == [5]


def test_writes_still_go_to_the_folder(tmp_path, monkeypatch):
    """_save_df_cache is a local-machine path and stays one; the
    deployed app's writes were always throwaway."""
    d = loose(tmp_path, {})
    point_at(monkeypatch, d)
    cache_module._save_df_cache("roster_1", pd.DataFrame([{"PLAYER": "x"}]))
    assert (tmp_path / "data_cache" / "roster_1.json").exists()


# ---- the staleness banner -------------------------------------------------
SEASON = "2025-26"
NOW = datetime.datetime(2026, 9, 19, 12, 0, 0)


def test_the_banner_reads_the_archive_when_there_is_no_folder(tmp_path):
    src = loose(tmp_path, {
        f"team_stats_advanced_{SEASON}.json":
            payload([{"GP": 41}], cached_at="2026-09-19T08:30:00"),
    })
    archive_path = str(tmp_path / "c.zip")
    ca.pack(src, archive_path, packed_at=datetime.datetime(2026, 9, 19, 8, 31))

    state = freshness.cache_age(str(tmp_path / "absent"), SEASON, now=NOW,
                                archive=ca.open_reader(archive_path))
    assert state["in_season"] is True
    assert state["level"] == "fresh"
    assert freshness.describe(state) == "Stats updated today"


def test_an_old_archive_still_reads_as_stale(tmp_path):
    """The failure this banner exists for: the deploy is minutes old,
    the data is a fortnight old, and the page must say the second."""
    src = loose(tmp_path, {
        f"team_stats_advanced_{SEASON}.json":
            payload([{"GP": 41}], cached_at="2026-09-05T08:30:00"),
    })
    archive_path = str(tmp_path / "c.zip")
    ca.pack(src, archive_path, packed_at=datetime.datetime(2026, 9, 5, 8, 31))

    state = freshness.cache_age(str(tmp_path / "absent"), SEASON, now=NOW,
                                archive=ca.open_reader(archive_path))
    assert state["level"] == "stale"
    assert "may be out of date" in freshness.describe(state)


def test_the_game_log_age_comes_from_the_pack_time_not_a_file_date(tmp_path):
    """Modification times on the deployed app record the checkout, so a
    cache packed two weeks ago would otherwise report itself as written
    at deploy time."""
    src = loose(tmp_path, {f"gamelog_1_{SEASON}.json": payload([{"PTS": 30}])})
    archive_path = str(tmp_path / "c.zip")
    packed = datetime.datetime(2026, 9, 5, 8, 31)
    ca.pack(src, archive_path, packed_at=packed)

    got = freshness._newest_gamelog(str(tmp_path / "absent"), SEASON,
                                    archive=ca.open_reader(archive_path))
    assert got == packed


# ---------------------------------------------------------------------
# The circuit breaker, which used to save time only where there was
# none to save.
#
# Observed 25 Sep: twenty-five consecutive [_load_roster_df] timeouts
# for ONE projection, long after the breaker had tripped, because it
# fell through to a fresh live attempt whenever nothing was cached --
# exactly the case where a 15-second wait is unaffordable.
# ---------------------------------------------------------------------

def _clear_breaker():
    cache_module.st.session_state["_live_nba_api_blocked"] = False
    cache_module.st.session_state["_live_nba_api_blocked_until"] = None


def test_a_tripped_breaker_stops_waiting_when_nothing_is_cached(tmp_path, monkeypatch):
    """The regression. Thirty rosters, an endpoint that is down, and
    nothing on disk: the first call pays the timeout, and the rest must
    not."""
    monkeypatch.setattr(cache_module, "CACHE_DIR", str(tmp_path))
    _clear_breaker()
    attempts = []

    def slow_and_broken():
        attempts.append(1)
        raise TimeoutError("read timed out")

    for i in range(30):
        try:
            cache_module.cached_or_live(f"roster_{i}", slow_and_broken)
        except Exception:
            pass

    assert len(attempts) == 1, (
        f"the endpoint was called {len(attempts)} times after it was known "
        f"to be down; that is {(len(attempts)-1)*15}s of a reader's life")


def test_it_still_fetches_before_anything_has_failed(tmp_path, monkeypatch):
    """The control. A breaker that refused from the start would pass the
    test above and leave the app unable to fetch anything at all."""
    monkeypatch.setattr(cache_module, "CACHE_DIR", str(tmp_path))
    _clear_breaker()
    calls = []

    def works():
        calls.append(1)
        return pd.DataFrame([{"x": 1}])

    for i in range(3):
        df, label = cache_module.cached_or_live(f"fine_{i}", works)
        assert label == "live" and not df.empty
    assert len(calls) == 3


def test_a_cached_copy_is_still_served_while_blocked(tmp_path, monkeypatch):
    """Refusing fast must not cost the reader data we already hold. The
    breaker's original purpose is preserved."""
    monkeypatch.setattr(cache_module, "CACHE_DIR", str(tmp_path))
    _clear_breaker()
    (tmp_path / "have_it.json").write_text(payload([{"x": 9}]))

    def broken():
        raise TimeoutError("read timed out")

    try:
        cache_module.cached_or_live("gone", broken)      # trips it, and raises
    except Exception:
        pass
    df, label = cache_module.cached_or_live("have_it", broken)
    assert not df.empty and df.iloc[0]["x"] == 9
    assert "cached copy" in label


def test_the_breaker_heals(tmp_path, monkeypatch):
    """A permanent block turns one blip into a session that can never
    fetch anything it does not already have. After the cooldown a call
    is allowed through to probe."""
    monkeypatch.setattr(cache_module, "CACHE_DIR", str(tmp_path))
    _clear_breaker()

    def broken():
        raise TimeoutError("read timed out")

    try:
        cache_module.cached_or_live("a", broken)
    except Exception:
        pass

    # Wind the clock past the cooldown rather than sleeping through it.
    monkeypatch.setattr(
        cache_module.time, "monotonic",
        lambda: cache_module.st.session_state["_live_nba_api_blocked_until"] + 1)

    calls = []

    def works():
        calls.append(1)
        return pd.DataFrame([{"x": 2}])

    df, label = cache_module.cached_or_live("b", works)
    assert label == "live" and len(calls) == 1
    assert cache_module.st.session_state.get("_live_nba_api_blocked") is False


def test_a_trip_from_another_module_is_adopted(tmp_path, monkeypatch):
    """engine/game_log.py and engine/tracker.py set the same flag with no
    cooldown of their own. Without adopting it, this function would keep
    a second opinion about whether nba.com is up and go on waiting."""
    monkeypatch.setattr(cache_module, "CACHE_DIR", str(tmp_path))
    _clear_breaker()
    cache_module.st.session_state["_live_nba_api_blocked"] = True   # as game_log does

    attempts = []

    def broken():
        attempts.append(1)
        raise TimeoutError("read timed out")

    for i in range(5):
        try:
            cache_module.cached_or_live(f"k{i}", broken)
        except Exception:
            pass

    assert attempts == [], "adopted trip should refuse without calling out at all"
