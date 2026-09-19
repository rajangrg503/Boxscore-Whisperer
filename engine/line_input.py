"""What did the reader mean by the number they typed?

WHY THIS EXISTS
Sportsbooks write the same bet two ways. bet365 posts a decimal line --
"Over 19.5 Points" -- and Sportsbet posts a threshold -- "20+ Points".
They are the same wager, and a reader copying either one onto this page
types what is in front of them.

A decimal is unambiguous: nothing lands on 19.5, so "over 19.5" and "19.5
or more" are the same set of games. A whole number is not:

    "20+ Points"  wins on 20     ->  P(PTS >= 20)
    "over 20"     loses on 20    ->  P(PTS >= 21)

For a stat that only takes whole values, that is a whole point of the
distribution, and it is never a rounding detail. Measured on a fitted
points distribution around 25:

    P(>= 20)  77.2%     what a "20+" bet actually needs
    P(>= 21)  74.5%     what the page used to answer

2.7 points, on every whole-number line. Across a nine-leg multi that
compounds into roughly a third, always in the direction that makes the
bet look worse than it is -- which is the safer direction to be wrong
in, and still wrong.

WHAT IT DOES
A whole number is read as a threshold, because that is how the only
books that use whole numbers write them, and the page says so out loud
rather than deciding quietly. A decimal is left exactly alone.

The strict-greater-than comparison downstream does not change; this
just hands it the right cutoff. Both hit rates and the model
probability read `cutoff`, so the badge and the percentage can never
disagree about what the line means.
"""

from collections import namedtuple

# value  -- what the reader typed, for display
# cutoff -- what to compare against with a strict >
# label  -- how the page describes the reading, or None when there is
#           nothing to explain
Line = namedtuple("Line", "value cutoff label is_threshold")


def interpret(entered):
    """A typed line, or None when nothing was entered.

    Only a user's own number is interpreted. A baseline the app computed
    is not a bet anybody placed, so it never gets the threshold reading
    even when it happens to land on a round figure -- see baseline().
    """
    if entered is None:
        return None
    value = float(entered)
    if value <= 0:
        return None
    if value.is_integer():
        whole = int(value)
        return Line(value=value, cutoff=whole - 0.5,
                    label=f"{whole} or more", is_threshold=True)
    return Line(value=value, cutoff=value, label=None, is_threshold=False)


def baseline(value):
    """The app's own projected number, used when no line was entered.

    Deliberately never a threshold. "20+" is a thing a sportsbook
    offers; a baseline that rounds to 20.0 is just a number this app
    computed, and reading it as a bet would invent a claim the reader
    never made.
    """
    value = float(value)
    return Line(value=value, cutoff=value, label=None, is_threshold=False)
