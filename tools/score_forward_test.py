#!/usr/bin/env python3
"""Settle up: what we projected, against what actually happened.

    python3 tools/score_forward_test.py --projections projections/2026-10-21T013000Z.json
    python3 tools/score_forward_test.py --projections ... --snapshot line_snapshots/2026-10-21/...json
    python3 tools/score_forward_test.py --summarise

WHY THIS EXISTS
tools/capture_projections.py records what we said before tip-off and
tools/capture_lines.py records what the market said. Neither is worth
anything until something compares them with the result. This is that
step, and it is the one that produces the number the whole product is
pitched on: a measured accuracy figure that nobody else in this market
publishes.

TWO CLAIMS, NOT ONE
They are worth keeping apart, because one is much stronger evidence
than the other and they fail in different ways.

  COVERAGE -- did our 80% range contain the actual result? This needs
  our projection and the box score. Both are ours or public, so the
  whole claim is reproducible by anyone from this repository. It is
  also the claim the app actually makes on its face.

  THE LINE -- when our number disagreed with the market, were we on
  the right side? This is the claim a reader cares about more, and it
  is the one that needs somebody else's data.

A snapshot is optional here. Without one, every projection is still
scored for coverage. That matters: the stronger, fully-public claim
must not be hostage to an odds feed being up.

WHAT A SCORED RECORD MAY CONTAIN
Not the line. Not a price, not a bookmaker. SportsGameOdds' terms
forbid redistributing their data through "downloadable files, bulk
exports or similar mechanisms", and a public repository accumulating a
season of nightly prop lines is all three.

So what travels is our side of it: our projection, our interval, the
public box-score result, which way we leaned, and whether that lean
was right -- plus the SHA-256 of the exact snapshot the lean was
computed from. The lean is a fact about our model. The digest fixes
the evidence behind it beyond revision without republishing a single
price, and the raw response is produced on request.

A reader who wants to check the coverage figure can do it from this
repository alone. A reader who wants to check the line figure has to
ask for the snapshot and hash it. That is a real cost, and it is the
honest one to pay.

DNP IS VOID
A player who did not play has nothing to settle. Scoring that as
wrong flatters nobody and scoring it as right is a lie; it is counted
and reported separately, and kept out of every percentage.
"""

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.actuals import (                                   # noqa: E402
    actual_stats, covered, side_settled, side_taken)
from engine.cache import read_payload                          # noqa: E402
from engine.line_input import interpret as interpret_line      # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULT_DIR = os.path.join(REPO_ROOT, "results")

# What the odds feed calls a stat, in our column names. Deliberately
# generous about spelling: this is a vocabulary we do not control and
# cannot see until a real capture exists, so near-misses are matched
# rather than dropped. Anything still unmatched is REPORTED, never
# silently skipped -- an unrecognised stat is a leg we failed to score,
# which is a bug, and it must not look like a leg nobody bet.
STAT_BY_NAME = {
    "points": "PTS", "pts": "PTS",
    "assists": "AST", "ast": "AST",
    "rebounds": "REB", "reb": "REB", "totalrebounds": "REB",
    "steals": "STL", "stl": "STL",
    "blocks": "BLK", "blk": "BLK",
    "turnovers": "TOV", "tov": "TOV",
    "threepointersmade": "FG3M", "three_pointers_made": "FG3M",
    "fg3m": "FG3M", "threes": "FG3M", "3pm": "FG3M",
    "threepointersattempted": "FG3A", "fg3a": "FG3A", "3pa": "FG3A",
    "offensiverebounds": "OREB", "oreb": "OREB",
}

# Keys that have carried a player id in the shapes seen so far. Same
# list tools/capture_projections.py walks for, kept in step with it.
PLAYER_KEYS = ("playerID", "player_id", "statEntityID")
# Keys that have carried the number the bet is struck at. "points" is
# deliberately absent: it is also a stat name, and a node carrying a
# points total would be read as a line struck at that total -- a wrong
# number scored silently, which is the one failure mode here that does
# not announce itself.
LINE_KEYS = ("overUnder", "over_under", "line", "handicap")
# Keys that have carried what the bet is on.
STAT_KEYS = ("statID", "stat_id", "statistic", "market", "marketName", "propType")


