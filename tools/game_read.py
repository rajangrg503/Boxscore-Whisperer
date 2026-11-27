"""Game read: what WE expect from each player tonight, from the matchup and his own history.

    python3 tools/game_read.py 2026-11-27                 # every game with saved lines
    python3 tools/game_read.py 2026-11-27 --game MIA@NYK

karma, 26 Nov 2026: "i want it to give the actual read of the game and not sportsbet land. i dont care
when i check the lines and its higher price or lower than i expected. based on the game it could give me
grimes 7 3s or sga 15pts. but it should be what it sees of the match up, similar game history, advantage".

So nothing here comes from a price. For each rotation player and each of PTS / REB / AST / 3PM:

  1. BASE      his per-minute rate this season x the minutes he should play tonight.
  2. THE READ  adjustments, each one shown with its reason:
                 - ONE opponent adjustment: his history v THIS opponent (per-minute, last three seasons,
                   4+ games, shrunk hard when games are few) averaged with what this opponent allows players
                   of his type this season. Averaged, not multiplied: they are two views of the same games.
                   Capped at 18% (12% for threes).
                 - the projected slate's lift when teammates are out (once the night is projected)
  3. HIS GAMES this season, each rescaled to tonight's minutes and the read, give the spread:
                 SAFE pick   = the highest rung he clears in 80%+ of those games  ("SGA 15+ points")
                 UPSIDE pick = the highest rung he clears in 25%+ of them        ("Grimes 7+ threes")
     ordered by how much the read moves his chance at that number (against his plain season at his usual
     minutes), and only stats he produces in volume (10+ points, 4+ rebounds, 3+ assists, 1.5+ threes). Beside each pick sits
     what he ACTUALLY did at that rung this season and v this opponent, so a confident read on thin history
     shows (27 Nov: Brunson 5+ threes read at 39% when he had done it 3 times in 18).

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
NAMES = {"PTS": "points", "REB": "rebounds", "AST": "assists", "FG3M": "threes", "PR": "points + rebounds",
         "PA": "points + assists", "RA": "rebounds + assists", "PRA": "points + rebounds + assists"}
# Combos are read too, so every leg the builder can use has a read chance; picks stay single stats.
COMBOS = ("PR", "PA", "RA", "PRA")
RUNGS.update({"PR": list(range(10, 65, 5)), "PA": list(range(10, 65, 5)), "RA": list(range(4, 32, 2)),
              "PRA": list(range(10, 75, 5))})
SAFE, UPSIDE = 0.80, 0.25
# Only a stat he produces in real volume is a pick: a 15% lift on 1.3 assists is a fifth of an assist
# (26 Nov 2026: Ryan Dunn 2+ assists topped DAL@PHX). Floors on the read's expectation.
VOLUME = {"PTS": 10, "REB": 4, "AST": 3, "FG3M": 1.5}
MIN_LIFT = 0.03                                         # an upside pick needs the read to add 3+ points of chance
# How far the read was off in the walk-forward check, 1-25 Nov 2026 (claude/game-read-backtest-2026-11-26.md):
# upside picks hit 2 points under "Read says", floors 8 under. Used when the read is set against the price.
CALIBRATION = {"upside": -0.02, "safe": -0.08}


def _clip(x, lo, hi):
    return max(lo, min(hi, x))


_BODIES = None


def _bodies():
    """{pid: (position, inches, pounds)} from every cached roster."""
    global _BODIES
    if _BODIES is None:
        from engine import archetype as arch
        from engine.cache import read_payload
        from engine.team_ids import TEAM_ID_BY_ABBR
        _BODIES = {}
        for team_id in set(TEAM_ID_BY_ABBR.values()):
            for r in (read_payload(f"roster_{team_id}") or {}).get("data") or []:
                _BODIES[int(r["PLAYER_ID"])] = (str(r.get("POSITION") or ""), arch.inches(r.get("HEIGHT")),
                                                arch.pounds(r.get("WEIGHT")))
    return _BODIES


def player_type(pid):
    """guard / wing / big for the opponent-allows read. engine.archetype calls anyone under 206 lb a guard,
    which made 6-8 forward Risacher a guard (26 Nov 2026); here a listed forward is a wing unless he is a big,
    a listed guard is a guard, and a G-F is a guard only at 6-5, or 6-6 and light."""
    pos, tall, heavy = _bodies().get(int(pid), ("", 0, 0))
    if not tall:
        import opp_allows as oa
        return oa.kind(pid)
    if pos in ("C", "C-F", "F-C") or (tall > 81 and heavy >= 225) or (tall >= 81 and heavy >= 232):
        return "big"
    if pos.startswith("F"):
        return "wing"
    if pos == "G" or tall <= 77 or (tall <= 78 and heavy < 206):
        return "guard"
    return "wing"


def opponent_allows(season):
    """{stat: {opp: {kind: (ratio, n)}}}: what each team gives up to guards / wings / bigs this season."""
    import opp_allows as oa
    logs = oa.logs(season)
    return {stat: oa.allows_all(stat, L=logs, kind_fn=player_type) for stat in STATS + COMBOS}, player_type


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
    for stat in STATS + COMBOS:
        total_min = sum(pc.nf(x["MIN"]) for x in season)
        per_min = sum(pc.value(x, stat) for x in season) / total_min if total_min else 0
        if per_min <= 0:
            continue
        why, mult = [], 1.0
        # ONE opponent adjustment. His own games v them and what they allow his type are two views of the same
        # thing (his games are part of what they allowed), so they are averaged, never multiplied together.
        opp_moves = []
        ratios = pb._h2h_ratios(prior, opp, stat)
        if len(ratios) >= 4:                            # few games count for little: 4 games move him a third of the way
            move = (st.mean(ratios) - 1) * len(ratios) / (len(ratios) + 8)
            if abs(move) >= 0.02:
                why.append(f"v {opp} he has run at {st.mean(ratios):.2f}x his usual rate over {len(ratios)} games")
                opp_moves.append(move)
        if allows and kind:
            ratio, n = allows.get(stat, {}).get(opp, {}).get(kind(pid), (1.0, 0))
            if n >= 15 and abs(ratio - 1) >= 0.04:
                why.append(f"{opp} allow {kind(pid)}s {ratio:.2f}x their usual {NAMES[stat]}")
                opp_moves.append((ratio - 1) * 0.5)
        if opp_moves:
            cap = 0.12 if stat == "FG3M" else 0.18      # threes swing the most from night to night
            mult *= 1 + _clip(st.mean(opp_moves), -cap, cap)
        if lift and stat in lift and abs(lift[stat] - 1) >= 0.02:
            why.append(f"teammates out lift his {NAMES[stat]} {lift[stat]:.2f}x (projected slate)")
            mult *= _clip(lift[stat], 0.85, 1.30)
        if abs(minutes - usual) >= 2:
            why.append(f"{minutes:.0f} minutes expected against his usual {usual:.0f}")
        games = [pc.value(x, stat) / pc.nf(x["MIN"]) * minutes * mult for x in season if pc.nf(x["MIN"]) >= 15]
        if len(games) < 5:
            continue
        share = lambda rung: sum(g >= rung for g in games) / len(games)  # noqa: E731
        # What the read adds: his chance at the rung as read minus his plain season (usual minutes, no
        # adjustment). Smoothed with a normal curve, because counting rescaled games against a rung jumps when
        # a 5% nudge carries several 1.9s over 2 (26 Nov 2026: Clayton 2+ threes, x1.05, showed +15 points).
        plain = [pc.value(x, stat) / pc.nf(x["MIN"]) * usual for x in season if pc.nf(x["MIN"]) >= 15]
        mu_r, sd_r = st.mean(games), (st.pstdev(games) or 1.0)
        mu_p, sd_p = st.mean(plain), (st.pstdev(plain) or 1.0)
        adds = lambda rung: None if rung is None else round(  # noqa: E731
            pb._norm_cdf((mu_r - rung + 0.5) / sd_r) - pb._norm_cdf((mu_p - rung + 0.5) / sd_p), 3)
        safe = max((r for r in RUNGS[stat] if share(r) >= SAFE), default=None)
        upside = max((r for r in RUNGS[stat] if UPSIDE <= share(r) <= 0.60 and (safe is None or r > safe)), default=None)
        raw = [pc.value(x, stat) for x in season]       # what he actually did, untouched by the read
        versus = [pc.value(x, stat) for x in prior if pc.ALIAS.get(pc.opp_of(x), pc.opp_of(x)) == opp]
        v_min = st.mean(pc.nf(x["MIN"]) for x in prior if pc.ALIAS.get(pc.opp_of(x), pc.opp_of(x)) == opp) if versus else 0
        then = f" at {v_min:.0f} min" if versus and abs(v_min - minutes) >= 4 else ""   # a different role back then
        record = lambda rung: None if rung is None else {  # noqa: E731
            "season": f"{sum(v >= rung for v in raw)} of {len(raw)}",
            "v_opp": (f"{sum(v >= rung for v in versus)} of {len(versus)}{then}" if versus else "--")}
        out["stats"][stat] = {"expected": round(per_min * minutes * mult, 1), "season": round(per_min * usual, 1),
                              "mult": round(mult, 3), "why": why,
                              "safe_record": record(safe), "upside_record": record(upside),
                              "safe_lift": adds(safe), "upside_lift": adds(upside),
                              "safe": safe, "safe_share": round(share(safe), 2) if safe else None,
                              "upside": upside, "upside_share": round(share(upside), 2) if upside else None,
                              "best": round(max(games), 0),
                              # share of his games, as read, reaching every whole number: the read's chance for
                              # any rung or line Sportsbet offers (string keys so it survives the JSON feed)
                              "chance": {str(r): round(share(r), 3) for r in range(1, int(max(games)) + 2)}}
    return out if out["stats"] else None


def game_shape(away, home):
    """The team-v-team lens (pace, possession battle, what each side gives up), as lines of text."""
    try:
        import game_lens as gl
        return gl.lens(away, home, gl.profiles(gl.load())).splitlines()[1:]
    except Exception:                                   # noqa: BLE001 -- the lens is context, never a blocker
        return []


def _roster(team):
    """(name, player id) for a team's cached roster; empty when the cache has none."""
    try:
        import slate_projections
        return slate_projections.roster(team)
    except Exception:                                   # noqa: BLE001
        return []


