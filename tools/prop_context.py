#!/usr/bin/env python3
"""Display-only context for prop lines: H2H, rung ladders and the Box Set list.

    python3 tools/prop_context.py ladder "Aday Mara" --date 2026-11-22
    python3 tools/prop_context.py bigticket --date 2026-11-21 [--min-p 0.88]
    python3 tools/prop_context.py h2h "PJ Washington" OKC PRA 24.5

Copied in spirit from PropFinder (karma approved 21 Nov 2026), plus the
app's "Advanced options" context brought to the slate:

* H2H: the line's hit rate vs tonight's opponent over the last two seasons,
  shown twice -- all games, and games since the opponent's last roster change
  (data/roster_changes.json). A big H2H number on a defence that has since
  changed is a stale edge (angle 16 in the project's angles checklist).
* ladder: every Sportsbet rung / main line saved by the Journal app for a
  player, with season, last-10 and H2H hit rates against the implied price.
  The best-value rung is the one whose CONSERVATIVE rate (the lowest of
  season, last 10 and, when n >= 5, H2H) beats the price by the most.
* bigticket: every rung on the slate rated ~88%+ by the same conservative
  rate, grouped by game, safest first, so the morning screen can build the
  Box Set (4 box-score SGMs of 8-9 legs at ~15 each; project doc
  claude/big-ticket-and-reads-plan.md).

NOTHING here picks bets or changes a betting rule. It prints context.
Probabilities are raw game-log hit rates: they are optimistic for young
players on the rise and for minutes that just went up, and they know
nothing about tonight's injuries beyond the slate file's out/day-to-day
lists. Read every row with the angles checklist.
"""
import argparse
import datetime as dt
import glob
import json
import os
import statistics as st
import sys
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMBOS = {"PRA": ("PTS", "REB", "AST"), "PR": ("PTS", "REB"), "PA": ("PTS", "AST"),
          "RA": ("REB", "AST"), "PTS": ("PTS",), "REB": ("REB",), "AST": ("AST",),
          "FG3M": ("FG3M",), "STL": ("STL",), "BLK": ("BLK",)}
ALIAS = {"NO": "NOP", "WSH": "WAS", "UTAH": "UTA", "SA": "SAS", "GS": "GSW", "NY": "NYK"}


def norm(name):
    s = unicodedata.normalize("NFD", str(name)).encode("ascii", "ignore").decode().lower()
    s = s.replace(".", "").replace("'", "")
    s = "".join(c if c.isalnum() or c.isspace() else " " for c in s)
    return " ".join(p for p in s.split() if p not in {"jr", "sr", "ii", "iii", "iv"})


def nf(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def season_of(day):
    y = day.year if day.month >= 9 else day.year - 1
    return f"{y}-{str(y + 1)[2:]}"


def prev_season(s):
    y = int(s[:4]) - 1
    return f"{y}-{str(y + 1)[2:]}"


def _date(row):
    return dt.datetime.strptime(row["GAME_DATE"], "%b %d, %Y").date()


def rows_for(pid, seasons, before=None):
    """Game rows (newest first) for pid across seasons, strictly before `before`."""
    out = []
    for s in seasons:
        try:
            d = json.load(open(os.path.join(ROOT, "data_cache", f"gamelog_{pid}_{s}.json")))
        except (OSError, ValueError):
            continue
        rows = (d.get("data") if isinstance(d, dict) else None) or []
        out += [r for r in rows if nf(r.get("MIN")) > 0]
    out = [r for r in out if before is None or _date(r) < before]
    out.sort(key=_date, reverse=True)
    return out


def value(row, stat):
    return sum(nf(row.get(c)) for c in COMBOS.get(stat, (stat,)))


def opp_of(row):
    return str(row.get("MATCHUP", "")).split()[-1]


def roster_changes():
    try:
        d = json.load(open(os.path.join(ROOT, "data", "roster_changes.json")))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in d.items() if not k.startswith("_")}


def player_types():
    try:
        d = json.load(open(os.path.join(ROOT, "data", "player_types.json")))
    except (OSError, ValueError):
        return {}
    return {norm(k): v for k, v in d.items() if not k.startswith("_")}


def h2h_from_rows(rows, opp, stat, line, side="over", since=None):
    """Hit counts vs `opp` (all games, and games on/after `since`)."""
    opp = ALIAS.get(opp, opp)
    vs = [r for r in rows if opp_of(r) == opp]
    hit = (lambda v: v > line) if side == "over" else (lambda v: v < line)
    vals = [value(r, stat) for r in vs]
    res = {"n": len(vals), "hits": sum(hit(v) for v in vals), "vals": vals}
    if since:
        sd = dt.date.fromisoformat(since)
        sv = [value(r, stat) for r in vs if _date(r) >= sd]
        res.update(since=since, since_n=len(sv), since_hits=sum(hit(v) for v in sv))
    return res


