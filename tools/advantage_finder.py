#!/usr/bin/env python3
"""Who has the BIGGEST EDGE in each game: his strength vs their weakness.

    python3 tools/advantage_finder.py 2026-11-09            # tonight's games (from the slate/schedule)
    python3 tools/advantage_finder.py 2026-11-08 --played   # a past night, using who actually played
    python3 tools/advantage_finder.py --backtest 2026-10-28 2026-11-08

karma, 9 Nov 2026 (wanted "since 3 days"): "like philly game, giddey had the
biggest advantage with light rim protection, good playmaking opportunity by
running in transition. rudy gobert having biggest rebounding opportunity."
And: "that is how you find ceiling bets."

For every rotation player in a game and each of PTS / REB / AST:

    strength  = his projected stat tonight (per-minute rate x recent minutes),
                and it must be a real strength (PTS >= 12, REB >= 6, AST >= 4)
    weakness  = what this opponent gives HIS ROLE (guard / wing / big) in that
                stat, against those players' own norms, before tonight
                (opp_allows, point in time, shrunk toward 1.0)
    edge      = strength x (weakness - 1), in units of the stat's game-to-game
                spread, so 3 extra rebounds counts like ~7 extra points

The top one or two per game are the "biggest edge". The opponent's missing
rotation players are listed beside it (a team without its bigs leaks boards
before the season numbers catch up), but they are NOT yet in the score.

Everything is computed from game logs strictly before the night, so the
backtest has no look-ahead.
"""
import argparse
import datetime as dt
import glob
import json
import os
import statistics as st
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

STATS = ("PTS", "REB", "AST")
SPREAD = {"PTS": 6.5, "REB": 2.8, "AST": 2.2}      # typical game-to-game sd
STRENGTH = {"PTS": 12.0, "REB": 6.0, "AST": 4.0}   # "this is one of his things"
PRIOR_GAMES = 12.0       # opponent weakness shrinks toward 1.0 with this many player-games
PRIOR_MIN = 300.0        # last season's rate counts like this many minutes
MIN_MIN = 18.0           # rotation player: recent minutes at least this
OPP_MIN_GAME = 15.0      # a player-game counts toward the opponent's weakness from here


