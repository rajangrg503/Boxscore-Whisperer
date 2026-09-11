"""Tests for engine/backtest_point_in_time.py -- the point-in-time
helpers the entire backtest's correctness claim rests on. Uses
2023-24's real season start/end dates (2023-10-24 / 2024-04-14),
confirmed live via LeagueGameLog, not assumed."""

from datetime import date

import pandas as pd
import pytest

from engine import cache as cache_module
from engine.backtest_point_in_time import (
    _checkpoint_date_for,
    point_in_time_baseline,
    point_in_time_missing_teammate_games,
    get_point_in_time_opponent_defense,
)

SEASON_START = date(2023, 10, 24)  # 2023-24's real first game date


def test_typical_november_game_uses_october_checkpoint():
    assert _checkpoint_date_for(SEASON_START, date(2023, 11, 3)) == date(2023, 10, 31)


def test_last_day_of_november_still_uses_october_checkpoint():
    # The case that would silently break if the boundary used <= where
    # it needed < -- even November's OWN last day must not use a
    # November checkpoint, since not all of November's games have
    # necessarily been played yet as of November 30 itself.
    assert _checkpoint_date_for(SEASON_START, date(2023, 11, 30)) == date(2023, 10, 31)


def test_first_day_of_december_transitions_to_november_checkpoint():
    # Proves the transition happens exactly at the boundary, not off
    # by one in either direction.
    assert _checkpoint_date_for(SEASON_START, date(2023, 12, 1)) == date(2023, 11, 30)


def test_season_first_real_game_date_is_excluded():
    assert _checkpoint_date_for(SEASON_START, SEASON_START) is None


def test_season_last_real_game_date_uses_march_checkpoint():
    # 2023-24's real last game date, confirmed live via LeagueGameLog.
    assert _checkpoint_date_for(SEASON_START, date(2024, 4, 14)) == date(2024, 3, 31)


def test_leap_day_uses_january_checkpoint_not_february():
    # 2024 is a leap year -- proves the "last day of the prior month"
    # math doesn't quietly break specifically on Feb 29.
    assert _checkpoint_date_for(SEASON_START, date(2024, 2, 29)) == date(2024, 1, 31)


# ---------- point_in_time_baseline ----------

@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_module, "CACHE_DIR", str(tmp_path))
    return tmp_path


def _write_gamelog_cache(tmp_path, player_id, season, rows):
    import json
    path = tmp_path / f"gamelog_{player_id}_{season}.json"
    path.write_text(json.dumps({"cached_at": "2026-01-01T00:00:00", "data": rows}))


def _row(game_date, pts):
    # All 9 real STAT_COLUMNS -- stats_from_gamelog() (called via the
    # real, unmocked STAT_COLUMNS default) needs every one present,
    # same shape a real cached gamelog row actually has.
    return {"GAME_DATE": game_date, "PTS": pts, "AST": 0, "REB": 0,
            "STL": 0, "BLK": 0, "FG3M": 0, "TOV": 0, "FG3A": 0, "OREB": 0}


def test_point_in_time_baseline_excludes_games_on_or_after_target_date(isolated_cache):
    _write_gamelog_cache(isolated_cache, "999", "2023-24", [
        _row("Nov 1, 2023", 10),   # before target -- included
        _row("Nov 2, 2023", 20),   # before target -- included
        _row("Nov 3, 2023", 999),  # ON target date -- excluded (strictly before, not on-or-before)
        _row("Nov 5, 2023", 999),  # after target -- excluded
    ])
    stats, n = point_in_time_baseline("999", "2023-24", date(2023, 11, 3))
    assert n == 2
    assert stats["PTS"][0] == 15.0  # mean of 10 and 20 only -- the 999 rows must never contribute


def test_point_in_time_baseline_raises_when_gamelog_not_cached(isolated_cache):
    with pytest.raises(FileNotFoundError):
        point_in_time_baseline("nonexistent_player", "2023-24", date(2023, 11, 3))


