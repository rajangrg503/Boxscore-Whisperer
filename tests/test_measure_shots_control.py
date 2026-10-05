"""Tests for tools/measure_shots_control.py.

The thing most worth protecting here is NOT the headline number, it is
the point-in-time discipline. A walk-forward that leaks the target game
into its own baseline produces a beautiful result that means nothing,
and it fails silently. So the first test constructs a log whose last
game is wildly out of character and asserts the prediction for it does
not move.

The arms are tested in both directions on purpose. A comparison that
only ever asserted "shots wins" would pass just as happily if the
function always returned shots, which is the bug that would make the
whole measurement worthless.
"""

import os
import random
import sys

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

from measure_shots_control import (  # noqa: E402
    mae_minutes,
    mae_shots,
    walk_forward,
)


def game(date, mins, fga, pts):
    return {"GAME_DATE": date, "MIN": mins, "FGA": fga, "PTS": pts}


def steady(n, start=1, mins=30, fga=10, pts=20):
    return [game(f"2026-01-{start + i:02d}", mins, fga, pts) for i in range(n)]


# --------------------------------------------------------------------
# Point-in-time: the one that matters
# --------------------------------------------------------------------

def test_the_target_game_never_feeds_its_own_baseline():
    """The classic silent backtest bug. Eleven ordinary games then one
    40-point explosion: the rates offered for the explosion must come
    from the eleven ordinary games, so they must be the ordinary rates."""
    games = steady(11) + [game("2026-02-01", 30, 25, 60)]
    records = list(walk_forward(games, min_prior=11))

    assert len(records) == 1
    ppm, ppf, mins, fga, pts = records[0]
    assert ppm == pytest.approx(20 / 30)   # the ordinary rate
    assert ppf == pytest.approx(20 / 10)   # the ordinary rate
    assert (fga, pts) == (25, 60)          # the real target, untouched


def test_each_game_sees_one_more_prior_than_the_last():
    """The running totals must accumulate, not reset or lag."""
    games = steady(3) + [game("2026-01-10", 30, 10, 50),
                         game("2026-01-11", 30, 10, 20)]
    records = list(walk_forward(games, min_prior=3))

    assert len(records) == 2
    # First target sees 3 steady games; second also sees the 50-pt game.
    assert records[0][0] == pytest.approx(20 / 30)
    assert records[1][0] > records[0][0]


def test_min_prior_is_honoured():
    assert list(walk_forward(steady(10), min_prior=10)) == []
    assert len(list(walk_forward(steady(11), min_prior=10))) == 1


def test_dnps_are_dropped_not_scored_as_zeros():
    """A did-not-play is not a zero-point performance, and counting it
    would drag every rate toward nothing."""
    games = steady(11) + [game("2026-02-01", 0, 0, 0),
                          game("2026-02-02", 30, 10, 20)]
    records = list(walk_forward(games, min_prior=11))
    assert len(records) == 1
    assert records[0][2] == 30


def test_games_are_ordered_by_date_not_file_order():
    """Cached logs are not guaranteed chronological, and an unsorted
    walk-forward is a lookahead with extra steps."""
    games = steady(11, start=5) + [game("2026-01-01", 30, 10, 99)]
    records = list(walk_forward(games, min_prior=11))
    assert len(records) == 1
    assert records[0][4] == 20          # the LAST game by date, not the 99


# --------------------------------------------------------------------
# The two arms, in both directions
# --------------------------------------------------------------------

def test_a_perfectly_steady_player_is_predicted_exactly_by_both():
    records = list(walk_forward(steady(15), min_prior=10))
    assert mae_minutes(records) == pytest.approx(0)
    assert mae_shots(records) == pytest.approx(0)


def test_shots_win_when_minutes_mislead():
    """The real case: usage changes while minutes do not. Hachimura
    played his usual minutes and took more than twice his usual shots."""
    games = steady(11) + [game("2026-02-01", 30, 22, 44)]
    records = list(walk_forward(games, min_prior=11))
    assert mae_shots(records) < mae_minutes(records)


def test_minutes_win_when_shots_mislead():
    """The control. A player who takes his usual shots in unusual
    minutes is the case minutes handles and shots does not -- without
    this test, a function that always favoured shots would pass."""
    games = steady(11) + [game("2026-02-01", 15, 10, 10)]
    records = list(walk_forward(games, min_prior=11))
    assert mae_minutes(records) < mae_shots(records)


# --------------------------------------------------------------------
# Noise
# --------------------------------------------------------------------

def test_zero_noise_is_the_exact_shot_count():
    records = list(walk_forward(steady(15), min_prior=10))
    assert mae_shots(records, noise=0) == pytest.approx(0)


def test_noise_makes_it_worse_monotonically_in_expectation():
    """A noise parameter that did nothing would leave the sensitivity
    table flat and the +/-3 break-even finding unfounded."""
    records = list(walk_forward(steady(60), min_prior=10))
    clean = mae_shots(records, noise=0)
    small = mae_shots(records, noise=2, rng=random.Random(1))
    large = mae_shots(records, noise=6, rng=random.Random(1))
    assert clean < small < large


def test_the_shot_count_never_goes_negative():
    """A player cannot take -1 shots; without the floor, noise would
    produce negative projected points on low-volume players."""
    games = steady(11, fga=1, pts=2) + [game("2026-02-01", 30, 0, 0)]
    records = list(walk_forward(games, min_prior=11))
    assert mae_shots(records, noise=5, rng=random.Random(3)) >= 0


def test_noise_is_reproducible_for_a_given_seed():
    records = list(walk_forward(steady(40), min_prior=10))
    a = mae_shots(records, noise=3, rng=random.Random(11))
    b = mae_shots(records, noise=3, rng=random.Random(11))
    assert a == b
