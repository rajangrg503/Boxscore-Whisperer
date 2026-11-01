#!/usr/bin/env python3
"""Rank tonight's prop lines by a TWO-SEASON hit rate, minutes-adjusted.

    python3 tools/rank_lines.py slate_lines/lines_YYYYMMDD.txt \
            --unavailable slate_lines/availability_YYYYMMDD.json

WHY THIS EXISTS
On 31 Oct 2026 the card was built from five-game logs alone, and six
rows looked like 4/5 or 5/5 locks. Checked against the SAME player's
previous season, scaled to this season's minutes, three of them
collapsed:

    Toumani Camara  UNDER 4.5 REB   4/4 on 4 games (max 2)
                                    -- but 5.1 reb/g over 87 games last
                                       season; at tonight's 25 mpg that
                                       scales to 3.8, not 1.8
    Onyeka Okongwu  UNDER 12.5 PTS  5/6 this season
                                    -- 61% of 80 games cleared 13 last
                                       season on the SAME shot volume;
                                       the drop is shooting %, which is
                                       not predictable
    LeBron James    UNDER 19.5 PTS  4/4 this season (max 18)
                                    -- 60% of 70 games went 20+ last
                                       season

A four-game sample does not overturn eighty games. The book is pricing
both seasons; a filter that reads only the recent one finds "edges" that
are really just small samples, which is the 22 Oct failure in a new
costume.

THE BLEND
    p_cur   hit rate on this season's games
    p_prev  hit rate on last season's games, each value scaled by
            (this season's mpg / last season's mpg)
    p       (n_cur * p_cur + PRIOR_W * p_prev) / (n_cur + PRIOR_W)

PRIOR_W = 12 treats last season, minutes-adjusted, as worth twelve
games of evidence. With n_cur = 5 that is 71% weight on the prior --
deliberately heavy, because the prior is what the recent-form filter
keeps ignoring.

The scaling is linear in minutes and that is WRONG in a known
direction: measured over 4,799 journal lines, production tracks minutes
almost proportionally downward (0.61 output per 0.55 minutes) but is
heavily damped upward (1.23 per 1.64). So a player whose minutes have
RISEN gets an over-generous p_prev here. Those rows are flagged.

Edge is against the raw price, which still carries the book's margin,
so a positive edge here is not yet a positive expectation -- it is a
row worth looking at. Nothing in this file is backed by a winning
record: the model's one scored slate stands at 49.5%.
"""
import argparse, json, os, statistics as st, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.cache import read_payload                      # noqa: E402
from engine.players import fold_diacritics                 # noqa: E402
from engine.team_ids import TEAM_ID_BY_ABBR                # noqa: E402

PRIOR_W = 12.0
MIN_GAMES_CUR = 4
MIN_GAMES_PREV = 15

# Karma's question, 31 Oct: "should we only value last season data for
# those who played significant games?"
#
# Games played is close but not the gate. Ajay Mitchell played plenty of
# significant games last season -- in a team that still had Holmgren,
# Jalen Williams, Caruso and Wallace. His 84% prior describes a role he
# is not in tonight, and no games-played threshold would have caught
# that, because he passes it easily.
#
# What matters is whether the role CARRIED OVER. Two things measure it
# without needing play-by-play: how the minutes moved, and how the shot
# volume per minute moved. A player at the same minutes and the same
# attempts is doing the same job; one whose attempts per 36 have jumped
# by half is not, however many games he played.
#
# The first attempt at this just scaled the prior's weight by similarity,
# and it was wrong in an instructive way. Shrinking the prior does not
# express "we know less" -- it hands the weight to p_cur, so a five-game
# sample gets MORE authority exactly where the role changed and the five
# games are least trustworthy. Sabonis (similarity 0.56, minutes up 25%)
# went from 74% to 79% on that change, which is backwards.
#
# A changed role means the prior answers a question we did not ask. So
# the prior's twelve games are SPLIT: `12 * sim` of them keep saying what
# last season said, and `12 * (1 - sim)` say 0.5 -- nothing. Total weight
# stays twelve, and a shifted role pulls the blend toward a coin flip
# instead of toward whichever extreme the small sample happens to show.


def role_similarity(mpg_cur, mpg_prev, fga36_cur, fga36_prev):
    """1.0 = same minutes and same shot volume per minute. Falls toward 0
    as either moves. Symmetric: doubling and halving are equally far."""
    def ratio(a, b):
        if not a or not b:
            return 0.5
        return min(a / b, b / a)
    return ratio(mpg_cur, mpg_prev) * ratio(fga36_cur, fga36_prev)


def norm(name):
    folded = fold_diacritics(str(name)).lower()
    folded = "".join(c if (c.isalnum() or c.isspace()) else " " for c in folded)
    return " ".join(p for p in folded.split()
                    if p and p not in {"jr", "sr", "ii", "iii", "iv"})


