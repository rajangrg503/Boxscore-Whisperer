#!/usr/bin/env python3
"""Ceiling screen: alt-ladder props where a big night is likelier than the price says.

    python3 tools/ceiling_screen.py 2026-11-04 [--lo 2.5 --hi 8] [--out "BOS:Jayson Tatum"]

WHY THIS EXISTS
On 5 Nov 2026 karma pointed at tipsters who post volume picks at +220 to
+750 (Sengun 8+ assists with both backup bigs out, Scheierman 6+ rebounds
starting for Tatum). The card so far asks "does he clear his usual line?".
This asks "how often does he reach a BIG number, and is that more often
than the price assumes?".

TWO OPINIONS, BOTH MUST AGREE
    p_box    tonight's box-score projection (journal/slate/<date>.json):
             minutes already redistributed for who is out, so a role
             change shows up here first. Spread borrowed from p_hist.
    p_hist   slip_check.check_leg: two-season blend, opponent defense,
             measured games without tonight's absentees.
    p_seen   how often he has ACTUALLY reached this number: every game this
             season and last (last season's games scaled to this season's
             minutes), then scaled to tonight's projected minutes --
             fully on the way down, damped (x0.75) on the way up, per
             the 4,799-line minutes measurement in rank_lines.py.

The first version used only p_box and p_hist. Both assume a bell curve,
and its tails ran fat: every far rung on the ladder came out at 30%+ with
"cleared 1 of his last 10" printed underneath. p_seen is the check on
that. A row is listed only when ALL THREE beat the price's implied chance,
ranked by expected return on the lowest, so no single optimistic number
can carry a pick.

SECOND PASS, same morning. Even with p_seen the top rows showed +100% to
+150% expected returns -- not believable against a book. They were bench
players whose minutes swing (Paul Reed 8 to 38, Jenkins 15 to 41), and
p_seen was scaling their history UP to tonight's projected minutes, which
is the exact number the book doubts (Reed got 8 minutes on 3 Nov). Now:
    - p_seen scales down only, never up;
    - his last 10 games must also clear the rung at least as often as
      the price implies (p_recent);
    - rows with swinging minutes are dropped unless --swing is passed;
    - fewer than 20 real games behind p_seen (rookies, VanVleet back from
      a season out) and the row is dropped.

MOTOR PLAYERS (data/player_notes.json, karma's reads). A player tagged
'motor' whose team has someone out is kept even when his minutes swing
and is marked in the output: his output rises with his minutes, which is
exactly the night the swing is upward (Scheierman, 4 Nov: 29 min, 9/11).

READ THE EV COLUMN AS A RANKING, NOT A PROMISE. The screen picks the
biggest gaps out of ~3,000 rungs, so the top rows are the ones where
every estimate happened to run high together. The true edge is smaller.

Assist legs are listed but marked: karma's straights rule avoids props that
depend on teammates making shots.
"""
import argparse
import datetime
import glob
import json
import math
import os
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import slip_check as sc  # noqa: E402
import rank_lines as rl  # noqa: E402
import player_notes as pn  # noqa: E402
from engine.season import season_for_date  # noqa: E402
from engine.cache import read_payload  # noqa: E402

REPO = os.path.dirname(HERE)
COLS = {"PTS": ["PTS"], "REB": ["REB"], "AST": ["AST"], "PR": ["PTS", "REB"],
        "PA": ["PTS", "AST"], "RA": ["REB", "AST"], "PRA": ["PTS", "REB", "AST"]}


