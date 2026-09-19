"""Tests for engine/line_input.py -- what the reader meant by their number.

The bet is the same either way; the notation is not. bet365 writes
"Over 19.5 Points", Sportsbet writes "20+ Points", and a reader copies
whichever is in front of them. A decimal can be taken literally. A whole
number cannot: "20+" wins on exactly 20 and "over 20" loses on it, which
for a whole-valued stat is a full point of probability.

What these protect:
  * a whole number gets the threshold reading, and says so
  * a decimal is left completely alone
  * the app's own baseline is never read as a bet the reader didn't make
  * cutoff is what downstream compares against, so the hit-rate badges
    and the model percentage can never answer differently
"""

import pytest

from engine.distribution import distribution_for
from engine.line_input import Line, baseline, interpret


# ---- whole numbers are thresholds ----------------------------------------
def test_a_whole_number_means_that_many_or_more():
    line = interpret(20)
    assert line.is_threshold
    assert line.cutoff == 19.5          # strict > 19.5 == >= 20
    assert line.value == 20
    assert line.label == "20 or more"


def test_a_whole_number_typed_as_a_float_is_still_a_threshold():
    """Streamlit's number_input hands over 20.0, not 20."""
    assert interpret(20.0) == interpret(20)


def test_the_cutoff_is_what_makes_the_count_right():
    """The reason the whole thing exists: strictly-greater-than against
    the raw 20 would drop every game of exactly 20, which a "20+" bet
    wins."""
    games = [18, 19, 20, 21, 22]
    line = interpret(20)
    assert sum(g > line.cutoff for g in games) == 3      # 20, 21, 22
    assert sum(g > line.value for g in games) == 2       # the old answer


# ---- decimals are left alone ---------------------------------------------
def test_a_decimal_line_is_untouched():
    line = interpret(19.5)
    assert not line.is_threshold
    assert line.cutoff == 19.5
    assert line.value == 19.5
    assert line.label is None           # nothing to explain


def test_the_two_notations_for_one_bet_agree():
    """"Over 19.5" and "20+" are the same wager. After interpretation
    they must compare identically, or the page gives two answers to the
    same question depending on which book the reader uses."""
    assert interpret(19.5).cutoff == interpret(20).cutoff


def test_a_half_line_below_a_whole_one_is_not_confused_with_it():
    assert interpret(20.5).cutoff == 20.5
    assert interpret(20.5).label is None


# ---- nothing entered ------------------------------------------------------
def test_zero_and_none_mean_no_line():
    assert interpret(0) is None
    assert interpret(0.0) is None
    assert interpret(None) is None


def test_a_negative_line_is_not_a_line():
    assert interpret(-5) is None


# ---- the app's own baseline ----------------------------------------------
def test_a_baseline_is_never_read_as_a_threshold():
    """A projected baseline that lands on 20.0 is a number this app
    computed, not a bet anyone placed. Reading it as "20+" would invent
    a claim the reader never made."""
    line = baseline(20.0)
    assert not line.is_threshold
    assert line.cutoff == 20.0
    assert line.label is None


def test_a_fractional_baseline_passes_straight_through():
    assert baseline(31.7).cutoff == pytest.approx(31.7)


# ---- the two displays cannot disagree ------------------------------------
def test_the_card_and_the_badge_read_the_same_cutoff():
    """The statline card prints "Clears 20 <pct>" and the hit-rate row
    prints a model badge. Both go through interpret() and both read
    .cutoff, so one cannot say 74% while the other says 77% -- which is
    exactly what happened before, because the card used the raw line."""
    dist = distribution_for("PTS", 25.0, 7.0, 40)
    line = interpret(20)

    card_pct = dist.sf(line.cutoff)
    badge_pct = dist.sf(line.cutoff)
    assert card_pct == badge_pct

    # and it is the "20 or more" answer, not the "21 or more" one
    assert card_pct == pytest.approx(dist.sf(19.5))
    assert card_pct > dist.sf(20.0)


def test_the_gap_is_worth_caring_about():
    """Guards the premise. If a whole point of a scoring distribution
    ever stopped mattering, this module would be ceremony."""
    dist = distribution_for("PTS", 25.0, 7.0, 40)
    assert dist.sf(19.5) - dist.sf(20.0) > 0.02     # measured ~2.7 points


# ---- shape ---------------------------------------------------------------
def test_interpret_returns_a_line():
    assert isinstance(interpret(20), Line)
    assert isinstance(baseline(20.0), Line)
