"""Read a captured odds snapshot: what the market offered, in our terms.

WHY THIS IS A MODULE AND NOT A FEW LINES IN THE SCORER
Two jobs need the same answer. tools/capture_projections.py needs the
player ids so it knows who to project, and tools/score_forward_test.py
needs the lines so it can settle up. If those two read the snapshot
differently, the app projects one set of players and scores another,
and the gap is invisible in both outputs.

WHAT THE SNAPSHOT ACTUALLY LOOKS LIKE
Worth writing down, because the first version of this was written
against a guess and every part of the guess was wrong.

    response.data[]                       one entry per event
      .players{}                          keyed by the feed's player id
        "CADE_CUNNINGHAM_1_NBA": {name, firstName, lastName, teamID}
      .odds{}                             keyed by a composite oddID
        "assists-CADE_CUNNINGHAM_1_NBA-game-ou-over": {
            statID: "assists",            what the bet is on
            playerID: "CADE_CUNNINGHAM_1_NBA",
            betTypeID: "ou",              ou | yn | ml | ml3way | sp
            periodID: "game",             game | reg | 1h | 1q
            sideID: "over",               over | under | yes | no | ...
            bookOverUnder: "5.5",         the line, AS A STRING
            fairOverUnder: "5.5",         the de-vigged consensus
            ...prices, per-bookmaker breakdown, open/close...
        }

Three things in there would each have silently produced an empty or a
wrong answer:

  * the line is bookOverUnder, a string. The guessed parser looked for
    a numeric "overUnder" or "line" and would have found zero legs on
    every night of the season.
  * playerID is the feed's own slug, not an NBA player id. Feeding it
    to the cache as gamelog_<id>_<season> finds nothing, so every
    player would have been skipped as "too little history" -- which
    looks exactly like the off-season.
  * a third of the entries are not game-long player props at all:
    moneylines, spreads, first-quarter and first-half lines. Scoring a
    1Q points line against a full box score would be wrong in a way no
    total would reveal.

SO: FILTER HARD, RESOLVE CAREFULLY, AND SAY WHAT WAS DROPPED
Everything not scored is counted and reported by reason, because
"failed to parse" and "deliberately not scored" are different facts
and only one of them is a bug.
"""

import json

from engine.players import fold_diacritics, get_player_id

# The feed's stat vocabulary, in our column names. Taken from a real
# capture, not from documentation.
STAT_BY_ID = {
    "points": "PTS",
    "assists": "AST",
    "rebounds": "REB",
    "steals": "STL",
    "blocks": "BLK",
    "turnovers": "TOV",
    "threePointersMade": "FG3M",
    "threePointersAttempted": "FG3A",
    "offensiveRebounds": "OREB",
}

# Markets we can see and choose not to score. Combined totals were
# considered and dropped: a reader can add two of our numbers together
# themselves, and a combined line is a different claim from either of
# the ones the app makes. doubleDouble and tripleDouble are yes/no bets
# that need a joint distribution the app does not have.
#
# These are listed rather than left to fall through, so a night's
# report can separate "we do not do these" from "our parser broke".
NOT_SCORED = frozenset({
    "points+assists", "points+rebounds", "rebounds+assists",
    "points+rebounds+assists", "doubleDouble", "tripleDouble",
    "fantasyScore",
})

# Only the game-long over/under on a player. "reg" is regulation-only,
# which settles differently from a box score that includes overtime.
WANTED_BET_TYPE = "ou"
WANTED_PERIOD = "game"


def _normalise_name(name):
    return " ".join(fold_diacritics(str(name)).lower().replace("-", " ").split())


def _folded_index():
    """Every NBA player by folded name. Built once per call site rather
    than at import: nba_api's static list is a module-level table, and
    a module that reaches for it at import time makes every test that
    imports this pay for it."""
    from nba_api.stats.static import players as static_players
    index = {}
    for row in static_players.get_players():
        index.setdefault(_normalise_name(row["full_name"]), []).append(row)
    return index


def resolve_player(name, index=None):
    """The feed's player, as an NBA player id, or None.

    The feed gives a display name and its own slug; the cache is keyed
    by NBA id. So this has to match on the name, which is the one place
    in this pipeline where a silent wrong answer is possible: two
    players have shared a name in this codebase before (the Brandon
    Williams bug in engine/tracker.py).

    engine.players.get_player_id already handles that case and returns
    a note when it had to choose. That note is passed back rather than
    swallowed, so a caller can report it -- a resolved-but-ambiguous
    player is worth seeing, and a projection scored against the wrong
    man is worse than one not scored at all.

    Returns (player_id, note) with player_id None when nothing matched.
    """
    if not name:
        return None, None

    player_id, _full, note = get_player_id(str(name))
    if player_id is not None:
        return player_id, note

    # The feed writes plain ASCII; the NBA's table has the diacritics.
    # "Nikola Jokic" does not match "Nikola Jokić" on a literal search,
    # and that is most of a team's stars in this league.
    index = index if index is not None else _folded_index()
    rows = index.get(_normalise_name(name)) or []
    if not rows:
        return None, None
    if len(rows) == 1:
        return rows[0]["id"], None
    active = [r for r in rows if r.get("is_active")]
    chosen = active[0] if active else rows[0]
    return chosen["id"], (
        f"Multiple players named '{name}' ({len(rows)}) -- resolved to "
        f"{'the active one' if active else 'the most recent match'}, "
        "but verify this is who you meant.")