def h2h(pid, opp, stat, line, side="over", day=None):
    day = day or dt.date.today()
    s = season_of(day)
    rows = rows_for(pid, [prev_season(s), s], before=day)
    ch = roster_changes().get(ALIAS.get(opp, opp), {})
    return h2h_from_rows(rows, opp, stat, line, side, ch.get("since"))


def h2h_text(h):
    if not h or not h.get("n"):
        return "H2H -"
    t = f"H2H {h['hits']}/{h['n']}"
    if h.get("since"):
        t += f" (since {h['since'][5:]}: {h.get('since_hits', 0)}/{h.get('since_n', 0)})"
    return t


def rates(rows, stat, line, side="over"):
    hit = (lambda v: v > line) if side == "over" else (lambda v: v < line)
    vals = [value(r, stat) for r in rows]
    if not vals:
        return None
    last = vals[:10]
    return {"season_n": len(vals), "season": sum(map(hit, vals)) / len(vals),
            "last10_n": len(last), "last10": sum(map(hit, last)) / len(last)}


def conservative(r, h=None):
    """Lowest of season, last 10 and (n >= 5) H2H hit rate."""
    ps = [r["season"], r["last10"]]
    if h and h.get("n", 0) >= 5:
        ps.append(h["hits"] / h["n"])
    return min(ps)


# ---------- lines and names ----------

def lines_snapshot(date):
    files = sorted(glob.glob(os.path.join(ROOT, "journal", "lines", date, "*.json")))
    if not files:
        return None
    return json.load(open(files[-1]))


def line_rows(snap):
    for ev in (snap or {}).get("events", []):
        for r in ev.get("rows", []):
            yield ev, r


def name_index():
    """name -> (pid, team) from the cached rosters."""
    idx = {}
    for f in glob.glob(os.path.join(ROOT, "data_cache", "roster_*.json")):
        try:
            d = json.load(open(f))
        except (OSError, ValueError):
            continue
        for row in (d.get("data") if isinstance(d, dict) else None) or []:
            if "PLAYER_ID" in row and "PLAYER" in row:
                idx[norm(row["PLAYER"])] = (int(row["PLAYER_ID"]), row.get("TEAM_ABBREVIATION"))
    return idx


def team_from_rows(rows):
    return str(rows[0].get("MATCHUP", "")).split()[0] if rows else None


def slate_file(date):
    try:
        return json.load(open(os.path.join(ROOT, "journal", "slate", f"{date}.json")))
    except (OSError, ValueError):
        return None


def legs_for_row(r):
    """(stat, line, side) for a lines row: X+ rungs become line X-0.5, over."""
    if r.get("kind") == "alt":
        return r["stat"], float(r["threshold"]) - 0.5, "over", r.get("price")
    if r.get("kind") == "ou":
        return r["stat"], float(r["line"]), "over", r.get("over")
    return None


# ---------- commands ----------

def cmd_ladder(name, date, idx=None):
    idx = idx or name_index()
    day = dt.date.fromisoformat(date)
    snap = lines_snapshot(date)
    if not snap:
        print(f"no saved lines for {date} (journal/lines/{date})")
        return 1
    hit = idx.get(norm(name))
    if not hit:
        print(f"{name}: not in the cached rosters")
        return 1
    pid, _team = hit
    s = season_of(day)
    cur = rows_for(pid, [s], before=day)
    both = rows_for(pid, [prev_season(s), s], before=day)
    team = team_from_rows(cur) or _team
    tags = player_types().get(norm(name))
    seen = []
    for ev, r in line_rows(snap):
        if norm(r.get("player", "")) != norm(name):
            continue
        leg = legs_for_row(r)
        if not leg or not leg[3]:
            continue
        stat, line, side, price = leg
        teams = ev.get("game", "").split("@")
        opp = teams[1] if teams and teams[0] == team else (teams[0] if teams else None)
        rr = rates(cur, stat, line, side)
        if not rr:
            continue
        ch = roster_changes().get(opp, {})
        h = h2h_from_rows(both, opp, stat, line, side, ch.get("since")) if opp else None
        cons = conservative(rr, h)
        seen.append((stat, line, price, rr, h, cons, cons - 1 / price, r.get("kind")))
    if not seen:
        print(f"{name}: no lines saved for {date}")
        return 1
    seen.sort(key=lambda x: (x[0], x[1]))
    best = max(seen, key=lambda x: x[6])
    print(f"{name} ({team}) {date}" + (f"  tags: {', '.join(tags)}" if tags else ""))
    print(f"{'stat':5s}{'line':>6s}{'kind':>5s}{'price':>7s}{'impl':>6s}{'season':>8s}{'last10':>8s}  {'H2H':28s}{'cons':>6s}{'value':>7s}")
    for x in seen:
        stat, line, price, rr, h, cons, val, kind = x
        mark = "  <- best value" if x is best else ""
        print(f"{stat:5s}{line:6.1f}{kind:>5s}{price:7.2f}{1/price:6.0%}{rr['season']:8.0%}{rr['last10']:8.0%}  "
              f"{h2h_text(h):28s}{cons:6.0%}{val:+7.0%}{mark}")
    return 0


