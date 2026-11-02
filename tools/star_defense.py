#!/usr/bin/env python3
"""Which defences take the other team's star away -- and what they give up.

    python3 tools/star_defense.py
    python3 tools/star_defense.py --season 2026-27 --min-games 3

WHY THIS EXISTS
Karma, 2 Nov 2026: "there are teams who take away certain advantage
from opponent. OKC used to take the main guys out of the equation with
their point-of-attack defence -- Booker, Donovan Mitchell -- and trade
that off for open corner threes."

For every game, the attacking team's STAR is its highest-usage player
that season (shots + 0.44 free throws + turnovers + assists per game,
10+ games). Against each defence this compares:

  star     the star's points against his own per-minute norm, at the
           minutes he played (20+), so a blowout does not read as defence
  3PA/3PM  everyone else's threes against their own norms -- the shots a
           defence concedes when it loads up on the star

Game logs have no shot locations, so "corner" threes are not separable;
this is all threes by the non-stars. A split-half check (games in date
order, odd vs even) says whether a defence's number is a trait or noise.
"""
import argparse, collections, datetime, os, statistics as st, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import opponent_absence as O                                # noqa: E402


def f(r, k):
    return float(r.get(k) or 0)


def run(seasons, min_games, split=None):
    games = O.load(seasons)
    base = collections.defaultdict(lambda: collections.defaultdict(float))
    for (season, gid), teams in games.items():
        for team, rows in teams.items():
            for pid, r in rows.items():
                b = base[(season, team, pid)]
                for k in ("MIN", "PTS", "FG3A", "FG3M"):
                    b[k] += f(r, k)
                b["U"] += O.usage(r)
                b["G"] += 1
    star = {}
    for (season, team, pid), b in base.items():
        if b["G"] >= 10 or (b["G"] >= 3 and season == seasons[-1] and len(seasons) == 1):
            u = b["U"] / b["G"]
            if (season, team) not in star or u > star[(season, team)][1]:
                star[(season, team)] = (pid, u)
    acc = collections.defaultdict(lambda: collections.defaultdict(float))
    ordered = sorted(games.items(), key=lambda kv: kv[0][1])
    for idx, ((season, gid), teams) in enumerate(ordered):
        if len(teams) != 2 or (split is not None and idx % 2 != split):
            continue
        (a, ra), (b_, rb) = teams.items()
        for off, rows, deff in ((a, ra, b_), (b_, rb, a)):
            A = acc[(season, deff)]
            sp = star.get((season, off), (None,))[0]
            if sp in rows and f(rows[sp], "MIN") >= 20:
                bb = base[(season, off, sp)]
                A["pts"] += f(rows[sp], "PTS")
                A["pts_x"] += bb["PTS"] / bb["MIN"] * f(rows[sp], "MIN")
                A["n"] += 1
            for pid, r in rows.items():
                bb = base.get((season, off, pid))
                if pid == sp or not bb or bb["G"] < 3 or not bb["MIN"]:
                    continue
                m = f(r, "MIN")
                A["3pa"] += f(r, "FG3A"); A["3pa_x"] += bb["FG3A"] / bb["MIN"] * m
                A["3pm"] += f(r, "FG3M"); A["3pm_x"] += bb["FG3M"] / bb["MIN"] * m
    out = {}
    for k, A in acc.items():
        if A["n"] >= min_games and A["pts_x"] and A["3pa_x"]:
            out[k] = {"games": int(A["n"]), "star": A["pts"] / A["pts_x"] - 1,
                      "3pa": A["3pa"] / A["3pa_x"] - 1, "3pm": A["3pm"] / A["3pm_x"] - 1}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", default="2025-26")
    ap.add_argument("--min-games", type=int, default=5)
    args = ap.parse_args(argv)
    res = run([args.season], args.min_games)
    print(f"{args.season}: opposing star's points vs his norm | everyone else's 3PA | 3PM")
    for (season, team), v in sorted(res.items(), key=lambda kv: kv[1]["star"]):
        print(f"  {team}  star {v['star']:+5.0%}   3PA {v['3pa']:+4.0%}   3PM {v['3pm']:+4.0%}   ({v['games']} g)")
    if args.season != "2026-27" or args.min_games >= 5:
        odd, even = run([args.season], 3, 0), run([args.season], 3, 1)
        both = [k for k in odd if k in even]
        if len(both) > 5:
            xs = [odd[k]["star"] for k in both]; ys = [even[k]["star"] for k in both]
            mx, my = st.mean(xs), st.mean(ys)
            r = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (
                (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5)
            print(f"\nsplit-half: star suppression in odd games vs even games, r = {r:+.2f} "
                  f"across {len(both)} defences (near 0 = noise, toward 1 = a trait)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
