#!/usr/bin/env python3
"""Team offence/defence ratings from player game logs, for game-level projections.

    python3 tools/team_defense.py                 # defence table, latest data
    python3 tools/team_defense.py --before 2026-11-07 --luck
    python3 tools/team_defense.py --game WAS OKC  # projected score, OKC at home

karma, 8 Nov 2026: OKC "held 3 straight opponents to a season-low FG%".
The model sums player projections and has no team-defence term, so elite
defences (OKC, SAS) barely register in its game scores. This builds that
term from the cached player game logs:

- Team box per game = sum of its players' rows (data_cache/gamelog_*).
- Possessions = FGA - OREB + TOV + 0.44*FTA (averaged over both teams).
- Offence/defence = points per 100 possessions, adjusted for opponent
  strength by a few rounds of iteration, with recent games weighted more
  (half-life in games) so an improving group such as OKC's shows up fast.
- --luck replaces each team's made threes with its attempts x the league
  3P%: a defence controls where shots come from more than whether threes
  fall, so this keeps the repeatable part.

Only games strictly before --before are used, so a backtest has no
look-ahead. Nothing here is proven yet: see
claude/game-level-model-backtest-2026-11-08.md.
"""
import argparse
import datetime
import glob
import json
import os
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALIAS = {"NO": "NOP", "WSH": "WAS", "UTAH": "UTA", "SA": "SAS", "GS": "GSW", "NY": "NYK"}
HOME_EDGE = 2.0  # points, a common rule of thumb; refit once there is enough data


def nf(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _date(s):
    return datetime.datetime.strptime(s, "%b %d, %Y").date()


def team_games(season="2026-27"):
    """{(game_id, team): box} with opponent, date and home flag."""
    box = defaultdict(lambda: defaultdict(float))
    meta = {}
    for f in glob.glob(os.path.join(ROOT, "data_cache", f"gamelog_*_{season}.json")):
        try:
            d = json.load(open(f))
        except (OSError, ValueError):
            continue
        rows = (d.get("data") if isinstance(d, dict) else None) or (d.get("payload", {}) or {}).get("data") or []
        for r in rows:
            if nf(r.get("MIN")) <= 0:
                continue
            m = r.get("MATCHUP", "")
            team = ALIAS.get(m[:3].strip(), m[:3].strip())
            opp = ALIAS.get(m[-3:].strip(), m[-3:].strip())
            k = (r["Game_ID"], team)
            for c in ("PTS", "FGA", "FGM", "FG3A", "FG3M", "FTA", "OREB", "TOV"):
                box[k][c] += nf(r.get(c))
            meta[k] = (opp, _date(r["GAME_DATE"]), " vs. " in m)
    games = []
    for (gid, team), b in box.items():
        opp, day, home = meta[(gid, team)]
        o = box.get((gid, opp))
        if not o:
            continue
        poss = ((b["FGA"] - b["OREB"] + b["TOV"] + 0.44 * b["FTA"]) +
                (o["FGA"] - o["OREB"] + o["TOV"] + 0.44 * o["FTA"])) / 2
        games.append({"gid": gid, "team": team, "opp": opp, "date": day, "home": home,
                      "pts": b["PTS"], "fg3a": b["FG3A"], "fg3m": b["FG3M"],
                      "fga": b["FGA"], "fgm": b["FGM"], "poss": poss,
                      "opp_pts": o["PTS"]})
    return games


def ratings(games, before=None, halflife=6.0, luck=False, rounds=6):
    g = [x for x in games if before is None or x["date"] < before]
    if not g:
        return None
    lg3 = sum(x["fg3m"] for x in g) / max(1.0, sum(x["fg3a"] for x in g))

    def pts(x):
        return x["pts"] - 3 * (x["fg3m"] - x["fg3a"] * lg3) if luck else x["pts"]

    lg = 100 * sum(pts(x) for x in g) / sum(x["poss"] for x in g)
    # recency weight: each team's games, newest = 1
    by_team = defaultdict(list)
    for x in g:
        by_team[x["team"]].append(x)
    w = {}
    for t, xs in by_team.items():
        xs.sort(key=lambda x: x["date"], reverse=True)
        for i, x in enumerate(xs):
            w[(x["gid"], t)] = 0.5 ** (i / halflife)
    off = {t: lg for t in by_team}
    de = {t: lg for t in by_team}   # points ALLOWED per 100; lower = better defence
    for _ in range(rounds):
        no, nd = defaultdict(float), defaultdict(float)
        wo, wd = defaultdict(float), defaultdict(float)
        for x in g:
            t, o = x["team"], x["opp"]
            eff = 100 * pts(x) / x["poss"]
            ww = w[(x["gid"], t)]
            no[t] += ww * (eff - de.get(o, lg) + lg)
            wo[t] += ww
            wd_ = w.get((x["gid"], o), ww)
            nd[o] += wd_ * (eff - off.get(t, lg) + lg)
            wd[o] += wd_
        off = {t: no[t] / wo[t] for t in no}
        de = {t: nd[t] / wd[t] for t in nd}
    pace = {}
    for t, xs in by_team.items():
        tw = sum(w[(x["gid"], t)] for x in xs)
        pace[t] = sum(w[(x["gid"], t)] * x["poss"] for x in xs) / tw
    return {"lg": lg, "off": off, "def": de, "pace": pace, "n": {t: len(v) for t, v in by_team.items()}}


def project(R, away, home):
    away, home = ALIAS.get(away, away), ALIAS.get(home, home)
    lg = R["lg"]
    pace = (R["pace"].get(away, 100) + R["pace"].get(home, 100)) / 2
    a = pace * (R["off"].get(away, lg) + R["def"].get(home, lg) - lg) / 100
    h = pace * (R["off"].get(home, lg) + R["def"].get(away, lg) - lg) / 100
    return a - HOME_EDGE / 2, h + HOME_EDGE / 2


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", help="YYYY-MM-DD: use only games before this date")
    ap.add_argument("--halflife", type=float, default=6.0)
    ap.add_argument("--luck", action="store_true", help="regress made threes to league 3P%")
    ap.add_argument("--game", nargs=2, metavar=("AWAY", "HOME"))
    a = ap.parse_args(argv)
    before = datetime.date.fromisoformat(a.before) if a.before else None
    R = ratings(team_games(), before, a.halflife, a.luck)
    if a.game:
        x, y = project(R, *a.game)
        print(f"{a.game[0]} {x:.0f} @ {a.game[1]} {y:.0f}  (total {x+y:.0f}, home by {y-x:+.1f})")
        return
    print(f"league {R['lg']:.1f} pts/100   (defence: lower = better; luck={a.luck})")
    for t in sorted(R["def"], key=R["def"].get):
        print(f"{t}  def {R['def'][t]:6.1f} ({R['def'][t]-R['lg']:+.1f})   off {R['off'][t]:6.1f} ({R['off'][t]-R['lg']:+.1f})   pace {R['pace'][t]:5.1f}  n={R['n'][t]}")


if __name__ == "__main__":
    main()