def norm(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return " ".join(s.replace(".", "").replace("'", "").split())


def over(line, mean, sd):
    return 0.5 * math.erfc((line - mean) / (sd * math.sqrt(2))) if sd > 0 else float(mean > line)


UP_DAMP = 0.75
PRIOR_W = 12.0


def p_seen(pid, cols, line, min_tonight, day):
    """Share of real games at or above the rung, minutes-adjusted to tonight."""
    season = season_for_date(day)
    y = int(season[:4])
    prev_season = f"{y - 1}-{str(y)[2:]}"
    cur = (read_payload(f"gamelog_{pid}_{season}") or {}).get("data") or []
    prev = (read_payload(f"gamelog_{pid}_{prev_season}") or {}).get("data") or []
    def val(g):
        return sum(rl.nf(g.get(c)) for c in cols)
    mc = [rl.nf(g.get("MIN")) for g in cur if rl.nf(g.get("MIN")) > 0]
    mp = [rl.nf(g.get("MIN")) for g in prev if rl.nf(g.get("MIN")) > 0]
    if len(mc) < 4:
        return None, 0
    mpg = sum(mc) / len(mc)
    scale_prev = mpg / (sum(mp) / len(mp)) if mp else 1.0
    r = (min_tonight or mpg) / mpg
    k = min(r, 1.0)  # down only: see SECOND PASS in the docstring
    vc = [val(g) * k for g in cur if rl.nf(g.get("MIN")) > 0]
    vp = [val(g) * scale_prev * k for g in prev if rl.nf(g.get("MIN")) > 0]
    pc = sum(v > line for v in vc) / len(vc)
    if len(vp) >= 15:
        pp = sum(v > line for v in vp) / len(vp)
        return (len(vc) * pc + PRIOR_W * pp) / (len(vc) + PRIOR_W), len(vc) + len(vp)
    return pc, len(vc)


def latest_lines(us_date):
    snaps = sorted(glob.glob(os.path.join(REPO, "journal", "lines", us_date, "*.json")),
                   key=os.path.getmtime)
    return json.load(open(snaps[-1])) if snaps else None


def screen(us_date, lo=2.5, hi=8.0, extra_out=(), limit=15, swing=False, min_games=20):
    slate = json.load(open(os.path.join(REPO, "journal", "slate", f"{us_date}.json")))
    lines = latest_lines(us_date)
    if not lines:
        sys.exit(f"no lines for {us_date}")
    players, outs = {}, {}
    for g in slate["games"]:
        game = f"{g['away']}@{g['home']}"
        for team, ps in g["players"].items():
            outs[team] = [str(o if isinstance(o, str) else o.get("name")).split(" (")[0]
                          for o in g["out"].get(team, [])]
            for p in ps:
                players[(game, norm(p["name"]))] = (team, p)
    for spec in extra_out:
        team, name = spec.split(":", 1)
        outs.setdefault(team, []).append(name)

    notes = pn.load()
    rows = []
    for ev in lines["events"]:
        for r in ev["rows"]:
            if r.get("kind") != "alt" or not (lo <= float(r["price"]) <= hi):
                continue
            hit = players.get((r["game"], norm(r["player"])))
            if not hit or r["stat"] not in COLS:
                continue
            team, p = hit
            if (p.get("min") or 0) < 12:
                continue
            line = float(r["threshold"]) - 0.5
            leg = {"player": p["name"], "pid": p["pid"], "team": team, "stat": r["stat"],
                   "line": line, "side": "over", "us_date": us_date, "game": r["game"]}
            try:
                chk = sc.check_leg(leg, outs.get(team) or None)
            except Exception as exc:  # one bad history must not sink the screen
                print(f"skip {p['name']} {r['stat']}: {exc}", file=sys.stderr)
                continue
            if chk.get("chance") is None or not chk.get("proj"):
                continue
            sd = max((chk["proj"]["high"] - chk["proj"]["mid"]) / 1.2816, 0.5)
            box_mean = sum(float(p.get(c) or 0) for c in COLS[r["stat"]])
            p_box, p_hist = over(line, box_mean, sd), chk["chance"]
            ps, n_seen = p_seen(p["pid"], COLS[r["stat"]], line, p.get("min"),
                                datetime.date.fromisoformat(us_date))
            if ps is None:
                continue
            implied = 1 / float(r["price"])
            rec = chk.get("recent") or {}
            p_recent = rec["over"] / rec["of"] if rec.get("of") else 0.0
            mins = chk.get("minutes") or {}
            motor = "motor" in pn.tags(p["name"], notes) and bool(outs.get(team))
            if not swing and not motor and mins and not mins.get("steady", True):
                continue
            if p_recent < implied or n_seen < min_games:
                continue
            low = min(p_box, p_hist, ps)
            if low <= implied:
                continue
            rows.append({"player": p["name"], "team": team, "game": r["game"],
                         "leg": f"{int(r['threshold'])}+ {r['stat']}", "price": float(r["price"]),
                         "implied": implied, "p_box": p_box, "p_hist": p_hist, "p_seen": ps, "n_seen": n_seen, "recent": f"{rec.get('over')}/{rec.get('of')}",
                         "ev": low * float(r["price"]) - 1, "box_mean": box_mean,
                         "min": p.get("min"), "assist": "AST" in COLS[r["stat"]], "motor": motor,
                         "flags": chk.get("flags", [])})
    rows.sort(key=lambda x: -x["ev"])
    # one row per player-stat: the best rung of each ladder
    seen, best = set(), []
    for x in rows:
        k = (x["player"], x["leg"].split()[1])
        if k not in seen:
            seen.add(k)
            best.append(x)
    return best[:limit]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("us_date", nargs="?", default=datetime.date.today().isoformat())
    ap.add_argument("--lo", type=float, default=2.5)
    ap.add_argument("--hi", type=float, default=8.0)
    ap.add_argument("--out", action="append", default=[], help='"TEAM:Player Name"')
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--swing", action="store_true", help="keep players whose minutes swing")
    a = ap.parse_args(argv)
    for x in screen(a.us_date, a.lo, a.hi, a.out, a.limit, a.swing):
        tag = (" [AST]" if x["assist"] else "") + (" [MOTOR: role up, karma's read]" if x["motor"] else "")
        print(f"{x['player']:24s} {x['team']} {x['leg']:9s} @{x['price']:<5} "
              f"implied {x['implied']:.0%}  box {x['p_box']:.0%} (mean {x['box_mean']:.1f}, "
              f"{x['min']:.0f}m)  hist {x['p_hist']:.0%}  seen {x['p_seen']:.0%}/{x['n_seen']}g  last10 {x['recent']}  ev {x['ev']:+.0%}{tag}")
        for f in x["flags"][:2]:
            print("    - " + f)


if __name__ == "__main__":
    main()
