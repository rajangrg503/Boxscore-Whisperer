"""The pure statistical core shared by app.py's get_season_baseline()
(live app) and engine/backtest_point_in_time.py's point_in_time_baseline()
(backtest) -- extracted so the actual mean/std computation lives in
exactly one place, not two. The two callers differ only in HOW they
decide which games belong in the dataframe handed to this function
(live season-selection-with-fallback vs. point-in-time date filtering)
-- never in how stats are computed from that dataframe once decided.
"""

from engine.stat_columns import STAT_COLUMNS


def stats_from_gamelog(df, stat_columns=STAT_COLUMNS):
    """Returns ({stat_col: (mean, std)}, n_games) from a gamelog
    dataframe -- the exact computation get_season_baseline() always
    did inline, now shared instead of duplicated."""
    stats_dict = {}
    for col, _ in stat_columns:
        stats_dict[col] = (df[col].mean(), df[col].std())
    return stats_dict, len(df)
