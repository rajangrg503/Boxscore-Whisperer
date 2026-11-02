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

  7. WHO EATS (2 Nov 2026, tools/opponent_absence.py and who_eats.py).
     If the OTHER team's key G, F or C (most minutes per game this
     season at that position) is out tonight, our players' rates move
     by what 1,405 games measured, by their own position group: their C
     out puts our C at +11% reb and pts and our G at +9% ast; their G
     out puts our F at +10% ast. The table is OPP_LIFT. Lifts from more
     than one absent key man multiply, capped at MAX_LIFT.
     On the second night of a BACK-TO-BACK, starters (28+ mpg) lose 2%
     of minutes before the roster is filled to 240; that is all the data
     supports -- their production is otherwise flat, and the minutes go
     down the bench, where the measured gain was.
     Our OWN absentees: the state weighting in step 1 reads the games
     this roster played without them. When a player has few such games
     (Podziemski had none without Curry and Draymond on 1 Nov 2026, then
     went 8 and 8), the measured per-minute shift for his position and
     role (OWN_RATE) is blended in, fully at zero matched games and not
     at all from OWN_FULL_AT. A key man is the most USAGE per game at his
     position, not the most minutes, and a player who has missed every
     game this season is not one: the team's games already lack him.
     The shift moves SHARE: the team's totals are rescaled back to what
     they were, so the players it names gain at their teammates' expense
     and an injury report cannot inflate a team's score.

WHAT IT DOES NOT KNOW
Who guards whom (the point-of-attack layer is a note, not a number),
minutes restrictions, rookies with no games, and blowouts -- a 20-point
game ends the starters' night at 30 minutes, and the method cannot see
that coming. Read the matchup reads printed under each team beside the
numbers, never instead of them.
"""
import argparse
import datetime
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
# their key man at position X is out -> our players at position Y:
# per-minute multipliers measured by tools/opponent_absence.py (2 Nov 2026)
OPP_LIFT = {
    "C": {"C": {"PTS": 1.112, "REB": 1.111, "AST": 1.061},
          "F": {"PTS": 1.060, "REB": 1.047, "AST": 1.067},
          "G": {"PTS": 1.045, "REB": 1.036, "AST": 1.101}},
    "F": {"C": {"PTS": 1.043, "REB": 1.020, "AST": 1.056},
          "F": {"PTS": 1.057, "REB": 1.032, "AST": 1.116},
          "G": {"PTS": 1.044, "REB": 1.017, "AST": 1.055}},
    "G": {"C": {"PTS": 1.068, "REB": 1.070, "AST": 1.070},
          "F": {"PTS": 1.057, "REB": 1.017, "AST": 1.114},
          "G": {"PTS": 1.026, "REB": 1.014, "AST": 1.060}},
}
# OUR key man at position X is out -> our players at position Y, by role:
# per-minute multipliers (tools/who_eats.py per-game change / minutes
# change, 2 Nov 2026). Minutes themselves are left to the 240-minute fill.
# Applied only as far as the player has few games without tonight's
# absentees: those games already carry the effect (see own_weight).
OWN_RATE = {
    "G": {"G": {"starter": {"PTS": 1.106, "REB": 1.090, "AST": 1.064},
                "rotation": {"PTS": 1.130, "REB": 0.990, "AST": 1.197}},
          "F": {"starter": {"PTS": 1.072, "REB": 1.020, "AST": 1.195},
                "rotation": {"PTS": 1.163, "REB": 0.985, "AST": 1.210}},
          "C": {"starter": {"PTS": 1.106, "REB": 1.034, "AST": 1.110},
                "rotation": {"PTS": 1.062, "REB": 1.026, "AST": 1.141}}},
    "F": {"G": {"starter": {"PTS": 1.129, "REB": 1.042, "AST": 1.044},
                "rotation": {"PTS": 1.082, "REB": 0.991, "AST": 1.076}},
          "F": {"starter": {"PTS": 1.133, "REB": 0.989, "AST": 1.214},
                "rotation": {"PTS": 1.230, "REB": 1.039, "AST": 1.173}},
          "C": {"starter": {"PTS": 1.053, "REB": 1.018, "AST": 0.914},
                "rotation": {"PTS": 1.045, "REB": 0.987, "AST": 0.957}}},
    "C": {"G": {"starter": {"PTS": 1.121, "REB": 1.004, "AST": 1.003},
                "rotation": {"PTS": 0.989, "REB": 0.996, "AST": 1.056}},
          "F": {"starter": {"PTS": 1.113, "REB": 1.068, "AST": 1.087},
                "rotation": {"PTS": 1.184, "REB": 1.028, "AST": 1.181}},
          # too few starting centres behind an absent key centre to
          # measure; the backup's numbers stand in, assists capped
          "C": {"starter": {"PTS": 1.062, "REB": 1.011, "AST": 1.200},
                "rotation": {"PTS": 1.062, "REB": 1.011, "AST": 1.200}}},
}
MAX_OWN = 1.25
OWN_FULL_AT = 5      # matched games at which the measured shift is fully in the data
ROTATION_MPG = 18.0
MAX_LIFT = 1.15
B2B_STARTER_MIN = 0.98       # who_eats.py: starters -2% minutes, production flat
STARTER_MPG = 28.0
KEY_MIN_GAMES = 3
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


# ---------------------------------------------------------------- who eats
def pos_group(pos):
    pos = (pos or "").upper()
    if pos.startswith("C") or pos == "F-C":
        return "C"
    if pos.startswith("F"):
        return "F"
    if pos.startswith("G"):
        return "G"
    return None


def usage_pg(rows):
    if not rows:
        return 0.0
    return sum(nf(r.get("FGA")) + 0.44 * nf(r.get("FTA")) + nf(r.get("TOV")) + nf(r.get("AST"))
               for r in rows) / len(rows)


def key_players(abbr):
    """{group: pid}: the most usage per game at each position group for
    this team -- this season's games with the team when he has three,
    else last season's with it (ten or more). Nobody who has missed every
    game this season while the team has played four or more."""
    team_games = len({g.get("GAME_DATE") for row in roster(abbr)
                      for g in log(int(row["PLAYER_ID"]), CUR) if team_of_row(g) == abbr})
    best = {}
    for row in roster(abbr):
        g = pos_group(row.get("POSITION"))
        if not g:
            continue
        pid = int(row["PLAYER_ID"])
        cur = [r for r in log(pid, CUR) if team_of_row(r) == abbr]
        prev = [r for r in log(pid, PREV) if team_of_row(r) == abbr]
        if len(cur) >= KEY_MIN_GAMES:
            u = usage_pg(cur)
        elif not cur and team_games >= 4:
            continue
        elif len(prev) >= 10:
            u = usage_pg(prev)
        else:
            continue
        if g not in best or u > best[g][1]:
            best[g] = (pid, u)
    return {g: v[0] for g, v in best.items()}


def opp_keys_out(opp, opp_out_ids):
    """Position groups whose key man on the other team is out tonight."""
    return sorted(g for g, pid in key_players(opp).items() if pid in opp_out_ids)


def lift_for(pos, keys_out):
    """{col: multiplier} for one of our players, from the opponent's absences."""
    g = pos_group(pos)
    lift = {"PTS": 1.0, "REB": 1.0, "AST": 1.0}
    if not g:
        return lift
    for x in keys_out:
        for col, v in OPP_LIFT[x][g].items():
            lift[col] *= v
    return {c: min(v, MAX_LIFT) for c, v in lift.items()}


