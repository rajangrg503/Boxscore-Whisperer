"""What actually happened in one game, and whether we had it.

The forward test has three parts. tools/capture_lines.py records what
the market said, tools/capture_projections.py records what we said, and
something has to settle up. This is the settling.

It is deliberately small and free of I/O. It takes a cached game log
payload -- the same one the app reads -- and answers two questions:
what did the player actually do on that date, and did our range
contain it. Both answers are needed in three places (the scorer, the
prediction tracker, and any accuracy page built on top), and three
copies of "low <= actual <= high" is how a published accuracy figure
quietly stops meaning what the page means.

WHY A DNP IS NOT A MISS
A player who did not play has no line to settle. Scoring that as a
wrong answer would make the app look worse than it is; scoring it as a
right one would make it look better. Both are lies of the same size.
Everything here returns None for "there is no game", and the caller is
expected to carry that through as void rather than fold it into a
percentage.
"""

import datetime

import pandas as pd

from engine.stat_columns import STAT_COLUMNS

# The NBA's game logs write dates like "Apr 13, 2025". Cached payloads
# built by the test harness use ISO. Both are read; anything else is a
# date we do not understand, which is a None rather than a guess.
_DATE_FORMATS = ("%b %d, %Y", "%Y-%m-%d")


def parse_game_date(value):
    """A date from a game log row, or None if it is not one."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        return pd.to_datetime(text).date()
    except (ValueError, TypeError):
        return None


def game_row(payload, game_date):
    """The one row for that date, or None if the player did not play.

    None covers both "no cached log at all" and "logged, but no game
    that day". The caller cannot act differently on the two anyway --
    in both cases there is nothing to settle -- and collapsing them
    here keeps every caller from having to remember that.
    """
    if not payload:
        return None
    wanted = parse_game_date(game_date)
    if wanted is None:
        return None
    for row in payload.get("data") or []:
        if parse_game_date(row.get("GAME_DATE")) == wanted:
            return row
    return None


def actual_stats(payload, game_date):
    """Every tracked stat the player actually put up, or None.

    Only the columns the app projects, so a scored record cannot
    quietly acquire a stat nobody ever made a claim about.
    """
    row = game_row(payload, game_date)
    if row is None:
        return None
    out = {}
    for col, _label in STAT_COLUMNS:
        value = row.get(col)
        if value is None or pd.isna(value):
            continue
        out[col] = float(value)
    return out or None


def covered(low, high, actual):
    """Did our range contain the result? None if we made no claim.

    The single definition of a hit. An 80% range that is scored
    inclusively in one place and exclusively in another produces two
    different accuracy figures from the same night, and the difference
    lands exactly on the boundary cases that matter most.
    """
    if actual is None or low is None or high is None:
        return None
    if pd.isna(actual) or pd.isna(low) or pd.isna(high):
        return None
    return bool(float(low) <= float(actual) <= float(high))


def side_taken(projected, cutoff):
    """Which way our number leans against a line, or None for a tie.

    cutoff is the already-interpreted threshold from
    engine/line_input.py -- 19.5 whether the book wrote "Over 19.5" or
    "20+". A projection sitting exactly on it is not a lean, and
    recording it as one would be scoring a coin toss as a call.
    """
    if projected is None or cutoff is None:
        return None
    projected, cutoff = float(projected), float(cutoff)
    if projected == cutoff:
        return None
    return "over" if projected > cutoff else "under"


def side_settled(actual, cutoff):
    """Which way the game actually went, or None if it landed on the
    line. Counting stats are whole numbers and a cutoff ends in .5, so
    that should never happen -- but minutes are not, and a scorer that
    silently calls a push a loss is the kind of thing nobody notices
    until the published figure is already wrong."""
    if actual is None or cutoff is None:
        return None
    actual, cutoff = float(actual), float(cutoff)
    if actual == cutoff:
        return None
    return "over" if actual > cutoff else "under"
