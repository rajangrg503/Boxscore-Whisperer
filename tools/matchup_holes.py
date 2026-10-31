#!/usr/bin/env python3
"""Who does this matchup advantage? Search the defence, not the player.

    python3 tools/matchup_holes.py OKC DAL
    python3 tools/matchup_holes.py --slate CHA WAS ORL TOR NYK PHI
    python3 tools/matchup_holes.py --team OKC --detail

WHY THIS EXISTS
Every other tool in this project iterates over PLAYERS and checks each
one against a line. That can only find a mismatch by accident, and only
once the mismatch has already shown up in a box score.

Karma's framing, 31 Oct 2026, is the other way round:

    "which player has the most advantage in this game. For example: okc
     vs dallas, players like pj washington has advantage because okc
     doesnt have big wing defender. Only guards and big."

That is a fact about a roster, knowable at tip-off, and it was true:

    OKC, everyone 6-5 and up
        Aday Mara           C    7-3  255
        Chet Holmgren       C-F  7-1  208
        Isaiah Hartenstein  C-F  7-0  250
        Thomas Sorber       C    6-9  250
        Jaylin Williams     F    6-9  240
        ---- cliff ----
        Kenrich Williams    G-F  6-7  210
        Micah Peavy         G-F  6-6  215   (2nd year)
        Shai Gilgeous-A.    G    6-6  195   (the offence; not a stopper)
        Nikola Topic        G    6-6  200
        ---- then 6-5 and below, all guards ----

    P.J. Washington (DAL)   F    6-6  230

Heavier than every wing OKC has. And worse than the roster suggests:
Dort went to Atlanta on 19 Jul 2026 (his record still carries the
HOW_ACQUIRED note) and Jalen Williams is out for the season. Both of
their wing stoppers, gone.

MINUTES, NOT BODIES
A 15-man roster is not a rotation. Counting Aday Mara as a big inflates
OKC's interior coverage when he barely plays. So coverage is weighted by
MINUTES ACTUALLY PLAYED this season, not by headcount. A team's coverage
in an archetype is that archetype's share of the team's total minutes.

WHAT THIS DOES NOT KNOW
1. Role. Height and weight get roughly 80% of the archetype. They put
   SGA (6-6, 195) in the wing bucket on the first pass and Jaylin
   Williams (6-9, 240) there too, when he plays the 5. The weight floor
   fixes most of it; the rest needs minutes-by-position, which is not
   cached. So this PRINTS THE NAMES in every bucket: the number is a
   prompt for a human to check, not a verdict.
2. Who is out TONIGHT. Minutes-weighting handles a player who has been
   absent for weeks; it does not handle one ruled out this afternoon.
   tools/availability.py is the gate that does, and it must run first.
3. Defensive quality within an archetype. A team with three poor wings
   scores the same as a team with three good ones. EPM's DEF component
   is the obvious fix and is not wired in yet.

So this narrows a slate to a shortlist. It does not pick anything.
"""
import argparse
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.cache import read_payload                          # noqa: E402
from engine.season import CURRENT_SEASON                       # noqa: E402
from engine.team_ids import TEAM_ID_BY_ABBR                    # noqa: E402

# Archetype thresholds, in inches and pounds. Documented rather than
# tuned: nothing here has been fitted to an outcome, and the moment one
# of them is, it belongs in a sweep with a measurement beside it.
from engine.archetype import (GUARD_MAX_IN, WING_MAX_IN, WING_MIN_LB,  # noqa: E402,F401
                              BIG_MIN_LB, ARCHETYPES, inches, pounds, archetype)


def minutes_played(player_id, season):
    """Total minutes this season, and games. Zero when nothing is cached,
    which is correct: a player with no logged minutes covers nobody."""
    payload = read_payload(f"gamelog_{player_id}_{season}")
    rows = (payload or {}).get("data") or []
    total = 0.0
    for row in rows:
        try:
            total += float(row.get("MIN") or 0)
        except (TypeError, ValueError):
            continue
    return total, len(rows)


