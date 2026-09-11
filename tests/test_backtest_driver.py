"""Tests for engine/backtest_point_in_time.py's build_backtest_row()
and run_backtest_for_player() -- the per-game and per-player backtest
orchestration. Uses isolated synthetic cache fixtures, not the real
244-player dataset (that's a separate, explicit real-data run)."""

import json
from datetime import date

import pytest

from engine import cache as cache_module
from engine.backtest_point_in_time import build_backtest_row, run_backtest_for_player

SEASON_START = date(2023, 10, 24)


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_module, "CACHE_DIR", str(tmp_path))
    return tmp_path


def _stat_row(**overrides):
    base = {"PTS": 20, "AST": 5, "REB": 6, "STL": 1, "BLK": 0, "FG3M": 2, "TOV": 3, "FG3A": 5, "OREB": 2}
    base.update(overrides)
    return base


def _write_gamelog(tmp_path, player_id, season, rows):
    path = tmp_path / f"gamelog_{player_id}_{season}.json"
    path.write_text(json.dumps({"cached_at": "2026-01-01T00:00:00", "data": rows}))


def _write_checkpoint(tmp_path, season, through_date, team_rows):
    path = tmp_path / f"team_stats_advanced_{season}_through_{through_date}.json"
    path.write_text(json.dumps({"cached_at": "2026-01-01T00:00:00", "data": team_rows}))


LAL_ID = 1610612747
BOS_ID = 1610612738


def _make_prior_games(n, start_day=1, matchup="LAL @ BOS"):
    # Game_ID uses a real regular-season prefix ("0022...") so these
    # fixtures pass _is_regular_season_game_id() like real gamelog rows do.
    return [
        _stat_row(GAME_DATE=f"Nov {start_day + i}, 2023", MATCHUP=matchup, Game_ID=f"002230{1000 + i}")
        for i in range(n)
    ]


def test_normal_game_produces_a_row_with_defense_applied(isolated_cache):
    prior = _make_prior_games(6)  # 6 prior games -- above MIN_BASELINE_GAMES=5
    _write_gamelog(isolated_cache, 2544, "2023-24", prior)
    _write_checkpoint(isolated_cache, "2023-24", "2023-10-31", [
        {"TEAM_ID": BOS_ID, "TEAM_NAME": "Boston Celtics", "DEF_RATING": 110.0, "PACE": 98.0, "GP": 4},
        {"TEAM_ID": LAL_ID, "TEAM_NAME": "Los Angeles Lakers", "DEF_RATING": 115.0, "PACE": 100.0, "GP": 4},
    ])
    game_row = _stat_row(GAME_DATE="Nov 8, 2023", MATCHUP="LAL @ BOS", Game_ID="target_game")

    row = build_backtest_row(2544, "LeBron James", "2023-24", SEASON_START, game_row)

    assert row is not None
    assert row["opponent_team_id"] == BOS_ID
    assert row["PTS_actual"] == 20  # the target game's own real stat line
    assert row["PTS_base"] == 20.0  # mean of the 6 identical prior games
    layers = json.loads(row["layers_json"])
    assert layers["opponent_defense"]["applied"] is True


def test_too_few_prior_games_returns_none(isolated_cache):
    prior = _make_prior_games(3)  # below MIN_BASELINE_GAMES=5
    _write_gamelog(isolated_cache, 2544, "2023-24", prior)
    game_row = _stat_row(GAME_DATE="Nov 5, 2023", MATCHUP="LAL @ BOS", Game_ID="target_game")

    row = build_backtest_row(2544, "LeBron James", "2023-24", SEASON_START, game_row)

    assert row is None