def cmd_bigticket(date, min_p=0.88, max_gap=10.0, min_min=18.0, min_price=1.08, idx=None):
    idx = idx or name_index()
    day = dt.date.fromisoformat(date)
    snap = lines_snapshot(date)
    if not snap:
        print(f"no saved lines for {date}")
        return 1
    slate = slate_file(date) or {}
    skip_names, big_gap = set(), set()
    for g in slate.get("games", []):
        for side in ("out", "day_to_day"):
            for team, names in (g.get(side) or {}).items():
                skip_names |= {norm(n) for n in names}
        sc = g.get("score") or {}
        if len(sc) == 2 and abs(sc[g["home"]] - sc[g["away"]]) >= max_gap:
            big_gap.add(f"{g['away']}@{g['home']}")
    types = player_types()
    s = season_of(day)
    cache, rows_out, skipped = {}, [], {"out/dtd": 0, "minutes": 0, "unknown": 0}
    for ev, r in line_rows(snap):
        leg = legs_for_row(r)
        if not leg or not leg[3]:
            continue
        n = norm(r.get("player", ""))
        if n in skip_names:
            skipped["out/dtd"] += 1
            continue
        if n not in cache:
            hit = idx.get(n)
            cache[n] = rows_for(hit[0], [s], before=day) if hit else None
        cur = cache[n]
        if not cur or len(cur) < 8:
            skipped["unknown"] += 1
            continue
        mins = [nf(x["MIN"]) for x in cur[:10]]
        if st.median(mins) < min_min or (len(mins) > 2 and st.pstdev(mins) > 8):
            skipped["minutes"] += 1
            continue
        stat, line, side, price = leg
        if price < min_price:
            continue   # a 1.01 rung adds nothing to a Box Set price
        rr = rates(cur, stat, line, side)
        cons = conservative(rr)
        if cons < min_p:
            continue
        rows_out.append((ev.get("game"), r["player"], stat, line, price, cons, rr,
                         types.get(n)))
    # Keep the HIGHEST qualifying rung per player and stat: the one that
    # adds the most price for the same confidence.
    best = {}
    for x in rows_out:
        k = (x[0], norm(x[1]), x[2])
        if k not in best or x[3] > best[k][3]:
            best[k] = x
    rows_out = list(best.values())
    by_game = {}
    for x in rows_out:
        by_game.setdefault(x[0], []).append(x)
    print(f"Box Set candidates {date}: rungs rated >= {min_p:.0%} (lowest of season and last 10), "
          f"{len(rows_out)} rows. Skipped: {skipped}. Games flagged for a big tool gap "
          f"(>= {max_gap:g} pts, blowout risk): {sorted(big_gap) or 'none'}")
    for game in sorted(by_game, key=lambda g: -len(by_game[g])):
        legs = sorted(by_game[game], key=lambda x: (-x[5], -x[4]))
        flag = "  ** blowout risk" if game in big_gap else ""
        print(f"\n{game}{flag}: {len(legs)} rows")
        for g, pl, stat, line, price, cons, rr, tags in legs[:15]:
            t = f"  [{', '.join(tags)}]" if tags else ""
            print(f"  {pl:24s}{stat:5s} {line + 0.5:4.0f}+  @{price:<6.2f} p={cons:.0%} "
                  f"(season {rr['season']:.0%}/{rr['season_n']}, L10 {rr['last10']:.0%}){t}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ladder"); a.add_argument("player"); a.add_argument("--date", required=True)
    b = sub.add_parser("bigticket"); b.add_argument("--date", required=True)
    b.add_argument("--min-p", type=float, default=0.88); b.add_argument("--max-gap", type=float, default=10.0)
    b.add_argument("--min-price", type=float, default=1.08)
    c = sub.add_parser("h2h"); c.add_argument("player"); c.add_argument("opp"); c.add_argument("stat")
    c.add_argument("line", type=float); c.add_argument("--date", default=None)
    args = ap.parse_args(argv)
    if args.cmd == "ladder":
        return cmd_ladder(args.player, args.date)
    if args.cmd == "bigticket":
        return cmd_bigticket(args.date, args.min_p, args.max_gap, min_price=args.min_price)
    idx = name_index()
    hit = idx.get(norm(args.player))
    if not hit:
        print("not found"); return 1
    day = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    print(h2h_text(h2h(hit[0], args.opp.upper(), args.stat.upper(), args.line, day=day)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
