"""Where our number and the market's differ most, tonight.

WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT
It is a disagreement list. It is not a tip sheet.

The difference matters more than it sounds. No competitor in this
market publishes a measured accuracy number -- that is the gap this
product exists to take -- and shipping "tonight's best bets" before
the forward test has said anything would be the same unevidenced claim
they all make, from a product whose whole pitch is that the working
can be checked.

So this says only what is true today: here is where our projection and
the market's line see a game differently, and by how much. Whether
those disagreements are worth money is a question the season answers,
through tools/score_forward_test.py. If they turn out right more often
than not, that earns stronger wording later, with a number behind it.

HOW DISAGREEMENT IS MEASURED
Not by the gap between the two numbers. A projection two points above
a points line and a projection two rebounds above a rebounds line are
not comparable claims, and ranking them together by raw difference
would put every points prop at the top of every list.

The comparable quantity is a probability. For each leg we compute
P(he goes over) from the same calibrated distribution the page already
uses for "chance he clears X" -- so a leg where we say 72% is a bigger
disagreement than one where we say 54%, whatever the stat.

THE MARKET'S SIDE OF IT -- AND THE ASSUMPTION THAT WAS WRONG
This module was built on one: that a line is the number at which a
book balances its action, so the market's own probability sits near
50% either way, and our distance from 50% is the disagreement. It said
plainly that this was an approximation, and that the exact implied
probability was in the prices, "which this repository does not store".

That last part stopped being true when the parser started reading
bookOdds. And a rehearsal against the one real capture showed the
approximation was not merely imprecise, it was selecting for exactly
the wrong legs:

    strong legs, median break-even      60.2%
    every other side, median            51.1%

Six of the eight biggest "disagreements" were priced PAST us -- we
made Cade Cunningham 59% under his threes and the price needed 68%.
The ranking was finding props where the book had already moved the
price to say what we were about to say, and calling that a
disagreement. It is the opposite of one: it is the market being more
confident than us, in our own direction.

The mechanism is arithmetic, not a small-sample effect. When a book
prices two sides evenly the line IS the midpoint. When it prices them
-300 and +240 the line is nowhere near it, and the market's opinion
lives in the price. Ranking by distance from 50% finds precisely the
props where the book has moved the price -- which are precisely the
ones where the book is most sure.

So the market's side now comes from the prices, de-vigged across the
pair (engine/pricing.implied_probability). Where a leg is not priced
on both sides the old 50% assumption is used and the row says so, so a
caller can tell a measured disagreement from an assumed one.

THE EARLY-SEASON TRAP
Rehearsed against the one real capture (September 2026, 25 legs, six
players), the largest disagreement was LeBron James points at 82%
over. That is not insight, it is a 41-year-old's last-season average
run against a market that has priced his decline -- and it is exactly
what the app does in the first weeks of a season, when it falls back
to the previous season's log.

So the list carries `n_prior` and the range with every row, and a
reader who sees a huge gap on a thin or stale sample can see why. It
self-corrects as the current season fills in. Worth remembering in
late October, when every entry on this list will be built from last
year.

WHAT LEAVES THIS MODULE
Our projection, our probability, the stat, the player, and which side
we are on. NOT the line, and NOT the price. SportsGameOdds' terms
forbid redistributing their data, and a public ranking that carried
their numbers would do exactly that. A reader looks up the line in
their own sportsbook, where they were going to place the bet anyway.

One consequence of the change above, worth stating because it is easy
to miss: `gap` is now our probability minus the MARKET's, so
publishing it beside `our_probability` would hand back the market's
implied probability by subtraction -- which is the price, in different
clothes. It stays internal, for ordering and filtering. `sentence()`
does not use it and nothing that leaves here should.
"""

from engine.distribution import distribution_for
from engine.line_input import interpret as interpret_line
from engine.pricing import implied_probability
from engine.stat_columns import STAT_COLUMNS

STAT_LABELS = dict(STAT_COLUMNS)

# Below this we are not really disagreeing. A leg we make 54% where the
# market makes it 52% is a leg the market has priced about right, and
# listing it would bury the few that are actually interesting.
#
# This is now measured against the DE-VIGGED market probability rather
# than against 50%, which makes it a much higher bar: eight points of
# edge over a real price is a great deal rarer than eight points away
# from a coin flip. The list gets shorter, and what is left is the part
# that was ever worth looking at.
MIN_PROBABILITY_GAP = 0.08

# tools/capture_projections.py writes "n_prior_games"; this module was
# written reading "n_prior", and its tests were written to match the
# module rather than the writer. So every real record produced exactly
# nothing: n_prior came back None, distribution_for declines below five
# prior games, probability_over returned None, and rank() returned an
# empty list on every night of the season while the tests passed.
#
# That is the whole failure mode this codebase keeps meeting -- a green
# test suite measuring the wrong shape -- so both spellings are read
# here, and tests/test_disagreement.py now builds its record through
# tools/capture_projections.py instead of by hand.
PRIOR_GAME_KEYS = ("n_prior_games", "n_prior")