def test_first_month_game_still_produces_a_row_with_defense_unavailable(isolated_cache):
    # 6 prior games, all within October (the season's first month) --
    # enough baseline data, but NO valid opponent-defense checkpoint
    # exists yet. The row must still be produced (baseline is real),
    # with the defense layer explicitly unavailable, not the whole
    # game silently dropped.
    prior = [
        _stat_row(GAME_DATE=f"Oct {25 + i}, 2023", MATCHUP="LAL @ BOS", Game_ID=f"002230{1000 + i}")
        for i in range(6)
    ]
    _write_gamelog(isolated_cache, 2544, "2023-24", prior)
    # Deliberately NOT writing any checkpoint file -- none should be needed/read.
    game_row = _stat_row(GAME_DATE="Oct 31, 2023", MATCHUP="LAL @ BOS", Game_ID="target_game")

    row = build_backtest_row(2544, "LeBron James", "2023-24", SEASON_START, game_row)

    assert row is not None
    layers = json.loads(row["layers_json"])
    assert layers["opponent_defense"]["applied"] is False
    assert layers["opponent_defense"]["data_quality"] == "unavailable"
    # baseline/actual are still real and present
    assert row["PTS_actual"] == 20


def test_unresolvable_opponent_abbreviation_returns_none(isolated_cache):
    prior = _make_prior_games(6)
    _write_gamelog(isolated_cache, 2544, "2023-24", prior)
    game_row = _stat_row(GAME_DATE="Nov 8, 2023", MATCHUP="LAL @ ZZZ", Game_ID="target_game")

    row = build_backtest_row(2544, "LeBron James", "2023-24", SEASON_START, game_row)

    assert row is None


def test_run_backtest_for_player_raises_when_gamelog_missing(isolated_cache):
    with pytest.raises(FileNotFoundError):
        run_backtest_for_player(999999, "Nobody", "2023-24", SEASON_START)


def test_run_backtest_for_player_skips_low_baseline_games_not_errors(isolated_cache):
    # First 4 games have < 5 prior games each (skipped); by the 6th
    # game there's enough history for a row.
    rows = _make_prior_games(8)
    _write_gamelog(isolated_cache, 2544, "2023-24", rows)
    _write_checkpoint(isolated_cache, "2023-24", "2023-10-31", [
        {"TEAM_ID": BOS_ID, "TEAM_NAME": "Boston Celtics", "DEF_RATING": 110.0, "PACE": 98.0, "GP": 4},
    ])

    results = run_backtest_for_player(2544, "LeBron James", "2023-24", SEASON_START)

    # Games at index 0-4 (1st through 5th) have fewer than 5 PRIOR
    # games each; only games from index 5 onward (6th game onward)
    # have >= 5 real prior games. 8 total games -> 3 valid rows.
    assert len(results) == 3


def test_run_backtest_for_player_excludes_playoff_games_even_when_cached(isolated_cache):
    # 10 regular-season games (enough for several past-MIN_BASELINE_GAMES
    # rows) followed by 3 playoff games -- exactly how a real cached
    # gamelog blends both season types together. Playoff games must
    # never produce a row, even though they're present in the cache
    # and would otherwise have enough prior games and a resolvable
    # opponent.
    reg_season = [
        _stat_row(GAME_DATE=f"Nov {i + 1}, 2023", MATCHUP="LAL @ BOS", Game_ID=f"002230{1000 + i}")
        for i in range(10)
    ]
    playoffs = [
        _stat_row(GAME_DATE=f"Apr {20 + i}, 2024", MATCHUP="LAL @ BOS", Game_ID=f"004230{100 + i}")
        for i in range(3)
    ]
    _write_gamelog(isolated_cache, 2544, "2023-24", reg_season + playoffs)
    _write_checkpoint(isolated_cache, "2023-24", "2023-10-31", [
        {"TEAM_ID": BOS_ID, "TEAM_NAME": "Boston Celtics", "DEF_RATING": 110.0, "PACE": 98.0, "GP": 4},
    ])
    # Deliberately NOT caching a March/April checkpoint -- if a playoff
    # row were incorrectly built, it would need one and raise; a clean
    # run with no error is itself part of the proof that none were built.

    results = run_backtest_for_player(2544, "LeBron James", "2023-24", SEASON_START)

    assert all(r["game_id"].startswith("0022") for r in results)
    assert not any(r["game_id"].startswith("0042") for r in results)
    # 10 regular-season games, first 5 skipped for insufficient prior
    # data -> exactly 5 valid regular-season rows, 0 playoff rows.
    assert len(results) == 5
