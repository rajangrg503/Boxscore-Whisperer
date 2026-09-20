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

THREE CLAIMS, NOT ONE
They are worth keeping apart, because they are not equally strong
evidence and they fail in different ways.

  COVERAGE -- did our 80% range contain the actual result? This needs
  our projection and the box score. Both are ours or public, so the
  whole claim is reproducible by anyone from this repository. It is
  also the claim the app actually makes on its face.

  THE LINE -- when our number disagreed with the market, were we on
  the right side? This is the claim a reader cares about more, and it
  is the one that needs somebody else's data.

  THE MONEY -- would following us have been profitable? A hit rate is
  not an answer to that: 55% at -140 loses and 48% at +130 wins. So
  every settled leg is also weighted by the price on the side we took,
  flat stakes, and reported in units and ROI. engine/pricing.py has
  the arithmetic and the licence reasoning.

The money figure is reported twice -- across every leg, and across
only the legs engine/disagreement.py would have put on a list. Those
are different products. "Bet all 300 props in tonight's feed" is
nobody's plan; "here are the four we think are wrong" is the thing
being sold, and it is the second number that says whether it works.

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

Money needs one more rule, because a per-leg profit IS a price: 0.909
units won says -110 out loud. So per-leg records carry only a boolean
-- was this leg priced -- and the money itself appears once, as a
night-level aggregate, and only when it covers enough legs that no
single price can be read back out of it. Below that it is withheld and
the record says so, with its leg count, so a season total can report
what it is missing rather than quietly averaging over a hole.

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
from engine.disagreement import for_leg, prior_games           # noqa: E402
from engine.odds_snapshot import read as read_snapshot         # noqa: E402
from engine.line_input import interpret as interpret_line      # noqa: E402
from engine import pricing                                     # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULT_DIR = os.path.join(REPO_ROOT, "results")

# The snapshot parser lives in engine/odds_snapshot.py, shared with
# tools/capture_projections.py. Two readers of the same file that
# disagreed would project one set of players and score another, and
# nothing in either output would show the gap.
legs_from_snapshot = read_snapshot


def print_snapshot_report(report):
    """Say what did not become a leg, and separate the reasons.

    "We do not score combined totals" and "our parser broke on a stat
    we have never seen" produce the same missing leg and need opposite
    responses, so a single dropped-count would be worse than useless.
    Only the first is expected; the rest want a person.
    """
    if not report:
        return
    quiet = {
        "not_scored": "markets we do not score",
        # Not discarded: engine/odds_snapshot.game_context() reads the
        # spreads and the game total, which are the only advance signal
        # we have for game script. They are simply not player props.
        "skipped_bet_types": "game markets, read separately",
        "skipped_periods": "not game-long",
        "team_markets": "team markets, not player props",
    }
    for key, label in quiet.items():
        counts = report.get(key)
        if counts:
            total = sum(counts.values())
            print(f"  {total} {label} ({', '.join(sorted(k for k in counts if k))})")

    for stat, count in (report.get("unknown_stats") or {}).items():
        print(f"  UNRECOGNISED stat '{stat}' x{count} -- a captured bet we "
              f"failed to score; add it to STAT_BY_ID")
    for name, count in (report.get("unresolved_players") or {}).items():
        print(f"  UNRESOLVED player '{name}' x{count} -- no NBA id matched")
    for name, note in (report.get("ambiguous_players") or {}).items():
        print(f"  AMBIGUOUS player '{name}': {note}")


def money_lines(block, indent="  "):
    """What the money block says, as lines a person can read.

    Empty when there is nothing to say, so a coverage-only night does
    not print two blank headings about units it never had.
    """
    out = []
    for key, label in (("strong", "on the legs we'd have listed"),
                       ("all", "on every leg in the feed")):
        tallied = (block or {}).get(key) or {}
        if tallied.get("withheld"):
            if tallied.get("legs"):
                out.append(f"{indent}{label}: {tallied['legs']} priced leg(s), "
                           f"units withheld ({tallied['withheld']})")
            continue
        sentence = pricing.summary_sentence(tallied)
        if sentence:
            out.append(f"{indent}{label}: {sentence}")
    return out


