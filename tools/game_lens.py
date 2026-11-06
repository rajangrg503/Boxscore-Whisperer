#!/usr/bin/env python3
"""How does THIS game get decided? A team-vs-team lens.

    python3 tools/game_lens.py POR LAL
    python3 tools/game_lens.py --slate OKC MEM MIN NO

WHY THIS EXISTS
Karma, 6 Nov 2026: "between two teams, who exploits their weakness? what
are the weaknesses? Whose strengths get enhanced? Who's better at winning
the possession battle? ... If one team plays fast, how's the half-court
offense or defense? How much leeway does one team have when they have bad
shooting variance? What does a particular team give up? That's what
decides an NBA game."

matchup_holes / matchup_edge answer this for PLAYERS (body types). This
answers it for TEAMS, from the team box stats daily_journal already writes
to journal/<date>.json (both sides of every game), so it needs no network.

WHAT IT MEASURES (per game, this season, league rank 1 = most)
  made     what a team produces: paint, fast-break, second-chance and
           off-turnover points, free-throw attempts, threes, offensive boards
  allowed  the same things given up to opponents
  poss     possession battle = (turnovers forced - committed)
           + (offensive boards - opponent's) + 0.44 x (FTA - opponent FTA):
           extra scoring chances a game before anyone shoots
  leeway   record in games where the team shot a WORSE FG% than its
           opponent -- how often it survives a cold night
  fast     share of points from fast breaks: who wants a track meet,
           and how the other side defends in transition

A "strength meets weakness" is a team's top-10 "made" stat against an
opponent's top-10 "allowed" stat (allowed rank 1 = gives up the most).
Small samples: a team has ~6-9 recorded games early in the season, so the
lens describes a lean, not a law. Read with the injury report: a team's
numbers were made with the players who played them.
"""
import argparse
import glob
import json
import os
import statistics as st
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALIAS = {"NO": "NOP", "WSH": "WAS", "UTAH": "UTA", "SA": "SAS", "GS": "GSW", "NY": "NYK", "PHO": "PHX", "BRK": "BKN"}
STATS = {   # key: (label, what it means for props when it is a strength vs a weakness)
    "paint": ("paint pts", "drivers' and bigs' points, free throws"),
    "fastbreak": ("fast-break pts", "pace up: guards/wings points, PRA"),
    "second": ("second-chance pts", "bigs' rebounds and putbacks"),
    "off_tov": ("pts off turnovers", "pace up; ball-pressure guards' steals/points"),
    "fta": ("free-throw attempts", "points for the players who draw fouls"),
    "fg3a": ("3PA", "shooters' 3PM"),
    "oreb": ("offensive boards", "bigs' rebounds"),
}


def load(season_prefix="2026-1"):
    games = []
    for f in sorted(glob.glob(os.path.join(ROOT, "journal", f"{season_prefix}*.json"))):
        try:
            d = json.load(open(f))
        except (OSError, ValueError):
            continue
        for g in d.get("games", []):
            if g.get("missing") or not g.get("tw") or not g.get("tl"):
                continue
            games.append((d.get("date"), g))
    return games


def profiles(games):
    rows = defaultdict(list)
    for date, g in games:
        for me, opp, mine, theirs in ((g["winner"], g["loser"], g["tw"], g["tl"]),
                                      (g["loser"], g["winner"], g["tl"], g["tw"])):
            rows[me].append({"date": date, "opp": opp, "won": me == g["winner"], "m": mine, "a": theirs})
    prof = {}
    for team, gs in rows.items():
        p = {"n": len(gs), "w": sum(x["won"] for x in gs)}
        for k in STATS:
            p["made_" + k] = st.mean(x["m"].get(k, 0) or 0 for x in gs)
            p["allowed_" + k] = st.mean(x["a"].get(k, 0) or 0 for x in gs)
        p["tov"] = st.mean(x["m"].get("tov", 0) or 0 for x in gs)
        p["forced"] = st.mean(x["a"].get("tov", 0) or 0 for x in gs)
        p["poss"] = p["forced"] - p["tov"] + p["made_oreb"] - p["allowed_oreb"] + 0.44 * (p["made_fta"] - p["allowed_fta"])
        p["pts"] = st.mean(x["m"]["pts"] for x in gs)
        p["opp_pts"] = st.mean(x["a"]["pts"] for x in gs)
        p["fast_share"] = p["made_fastbreak"] / p["pts"] if p["pts"] else 0
        p["halfcourt_pts"] = p["pts"] - p["made_fastbreak"]
        p["halfcourt_allowed"] = p["opp_pts"] - p["allowed_fastbreak"]
        cold = [x for x in gs if (x["m"].get("fg_pct") or 0) < (x["a"].get("fg_pct") or 0)]
        p["cold_n"], p["cold_w"] = len(cold), sum(x["won"] for x in cold)
        prof[team] = p
    # league ranks (1 = most)
    keys = [k for k in next(iter(prof.values())) if k not in ("n", "w", "cold_n", "cold_w")]
    for k in keys:
        order = sorted(prof, key=lambda t: -prof[t][k])
        for i, t in enumerate(order, 1):
            prof[t]["rk_" + k] = i
    return prof