def role_of(minutes):
    if minutes >= STARTER_MPG:
        return "starter"
    return "rotation" if minutes >= ROTATION_MPG else None


def own_weight(n_matched):
    """1.0 with no games without tonight's absentees, 0 from OWN_FULL_AT."""
    return max(0.0, 1.0 - n_matched / OWN_FULL_AT)


def own_lift(pos, minutes, own_out, n_matched):
    """{col: multiplier} from OUR absent key men, for one player."""
    g, r = pos_group(pos), role_of(minutes)
    lift = {"PTS": 1.0, "REB": 1.0, "AST": 1.0}
    w = own_weight(n_matched)
    if not g or not r or not w:
        return lift
    for x in own_out:
        for col, v in OWN_RATE[x][g][r].items():
            lift[col] *= v
    return {c: min(1 + w * (v - 1), MAX_OWN) for c, v in lift.items()}


def us_date_of(game, feed=None):
    """The game's US calendar date: tip time minus five hours, else the
    feed's date. None when neither is known."""
    tip = game.get("tipoff")
    if tip:
        try:
            t = datetime.datetime.fromisoformat(str(tip).replace("Z", "+00:00"))
            return (t - datetime.timedelta(hours=5)).date()
        except ValueError:
            pass
    d = str((feed or {}).get("date") or "")[:8]
    try:
        return datetime.datetime.strptime(d, "%Y%m%d").date()
    except ValueError:
        return None


def played_day_before(abbr, day):
    """True when the cache shows this team played the US day before."""
    if not day:
        return False
    want = (day - datetime.timedelta(days=1)).strftime("%b %d, %Y")
    for row in roster(abbr):
        for g in log(int(row["PLAYER_ID"]), CUR):
            if team_of_row(g) == abbr and g.get("GAME_DATE") == want:
                return True
    return False