def _normalise(name):
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def legs_from_snapshot(path):
    """Every (player, stat, line) the snapshot offers, best effort.

    THE SCHEMA IS NOT DOCUMENTED well enough to assume a shape, and a
    wrong assumption here is expensive in a specific way: it produces a
    scored record that looks complete and is missing half the bets. So
    this walks for objects that carry all three things at once rather
    than following a path, and returns what it could not interpret
    alongside what it could.

    Returns (legs, unknown_stats). legs are dicts of player_id, stat,
    line. unknown_stats counts the stat names that looked like legs but
    matched nothing in STAT_BY_NAME -- the caller is expected to print
    that, not swallow it.
    """
    with open(path) as handle:
        blob = json.load(handle)

    legs = {}
    unknown = Counter()

    def first(node, keys):
        for key in keys:
            if key in node and node[key] is not None:
                return node[key]
        return None

    def walk(node, inherited_player=None):
        if isinstance(node, dict):
            player = first(node, PLAYER_KEYS) or inherited_player
            raw_stat = first(node, STAT_KEYS)
            raw_line = first(node, LINE_KEYS)

            if player is not None and raw_stat is not None and raw_line is not None:
                try:
                    value = float(raw_line)
                except (TypeError, ValueError):
                    value = None
                if value is not None:
                    stat = STAT_BY_NAME.get(_normalise(raw_stat))
                    if stat is None:
                        unknown[str(raw_stat)] += 1
                    else:
                        # One line per player and stat. A feed carries
                        # the same prop from several books; they agree
                        # on the number far more often than not, and
                        # scoring the same leg nine times would weight
                        # one popular prop nine times in the figure.
                        legs.setdefault((str(player), stat),
                                        {"player_id": str(player), "stat": stat,
                                         "line": value})
            for value in node.values():
                walk(value, player)
        elif isinstance(node, list):
            for item in node:
                walk(item, inherited_player)

    walk(blob.get("response", blob))
    return list(legs.values()), unknown


