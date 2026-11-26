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
  python3 tools/parlay_builder.py 2026-11-27 longshot --legs 3          # any number of legs
  python3 tools/parlay_builder.py 2026-11-27 sgm --game MIA@NYK --legs 4 [--play]
  python3 tools/parlay_builder.py 2026-11-27 big --target 500
  python3 tools/parlay_builder.py 2026-11-27 bonus
  python3 tools/parlay_builder.py 2026-11-27 check "Davion Mitchell PTS 15+; Luka Doncic AST 8+ @2.40" [--price 9.5]
  python3 tools/parlay_builder.py backtest
  bash tools/publish_builder.sh                             # save lines, write the phone feed, push

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
VERSION = 4             # bump when load_legs changes shape (the Journal page caches legs by it)
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


def has_slate(date):
    """True when the night has been projected (Journal > Tonight): that is where Out / day-to-day comes from."""
    return bool((pc.slate_file(date) or {}).get("games"))


NO_SLATE = ("This night hasn't been projected yet, so injuries are NOT checked: a player who is out or "
            "day-to-day can appear. Project the games in the Journal's Tonight tab, or check the injury list yourself.")


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
        sides_of_game = game.split("@")
        team = s.get("team") or roster_team or pc.ALIAS.get(pc.team_from_rows(prior), pc.team_from_rows(prior))
        if team not in sides_of_game:                 # no slate and a stale team (traded): unknown
            team = None
        base = {"date": date, "game": game, "player": r["player"], "pid": pid, "stat": r["stat"],
                "team": team, "opp": s.get("opp") or (next((t for t in sides_of_game if t != team), None) if team else None),
                "crank": s.get("crank") or 99, "dtd": bool(s.get("dtd")), "outs": s.get("outs"),
                "min_l10": (st.mean(pc.nf(x["MIN"]) for x in prior[:10]) if prior else None),
                "_pa": (st.mean(pc.value(x, "PA") for x in prior[:20]) if prior else 0.0)}
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
            versus = [x for x in prior if base["opp"] and pc.ALIAS.get(pc.opp_of(x), pc.opp_of(x)) == base["opp"]]
            leg["h2h_n"] = len(versus)                 # tonight's opponent, last season and this one
            leg["h2h_hits"] = sum(clears(pc.value(x, r["stat"])) for x in versus)
            leg["cls"] = ("ou_over" if kind == "over" else "ou_under" if kind == "under"
                          else "alt_single" if r["stat"] in SINGLE else "alt_combo")
            if actual is not None:
                leg["actual"] = actual
                leg["hit"] = (actual >= th if kind == "alt" else actual > th if kind == "over" else actual < th)
                leg["push"] = kind != "alt" and actual == th
            legs.append(leg)
    # No projected slate for this night yet: rank each team's creators by recent points + assists instead.
    by_team = collections.defaultdict(dict)
    for x in legs:
        if x["crank"] == 99 and x["team"]:
            by_team[(x["game"], x["team"])][x["pid"]] = x["_pa"]
    for players in by_team.values():
        order = sorted(players, key=lambda pid: -players[pid])
        for x in legs:
            if x["crank"] == 99 and x["pid"] in players:
                x["crank"] = order.index(x["pid"]) + 1
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


FEED_PATH = os.path.join(ROOT, "data", "parlay_feed.json.gz")
_FEED_DROP = ("_pa", "_e", "_phi", "p", "ev", "cost")


def build_feed(dates=None, path=FEED_PATH):
    """Everything the phone page needs for the coming nights, in one small file: the priced legs (with each
    player's record), whether the night was projected, and the calibration. karma chose on 26 Nov 2026 to
    publish this with the public app so the builder works away from the Mac."""
    import gzip
    from zoneinfo import ZoneInfo
    today = dt.datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    nights = {}
    for d in dates or [d for d in line_nights() if d >= today]:
        legs = load_legs(d)
        if legs:
            nights[d] = {"has_slate": has_slate(d),
                         "legs": [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in x.items()
                                   if k not in _FEED_DROP} for x in legs]}
    feed = {"made_at": dt.datetime.now(ZoneInfo("Australia/Sydney")).isoformat(timespec="minutes"),
            "version": VERSION, "cal": load_cal(), "nights": nights}
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(feed, f, separators=(",", ":"))
    return feed


def load_feed(path=FEED_PATH):
    import gzip
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, EOFError):
        return {}


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


