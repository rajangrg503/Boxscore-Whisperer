"""Tests for batch_freshness.

This module decides whether a cache file gets re-fetched, so every test
here is paired with the opposite case. A freshness check that always
returned False would refresh correctly and cost hours; one that always
returned True would be the bug it was written to fix, and would pass
any test that only ever checked the skip path.
"""

import datetime
import json

import batch_freshness
from batch_freshness import cached_within, settled_or_fresh


NOW = datetime.datetime(2026, 10, 3, 21, 0, 0)

CURRENT = "2026-27"
PREVIOUS = "2025-26"


def write(tmp_path, payload, name="roster_1610612761.json"):
    path = tmp_path / name
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def at(hours_ago):
    return (NOW - datetime.timedelta(hours=hours_ago)).isoformat()


# --------------------------------------------------------------------
# The two that matter
# --------------------------------------------------------------------

def test_a_file_fetched_an_hour_ago_is_left_alone(tmp_path):
    path = write(tmp_path, {"cached_at": at(1), "data": []})
    assert cached_within(path, 20, now=NOW) is True


def test_a_file_fetched_three_weeks_ago_is_refetched(tmp_path):
    """The actual bug. Every roster in the cache was from 10 September
    and the script skipped all thirty of them on every run for three
    weeks, because it only ever asked whether the file existed."""
    path = write(tmp_path, {"cached_at": "2026-09-10T08:36:21", "data": []})
    assert cached_within(path, 20, now=NOW) is False


# --------------------------------------------------------------------
# The window, and why it is not 24
# --------------------------------------------------------------------

def test_the_default_window_is_shorter_than_a_day(tmp_path):
    """The job runs daily at 08:30. A flat 24 hours would call
    yesterday's file fresh whenever a run started a minute early, so the
    data would refresh every other day while the log still said
    "skipping" -- which is the same silent staleness in slow motion."""
    assert batch_freshness.DAILY < 24

    yesterday = write(tmp_path, {"cached_at": at(23), "data": []})
    assert cached_within(yesterday, batch_freshness.DAILY, now=NOW) is False


def test_the_window_is_honoured_as_given(tmp_path):
    """The control on the window: a caller that wants a week gets a
    week, so the expensive endpoints can choose their own cost."""
    path = write(tmp_path, {"cached_at": at(72), "data": []})
    assert cached_within(path, 20, now=NOW) is False
    assert cached_within(path, 24 * 7, now=NOW) is True


# --------------------------------------------------------------------
# Every way of not knowing means fetch
# --------------------------------------------------------------------

def test_a_missing_file_is_fetched(tmp_path):
    assert cached_within(str(tmp_path / "nothing.json"), 20, now=NOW) is False


def test_a_half_written_file_is_fetched(tmp_path):
    """An interrupted run leaves truncated JSON. Being unparseable must
    not be a way to survive forever."""
    path = write(tmp_path, '{"cached_at": "2026-10-03T20:', name="truncated.json")
    assert cached_within(path, 20, now=NOW) is False


def test_a_payload_without_the_stamp_is_fetched(tmp_path):
    """Written before the format carried cached_at. One fetch gains the
    stamp and every run after can judge it properly."""
    path = write(tmp_path, {"data": [{"PLAYER": "Scottie Barnes"}]})
    assert cached_within(path, 20, now=NOW) is False


def test_an_unparseable_stamp_is_fetched(tmp_path):
    path = write(tmp_path, {"cached_at": "last Tuesday", "data": []})
    assert cached_within(path, 20, now=NOW) is False


def test_a_stamp_from_the_future_is_fetched(tmp_path):
    """A clock-skewed write would otherwise read as fresh forever --
    precisely the failure this module exists to end. One wasted fetch is
    the cheaper mistake."""
    path = write(tmp_path, {"cached_at": (NOW + datetime.timedelta(hours=5)).isoformat()})
    assert cached_within(path, 20, now=NOW) is False


def test_a_json_list_is_not_mistaken_for_a_payload(tmp_path):
    path = write(tmp_path, [{"cached_at": at(1)}], name="list.json")
    assert cached_within(path, 20, now=NOW) is False


# --------------------------------------------------------------------
# The control on the whole module
# --------------------------------------------------------------------

def test_it_is_not_simply_always_false(tmp_path):
    """Every test above but two expects False. A function that returned
    False unconditionally would pass nearly all of them, re-fetch 148,000
    immutable box scores on every run, and turn a two-hour job into a
    two-day one."""
    fresh = write(tmp_path, {"cached_at": at(0.5), "data": []})
    assert cached_within(fresh, 20, now=NOW) is True
    assert cached_within(fresh, 1, now=NOW) is True


def test_now_defaults_to_the_real_clock(tmp_path):
    """now= exists for the tests; the callers do not pass it."""
    path = write(tmp_path, {"cached_at": datetime.datetime.now().isoformat()})
    assert cached_within(path, 20) is True


# --------------------------------------------------------------------
# settled_or_fresh: a finished season and a running one are different
# kinds of thing wearing the same shape.
# --------------------------------------------------------------------

def test_a_finished_season_is_kept_on_sight(tmp_path):
    """2025-26 will read the same in five years. Re-fetching it is pure
    cost, which is the judgement that makes the whole refresh
    affordable."""
    old = write(tmp_path, {"cached_at": "2026-09-10T08:36:21"},
                name="hustle_team_stats_2025-26.json")
    assert settled_or_fresh(old, PREVIOUS, CURRENT, 20) is True


def test_the_running_season_goes_stale(tmp_path):
    """THE one with a deadline. Skipping the live season on existence
    means the first fetch after opening night stands in for the whole
    year -- a one-game sample behind every opponent-defence adjustment,
    with nothing anywhere reporting a problem."""
    live = write(tmp_path, {"cached_at": "2026-10-20T22:00:00"},
                 name="hustle_team_stats_2026-27.json")
    in_december = datetime.datetime(2026, 12, 1, 9, 0, 0)
    assert settled_or_fresh(live, CURRENT, CURRENT, 20, now=in_december) is False


def test_the_running_season_is_left_alone_when_just_fetched(tmp_path):
    """The control. A rule that always refreshed the live season would
    pass the test above and fetch it twice in one morning."""
    live = write(tmp_path, {"cached_at": at(1)}, name="hustle_team_stats_2026-27.json")
    assert settled_or_fresh(live, CURRENT, CURRENT, 20, now=NOW) is True


def test_a_finished_season_is_kept_however_old_it_is(tmp_path):
    """The control on the other half: the settled branch must not be
    quietly running the freshness check. A 2023-24 file from years ago
    is still correct and must never be re-fetched."""
    ancient = write(tmp_path, {"cached_at": "2024-01-02T03:04:05"},
                    name="hustle_team_stats_2023-24.json")
    assert settled_or_fresh(ancient, "2023-24", CURRENT, 20, now=NOW) is True


def test_a_missing_file_is_fetched_for_either_kind(tmp_path):
    missing = str(tmp_path / "nothing.json")
    assert settled_or_fresh(missing, PREVIOUS, CURRENT, 20) is False
    assert settled_or_fresh(missing, CURRENT, CURRENT, 20) is False