def nf(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def build_index(exclude, dtd, season_cur, season_prev):
    index = {}
    for abbr, team_id in TEAM_ID_BY_ABBR.items():
        for row in ((read_payload(f"roster_{team_id}") or {}).get("data") or []):
            pid = int(row["PLAYER_ID"])
            if pid in exclude or row["PLAYER"] in dtd:
                continue
            cur = (read_payload(f"gamelog_{pid}_{season_cur}") or {}).get("data") or []
            prev = (read_payload(f"gamelog_{pid}_{season_prev}") or {}).get("data") or []
            if len(cur) < MIN_GAMES_CUR or len(prev) < MIN_GAMES_PREV:
                continue
            mc = [nf(x.get("MIN")) for x in cur]
            mp = [nf(x.get("MIN")) for x in prev]
            if not st.mean(mc) or not st.mean(mp):
                continue
            fga_c = sum(nf(x.get("FGA")) for x in cur)
            fga_p = sum(nf(x.get("FGA")) for x in prev)
            fga36_c = fga_c / sum(mc) * 36 if sum(mc) else 0.0
            fga36_p = fga_p / sum(mp) * 36 if sum(mp) else 0.0
            sim = role_similarity(st.mean(mc), st.mean(mp), fga36_c, fga36_p)
            index[norm(row["PLAYER"])] = {
                "name": row["PLAYER"], "team": abbr, "cur": cur, "prev": prev,
                "mpg": st.mean(mc), "mpg_prev": st.mean(mp),
                "mcv": st.pstdev(mc) / st.mean(mc),
                "scale": st.mean(mc) / st.mean(mp),
                "fga36": fga36_c, "fga36_prev": fga36_p, "sim": sim,
                "w_prev": PRIOR_W * sim,
                "w_unknown": PRIOR_W * (1.0 - sim),
            }
    return index


COMBOS = {"PRA": ("PTS", "REB", "AST"), "PR": ("PTS", "REB"),
          "PA": ("PTS", "AST"), "RA": ("REB", "AST")}


def stat_of(row, stat):
    """A game's value for a stat, combos summed from their parts: medians
    do not add (pra-is-the-missing-column, 20 Oct), so a combo is read per
    game and the line is tested against that, never against a sum of
    projections."""
    parts = COMBOS.get(stat, (stat,))
    return sum(nf(row.get(c)) for c in parts)


def rate(player, stat, line, side):
    vc = [stat_of(x, stat) for x in player["cur"]]
    vp = [stat_of(x, stat) * player["scale"] for x in player["prev"]]
    hit = (lambda v: v > line) if side == "OVER" else (lambda v: v < line)
    p_cur = sum(1 for v in vc if hit(v)) / len(vc)
    p_prev = sum(1 for v in vp if hit(v)) / len(vp)
    n = len(vc)
    wp, wu = player["w_prev"], player["w_unknown"]
    blend = (n * p_cur + wp * p_prev + wu * 0.5) / (n + wp + wu)
    return p_cur, p_prev, blend, vc


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lines", help="game~stat~player~line~overprice~underprice per row")
    ap.add_argument("--unavailable", default=None)
    ap.add_argument("--season", default="2026-27")
    ap.add_argument("--prev-season", default="2025-26")
    ap.add_argument("--min-blend", type=float, default=0.60)
    ap.add_argument("--max-mcv", type=float, default=0.22)
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args(argv)

    exclude, dtd = set(), set()
    if args.unavailable:
        feed = json.load(open(args.unavailable))
        for game in feed.get("games", []):
            for row in game.get("out", []):
                if row.get("player_id"):
                    exclude.add(int(row["player_id"]))
        dtd = {d["name"] for d in feed.get("day_to_day", [])}
        print(f"availability gate: {len(exclude)} out, {len(dtd)} day-to-day "
              f"(day-to-day players are DROPPED, not bet)")
    else:
        print("availability gate: NOT APPLIED -- run tools/availability.py first")

    index = build_index(exclude, dtd, args.season, args.prev_season)
    rows, skipped = [], []
    for raw in open(args.lines):
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        game, stat, name, line, po, pu = raw.split("~")
        p = index.get(norm(name))
        if p is None:
            skipped.append(f"{name} ({game} {stat})")
            continue
        for side, price in (("OVER", float(po)), ("UNDER", float(pu))):
            p_cur, p_prev, blend, vc = rate(p, stat, float(line), side)
            rows.append({
                "game": game, "stat": stat, "name": p["name"], "line": float(line),
                "side": side, "price": price, "p_cur": p_cur, "p_prev": p_prev,
                "blend": blend, "edge": blend - 1.0 / price, "mcv": p["mcv"],
                "mpg": p["mpg"], "log": [int(v) for v in vc],
                "minutes_up": p["scale"] > 1.12,
                "sim": p["sim"],
            })

    keep = [r for r in rows
            if r["blend"] >= args.min_blend and r["mcv"] <= args.max_mcv
            and r["edge"] > 0 and min(r["p_cur"], r["p_prev"]) >= 0.50]
    keep.sort(key=lambda r: -r["edge"])

    print(f"\n{len(rows)} sides priced, {len(keep)} clear "
          f"blend>={args.min_blend:.0%}, both seasons>=50%, mcv<={args.max_mcv}, edge>0\n")
    print(f"{'game':9s}{'player':25s}{'stat':5s}{'side':6s}{'line':>6s}{'$':>6s}"
          f"{'cur':>6s}{'prev':>6s}{'role':>6s}{'blend':>7s}{'edge':>7s}"
          f"{'mpg':>6s}{'mcv':>6s}  log")
    for r in keep[:args.top]:
        flag = "  ^minutes up vs last season (p_prev optimistic)" if r["minutes_up"] else ""
        print(f"{r['game']:9s}{r['name']:25s}{r['stat']:5s}{r['side']:6s}"
              f"{r['line']:6.1f}{r['price']:6.2f}{r['p_cur']:6.0%}{r['p_prev']:6.0%}"
              f"{r['sim']:6.2f}{r['blend']:7.0%}{r['edge']:+7.1%}"
              f"{r['mpg']:6.1f}{r['mcv']:6.2f}  {r['log']}{flag}")
    if skipped:
        print(f"\nno two-season sample, dropped: {', '.join(sorted(set(skipped)))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
