#!/usr/bin/env python3
"""Full box score for every game on a slate, from the roster that will play.

    python3 tools/box_score.py --unavailable slate_lines/availability_20261031.json
    python3 tools/box_score.py --unavailable ... --game OKC@HOU --json out.json

HOW A LINE IS BUILT (the 31 Oct 2026 method, in order)

  1. WHO IS OUT decides which past games count. Every past game, this
     season and last, is scored by how much of tonight's missing minutes
     were missing then too (tools/roster_state.py): a MATCH (>=80%) is a
     game by the team that will take the floor, a PARTIAL (>=30%) is
     half-way, and a FULL-strength game is weak evidence about a team
     that will not play. Weights 1.0 / 0.6 / 0.35.

     Last season counts only for absentees who were on this roster then
     (games with this team) or who have no games at all that season --
     VanVleet missed all of 2025-26 on the same Houston roster, so every
     Houston game last season is a MATCH for a night he is out; Morant was
     in Memphis, so Portland's games without him last season say nothing.

  2. MINUTES stay current-season. Last season's minutes describe last
     season's job (Camara 33 then, 25 now). Current rows, state-weighted,
     when there are three; otherwise last season's, flagged.

  3. RATES per 36 are blended across both seasons, current rows at their
     state weight and last season worth at most 12 games scaled by ROLE
     SIMILARITY (engine/role_blend.py) -- same minutes and same shots per
     minute is the same job; a bench man turned starter keeps little of
     his old season.

  4. ATTEMPTS are predictable, EFFICIENCY is not. Points come from
     FGA / 3PA / FTA at shrunk percentages -- the player's own make rates
     pulled toward the league (2P% K=150, 3P% K=150, FT% K=100 attempts).
     A hot five games stays a hot five games.

  5. MINUTES HAVE A CEILING. The roster is filled to 240 by scaling the
     players who are not yet at 36, then capping, then repeating: a short
     bench pushes surplus DOWN the rotation, never onto the stars
     (the 42-minute SGA mistake).

  6. OPPONENT and HOME COURT scale scoring columns: the opponent's points
     allowed per game this season, shrunk toward league with K=10 games,
     and +/-1.5% for home court. Rebounds are left alone.

WHAT IT DOES NOT KNOW
Who guards whom (the point-of-attack layer is a note, not a number),
minutes restrictions, rookies with no games, and blowouts -- a 20-point
game ends the starters' night at 30 minutes, and the method cannot see
that coming. Read the matchup reads printed under each team beside the
numbers, never instead of them.
"""
import argparse
import json
import os
import statistics as st
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.cache import read_payload                      # noqa: E402
from engine.team_ids import TEAM_ID_BY_ABBR                # noqa: E402
from engine.role_blend import role_similarity, PRIOR_W     # noqa: E402
from engine import matchup as mx                           # noqa: E402

CUR, PREV = "2026-27", "2025-26"
STATE_W = {"match": 1.0, "partial": 0.6, "full": 0.35}
CAP, TEAM_MIN = 36.0, 240.0
MIN_MPG = 8.0
MINUTES_MIN_GAMES = 3
K2, K3, KFT = 150.0, 150.0, 100.0
K_TEAM = 10.0
HOME = 0.015
COLS = ("PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV", "FGA", "FTA")


