#!/usr/bin/env python3
"""Head-to-head per-minute rate: how a player produces against tonight's opponent
compared with his own norm, as a BOOST / VETO / BOOM-BUST flag for the 7pm screen.

    python3 tools/h2h_rate.py 2026-11-26              # every slate player (22+ proj min)
    python3 tools/h2h_rate.py 2026-11-26 --all        # also print the neutral rows
    python3 tools/h2h_rate.py --player 1630183 --opp OKC --stat PTS

WHY THIS EXISTS
US 25 Nov 2026: karma's read "McDaniels always scores against OKC". The data
agreed -- 12 games v OKC (20+ min, incl. the 2025 WCF) at 1.15x his own
points-per-minute -- but the box-score projection has no head-to-head input,
and the screen only used H2H as a veto. He scored 32 (line 17.5; 30+ paid
10.00). The 24 Nov matchup-history backtest had already found good H2H
records add ~10-18% to PTS/AST (REB barely moves) and 4+ bad games at
<=0.80 knock ~16% off.

HOW
For each game against the opponent in the last three seasons (regular season
and playoffs) with MIN >= 20, ratio = stat / (that season's regular-season
stat-per-minute x minutes played). The flag uses the mean ratio:
    BOOST   mean >= 1.10 and n >= 6   -> project x mean (capped at 1.25),
                                         look at upside rungs
    VETO    mean <= 0.85 and n >= 4   -> step down or drop
    BOOM    spread (max/min ratio) >= 2.5 with n >= 6 -> main line + small
                                         upside, never a big stake
Roster changes since those games (new defenders, missing bigs) are not
modelled; the screen still says why tonight differs.
"""
import argparse
import glob
import json
import os
import statistics as st
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "data_cache")
SEASONS = ("2024-25", "2025-26", "2026-27")
MIN_MINUTES = 20
BOOST_AT, VETO_AT, BOOM_SPREAD, CAP = 1.10, 0.85, 2.5, 1.25
ALIAS = {"NO": "NOP", "WSH": "WAS", "UTAH": "UTA", "SA": "SAS", "GS": "GSW", "NY": "NYK"}


def _val(row, stat):
    if stat == "PRA":
        return row["PTS"] + row["REB"] + row["AST"]
    if stat == "PR":
        return row["PTS"] + row["REB"]
    if stat == "PA":
        return row["PTS"] + row["AST"]
    return row[stat]


def load(pid, season, cache=CACHE):
    path = os.path.join(cache, f"gamelog_{pid}_{season}.json")
    if not os.path.exists(path):
        return []
    d = json.load(open(path))
    rows = d.get("data") if isinstance(d, dict) else d
    return [r for r in rows or [] if (r.get("MIN") or 0) > 0]


def opp_of(row):
    return ALIAS.get(row["MATCHUP"][-3:].strip(), row["MATCHUP"][-3:].strip())


def h2h(pid, opp, stat, cache=CACHE, seasons=SEASONS):
    """{'n', 'mean', 'lo', 'hi', 'games': [(date, min, value, ratio)], 'flag'}"""
    games = []
    for s in seasons:
        rows = load(pid, s, cache)
        reg = [r for r in rows if str(r.get("SEASON_ID", "2"))[0] == "2"]
        mins = sum(r["MIN"] for r in reg)
        if not mins:
            continue
        per_min = sum(_val(r, stat) for r in reg) / mins
        if per_min <= 0:
            continue
        for r in rows:
            if opp_of(r) == opp and r["MIN"] >= MIN_MINUTES:
                ratio = _val(r, stat) / (per_min * r["MIN"])
                games.append((r["GAME_DATE"], r["MIN"], _val(r, stat), round(ratio, 2)))
    out = {"n": len(games), "games": games, "mean": None, "lo": None, "hi": None, "flag": None}
    if not games:
        return out
    ratios = [g[3] for g in games]
    out.update(mean=round(st.mean(ratios), 2), lo=min(ratios), hi=max(ratios))
    out["flag"] = flag(out)
    return out


def flag(res):
    n, m, lo, hi = res["n"], res["mean"], res["lo"], res["hi"]
    tags = []
    if m is not None and m >= BOOST_AT and n >= 6:
        tags.append("BOOST")
    if m is not None and m <= VETO_AT and n >= 4:
        tags.append("VETO")
    if n >= 6 and lo and hi / lo >= BOOM_SPREAD:
        tags.append("BOOM")
    return "+".join(tags) or None


def boosted(projection, res):
    """Projection adjusted by a BOOST (capped); unchanged otherwise."""
    if res.get("flag") and "BOOST" in res["flag"]:
        return round(projection * min(res["mean"], CAP), 1)
    return projection


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("date", nargs="?")
    ap.add_argument("--player", type=int)
    ap.add_argument("--opp")
    ap.add_argument("--stat", default="PTS")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--min-proj", type=float, default=22)
    a = ap.parse_args(argv)
    if a.player:
        r = h2h(a.player, a.opp, a.stat)
        print(json.dumps(r, indent=1))
        return 0
    slate = json.load(open(os.path.join(ROOT, "journal", "slate", f"{a.date}.json")))
    for g in slate["games"]:
        print(f"## {g['away']}@{g['home']}")
        for team in (g["away"], g["home"]):
            opp = g["home"] if team == g["away"] else g["away"]
            for p in g["players"][team]:
                if p["min"] < a.min_proj:
                    continue
                for stat in ("PTS", "REB", "AST"):
                    r = h2h(p["pid"], opp, stat)
                    if (not r["flag"] or r["flag"] == "BOOM") and not a.all:
                        continue   # BOOM alone is common (single-game ratios swing); show it only beside BOOST/VETO
                    proj = p.get(stat)
                    print(f"  {team} {p['name'][:22]:22s} {stat} x{r['mean']} n{r['n']} "
                          f"[{r['lo']}-{r['hi']}] {r['flag'] or ''}  proj {proj} -> {boosted(proj, r)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