# ---------- point_in_time_missing_teammate_games ----------

def test_filters_then_caps_not_caps_then_filters():
    # 15 future-dated rows placed FIRST (mimicking the cached df's raw
    # order, which is NOT reliably chronological), 10 real past-dated
    # rows placed LAST. Capping at 20 BEFORE filtering would grab all
    # 15 future rows + only 5 of the 10 past rows, then filtering
    # would leave just 5. Filtering first correctly finds all 10 real
    # past games, well under the cap.
    target = date(2023, 12, 1)
    future_rows = [{"Game_ID": f"future{i}", "GAME_DATE": "Dec 15, 2023"} for i in range(15)]
    past_rows = [{"Game_ID": f"past{i}", "GAME_DATE": f"Nov {i + 1}, 2023"} for i in range(10)]
    df = pd.DataFrame(future_rows + past_rows)

    result = point_in_time_missing_teammate_games(df, target, max_games=20)

    assert len(result) == 10  # NOT 5 -- proves filter-before-cap, not cap-before-filter
    assert set(result["Game_ID"]) == {f"past{i}" for i in range(10)}


def test_result_never_contains_a_future_game():
    target = date(2023, 12, 1)
    df = pd.DataFrame(
        [{"Game_ID": "past0", "GAME_DATE": "Nov 1, 2023"}]
        + [{"Game_ID": f"future{i}", "GAME_DATE": "Dec 15, 2023"} for i in range(30)]
    )
    result = point_in_time_missing_teammate_games(df, target, max_games=20)
    assert (pd.to_datetime(result["GAME_DATE"]) < pd.Timestamp(target)).all()


def test_result_is_sorted_most_recent_first():
    target = date(2023, 12, 1)
    df = pd.DataFrame([
        {"Game_ID": "a", "GAME_DATE": "Nov 1, 2023"},
        {"Game_ID": "b", "GAME_DATE": "Nov 20, 2023"},
        {"Game_ID": "c", "GAME_DATE": "Nov 10, 2023"},
    ])
    result = point_in_time_missing_teammate_games(df, target, max_games=20)
    assert list(result["Game_ID"]) == ["b", "c", "a"]  # most recent (Nov 20) first


def test_respects_max_games_cap_after_filtering():
    target = date(2023, 12, 1)
    df = pd.DataFrame([{"Game_ID": f"g{i}", "GAME_DATE": f"Nov {i + 1}, 2023"} for i in range(15)])
    result = point_in_time_missing_teammate_games(df, target, max_games=5)
    assert len(result) == 5
    # the 5 MOST RECENT of the 15 -- Nov 11 through Nov 15
    assert set(result["Game_ID"]) == {"g10", "g11", "g12", "g13", "g14"}


# ---------- get_point_in_time_opponent_defense ----------

def test_first_month_game_returns_none_not_an_error(isolated_cache):
    result = get_point_in_time_opponent_defense("2023-24", SEASON_START, 1610612737, date(2023, 10, 26))
    assert result is None


def test_missing_checkpoint_raises_not_falls_back(isolated_cache):
    # isolated_cache points CACHE_DIR at an empty tmp_path -- no
    # checkpoint files exist, so this must raise, never silently
    # substitute a live fetch or any other data.
    with pytest.raises(FileNotFoundError):
        get_point_in_time_opponent_defense("2023-24", SEASON_START, 1610612737, date(2023, 11, 15))


def test_real_checkpoint_returns_sensible_data():
    # Integration-style: reads one of the actual 18 real checkpoint
    # files cached in Phase B1 (no isolated_cache fixture here --
    # deliberately uses the real data_cache/), confirming the wiring
    # against real data, not just a synthetic fixture.
    result = get_point_in_time_opponent_defense(
        "2023-24", SEASON_START, 1610612737, date(2023, 11, 3)
    )
    assert result is not None
    def_rating, league_avg, note = result
    assert def_rating > 0
    assert league_avg > 0
    assert "2023-10-31" in note  # November games must resolve to the October checkpoint