def nf(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def roster(abbr):
    return (read_payload(f"roster_{TEAM_ID_BY_ABBR[abbr]}") or {}).get("data") or []


def log(pid, season):
    return (read_payload(f"gamelog_{pid}_{season}") or {}).get("data") or []


def team_of_row(row):
    return (row.get("MATCHUP") or "")[:3]


def mpg(rows):
    return st.mean([nf(r.get("MIN")) for r in rows]) if rows else 0.0


def rate36(rows, col, w=None):
    w = w or [1.0] * len(rows)
    m = sum(wi * nf(r.get("MIN")) for wi, r in zip(w, rows))
    return sum(wi * nf(r.get(col)) for wi, r in zip(w, rows)) / m * 36 if m else 0.0


# ---------------------------------------------------------------- league
def league_rates():
    """Minutes-weighted make rates and points per game, both seasons."""
    tot = defaultdict(float)
    team_pts = defaultdict(lambda: defaultdict(float))     # season -> (team, date) -> pts
    for abbr in TEAM_ID_BY_ABBR:
        for row in roster(abbr):
            for season in (CUR, PREV):
                for g in log(int(row["PLAYER_ID"]), season):
                    for k in ("FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA"):
                        tot[k] += nf(g.get(k))
                    team_pts[season][(team_of_row(g), g.get("GAME_DATE"))] += nf(g.get("PTS"))
    two_m, two_a = tot["FGM"] - tot["FG3M"], tot["FGA"] - tot["FG3A"]
    rates = {"P2": two_m / two_a if two_a else 0.54,
             "P3": tot["FG3M"] / tot["FG3A"] if tot["FG3A"] else 0.36,
             "FT": tot["FTM"] / tot["FTA"] if tot["FTA"] else 0.78}
    return rates, team_pts


def team_strength(team_pts, abbr):
    """(scored, allowed) per game this season as ratios to league, shrunk."""
    cur = team_pts[CUR]
    games = {d: p for (t, d), p in cur.items() if t == abbr}
    if not games:
        return 1.0, 1.0, 0
    league = st.mean(cur.values())
    # points allowed: the other team's total on the same date is not keyed
    # by opponent here, so read the opponent from any player's MATCHUP
    allowed = []
    for row in roster(abbr):
        for g in log(int(row["PLAYER_ID"]), CUR):
            m = g.get("MATCHUP") or ""
            opp = m[-3:]
            d = g.get("GAME_DATE")
            if (opp, d) in cur:
                allowed.append((d, cur[(opp, d)]))
    allowed = dict(allowed)
    n = len(games)
    for_r = st.mean(games.values()) / league
    all_r = st.mean(allowed.values()) / league if allowed else 1.0
    shrink = n / (n + K_TEAM)
    return 1 + (for_r - 1) * shrink, 1 + (all_r - 1) * shrink, n


# ---------------------------------------------------------------- states
def absentees(feed, abbr):
    out = {}
    for game in feed.get("games", []):
        for row in game.get("out", []):
            if row.get("team") == abbr and row.get("player_id"):
                out[int(row["player_id"])] = row.get("nba_name") or row.get("name")
    return out


def state_by_date(abbr, season, out_ids, weights):
    """game_date -> match | partial | full for one team-season."""
    games = {}
    for row in roster(abbr):
        pid = int(row["PLAYER_ID"])
        for g in log(pid, season):
            if team_of_row(g) == abbr:
                games.setdefault(g.get("GAME_DATE"), set()).add(pid)
    total = sum(weights.get(p, 0.0) for p in out_ids) or 0.0
    verdict = {}
    for date, played in games.items():
        if not total:
            verdict[date] = "full"
            continue
        gone = sum(weights.get(p, 0.0) for p in out_ids if p not in played)
        share = gone / total
        verdict[date] = "match" if share >= 0.80 else ("partial" if share >= 0.30 else "full")
    return verdict


def prior_absentees(abbr, out_ids):
    """Absentees who count for LAST season: on this roster then, or with
    no games at all that season (injured all year, or a rookie)."""
    keep = {}
    for pid in out_ids:
        rows = log(pid, PREV)
        here = [r for r in rows if team_of_row(r) == abbr]
        if not rows or here:
            keep[pid] = mpg(here) if here else mpg(log(pid, CUR))
    return keep


# ---------------------------------------------------------------- players
def project_team(abbr, out_ids, league, opp_allowed, home):
    cur_w = {p: mpg(log(p, CUR)) or mpg(log(p, PREV)) for p in out_ids}
    v_cur = state_by_date(abbr, CUR, out_ids, cur_w)
    prev_ids = prior_absentees(abbr, out_ids)
    v_prev = state_by_date(abbr, PREV, set(prev_ids), prev_ids)
    counts = {s: sum(1 for v in v_cur.values() if v == s) for s in STATE_W}
    counts_prev = {s: sum(1 for v in v_prev.values() if v == s) for s in STATE_W}

    players = []
    for row in roster(abbr):
        pid = int(row["PLAYER_ID"])
        if pid in out_ids:
            continue
        cur = [g for g in log(pid, CUR) if team_of_row(g) == abbr]
        prev = [g for g in log(pid, PREV) if team_of_row(g) == abbr]
        if not cur and not prev:
            continue
        w_cur = [STATE_W[v_cur.get(g.get("GAME_DATE"), "full")] for g in cur]
        w_prev_state = [STATE_W[v_prev.get(g.get("GAME_DATE"), "full")] for g in prev]

        # minutes: current season, state-weighted
        if len(cur) >= MINUTES_MIN_GAMES:
            minutes = sum(w * nf(g.get("MIN")) for w, g in zip(w_cur, cur)) / sum(w_cur)
            minutes_src = "cur"
        elif cur and prev:
            minutes = (mpg(cur) + mpg(prev)) / 2
            minutes_src = "mixed"
        elif cur:
            minutes, minutes_src = mpg(cur), "cur-thin"
        else:
            minutes, minutes_src = mpg(prev), "prev"

        # similarity of the job across seasons
        if cur and prev:
            sim = role_similarity(mpg(cur), mpg(prev), rate36(cur, "FGA"), rate36(prev, "FGA"))
        else:
            sim = 1.0
        prior_total = PRIOR_W * sim
        s_prev = sum(w_prev_state) or 1.0
        w_prev = [w * prior_total / s_prev for w in w_prev_state] if prev else []

        rows = cur + prev
        w = w_cur + w_prev
        if not rows or sum(w) == 0:
            continue

        def r36(col):
            return rate36(rows, col, w)

        def tot(col):
            return sum(wi * nf(g.get(col)) for wi, g in zip(w, rows))

        fga, f3a, fta = r36("FGA"), r36("FG3A"), r36("FTA")
        two_a = tot("FGA") - tot("FG3A")
        p2 = ((tot("FGM") - tot("FG3M")) + K2 * league["P2"]) / (two_a + K2)
        p3 = (tot("FG3M") + K3 * league["P3"]) / (tot("FG3A") + K3)
        ft = (tot("FTM") + KFT * league["FT"]) / (tot("FTA") + KFT)
        per36 = {
            "FGA": fga, "FG3A": f3a, "FTA": fta,
            "FG3M": f3a * p3,
            "PTS": (fga - f3a) * p2 * 2 + f3a * p3 * 3 + fta * ft,
            "REB": r36("REB"), "AST": r36("AST"), "STL": r36("STL"),
            "BLK": r36("BLK"), "TOV": r36("TOV"),
        }
        n_match = sum(1 for g in cur if v_cur.get(g.get("GAME_DATE")) == "match")
        n_match_prev = sum(1 for g in prev if v_prev.get(g.get("GAME_DATE")) == "match")
        players.append({
            "pid": pid, "name": row["PLAYER"], "pos": row.get("POSITION"),
            "mpg": minutes, "minutes_src": minutes_src, "sim": sim,
            "n_cur": len(cur), "n_prev": len(prev), "n_match": n_match,
            "n_match_prev": n_match_prev, "w_cur": sum(w_cur), "w_prev": sum(w_prev),
            "per36": per36,
        })

    # minutes allocation: cap and push down the bench
    use = sorted([p for p in players if p["mpg"] >= MIN_MPG], key=lambda p: -p["mpg"])
    # an eleven-man rotation at most; beyond that the end of the bench sits.
    # The bench is NOT trimmed to make the sum fit -- that hands its minutes
    # to the starters, whose rates are higher, and inflates every total.
    use = use[:11]
    mins = {p["pid"]: p["mpg"] for p in use}
    for _ in range(14):
        total = sum(mins.values())
        if abs(total - TEAM_MIN) < 0.1:
            break
        capped = {k for k, v in mins.items() if v >= CAP - 1e-6}
        free = [k for k in mins if k not in capped]
        room = TEAM_MIN - CAP * len(capped)
        if not free or room <= 0:
            break
        k = room / sum(mins[f] for f in free)
        for f in free:
            mins[f] = min(mins[f] * k, CAP)
        for c in capped:
            mins[c] = CAP

    scale = opp_allowed * (1 + HOME if home else 1 - HOME)
    out = []
    for p in use:
        m = mins[p["pid"]]
        line = {"min": m}
        for col in ("PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV", "FGA", "FTA"):
            v = p["per36"][col] * m / 36
            if col in ("PTS", "AST", "FG3M", "FGA", "FTA", "TOV"):
                v *= scale
            line[col] = v
        out.append(dict(p, line=line))
    out.sort(key=lambda p: -p["line"]["min"])
    return out, {"cur": counts, "prev": counts_prev, "scale": scale}


# ---------------------------------------------------------------- print
def fmt_team(abbr, players, meta, out_names, opp, exclude):
    c, cp = meta["cur"], meta["prev"]
    print(f"--- {abbr}   out: {', '.join(sorted(out_names)) or 'nobody'}")
    print(f"    this season: {c['match']} match / {c['partial']} partial / {c['full']} full   "
          f"last season: {cp['match']} match / {cp['partial']} partial / {cp['full']} full   "
          f"scoring x{meta['scale']:.3f}")
    print(f"    {'player':24s}{'min':>4s}{'pts':>5s}{'reb':>5s}{'ast':>5s}{'3pm':>5s}"
          f"{'stl':>4s}{'blk':>4s}{'tov':>4s}{'fga':>5s}{'fta':>5s}   evidence")
    tot = defaultdict(float)
    for p in players:
        l = p["line"]
        ev = f"m{p['n_match']}/{p['n_cur']}"
        if p["n_prev"]:
            ev += f" +prev{p['n_match_prev']}/{p['n_prev']}@{p['w_prev']:.0f}"
        if p["minutes_src"] != "cur":
            ev += f" min:{p['minutes_src']}"
        print(f"    {p['name']:24s}{l['min']:4.0f}{l['PTS']:5.1f}{l['REB']:5.1f}{l['AST']:5.1f}"
              f"{l['FG3M']:5.1f}{l['STL']:4.1f}{l['BLK']:4.1f}{l['TOV']:4.1f}{l['FGA']:5.1f}"
              f"{l['FTA']:5.1f}   {ev}")
        for k, v in l.items():
            tot[k] += v
    print(f"    {'TEAM':24s}{tot['min']:4.0f}{tot['PTS']:5.1f}{tot['REB']:5.1f}{tot['AST']:5.1f}"
          f"{tot['FG3M']:5.1f}{tot['STL']:4.1f}{tot['BLK']:4.1f}{tot['TOV']:4.1f}{tot['FGA']:5.1f}"
          f"{tot['FTA']:5.1f}")
    # matchup reads for the top scorers
    reads = []
    for p in players[:6]:
        try:
            r = mx.read(p["pid"], opp, CUR, exclude=exclude)
        except Exception:
            r = None
        if r and r.get("edge") in ("with", "against"):
            reads.append(f"{'▲' if r['edge'] == 'with' else '▼'} {p['name']}: {r['verdict']}")
    for r in reads:
        print(f"      {r}")
    print()
    return tot


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--unavailable", required=True)
    ap.add_argument("--game", help="e.g. OKC@HOU; default every game in the file")
    ap.add_argument("--json", help="write every line here")
    args = ap.parse_args(argv)

    feed = json.load(open(args.unavailable))
    league, team_pts = league_rates()
    print(f"league make rates  2P {league['P2']:.3f}  3P {league['P3']:.3f}  FT {league['FT']:.3f}\n")
    dump = []
    for game in feed.get("games", []):
        away, home = game["away"], game["home"]
        if args.game and args.game.upper() != f"{away}@{home}":
            continue
        out_a, out_h = absentees(feed, away), absentees(feed, home)
        for_a, all_a, n_a = team_strength(team_pts, away)
        for_h, all_h, n_h = team_strength(team_pts, home)
        print(f"===== {away} @ {home}   "
              f"{away} scores x{for_a:.3f} allows x{all_a:.3f} ({n_a}g)   "
              f"{home} scores x{for_h:.3f} allows x{all_h:.3f} ({n_h}g)")
        pa, ma = project_team(away, set(out_a), league, all_h, home=False)
        ph, mh = project_team(home, set(out_h), league, all_a, home=True)
        ta = fmt_team(away, pa, ma, out_a.values(), home, exclude=set(out_h))
        th = fmt_team(home, ph, mh, out_h.values(), away, exclude=set(out_a))
        print(f"    MODEL  {away} {ta['PTS']:.0f} - {home} {th['PTS']:.0f}   "
              f"total {ta['PTS'] + th['PTS']:.0f}   "
              f"{home if th['PTS'] >= ta['PTS'] else away} by {abs(th['PTS'] - ta['PTS']):.1f}\n")
        dump.append({"away": away, "home": home,
                     "score": {away: ta["PTS"], home: th["PTS"]},
                     "players": {away: [dict(name=p["name"], pid=p["pid"], **p["line"]) for p in pa],
                                 home: [dict(name=p["name"], pid=p["pid"], **p["line"]) for p in ph]}})
    if args.json:
        json.dump(dump, open(args.json, "w"), indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