def team_profile(abbr, season, exclude=()):
    """Minutes-weighted archetype coverage for one team.

    `exclude` is a set of NBA PLAYER_IDs who are not available tonight,
    from tools/availability.py. Without it this counts a season-ending
    injury as live coverage: on 31 Oct 2026 it credited Oklahoma City
    with Jalen Williams at 31.7 mpg while he was out for the year.
    """
    team_id = TEAM_ID_BY_ABBR.get(abbr)
    payload = read_payload(f"roster_{team_id}") if team_id else None
    if payload is None:
        return None

    players, sat = [], []
    for row in (payload.get("data") or []):
        kind = archetype(row.get("POSITION"), row.get("HEIGHT"), row.get("WEIGHT"))
        if kind is None:
            continue
        if int(row["PLAYER_ID"]) in exclude:
            sat.append(row["PLAYER"])
            continue
        mins, games = minutes_played(int(row["PLAYER_ID"]), season)
        players.append({
            "name": row["PLAYER"],
            "id": int(row["PLAYER_ID"]),
            "pos": row.get("POSITION"),
            "height": row.get("HEIGHT"),
            "weight": pounds(row.get("WEIGHT")),
            "archetype": kind,
            "minutes": mins,
            "games": games,
            "mpg": round(mins / games, 1) if games else 0.0,
        })

    total = sum(p["minutes"] for p in players) or 1.0
    share = defaultdict(float)
    for p in players:
        share[p["archetype"]] += p["minutes"]
    return {
        "abbr": abbr,
        "players": sorted(players, key=lambda p: -p["minutes"]),
        "share": {k: round(100.0 * share[k] / total, 1) for k in ARCHETYPES},
        "total_minutes": total,
        "excluded": sat,
    }


