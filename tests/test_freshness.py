"""Tests for engine/freshness.py -- the cache-age readout.

The app can't refresh its own data (stats.nba.com blocks Streamlit
Cloud, and GitHub's runners), so a stale cache is its quietest failure:
nothing breaks, the numbers just describe last week. These tests pin the
two things that matter -- that age is measured from the OLDER of the two
inputs a prediction leans on, and that the app stays quiet out of season
instead of crying wolf."""

import datetime
import json
import os

import pytest

from engine import freshness

SEASON = "2026-27"
NOW = datetime.datetime(2027, 1, 10, 12, 0, 0)


def _cache(tmp_path, team_cached_at, games_played, gamelog_age_days=0.0):
    team = {"cached_at": team_cached_at,
            "data": [{"TEAM_ID": 1, "GP": games_played, "DEF_RATING": 112.0, "PACE": 99.0}]}
    (tmp_path / f"team_stats_advanced_{SEASON}.json").write_text(json.dumps(team))
    log = tmp_path / f"gamelog_203999_{SEASON}.json"
    log.write_text(json.dumps({"cached_at": team_cached_at, "data": []}))
    when = (NOW - datetime.timedelta(days=gamelog_age_days)).timestamp()
    os.utime(log, (when, when))
    return str(tmp_path)


def test_fresh_in_season(tmp_path):
    cache = _cache(tmp_path, (NOW - datetime.timedelta(hours=6)).isoformat(), 38, 0.25)
    state = freshness.cache_age(cache, SEASON, now=NOW)
    assert state["level"] == "fresh" and state["in_season"] is True
    assert state["age_days"] == pytest.approx(0.25, abs=0.01)
    assert freshness.describe(state) == "Stats updated today"


def test_aging_then_stale(tmp_path):
    cache = _cache(tmp_path, (NOW - datetime.timedelta(days=3)).isoformat(), 38, 3)
    assert freshness.cache_age(cache, SEASON, now=NOW)["level"] == "aging"
    assert "refresh is due" in freshness.describe(freshness.cache_age(cache, SEASON, now=NOW))

    cache = _cache(tmp_path, (NOW - datetime.timedelta(days=9)).isoformat(), 38, 9)
    state = freshness.cache_age(cache, SEASON, now=NOW)
    assert state["level"] == "stale"
    assert "may be out of date" in freshness.describe(state)
    assert "9 days ago" in freshness.describe(state)


def test_age_follows_the_older_half_of_the_cache(tmp_path):
    # team stats refreshed an hour ago, game logs a week old: a
    # half-done refresh must read as a week old, not an hour
    cache = _cache(tmp_path, (NOW - datetime.timedelta(hours=1)).isoformat(), 38, 7)
    state = freshness.cache_age(cache, SEASON, now=NOW)
    assert state["age_days"] == pytest.approx(7, abs=0.1)
    assert state["level"] == "stale"


def test_quiet_out_of_season(tmp_path):
    cache = _cache(tmp_path, (NOW - datetime.timedelta(days=40)).isoformat(), 0, 40)
    state = freshness.cache_age(cache, SEASON, now=NOW)
    assert state["in_season"] is False and state["level"] == "off_season"
    assert freshness.describe(state) is None


def test_missing_or_broken_cache_says_nothing(tmp_path):
    state = freshness.cache_age(str(tmp_path), SEASON, now=NOW)
    assert state["level"] == "unknown" and state["age_days"] is None
    assert freshness.describe(state) is None

    (tmp_path / f"team_stats_advanced_{SEASON}.json").write_text("{not json")
    assert freshness.cache_age(str(tmp_path), SEASON, now=NOW)["level"] == "unknown"

    (tmp_path / f"team_stats_advanced_{SEASON}.json").write_text(
        json.dumps({"cached_at": "not a date", "data": [{"GP": 12}]}))
    state = freshness.cache_age(str(tmp_path), SEASON, now=NOW)
    assert state["level"] == "unknown"


def test_yesterday_reads_as_yesterday(tmp_path):
    cache = _cache(tmp_path, (NOW - datetime.timedelta(days=1.2)).isoformat(), 38, 1.2)
    assert freshness.describe(freshness.cache_age(cache, SEASON, now=NOW)) == "Stats updated yesterday"