def read_night(date, legs=None, game=None):
    """{game: {"shape": [...], "players": [player_read...], "picks": [...]}} for every game of the night.
    Games with saved lines use the players Sportsbet lists; games without use the team rosters."""
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
    # Games Sportsbet has no player lines for yet are still read, from the rosters: the read never needs a price.
    injuries = pb.load_injuries(date).get("teams") or {}
    for g in pb.night_games(date):
        if g in by_game or (game and g != game):
            continue
        away, home = g.split("@")
        for team, opp in ((away, home), (home, away)):
            listed = injuries.get(team, {})
            out_ids = {e.get("pid") for e in listed.get("out", [])}
            dtd_ids = {e.get("pid") for e in listed.get("dtd", [])}
            for name, pid in _roster(team):
                if pid in seen or pid in out_ids:
                    continue
                seen.add(pid)
                s = slate.get(pid, {})
                read = player_read(pid, name, team, opp, day, minutes=s.get("min"), lift=s.get("lift"), allows=allows, kind=kind)
                if read and read["minutes"] >= 18:
                    read["dtd"] = pid in dtd_ids
                    by_game.setdefault(g, []).append(read)
    out = {}
    for g, players in by_game.items():
        picks = []
        for p in players:
            for stat, s in p["stats"].items():
                if stat not in STATS or s["expected"] < VOLUME[stat]:
                    continue                            # not a stat he produces in volume
                for kind_, rung, share, rec, lift in (
                        ("safe", s["safe"], s["safe_share"], s["safe_record"], s.get("safe_lift")),
                        ("upside", s["upside"], s["upside_share"], s["upside_record"], s.get("upside_lift"))):
                    if rung is None or (kind_ == "safe" and rung == RUNGS[stat][0]):
                        continue                        # a floor at the lowest rung says nothing
                    if kind_ == "upside" and (lift or 0) < MIN_LIFT:
                        continue                        # upside only where the read moves his chance
                    picks.append({"kind": kind_, "player": p["player"], "pid": p["pid"], "team": p["team"], "stat": stat,
                                  "rung": rung, "share": share, "mult": s["mult"], "expected": s["expected"],
                                  "why": s["why"], "dtd": p["dtd"], "season": rec["season"], "v_opp": rec["v_opp"],
                                  "lift": lift or 0.0,
                                  "price": price.get((p["pid"], stat, float(rung)))
                                  or price.get((p["pid"], stat, rung))})
        for k in picks:                                 # the read against the price, after the backtest's correction
            k["edge"] = (round(k["share"] + CALIBRATION[k["kind"]] - 1 / k["price"], 3) if k.get("price") else None)
        picks.sort(key=lambda k: (-k["lift"], -k["share"]))      # most chance added by the read first
        away, home = g.split("@")
        out[g] = {"shape": game_shape(away, home), "players": sorted(players, key=lambda p: -p["minutes"]), "picks": picks}
    return out


