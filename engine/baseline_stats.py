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

from engine.minutes import minutes_aware_means
from engine.stat_columns import STAT_COLUMNS


def stats_from_gamelog(df, stat_columns=STAT_COLUMNS, minutes_aware=True):
    """({stat_col: (mean, std)}, n_games) from a gamelog dataframe.

    minutes_aware=False gives the flat per-game averages this used to
    return, which is what the sweeps compare against -- it is the
    control, not a legacy path.
    """
    means = minutes_aware_means(df, stat_columns) if minutes_aware else None
    stats_dict = {}
    for col, _ in stat_columns:
        mean = df[col].mean() if means is None else means[col]
        stats_dict[col] = (mean, df[col].std())
    return stats_dict, len(df)
