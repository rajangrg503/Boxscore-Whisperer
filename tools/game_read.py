"""Game read: what WE expect from each player tonight, from the matchup and his own history.

    python3 tools/game_read.py 2026-11-27                 # every game with saved lines
    python3 tools/game_read.py 2026-11-27 --game MIA@NYK

karma, 26 Nov 2026: "i want it to give the actual read of the game and not sportsbet land. i dont care
when i check the lines and its higher price or lower than i expected. based on the game it could give me
grimes 7 3s or sga 15pts. but it should be what it sees of the match up, similar game history, advantage".

So nothing here comes from a price. For each rotation player and each of PTS / REB / AST / 3PM:

  1. BASE      his per-minute rate this season x the minutes he should play tonight.
  2. THE READ  multipliers, each one shown with its reason:
                 - his history v THIS opponent (per-minute, last three seasons, shrunk when games are few)
                 - what this opponent allows players of his type (guard / wing / big) this season
                 - the projected slate's lift when teammates are out (once the night is projected)
  3. HIS GAMES this season, each rescaled to tonight's minutes and the read, give the spread:
                 SAFE pick   = the highest rung he clears in 80%+ of those games  ("SGA 15+ points")
                 UPSIDE pick = the highest rung he clears in 25%+ of them        ("Grimes 7+ threes")
     and the players are ordered by how big the advantage is (the read's multiplier).

The Sportsbet price for a pick, when one is saved, is looked up afterwards and shown last.

HONEST LIMITS. On the 23 saved nights, reads like these did not beat the price (see parlay_builder's
scoreboard), and nothing here knows who guards whom or how the game script goes. Every pick is written to
the builder's log before the games so this read gets its own record.
"""
import argparse
import datetime as dt
import math
import os
import statistics as st
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import parlay_builder as pb  # noqa: E402
import prop_context as pc  # noqa: E402

STATS = ("PTS", "REB", "AST", "FG3M")
RUNGS = {"PTS": [5, 10, 15, 20, 25, 30, 35, 40, 45, 50], "REB": [2, 4, 6, 8, 10, 12, 14, 16, 18],
         "AST": [2, 4, 6, 8, 10, 12, 14], "FG3M": [1, 2, 3, 4, 5, 6, 7, 8]}
NAMES = {"PTS": "points", "REB": "rebounds", "AST": "assists", "FG3M": "threes"}
SAFE, UPSIDE = 0.80, 0.25


def _clip(x, lo, hi):
    return max(lo, min(hi, x))


def opponent_allows(season):
    """{stat: {opp: {kind: (ratio, n)}}}: what each team gives up to guards / wings / bigs this season."""
    import opp_allows as oa
    logs = oa.logs(season)
    return {stat: oa.allows_all(stat, L=logs) for stat in STATS}, oa.kind


def player_read(pid, name, team, opp, day, minutes=None, lift=None, allows=None, kind=None):
    """His expected line and spread tonight, by stat, with every adjustment named. None if too few games."""
    last = pc.prev_season(pc.season_of(day))
    prior = [x for x in pc.rows_for(pid, [pc.prev_season(last), last, pc.season_of(day)]) if pc._date(x) < day]
    season = [x for x in prior if pc.season_of(pc._date(x)) == pc.season_of(day) and pc.nf(x["MIN"]) >= 10]
    if len(season) < 5:
        return None
    usual = st.mean(pc.nf(x["MIN"]) for x in season[:10])
    minutes = minutes or usual
    out = {"pid": pid, "player": name, "team": team, "opp": opp, "minutes": round(minutes, 1),
           "usual_minutes": round(usual, 1), "games": len(season), "stats": {}}
    for stat in STATS:
        total_min = sum(pc.nf(x["MIN"]) for x in season)
        per_min = sum(pc.value(x, stat) for x in season) / total_min if total_min else 0
        if per_min <= 0:
            continue
        why, mult = [], 1.0
        ratios = pb._h2h_ratios(prior, opp, stat)
        if len(ratios) >= 3:
            m = _clip(1 + (st.mean(ratios) - 1) * len(ratios) / (len(ratios) + 4), 0.80, 1.25)
            if abs(m - 1) >= 0.03:
                why.append(f"v {opp} he has run at {st.mean(ratios):.2f}x his usual rate over {len(ratios)} games")
                mult *= m
        if allows and kind:
            ratio, n = allows.get(stat, {}).get(opp, {}).get(kind(pid), (1.0, 0))
            if n >= 15:
                m = _clip(1 + (ratio - 1) * 0.5, 0.90, 1.12)
                if abs(m - 1) >= 0.02:
                    why.append(f"{opp} allow {kind(pid)}s {ratio:.2f}x their usual {NAMES[stat]}")
                    mult *= m
        if lift and stat in lift and abs(lift[stat] - 1) >= 0.02:
            why.append(f"teammates out lift his {NAMES[stat]} {lift[stat]:.2f}x (projected slate)")
            mult *= _clip(lift[stat], 0.85, 1.30)
        if abs(minutes - usual) >= 2:
            why.append(f"{minutes:.0f} minutes expected against his usual {usual:.0f}")
        games = [pc.value(x, stat) / pc.nf(x["MIN"]) * minutes * mult for x in season if pc.nf(x["MIN"]) >= 15]
        if len(games) < 5:
            continue
        share = lambda rung: sum(g >= rung for g in games) / len(games)  # noqa: E731
        safe = max((r for r in RUNGS[stat] if share(r) >= SAFE), default=None)
        upside = max((r for r in RUNGS[stat] if UPSIDE <= share(r) <= 0.60 and (safe is None or r > safe)), default=None)
        out["stats"][stat] = {"expected": round(per_min * minutes * mult, 1), "season": round(per_min * usual, 1),
                              "mult": round(mult, 3), "why": why,
                              "safe": safe, "safe_share": round(share(safe), 2) if safe else None,
                              "upside": upside, "upside_share": round(share(upside), 2) if upside else None,
                              "best": round(max(games), 0)}
    return out if out["stats"] else None


