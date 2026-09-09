"""Tests for engine/backtest_point_in_time.py's _checkpoint_date_for()
-- the function the entire backtest's point-in-time-correctness claim
rests on. Uses 2023-24's real season start/end dates (2023-10-24 /
2024-04-14), confirmed live via LeagueGameLog, not assumed."""

from datetime import date

from engine.backtest_point_in_time import _checkpoint_date_for

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
