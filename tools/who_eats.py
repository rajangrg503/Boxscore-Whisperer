#!/usr/bin/env python3
"""Who eats when the circumstances change?

    python3 tools/who_eats.py
    python3 tools/who_eats.py --boot 1000 --json logs/who_eats.json

WHY THIS EXISTS
Karma, 2 Nov 2026: "we need a rule of who's going to eat based on the
circumstances." The card already reads one circumstance by hand, who is
missing from a player's own team (roster_state.py), and
opponent_absence.py measured the other side. This measures the rest on
the same cached games, so the rule is built from numbers and not hunches:

  OWN   a team's key G / F / C sits: which teammates gain, by position
        group and by role (starter 28+ mpg, rotation 18-28, bench <18)?
        Reported per GAME (minutes x rate), because a prop is a per-game
        number and the gain can come from minutes, from rate, or both.
  B2B   the second night of a back-to-back against games with at least
        one rest day, by role.

Every comparison is a player against himself, pooled with weights from
the games behind the rarer side; the 90% interval resamples players.
Positions come from today's rosters (see opponent_absence.py for the
caveats, which apply here too).
"""
import argparse, collections, datetime, json, os, random, statistics as st, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import opponent_absence as O                                # noqa: E402

STATS = ("MIN", "PTS", "REB", "AST")


def role(mpg):
    return "starter" if mpg >= 28 else ("rotation" if mpg >= 18 else "bench")


def season_mpg(games):
    tot = collections.defaultdict(lambda: [0.0, 0])
    for (season, _), teams in games.items():
        for team, rows in teams.items():
            for pid, r in rows.items():
                t = tot[(season, team, pid)]
                t[0] += float(r["MIN"] or 0)
                t[1] += 1
    return {k: v[0] / v[1] for k, v in tot.items() if v[1] >= 10}


def per_game_ratio(pairs, stat):
    """Pooled ratio of per-game averages, weighted by the rare-side games."""
    num = den = 0.0
    for rare, usual in pairs:
        a = st.mean(float(r.get(stat) or 0) for r in rare)
        b = st.mean(float(r.get(stat) or 0) for r in usual)
        if b <= 0:
            continue
        num += (a / b) * len(rare)
        den += len(rare)
    return num / den if den else None


def summarize(pairs, rng, boot):
    out = {"players": len(pairs), "games": sum(len(a) for a, _ in pairs)}
    for stat in STATS:
        est = per_game_ratio(pairs, stat)
        bs = sorted(b for b in (per_game_ratio([pairs[rng.randrange(len(pairs))] for _ in pairs], stat)
                                for _ in range(boot)) if b)
        out[stat] = (est, bs[int(.05 * len(bs))], bs[int(.95 * len(bs)) - 1]) if bs else (est, None, None)
    return out


def own_absence(games, pos, key, mpg):
    cells = collections.defaultdict(lambda: collections.defaultdict(lambda: {"absent": [], "present": []}))
    for (season, gid), teams in games.items():
        for team, rows in teams.items():
            kmap = key.get((season, team), {})
            for X, kp in kmap.items():
                side = "present" if kp in rows else "absent"
                for pid, r in rows.items():
                    if pid == kp or (season, team, pid) not in mpg or not pos.get(pid):
                        continue
                    cell = (X, pos[pid], role(mpg[(season, team, pid)]))
                    cells[cell][(season, team, pid)][side].append(r)
    return cells


def back_to_back(games, mpg):
    dates = collections.defaultdict(set)
    rows_by = []
    for (season, gid), teams in games.items():
        for team, rows in teams.items():
            any_row = next(iter(rows.values()))
            d = datetime.datetime.strptime(any_row["GAME_DATE"], "%b %d, %Y").date()
            dates[(season, team)].add(d)
            rows_by.append((season, team, d, rows))
    cells = collections.defaultdict(lambda: collections.defaultdict(lambda: {"absent": [], "present": []}))
    for season, team, d, rows in rows_by:
        b2b = (d - datetime.timedelta(days=1)) in dates[(season, team)]
        for pid, r in rows.items():
            k = (season, team, pid)
            if k not in mpg:
                continue
            cells[role(mpg[k])][k]["absent" if b2b else "present"].append(r)
    return cells


def fmt(cell):
    parts = []
    for s in STATS:
        e, lo, hi = cell[s]
        parts.append(f"{s} {e - 1:+.0%}" + (f" [{lo - 1:+.0%},{hi - 1:+.0%}]" if lo else ""))
    return "  ".join(parts)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seasons", nargs="+", default=["2025-26", "2026-27"])
    ap.add_argument("--boot", type=int, default=400)
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)
    rng = random.Random(11)
    pos = O.positions()
    games = O.load(args.seasons)
    key = O.keys(games, pos)
    mpg = season_mpg(games)
    report = {"own": {}, "b2b": {}}

    print("OWN TEAM: key man out -> teammates, per game (rate x minutes)")
    own = own_absence(games, pos, key, mpg)
    for X in "GFC":
        for g in "GFC":
            for r in ("starter", "rotation", "bench"):
                pairs = [(v["absent"], v["present"]) for v in own[(X, g, r)].values()
                         if len(v["absent"]) >= 3 and len(v["present"]) >= 5]
                if len(pairs) < 8:
                    continue
                cell = summarize(pairs, rng, args.boot)
                report["own"][f"{X}->{g} {r}"] = cell
                print(f"  {X} out -> {g} {r:9s} ({cell['players']:3d} pl, {cell['games']:4d} g)  {fmt(cell)}")

    print("\nBACK-TO-BACK second night vs rested, per game")
    b2b = back_to_back(games, mpg)
    for r in ("starter", "rotation", "bench"):
        pairs = [(v["absent"], v["present"]) for v in b2b[r].values()
                 if len(v["absent"]) >= 3 and len(v["present"]) >= 5]
        cell = summarize(pairs, rng, args.boot)
        report["b2b"][r] = cell
        print(f"  {r:9s} ({cell['players']:3d} pl, {cell['games']:4d} g)  {fmt(cell)}")
    if args.json:
        with open(args.json, "w") as f:
            json.dump(report, f, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
