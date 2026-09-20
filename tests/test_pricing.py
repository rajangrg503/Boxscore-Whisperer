"""Tests for engine/pricing.py -- the arithmetic behind a profit claim.

A wrong number here does not look wrong. It looks like a slightly
better season, and it would be published as one. So these tests are
mostly about the ways a money figure quietly inflates itself:

  * a de-vigged price paying out as if it were a real one
  * odds parsed as a number when they were never a price at all
  * an empty run reporting 0% ROI instead of "no figure"
  * a push counted as a loss, or as a leg that never happened
"""

import math

from engine import pricing


# ---- reading a price ------------------------------------------------------
def test_a_plus_price_pays_its_own_number():
    assert pricing.profit_per_unit("+150") == 1.5
    assert pricing.profit_per_unit(150) == 1.5


def test_a_minus_price_pays_the_reciprocal():
    assert math.isclose(pricing.profit_per_unit("-110"), 100 / 110)
    assert math.isclose(pricing.profit_per_unit("-200"), 0.5)


def test_even_money_is_one_unit_either_way_it_is_written():
    assert pricing.profit_per_unit("+100") == 1.0
    assert pricing.profit_per_unit("-100") == 1.0


def test_whitespace_and_a_missing_sign_are_still_read():
    assert pricing.profit_per_unit("  -110 ") == pricing.profit_per_unit("-110")
    assert pricing.profit_per_unit("150") == 1.5


def test_nothing_that_is_not_a_price_becomes_one():
    # Each of these has been a real feed value at some point: absent,
    # blank, a placeholder, or a boolean that Python will happily treat
    # as the integer 1 if nobody stops it.
    for junk in (None, "", "   ", "n/a", "even", True, False, float("nan"),
                 float("inf")):
        assert pricing.profit_per_unit(junk) is None, junk


def test_odds_inside_the_hundred_are_refused_rather_than_guessed():
    # American odds are undefined between -100 and +100. A feed sending
    # -50 has sent something that is not a price; reading it as one
    # would put an invented payout into a published figure.
    for impossible in ("-50", "0", "+99", "-99.5"):
        assert pricing.profit_per_unit(impossible) is None, impossible


# ---- what a price demands -------------------------------------------------
def test_break_even_at_the_standard_price_is_the_familiar_figure():
    assert math.isclose(pricing.break_even(-110), 0.5238, abs_tol=0.0001)


def test_break_even_falls_as_the_price_lengthens():
    assert pricing.break_even("+200") < pricing.break_even("-110")


def test_break_even_of_a_non_price_is_not_a_number():
    assert pricing.break_even("nonsense") is None


# ---- settling one leg -----------------------------------------------------
def test_a_winner_pays_net_profit_not_turnover():
    # -110 returns 1.909 in total; 0.909 of that is the stake coming
    # back. Reporting the gross would roughly double every ROI figure.
    assert math.isclose(pricing.settle("-110", True), 100 / 110)


def test_a_loser_costs_exactly_the_stake():
    assert pricing.settle("+500", False) == -pricing.STAKE


def test_an_unsettled_leg_is_not_a_loss():
    # A push, or a leg we could not score. Both must stay out of the
    # money entirely -- counting them as losses is the single easiest
    # way to make a working model look broken.
    assert pricing.settle("-110", None) is None


def test_a_leg_with_no_price_is_dropped_rather_than_assumed():
    assert pricing.settle(None, True) is None
    assert pricing.settle("", False) is None


# ---- aggregating ----------------------------------------------------------
def test_a_run_adds_up_to_units_and_roi():
    # two winners at even money, one loser: +1 +1 -1 = +1 on 3 staked
    tallied = pricing.tally([1.0, 1.0, -1.0])
    assert tallied["legs"] == 3
    assert tallied["staked"] == 3.0
    assert tallied["profit"] == 1.0
    assert math.isclose(tallied["roi"], 100 / 3, abs_tol=0.01)


def test_nones_are_skipped_so_a_caller_need_not_filter_twice():
    assert pricing.tally([1.0, None, -1.0])["legs"] == 2


def test_an_empty_run_has_no_roi_rather_than_a_zero_one():
    # "We broke even" and "we have no figure" are different sentences
    # and only one of them is true of an empty season.
    tallied = pricing.tally([])
    assert tallied["legs"] == 0
    assert tallied["roi"] is None


def test_losing_every_leg_is_minus_one_hundred_percent():
    assert pricing.tally([-1.0, -1.0])["roi"] == -100.0


# ---- the disclosure rule --------------------------------------------------
def test_a_thin_aggregate_is_not_publishable():
    # One winning leg's profit IS its price. This threshold is a
    # licence rule, not a sample-size one.
    assert not pricing.publishable(pricing.tally([0.909]))


def test_the_threshold_is_inclusive_at_its_own_number():
    just_enough = [1.0] * pricing.MIN_PRICED_LEGS_TO_PUBLISH
    assert pricing.publishable(pricing.tally(just_enough))
    assert not pricing.publishable(pricing.tally(just_enough[:-1]))


def test_nothing_publishes_out_of_an_empty_tally():
    assert not pricing.publishable(pricing.tally([]))
    assert not pricing.publishable(None)


# ---- the sentence ---------------------------------------------------------
def test_the_sentence_names_the_break_even_beside_the_hit_rate():
    line = pricing.summary_sentence(pricing.tally([1.0] * 12), hit_rate=0.55)
    assert "52.4%" in line and "55.0%" in line


def test_a_losing_run_reads_as_a_loss():
    line = pricing.summary_sentence(pricing.tally([-1.0] * 12))
    assert line.startswith("-12.00 units")
    assert "-100.0% ROI" in line


def test_there_is_no_sentence_when_there_is_no_figure():
    assert pricing.summary_sentence(pricing.tally([])) is None
    assert pricing.summary_sentence(None) is None
