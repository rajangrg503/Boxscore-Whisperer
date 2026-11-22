#!/usr/bin/env python3
"""Team style profile: how a team plays, ranked 1-30. Display only.

    python3 tools/team_profile.py BOS SAC
    python3 tools/team_profile.py --rank oreb_pct
    python3 tools/team_profile.py OKC --before 2026-11-22 --last 10

WHY (22 Nov 2026)
Two lessons from US 21 Nov that a player's own numbers can't show:
- Threes straights are capped by the TEAM's style. BOS took 59 threes,
  SAC 26: Hauser got 11 attempts, LaVine 3 (angle 25).
- A crashing opponent takes the long misses that would be a big's easy
  defensive boards: Sabonis had 2 REB at half vs BOS (angle 24).
So every slate game gets both teams' profile: three-point volume, free
throw rate, offensive rebounding, pace, and what they allow.

Built from the cached player game logs (data_cache/gamelog_*), summed to a
team box per game, the same way tools/team_defense.py does. Only games
strictly before --before are used. Nothing here changes how picks are made.
"""
import argparse
import datetime
import glob
import json
import os
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALIAS = {"NO": "NOP", "WSH": "WAS", "UTAH": "UTA", "SA": "SAS", "GS": "GSW", "NY": "NYK"}
COLS = ("PTS", "FGA", "FGM", "FG3A", "FG3M", "FTA", "OREB", "DREB", "TOV")

# (key, label, higher_is_rank_1)
METRICS = [
    ("pace", "Pace (poss/g)", True),
    ("fg3a_g", "3PA per game", True),
    ("fg3a_rate", "3PA rate (3PA/FGA)", True),
    ("fg3_pct", "3P%", True),
    ("ft_rate", "FT rate (FTA/FGA)", True),
    ("oreb_pct", "OREB% (crashing)", True),
    ("dreb_pct", "DREB% (cleaning own glass)", True),
    ("tov_pct", "TOV% (lower = safer)", False),
    ("opp_fg3a_g", "Opp 3PA per game allowed", True),
    ("opp_efg", "Opp eFG% allowed (lower = better D)", False),
    ("opp_oreb_pct", "Opp OREB% allowed", True),
]


def nf(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _date(s):
    return datetime.datetime.strptime(s, "%b %d, %Y").date()


def _rows(path):
    try:
        d = json.load(open(path))
    except (OSError, ValueError):
        return []
    if isinstance(d, list):
        return d
    return d.get("data") or (d.get("payload", {}) or {}).get("data") or []


def team_boxes(rows):
    """Sum player rows to {(game_id, team): box}; attach opponent, date."""
    box = defaultdict(lambda: defaultdict(float))
    meta = {}
    for r in rows:
        if nf(r.get("MIN")) <= 0:
            continue
        m = r.get("MATCHUP", "")
        team = ALIAS.get(m[:3].strip(), m[:3].strip())
        opp = ALIAS.get(m[-3:].strip(), m[-3:].strip())
        k = (r["Game_ID"], team)
        for c in COLS:
            box[k][c] += nf(r.get(c))
        meta[k] = (opp, _date(r["GAME_DATE"]))
    games = []
    for (gid, team), b in box.items():
        opp, day = meta[(gid, team)]
        o = box.get((gid, opp))
        if not o:
            continue
        games.append({"gid": gid, "team": team, "opp": opp, "date": day,
                      "b": dict(b), "o": dict(o)})
    return games


def load_games(season="2026-27"):
    rows = []
    for f in glob.glob(os.path.join(ROOT, "data_cache", f"gamelog_*_{season}.json")):
        rows.extend(_rows(f))
    return team_boxes(rows)


def _poss(b):
    return b["FGA"] - b["OREB"] + b["TOV"] + 0.44 * b["FTA"]


def profile(games, before=None, last=None):
    """{team: {metric: value, 'n': games}} from team-game boxes."""
    by = defaultdict(list)
    for g in games:
        if before is None or g["date"] < before:
            by[g["team"]].append(g)
    out = {}
    for t, gs in by.items():
        gs.sort(key=lambda g: g["date"])
        if last:
            gs = gs[-last:]
        s = defaultdict(float)
        o = defaultdict(float)
        poss = 0.0
        for g in gs:
            for c in COLS:
                s[c] += g["b"].get(c, 0.0)
                o[c] += g["o"].get(c, 0.0)
            poss += (_poss(g["b"]) + _poss(g["o"])) / 2
        n = len(gs)
        div = lambda a, b: a / b if b else 0.0  # noqa: E731
        out[t] = {
            "n": n,
            "pace": div(poss, n),
            "fg3a_g": div(s["FG3A"], n),
            "fg3a_rate": div(s["FG3A"], s["FGA"]),
            "fg3_pct": div(s["FG3M"], s["FG3A"]),
            "ft_rate": div(s["FTA"], s["FGA"]),
            "oreb_pct": div(s["OREB"], s["OREB"] + o["DREB"]),
            "dreb_pct": div(s["DREB"], s["DREB"] + o["OREB"]),
            "tov_pct": div(s["TOV"], poss),
            "opp_fg3a_g": div(o["FG3A"], n),
            "opp_efg": div(o["FGM"] + 0.5 * o["FG3M"], o["FGA"]),
            "opp_oreb_pct": div(o["OREB"], o["OREB"] + s["DREB"]),
        }
    return out


def ranks(prof):
    """{metric: {team: rank}}, rank 1 = most (or least, for 'lower is rank 1')."""
    r = {}
    for key, _, high in METRICS:
        order = sorted(prof, key=lambda t: prof[t][key], reverse=high)
        r[key] = {t: i + 1 for i, t in enumerate(order)}
    return r


def _fmt(key, v):
    if key in ("pace", "fg3a_g", "opp_fg3a_g"):
        return f"{v:.1f}"
    return f"{100 * v:.1f}%"


def show(teams, prof, rk):
    teams = [ALIAS.get(t, t) for t in teams]
    missing = [t for t in teams if t not in prof]
    for t in missing:
        print(f"{t}: no games in the cache")
    teams = [t for t in teams if t in prof]
    if not teams:
        return
    print(f"{'':38}" + "".join(f"{t:>14}" for t in teams))
    print(f"{'games':38}" + "".join(f"{prof[t]['n']:>14}" for t in teams))
    for key, label, _ in METRICS:
        cells = "".join(f"{_fmt(key, prof[t][key]) + ' (' + str(rk[key][t]) + ')':>14}" for t in teams)
        print(f"{label:38}{cells}")
    print("(rank 1-30 in brackets; for TOV% and Opp eFG% rank 1 = lowest)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("teams", nargs="*")
    ap.add_argument("--before", help="only games before this date (YYYY-MM-DD)")
    ap.add_argument("--last", type=int, help="only each team's last N games")
    ap.add_argument("--season", default="2026-27")
    ap.add_argument("--rank", choices=[m[0] for m in METRICS], help="print the league table for one metric")
    a = ap.parse_args(argv)
    before = datetime.date.fromisoformat(a.before) if a.before else None
    prof = profile(load_games(a.season), before=before, last=a.last)
    if not prof:
        print("no team games found in data_cache")
        return 1
    rk = ranks(prof)
    if a.rank:
        key = a.rank
        label = dict((m[0], m[1]) for m in METRICS)[key]
        print(label)
        for t, i in sorted(rk[key].items(), key=lambda x: x[1]):
            print(f"{i:>3} {t}  {_fmt(key, prof[t][key])}  ({prof[t]['n']} g)")
        return 0
    show(a.teams or sorted(prof), prof, rk)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
