#!/usr/bin/env python3
"""How does THIS player score against the defenders he'll actually see tonight?

    python3 tools/defender_matchups.py "Austin Reaves" DET
    python3 tools/defender_matchups.py 1630559 DET --season 2025-26

karma, 9 Nov 2026: measure the matchup advantage, not just the team. Our
opp_allows.py reads a defence by ROLE (what it gives guards / wings / bigs).
This reads it by DEFENDER: the NBA's matchup tracking says who guarded a
player, for how many partial possessions, and what he scored on them.

For the player and tonight's opponent it takes every defender on the
opponent's CURRENT roster, adds up what the player did against them, and
compares it with what he did against everyone (his own norm):

    factor = (his pts per 100 possessions vs these defenders)
             / (his pts per 100 possessions vs all defenders)

The factor is shrunk toward 1.0 with a prior of K possessions, so a few
lucky trips don't read as an edge. AST, TOV, FG% and FTA rates get the same
treatment. Rebounds are NOT in the matchup data: this says nothing about REB.

DATA
stats.nba.com is blocked from the device shell and the cloud container but
works from Chrome on an nba.com page. The fetcher there saves one file per
player, season and opponent to data/matchups/<season>/<pid>_<oppTeamId>.json:

    {"pid", "season", "opp_tid", "roster_asof",
     "totals": {"poss","pts","ast","tov","fgm","fga","fg3m","fg3a","ftm","fta","sfl"},
     "vs": [[def_id, def_name, gp, poss, pts, ast, tov, fgm, fga, fg3m, fg3a, ftm, fta, sfl], ...]}

WHAT THIS IS NOT
A check beside the chance, not a pick source and not folded into it. A
season of matchups vs one roster is a small, noisy sample, and last
season's defenders may guard him differently this year.
"""
import argparse
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(ROOT, "data", "matchups")
K = 60.0            # prior, in partial possessions
MIN_POSS = 40.0     # below this vs the roster, call it "too few"
FIELDS = ["def_id", "def_name", "gp", "poss", "pts", "ast", "tov", "fgm", "fga",
          "fg3m", "fg3a", "ftm", "fta", "sfl"]
TEAM_IDS = {"ATL": 1610612737, "BOS": 1610612738, "BKN": 1610612751, "CHA": 1610612766,
            "CHI": 1610612741, "CLE": 1610612739, "DAL": 1610612742, "DEN": 1610612743,
            "DET": 1610612765, "GSW": 1610612744, "HOU": 1610612745, "IND": 1610612754,
            "LAC": 1610612746, "LAL": 1610612747, "MEM": 1610612763, "MIA": 1610612748,
            "MIL": 1610612749, "MIN": 1610612750, "NOP": 1610612740, "NYK": 1610612752,
            "OKC": 1610612760, "ORL": 1610612753, "PHI": 1610612755, "PHX": 1610612756,
            "POR": 1610612757, "SAC": 1610612758, "SAS": 1610612759, "TOR": 1610612761,
            "UTA": 1610612762, "WAS": 1610612764}
ALIAS = {"NO": "NOP", "WSH": "WAS", "UTAH": "UTA", "SA": "SAS", "GS": "GSW", "NY": "NYK", "PHO": "PHX"}


def team_id(abbr):
    if isinstance(abbr, int) or str(abbr).isdigit():
        return int(abbr)
    a = ALIAS.get(abbr.upper(), abbr.upper())
    return TEAM_IDS[a]


def path(pid, opp_tid, season):
    return os.path.join(DIR, season, f"{int(pid)}_{int(opp_tid)}.json")


def load(pid, opp_tid, season):
    p = path(pid, opp_tid, season)
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return json.load(f)


def _rate(num, poss):
    return 100.0 * num / poss if poss else None


def _shrunk_factor(num_vs, poss_vs, norm_rate):
    """(num_vs + prior) / (poss_vs + K) against the norm, as a ratio."""
    if not norm_rate:
        return None
    prior = norm_rate / 100.0 * K
    return ((num_vs + prior) / (poss_vs + K) * 100.0) / norm_rate


def summarise(entry, top=4):
    """The numbers behind one player vs one roster."""
    if not entry:
        return None
    t = entry["totals"]
    vs = [dict(zip(FIELDS, r)) for r in entry.get("vs", [])]
    agg = {k: sum(float(r[k] or 0) for r in vs) for k in ("poss", "pts", "ast", "tov", "fgm", "fga", "fta")}
    out = {"season": entry["season"], "poss_all": t["poss"], "poss_vs": agg["poss"],
           "too_few": agg["poss"] < MIN_POSS}
    for stat in ("pts", "ast", "tov", "fta"):
        norm = _rate(t[stat], t["poss"])
        out[f"{stat}_norm"] = norm
        out[f"{stat}_vs"] = _rate(agg[stat], agg["poss"])
        out[f"{stat}_factor"] = _shrunk_factor(agg[stat], agg["poss"], norm)
    out["fg_norm"] = t["fgm"] / t["fga"] if t["fga"] else None
    out["fg_vs"] = agg["fgm"] / agg["fga"] if agg["fga"] else None
    out["defenders"] = [
        {"name": r["def_name"], "poss": r["poss"], "gp": r["gp"], "pts": r["pts"],
         "pts100": _rate(r["pts"], r["poss"]), "fg": f"{int(r['fgm'])}/{int(r['fga'])}"}
        for r in sorted(vs, key=lambda r: -float(r["poss"] or 0))[:top]]
    out["flag"] = flag(out)
    return out