# What the other side's absences and a back-to-back do, in words, for the
# Journal's Tonight tab. Same measured lifts as OPP_LIFT, rounded.
OWN_NAMES = {"G": "guard", "F": "forward", "C": "centre"}
WHO_EATS_TEXT = {
    "C": "{opp}'s main centre is out, so {team}'s bigs get about 11% more rebounds and points "
         "and its guards about 9% more assists.",
    "F": "{opp}'s main forward is out, so {team}'s forwards get about 7% more assists and 5% "
         "more points.",
    "G": "{opp}'s main guard is out, so {team}'s forwards get about 10% more assists and 6% "
         "more points.",
}


def who_eats_notes(game):
    """Plain-text lines for a projected game's 'context' block."""
    away, home = game["away"], game["home"]
    out = []
    for team, opp in ((away, home), (home, away)):
        ctx = (game.get("context") or {}).get(team) or {}
        for x in ctx.get("their_key_out") or []:
            if x in WHO_EATS_TEXT:
                out.append(WHO_EATS_TEXT[x].format(team=team, opp=opp))
        thin = [p["name"] for p in (game.get("players") or {}).get(team, [])
                if (p.get("lift") or {}) and (p.get("evidence") or {}).get("match", 0) < OWN_FULL_AT
                and ctx.get("own_key_out")]
        if ctx.get("own_key_out") and thin:
            names = " and ".join(OWN_NAMES[x] for x in ctx["own_key_out"])
            who = ", ".join(thin[:4]) + (" and others" if len(thin) > 4 else "")
            out.append(f"{team} is missing its main {names} and has few games without "
                       f"{'them' if len(ctx['own_key_out']) > 1 else 'him'}, so the measured "
                       f"shift is added for {who}" + ("" if who.endswith(".") else "."))
        if ctx.get("back_to_back"):
            out.append(f"{team} played last night: its starters lose a couple of minutes and "
                       "the bench picks them up. Not a reason to bet starters under.")
    return out


# ---------------------------------------------------------------- players
def project_team(abbr, out_ids, league, opp_allowed, home, keys_out=(), b2b=False):
    own_out = sorted(g for g, pid in key_players(abbr).items() if pid in out_ids)
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
    if b2b:
        for p in players:
            if p["mpg"] >= STARTER_MPG:
                p["mpg"] *= B2B_STARTER_MIN
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
        lift = lift_for(p["pos"], keys_out)
        mine = own_lift(p["pos"], p["mpg"], own_out, p["n_match"] + p["n_match_prev"])
        lift = {c: lift[c] * mine[c] for c in lift}
        line = {"min": m}
        for col in ("PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV", "FGA", "FTA"):
            v = p["per36"][col] * m / 36
            if col in ("PTS", "AST", "FG3M", "FGA", "FTA", "TOV"):
                v *= scale
            # attempts and makes move with points: more shots, not better ones
            v *= lift["PTS"] if col in ("PTS", "FG3M", "FGA", "FTA") else lift.get(col, 1.0)
            line[col] = v
        out.append(dict(p, line=line, lift={c: round(v, 3) for c, v in lift.items() if v != 1.0}))
    # Our own absences change WHO gets the shots, not how many the team
    # takes: the boxes are rescaled so the own-team shift moves share
    # between players and leaves the team's totals where they were.
    if own_out:
        for col, key in (("PTS", "PTS"), ("FG3M", "PTS"), ("FGA", "PTS"), ("FTA", "PTS"),
                         ("REB", "REB"), ("AST", "AST")):
            mine = [own_lift(p["pos"], p["mpg"], own_out, p["n_match"] + p["n_match_prev"])[key]
                    for p in out]
            total = sum(p["line"][col] for p in out)
            before = sum(p["line"][col] / m for p, m in zip(out, mine))
            if total:
                for p in out:
                    p["line"][col] *= before / total
    out.sort(key=lambda p: -p["line"]["min"])
    return out, {"cur": counts, "prev": counts_prev, "scale": scale,
                 "keys_out": list(keys_out), "own_out": own_out, "b2b": b2b}


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


# ---------------------------------------------------------------- for the Journal
LINE_COLS = ("PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV", "FGA", "FTA")


def matchup_reads(players, opp, exclude, top=6):
    """The ▲/▼ archetype reads for a team's top-minutes players."""
    reads = []
    for p in players[:top]:
        try:
            r = mx.read(p["pid"], opp, CUR, exclude=exclude)
        except Exception:                                   # noqa: BLE001
            r = None
        if r and r.get("edge") in ("with", "against"):
            reads.append({"name": p["name"], "edge": r["edge"], "verdict": r["verdict"]})
    return reads


