"""How often did he actually clear this line? -- and over how many games.

WHY THIS EXISTS
The L5 / L10 / L20 / Season row is the most scannable thing on the page,
which makes it the easiest to misread. Each badge is a percentage with no
denominator, so four greens in a row look like four separate pieces of
evidence. They are not, whenever the log is shorter than the widest
window: a player with five head-to-head games against one opponent has
L5, L10, L20 and Season all computed over the SAME five games, and the
page showed 100% / 100% / 100% / 100%. One sample of five, wearing four
hats, next to a model number in the low forties.

For a reader deciding something on these numbers, that is the worst kind
of wrong -- not an error in the maths, but a layout that manufactures
corroboration out of nothing.

WHAT THIS DOES
Builds the same windows, then collapses any that cover exactly the same
games, keeping the widest honest label for each distinct sample, and
carries the game count with every row so the denominator travels with
the percentage:

    30 games  ->  L5(5)  L10(10)  L20(20)  Season(30)   unchanged
     8 games  ->  L5(5)  Season(8)
     5 games  ->  Season(5)                             one row, not four

Nothing here knows about Streamlit or HTML; it returns plain rows for the
app to draw.
"""

from collections import namedtuple

# The windows worth showing, narrowest first. The full log is appended
# after these and always carries the "everything we have" label.
WINDOWS = (("L5", 5), ("L10", 10), ("L20", 20))

# Below this many games the percentages move too far on a single result
# to be read as a rate, so the page says so underneath them.
MIN_TRUSTWORTHY_GAMES = 10

HitRate = namedtuple("HitRate", "label pct games")


def _pct_over(series, line):
    """Share of values strictly above the line, as a percentage."""
    return float((series > line).sum()) / len(series) * 100.0


def hit_rates(game_log_df, line, stat_col="PTS", h2h=False):
    """Rows of (label, pct, games) for one stat, newest games first.

    game_log_df must already be sorted with the most recent game first --
    the windows are simple head() slices, exactly as the page describes
    them ("his last 5 games").

    Two windows that cover the same games produce one row, labelled with
    the wider of the two, because that is the one that tells the reader
    the sample ran out. An empty log produces no rows at all rather than
    a row of N/A: nothing measured is better said by saying nothing.
    """
    total = len(game_log_df)
    if total == 0:
        return []

    full_label = "All H2H" if h2h else "Season"
    candidates = [(label, min(n, total)) for label, n in WINDOWS]
    candidates.append((full_label, total))

    # Keyed by sample size, so a later (wider) label replaces an earlier
    # one covering the same games. dicts keep insertion order, and the
    # sizes only ever grow, so the result is already narrowest-first.
    by_size = {}
    for label, size in candidates:
        by_size[size] = label

    rows = []
    for size, label in by_size.items():
        subset = game_log_df.head(size)
        rows.append(HitRate(label, _pct_over(subset[stat_col], line), size))
    return rows


def against_opponent(opponent_log, season_log, line, stat_col="PTS"):
    """Rows for one stat, opponent first and the season alongside it.

    WHY BOTH
    Leading with the opponent is what a reader is actually asking: the
    head-to-head table sits directly above this row, and reading "he has
    cleared this every time against Denver" three inches above a row
    saying 40% is the kind of contradiction that makes a page useless.
    That 40% was his last five games against ANYONE.

    But the opponent number cannot stand alone. Across 136 players and
    ~4,000 player-opponent pairs in the cache, the median player has SIX
    games against a given opponent, 85% of matchups have fewer than ten,
    and none have twenty. Shown by itself it would read as the more
    relevant number while being far the less reliable one.

    So the season rate travels with it, carrying its own game count, and
    the windows collapse exactly as everywhere else -- six games produce
    one badge, not four.
    """
    rows = hit_rates(opponent_log, line, stat_col, h2h=True) if opponent_log is not None else []
    season = hit_rates(season_log, line, stat_col) if season_log is not None else []
    if season:
        widest = season[-1]
        rows = rows + [HitRate("Season", widest.pct, widest.games)]
    return rows


def sample_caveat(total_games, h2h=False):
    """One short line when the sample is too thin to lean on, else None.

    Five games is not a hit rate, it is an anecdote with a percent sign.
    The page still shows it -- hiding it would be its own kind of
    dishonesty -- but it should not be shown silently.
    """
    if not total_games or total_games >= MIN_TRUSTWORTHY_GAMES:
        return None
    where = "head-to-head games" if h2h else "games"
    return (f"Only {total_games} {where} behind these percentages — "
            f"one game moves them by {100.0 / total_games:.0f} points.")
