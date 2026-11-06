#!/usr/bin/env python3
"""Does the OPPONENT let players like him reach his number?

    python3 tools/opp_allows.py MIN RA
    python3 tools/opp_allows.py WAS REB --min 24

Karma, 7 Nov 2026: "the measurement should be if the opponent team allows
him or any of our picks to reach that number." A player's own hit rate is
half the story; the other half is what this defence gives up to his kind of
player.

For every player game this season against the opponent (from the cached
game logs, 20+ minutes), compare what he did with his own per-minute norm
at the minutes he played. Grouped by archetype (guard / wing / big, from
engine.archetype), so a guard's line is read against what this defence
allows to guards. Ratio 1.10 = players in that role get 10% more than
usual against this team. Early-season samples are small; read n.
"""
import argparse
import glob
import json
import os
import statistics as st
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from engine import archetype as _arch  # noqa: E402

COLS = {"PTS": ["PTS"], "REB": ["REB"], "AST": ["AST"], "FG3M": ["FG3M"], "PR": ["PTS", "REB"],
        "PA": ["PTS", "AST"], "RA": ["REB", "AST"], "PRA": ["PTS", "REB", "AST"]}
ALIAS = {"NO": "NOP", "WSH": "WAS", "UTAH": "UTA", "SA": "SAS", "GS": "GSW", "NY": "NYK"}


def nf(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def logs(season="2026-27"):
    out = {}
    for f in glob.glob(os.path.join(ROOT, "data_cache", f"gamelog_*_{season}.json")):
        pid = int(os.path.basename(f).split("_")[1])
        try:
            d = json.load(open(f))
        except (OSError, ValueError):
            continue
        rows = (d.get("data") if isinstance(d, dict) else None) or (d.get("payload", {}) or {}).get("data") or []
        rows = [r for r in rows if nf(r.get("MIN")) > 0]
        if rows:
            out[pid] = rows
    return out


def kind(pid, cache={}):
    if pid not in cache:
        try:
            a = _arch.of_player(pid)
            cache[pid] = (a.get("archetype") if isinstance(a, dict) else a) or "?"
        except Exception:
            cache[pid] = "?"
    return str(cache[pid]).lower()


def allows(opp, stat, min_min=20.0, season="2026-27", L=None):
    L = L or logs(season)
    cols = COLS[stat]
    by = defaultdict(list)
    for pid, rows in L.items():
        mins = sum(nf(r["MIN"]) for r in rows)
        if mins < 60:
            continue
        rate = sum(sum(nf(r.get(c)) for c in cols) for r in rows) / mins
        if rate <= 0:
            continue
        for r in rows:
            m = r.get("MATCHUP", "")
            if not m.endswith(opp) or nf(r["MIN"]) < min_min:
                continue
            exp = rate * nf(r["MIN"])
            act = sum(nf(r.get(c)) for c in cols)
            by[kind(pid)].append((act / exp, act, exp))
    return {k: {"ratio": st.mean(x[0] for x in v), "n": len(v)} for k, v in by.items()}


def main():
    ap = argparse.ArgumentParser(description="What does OPP allow to guards/wings/bigs, against their own norms?")
    ap.add_argument("opp")
    ap.add_argument("stat", choices=sorted(COLS))
    ap.add_argument("--min", type=float, default=20.0, help="minimum minutes for a game to count")
    a = ap.parse_args()
    res = allows(ALIAS.get(a.opp.upper(), a.opp.upper()), a.stat, a.min)
    for k, v in sorted(res.items()):
        print(f"{k:6s} {v['ratio']:.2f}x their norm  (n={v['n']} player games)")


if __name__ == "__main__":
    main()
