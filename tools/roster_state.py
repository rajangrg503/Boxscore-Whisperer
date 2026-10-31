#!/usr/bin/env python3
"""Split a team's season by WHO ELSE WAS PLAYING, not by date.

    python3 tools/roster_state.py OKC --unavailable slate_lines/availability_20261031.json
    python3 tools/roster_state.py OKC --unavailable ... --min-mpg 15

WHY THIS EXISTS
On 31 Oct 2026 rank_lines.py said Ajay Mitchell UNDER 18.5 points was
the biggest edge on the board. Karma said the opposite: "that 29,30 is
the new way of thunder after injury."

He was right, and the reason is that a season is not one sample. His
five games were two different teams:

    20 Oct   4 pts, PRA 15   Holmgren, Williams, Caruso, Wallace all played
    22 Oct  12 pts, PRA 12   same four played
    25 Oct  18 pts, PRA 28   same four played
    26 Oct  30 pts, PRA 37   only Caruso of the four
    28 Oct  29 pts, PRA 36   none of the four

Tonight all five are out. So two of his five games describe tonight and
three describe a team that will not be on the floor, and averaging them
produces a number that matches neither.

rank_lines.py blends LAST season against THIS season for exactly this
reason -- a role that did not carry over -- and then treats this season
as a single block, which is the same mistake one level down.

HOW THE MATCH IS MADE
A player appears in a game's log only if he played, so his absence is
readable straight from the cache without an injury history. For each
past game this asks which of tonight's absent players were also absent
then. "Matching" means ALL of them were out; "partial" means some were.
No API, no injury feed, no network -- just the game logs already here.

WHAT IT CANNOT DO
It cannot tell a rested star from an injured one, it cannot see minutes
restrictions, and with five games played a matching sample is often one
or two games. Two games is not evidence of a rate; it is evidence that
the question is worth asking. Read it beside the full-season numbers,
which are printed next to it, and never instead of them.
"""
import argparse
import json
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.cache import read_payload                      # noqa: E402
from engine.team_ids import TEAM_ID_BY_ABBR                # noqa: E402


def nf(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def team_games(abbr, season, out_ids):
    """{game_date: set(player_ids who played)} for one team."""
    games = {}
    for row in ((read_payload(f"roster_{TEAM_ID_BY_ABBR[abbr]}") or {}).get("data") or []):
        pid = int(row["PLAYER_ID"])
        for g in ((read_payload(f"gamelog_{pid}_{season}") or {}).get("data") or []):
            games.setdefault(g.get("GAME_DATE"), set()).add(pid)
    return games


def classify(games, out_ids, weights):
    """game_date -> 'match' | 'partial' | 'full', by tonight's absences.

    Counting heads is too blunt: on 26 Oct Oklahoma City were missing
    Holmgren, Jalen Williams, Wallace and Sorber and had only Caruso,
    which is all but identical to tonight -- yet a strict all-or-nothing
    test files it beside a full-strength game. So each absentee counts
    for the minutes he plays, and a game matches when at least 80% of
    tonight's missing minutes were missing then too.
    """
    total = sum(weights.get(p, 0.0) for p in out_ids) or 1.0
    verdict = {}
    for date, played in games.items():
        gone = sum(weights.get(p, 0.0) for p in out_ids if p not in played)
        share = gone / total
        verdict[date] = "match" if share >= 0.80 else ("partial" if share >= 0.30 else "full")
    return verdict


def mpg_of(pid, season):
    log = (read_payload(f"gamelog_{pid}_{season}") or {}).get("data") or []
    if not log:
        return 0.0
    return sum(nf(g.get("MIN")) for g in log) / len(log)


def line(label, rows):
    if not rows:
        return f"      {label:9s}  --"
    f = lambda k: st.mean([nf(r.get(k)) for r in rows])      # noqa: E731
    pra = st.mean([nf(r.get("PTS")) + nf(r.get("REB")) + nf(r.get("AST")) for r in rows])
    return (f"      {label:9s} n={len(rows)}  {f('MIN'):4.1f}min  "
            f"{f('PTS'):5.1f}pts {f('REB'):4.1f}reb {f('AST'):4.1f}ast  "
            f"PRA {pra:5.1f}")


def main(argv=None):
    from engine.season import CURRENT_SEASON

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("team", help="team abbreviation, e.g. OKC")
    ap.add_argument("--unavailable", required=True,
                    help="JSON from tools/availability.py")
    ap.add_argument("--season", default=CURRENT_SEASON)
    ap.add_argument("--min-mpg", type=float, default=12.0)
    args = ap.parse_args(argv)

    feed = json.load(open(args.unavailable))
    out_ids, out_names = set(), []
    for game in feed.get("games", []):
        for row in game.get("out", []):
            if row.get("team") == args.team and row.get("player_id"):
                out_ids.add(int(row["player_id"]))
                out_names.append(row.get("nba_name") or row.get("name"))
    if not out_ids:
        print(f"{args.team}: nobody ruled out tonight -- the whole season matches.")
        return 0

    print(f"{args.team} out tonight: {', '.join(sorted(out_names))}\n")

    games = team_games(args.team, args.season, out_ids)
    weights = {p: mpg_of(p, args.season) for p in out_ids}
    missing = [p for p, m in weights.items() if m == 0.0]
    if missing:
        print(f"  note: {len(missing)} of tonight's absentees have no games "
              f"this season, so they carry no weight in the match.\n")
    verdict = classify(games, out_ids, weights)
    counts = {v: sum(1 for x in verdict.values() if x == v) for v in ("match", "partial", "full")}
    print(f"team games this season: {len(verdict)}  "
          f"(matching tonight: {counts['match']}, partial: {counts['partial']}, "
          f"full strength: {counts['full']})")
    for date in sorted(verdict, key=lambda d: verdict[d]):
        print(f"    {date:>14s}  {verdict[date]}")
    print()
    if not counts["match"]:
        print("  NO past game matches tonight's absences. Every number below is\n"
              "  from a different team than the one that will play.\n")

    for row in ((read_payload(f"roster_{TEAM_ID_BY_ABBR[args.team]}") or {}).get("data") or []):
        pid = int(row["PLAYER_ID"])
        if pid in out_ids:
            continue
        log = (read_payload(f"gamelog_{pid}_{args.season}") or {}).get("data") or []
        if not log or st.mean([nf(g.get("MIN")) for g in log]) < args.min_mpg:
            continue
        buckets = {"match": [], "partial": [], "full": []}
        for g in log:
            buckets[verdict.get(g.get("GAME_DATE"), "full")].append(g)
        print(f"   {row['PLAYER']}")
        print(line("season", log))
        for key, label in (("match", "TONIGHT"), ("partial", "partial"), ("full", "full str")):
            if buckets[key]:
                print(line(label, buckets[key]))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