def digest_of(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def score(projections, legs, game_date, read=None):
    """Score one night. Pure apart from the cache read, so the tests
    can hand it a reader and a game log and check the arithmetic.

    The reader is resolved here rather than bound as a default, so that
    patching this module's read_payload reaches every caller -- a
    default argument would capture the original at import and quietly
    ignore the patch."""
    read = read or read_payload
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

                    # Was this one of the legs the app would actually
                    # have put in front of somebody? "Right 55% of the
                    # time across every line in the feed" and "right
                    # 55% on the handful we flagged" are different
                    # claims, and only the second is the product.
                    claim_for_gap = dict(claim)
                    claim_for_gap.setdefault(
                        "n_prior_games", prior_games(projection))
                    entry["strong"] = for_leg(leg, claim_for_gap) is not None

                    # Whether the side we took had a price, not what it
                    # was. The flag makes the denominator of the money
                    # figures auditable without republishing a cent of
                    # somebody else's data.
                    entry["priced"] = (took is not None and
                                       took in (leg.get("prices") or {}))

                    # Counted on settled legs only, so every total in
                    # this record has the same denominator rule: a push
                    # is not a leg we got right or wrong, here or
                    # anywhere else in the file.
                    if entry["side_correct"] is not None:
                        if entry["strong"]:
                            totals["strong_legs"] += 1
                            totals["strong_correct"] += 1 if entry["side_correct"] else 0
                        if entry["priced"]:
                            totals["priced_legs"] += 1
                            if entry["strong"]:
                                totals["strong_priced_legs"] += 1
            scored[stat] = entry

        players[player_id] = {"status": "scored", "stats": scored}
        totals["scored_players"] += 1

    return players, dict(totals)


def money(players, legs):
    """What the night's legs paid, in units, at one unit a leg.

    Reads the already-scored players dict, so the accuracy figures and
    the money figures cannot come from two different passes and quietly
    disagree about which legs counted.

    Returns {"all": tally, "strong": tally} -- the second restricted to
    the legs engine/disagreement.py would have put on a list. The gap
    between those two numbers is the whole question of whether picking
    the props matters, which is what he said the customer is actually
    paying for: they expect to be in profit, not to win every night.

    NOTHING HERE IS PUBLISHABLE UNTIL publishable_money() HAS SEEN IT.
    See engine/pricing.py's licence note: a thin aggregate is a price.
    """
    by_key = {(leg["player_id"], leg["stat"]): leg for leg in legs or []}
    everything, strong = [], []
    for player_id, player in (players or {}).items():
        for stat, entry in (player.get("stats") or {}).items():
            correct = entry.get("side_correct")
            if correct is None:
                continue
            leg = by_key.get((player_id, stat))
            if leg is None:
                continue
            price = (leg.get("prices") or {}).get(entry.get("side_taken"))
            units = pricing.settle(price, correct)
            if units is None:
                continue
            # The bar as well as the result. A strike rate published
            # without the break-even it had to clear is not an answer,
            # and this feed's props are not -110: the one real capture
            # puts the typical bar at 53.3%.
            row = (units, pricing.break_even(price))
            everything.append(row)
            if entry.get("strong"):
                strong.append(row)
    return {"all": pricing.tally([u for u, _b in everything],
                                 [b for _u, b in everything]),
            "strong": pricing.tally([u for u, _b in strong],
                                    [b for _u, b in strong])}


def publishable_money(tallies):
    """The money block as it may be written to a public file.

    A tally that clears the disclosure threshold travels whole. One
    that does not keeps its leg count -- so a season total can say how
    much it is missing -- and loses every figure derived from a price.
    """
    out = {"basis": "one unit a leg, flat, at the book price on the side taken"}
    for key, tallied in (tallies or {}).items():
        if pricing.publishable(tallied):
            out[key] = tallied
        else:
            out[key] = {"legs": tallied.get("legs", 0), "withheld": (
                f"fewer than {pricing.MIN_PRICED_LEGS_TO_PUBLISH} priced "
                f"legs; the aggregate would republish the prices")}
    return out


def build_record(projections, projections_path, legs, snapshot_path,
                 game_date, scored_at, read=None):
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
        "money": publishable_money(money(players, legs)),
        # Did this night settle anything? A night where every player
        # voided is not evidence and must never advance a counter that
        # claims to measure how much evidence there is.
        #
        # Preseason is the case that forces this. The NBA plays
        # exhibition games from 3 to 16 October, engine/game_log.py
        # fetches "Regular Season" and "Playoffs" only, and so not one
        # preseason box score reaches the cache: every player voids,
        # the record is written, and it is permanent. Ten of those
        # would have taken engine/forward_record.py halfway to its
        # twenty-night gate on nothing.
        #
        # A failed morning refresh produces the identical record for a
        # completely different reason, which is why the flag says what
        # is true of the file rather than naming preseason.
        "evidence": bool(totals.get("scored") or totals.get("legs")),
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


PROJECTION_DIR = os.path.join(REPO_ROOT, "projections")
SNAPSHOT_DIR = os.path.join(REPO_ROOT, "line_snapshots")

# How long after the games before a night is worth scoring. The cache
# only learns a box score when the morning refresh fetches it, so
# scoring too early produces a night full of "void" players who in fact
# played -- and a void is permanent once written.
SETTLE_AFTER_HOURS = 14


def _captured_at(path):
    try:
        with open(path) as handle:
            stamp = json.load(handle).get("captured_at")
        when = datetime.fromisoformat(stamp)
        return when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    except (OSError, ValueError, TypeError):
        return None


def snapshot_for(projection_captured_at, snapshot_dir=None):
    """The line snapshot that best represents what the market said.

    The LAST one taken on the same evening, not the nearest: the
    capture job samples across the slate precisely because the latest
    line before a tip is the only one worth calling a close. Anything
    more than eight hours after the projection belongs to another
    night.
    """
    if projection_captured_at is None:
        return None
    root = snapshot_dir or SNAPSHOT_DIR
    best, best_when = None, None
    for folder, _dirs, files in os.walk(root):
        for name in files:
            if not name.endswith(".json"):
                continue
            path = os.path.join(folder, name)
            when = _captured_at(path)
            if when is None:
                continue
            delta = (when - projection_captured_at).total_seconds()
            if not -3600 <= delta <= 8 * 3600:
                continue
            if best_when is None or when > best_when:
                best, best_when = path, when
    return best


def unscored(now=None, projection_dir=None, result_dir=None):
    """Nights we have a claim for and no verdict on.

    Skips anything too recent to settle honestly -- see
    SETTLE_AFTER_HOURS -- because a night scored before the cache has
    the box scores records every player as void, and that record is
    what gets published.
    """
    now = now or datetime.now(timezone.utc)
    projection_dir = projection_dir or PROJECTION_DIR
    result_dir = result_dir or RESULT_DIR
    if not os.path.isdir(projection_dir):
        return []

    done = set()
    if os.path.isdir(result_dir):
        done = {name[:-len(".json")] for name in os.listdir(result_dir)
                if name.endswith(".json")}

    out = []
    for name in sorted(os.listdir(projection_dir)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(projection_dir, name)
        when = _captured_at(path)
        if when is None:
            continue
        if (now - when).total_seconds() < SETTLE_AFTER_HOURS * 3600:
            continue
        with open(path) as handle:
            body = json.load(handle)
        game_date = game_date_for(body)
        if not game_date or game_date in done:
            continue
        out.append((path, body, game_date))
    return out


def score_unscored(now=None):
    """Score every night that is ready. Written for the refresh job to
    call once a morning, after it has updated the cache -- which is the
    only moment the box scores are certainly there."""
    pending = unscored(now)
    if not pending:
        print("nothing new to score")
        return 0

    for path, body, game_date in pending:
        snapshot = snapshot_for(_captured_at(path))
        legs = []
        if snapshot:
            legs, report = legs_from_snapshot(snapshot)
            print(f"{game_date}: {len(legs)} leg(s) from "
                  f"{os.path.basename(snapshot)}")
            print_snapshot_report(report)
        else:
            # Coverage is still scoreable, and saying so matters: a
            # night with no snapshot is a night with no market claim,
            # not a night with no evidence.
            print(f"{game_date}: no line snapshot -- coverage only")

        record = build_record(body, path, legs, snapshot, game_date,
                              now or datetime.now(timezone.utc))
        result_path, digest = write_record(record, game_date)
        totals = record["totals"]
        if not record["evidence"]:
            # Said loudly, because the two causes want opposite
            # responses: an exhibition night is fine and expected, a
            # regular-season night like this means the cache never got
            # the box scores and somebody should look.
            print(f"{game_date}: NOTHING SETTLED — all "
                  f"{totals.get('void_players', 0)} player(s) void. "
                  f"Expected for a preseason date; on a regular-season "
                  f"night it means the cache has no box scores yet.")
        line = (f"  {totals.get('scored', 0)} claim(s) scored, "
                f"{totals.get('covered', 0)} inside the range, "
                f"{totals.get('void_players', 0)} player(s) void")
        if totals.get("legs"):
            line += (f"; {totals['legs_correct']}/{totals['legs']} right "
                     f"against the line")
        if totals.get("strong_legs"):
            line += (f", {totals['strong_correct']}/{totals['strong_legs']} "
                     f"on the strong ones")
        print(line)
        for extra in money_lines(record.get("money")):
            print(extra)
        print(f"  {os.path.relpath(result_path, REPO_ROOT)}  "
              f"sha256 {digest[:16]}...")
    return 0


def summarise():
    if not os.path.isdir(RESULT_DIR):
        print("nothing scored yet")
        return 0
    files = sorted(f for f in os.listdir(RESULT_DIR) if f.endswith(".json"))
    if not files:
        print("nothing scored yet")
        return 0
    run = Counter()
    # Money is summed rather than re-derived: the records are what was
    # published, and a season figure that disagreed with the nights it
    # is made of would be the worse number to trust.
    purse = {"all": Counter(), "strong": Counter()}
    for name in files:
        try:
            with open(os.path.join(RESULT_DIR, name)) as handle:
                body = json.load(handle)
            totals = body.get("totals") or {}
        except (OSError, ValueError):
            print(f"  {name}  unreadable")
            continue
        run.update(totals)
        for key, pot in purse.items():
            tallied = (body.get("money") or {}).get(key) or {}
            if tallied.get("withheld"):
                pot["withheld_legs"] += tallied.get("legs", 0)
                pot["withheld_nights"] += 1
            elif tallied.get("legs"):
                pot["legs"] += tallied["legs"]
                pot["staked"] += tallied.get("staked", 0)
                pot["profit"] += tallied.get("profit", 0)
                if tallied.get("break_even") is not None:
                    # Weighted by legs, so a twelve-leg Tuesday does not
                    # count the same as a ninety-leg Saturday.
                    pot["bar_weighted"] += tallied["break_even"] * tallied["legs"]
                    pot["bar_legs"] += tallied["legs"]
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
    if run["strong_legs"]:
        print(f"  on the legs we'd have listed: {run['strong_correct']}/"
              f"{run['strong_legs']} = "
              f"{100.0 * run['strong_correct'] / run['strong_legs']:.1f}%")
    if run["legs_push"]:
        print(f"  {run['legs_push']} leg(s) landed on the line, not counted")

    for key, label in (("strong", "the legs we'd have listed"),
                       ("all", "every leg in the feed")):
        pot = purse[key]
        if pot["legs"]:
            hit = None
            if key == "strong" and run["strong_legs"]:
                hit = run["strong_correct"] / run["strong_legs"]
            elif key == "all" and run["legs"]:
                hit = run["legs_correct"] / run["legs"]
            sentence = pricing.summary_sentence(
                {"legs": pot["legs"], "staked": pot["staked"],
                 "profit": pot["profit"],
                 "roi": (100.0 * pot["profit"] / pot["staked"]
                         if pot["staked"] else None),
                 "break_even": (pot["bar_weighted"] / pot["bar_legs"]
                                if pot["bar_legs"] else None)},
                hit_rate=hit)
            print(f"  money, {label}: {sentence}")
        if pot["withheld_legs"]:
            # Said out loud, every time. A profit figure that silently
            # skipped a third of the season is the kind of number this
            # whole apparatus exists not to publish.
            print(f"    plus {pot['withheld_legs']} priced leg(s) over "
                  f"{pot['withheld_nights']} night(s) withheld, not counted "
                  f"above")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projections", help="a projections/*.json record")
    parser.add_argument("--snapshot", default=None,
                        help="the line snapshot captured alongside it (optional)")
    parser.add_argument("--date", default=None,
                        help="the US game date; inferred from the capture time if omitted")
    parser.add_argument("--summarise", action="store_true")
    parser.add_argument("--catch-up", action="store_true",
                        help="score every night that has a projection, no "
                             "result yet, and has had time to settle")
    parser.add_argument("--dry-run", action="store_true",
                        help="score and report, write nothing")
    args = parser.parse_args(argv)

    if args.summarise:
        return summarise()

    if args.catch_up:
        return score_unscored()

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

    legs = []
    if args.snapshot:
        legs, report = legs_from_snapshot(args.snapshot)
        print(f"{len(legs)} leg(s) read from the snapshot")
        print_snapshot_report(report)

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
    if totals.get("strong_legs"):
        line += (f", {totals['strong_correct']}/{totals['strong_legs']} "
                 f"on the strong ones")
    extras = money_lines(body.get("money"))

    if args.dry_run:
        print(line + "  -- dry run, nothing written")
        for extra in extras:
            print(extra)
        return 0

    path, digest = write_record(body, game_date)
    print(line)
    for extra in extras:
        print(extra)
    print(f"  {os.path.relpath(path, REPO_ROOT)}  sha256 {digest[:16]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