def digest_of(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def score(projections, legs, game_date, read=read_payload):
    """Score one night. Pure apart from the cache read, so the tests
    can hand it a reader and a game log and check the arithmetic."""
    season = projections.get("season")
    by_player_stat = {(leg["player_id"], leg["stat"]): leg for leg in legs}

    players = {}
    totals = Counter()
    for player_id, projection in (projections.get("players") or {}).items():
        actual = actual_stats(read(f"gamelog_{player_id}_{season}"), game_date)
        if actual is None:
            # Did not play, or the game log has not caught up yet. Both
            # are "nothing to settle"; neither is a miss.
            players[player_id] = {"status": "void"}
            totals["void_players"] += 1
            continue

        scored = {}
        for stat, claim in (projection.get("stats") or {}).items():
            if stat not in actual:
                continue
            result = actual[stat]
            entry = {
                "projected": claim.get("projected"),
                "low": claim.get("low"),
                "high": claim.get("high"),
                "actual": result,
                "covered": covered(claim.get("low"), claim.get("high"), result),
            }
            if entry["covered"] is not None:
                totals["scored"] += 1
                totals["covered"] += 1 if entry["covered"] else 0

            leg = by_player_stat.get((player_id, stat))
            if leg is not None:
                # interpret() is the same reading the page gives a
                # number a reader types in: a whole 20 means "20 or
                # more", so the cutoff is 19.5 either way the book
                # wrote it. Scoring it differently here would measure
                # something the product does not claim.
                line = interpret_line(leg["line"])
                if line is not None:
                    took = side_taken(claim.get("projected"), line.cutoff)
                    went = side_settled(result, line.cutoff)
                    entry["side_taken"] = took
                    entry["side_settled"] = went
                    # NOTE: line.cutoff is deliberately NOT recorded.
                    # See "WHAT A SCORED RECORD MAY CONTAIN" above.
                    if took is None or went is None:
                        entry["side_correct"] = None
                        totals["legs_push"] += 1
                    else:
                        entry["side_correct"] = (took == went)
                        totals["legs"] += 1
                        totals["legs_correct"] += 1 if took == went else 0
            scored[stat] = entry

        players[player_id] = {"status": "scored", "stats": scored}
        totals["scored_players"] += 1

    return players, dict(totals)


def build_record(projections, projections_path, legs, snapshot_path,
                 game_date, scored_at, read=read_payload):
    players, totals = score(projections, legs, game_date, read=read)
    sources = {
        "projections_file": os.path.relpath(projections_path, REPO_ROOT),
        "projections_sha256": digest_of(projections_path),
    }
    if snapshot_path:
        # The digest, not the file. The snapshot itself stays out of
        # the repository; this is what makes it auditable anyway.
        sources["line_snapshot_sha256"] = digest_of(snapshot_path)
        sources["line_snapshot_file"] = os.path.basename(snapshot_path)
    return {
        "scored_at": scored_at.isoformat(timespec="seconds"),
        "game_date": str(game_date),
        "season": projections.get("season"),
        "range_nominal": projections.get("range_nominal"),
        "sources": sources,
        "players": players,
        "totals": totals,
    }


def write_record(body, game_date):
    """Write the scored night and return (path, sha256), over the bytes
    on disk so verifying needs nothing but sha256sum."""
    os.makedirs(RESULT_DIR, exist_ok=True)
    path = os.path.join(RESULT_DIR, f"{game_date}.json")
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    with open(path, "wb") as handle:
        handle.write(raw)
    return path, hashlib.sha256(raw).hexdigest()


def game_date_for(projections, override=None):
    """The US date the games were played on.

    Captures run from Australia, so the capture timestamp's UTC date is
    the game date only by luck. Eastern is what the NBA schedules in,
    and October straddles a DST change, so this asks the timezone
    database rather than subtracting a fixed number of hours.
    """
    if override:
        return override
    captured = projections.get("captured_at")
    if not captured:
        return None
    when = datetime.fromisoformat(captured)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return str(when.astimezone(ZoneInfo("America/New_York")).date())
    except Exception:
        return None


def summarise():
    if not os.path.isdir(RESULT_DIR):
        print("nothing scored yet")
        return 0
    files = sorted(f for f in os.listdir(RESULT_DIR) if f.endswith(".json"))
    if not files:
        print("nothing scored yet")
        return 0
    run = Counter()
    for name in files:
        try:
            with open(os.path.join(RESULT_DIR, name)) as handle:
                body = json.load(handle)
            totals = body.get("totals") or {}
        except (OSError, ValueError):
            print(f"  {name}  unreadable")
            continue
        run.update(totals)
        print(f"  {name}  {totals.get('scored', 0)} scored, "
              f"{totals.get('covered', 0)} in range, "
              f"{totals.get('void_players', 0)} void")

    print(f"\n{len(files)} night(s)")
    if run["scored"]:
        print(f"  range coverage: {run['covered']}/{run['scored']} "
              f"= {100.0 * run['covered'] / run['scored']:.1f}%")
    if run["legs"]:
        print(f"  against the line: {run['legs_correct']}/{run['legs']} "
              f"= {100.0 * run['legs_correct'] / run['legs']:.1f}%")
    if run["legs_push"]:
        print(f"  {run['legs_push']} leg(s) landed on the line, not counted")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projections", help="a projections/*.json record")
    parser.add_argument("--snapshot", default=None,
                        help="the line snapshot captured alongside it (optional)")
    parser.add_argument("--date", default=None,
                        help="the US game date; inferred from the capture time if omitted")
    parser.add_argument("--summarise", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="score and report, write nothing")
    args = parser.parse_args(argv)

    if args.summarise:
        return summarise()

    if not args.projections:
        print("nothing to score -- pass --projections", file=sys.stderr)
        return 2

    with open(args.projections) as handle:
        projections = json.load(handle)

    game_date = game_date_for(projections, args.date)
    if not game_date:
        print("could not work out the game date -- pass --date YYYY-MM-DD",
              file=sys.stderr)
        return 2

    legs, unknown = ([], Counter())
    if args.snapshot:
        legs, unknown = legs_from_snapshot(args.snapshot)
        print(f"{len(legs)} leg(s) read from the snapshot")
        if unknown:
            # Loudly. Every one of these is a bet we captured and then
            # failed to score, and the fix is one line in STAT_BY_NAME.
            print("  UNRECOGNISED stat names (not scored): " +
                  ", ".join(f"{name} x{count}"
                            for name, count in unknown.most_common(12)))

    scored_at = datetime.now(timezone.utc)
    body = build_record(projections, args.projections, legs, args.snapshot,
                        game_date, scored_at)
    totals = body["totals"]

    line = (f"{game_date}: {totals.get('scored', 0)} claim(s) scored, "
            f"{totals.get('covered', 0)} inside the range, "
            f"{totals.get('void_players', 0)} player(s) void")
    if totals.get("legs"):
        line += (f"; {totals['legs_correct']}/{totals['legs']} right "
                 f"against the line")

    if args.dry_run:
        print(line + "  -- dry run, nothing written")
        return 0

    path, digest = write_record(body, game_date)
    print(line + f"\n  {os.path.relpath(path, REPO_ROOT)}  sha256 {digest[:16]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
