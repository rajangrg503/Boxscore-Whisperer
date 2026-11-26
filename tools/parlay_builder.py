"""Parlay builder: the true chance and the expected return of any slip, and the
cheapest way to build the slips karma likes (ladder, longshot, same-game multi,
big ticket, bonus bet) -- measured against real Sportsbet prices, not assumed.

WHAT THE RESEARCH FOUND (26 Nov 2026: 23 saved nights, ~72,000 graded alt rungs and
~20,800 over/under lines; write-up in the project doc claude/parlay-builder-research-2026-11-26.md)
  * The price is the best estimate of a leg's chance. Our own leg features (form, H2H,
    creator rank, hit-rate gap, projection, injuries) did not improve on it in a
    walk-forward test, so this tool does NOT claim to find +EV legs.
  * What differs a lot is the bookmaker's cut by kind of leg: single-stat rungs at
    1.15-3.5 give up about 7-8% each, combo rungs (PRA/PR/PA/RA) 10-15%, rungs past
    4.0 from 13% up to 40%, over/under overs ~9% and unders ~3%.
  * The cut compounds with every leg, in same-game multis too: Sportsbet prices a
    different-player SGM at about 0.96 x the product of its legs (365 live quotes).
  * Legs on different players in one game are close to independent given the lines;
    scoring legs of TEAMMATES are slightly negative (one ball), so same-team stacks
    land less often than the price implies. Same-player legs are strongly positive
    and Sportsbet discounts them about fairly.
So the builder prices slips honestly (true chance, fair price, expected return per
$1) and picks the structure that gives up least: few legs, single stats, 1.5-3.5 a
leg, different teams, and promos (bonus bets, Bet Return) when offered. Within a
price band it breaks ties with the player's own record at the line -- a tie-breaker
karma can read, not a proven edge.

  python3 tools/parlay_builder.py calibrate            # refit from journal/lines (local file)
  python3 tools/parlay_builder.py 2026-11-27 ladder
  python3 tools/parlay_builder.py 2026-11-27 longshot --legs 3
  python3 tools/parlay_builder.py 2026-11-27 sgm --game MIA@NYK --legs 4
  python3 tools/parlay_builder.py 2026-11-27 big --target 500
  python3 tools/parlay_builder.py 2026-11-27 bonus
  python3 tools/parlay_builder.py 2026-11-27 check "Davion Mitchell PTS 15+; Luka Doncic AST 8+ @2.40" [--price 9.5]
  python3 tools/parlay_builder.py backtest

A leg in `check` is "<player> <STAT> <N>+" (alt rung), "<player> <STAT> o<line>" or
"u<line>"; add "@price" to override the saved price. It shows evidence; karma decides.
"""
import argparse
import collections
import datetime as dt
import glob
import itertools
import json
import math
import os
import random
import statistics as st
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import prop_context as pc  # noqa: E402

CAL_PATH = os.path.join(ROOT, "journal", "_cache", "parlay_calibration.json")
SAME_PATH = os.path.join(ROOT, "data", "parlay_same_player_corr.json")
SINGLE = ("PTS", "REB", "AST", "FG3M")
STAT_CLASS = {"PTS": "P", "REB": "R", "AST": "A", "FG3M": "T", "PR": "C", "PA": "C", "RA": "C", "PRA": "C"}
EDGES = [1.0, 1.04, 1.1, 1.2, 1.35, 1.5, 1.75, 2.1, 2.6, 3.5, 5, 8, 15, 60]
BONUS_VALUE = 0.70      # what $1 of bonus bet is worth, from the `bonus` screen (measured, not promised)
SGM_HAIRCUT = 0.96      # Sportsbet's price for a different-player SGM / product of its legs (365 quotes, 26 Nov)
_NORMAL = st.NormalDist()


# ---------------------------------------------------------------- nights and legs

def _resolver():
    idx = pc.name_index()

    def resolve(name):
        n = pc.norm(name)
        if n in idx:
            return idx[n]
        parts = n.split()
        if len(parts) >= 2 and len(parts[0]) == 1:      # "G Antetokounmpo"
            c = [k for k in idx if k[0] == parts[0] and k.split()[1:] == parts[1:]]
            if len(c) == 1:
                return idx[c[0]]
        c = [k for k in idx if k.replace(" ", "") == n.replace(" ", "")]
        return idx[c[0]] if len(c) == 1 else (None, None)
    return resolve


