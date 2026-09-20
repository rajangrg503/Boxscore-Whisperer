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

THE MARKET'S SIDE OF IT
A line is roughly the number at which a book balances its action, so
the market's own implied probability sits near 50% either way (nearer
52.4% once the usual -110 vig is counted, on both sides at once). We
treat the line as the market's midpoint and measure our distance from
it.

That is an approximation and it is stated as one. The exact implied
probability is in the prices, which this repository does not store --
see the licence note below. The approximation is good enough for
ordering, which is all this produces.

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
we are on. NOT the line. SportsGameOdds' terms forbid redistributing
their data, and a public ranking that carried their numbers would do
exactly that. A reader looks up the line in their own sportsbook,
where they were going to place the bet anyway.
"""

from engine.distribution import distribution_for
from engine.line_input import interpret as interpret_line
from engine.stat_columns import STAT_COLUMNS

STAT_LABELS = dict(STAT_COLUMNS)

# Below this we are not really disagreeing. A leg we make 54% is a leg
# the market has priced about right, and listing it would bury the few
# that are actually interesting.
MIN_PROBABILITY_GAP = 0.08


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
    n_prior = int(projection.get("n_prior") or 0)
    dist = distribution_for(stat, float(mean), float(spread), n_prior)
    if dist is None:
        return None
    return float(dist.sf(float(cutoff)))


def for_leg(leg, projection):
    """One leg, as a disagreement, or None if there isn't one.

    Returns None rather than a zero-sized entry when we have no
    projection, no distribution, or no real difference of opinion --
    a list padded with legs we agree about is a worse list.
    """
    stat = leg.get("stat")
    line = interpret_line(leg.get("line"))
    if stat is None or line is None:
        return None

    p_over = probability_over(stat, projection, line.cutoff)
    if p_over is None:
        return None

    gap = p_over - 0.5
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
        "n_prior": projection.get("n_prior"),
        "side": "over" if gap > 0 else "under",
        # Our probability for the side we are on, which is the number a
        # reader can act on. The complement is on the other side.
        "our_probability": p_over if gap > 0 else 1.0 - p_over,
        "gap": abs(gap),
        # NOTE: the line is deliberately absent. See the module note.
    }


def rank(legs, projections, limit=None):
    """Tonight's legs, biggest disagreement first.

    projections is the record written by tools/capture_projections.py:
    {player_id: {"n_prior": int, "stats": {stat: {...}}}}.
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
        projection.setdefault("n_prior", player.get("n_prior"))
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
