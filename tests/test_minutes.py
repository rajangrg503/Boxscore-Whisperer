"""Tests for engine/minutes.py -- the projected-minutes baseline.

The behaviour worth protecting: the window counts games he PLAYED (not
calendar games), the log's ordering cannot silently flip "his last three"
into "his first three", and a log too thin to project from returns None
rather than a confident wrong number.
"""

import pandas as pd
import pytest

from engine import minutes as m
from engine.baseline_stats import stats_from_gamelog

COLS = [("PTS", "Points"), ("REB", "Rebounds")]


def log(minutes, points=None, rebounds=None, newest_first=True):
    """A game log. The app hands these over newest-first; the backtest
    does not, which is exactly the thing that must not matter."""
    n = len(minutes)
    points = points if points is not None else [2 * x for x in minutes]
    rebounds = rebounds if rebounds is not None else [1.0] * n
    dates = pd.date_range("2026-01-01", periods=n)
    df = pd.DataFrame({"GAME_DATE": dates, "MIN": minutes,
                       "PTS": points, "REB": rebounds})
    return df.iloc[::-1].reset_index(drop=True) if newest_first else df


# ---- the projection -------------------------------------------------------
def test_projection_is_half_recent_half_season():
    # oldest..newest = 10, 10, 30, 30, 30 -> mpg 22, recent 30
    d = log([10, 10, 30, 30, 30])
    assert m.projected_minutes(d) == pytest.approx(0.5 * 30 + 0.5 * 22)


def test_steady_workload_projects_to_the_average():
    d = log([28, 28, 28, 28, 28, 28])
    assert m.projected_minutes(d) == pytest.approx(28.0)


def test_ordering_of_the_log_does_not_change_the_answer():
    """The whole point of sorting inside the module."""
    newest_first = log([10, 10, 30, 30, 30], newest_first=True)
    oldest_first = log([10, 10, 30, 30, 30], newest_first=False)
    assert m.projected_minutes(newest_first) == pytest.approx(
        m.projected_minutes(oldest_first))


def test_window_counts_played_games_not_calendar_games():
    """Three zero-minute games between the recent run and tonight must
    not empty the window."""
    d = log([30, 30, 30, 30, 30, 0, 0, 0])
    assert m.projected_minutes(d) == pytest.approx(30.0)
    assert m.describe(d)["games"] == 5          # the DNPs are not games


# ---- the baseline it produces --------------------------------------------
def test_rate_times_minutes_beats_the_flat_average_on_a_rising_workload():
    d = log([10, 10, 30, 30, 30])           # 2 points per minute throughout
    means = m.minutes_aware_means(d, COLS)
    flat = d["PTS"].mean()
    assert means["PTS"] == pytest.approx(2 * m.projected_minutes(d))
    assert means["PTS"] > flat


def test_every_stat_scales_by_the_same_minutes_factor():
    d = log([10, 10, 30, 30, 30], rebounds=[1, 1, 3, 3, 3])
    means = m.minutes_aware_means(d, COLS)
    proj = m.projected_minutes(d)
    total_min = 110.0
    assert means["PTS"] == pytest.approx(d["PTS"].sum() / total_min * proj)
    assert means["REB"] == pytest.approx(d["REB"].sum() / total_min * proj)


# ---- the guard rails ------------------------------------------------------
def test_too_few_games_returns_none():
    d = log([30] * (m.MIN_PRIOR_GAMES - 1))
    assert m.projected_minutes(d) is None
    assert m.minutes_aware_means(d, COLS) is None
    assert m.describe(d) is None


def test_a_log_with_no_minutes_played_returns_none():
    assert m.projected_minutes(log([0, 0, 0, 0, 0, 0])) is None


def test_empty_or_missing_column_returns_none():
    assert m.projected_minutes(pd.DataFrame()) is None
    assert m.projected_minutes(None) is None
    assert m.projected_minutes(pd.DataFrame({"PTS": [1, 2, 3, 4, 5, 6]})) is None


def test_describe_shows_its_working():
    d = log([10, 10, 30, 30, 30])
    got = m.describe(d)
    assert got["recent"] == pytest.approx(30.0)
    assert got["mpg"] == pytest.approx(22.0)
    assert got["projected"] == pytest.approx(26.0)
    assert got["games"] == 5
    assert got["window"] == m.RECENT_WINDOW


# ---- what the app actually calls -----------------------------------------
def test_stats_from_gamelog_is_minutes_aware_by_default():
    d = log([10, 10, 30, 30, 30])
    aware, n = stats_from_gamelog(d, COLS)
    flat, _ = stats_from_gamelog(d, COLS, minutes_aware=False)
    assert n == 5
    assert aware["PTS"][0] > flat["PTS"][0]
    assert flat["PTS"][0] == pytest.approx(d["PTS"].mean())


def test_the_spread_is_untouched_by_the_minutes_model():
    """The point estimate moved; how much he varies around it did not."""
    d = log([10, 10, 30, 30, 30])
    aware, _ = stats_from_gamelog(d, COLS)
    flat, _ = stats_from_gamelog(d, COLS, minutes_aware=False)
    for col, _label in COLS:
        assert aware[col][1] == pytest.approx(flat[col][1])


def test_a_thin_log_falls_back_to_the_flat_average():
    d = log([30] * (m.MIN_PRIOR_GAMES - 1))
    aware, _ = stats_from_gamelog(d, COLS)
    assert aware["PTS"][0] == pytest.approx(d["PTS"].mean())


# ---- why the model declined, said out loud ------------------------------
def test_why_not_is_none_when_the_model_applies():
    assert m.why_not(log([10, 10, 30, 30, 30]), COLS) is None


def test_why_not_names_a_thin_log():
    assert "played games" in m.why_not(log([30, 30]), COLS)


def test_why_not_names_a_missing_stat_column():
    d = log([30] * 6)
    reason = m.why_not(d, COLS + [("FG3A", "3PT attempts")])
    assert "FG3A" in reason


def test_why_not_and_means_always_agree():
    """The page reads why_not to decide whether to explain a per-minute
    rate; if the two ever disagree it prints prose about a model that
    didn't run."""
    for mins in ([10, 10, 30, 30, 30], [30, 30], [0, 0, 0, 0, 0, 0], [30] * 6):
        d = log(mins)
        assert (m.why_not(d, COLS) is None) == (m.minutes_aware_means(d, COLS) is not None)