def flag(s):
    """One word for the screen: 'struggles', 'feasts', 'thin' or ''."""
    if s is None:
        return "no data"
    if s["too_few"]:
        return "thin"
    f = s["pts_factor"]
    if f is not None and f <= 0.85:
        return "struggles"
    if f is not None and f >= 1.15:
        return "feasts"
    return ""


def check(pid, opp, seasons=("2026-27", "2025-26")):
    """Summaries for each season we have, newest first."""
    tid = team_id(opp)
    return [s for s in (summarise(load(pid, tid, se)) for se in seasons) if s]


def parse_line(line, season, asof=None):
    """One line from the Chrome fetcher -> one cache entry.

    pid|oppTid|poss,pts,ast,tov,fgm,fga,fta|id~name~gp~poss~pts~ast~tov~fgm~fga~fta;...
    The fetcher keeps the 3 defenders with the most possessions and sums the
    rest of the roster into one 'rest' row, which is all summarise() needs.
    """
    pid, tid, tot, vs = (line.strip().split("|") + [""])[:4]
    t = [float(x) for x in tot.split(",")]
    totals = dict(zip(("poss", "pts", "ast", "tov", "fgm", "fga", "fta"), t))
    totals.update(fg3m=0, fg3a=0, ftm=0, sfl=0)
    rows = []
    for part in filter(None, vs.split(";")):
        d = part.split("~")
        did, name, gp, poss, pts, ast, tov, fgm, fga, fta = d
        rows.append([int(did), name, int(float(gp)), float(poss), float(pts), float(ast), float(tov),
                     float(fgm), float(fga), 0, 0, 0, float(fta), 0])
    return {"pid": int(pid), "season": season, "opp_tid": int(tid), "roster_asof": asof,
            "totals": totals, "vs": rows}


def import_lines(path_in, season, asof=None):
    n = 0
    os.makedirs(os.path.join(DIR, season), exist_ok=True)
    for line in open(path_in, encoding="utf-8"):
        if "|" not in line:
            continue
        e = parse_line(line, season, asof)
        with open(path(e["pid"], e["opp_tid"], season), "w", encoding="utf-8") as f:
            json.dump(e, f, ensure_ascii=False)
        n += 1
    return n


def _fmt(x, nd=1):
    return "-" if x is None else f"{x:.{nd}f}"


def report(name, opp, sums):
    lines = [f"{name} vs {opp} defenders (current roster)"]
    if not sums:
        lines.append("  no matchup data cached: fetch it in Chrome first")
    for s in sums:
        lines.append(f"  {s['season']}: {_fmt(s['poss_vs'],0)} poss vs them (of {_fmt(s['poss_all'],0)})"
                     f"  pts/100 {_fmt(s['pts_vs'])} vs norm {_fmt(s['pts_norm'])}"
                     f"  -> factor {_fmt(s['pts_factor'],2)}  {s['flag']}")
        lines.append(f"     FG {_fmt((s['fg_vs'] or 0)*100,0)}% (norm {_fmt((s['fg_norm'] or 0)*100,0)}%)"
                     f"  AST x{_fmt(s['ast_factor'],2)}  TOV x{_fmt(s['tov_factor'],2)}  FTA x{_fmt(s['fta_factor'],2)}")
        for d in s["defenders"]:
            lines.append(f"     {d['name']:<24} {d['poss']:>5.0f} poss, {d['pts']:>3.0f} pts ({_fmt(d['pts100'])}/100), FG {d['fg']}")
    return "\n".join(lines)


def _pid(name_or_id):
    if str(name_or_id).isdigit():
        return int(name_or_id), str(name_or_id)
    from nba_api.stats.static import players
    hit = [p for p in players.get_players() if p["full_name"].lower() == name_or_id.lower()]
    if not hit:
        sys.exit(f"no player called {name_or_id!r}")
    return hit[0]["id"], hit[0]["full_name"]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("player", nargs="?")
    ap.add_argument("opp", nargs="?")
    ap.add_argument("--season", action="append")
    ap.add_argument("--import", dest="import_file", help="lines saved from the Chrome fetcher")
    ap.add_argument("--asof", help="roster date for --import")
    a = ap.parse_args()
    if a.import_file:
        season = (a.season or ["2026-27"])[0]
        print(f"imported {import_lines(a.import_file, season, a.asof)} entries for {season}")
        return
    pid, name = _pid(a.player)
    print(report(name, a.opp, check(pid, a.opp, tuple(a.season) if a.season else ("2026-27", "2025-26"))))


if __name__ == "__main__":
    main()