def nf(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _rows(f):
    try:
        d = json.load(open(f))
    except (OSError, ValueError):
        return []
    return (d.get("data") if isinstance(d, dict) else None) or (d.get("payload", {}) or {}).get("data") or []


def load(season):
    """{pid: [row, ...]} with _d (date), _team, _opp added, oldest first."""
    out = {}
    for f in glob.glob(os.path.join(ROOT, "data_cache", f"gamelog_*_{season}.json")):
        pid = int(os.path.basename(f).split("_")[1])
        rows = []
        for r in _rows(f):
            if nf(r.get("MIN")) <= 0:
                continue
            m = r.get("MATCHUP", "")
            parts = m.replace(" vs. ", " @ ").split(" @ ")
            if len(parts) != 2:
                continue
            r = dict(r)
            r["_d"] = dt.datetime.strptime(r["GAME_DATE"], "%b %d, %Y").date()
            r["_team"], r["_opp"] = parts[0].strip(), parts[1].strip()
            rows.append(r)
        if rows:
            out[pid] = sorted(rows, key=lambda r: r["_d"])
    return out


_KIND = {}


def kind(pid):
    if pid not in _KIND:
        try:
            from engine import archetype as _arch
            a = _arch.of_player(pid)
            _KIND[pid] = str((a.get("archetype") if isinstance(a, dict) else a) or "?").lower()
        except Exception:
            _KIND[pid] = "?"
    return _KIND[pid]


CTX_ALPHA = 1.0          # weight of the team-context factor (0 = role numbers only)


def team_games():
    """[(date, team, {made}, {allowed})] from the journal's team box stats."""
    out = []
    for f in sorted(glob.glob(os.path.join(ROOT, "journal", "2026-1*.json"))):
        try:
            d = json.load(open(f))
        except (OSError, ValueError):
            continue
        day = dt.date.fromisoformat(d.get("date"))
        for g in d.get("games", []):
            if g.get("missing") or not g.get("tw") or not g.get("tl"):
                continue
            out.append((day, g["winner"], g["tw"], g["tl"]))
            out.append((day, g["loser"], g["tl"], g["tw"]))
    return out


TG = None


def context(opp, stat, day):
    """How much this opponent's TEAM numbers leak the thing that feeds the stat,
    against the league, from games before tonight.
      REB: offensive boards they give up, and how often they miss (their FG%)
      PTS/AST: paint and fast-break points they give up (rim protection, transition)
    """
    global TG
    if TG is None:
        TG = team_games()
    rows = [(t, m, a) for d, t, m, a in TG if d < day]
    mine = [(m, a) for t, m, a in rows if t == opp]
    if len(mine) < 3 or len(rows) < 30:
        return 1.0
    def avg(xs, side, k):
        v = [(x[side].get(k) or 0) for x in xs]
        return st.mean(v) if v else 0
    lg = [(m, a) for t, m, a in rows]
    if stat == "REB":
        r1 = avg(mine, 1, "oreb") / (avg(lg, 1, "oreb") or 1)
        miss = (1 - (avg(mine, 0, "fg_pct") or 0.47)) / (1 - (avg(lg, 0, "fg_pct") or 0.47))
        f = (r1 + miss) / 2
    else:
        r1 = avg(mine, 1, "paint") / (avg(lg, 1, "paint") or 1)
        r2 = avg(mine, 1, "fastbreak") / (avg(lg, 1, "fastbreak") or 1)
        f = (r1 + r2) / 2
    n = len(mine)
    return (f * n + 1.0 * 6) / (n + 6)       # shrink: 6 games of prior at 1.0


class Book:
    """Point-in-time numbers from the logs, for one night."""

    def __init__(self, L, prev, day):
        self.day = day
        self.before = {p: [r for r in rows if r["_d"] < day] for p, rows in L.items()}
        self.prev = prev
        self._rate = {}

    def rate(self, pid, stat):
        """Per-minute rate before tonight, last season as a 300-minute prior."""
        k = (pid, stat)
        if k not in self._rate:
            rows = self.before.get(pid, [])
            mins = sum(nf(r["MIN"]) for r in rows)
            tot = sum(nf(r[stat]) for r in rows)
            prow = self.prev.get(pid, [])
            pm = sum(nf(r["MIN"]) for r in prow)
            if pm > 0:
                prate = sum(nf(r[stat]) for r in prow) / pm
                w = min(PRIOR_MIN, pm)
                self._rate[k] = (tot + prate * w) / (mins + w) if mins + w else None
            else:
                self._rate[k] = tot / mins if mins >= 60 else None
        return self._rate[k]

    def recent_min(self, pid, n=5):
        rows = self.before.get(pid, [])[-n:]
        return st.mean(nf(r["MIN"]) for r in rows) if rows else 0.0

    def team_of(self, pid):
        rows = self.before.get(pid, [])
        return rows[-1]["_team"] if rows else None

    def weakness(self, opp, role, stat, _cache={}):
        k = (self.day, opp, role, stat)
        if k not in _cache:
            ratios = []
            for pid, rows in self.before.items():
                if kind(pid) != role:
                    continue
                rt = self.rate(pid, stat)
                if not rt:
                    continue
                for r in rows:
                    if r["_opp"] == opp and nf(r["MIN"]) >= OPP_MIN_GAME:
                        exp = rt * nf(r["MIN"])
                        if exp > 0.5:
                            ratios.append(nf(r[stat]) / exp)
            n = len(ratios)
            _cache[k] = ((sum(ratios) + PRIOR_GAMES) / (n + PRIOR_GAMES), n)
        return _cache[k]

    def rotation(self, team, min_avg=20.0):
        """Players who have been regulars for this team lately."""
        out = []
        for pid, rows in self.before.items():
            mine = [r for r in rows if r["_team"] == team][-5:]
            if len(mine) >= 2 and st.mean(nf(r["MIN"]) for r in mine) >= min_avg:
                out.append(pid)
        return out


def name_of(pid, L):
    try:
        from nba_api.stats.static import players
        p = players.find_player_by_id(pid)
        return p["full_name"] if p else str(pid)
    except Exception:
        return str(pid)


def edges(book, team, opp, playing=None, minutes=None):
    """Every (player, stat) edge for one side of a game.
    minutes: {pid: projected minutes} from tonight's slate (live mode)."""
    out = []
    pids = list(minutes) if minutes else (playing if playing is not None else book.rotation(team, MIN_MIN))
    for pid in pids:
        m = minutes[pid] if minutes else book.recent_min(pid)
        if m < MIN_MIN:
            continue
        role = kind(pid)
        if role not in ("guard", "wing", "big"):
            continue
        for s in STATS:
            rt = book.rate(pid, s)
            if not rt:
                continue
            proj = rt * m
            if proj < STRENGTH[s]:
                continue
            w, n = book.weakness(opp, role, s)
            c = context(opp, s, book.day)
            weak = w * (c ** CTX_ALPHA)
            out.append({"pid": pid, "team": team, "opp": opp, "role": role, "stat": s,
                        "proj": proj, "min": m, "weak": weak, "role_weak": w, "ctx": c, "n": n,
                        "edge": proj * (weak - 1) / SPREAD[s]})
    return out


def nights_games(L, day):
    """{game_id: {team: [pid,...]}} for a past night, from who actually played."""
    g = defaultdict(lambda: defaultdict(list))
    for pid, rows in L.items():
        for r in rows:
            if r["_d"] == day:
                g[r["Game_ID"]][r["_team"]].append(pid)
    return g


def actual(L, pid, day, stat):
    for r in L.get(pid, []):
        if r["_d"] == day:
            return nf(r[stat]), nf(r["MIN"])
    return None, None


def missing(book, team, playing):
    rot = book.rotation(team, 20.0)
    return [p for p in rot if p not in set(playing)]


def backtest(start, end, top=1):
    L, P = load("2026-27"), load("2025-26")
    day = start
    picked, rest = [], []
    while day <= end:
        games = nights_games(L, day)
        if games:
            book = Book(L, P, day)
            for gid, sides in games.items():
                teams = list(sides)
                if len(teams) != 2:
                    continue
                cand = []
                for t, o in ((teams[0], teams[1]), (teams[1], teams[0])):
                    cand += edges(book, t, o, playing=sides[t])
                cand.sort(key=lambda e: -e["edge"])
                for i, e in enumerate(cand):
                    a, amin = actual(L, e["pid"], day, e["stat"])
                    if a is None:
                        continue
                    e["actual"], e["amin"], e["day"] = a, amin, day
                    (picked if i < top else rest).append(e)
        day += dt.timedelta(days=1)
    return picked, rest


def summary(rows):
    if not rows:
        return "n=0"
    beat = sum(r["actual"] > r["proj"] for r in rows) / len(rows)
    ceil = sum(r["actual"] >= 1.3 * r["proj"] for r in rows) / len(rows)
    ratio = st.mean(r["actual"] / r["proj"] for r in rows)
    permin = st.mean((r["actual"] / r["amin"]) / (r["proj"] / r["min"]) for r in rows if r["amin"])
    return (f"n={len(rows):3d}  beat proj {beat:5.1%}  ceiling (>=1.3x) {ceil:5.1%}  "
            f"actual/proj {ratio:.2f}  per-minute {permin:.2f}")


def live(day, book, L, top):
    """Tonight, from journal/slate/<date>.json (its minutes already account for outs)."""
    path = os.path.join(ROOT, "journal", "slate", f"{day.isoformat()}.json")
    if not os.path.exists(path):
        sys.exit(f"no slate at {path}: run 'Project these games again' in the Journal first")
    slate = json.load(open(path))
    alias = {"WSH": "WAS", "NO": "NOP", "SA": "SAS", "UTAH": "UTA", "GS": "GSW", "NY": "NYK"}
    for g in slate.get("games", []):
        A, H = (alias.get(g["away"], g["away"]), alias.get(g["home"], g["home"]))
        cand = []
        for t, o in ((A, H), (H, A)):
            plist = g.get("players", {}).get(t) or g.get("players", {}).get(g["away"] if t == A else g["home"]) or []
            mins = {p["pid"]: float(p.get("min") or 0) for p in plist if p.get("pid")}
            cand += edges(book, t, o, minutes=mins)
        cand.sort(key=lambda e: -e["edge"])
        print(f"\n{A} @ {H}")
        outs = g.get("out", {})
        for t in outs:
            if outs[t]:
                print(f"  {t} out: " + ", ".join(outs[t]))
        for e in cand[:top]:
            print(f"  {name_of(e['pid'], L):24} {e['stat']:3} proj {e['proj']:4.1f} ({e['min']:.0f} min)  "
                  f"{e['opp']} gives {e['role']}s {e['role_weak']:.2f}x (n={e['n']}), team context {e['ctx']:.2f}x"
                  f"  edge {e['edge']:+.2f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("date", nargs="?")
    ap.add_argument("--played", action="store_true", help="past night: use who actually played")
    ap.add_argument("--backtest", nargs=2, metavar=("START", "END"))
    ap.add_argument("--top", type=int, default=2)
    ap.add_argument("--alpha", type=float, help="context weight for --backtest (default CTX_ALPHA)")
    a = ap.parse_args()
    if a.backtest:
        s, e = (dt.date.fromisoformat(x) for x in a.backtest)
        global CTX_ALPHA
        for alpha in ((CTX_ALPHA,) if a.alpha is None else (a.alpha,)):
          CTX_ALPHA = alpha
          print(f"context weight {alpha}")
          for top in (1, 2):
            pk, rest = backtest(s, e, top)
            print(f"top {top} per game: {summary(pk)}")
            print(f"   everyone else:  {summary(rest)}")
        return
    day = dt.date.fromisoformat(a.date)
    L, P = load("2026-27"), load("2025-26")
    book = Book(L, P, day)
    if not a.played:
        return live(day, book, L, a.top)
    games = nights_games(L, day)
    for gid, sides in games.items():
        teams = list(sides)
        if len(teams) != 2:
            continue
        cand = []
        for t, o in ((teams[0], teams[1]), (teams[1], teams[0])):
            cand += edges(book, t, o, playing=sides[t])
        cand.sort(key=lambda e: -e["edge"])
        print(f"\n{teams[0]} v {teams[1]}")
        for t in teams:
            miss = missing(book, t, sides[t])
            if miss:
                print(f"  {t} missing: " + ", ".join(f"{name_of(p, L)} ({kind(p)})" for p in miss))
        for e in cand[:a.top]:
            act, _ = actual(L, e["pid"], day, e["stat"])
            print(f"  {name_of(e['pid'], L):24} {e['stat']:3} proj {e['proj']:4.1f}  {e['opp']} gives {e['role']}s "
                  f"{e['role_weak']:.2f}x (n={e['n']}), team context {e['ctx']:.2f}x  edge {e['edge']:+.2f}" + (f"  -> actual {act:.0f}" if act is not None else ""))


if __name__ == "__main__":
    main()
