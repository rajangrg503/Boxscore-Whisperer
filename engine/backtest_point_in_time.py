"""Point-in-time helpers for the retroactive backtesting project (see
~/.claude/plans/backtest-engine-plan.md) -- functions that turn "what
would this prediction have used on this real past date" into concrete,
testable logic, kept separate from the live prediction path in
engine/adjustments/ since these only make sense for backtesting.

_checkpoint_date_for() specifically: the piece the whole backtest's
point-in-time-correctness claim rests on. Get this wrong and the
backtest silently leaks future data into past predictions -- see its
docstring and tests/test_backtest_point_in_time.py's boundary cases
for why each one exists.
"""

from datetime import date, timedelta
from typing import Optional


def _checkpoint_date_for(season_start: date, game_date: date) -> Optional[date]:
    """The opponent-defense monthly checkpoint a prediction for
    `game_date` may honestly use, given the season it belongs to
    started on `season_start`.

    Checkpoints are calendar-month-aligned: the checkpoint for any
    game is the LAST DAY OF THE CALENDAR MONTH IMMEDIATELY BEFORE
    game_date's own month -- never game_date's own month, even on
    that month's last day, since some of that month's games may not
    have been played yet as of any given day within it. Correctness
    comes from the checkpoint's own date_to_nullable scoping the
    underlying query (e.g. Oct 31), not from when the checkpoint file
    happened to be fetched -- a November game can never see November
    data because the October checkpoint was never queried past Oct 31
    in the first place.

    Returns None for a game in the season's own first calendar month:
    no prior checkpoint exists within this season, and reaching into
    an earlier, uncached, unverified season was explicitly rejected
    (see the plan's Phase B1 notes) -- these games are excluded from
    opponent_defense scoring entirely, the same MIN_SAMPLE/reason-
    excluded pattern analytics/layer_accuracy.py already uses, rather
    than approximated with a disclosed caveat."""
    if (game_date.year, game_date.month) == (season_start.year, season_start.month):
        return None
    first_of_this_month = game_date.replace(day=1)
    return first_of_this_month - timedelta(days=1)