def lens(a, b, prof, top=10):
    A, B = prof.get(a), prof.get(b)
    if not A or not B:
        return f"{a} @ {b}: no recorded games for {a if not A else b}"
    out = [f"=== {a} @ {b}   ({a} {A['w']}-{A['n']-A['w']}, {b} {B['w']}-{B['n']-B['w']} in recorded games)"]

    def edge(x, y, X, Y):
        hits = []
        for k, (lab, prop) in STATS.items():
            if X["rk_made_" + k] <= top and Y["rk_allowed_" + k] <= top:
                hits.append(f"  {x} {lab} {X['made_'+k]:.1f} (#{X['rk_made_'+k]}) vs {y} allows {Y['allowed_'+k]:.1f} "
                            f"(#{Y['rk_allowed_'+k]} most) -> {prop}")
        return hits

    for x, y, X, Y in ((a, b, A, B), (b, a, B, A)):
        h = edge(x, y, X, Y)
        out.append(f"{x}'s strengths that {y} feeds:" if h else f"{x}: no top-{top} strength meets a top-{top} {y} weakness")
        out += h
    for t, P in ((a, A), (b, B)):
        out.append(f"{t}: possession edge {P['poss']:+.1f}/g (#{P['rk_poss']}) | forces {P['forced']:.1f} TO, commits {P['tov']:.1f} | "
                   f"fast-break share {P['fast_share']:.0%} (#{P['rk_fast_share']}) | half-court pts {P['halfcourt_pts']:.0f} for, "
                   f"{P['halfcourt_allowed']:.0f} against | when out-shot: {P['cold_w']}-{P['cold_n']-P['cold_w']}")
    fast, slow = (a, b) if A["fast_share"] >= B["fast_share"] else (b, a)
    F, S = prof[fast], prof[slow]
    if F["rk_fast_share"] > len(prof) // 2:
        out.append(f"Tempo: neither team leans on transition ({a} {A['fast_share']:.0%}, {b} {B['fast_share']:.0%}); "
                   f"a half-court game. Half court allowed: {a} {A['halfcourt_allowed']:.0f} (#{A['rk_halfcourt_allowed']} most), "
                   f"{b} {B['halfcourt_allowed']:.0f} (#{B['rk_halfcourt_allowed']} most).")
    else:
      out.append(f"Tempo: {fast} wants to run ({F['fast_share']:.0%} of points in transition); {slow} allows "
                 f"{S['allowed_fastbreak']:.1f} fast-break pts (#{S['rk_allowed_fastbreak']} most). In the half court, "
                 f"{slow} allows {S['halfcourt_allowed']:.0f} (#{S['rk_halfcourt_allowed']} most), {fast} scores "
                 f"{F['halfcourt_pts']:.0f} (#{F['rk_halfcourt_pts']}).")
    d = A["poss"] - B["poss"]
    lead = a if d > 0 else b
    out.append(f"Possession battle leans {lead} by about {abs(d):.1f} chances a game: "
               f"that is {lead}'s cushion on a cold shooting night.")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("teams", nargs="*")
    ap.add_argument("--slate", nargs="+", help="AWAY HOME AWAY HOME ...")
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args()
    teams = [ALIAS.get(t.upper(), t.upper()) for t in (a.slate or a.teams)]
    prof = profiles(load())
    for i in range(0, len(teams) - 1, 2):
        print(lens(teams[i], teams[i + 1], prof, a.top))
        print()


if __name__ == "__main__":
    main()