def game_shape(away, home):
    """The team-v-team lens (pace, possession battle, what each side gives up), as lines of text."""
    try:
        import game_lens as gl
        return gl.lens(away, home, gl.profiles(gl.load())).splitlines()[1:]
    except Exception:                                   # noqa: BLE001 -- the lens is context, never a blocker
        return []


def read_night(date, legs=None, game=None):
    """{game: {"shape": [...], "players": [player_read...], "picks": [...]}} for every game with saved lines."""
    day = dt.date.fromisoformat(date)
    legs = legs if legs is not None else pb.load_legs(date)
    try:
        allows, kind = opponent_allows(pc.season_of(day))
    except Exception:                                   # noqa: BLE001
        allows, kind = None, None
    slate = {p["pid"]: p for g in (pc.slate_file(date) or {}).get("games", [])
             for players in g.get("players", {}).values() for p in players}
    price = {(x["pid"], x["stat"], x["th"]): x["price"] for x in legs if x["kind"] == "alt"}
    seen, by_game = set(), {}
    for x in legs:
        if (game and x["game"] != game) or x["pid"] in seen or not x.get("team") or x.get("is_out"):
            continue
        seen.add(x["pid"])
        s = slate.get(x["pid"], {})
        read = player_read(x["pid"], x["player"], x["team"], x["opp"], day, minutes=s.get("min"),
                           lift=s.get("lift"), allows=allows, kind=kind)
        if read and read["minutes"] >= 18:
            read["dtd"] = bool(x.get("dtd"))
            by_game.setdefault(x["game"], []).append(read)
    out = {}
    for g, players in by_game.items():
        picks = []
        for p in players:
            for stat, s in p["stats"].items():
                for kind_, rung, share in (("safe", s["safe"], s["safe_share"]), ("upside", s["upside"], s["upside_share"])):
                    if rung is None or (kind_ == "safe" and rung == RUNGS[stat][0]):
                        continue                        # a floor at the lowest rung says nothing
                    if kind_ == "upside" and s["mult"] < 1.05:
                        continue                        # upside only where the read sees an advantage
                    picks.append({"kind": kind_, "player": p["player"], "pid": p["pid"], "team": p["team"], "stat": stat,
                                  "rung": rung, "share": share, "mult": s["mult"], "expected": s["expected"],
                                  "why": s["why"], "dtd": p["dtd"], "price": price.get((p["pid"], stat, float(rung)))
                                  or price.get((p["pid"], stat, rung))})
        picks.sort(key=lambda k: (-k["mult"], -k["share"]))
        away, home = g.split("@")
        out[g] = {"shape": game_shape(away, home), "players": sorted(players, key=lambda p: -p["minutes"]), "picks": picks}
    return out


def pick_label(k):
    return f"{k['player']} {k['rung']}+ {NAMES[k['stat']]}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("date")
    ap.add_argument("--game")
    a = ap.parse_args(argv)
    night = read_night(a.date, game=a.game)
    if not night:
        print(f"No saved lines for {a.date}: the read uses them only to know who is playing.")
        return 1
    for g, r in night.items():
        print(f"\n=== {g}")
        for line in r["shape"]:
            print("  " + line)
        for title, kind_ in (("Biggest advantages (upside picks)", "upside"), ("Floors (safe picks)", "safe")):
            rows = [k for k in r["picks"] if k["kind"] == kind_][:8]
            print(f"\n  {title}")
            for k in rows:
                price = f"Sportsbet {k['price']:.2f}" if k["price"] else "no price saved"
                print(f"    {pick_label(k):34s} clears it in {100 * k['share']:.0f}% of his games as read"
                      f" | expects {k['expected']:g} | read x{k['mult']:.2f} | {price}{' | DAY-TO-DAY' if k['dtd'] else ''}")
                for w in k["why"]:
                    print(f"        - {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
