"""What a right answer was actually worth.

WHY A HIT RATE IS NOT ENOUGH
The forward test already asks whether we were on the right side of the
line. That is the honest accuracy claim and it stays the headline. But
a customer does not get paid in percentages. He asked for exactly this
when he described how the people he follows grow an audience:

    "they dont expect to win everyday, they expect to be in profit"

Those are different questions and they can disagree. A model that is
right 55% of the time on legs priced at -140 loses money; one that is
right 48% of the time on +130 dogs makes it. Publishing only the hit
rate would let us look right while a reader following us went broke,
and that is precisely the failure this product exists not to have.

So: the same settled legs, weighted by what they paid.

THE UNIT
One unit staked per leg, flat. Not a staking plan -- flat betting is
the only scheme that measures the model rather than the scheme, and
anything cleverer (Kelly, progressive, correlated multis) is a separate
claim needing its own evidence.

Profit is net. A won leg at -110 returns 0.909 units of profit, not
1.909 of turnover; a lost leg costs 1.000. ROI is profit over staked,
which is the number a bettor recognises.

THE BREAK-EVEN LINE
Every price carries the percentage you must beat to make money at it:
1 / (1 + profit). At the usual -110 that is 52.38%, which is why "we
were right 52% of the time" is a loss and not a near miss. Every figure
this module produces is reported next to the break-even it had to
clear, because a hit rate without its price is not an answer.

And the usual -110 is not what this feed offers. Measured on the real
capture of 20 September 2026 -- 25 player props, every one priced on
both sides -- the median two-sided overround is 1.068, so the true bar
on a typical leg is 53.3%, not 52.38%. Prop markets are not sides
markets and carry a fatter margin. So the break-even is computed from
the prices actually taken and carried in the tally; the -110 figure is
a fallback only, and says so when it is used.

LICENCE -- READ BEFORE PUBLISHING ANYTHING FROM HERE
The prices are SportsGameOdds' data, exactly like the lines, and their
terms forbid redistributing it through "downloadable files, bulk
exports or similar mechanisms". The repository is public.

A per-leg profit figure IS the price: 0.909 units won says -110 as
plainly as printing it. So per-leg money never leaves this module. What
may be published is an aggregate over enough legs that no individual
price can be read back out of it -- see MIN_PRICED_LEGS_TO_PUBLISH,
which tools/score_forward_test.py enforces before it writes anything.

That threshold is a disclosure rule, not a sample-size rule. It is not
about whether the number means much; it is about whether the number is
somebody else's data wearing a hat.

The aggregate break-even gets the same gate, and deserves a note. It
is closer to a price than the ROI is -- a night whose legs were all
struck at one price would report that price back as its mean. In
practice they never are: on the one real capture the per-leg bar ran
from 39.7% to 67.7% across 25 legs. And it is not optional. Publishing
a strike rate without the bar it had to clear is the exact dishonesty
this apparatus exists to prevent, so the figure travels, aggregated,
gated, and identifying no individual prop's price.
"""

# Flat stakes, one unit a leg. A constant rather than a literal so the
# arithmetic below reads as money and not as a magic 1.
STAKE = 1.0

# Below this many priced legs, a night's aggregate profit is close
# enough to a single price to count as republishing one. See the
# licence note above.
MIN_PRICED_LEGS_TO_PUBLISH = 10

# The price a book posts on both sides of a standard prop. Used only to
# describe the break-even a reader should have in mind, never to stand
# in for a price we do not have.
TYPICAL_PRICE = -110


def profit_per_unit(price):
    """Net profit on a one-unit winner at this American price, or None.

    None means "we cannot price this leg", which is a different fact
    from "this leg lost" and must never be folded into one. A leg we
    cannot price is dropped from the money figures and still counted in
    the hit rate.
    """
    if price is None:
        return None
    if isinstance(price, bool):        # bool is an int; nobody means this
        return None
    try:
        value = float(str(price).strip().replace("+", ""))
    except (TypeError, ValueError):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    # American odds are undefined between -100 and +100. A feed that
    # hands us -50 has sent something that is not a price, and guessing
    # at it would put an invented number into a money figure.
    if abs(value) < 100:
        return None
    if value > 0:
        return value / 100.0
    return 100.0 / abs(value)


def break_even(price):
    """The strike rate this price needs to come out even, or None."""
    profit = profit_per_unit(price)
    if profit is None:
        return None
    return 1.0 / (1.0 + profit)


def settle(price, correct):
    """Units won or lost on one leg, or None when it does not count.

    correct is True, False, or None for a push or an unscorable leg.
    A push returns 0.0 -- the stake comes back, so it is a real result
    worth zero, not a missing one.
    """
    if correct is None:
        return None
    profit = profit_per_unit(price)
    if profit is None:
        return None
    return profit * STAKE if correct else -STAKE


def tally(results, break_evens=None):
    """Aggregate a run of per-leg unit results.

    results: an iterable of the numbers settle() returned. Nones are
    skipped here as well as there, so a caller can hand this a whole
    night without filtering twice.

    break_evens: the break_even() of each leg's price, in any order. Its
    mean is carried through as the bar this run actually had to clear.
    Measured on the one real capture (20 Sep 2026, 25 legs priced both
    sides) that bar is 53.3%, not the 52.4% a -110 assumption gives:
    the feed's median two-sided overround is 1.068, so the standard
    price is not the standard price. Publishing a hit rate against the
    wrong break-even would flatter us by about a point, every time.

    Returns a dict with legs, staked, profit, roi (a percentage) and
    break_even (a fraction, or None). roi is deliberately None rather
    than 0.0 on an empty run: "we broke even" and "we have no figure"
    are not the same sentence.
    """
    units = [value for value in results if value is not None]
    staked = STAKE * len(units)
    profit = sum(units)
    bars = [value for value in (break_evens or []) if value is not None]
    return {
        "legs": len(units),
        "staked": round(staked, 4),
        "profit": round(profit, 4),
        "roi": (round(100.0 * profit / staked, 2) if staked else None),
        "break_even": (round(sum(bars) / len(bars), 4) if bars else None),
    }


def publishable(tallied):
    """True when this aggregate covers enough legs to publish.

    The whole point is that a thin aggregate reconstructs the prices
    behind it. One winning leg's profit is the price; two legs with a
    known win/loss pattern narrow it hard. Ten does not.
    """
    return bool(tallied) and tallied.get("legs", 0) >= MIN_PRICED_LEGS_TO_PUBLISH


def summary_sentence(tallied, hit_rate=None):
    """One line a person can read, or None when there is no figure.

    Says the profit with its ROI, and -- when the hit rate is to hand --
    the break-even those legs actually had to clear, so a reader never
    sees a strike rate without knowing what it was up against. The
    measured bar is used when the tally carries one; TYPICAL_PRICE is
    only the fallback, and is named as an assumption when it is used.
    """
    if not tallied or not tallied.get("legs"):
        return None
    profit = tallied["profit"]
    roi = tallied["roi"]
    sign = "+" if profit >= 0 else ""
    line = (f"{sign}{profit:.2f} units on {tallied['legs']} leg(s) at one "
            f"unit each ({roi:+.1f}% ROI)")
    if hit_rate is not None:
        measured = tallied.get("break_even")
        if measured is not None:
            line += (f"; {hit_rate:.1%} right, against {measured:.1%} needed "
                     f"to break even at the prices taken")
        else:
            line += (f"; {hit_rate:.1%} right, against "
                     f"{break_even(TYPICAL_PRICE):.1%} needed to break even "
                     f"if every leg were {TYPICAL_PRICE}")
    return line
