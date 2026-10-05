"""The number on the card is the MIDDLE of a player's range, not his
average, and these tests exist to keep it that way.

WHY IT MATTERS
NBA counting stats are skewed right: a floor at zero, no ceiling, so the
long tail drags the mean above the middle. A posted threshold sits near
the middle of a distribution. Comparing a mean to it therefore reads
high every single time, on every player, in the same direction -- which
looks exactly like finding an edge and is actually a unit error. It is
the best-sourced mistake in public prop analysis.

The regression these guard against is subtle and silent: nothing throws
if the mean comes back instead of the median. Every number still looks
reasonable. The bias is a fraction of a point per stat and only shows up
as a systematic lean across hundreds of rows, which is precisely the
kind of thing a forward record would bake in before anyone noticed.

Each test is paired with its opposite where one exists, because a
function that simply shaved a little off every projection would satisfy
"median is below mean" without being a median at all.
"""

import pytest

from engine import distribution as dist
from engine import tracker
from engine.stat_columns import STAT_COLUMNS

# A deliberately SYMMETRIC grid: 1,001 percentiles evenly spread from
# -3 to +3. Its median and mean coincide, which is the control.
SYMMETRIC = {
    "model": "empirical_z",
    "z": [round(-3.0 + 6.0 * i / 1000.0, 4) for i in range(1001)],
    "held_out": {"coverage_50": 0.5, "coverage_80": 0.8, "brier": 0.2},
}


# --------------------------------------------------------------------
# The premise, against the distributions actually shipped
# --------------------------------------------------------------------

def test_every_shipped_stat_puts_its_median_below_its_mean():
    """If this fails the whole change is pointless -- it means the
    fitted distributions are not right-skewed and a mean would have
    been a fine thing to show."""
    for col, _ in STAT_COLUMNS:
        d = dist.distribution_for(col, 22.0, 9.0, 40)
        assert d is not None, f"{col} has no fitted distribution"
        assert d.quantile(0.5) < 22.0, f"{col} median is not below its mean"


def test_the_gap_is_wider_for_a_bigger_projection():
    """The error a mean introduces is not a constant -- it scales with
    the projection, so it is worst for exactly the high-volume players
    whose thresholds people actually bet."""
    for col, _ in STAT_COLUMNS:
        big = dist.distribution_for(col, 22.0, 9.0, 40)
        small = dist.distribution_for(col, 6.0, 3.0, 40)
        assert (22.0 - big.quantile(0.5)) > (6.0 - small.quantile(0.5)), col


# --------------------------------------------------------------------
# That it is a median, and not merely a smaller number
# --------------------------------------------------------------------

def test_the_median_really_is_the_fifty_percent_point():
    """The definition. Discrete models (the negative binomials) cannot
    land on exactly 0.5 because probability arrives in lumps at whole
    numbers, so the tolerance is loose enough to admit them and tight
    enough to reject a mean, which sits several points off."""
    for col, _ in STAT_COLUMNS:
        d = dist.distribution_for(col, 22.0, 9.0, 40)
        assert d.sf(d.quantile(0.5)) == pytest.approx(0.5, abs=0.05), col


def test_a_symmetric_distribution_puts_the_two_in_the_same_place():
    """The control, and the reason the tests above do not prove much on
    their own: shave 5% off every projection and they all pass. Here
    the right answer is NOT to move the number at all."""
    d = dist.distribution_for("PTS", 20.0, 8.0, 40, distributions={"PTS": SYMMETRIC})
    assert d.quantile(0.5) == pytest.approx(20.0, abs=0.05)


def test_the_median_sits_inside_the_eighty_percent_range():
    for col, _ in STAT_COLUMNS:
        d = dist.distribution_for(col, 18.0, 7.0, 40)
        low, high = d.interval(0.8)
        assert low < d.quantile(0.5) < high, col


# --------------------------------------------------------------------
# What gets written to the record
# --------------------------------------------------------------------

def _preds(**extra):
    base = {"low": 15.0, "predicted": 20.0, "high": 25.0, "base": 19.0}
    base.update(extra)
    return {col: dict(base) for col, _ in STAT_COLUMNS}


def test_the_log_stores_the_number_the_reader_was_shown():
    """A record of a number nobody saw would score the wrong thing."""
    row = tracker._build_row(
        201939, "Stephen Curry", "Los Angeles Clippers", "LAC",
        None, _preds(median=18.4))
    assert row["PTS_mid"] == 18.4


def test_a_prediction_with_no_median_still_logs_its_mean():
    """Callers that build prediction dicts by hand carry no median, and
    for them the mean is the only centre there is -- that must not
    raise, or saving a prediction becomes an exception."""
    row = tracker._build_row(
        201939, "Stephen Curry", "Los Angeles Clippers", "LAC",
        None, _preds())
    assert row["PTS_mid"] == 20.0


def test_the_mean_is_still_kept_alongside_it():
    """Expectations add and medians do not, so engine/team_total.py
    needs the mean to stay exactly where it was. Losing it would break
    the team totals quietly."""
    preds = _preds(median=18.4)
    assert preds["PTS"]["predicted"] == 20.0
    row = tracker._build_row(
        201939, "Stephen Curry", "Los Angeles Clippers", "LAC",
        None, preds)
    assert row["PTS_base"] == 19.0
