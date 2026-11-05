#!/usr/bin/env python3
"""Closing-line value for a night's slips, by price.

    python3 tools/clv_report.py 2026-11-04

WHY THIS EXISTS
6 Nov 2026, after a 2-6 night, karma asked how winning bettors actually
win. The one measure the research and long-run winners agree on is the
closing line: did you get a better price than the market settled on just
before tip? It shows skill (or its absence) within ~50 bets, long before
a win rate does, and it does not care whether one leg lost by half a
rebound.

render_clv in the Journal already reports points of line for legs that
carry an over/under line. Most of our legs do not: "To Record N+" legs,
and slips logged without a per-leg price. This reads the price we could
have taken at bet time (the last snapshot saved before the slip was
placed) and the closing price (the last one before tip), for the SAME
outcome -- same player, stat, side and line -- and reports

    clv = open_price / close_price - 1

Positive: the price shortened after we bet, i.e. the market moved toward
us. For an over whose main line moved, the outcome is matched on the "N+"
ladder rung (threshold = line + 0.5), so a line move is still priced. An
under with a moved line has no ladder; it gets points of line only.
Snapshots come from tools/line_snapshot.py (the --closing launchd job).
"""
import argparse
import datetime
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import picks as pk  # noqa: E402

REPO = os.path.dirname(HERE)
LINES = os.path.join(REPO, "journal", "lines")


def _ts(s):
    return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def snapshots(us_date):
    out = []
    for f in glob.glob(os.path.join(LINES, us_date, "*.json")):
        try:
            d = json.load(open(f))
            out.append((_ts(d["taken_at"]), d))
        except (OSError, ValueError, KeyError):
            continue
    return sorted(out, key=lambda x: x[0])


def price_in(snap, leg):
    """(price, ou_line) for this exact outcome in one snapshot."""
    want, side, line = pk._norm(leg["player"]), leg.get("side", "over"), leg["line"]
    price, ou_line = None, None
    for e in snap.get("events") or []:
        if leg.get("game") and e.get("game") != leg["game"]:
            continue
        for r in e.get("rows") or []:
            if r["stat"] != leg["stat"] or pk._norm(r.get("player") or "") != want:
                continue
            if r["kind"] == "ou":
                ou_line = r["line"]
                if r["line"] == line:
                    price = r["over"] if side == "over" else r["under"]
            elif side == "over" and price is None and r.get("threshold") == int(line + 0.5):
                price = r["price"]
    return price, ou_line


def start_of(snaps, game):
    for _, d in snaps:
        for e in d.get("events") or []:
            if e.get("game") == game and e.get("start"):
                return _ts(e["start"])
    return None


def leg_clv(leg, placed, snaps):
    tip = start_of(snaps, leg.get("game"))
    before_bet = [d for t, d in snaps if placed and t <= placed]
    before_tip = [d for t, d in snaps if tip and t < tip]
    if not before_bet or not before_tip:
        return None
    op, oline = None, None
    for d in reversed(before_bet):
        op, oline = price_in(d, leg)
        if op:
            break
    cp, cline = None, None
    for d in reversed(before_tip):
        cp, cline = price_in(d, leg)
        if cp or cline is not None:
            break
    out = {"open": op, "close": cp, "open_line": oline, "close_line": cline}
    if op and cp:
        out["clv"] = round(op / cp - 1, 3)
    if oline is not None and cline is not None:
        under = leg.get("side") == "under"
        out["line_move"] = round((oline - cline) if under else (cline - oline), 1)
    return out


def report(us_date, folders=("picks", "competitors")):
    snaps = snapshots(us_date)
    rows = []
    for folder in folders:
        for f in glob.glob(os.path.join(REPO, "journal", folder, "*.json")):
            s = json.load(open(f))
            legs = [l for l in s.get("legs") or [] if l.get("us_date") == us_date and l.get("line") is not None]
            if not legs:
                continue
            who = ("tipster: " if folder == "competitors" else "") + (s.get("picked_by") or "you")
            placed = _ts(s["placed_at"]) if s.get("placed_at") else None
            for l in legs:
                c = leg_clv(l, placed, snaps)
                rows.append({"slip": s.get("id"), "who": who, "player": l["player"], "stat": l["stat"],
                             "side": l.get("side", "over"), "line": l["line"], **(c or {})})
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("us_date")
    a = ap.parse_args(argv)
    rows = report(a.us_date)
    seen, uniq = set(), []
    for r in rows:   # a leg in several slips counts once per picker
        k = (r["who"], r["player"], r["stat"], r["side"], r["line"])
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    by = {}
    for r in uniq:
        if "clv" in r:
            by.setdefault(r["who"], []).append(r["clv"])
    for who, v in sorted(by.items()):
        beat = sum(x > 0 for x in v)
        print(f"{who:28s} {len(v):3d} priced legs  beat the close {beat}/{len(v)}  avg {sum(v)/len(v):+.1%}")
    print()
    for r in sorted(uniq, key=lambda r: r.get("clv", 0)):
        if "clv" in r or "line_move" in r:
            print(f"{r['who'][:18]:18s} {r['player'][:20]:20s} {r['side'][:1]}{r['line']:<5} {r['stat']:4s} "
                  f"open {r.get('open')} close {r.get('close')} clv {r.get('clv', '')} line_move {r.get('line_move', '')}")


if __name__ == "__main__":
    main()