def _latest_rows(date):
    """Latest pre-tip row per (game, player, stat, kind, line) across the night's snapshots."""
    last = {}
    for f in sorted(glob.glob(os.path.join(ROOT, "journal", "lines", date, "*.json"))):
        try:
            snap = json.load(open(f))
            taken = dt.datetime.fromisoformat(snap["taken_at"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        for ev in snap.get("events", []):
            if ev.get("us_date") not in (None, date):
                continue
            try:
                if taken >= dt.datetime.fromisoformat(ev["start"]):
                    continue
            except (KeyError, ValueError):
                pass
            for r in ev.get("rows", []):
                last[(ev["game"], r["player"], r["stat"], r["kind"], r.get("line", r.get("threshold")))] = (ev["game"], r)
    return list(last.values())


def _slate_players(date):
    out = {}
    for g in (pc.slate_file(date) or {}).get("games", []):
        for team, players in g.get("players", {}).items():
            opp = g["home"] if team == g["away"] else g["away"]
            order = sorted(players, key=lambda p: -(p.get("PTS", 0) + p.get("AST", 0)))
            dtd = [pc.norm(x) for x in g.get("day_to_day", {}).get(team, [])]
            for p in players:
                out[p["pid"]] = {"team": team, "opp": opp, "crank": order.index(p) + 1, "pmin": p.get("min"),
                                 "outs": len(g.get("out", {}).get(team, [])),
                                 "dtd": any(pc.norm(p["name"]) in x for x in dtd)}
    return out


def leg_label(leg):
    if leg["kind"] == "alt":
        return f"{leg['player']} {leg['stat']} {leg['th']:g}+"
    return f"{leg['player']} {leg['stat']} {'o' if leg['kind'] == 'over' else 'u'}{leg['th']:g}"


def load_legs(date, with_actuals=False):
    """Every priced leg for a US date: alt rungs, and both sides of each over/under."""
    resolve = _resolver()
    slate = _slate_players(date)
    day = dt.date.fromisoformat(date)
    logs, legs = {}, []
    for game, r in _latest_rows(date):
        if r["stat"] not in STAT_CLASS:
            continue
        pid, roster_team = resolve(r["player"])
        if pid is None:
            continue
        if pid not in logs:
            rows = pc.rows_for(pid, [pc.prev_season(pc.season_of(day)), pc.season_of(day)])
            logs[pid] = ([x for x in rows if pc._date(x) < day], [x for x in rows if pc._date(x) == day])
        prior, today = logs[pid]
        s = slate.get(pid, {})
        base = {"date": date, "game": game, "player": r["player"], "pid": pid, "stat": r["stat"],
                "team": s.get("team") or roster_team, "opp": s.get("opp"), "crank": s.get("crank") or 99,
                "dtd": bool(s.get("dtd")), "outs": s.get("outs"),
                "min_l10": (st.mean(pc.nf(x["MIN"]) for x in prior[:10]) if prior else None)}
        actual = pc.value(today[0], r["stat"]) if (with_actuals and today) else None
        sides = ([("alt", r["threshold"], r.get("price"))] if r["kind"] == "alt"
                 else [("over", r["line"], r.get("over")), ("under", r["line"], r.get("under"))])
        for kind, th, price in sides:
            if not price or price <= 1.0:
                continue
            leg = dict(base, kind=kind, th=th, price=float(price))
            clears = (lambda v: v >= th) if kind == "alt" else (lambda v: v > th) if kind == "over" else (lambda v: v < th)
            season = [x for x in prior if pc.season_of(pc._date(x)) == pc.season_of(day)]
            leg["n_season"] = len(season)
            leg["hr_season"] = (sum(clears(pc.value(x, r["stat"])) for x in season) / len(season)) if season else None
            leg["hr_l8"] = (sum(clears(pc.value(x, r["stat"])) for x in prior[:8]) / len(prior[:8])) if prior else None
            leg["cls"] = ("ou_over" if kind == "over" else "ou_under" if kind == "under"
                          else "alt_single" if r["stat"] in SINGLE else "alt_combo")
            if actual is not None:
                leg["actual"] = actual
                leg["hit"] = (actual >= th if kind == "alt" else actual > th if kind == "over" else actual < th)
                leg["push"] = kind != "alt" and actual == th
            legs.append(leg)
    return legs


# ---------------------------------------------------------------- calibration

def _logit(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def fit_tables(legs, min_n=150):
    """Per leg class: [[implied chance, measured chance, n], ...] by price bucket, lightly smoothed."""
    tables = {}
    for cls in ("alt_single", "alt_combo", "ou_over", "ou_under"):
        rows = [x for x in legs if x["cls"] == cls and not x.get("push")]
        raw = []
        for lo, hi in zip(EDGES, EDGES[1:]):
            b = [x for x in rows if lo <= x["price"] < hi]
            if len(b) >= min_n:
                raw.append([sum(1 / x["price"] for x in b) / len(b), sum(x["hit"] for x in b) / len(b), len(b)])
        shift = [_logit(t) - _logit(i) for i, t, _ in raw]
        out = []
        for k, (imp, _, n) in enumerate(raw):       # smooth each bucket's shift with its neighbours, weighted by n
            js = [j for j in (k - 1, k, k + 1) if 0 <= j < len(raw)]
            w = [raw[j][2] * (2 if j == k else 1) for j in js]
            sm = sum(shift[j] * wj for j, wj in zip(js, w)) / sum(w)
            out.append([round(imp, 4), round(1 / (1 + math.exp(-(_logit(imp) + sm))), 4), n])
        tables[cls] = out
    return tables


def fair_prob(price, cls, cal):
    """The measured chance a leg at this price lands (the price with the bookmaker's cut taken out)."""
    table = (cal or {}).get("tables", {}).get(cls) or []
    x = _logit(1.0 / price)
    if not table:
        return 1 / (1 + math.exp(-(x - 0.2)))        # no calibration file: assume a ~9% cut at evens
    pts = sorted((_logit(i), _logit(t) - _logit(i)) for i, t, _ in table)
    if x <= pts[0][0]:
        s = pts[0][1]
    elif x >= pts[-1][0]:
        s = pts[-1][1]
    else:
        s = next(s0 + (s1 - s0) * (x - x0) / (x1 - x0) for (x0, s0), (x1, s1) in zip(pts, pts[1:]) if x0 <= x <= x1)
    return 1 / (1 + math.exp(-(x + s)))


def between_sums(rows, acc=None, cross_sample=0.15, seed=4):
    """Add one night's over/under overs (each with its calibrated chance 'p') to the pair sums."""
    acc = collections.defaultdict(lambda: [0.0, 0.0, 0]) if acc is None else acc
    rnd = random.Random(seed)
    rows = [x for x in rows if x["cls"] == "ou_over" and x.get("team") and "hit" in x]
    e = [x["hit"] - x["p"] for x in rows]
    phi = [math.exp(-_NORMAL.inv_cdf(min(max(x["p"], .01), .99)) ** 2 / 2) / math.sqrt(2 * math.pi) for x in rows]
    for i, a in enumerate(rows):
        for j in range(i + 1, len(rows)):
            b = rows[j]
            if a["pid"] == b["pid"]:
                continue
            cp = "".join(sorted((STAT_CLASS[a["stat"]], STAT_CLASS[b["stat"]])))
            tier = "top3" if a["crank"] <= 3 and b["crank"] <= 3 else "other"
            if a["game"] != b["game"]:
                if rnd.random() > cross_sample:
                    continue
                key = f"cross|{tier}|{cp}"
            else:
                key = f"{'mate' if a['team'] == b['team'] else 'opp'}|{tier}|{cp}"
            s = acc[key]
            s[0] += e[i] * e[j]
            s[1] += phi[i] * phi[j]
            s[2] += 1
    return acc


def between_from_sums(acc, min_n=2000, shrink=3000):
    """Latent correlation between two different players' overs in one game, GIVEN the lines, net of the
    same-night cross-game baseline (which absorbs any night the market ran hot or cold).
    Keys: 'mate|top3|PP', 'opp|other|CR' ... (teammates/opponents, both top-3 creators or not, stat classes)."""
    out = {}
    for key, (se, sp, n) in acc.items():
        rel, tier, cp = key.split("|")
        if rel == "cross" or n < min_n:
            continue
        base = acc.get(f"cross|{tier}|{cp}", [0, 0, 0])
        r = (se / n - (base[0] / base[2] if base[2] else 0.0)) / (sp / n)
        out[key] = round(max(-0.3, min(0.3, r * n / (n + shrink))), 3)
    return out


def fit_between(legs_by_night):
    acc = None
    for rows in legs_by_night.values():
        acc = between_sums(rows, acc)
    return between_from_sums(acc or {})


def graded_night(date):
    """A finished night's legs with results; cached locally once the night is two days old."""
    import pickle
    path = os.path.join(ROOT, "journal", "_cache", "parlay_nights", f"{date}.pkl")
    if os.path.exists(path):
        return pickle.load(open(path, "rb"))
    legs = [x for x in load_legs(date, with_actuals=True) if "hit" in x]
    if legs and dt.date.fromisoformat(date) <= dt.date.today() - dt.timedelta(days=3):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        pickle.dump(legs, open(path, "wb"))
    return legs


def line_nights():
    return sorted(d for d in os.listdir(os.path.join(ROOT, "journal", "lines")) if len(d) == 10)


def calibrate(dates=None, verbose=True):
    by_night = {}
    for d in dates or line_nights():
        legs = graded_night(d)
        if legs:
            by_night[d] = legs
    allegs = [x for v in by_night.values() for x in v]
    cal = {"fitted_on": sorted(by_night), "legs": len(allegs), "tables": fit_tables(allegs),
           "sgm_haircut": SGM_HAIRCUT}
    for x in allegs:
        x["p"] = fair_prob(x["price"], x["cls"], cal)
    cal["between"] = fit_between(by_night)
    os.makedirs(os.path.dirname(CAL_PATH), exist_ok=True)
    json.dump(cal, open(CAL_PATH, "w"), indent=1)
    if verbose:
        print(f"calibrated on {len(by_night)} nights, {len(allegs)} graded legs -> {os.path.relpath(CAL_PATH, ROOT)}")
        for cls, t in cal["tables"].items():
            print(" ", cls, " ".join(f"{1 / i:.2f}:{100 * (tr / i - 1):+.0f}%" for i, tr, _ in t))
    return cal


def load_cal():
    try:
        return json.load(open(CAL_PATH))
    except (OSError, ValueError):
        return {}


def load_same():
    try:
        d = json.load(open(SAME_PATH))
        return {tuple(k.split("|")): v for k, v in d["r"].items()}
    except (OSError, ValueError, KeyError):
        return {}


# ---------------------------------------------------------------- dependence and joint chance

def pair_r(a, b, cal=None, same=None):
    """Latent correlation between two legs' outcomes (0 = independent)."""
    if a["game"] != b["game"] or a["date"] != b["date"]:
        return 0.0
    sign = (-1 if a["kind"] == "under" else 1) * (-1 if b["kind"] == "under" else 1)
    if a["pid"] == b["pid"]:
        if a["stat"] == b["stat"]:
            return 0.98 * sign
        r = (same or {}).get(tuple(sorted((a["stat"], b["stat"]))), 0.3)
        return r * sign
    rel = "mate" if a.get("team") and a.get("team") == b.get("team") else "opp"
    tier = "top3" if a.get("crank", 99) <= 3 and b.get("crank", 99) <= 3 else "other"
    cp = "".join(sorted((STAT_CLASS[a["stat"]], STAT_CLASS[b["stat"]])))
    return sign * (cal or {}).get("between", {}).get(f"{rel}|{tier}|{cp}", 0.0)


def _corr(legs, cal, same):
    n = len(legs)
    c = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            c[i, j] = c[j, i] = pair_r(legs[i], legs[j], cal, same)
    w, v = np.linalg.eigh(c)
    if w.min() < 1e-6:                                # nearest valid correlation matrix
        c = (v * np.clip(w, 1e-6, None)) @ v.T
        d = np.sqrt(np.diag(c))
        c = c / d[:, None] / d[None, :]
    return c


def simulate(legs, probs, cal=None, same=None, n=200000, seed=0):
    """Boolean matrix (n draws x legs): which legs land in each simulated night (Gaussian copula)."""
    c = _corr(legs, cal, same)
    z = np.random.default_rng(seed).standard_normal((n, len(legs))) @ np.linalg.cholesky(c).T
    cut = np.array([-_NORMAL.inv_cdf(min(max(p, 1e-6), 1 - 1e-6)) for p in probs])
    return z > cut


def evaluate(legs, price=None, cal=None, same=None, stake="cash", bet_return=False, n=200000, seed=0):
    """True chance, fair price and expected return per $1 for a slip.
    price: the bookmaker's price for the whole slip (default: product of leg prices, x the SGM haircut
    for each game with 2+ legs). stake='bonus': stake not returned. bet_return: stake back as a bonus
    bet when exactly one leg misses."""
    probs = [fair_prob(x["price"], x["cls"], cal) for x in legs]
    hits = simulate(legs, probs, cal, same, n=n, seed=seed)
    k = hits.sum(1)
    p_all = float((k == len(legs)).mean())
    p_one = float((k == len(legs) - 1).mean())
    product = float(np.prod([x["price"] for x in legs]))
    est = price is None
    if est:
        price = est_price(legs, cal, same)
    ret = p_all * (price - 1 if stake == "bonus" else price)
    if bet_return and len(legs) >= 3:
        ret += p_one * BONUS_VALUE
    cost = BONUS_VALUE if stake == "bonus" else 1.0
    return {"legs": len(legs), "chance": p_all, "one_short": p_one, "fair_price": (1 / p_all if p_all else float("inf")),
            "price": price, "price_is_estimate": est, "product": product,
            "independent": float(np.prod(probs)), "return_per_dollar": ret, "roi": ret / cost - 1,
            "stake": stake, "leg_probs": probs}


def est_price(legs, cal=None, same=None):
    """What Sportsbet is likely to pay: the product of leg prices; x ~0.96 per game holding 2+ different
    players; same-player pairs cut by about the fair amount. An ESTIMATE: get a live quote before betting."""
    price = float(np.prod([x["price"] for x in legs]))
    haircut = (cal or {}).get("sgm_haircut", SGM_HAIRCUT)
    by_game = collections.defaultdict(list)
    for x in legs:
        by_game[(x["date"], x["game"])].append(x)
    for g in by_game.values():
        if len({x["pid"] for x in g}) >= 2:
            price *= haircut
        for a, b in itertools.combinations(g, 2):
            if a["pid"] == b["pid"]:
                pa, pb = fair_prob(a["price"], a["cls"], cal), fair_prob(b["price"], b["cls"], cal)
                both = float(simulate([a, b], [pa, pb], cal, same, n=40000, seed=1).all(1).mean())
                price *= (pa * pb) / max(both, 1e-6)
    return price


# ---------------------------------------------------------------- karma's rules and the builders

def eligible(leg, purpose="multi"):
    """karma's standing rules for a leg in a built slip (purpose: 'single', 'multi', 'sgm')."""
    if leg["kind"] != "alt" or leg.get("dtd"):
        return False                                   # built slips use X+ rungs; no day-to-day players
    if leg["stat"] == "FG3M" and purpose != "single":
        return False                                   # threes are straights only
    if "doncic" in pc.norm(leg["player"]) and leg["stat"] == "PTS" and leg["th"] >= 35:
        return False                                   # Luka: no 35+
    m = leg.get("min_l10") or 0
    if m < 20:
        return False                                   # no deep-bench players at all
    if purpose in ("multi", "sgm") and m < 28 and not (m >= 22 and leg["stat"] == "REB"):
        return False                                   # SGM/multi legs: 28+ min starters (22-28 for boards)
    return True


def _annotate(legs, cal):
    for x in legs:
        x["p"] = fair_prob(x["price"], x["cls"], cal)
        x["ev"] = x["p"] * x["price"] - 1
        x["cost"] = -math.log(max(x["p"] * x["price"], 1e-9)) / math.log(x["price"])   # cut per unit of odds
    return legs


def _best_per_player(legs, key):
    best = {}
    for x in sorted(legs, key=key):
        best.setdefault(x["pid"], x)
    return list(best.values())


def context(leg):
    """How far his own record at this line beats the price's chance (the lower of season and last 8).
    A tie-breaker only: in the 23-night walk-forward test this did NOT beat the price."""
    rates = [r for r in (leg.get("hr_season") if (leg.get("n_season") or 0) >= 8 else None, leg.get("hr_l8")) if r is not None]
    return (min(rates) - leg["p"]) if rates else -1.0


RANK_DRAWS = 40000


def _rank(slips, cal, same, top, stake="cash", bet_return=False, n=None):
    out = []
    for legs in slips:
        e = evaluate(legs, cal=cal, same=same, stake=stake, bet_return=bet_return, n=n or RANK_DRAWS)
        out.append((e["roi"], legs, e))
    out.sort(key=lambda t: (-round(t[0], 2), -sum(context(x) for x in t[1]) / len(t[1])))
    return out[:top]


def build_ladder(legs, cal, same, lo=1.9, hi=2.15, top=5):
    """One bet near 2.00: a single, or two legs from two different games."""
    singles = [x for x in legs if eligible(x, "single") and lo <= x["price"] <= hi and x["stat"] in SINGLE]
    pool = sorted([x for x in legs if eligible(x, "multi") and 1.3 <= x["price"] <= 1.6 and x["stat"] in SINGLE],
                  key=lambda x: -context(x))[:40]
    pairs = [[a, b] for a, b in itertools.combinations(pool, 2)
             if a["game"] != b["game"] and lo <= a["price"] * b["price"] <= hi]
    return _rank([[x] for x in sorted(singles, key=lambda x: -context(x))[:40]] + pairs, cal, same, top)


def build_longshot(legs, cal, same, n_legs=3, lo=2.0, hi=3.6, top=5, stake="cash", bet_return=False):
    """n legs from n different games, each a single-stat rung in the cheapest price band."""
    pool = [x for x in legs if eligible(x, "multi") and lo <= x["price"] <= hi and x["stat"] in SINGLE]
    pool = sorted(_best_per_player(pool, lambda x: -context(x)), key=lambda x: -context(x))
    per_game = collections.defaultdict(list)
    for x in pool:
        if len(per_game[x["game"]]) < 3:
            per_game[x["game"]].append(x)
    games = list(per_game)
    slips = []
    for gs in itertools.combinations(games, min(n_legs, len(games))):
        for combo in itertools.product(*[per_game[g] for g in gs]):
            slips.append(list(combo))
    random.Random(0).shuffle(slips)
    return _rank(slips[:400], cal, same, top, stake=stake, bet_return=bet_return)


def build_sgm(legs, cal, same, game, n_legs=4, lo=1.25, hi=3.6, top=5, bet_return=False):
    """Best same-game multi: one leg per player, single stats, and no more than half the legs from one team."""
    pool = [x for x in legs if x["game"] == game and eligible(x, "sgm") and lo <= x["price"] <= hi
            and x["stat"] in SINGLE]
    pool = sorted(_best_per_player(pool, lambda x: -context(x)), key=lambda x: -context(x))[:10]
    slips = []
    for combo in itertools.combinations(pool, min(n_legs, len(pool))):
        teams = collections.Counter(x["team"] for x in combo)
        if max(teams.values()) <= math.ceil(len(combo) / 2):
            slips.append(list(combo))
    return _rank(slips[:600], cal, same, top, bet_return=bet_return)


def build_big(legs, cal, same, target=500.0, top=3):
    """Cheapest route to a big price: the legs that give up least per unit of odds, different players and
    teams, at most two a game (one a team)."""
    pool = [x for x in legs if eligible(x, "multi") and 1.8 <= x["price"] <= 3.6 and x["stat"] in SINGLE]
    pool = sorted(_best_per_player(pool, lambda x: (round(x["cost"], 2), -context(x))), key=lambda x: (round(x["cost"], 2), -context(x)))
    slips = []
    for seed in range(top * 4):
        rnd = random.Random(seed)
        order = pool[:] if seed == 0 else sorted(pool, key=lambda x: x["cost"] + rnd.uniform(0, 0.03))
        slip, teams, price = [], set(), 1.0
        for x in order:
            if price >= target:
                break
            if x["team"] in teams:
                continue
            slip.append(x)
            teams.add(x["team"])
            price *= x["price"]
        if price >= target and slip not in slips:
            slips.append(slip)
    return _rank(slips, cal, same, top, n=400000)


def build_bonus(legs, cal, same, top=5):
    """Best use of a bonus bet (stake not returned): the slip that turns $1 of bonus into the most cash."""
    singles = [[x] for x in legs if eligible(x, "single") and 2.6 <= x["price"] <= 6 and x["stat"] in SINGLE]
    singles = sorted(singles, key=lambda s: (-round(s[0]["p"] * (s[0]["price"] - 1), 2), -context(s[0])))[:30]
    multis = [t[1] for t in build_longshot(legs, cal, same, n_legs=2, top=15, stake="bonus")]
    multis += [t[1] for t in build_longshot(legs, cal, same, n_legs=3, top=15, stake="bonus")]
    return _rank(singles + multis, cal, same, top, stake="bonus")


# ---------------------------------------------------------------- check a slip by name

def parse_legs(text, legs):
    """'Player STAT 15+; Player STAT o7.5 @1.90' -> leg dicts (matched to the night's saved prices)."""
    out, problems = [], []
    by_name = collections.defaultdict(list)
    for x in legs:
        by_name[pc.norm(x["player"])].append(x)
    for part in [p.strip() for p in text.replace("\n", ";").split(";") if p.strip()]:
        price = None
        if "@" in part:
            part, pr = part.rsplit("@", 1)
            price = float(pr)
        toks = part.split()
        if len(toks) < 3:
            problems.append(f"can't read '{part}'")
            continue
        spec, stat, name = toks[-1].lower(), toks[-2].upper().replace("3PM", "FG3M"), pc.norm(" ".join(toks[:-2]))
        kind, th = ("alt", float(spec[:-1])) if spec.endswith("+") else (("over" if spec[0] == "o" else "under"), float(spec[1:]))
        cands = [k for k in by_name if k == name] or [k for k in by_name if name in k or k.split()[-1] == name.split()[-1]]
        match = [x for k in cands for x in by_name[k] if x["stat"] == stat and x["kind"] == kind and abs(x["th"] - th) < 1e-9]
        if match:
            leg = dict(match[0])
        elif price and cands and by_name[cands[0]]:
            ref = by_name[cands[0]][0]
            leg = dict(ref, stat=stat, kind=kind, th=th,
                       cls=("ou_over" if kind == "over" else "ou_under" if kind == "under"
                            else "alt_single" if stat in SINGLE else "alt_combo"))
        else:
            problems.append(f"no saved price for '{part.strip()}' (add @price)")
            continue
        if price:
            leg["price"] = price
        out.append(leg)
    return out, problems


# ---------------------------------------------------------------- output

def show(title, ranked, cal, note=""):
    print(f"\n== {title}")
    if note:
        print(note)
    if not ranked:
        print("  nothing passes the rules tonight")
    for roi, legs, e in ranked:
        tag = " (est.)" if e["price_is_estimate"] and any(
            len([y for y in legs if y["game"] == x["game"]]) > 1 for x in legs) else ""
        chance = f"{100 * e['chance']:.1f}%" if e["chance"] >= 0.02 else f"1 in {1 / max(e['chance'], 1e-9):,.0f}"
        value = (f"$1 of bonus bet comes back as {100 * e['return_per_dollar']:.0f}c" if e["stake"] == "bonus"
                 else f"expected return {100 * e['roi']:+.0f}%")
        print(f"  price {e['price']:.2f}{tag} | true chance {chance} (fair price {e['fair_price']:.2f}) | {value}")
        for x, p in zip(legs, e["leg_probs"]):
            print(f"      {leg_label(x):38s} {x['game']:8s} @{x['price']:<5.2f} lands {100 * p:.0f}%  ({100 * (p * x['price'] - 1):+.0f}%)  {record(x)}")


def record(leg):
    def pct(v):
        return "--" if v is None else f"{100 * v:.0f}%"
    return f"his record: season {pct(leg.get('hr_season'))} of {leg.get('n_season') or 0}, last 8 {pct(leg.get('hr_l8'))}, {leg.get('min_l10') or 0:.0f} min"


def backtest(min_train=8, per_night=25, seed=0, budget=140, verbose=True):
    """Walk-forward: calibrate on the nights before, build each slip type for the night, grade it.
    Reports predicted vs actual hit rate and the return at the bookmaker's price (same-game prices are
    the 0.96 x product estimate). Saves progress after every night; run again to continue."""
    import pickle
    import time
    global RANK_DRAWS
    t0, RANK_DRAWS = time.time(), 6000
    state_path = os.path.join(ROOT, "journal", "_cache", "parlay_backtest.pkl")
    try:
        state = pickle.load(open(state_path, "rb"))
    except (OSError, ValueError, EOFError):
        state = {"done": [], "rows": []}
    same = load_same()
    order = [d for d in line_nights() if graded_night(d)]
    rnd = random.Random(seed)
    for i, d in enumerate(order):
        if i < min_train or d in state["done"]:
            continue
        if time.time() - t0 > budget:
            break
        nights = {dd: [x for x in graded_night(dd) if not x.get("push")] for dd in order[:i + 1]}
        train = [x for dd in order[:i] for x in nights[dd]]
        cal = {"tables": fit_tables(train), "sgm_haircut": SGM_HAIRCUT}
        for x in train:
            x["p"] = fair_prob(x["price"], x["cls"], cal)
        cal["between"] = fit_between({dd: nights[dd] for dd in order[:i]})
        legs = _annotate(nights[d], cal)
        games = sorted({x["game"] for x in legs})
        built = {"ladder (single or 2 games, ~2.00)": [t[1] for t in build_ladder(legs, cal, same, top=per_night)],
                 "longshot 3 (3 games, 2.0-3.6 a leg)": [t[1] for t in build_longshot(legs, cal, same, 3, top=per_night)],
                 "builder SGM, 4 legs": [t[1] for g in games for t in build_sgm(legs, cal, same, g, 4, top=4)],
                 "big ticket (target 500)": [t[1] for t in build_big(legs, cal, same, 500, top=6)]}
        # what we used to build: same-team stacks, combo-heavy same-game multis, long strings of near-locks
        for g in games:
            ok = [x for x in legs if x["game"] == g and x["kind"] == "alt" and 1.25 <= x["price"] <= 2.6 and not x.get("dtd")]
            for team in sorted({x["team"] for x in ok if x["team"]}):
                tp = _best_per_player([x for x in ok if x["team"] == team and x["stat"] == "PTS"], lambda x: abs(x["price"] - 1.8))
                for _ in range(3):
                    if len(tp) >= 4:
                        built.setdefault("OLD STYLE: 4 points legs, one team", []).append(rnd.sample(tp, 4))
            cp = _best_per_player([x for x in ok if x["cls"] == "alt_combo"], lambda x: abs(x["price"] - 1.6))
            for _ in range(4):
                if len(cp) >= 6:
                    built.setdefault("OLD STYLE: 6 combo legs, one game", []).append(rnd.sample(cp, 6))
        near = _best_per_player([x for x in legs if x["kind"] == "alt" and x["price"] <= 1.12 and not x.get("dtd")], lambda x: -x["price"])
        for _ in range(10):
            if len(near) >= 20:
                built.setdefault("OLD STYLE: 20 near-locks (<=1.12)", []).append(rnd.sample(near, 20))
        for name, slips in built.items():
            for slip in slips:
                e = evaluate(slip, cal=cal, same=same, n=20000)
                state["rows"].append((name, d, e["chance"], bool(all(x["hit"] for x in slip)), e["price"]))
        state["done"].append(d)
        pickle.dump(state, open(state_path, "wb"))
    todo = [d for d in order[min_train:] if d not in state["done"]]
    out = {}
    for name in dict.fromkeys(r[0] for r in state["rows"]):
        rows = [r for r in state["rows"] if r[0] == name]
        by = collections.defaultdict(list)
        for _, d, _, won, price in rows:
            by[d].append(won * price)
        ks, boots = list(by), []
        for _ in range(400):
            s = [v for k in rnd.choices(ks, k=len(ks)) for v in by[k]]
            boots.append(sum(s) / len(s) - 1)
        boots.sort()
        out[name] = {"slips": len(rows), "nights": len(ks), "predicted": sum(r[2] for r in rows) / len(rows),
                     "actual": sum(r[3] for r in rows) / len(rows), "roi": sum(r[3] * r[4] for r in rows) / len(rows) - 1,
                     "lo": boots[20], "hi": boots[379]}
    if verbose:
        print(f"walk-forward: {len(state['done'])} nights done" + (f", {len(todo)} to go (run again)" if todo else
              f" ({order[min_train]} to {order[-1]})"))
        for name, o in out.items():
            print(f"  {name:38s} slips {o['slips']:5d}  predicted hit {100 * o['predicted']:5.1f}%  actual {100 * o['actual']:5.1f}%"
                  f"  return {100 * o['roi']:+5.0f}% [{100 * o['lo']:+.0f},{100 * o['hi']:+.0f}]")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("date", help="US game date, or 'calibrate' / 'backtest'")
    ap.add_argument("mode", nargs="?", choices=["ladder", "longshot", "sgm", "big", "bonus", "check", "legs"])
    ap.add_argument("text", nargs="?", help="legs for `check`")
    ap.add_argument("--legs", type=int, default=3)
    ap.add_argument("--game")
    ap.add_argument("--target", type=float, default=500.0)
    ap.add_argument("--price", type=float, help="the bookmaker's price for the whole slip (check)")
    ap.add_argument("--bonus", action="store_true", help="stake is a bonus bet (not returned)")
    ap.add_argument("--bet-return", action="store_true", help="stake back as a bonus bet if exactly one leg misses")
    a = ap.parse_args(argv)
    if a.date == "calibrate":
        calibrate()
        return 0
    if a.date == "backtest":
        backtest()
        return 0
    cal, same = load_cal(), load_same()
    if not cal:
        print("No calibration file yet: run `python3 tools/parlay_builder.py calibrate` first.")
        return 1
    legs = _annotate(load_legs(a.date), cal)
    if not legs:
        print(f"No saved lines for {a.date} (journal/lines/{a.date}/).")
        return 1
    print(f"{a.date}: {len(legs)} priced legs, {len({x['game'] for x in legs})} games; calibrated on "
          f"{len(cal.get('fitted_on', []))} nights. Expected return = what $1 comes back as on average, minus $1.")
    if a.mode == "ladder":
        show("Ladder (about 2.00)", build_ladder(legs, cal, same), cal)
    elif a.mode == "longshot":
        show(f"Longshot {a.legs} (one leg a game)", build_longshot(legs, cal, same, a.legs, bet_return=a.bet_return,
                                                                   stake="bonus" if a.bonus else "cash"), cal)
    elif a.mode == "sgm":
        for g in ([a.game] if a.game else sorted({x["game"] for x in legs})):
            show(f"Same-game multi {g}, {a.legs} legs", build_sgm(legs, cal, same, g, a.legs, bet_return=a.bet_return), cal,
                 "  Sportsbet price is an estimate (0.96 x the product of the legs): get a live quote, then re-run `check --price`.")
    elif a.mode == "big":
        show(f"Big ticket, target {a.target:g}", build_big(legs, cal, same, a.target), cal)
    elif a.mode == "bonus":
        show("Bonus bet: most cash per $1 of bonus", build_bonus(legs, cal, same), cal)
    elif a.mode == "check":
        slip, problems = parse_legs(a.text or "", legs)
        for p in problems:
            print("  !", p)
        if slip:
            e = evaluate(slip, price=a.price, cal=cal, same=same, stake="bonus" if a.bonus else "cash",
                         bet_return=a.bet_return, n=400000)
            show("Slip check", [(e["roi"], slip, e)], cal)
            print(f"      if the legs were independent: {100 * e['independent']:.1f}%; exactly one leg short: {100 * e['one_short']:.1f}%")
    else:
        best = sorted([x for x in legs if x["kind"] == "alt" and eligible(x, "single")], key=lambda x: -x["ev"])[:25]
        print("\n== Legs that give up least")
        for x in best:
            print(f"  {leg_label(x):38s} {x['game']:8s} @{x['price']:<5.2f} lands {100 * x['p']:.0f}%  ({100 * x['ev']:+.0f}%)  {record(x)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