def league_baseline(season, teams=None, exclude=()):
    """League median share per archetype, so 'a hole' means something
    relative rather than a number somebody picked.

    The baseline is built WITHOUT tonight's exclusions by default: the
    median should describe how teams are normally built, not how two of
    them happen to be shorthanded this evening.
    """
    shares = defaultdict(list)
    for abbr in (teams or TEAM_ID_BY_ABBR):
        prof = team_profile(abbr, season, exclude)
        if not prof or prof["total_minutes"] < 1:
            continue
        for kind in ARCHETYPES:
            shares[kind].append(prof["share"][kind])
    median = {}
    for kind in ARCHETYPES:
        vals = sorted(shares[kind])
        median[kind] = vals[len(vals) // 2] if vals else 0.0
    return median


def holes(profile, baseline, gap=4.0):
    """Archetypes where this team's coverage trails the league median by
    more than `gap` percentage points of its own minutes."""
    out = []
    for kind in ARCHETYPES:
        deficit = baseline[kind] - profile["share"][kind]
        if deficit >= gap:
            out.append((kind, round(deficit, 1)))
    return sorted(out, key=lambda t: -t[1])


def exploiters(profile, kind, min_mpg=12.0):
    """Players on this team of the given archetype who actually play."""
    return [p for p in profile["players"]
            if p["archetype"] == kind and p["mpg"] >= min_mpg]


def describe(profile, baseline, detail=False):
    lines = [f"{profile['abbr']}  coverage by minutes:"]
    if profile.get("excluded"):
        lines.append(f"    (out tonight, not counted: "
                     f"{', '.join(profile['excluded'])})")
    for kind in ARCHETYPES:
        mark = ""
        deficit = baseline[kind] - profile["share"][kind]
        if deficit >= 4.0:
            mark = f"   <-- {deficit:.1f}pt below league median"
        lines.append(f"    {kind:6s} {profile['share'][kind]:5.1f}%"
                     f"  (league {baseline[kind]:.1f}%){mark}")
    if detail:
        lines.append("    rotation:")
        for p in profile["players"]:
            if p["mpg"] < 6:
                continue
            lines.append(f"      {p['name']:26s} {str(p['pos']):4s} "
                         f"{str(p['height']):5s} {p['weight']:>4d}lb  "
                         f"{p['archetype']:6s} {p['mpg']:5.1f} mpg")
    return "\n".join(lines)


def matchup(home, away, season, baseline, min_mpg=12.0, exclude=()):
    """Both sides of one game: each team's holes, and who opposite can
    stand in them."""
    a = team_profile(away, season, exclude)
    b = team_profile(home, season, exclude)
    if a is None or b is None:
        return None
    report = {"away": a, "home": b, "edges": []}
    for defence, offence in ((a, b), (b, a)):
        for kind, deficit in holes(defence, baseline):
            names = exploiters(offence, kind, min_mpg)
            if not names:
                continue
            report["edges"].append({
                "defence": defence["abbr"],
                "offence": offence["abbr"],
                "archetype": kind,
                "deficit": deficit,
                "players": names,
            })
    report["edges"].sort(key=lambda e: -e["deficit"])
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("teams", nargs="*",
                        help="team abbreviations in pairs: AWAY HOME AWAY HOME ...")
    parser.add_argument("--team", help="profile one team and stop")
    parser.add_argument("--season", default=CURRENT_SEASON)
    parser.add_argument("--detail", action="store_true", help="print rotations")
    parser.add_argument("--min-mpg", type=float, default=12.0,
                        help="a player below this does not count as an exploiter")
    parser.add_argument("--unavailable", default=None,
                        help="JSON from tools/availability.py --out; players "
                             "listed OUT there are removed from coverage")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    # The gate. Without it this credits a team with a man who is not
    # playing -- the Jalen Williams bug, 31 Oct 2026.
    exclude = set()
    if args.unavailable:
        with open(args.unavailable) as handle:
            feed = json.load(handle)
        for game in feed.get("games", []):
            for row in game.get("out", game.get("injuries", [])):
                if row.get("severity") == "out" and row.get("player_id"):
                    exclude.add(int(row["player_id"]))
        print(f"availability gate: {len(exclude)} player(s) ruled out "
              f"({os.path.basename(args.unavailable)})")
    else:
        print("availability gate: NOT APPLIED -- coverage may count players "
              "who are out. Run tools/availability.py --out first.")

    unknown = [t for t in (args.teams + ([args.team] if args.team else []))
               if t and t not in TEAM_ID_BY_ABBR]
    if unknown:
        parser.error(f"unknown team abbreviation(s): {', '.join(unknown)}")

    baseline = league_baseline(args.season)

    if args.team:
        prof = team_profile(args.team, args.season, exclude)
        if prof is None:
            print(f"no cached roster for {args.team}")
            return 1
        print(describe(prof, baseline, detail=True))
        return 0

    if len(args.teams) % 2:
        parser.error("teams must come in AWAY HOME pairs")

    results = []
    for i in range(0, len(args.teams), 2):
        away, home = args.teams[i], args.teams[i + 1]
        rep = matchup(home, away, args.season, baseline, args.min_mpg, exclude)
        if rep is None:
            print(f"{away} @ {home}: missing roster")
            continue
        results.append(rep)
        if args.json:
            continue
        print(f"\n=== {away} @ {home} ===")
        print(describe(rep["away"], baseline, args.detail))
        print(describe(rep["home"], baseline, args.detail))
        if not rep["edges"]:
            print("  no structural hole either way")
            continue
        for e in rep["edges"]:
            print(f"\n  {e['offence']} attacks {e['defence']}'s {e['archetype']} "
                  f"coverage ({e['deficit']:.1f}pt below median):")
            for p in e["players"]:
                print(f"      {p['name']:26s} {str(p['height']):5s} "
                      f"{p['weight']:>4d}lb  {p['mpg']:5.1f} mpg")

    if args.json:
        slim = [{
            "away": r["away"]["abbr"], "home": r["home"]["abbr"],
            "edges": [{
                "defence": e["defence"], "offence": e["offence"],
                "archetype": e["archetype"], "deficit": e["deficit"],
                "players": [{"name": p["name"], "id": p["id"],
                             "height": p["height"], "weight": p["weight"],
                             "mpg": p["mpg"]} for p in e["players"]],
            } for e in r["edges"]],
        } for r in results]
        print(json.dumps(slim, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