def _line_from(odd):
    """The number the bet is struck at, and where it came from.

    bookOverUnder is a real bookmaker's line -- what the market
    actually offered. fairOverUnder is the feed's de-vigged consensus,
    which is a better estimate and a worse claim: "we beat the market"
    means we beat something somebody could have bet. So the book line
    wins, and the fallback is recorded rather than blended in silently.
    """
    for field, source in (("bookOverUnder", "book"), ("fairOverUnder", "fair")):
        raw = odd.get(field)
        if raw in (None, ""):
            continue
        try:
            return float(raw), source
        except (TypeError, ValueError):
            continue
    return None, None


def load(path_or_blob):
    """The snapshot's event list, whether given a path or a parsed blob."""
    blob = path_or_blob
    if isinstance(path_or_blob, str):
        with open(path_or_blob) as handle:
            blob = json.load(handle)
    response = blob.get("response", blob)
    if isinstance(response, dict):
        return response.get("data") or response.get("events") or []
    if isinstance(response, list):
        return response
    return []


def read(path_or_blob):
    """Every game-long player over/under in the snapshot, in our terms.

    Returns (legs, report). A leg is a dict of player_id (NBA), stat,
    line, line_source and name. The report counts everything that did
    NOT become a leg, by reason -- unknown stats, unresolved players,
    and the markets we deliberately skip -- because a thin night and a
    broken parser look identical in a leg count alone.
    """
    events = load(path_or_blob)
    index = _folded_index()

    legs = {}
    report = {"unknown_stats": {}, "unresolved_players": {},
              "not_scored": {}, "ambiguous_players": {},
              "skipped_bet_types": {}, "skipped_periods": {},
              "team_markets": {}}
    resolved = {}

    def count(bucket, key):
        report[bucket][key] = report[bucket].get(key, 0) + 1

    for event in events:
        names = {pid: (player or {}).get("name")
                 for pid, player in (event.get("players") or {}).items()}

        for odd in (event.get("odds") or {}).values():
            if not isinstance(odd, dict):
                continue
            stat_id = odd.get("statID")
            if stat_id in NOT_SCORED:
                count("not_scored", stat_id)
                continue
            if odd.get("betTypeID") != WANTED_BET_TYPE:
                count("skipped_bet_types", odd.get("betTypeID"))
                continue
            if odd.get("periodID") != WANTED_PERIOD:
                count("skipped_periods", odd.get("periodID"))
                continue

            feed_id = odd.get("playerID") or odd.get("statEntityID")
            if not feed_id:
                continue
            if feed_id not in names:
                # Team totals are over/unders on the game too, and they
                # name their entity "all", "home" or "away". Reporting
                # those as players we failed to match would cry wolf on
                # every capture, which is how a real unresolved player
                # ends up unnoticed. The event's own players dict is
                # the authority on who is a player.
                count("team_markets", str(feed_id))
                continue

            stat = STAT_BY_ID.get(stat_id)
            if stat is None:
                count("unknown_stats", stat_id)
                continue

            line, source = _line_from(odd)
            if line is None or line <= 0:
                continue

            if feed_id not in resolved:
                name = names.get(feed_id)
                player_id, note = resolve_player(name, index)
                resolved[feed_id] = player_id
                if player_id is None:
                    count("unresolved_players", name or feed_id)
                elif note:
                    report["ambiguous_players"][str(name)] = note
            player_id = resolved[feed_id]
            if player_id is None:
                continue

            # over and under are two entries carrying the same number,
            # and a feed carries the same prop from several books. One
            # leg per player and stat, or one popular prop is weighted
            # several times in the published figure.
            legs.setdefault((str(player_id), stat), {
                "player_id": str(player_id),
                "stat": stat,
                "line": line,
                "line_source": source,
                "name": names.get(feed_id),
            })

    report = {key: value for key, value in report.items() if value}
    return list(legs.values()), report


def nba_player_ids(path_or_blob):
    """Who to project tonight: NBA ids, resolved from the feed's names.

    tools/capture_projections.py used to walk the snapshot for anything
    that looked like a player id, which in this schema returns the
    feed's own slugs -- and a slug finds no cached game log, so every
    player is skipped as having too little history. On an ordinary
    night that is indistinguishable from the off-season.
    """
    legs, report = read(path_or_blob)
    return sorted({leg["player_id"] for leg in legs}), report