def prior_games(projection):
    """Games behind this projection, under either spelling, as an int."""
    for key in PRIOR_GAME_KEYS:
        value = (projection or {}).get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0
    return 0


def probability_over(stat, projection, cutoff):
    """Our chance the player finishes at or above the cutoff.

    Uses the same distribution the page shows, through the same
    function, so a leg ranked here and the same leg typed into the
    player card can never disagree.
    """
    if projection is None or cutoff is None:
        return None
    mean = projection.get("projected")
    if mean is None:
        return None
    spread = projection.get("spread")
    if spread is None:
        # The recorded projection carries an interval rather than a
        # spread. Recover a usable width from it: the 80% range of a
        # roughly symmetric distribution spans about 2.56 standard
        # deviations, which is close enough to order legs by and is
        # only ever used when the spread itself was not recorded.
        low, high = projection.get("low"), projection.get("high")
        if low is None or high is None:
            return None
        spread = max((float(high) - float(low)) / 2.56, 1e-6)
    n_prior = prior_games(projection)
    dist = distribution_for(stat, float(mean), float(spread), n_prior)
    if dist is None:
        return None
    return float(dist.sf(float(cutoff)))


def market_probability(leg):
    """The market's chance of the OVER, and where the number came from.

    Returns (probability, priced). priced is False when the pair of
    prices was not there and the old 50% assumption had to be used --
    carried through to the row so a caller can tell a measured
    disagreement from an assumed one rather than trusting both alike.
    """
    prices = leg.get("prices") or {}
    implied = implied_probability(prices.get("over"), prices.get("under"))
    if implied is None:
        return 0.5, False
    return implied, True


def for_leg(leg, projection):
    """One leg, as a disagreement, or None if there isn't one.

    Returns None rather than a zero-sized entry when we have no
    projection, no distribution, or no real difference of opinion --
    a list padded with legs we agree about is a worse list, and a leg
    the market has already priced past us is worse still.
    """
    stat = leg.get("stat")
    line = interpret_line(leg.get("line"))
    if stat is None or line is None:
        return None

    p_over = probability_over(stat, projection, line.cutoff)
    if p_over is None:
        return None

    market, priced = market_probability(leg)
    # Against the market's number, not against a coin flip. On an
    # evenly-priced prop these are the same thing; on a juiced one they
    # are opposites, which is the whole reason this changed.
    gap = p_over - market
    if abs(gap) < MIN_PROBABILITY_GAP:
        return None

    return {
        "player_id": leg.get("player_id"),
        "name": leg.get("name"),
        "stat": stat,
        "stat_label": STAT_LABELS.get(stat, stat),
        "projected": projection.get("projected"),
        "low": projection.get("low"),
        "high": projection.get("high"),
        "n_prior": prior_games(projection),
        "side": "over" if gap > 0 else "under",
        # Our probability for the side we are on, which is the number a
        # reader can act on. The complement is on the other side.
        "our_probability": p_over if gap > 0 else 1.0 - p_over,
        # Whether the market's side of this came from its prices or
        # from the 50% assumption. A row built on the assumption is a
        # weaker claim and should be readable as one.
        "market_priced": priced,
        # INTERNAL. our_probability minus this is the market's implied
        # probability, which is the price. Ordering and filtering only
        # -- see WHAT LEAVES THIS MODULE.
        "gap": abs(gap),
    }


def rank(legs, projections, limit=None):
    """Tonight's legs, biggest disagreement first.

    projections is the record written by tools/capture_projections.py:
    {player_id: {"n_prior_games": int, "stats": {stat: {...}}}}.
    """
    players = (projections or {}).get("players") or projections or {}
    out = []
    for leg in legs or []:
        player = players.get(str(leg.get("player_id")))
        if not player:
            continue
        projection = (player.get("stats") or {}).get(leg.get("stat"))
        if not projection:
            continue
        projection = dict(projection)
        # The count lives on the player, not the stat. Carried down
        # under the writer's own spelling -- see PRIOR_GAME_KEYS.
        projection.setdefault("n_prior_games", prior_games(player))
        entry = for_leg(leg, projection)
        if entry:
            out.append(entry)
    out.sort(key=lambda row: row["gap"], reverse=True)
    return out[:limit] if limit else out


def sentence(entry):
    """One line, claiming only what was measured."""
    if not entry:
        return None
    who = entry.get("name") or f"player {entry.get('player_id')}"
    return (f"{who} — {entry['stat_label']}: we make him "
            f"{entry['our_probability']:.0%} to go {entry['side']} "
            f"the market's number (we project {entry['projected']:.1f}).")
