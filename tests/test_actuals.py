"""Tests for engine/actuals.py -- the settling half of the forward test.

Small module, but everything published as an accuracy figure passes
through it, so the edges matter more than the middle: a DNP, a result
sitting exactly on the boundary of our range, a date in a format the
NBA writes and our fixtures do not.
"""

import datetime

import pytest

from engine.actuals import (
    actual_stats, covered, game_row, parse_game_date, side_settled, side_taken)


def payload(*rows):
    return {"cached_at": "x", "data": list(rows)}


def game(date, **stats):
    row = {"GAME_DATE": date, "MIN": 34, "PTS": 20, "REB": 8, "AST": 5,
           "STL": 1, "BLK": 1, "FG3M": 2, "FG3A": 5, "TOV": 3, "OREB": 2}
    row.update(stats)
    return row


# ---- reading a date -------------------------------------------------------
def test_the_nba_writes_dates_one_way_and_our_fixtures_another():
    assert parse_game_date("Apr 13, 2025") == datetime.date(2025, 4, 13)
    assert parse_game_date("2025-04-13") == datetime.date(2025, 4, 13)


def test_a_date_we_cannot_read_is_none_not_a_guess():
    assert parse_game_date("") is None
    assert parse_game_date(None) is None
    assert parse_game_date("not a date") is None


# ---- finding the game -----------------------------------------------------
def test_the_right_game_is_picked_out_of_a_season():
    log = payload(game("Apr 13, 2025", PTS=18), game("Apr 11, 2025", PTS=26))
    assert game_row(log, "2025-04-11")["PTS"] == 26


def test_a_player_who_did_not_play_that_night_is_none():
    log = payload(game("Apr 13, 2025"))
    assert game_row(log, "2025-04-12") is None


def test_no_cached_log_at_all_is_none_not_an_error():
    """Out of season, and on the first night before the refresh has
    caught up, this is the normal case rather than a failure."""
    assert game_row(None, "2025-04-13") is None
    assert game_row({}, "2025-04-13") is None


def test_actual_stats_carries_every_column_the_app_projects():
    from engine.stat_columns import STAT_COLUMNS
    stats = actual_stats(payload(game("Apr 13, 2025")), "2025-04-13")
    for col, _label in STAT_COLUMNS:
        assert col in stats, f"{col} would be unscoreable"


def test_actual_stats_does_not_invent_a_stat_that_is_missing():
    log = payload({"GAME_DATE": "Apr 13, 2025", "PTS": 20})
    stats = actual_stats(log, "2025-04-13")
    assert stats == {"PTS": 20.0}


def test_a_dnp_yields_none_rather_than_a_row_of_zeroes():
    """Zeroes would score as a loss on every over. A DNP is void."""
    assert actual_stats(payload(game("Apr 13, 2025")), "2025-04-12") is None


# ---- was it in the range --------------------------------------------------
@pytest.mark.parametrize("low,high,actual,expected", [
    (17, 42, 25, True),
    (17, 42, 16, False),
    (17, 42, 43, False),
    (17, 42, 17, True),    # inclusive at the edges, once, everywhere
    (17, 42, 42, True),
])
def test_coverage_is_inclusive_at_both_edges(low, high, actual, expected):
    assert covered(low, high, actual) is expected


def test_no_claim_means_no_verdict():
    """Not False. A stat we never projected must not count against us
    in a coverage figure."""
    assert covered(None, 42, 25) is None
    assert covered(17, None, 25) is None
    assert covered(17, 42, None) is None


# ---- which side ------------------------------------------------------------
def test_the_side_we_took_is_read_against_the_cutoff():
    assert side_taken(21.4, 19.5) == "over"
    assert side_taken(18.2, 19.5) == "under"


def test_a_projection_sitting_on_the_line_is_not_a_lean():
    """Recording it as one scores a coin toss as a call."""
    assert side_taken(19.5, 19.5) is None


def test_the_result_settles_the_same_way():
    assert side_settled(21, 19.5) == "over"
    assert side_settled(18, 19.5) == "under"
    assert side_settled(19.5, 19.5) is None