def project_slate(feed, only=None):
    """Every game in an availability feed (tools/availability.py's shape),
    projected: {"games": [...], "league": {...}}. Each game carries both
    teams' lines, the projected score, who is out and day-to-day, and the
    matchup reads -- the same numbers main() prints, as data."""
    league, team_pts = league_rates()
    games = []
    for game in feed.get("games", []):
        away, home = game["away"], game["home"]
        if only and f"{away}@{home}" != only:
            continue
        out_a, out_h = absentees(feed, away), absentees(feed, home)
        _for_a, all_a, _ = team_strength(team_pts, away)
        _for_h, all_h, _ = team_strength(team_pts, home)
        day = us_date_of(game, feed)
        ka, kh = opp_keys_out(home, set(out_h)), opp_keys_out(away, set(out_a))
        ba, bh = played_day_before(away, day), played_day_before(home, day)
        pa, ma = project_team(away, set(out_a), league, all_h, home=False, keys_out=ka, b2b=ba)
        ph, mh = project_team(home, set(out_h), league, all_a, home=True, keys_out=kh, b2b=bh)
        dtd = {}
        for row in game.get("injuries") or []:
            if row.get("severity") != "out":
                dtd.setdefault(row.get("team"), []).append(
                    f"{row.get('nba_name') or row.get('name')} ({row.get('status')})")
        entry = {"away": away, "home": home, "tipoff": game.get("tipoff"),
                 "out": {away: sorted(out_a.values()), home: sorted(out_h.values())},
                 "day_to_day": {away: dtd.get(away, []), home: dtd.get(home, [])},
                 "players": {}, "score": {}, "reads": {}, "states": {},
                 "context": {away: {"their_key_out": ka, "back_to_back": ba,
                                    "own_key_out": ma["own_out"]},
                             home: {"their_key_out": kh, "back_to_back": bh,
                                    "own_key_out": mh["own_out"]}}}
        for team, opp, players, meta, exclude in ((away, home, pa, ma, set(out_h)),
                                                  (home, away, ph, mh, set(out_a))):
            entry["players"][team] = [
                dict({"name": p["name"], "pid": p["pid"], "min": round(p["line"]["min"], 1),
                      "evidence": {"match": p["n_match"], "games": p["n_cur"],
                                   "prev_weight": round(p["w_prev"], 1),
                                   "minutes_from": p["minutes_src"]},
                       "lift": p.get("lift") or {}},
                     **{c: round(p["line"][c], 1) for c in LINE_COLS})
                for p in players]
            entry["score"][team] = round(sum(p["line"]["PTS"] for p in players), 1)
            entry["reads"][team] = matchup_reads(players, opp, exclude)
            entry["states"][team] = meta["cur"]
        games.append(entry)
    return {"games": games,
            "league": {k: round(v, 3) for k, v in league.items()}}


def stale_players(team, pids, journal_dir, before):
    """Players whose stats cache is missing the team's most recent game.

    The cache refreshes from stats.nba.com, which times out on some
    players and not others (1 Nov 2026: half of them). The written
    Journal nights come from ESPN and do have that game, so they say who
    played in it: anyone who did and has no row for that date is being
    projected without it. Returns (date or None, [pid, ...])."""
    import glob as _glob
    for path in sorted(_glob.glob(os.path.join(journal_dir, "????-??-??.json")), reverse=True):
        day = os.path.basename(path)[:10]
        if day >= before:
            continue
        try:
            night = json.load(open(path))
        except (OSError, ValueError):
            continue
        for g in night.get("games") or []:
            if team not in (g.get("home"), g.get("away")):
                continue
            played = {m["pid"] for m in g.get("minutes") or []
                      if m.get("team") == team and m.get("played")}
            stamp = datetime.datetime.strptime(day, "%Y-%m-%d").strftime("%b %d, %Y")
            missing = [pid for pid in pids if pid in played and not any(
                (r.get("GAME_DATE") or "") == stamp for r in log(pid, CUR))]
            return day, missing
    return None, []


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
        day = us_date_of(game, feed)
        ka, kh = opp_keys_out(home, set(out_h)), opp_keys_out(away, set(out_a))
        ba, bh = played_day_before(away, day), played_day_before(home, day)
        for t, k, b in ((away, ka, ba), (home, kh, bh)):
            if k or b:
                print(f"    who eats  {t}: " + ", ".join(
                    [f"their key {x} is out" for x in k] + (["back-to-back"] if b else [])))
        pa, ma = project_team(away, set(out_a), league, all_h, home=False, keys_out=ka, b2b=ba)
        ph, mh = project_team(home, set(out_h), league, all_a, home=True, keys_out=kh, b2b=bh)
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
