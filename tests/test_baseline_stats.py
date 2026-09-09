"""Tests for engine/baseline_stats.py's stats_from_gamelog() -- the
pure computation shared by app.py's get_season_baseline() and
engine/backtest_point_in_time.py's point_in_time_baseline()."""

import pandas as pd

from engine.baseline_stats import stats_from_gamelog

STAT_COLS = [("PTS", "Points"), ("AST", "Assists")]


def test_computes_mean_and_std_per_stat():
    df = pd.DataFrame({"PTS": [10, 20, 30], "AST": [1, 2, 3]})
    stats, n = stats_from_gamelog(df, stat_columns=STAT_COLS)
    assert n == 3
    assert stats["PTS"][0] == 20.0
    assert stats["AST"][0] == 2.0


def test_empty_dataframe_returns_zero_games():
    df = pd.DataFrame({"PTS": [], "AST": []})
    stats, n = stats_from_gamelog(df, stat_columns=STAT_COLS)
    assert n == 0
    assert pd.isna(stats["PTS"][0])