def _norm_cdf(x):
    """Normal CDF for arrays (Abramowitz & Stegun 7.1.26, error under 1e-7)."""
    t = 1.0 / (1.0 + 0.3275911 * np.abs(x) / math.sqrt(2))
    poly = t * (0.254829592 + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429))))
    erf = 1.0 - poly * np.exp(-x * x / 2)
    return 0.5 * (1.0 + np.sign(x) * erf)


def _norm_inv(p):
    """Inverse normal CDF for arrays (Acklam's rational approximation, relative error ~1e-9)."""
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02,
         -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01,
         -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00,
         4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00)
    p = np.clip(np.asarray(p, float), 1e-300, 1 - 1e-16)
    out = np.empty_like(p)
    lo, hi = p < 0.02425, p > 1 - 0.02425
    mid = ~(lo | hi)
    q = p[mid] - 0.5
    r = q * q
    out[mid] = ((((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
                / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1))
    for mask, sign in ((lo, 1.0), (hi, -1.0)):
        q = np.sqrt(-2 * np.log(p[mask] if sign > 0 else 1 - p[mask]))
        out[mask] = sign * ((((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
                            / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1))
    return out


def orthant(cuts, corr, n=4000, seed=0):
    """Chance every leg lands: P(z_i > cut_i for all i) for correlated normals, by the GHK sequential
    simulator. Unlike counting simulated nights it stays accurate for 1-in-a-million slips."""
    k = len(cuts)
    if k == 0:
        return 1.0
    low = np.linalg.cholesky(corr)
    u = np.random.default_rng(seed).random((n, k))
    z = np.zeros((n, k))
    weight = np.ones(n)
    for i in range(k):
        bound = (cuts[i] - z[:, :i] @ low[i, :i]) / low[i, i]
        above = np.clip(1.0 - _norm_cdf(bound), 1e-300, 1.0)
        weight *= above
        z[:, i] = _norm_inv(1.0 - above * (1.0 - u[:, i]))        # a draw from the part above the bound
    return float(weight.mean())


def evaluate(legs, price=None, cal=None, same=None, stake="cash", bet_return=False, n=6000, seed=0, one_short=True):
    """True chance, fair price and expected return per $1 for a slip.
    price: the bookmaker's price for the whole slip (default: product of leg prices, x the SGM haircut
    for each game with 2+ legs). stake='bonus': stake not returned. bet_return: stake back as a bonus
    bet when exactly one leg misses."""
    probs = [fair_prob(x["price"], x["cls"], cal) for x in legs]
    corr = _corr(legs, cal, same)
    cuts = np.array([-_NORMAL.inv_cdf(min(max(p, 1e-9), 1 - 1e-9)) for p in probs])
    p_all = orthant(cuts, corr, n=n, seed=seed)
    p_one = 0.0
    if (one_short or bet_return) and len(legs) > 1:
        for i in range(len(legs)):                    # every leg but i lands, minus all of them landing
            keep = [j for j in range(len(legs)) if j != i]
            p_one += max(orthant(cuts[keep], corr[np.ix_(keep, keep)], n=n, seed=seed) - p_all, 0.0)
    elif len(legs) == 1:
        p_one = 1.0 - p_all
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
                both = orthant(np.array([-_NORMAL.inv_cdf(pa), -_NORMAL.inv_cdf(pb)]), _corr([a, b], cal, same), n=2000, seed=1)
                price *= (pa * pb) / max(both, 1e-6)
    return price


# ---------------------------------------------------------------- karma's rules and the builders

def eligible(leg, purpose="multi"):
    """karma's standing rules for a leg in a built slip (purpose: 'single', 'multi', 'sgm';
    'play' = playing around: any X+ rung on a 20+ minute player who isn't day-to-day)."""
    if leg["kind"] != "alt" or leg.get("dtd"):
        return False                                   # built slips use X+ rungs; no day-to-day players
    if purpose == "play":
        return (leg.get("min_l10") or 0) >= 20
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


RANK_DRAWS = 800


def _rank(slips, cal, same, top, stake="cash", bet_return=False, n=None):
    """Score every candidate quickly, then price the best ones properly."""
    out = []
    for legs in slips:
        e = evaluate(legs, cal=cal, same=same, stake=stake, bet_return=bet_return, n=n or RANK_DRAWS, one_short=False)
        out.append((e["roi"], legs, e))
    # Expected return first; among near-equal slips the players' own records decide (20 points of
    # record counts like 1 point of return, so it only ever separates slips the price can't).
    out.sort(key=lambda t: -(t[0] + 0.05 * sum(max(context(x), -0.5) for x in t[1]) / len(t[1])))
    final = []
    for _, legs, _ in out[:top]:
        e = evaluate(legs, cal=cal, same=same, stake=stake, bet_return=bet_return)
        final.append((e["roi"], legs, e))
    return final


def build_ladder(legs, cal, same, lo=1.9, hi=2.15, top=5):
    """One bet near 2.00: a single, or two legs from two different games."""
    singles = [x for x in legs if eligible(x, "single") and lo <= x["price"] <= hi and x["stat"] in SINGLE]
    pool = sorted([x for x in legs if eligible(x, "multi") and 1.3 <= x["price"] <= 1.6 and x["stat"] in SINGLE],
                  key=lambda x: -context(x))[:40]
    pairs = [[a, b] for a, b in itertools.combinations(pool, 2)
             if a["game"] != b["game"] and lo <= a["price"] * b["price"] <= hi]
    return _rank([[x] for x in sorted(singles, key=lambda x: -context(x))[:40]] + pairs, cal, same, top)


def _pool(legs, purpose, lo, hi, loose):
    """One leg per player, best own-record first. loose = play mode: any stat, 20+ minutes."""
    pool = [x for x in legs if eligible(x, "play" if loose else purpose) and lo <= x["price"] <= hi
            and (loose or x["stat"] in SINGLE)]
    return sorted(_best_per_player(pool, lambda x: -context(x)), key=lambda x: -context(x))


def build_longshot(legs, cal, same, n_legs=3, lo=2.0, hi=3.6, top=5, stake="cash", bet_return=False, loose=False):
    """n legs spread over the night's games as evenly as possible, each a single-stat rung in the cheapest
    price band; one leg per player and (unless loose) per team. Any number of legs: with more legs than
    games, games take two or more each."""
    pool = _pool(legs, "multi", lo, hi, loose)
    per_game = collections.defaultdict(list)
    for x in pool:
        per_game[x["game"]].append(x)
    games = sorted(per_game)
    if not games:
        return []
    n_legs = min(n_legs, len(pool) if loose else len({x["team"] or x["pid"] for x in pool}))
    slips, seen = [], set()
    for seed in range(400):
        rnd = random.Random(seed)
        order = games[:]
        rnd.shuffle(order)
        lists = {g: (per_game[g][:] if seed == 0 else rnd.sample(per_game[g][:8], len(per_game[g][:8]))) for g in games}
        slip, teams = [], set()
        while len(slip) < n_legs and any(lists.values()):
            for g in order:                             # one leg from each game in turn
                while lists[g]:
                    x = lists[g].pop(0)
                    if loose or (x["team"] or x["pid"]) not in teams:
                        slip.append(x)
                        teams.add(x["team"] or x["pid"])
                        break
                if len(slip) == n_legs:
                    break
        key = frozenset(id(x) for x in slip)
        if len(slip) == n_legs and key not in seen:
            seen.add(key)
            slips.append(slip)
    return _rank(slips, cal, same, top, stake=stake, bet_return=bet_return)


def build_sgm(legs, cal, same, game, n_legs=4, lo=1.25, hi=3.6, top=5, bet_return=False, loose=False):
    """Best same-game multi: one leg per player, single stats, and no more than half the legs from one
    team. Any number of legs up to the players available; loose (play mode) drops the stat, minutes and
    team-balance rules."""
    pool = _pool([x for x in legs if x["game"] == game], "sgm", 1.1 if loose else lo, 6.0 if loose else hi, loose)
    n_legs = min(n_legs, len(pool))
    if not n_legs:
        return []
    pool = pool[:max(10, n_legs + 3)]
    if math.comb(len(pool), n_legs) <= 600:
        combos = itertools.combinations(pool, n_legs)
    else:
        rnd = random.Random(0)
        combos = [rnd.sample(pool, n_legs) for _ in range(600)]
    slips = []
    for combo in combos:
        teams = collections.Counter(x["team"] or x["pid"] for x in combo)      # unknown team: counts alone
        if loose or max(teams.values()) <= math.ceil(len(combo) / 2):
            slips.append(list(combo))
    return _rank(slips[:600], cal, same, top, bet_return=bet_return)


def build_big(legs, cal, same, target=500.0, top=3):
    """Cheapest route to a big price: the legs that give up least per unit of odds, different players,
    one per team -- then two, then three per team if the target needs more legs than there are teams."""
    pool = [x for x in legs if eligible(x, "multi") and 1.8 <= x["price"] <= 3.6 and x["stat"] in SINGLE]
    pool = sorted(_best_per_player(pool, lambda x: (round(x["cost"], 2), -context(x))), key=lambda x: (round(x["cost"], 2), -context(x)))
    slips = []
    for seed in range(top * 4):
        rnd = random.Random(seed)
        order = pool[:] if seed == 0 else sorted(pool, key=lambda x: x["cost"] + rnd.uniform(0, 0.03))
        slip, teams, price = [], collections.Counter(), 1.0
        for per_team in (1, 2, 3, 99):
            for x in order:
                if price >= target:
                    break
                if x in slip or teams[x["team"] or x["pid"]] >= per_team:
                    continue
                slip.append(x)
                teams[x["team"] or x["pid"]] += 1
                price *= x["price"]
        if price >= target and slip not in slips:
            slips.append(slip)
    return _rank(slips, cal, same, top)


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
    versus = f", v {leg.get('opp')} {leg.get('h2h_hits')}/{leg.get('h2h_n')}" if leg.get("h2h_n") else ""
    return (f"his record: season {pct(leg.get('hr_season'))} of {leg.get('n_season') or 0}, last 8 {pct(leg.get('hr_l8'))}"
            f"{versus}, {leg.get('min_l10') or 0:.0f} min")


def backtest(min_train=8, per_night=25, seed=0, budget=140, verbose=True):
    """Walk-forward: calibrate on the nights before, build each slip type for the night, grade it.
    Reports predicted vs actual hit rate and the return at the bookmaker's price (same-game prices are
    the 0.96 x product estimate). Saves progress after every night; run again to continue."""
    import pickle
    import time
    t0 = time.time()
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
                e = evaluate(slip, cal=cal, same=same, one_short=False)
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
    ap.add_argument("date", help="US game date, or 'calibrate' / 'backtest' / 'publish' (write the phone feed)")
    ap.add_argument("mode", nargs="?", choices=["ladder", "longshot", "sgm", "big", "bonus", "check", "legs"])
    ap.add_argument("text", nargs="?", help="legs for `check`")
    ap.add_argument("--legs", type=int, default=3)
    ap.add_argument("--game")
    ap.add_argument("--target", type=float, default=500.0)
    ap.add_argument("--price", type=float, help="the bookmaker's price for the whole slip (check)")
    ap.add_argument("--bonus", action="store_true", help="stake is a bonus bet (not returned)")
    ap.add_argument("--bet-return", action="store_true", help="stake back as a bonus bet if exactly one leg misses")
    ap.add_argument("--play", action="store_true",
                    help="playing around: any stat, 20+ minute players, no team-balance rule (longshot, sgm)")
    a = ap.parse_args(argv)
    if a.date == "calibrate":
        calibrate()
        return 0
    if a.date == "publish":
        feed = build_feed()
        print(f"feed written: {os.path.relpath(FEED_PATH, ROOT)} "
              + (", ".join(f"{d} ({len(n['legs'])} legs)" for d, n in feed["nights"].items()) or "no coming nights with saved lines"))
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
    if not has_slate(a.date):
        print("NOTE: " + NO_SLATE)
    print(f"{a.date}: {len(legs)} priced legs, {len({x['game'] for x in legs})} games; calibrated on "
          f"{len(cal.get('fitted_on', []))} nights. Expected return = what $1 comes back as on average, minus $1.")
    if a.mode == "ladder":
        show("Ladder (about 2.00)", build_ladder(legs, cal, same), cal)
    elif a.mode == "longshot":
        show(f"Longshot, {a.legs} legs spread over the games", build_longshot(
            legs, cal, same, a.legs, bet_return=a.bet_return, stake="bonus" if a.bonus else "cash", loose=a.play), cal)
    elif a.mode == "sgm":
        for g in ([a.game] if a.game else sorted({x["game"] for x in legs})):
            show(f"Same-game multi {g}, {a.legs} legs", build_sgm(legs, cal, same, g, a.legs, bet_return=a.bet_return,
                                                                  loose=a.play), cal,
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
                         bet_return=a.bet_return, n=20000)
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
