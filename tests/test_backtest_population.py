"""Tests for build_backtest_population.py -- the point-in-time case set
the published numbers are measured on.

The builder itself reads 3,690 cached box scores, so these tests exercise
its pure pieces on small synthetic inputs: minute parsing, and the
point-in-time baselines (prior PLAYED games only, never the game itself
or anything after it)."""

import pandas as pd
import pytest

import build_backtest_population as bp


def test_minutes_parsing():
    assert bp.minutes_to_float("24:06") == pytest.approx(24.1)
    assert bp.minutes_to_float("31") == 31.0
    assert bp.minutes_to_float("") == 0.0
    assert bp.minutes_to_float(None) == 0.0
    assert bp.minutes_to_float("bogus") == 0.0
    assert bp.minutes_to_float("12:bad") == 0.0


def _games(points, minutes, season="2024-25"):
    """One player's season, in date order."""
    return pd.DataFrame([{
        "player_id": "1", "season": season, "game_id": f"002240000{i}",
        "game_date": pd.Timestamp("2025-01-01") + pd.Timedelta(days=i),
        "minutes": m, "played": m > 0,
        **{col: (p if col == "PTS" else 0.0) for col in bp.BOX_FIELDS},
    } for i, (p, m) in enumerate(zip(points, minutes))])


def test_flat_baselines_use_only_prior_played_games():
    games = bp.add_point_in_time_baselines(_games([10, 20, 30, 0, 40], [30, 30, 30, 0, 30]))
    # first game: nothing before it
    assert games["n_prior"].tolist() == [0, 1, 2, 3, 3]
    assert pd.isna(games["PTS_flat"].iloc[0])
    assert games["PTS_flat"].iloc[1] == pytest.approx(10.0)
    assert games["PTS_flat"].iloc[2] == pytest.approx(15.0)
    # the DNP (0 minutes) contributes nothing and doesn't advance n_prior
    assert games["PTS_flat"].iloc[3] == pytest.approx(20.0)
    assert games["PTS_flat"].iloc[4] == pytest.approx(20.0)
    assert games["mpg_prior"].iloc[4] == pytest.approx(30.0)


def test_minutes_aware_baseline_is_rate_times_projected_minutes():
    """Steady 30 minutes a night: projected minutes equal MPG, so the
    minutes-aware baseline collapses onto the flat average."""
    games = bp.add_point_in_time_baselines(_games([10, 20, 30, 40], [30, 30, 30, 30]))
    assert games["min_projected"].iloc[3] == pytest.approx(30.0)
    assert games["PTS_base"].iloc[3] == pytest.approx(games["PTS_flat"].iloc[3])


def test_minutes_aware_baseline_scales_with_a_rising_workload():
    """Same points per minute, but his last three games ran longer: the
    projection has to come out above the flat average, not equal to it."""
    # minutes 10, 10, 30, 30, 30 -> at row 5 the last three played games
    # are all 30s while the season average is still dragged down to 22
    games = bp.add_point_in_time_baselines(
        _games([10, 10, 30, 30, 30, 30], [10, 10, 30, 30, 30, 30]))
    row = 5
    mpg = games["mpg_prior"].iloc[row]
    assert mpg == pytest.approx(22.0)
    assert games["min_recent"].iloc[row] == pytest.approx(30.0)   # last 3 PLAYED
    assert games["min_projected"].iloc[row] == pytest.approx(26.0)
    assert games["min_projected"].iloc[row] == pytest.approx(0.5 * 30.0 + 0.5 * mpg)
    # 1 point per minute throughout, so the baseline is just the minutes
    assert games["PTS_base"].iloc[row] == pytest.approx(games["min_projected"].iloc[row])
    assert games["PTS_base"].iloc[row] > games["PTS_flat"].iloc[row]


def test_recent_window_counts_played_games_not_calendar_games():
    """A player back from a three-game absence must still have a recent
    window -- taking the last three CALENDAR games would land entirely on
    games he missed and produce NaN."""
    games = bp.add_point_in_time_baselines(
        _games([10, 20, 30, 0, 0, 0, 40], [20, 20, 20, 0, 0, 0, 20]))
    assert games["min_recent"].iloc[6] == pytest.approx(20.0)
    assert not pd.isna(games["PTS_base"].iloc[6])


def test_baseline_spread_is_the_prior_games_spread():
    games = bp.add_point_in_time_baselines(_games([10, 20, 30], [30, 30, 30]))
    assert pd.isna(games["PTS_std"].iloc[1])            # one prior game: no spread yet
    assert games["PTS_std"].iloc[2] == pytest.approx(pd.Series([10.0, 20.0]).std())


def test_players_and_seasons_never_mix():
    a = _games([10, 10, 10], [30, 30, 30])
    b = _games([40, 40, 40], [30, 30, 30], season="2025-26")
    c = a.copy()
    c["player_id"] = "2"
    c["PTS"] = 100.0
    games = bp.add_point_in_time_baselines(pd.concat([a, b, c], ignore_index=True))
    first_of_each = games.groupby(["player_id", "season"]).head(1)
    assert first_of_each["n_prior"].tolist() == [0, 0, 0]
    assert first_of_each["PTS_base"].isna().all()
    second = games.groupby(["player_id", "season"]).nth(1)
    assert sorted(second["PTS_base"].tolist()) == [10.0, 40.0, 100.0]
