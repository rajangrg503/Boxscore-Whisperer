"""The pure statistical core shared by app.py's get_season_baseline()
(live app) and engine/backtest_point_in_time.py's point_in_time_baseline()
(backtest) -- extracted so the actual mean/std computation lives in
exactly one place, not two. The two callers differ only in HOW they
decide which games belong in the dataframe handed to this function
(live season-selection-with-fallback vs. point-in-time date filtering)
-- never in how stats are computed from that dataframe once decided.

The centre of each projection is a per-minute rate times projected
minutes rather than a flat per-game average -- see engine/minutes.py for
what that buys (about three points of direction accuracy) and what it
does not (it still does not clear the vig). The spread stays the
player's own game-to-game standard deviation: the point estimate moved,
how much he varies around it did not.
"""

from engine.minutes import minutes_aware_means, spread_at_minutes
from engine.stat_columns import STAT_COLUMNS


def stats_from_gamelog(df, stat_columns=STAT_COLUMNS, minutes_aware=True,
                       minutes_override=None):
    """({stat_col: (mean, std)}, n_games) from a gamelog dataframe.

    minutes_aware=False gives the flat per-game averages this used to
    return, which is what the sweeps compare against -- it is the
    control, not a legacy path.

    minutes_override is the reader's own minutes, and it moves the
    SPREAD as well as the mean -- see spread_at_minutes() in
    engine/minutes.py for why, and for the version of this that was
    wrong. Short form: a per-game spread is the spread of games he
    played for his usual length, so keeping it whole while halving the
    mean produces a range whose upper half is games that cannot happen.
    Asserting minutes removes one real source of variation (how long he
    plays); what remains is how productive he is per minute, and that is
    what gets measured.

    This module docstring's rule still holds everywhere else: where the
    minutes are the model's own, the point estimate moves and the spread
    does not.

    The backtest never passes it. Nothing in engine/backtest_*.py knows
    tonight's rotation, and a backtest that could set its own minutes
    would be scoring itself against a number it chose.
    """
    means = (minutes_aware_means(df, stat_columns, minutes_override=minutes_override)
             if minutes_aware else None)
    spreads = (spread_at_minutes(df, stat_columns, minutes_override)
               if minutes_aware and minutes_override is not None else None)
    stats_dict = {}
    for col, _ in stat_columns:
        mean = df[col].mean() if means is None else means[col]
        std = df[col].std() if spreads is None else spreads[col]
        stats_dict[col] = (mean, std)
    return stats_dict, len(df)
