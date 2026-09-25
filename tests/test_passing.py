"""Tests for engine/passing.py -- where the ball actually went.

This module answers a question the model cannot: when a reader says a
player is being double teamed, who gets the ball instead. There is no
double-team statistic, so it reports the consequence rather than
guessing at the cause, and it never touches a projection.

What is worth protecting: a thin sample never becomes a rate, a
teammate who has since been traded is marked rather than hidden, and
the date filter is in the format the endpoint actually honours.
"""

import datetime

import pandas as pd

from engine import passing


def made(rows):
    """A PassesMade frame. Columns as the endpoint returns them."""
    return pd.DataFrame([
        {"PASS_TO": name, "PASS_TEAMMATE_PLAYER_ID": pid, "FREQUENCY": freq,
         "PASS": passes, "AST": ast, "FGM": fgm, "FGA": fga,
         "FG3M": fg3m, "FG3A": fg3a}
        for name, pid, freq, passes, ast, fgm, fga, fg3m, fg3a in rows
    ])


# --------------------------------------------------------------------
# What it shows
# --------------------------------------------------------------------

def test_the_most_fed_teammate_comes_first():
    df = made([
        ("Williams, Jalen", 1631114, 0.11, 140, 30, 50, 100, 12, 30),
        ("Holmgren, Chet", 1628369, 0.19, 240, 44, 80, 150, 9, 22),
        ("Caruso, Alex", 1627936, 0.05, 60, 11, 20, 45, 8, 20),
    ])
    out = passing.feeds(df)
    assert [r["name"] for r in out] == [
        "Holmgren, Chet", "Williams, Jalen", "Caruso, Alex"]
    assert out[0]["passes"] == 240


def test_a_handful_of_passes_is_not_a_teammate_he_feeds():
    """Two passes that happened to become a three say nothing about
    where the ball goes."""
    df = made([
        ("Holmgren, Chet", 1628369, 0.19, 240, 44, 80, 150, 9, 22),
        ("Bench, Deep", 999, 0.001, 3, 1, 1, 2, 1, 2),
    ])
    assert [r["name"] for r in passing.feeds(df)] == ["Holmgren, Chet"]


def test_an_empty_or_missing_frame_is_just_empty():
    """Early in a season this endpoint has nothing to say, and the page
    has to render anyway."""
    assert passing.feeds(None) == []
    assert passing.feeds(pd.DataFrame()) == []


# --------------------------------------------------------------------
# What it refuses
# --------------------------------------------------------------------

def test_a_thin_shooting_sample_does_not_become_a_percentage():
    """A percentage over four attempts is not a number, it is a rumour
    -- the same rule the hit-rate badges already follow."""
    row = {"fg3m": 3, "fg3a": 4}
    assert passing.shooting_note(row) is None


def test_a_real_shooting_sample_does():
    """The control. A guard that refused every sample would pass the
    test above and make the panel say nothing at all."""
    note = passing.shooting_note({"fg3m": 12, "fg3a": 30})
    assert "12/30" in note and "40%" in note


def test_a_traded_teammate_is_marked_not_hidden():
    """Early in a season this is last season's passing, so some of these
    names are at other clubs now. Dropping them silently would
    misrepresent how much of his passing the table accounts for, and
    would have the panel describe a team that does not exist."""
    df = made([
        ("Holmgren, Chet", 1628369, 0.19, 240, 44, 80, 150, 9, 22),
        ("Gone, Player", 555, 0.09, 100, 20, 40, 80, 10, 25),
    ])
    out = passing.feeds(df, roster_ids=[1628369])
    assert [r["name"] for r in out] == ["Holmgren, Chet", "Gone, Player"]
    assert out[0]["still_here"] is True
    assert out[1]["still_here"] is False


def test_without_a_roster_nobody_is_claimed_either_way():
    """still_here is None, not False. A caller with no roster to compare
    against must not have the panel assert that everyone has left."""
    df = made([("Holmgren, Chet", 1628369, 0.19, 240, 44, 80, 150, 9, 22)])
    assert passing.feeds(df)[0]["still_here"] is None


# --------------------------------------------------------------------
# The date filter, which fails silently when it is wrong
# --------------------------------------------------------------------

def test_the_recent_window_is_in_the_format_the_endpoint_honours():
    """nba.com wants MM/DD/YYYY. An ISO date is accepted and ignored,
    which would make "recently" quietly identical to the season -- two
    panels showing the same numbers with different headings, and
    nothing anywhere saying why."""
    got = passing.recent_cutoff(datetime.date(2026, 11, 5))
    assert got == "10/15/2026"
    assert got.count("/") == 2 and len(got) == 10


def test_the_window_is_the_documented_length():
    today = datetime.date(2027, 1, 20)
    start = datetime.datetime.strptime(
        passing.recent_cutoff(today), "%m/%d/%Y").date()
    assert (today - start).days == passing.RECENT_DAYS
