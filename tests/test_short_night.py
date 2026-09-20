"""Tests for engine/short_night.py -- a measured rate, shown carefully.

This is the first thing the app says that is a claim about OTHER
players' games rather than about the one in front of the reader, so
what is pinned here is mostly restraint:

  * it stays quiet unless the situation is genuinely worse than average
  * it stays quiet when there is not enough history to say anything
  * the number it prints is the one in the table, not a rounded retelling
  * the wording never promises anything about tonight
"""

import json

import pandas as pd
import pytest

from engine import short_night as sn


def log(*minutes):
    """A game log, newest first, as the NBA returns it."""
    return pd.DataFrame([{"MIN": value, "PTS": 10} for value in minutes])


# ---- counting ------------------------------------------------------------
def test_it_counts_short_appearances_in_the_recent_window():
    assert sn.recent_short_stints(log(4, 3, 30, 35, 33)) == 2


def test_only_the_recent_window_counts():
    """A bad week in November is not this player's situation in March."""
    assert sn.recent_short_stints(log(30, 32, 34, 2, 1, 3)) == 0


def test_minutes_written_as_a_clock_string_are_understood():
    """The NBA writes "34:12" in some payloads and 34.2 in others."""
    assert sn.recent_short_stints(log("4:30", "33:10", "31:00")) == 1


def test_too_few_games_says_nothing_rather_than_guessing():
    assert sn.recent_short_stints(log(4, 3)) is None
    assert sn.recent_short_stints(log()) is None


def test_a_missing_minutes_value_says_nothing():
    """A DNP row with no minutes is not proof of a short night."""
    assert sn.recent_short_stints(log(None, 30, 31)) is None


# ---- when it speaks ------------------------------------------------------
def test_a_player_with_two_short_nights_gets_a_note():
    detail = sn.risk(log(4, 3, 30))
    assert detail is not None
    assert detail["bucket"] == "2-3 short"
    assert detail["rate"] > 0.5
    assert detail["n"] > 1000


def test_a_player_with_one_short_night_gets_a_note():
    detail = sn.risk(log(4, 30, 31))
    assert detail is not None and detail["bucket"] == "1 short"


def test_a_player_playing_normally_gets_nothing():
    """52,298 of the 70,944 player-games behind this table are in the
    quiet bucket. A note that fires on everyone trains a reader to
    ignore the ones that matter."""
    assert sn.risk(log(34, 31, 36)) is None


def test_a_bucket_no_worse_than_average_is_not_worth_saying(monkeypatch):
    monkeypatch.setattr(sn, "RATES", {
        "recent_appearances": 3, "short_stint_minutes": 10.0,
        "overall": {"rate": 0.14},
        "by_short_stints": {"1 short": {"rate": 0.15, "low": 0.1, "high": 0.2,
                                        "n": 500, "lift": 1.05}}})
    assert sn.risk(log(4, 30, 31)) is None


def test_no_rates_table_means_no_note_not_a_guess(monkeypatch):
    monkeypatch.setattr(sn, "RATES", None)
    assert sn.risk(log(4, 3, 30)) is None
    assert sn.recent_short_stints(log(4, 3, 30)) is None


# ---- what it says --------------------------------------------------------
def test_the_sentence_reports_the_measured_rate_and_its_sample():
    detail = sn.risk(log(4, 3, 30))
    line = sn.sentence(detail)
    assert f"{detail['rate']:.0%}" in line
    assert f"{detail['n']:,}" in line


def test_the_sentence_is_about_players_in_this_spot_not_about_tonight():
    """The table is a historical frequency over other players' games.
    "He will go short" claims something it never measured."""
    line = sn.sentence(sn.risk(log(4, 3, 30)))
    assert "players in this spot" in line
    assert "he will" not in line.lower()
    assert "expect" not in line.lower()


def test_no_detail_means_no_sentence():
    assert sn.sentence(None) is None


# ---- the table itself ----------------------------------------------------
def test_the_shipped_table_is_the_one_the_sweep_measured():
    """A hand-edited rate would be a number somebody typed, presented
    to a reader as a measurement."""
    with open(sn.RATES_PATH) as handle:
        rates = json.load(handle)
    assert rates["player_games"] > 60000
    assert len(rates["seasons"]) == 3
    assert rates["overall"]["n"] == rates["player_games"]
    for bucket, row in rates["by_short_stints"].items():
        assert row["low"] <= row["rate"] <= row["high"], bucket
        assert row["n"] > 0


def test_every_mentionable_bucket_has_a_sample_worth_quoting():
    for bucket, row in (sn.RATES["by_short_stints"] or {}).items():
        if row["lift"] >= sn.MIN_LIFT_TO_MENTION:
            assert row["n"] >= 1000, f"{bucket} is too thin to publish"


@pytest.mark.parametrize("minutes,expected", [
    ((4, 3, 2), "2-3 short"),
    ((4, 3, 30), "2-3 short"),
    ((4, 30, 31), "1 short"),
    ((30, 31, 32), "none short"),
])
def test_bucketing_is_what_the_table_was_built_with(minutes, expected):
    assert sn._bucket_for(sn.recent_short_stints(log(*minutes))) == expected


def test_the_log_is_sorted_rather_than_trusted():
    """This frame arrives from several call sites -- a season log, a
    head-to-head log, two seasons concatenated. Reading the wrong end
    of it would describe a player's October instead of his March."""
    oldest_first = pd.DataFrame([
        {"GAME_DATE": "2026-01-01", "MIN": 33},
        {"GAME_DATE": "2026-01-03", "MIN": 31},
        {"GAME_DATE": "2026-01-05", "MIN": 4},
        {"GAME_DATE": "2026-01-07", "MIN": 3},
    ])
    assert sn.recent_short_stints(oldest_first) == 2
