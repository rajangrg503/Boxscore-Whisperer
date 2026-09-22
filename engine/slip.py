"""Tonight's claims, written down before tip-off, and what happened.

WHAT THIS IS FOR
The growth mechanic is posting a nightly slip. The honest version of
that is not a tip sheet -- engine/disagreement.py says at length why
this product cannot publish "tonight's best bets" before the forward
test has a number, and that reasoning has not changed.

What it CAN publish from night one is the thing no competitor does at
all: the claim in public beforehand, and the result in public the next
morning, whether or not it went well. The record is the differentiator.
This posts it while it is being earned rather than claiming it first.

WHY THE SLIP IS A FILE AND NOT A FORMATTER
Because the alternative is cherry-picking, and it would not even feel
like cherry-picking at the time. If the morning post chooses which
claims to report, every morning quietly selects for the ones that
landed, and the public record diverges from the private one. Nobody
would have to intend it.

So commit() writes the card BEFORE the games, settle() may only report
what the card already named, and the test suite pins that the settled
set equals the committed set. The file is the commitment; the morning
post is an accounting of it.

WHAT MAY NOT APPEAR HERE, EVER
No line, no price, no book name. The SportsGameOdds terms forbid
redistributing their data, and this repository is public. That is why
the claim is a projection and a range rather than a side against a
number: "we said 22.3 to 27.0, he scored 30" is entirely ours, and
needs nothing of theirs to be meaningful.

It also happens to be the better claim. A range that is wrong is
visibly wrong. An over/under call is right half the time by accident.

WHAT "COVERED" MEANS, AND WHY THAT AND NOT A HIT RATE
covered = the real number landed inside the range we published. That
is the same quantity engine/forward_record.py reports as `coverage`,
so a reader who follows the nightly posts and a reader who opens the
record page are looking at one number, not two that drift apart.

A range nominally holding RANGE_NOMINAL of outcomes should cover about
that often. Covering far more often means the ranges are too wide to
say anything; far less means they are dishonest. Both are findings,
and both are the reader's to see.
"""

from collections import OrderedDict

# The stats a card reports. Points, rebounds, assists -- the three a
# reader recognises without being taught the product first.
CARD_STATS = ("PTS", "REB", "AST")

# How many players a card names. Small enough to read on a phone,
# large enough that one lucky night cannot carry it.
CARD_SIZE = 6

# The selection rule, stated in the post itself so nobody has to trust
# it: the highest projected scorers on tonight's slate, ties broken by
# player id. Fixed before tip-off and independent of any outcome --
# which is the only property that matters here.
SELECTION = "the {n} highest projected scorers on tonight's slate"


def names_by_id():
    """player_id (str) -> full name, from nba_api's static table.

    Static, local, no network -- the same source engine/players.py and
    engine/odds_snapshot.py already use. Deliberately NOT the name on
    the odds feed's leg: names are public facts about the league, and
    taking this one from our own side keeps the feed out of anything
    published.
    """
    from nba_api.stats.static import players as static_players
    return {str(row["id"]): row["full_name"]
            for row in static_players.get_players()}


def _projected(projection, stat):
    return ((projection.get("stats") or {}).get(stat) or {})


def select(projections, size=CARD_SIZE):
    """Which players tonight's card names. Outcome-independent.

    Ranked by projected points, descending, ties by player id. A player
    with no points projection cannot be ranked and is not on the card.
    """
    ranked = []
    for player_id, projection in (projections.get("players") or {}).items():
        points = _projected(projection, "PTS").get("projected")
        if points is None:
            continue
        ranked.append((-float(points), str(player_id)))
    ranked.sort()
    return [player_id for _points, player_id in ranked[:size]]


def commit(projections, game_date, size=CARD_SIZE, names=None):
    """The slip: what we are claiming tonight, fixed before tip-off.

    Everything here is ours -- a projection and the range around it.
    Write this out and commit it; settle() later may report only what
    this named.
    """
    names = names_by_id() if names is None else names
    claims = []
    for player_id in select(projections, size):
        projection = (projections.get("players") or {})[player_id]
        for stat in CARD_STATS:
            claim = _projected(projection, stat)
            if claim.get("projected") is None:
                continue
            claims.append(OrderedDict((
                ("player_id", player_id),
                ("name", names.get(player_id, f"player {player_id}")),
                ("stat", stat),
                ("projected", claim["projected"]),
                ("low", claim.get("low")),
                ("high", claim.get("high")),
                ("calibrated", claim.get("calibrated")),
            )))
    return OrderedDict((
        ("game_date", str(game_date)),
        ("range_nominal", projections.get("range_nominal")),
        ("selection", SELECTION.format(n=size)),
        ("claims", claims),
    ))


def settle(slip, record):
    """What happened to exactly the claims the slip committed.

    Every claim comes back, including the ones with no result. A claim
    that cannot be settled says so; it does not quietly vanish, which
    is the failure this whole module is arranged to prevent.
    """
    players = (record or {}).get("players") or {}
    settled = []
    for claim in slip.get("claims") or []:
        entry = OrderedDict(claim)
        stats = (players.get(claim["player_id"]) or {}).get("stats") or {}
        result = stats.get(claim["stat"]) or {}
        entry["actual"] = result.get("actual")
        entry["covered"] = result.get("covered")
        settled.append(entry)
    return OrderedDict((
        ("game_date", slip.get("game_date")),
        ("range_nominal", slip.get("range_nominal")),
        ("selection", slip.get("selection")),
        ("claims", settled),
    ))


def tally(settled):
    """Covered, settled, and the ones that never resolved."""
    claims = settled.get("claims") or []
    resolved = [c for c in claims if c.get("covered") is not None]
    return {
        "claims": len(claims),
        "settled": len(resolved),
        "covered": sum(1 for c in resolved if c["covered"]),
        "unsettled": len(claims) - len(resolved),
    }


def _band(claim):
    low, high = claim.get("low"), claim.get("high")
    if low is None or high is None:
        return f"{claim['projected']:.1f}"
    return f"{low:.1f}-{high:.1f}"


def render_card(slip):
    """The pre-game post."""
    nominal = slip.get("range_nominal")
    share = f"{round(nominal * 100)}%" if nominal else "our stated"
    lines = [f"Tonight's card -- {slip['game_date']}", ""]
    lines.append(f"What we expect, before tip-off. Each range is a "
                 f"{share} range.")
    lines.append("")
    for claim in slip.get("claims") or []:
        lines.append(f"  {claim['name']:<24} {claim['stat']:<4} "
                     f"{_band(claim)}")
    lines.append("")
    lines.append(f"Card is {slip.get('selection')}. Results posted in "
                 f"the morning, all of them.")
    return "\n".join(lines)


def render_result(settled):
    """The next-morning post. Every claim the card made, settled."""
    counts = tally(settled)
    lines = [f"How last night's card went -- {settled['game_date']}", ""]
    for claim in settled.get("claims") or []:
        actual = claim.get("actual")
        if claim.get("covered") is None:
            mark, got = "--", "did not play"
        else:
            mark = "in" if claim["covered"] else "out"
            got = f"got {actual:g}"
        lines.append(f"  {claim['name']:<24} {claim['stat']:<4} "
                     f"{_band(claim):<12} {got:<16} {mark}")
    lines.append("")
    if counts["settled"]:
        lines.append(f"{counts['covered']} of {counts['settled']} inside "
                     f"the range.")
    else:
        lines.append("Nothing settled -- no results for this card.")
    if counts["unsettled"]:
        lines.append(f"{counts['unsettled']} claim(s) unsettled and "
                     f"reported as such.")
    return "\n".join(lines)
