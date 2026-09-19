"""Tests for engine/hit_rates.py -- the L5/L10/L20/Season row.

The behaviour worth protecting is the collapsing: a log shorter than the
widest window must not produce several rows describing the same games,
because four identical greens read as four confirmations when they are
one sample wearing four hats.
"""

import pandas as pd
import pytest

from engine import hit_rates as hr


def log(points):
    """A game log, newest first, the way the app sorts it."""
    return pd.DataFrame({"PTS": points, "AST": [1] * len(points)})


# ---- collapsing -----------------------------------------------------------
def test_full_season_keeps_every_window():
    rows = hr.hit_rates(log(list(range(30))), 10.0, "PTS")
    assert [r.label for r in rows] == ["L5", "L10", "L20", "Season"]
    assert [r.games for r in rows] == [5, 10, 20, 30]


def test_five_games_collapse_to_a_single_row():
    """The bug this module exists for: five head-to-head games showed
    L5 100%, L10 100%, L20 100% and All H2H 100%."""
    rows = hr.hit_rates(log([40, 38, 35, 33, 31]), 30.0, "PTS", h2h=True)
    assert len(rows) == 1
    assert rows[0].label == "All H2H"
    assert rows[0].games == 5
    assert rows[0].pct == 100.0


def test_eight_games_keep_l5_and_the_whole_log():
    rows = hr.hit_rates(log([20] * 8), 10.0, "PTS")
    assert [(r.label, r.games) for r in rows] == [("L5", 5), ("Season", 8)]


def test_exactly_twenty_games_drops_the_redundant_season_row():
    rows = hr.hit_rates(log([20] * 20), 10.0, "PTS")
    assert [(r.label, r.games) for r in rows] == [("L5", 5), ("L10", 10), ("Season", 20)]


def test_h2h_label_only_changes_the_whole_log_row():
    rows = hr.hit_rates(log(list(range(25))), 10.0, "PTS", h2h=True)
    assert [r.label for r in rows] == ["L5", "L10", "L20", "All H2H"]


def test_empty_log_produces_no_rows():
    assert hr.hit_rates(log([]), 10.0, "PTS") == []


# ---- the percentages themselves ------------------------------------------
def test_windows_take_the_most_recent_games():
    # newest first: the last five are 50,50,50,50,50; older ones are 0
    rows = hr.hit_rates(log([50] * 5 + [0] * 25), 10.0, "PTS")
    by_label = {r.label: r for r in rows}
    assert by_label["L5"].pct == 100.0
    assert by_label["Season"].pct == pytest.approx(5 / 30 * 100)


def test_line_is_strict_so_landing_exactly_on_it_is_not_a_clear():
    rows = hr.hit_rates(log([10] * 10), 10.0, "PTS")
    assert all(r.pct == 0.0 for r in rows)


def test_each_stat_column_is_read_separately():
    frame = pd.DataFrame({"PTS": [30] * 6, "AST": [1] * 6})
    assert hr.hit_rates(frame, 10.0, "PTS")[0].pct == 100.0
    assert hr.hit_rates(frame, 10.0, "AST")[0].pct == 0.0


# ---- the thin-sample caveat ----------------------------------------------
def test_caveat_appears_only_for_thin_samples():
    assert hr.sample_caveat(30) is None
    assert hr.sample_caveat(hr.MIN_TRUSTWORTHY_GAMES) is None
    assert hr.sample_caveat(0) is None          # nothing to caveat
    assert hr.sample_caveat(None) is None


def test_caveat_states_the_count_and_what_one_game_is_worth():
    line = hr.sample_caveat(5, h2h=True)
    assert "5 head-to-head games" in line
    assert "20 points" in line                  # 100/5
    assert "head-to-head" not in hr.sample_caveat(4)


# ---- opponent first, season alongside -------------------------------------
def opp_and_season(opp_points, season_points, line=20.0):
    return hr.against_opponent(log(opp_points), log(season_points), line, "PTS")


def test_opponent_windows_lead_and_the_season_follows():
    """The question a reader is asking is "against THEM", so those windows
    come first; the season rate travels with them for weight."""
    rows = opp_and_season([30] * 18, [10] * 80)
    assert [r.label for r in rows] == ["L5", "L10", "All H2H", "Season"]
    assert [r.games for r in rows] == [5, 10, 18, 80]
    assert rows[0].pct == 100.0        # crushes them
    assert rows[-1].pct == 0.0         # but not everyone else


def test_a_thin_matchup_collapses_instead_of_faking_windows():
    """The median player has six games against a given opponent. Six games
    must not appear as L5/L10/L20."""
    rows = opp_and_season([30] * 6, [10] * 80)
    assert [(r.label, r.games) for r in rows] == [("L5", 5), ("All H2H", 6), ("Season", 80)]


def test_the_two_samples_are_never_mixed():
    """The bug this replaced: the row showed last-5-vs-anyone next to a
    head-to-head table, so 100% against one team read as 40%."""
    rows = opp_and_season([35, 36, 34, 35, 32], [35, 15, 32, 19, 26], line=31.2)
    by = {r.label: r for r in rows}
    assert by["All H2H"].pct == 100.0 and by["All H2H"].games == 5
    assert by["Season"].pct == 40.0 and by["Season"].games == 5


def test_no_history_against_this_opponent_still_shows_the_season():
    rows = opp_and_season([], [30] * 40)
    assert [(r.label, r.games) for r in rows] == [("Season", 40)]


def test_no_games_at_all_shows_nothing():
    assert opp_and_season([], []) == []