def calibrated(share):
    """The read's chance corrected by the backtest: floor-like chances (75%+) ran 8 points high, the rest 2."""
    return share + (CALIBRATION["safe"] if share >= 0.75 else CALIBRATION["upside"])


def annotate_legs(legs, night):
    """Put the read on every leg: x["read"] = our chance (after the backtest correction) and x["read_edge"] =
    that minus the chance Sportsbet's price implies. None where the player wasn't read (under 18 minutes,
    too few games). The builder tabs can then use only legs our read rates at or above the price."""
    table = {(p["pid"], stat): s.get("chance") or {} for r in night.values() for p in r["players"]
             for stat, s in p["stats"].items()}
    for x in legs:
        x["read"] = x["read_edge"] = None
        chance = table.get((x["pid"], x["stat"]))
        if not chance:
            continue
        rung = int(round(x["th"])) if x["kind"] == "alt" else int(math.floor(x["th"])) + 1
        top = max(int(k) for k in chance)
        over = 1.0 if rung < 1 else 0.0 if rung > top else chance.get(str(rung), 0.0)
        read = (1 - over) if x["kind"] == "under" else calibrated(over)
        x["read"] = round(min(max(read, 0.01), 0.99), 3)
        x["read_edge"] = round(x["read"] - 1 / x["price"], 3)
    return legs


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
                price = (f"Sportsbet {k['price']:.2f} (read v price {100 * k['edge']:+.0f} pts)" if k["price"]
                         else "no price saved")
                print(f"    {pick_label(k):34s} read {100 * k['share']:.0f}% ({100 * k['lift']:+.0f} pts) | actually {k['season']} this season, {k['v_opp']} v them"
                      f" | expects {k['expected']:g} | read x{k['mult']:.2f} | {price}{' | DAY-TO-DAY' if k['dtd'] else ''}")
                for w in k["why"]:
                    print(f"        - {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
