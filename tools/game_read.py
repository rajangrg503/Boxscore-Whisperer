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
import collections
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
VOLUME = {"PTS": 10, "REB": 4, "AST": 3, "FG3M": 1.5, "PA": 15}
# Pass-first drivers (karma; NBA Drives: they pass on half or more of their drives): some nights they score,
# some they set up, so their pick is points + assists, never points or assists alone (27 Nov: Davion 12 pts /
# 9 ast missed 15+ points but landed 20+ PA; Nembhard scored instead of passing).
PASS_FIRST = {"Davion Mitchell", "Andrew Nembhard", "Josh Giddey", "Deni Avdija", "Franz Wagner"}
PASS_FIRST_STATS = ("PA", "REB", "FG3M")
# Steady: a rung he cleared in 7+ of his last 10, priced, whatever the matchup says. Biggest advantages only shows
# where the matchup moves a player, so a player who just does his job every night never showed (karma, 27 Nov:
# "coward has been so consistent. why isnt he showing up"). STEADY is karma's own list of reliable players.
STEADY = {"Cedric Coward"}
# karma, 28 Nov 2026: "a list of 80% or above vs this team. or 4 of 5, 6 of 8" every night. His highest rung
# cleared in 80%+ of his games v tonight's opponent (last three seasons, 5+ games), every stat and combo.
VTHEM_SHARE, VTHEM_MIN = 0.80, 5
STEADY_SHARE, STEADY_STATS = 0.70, ("PTS", "REB", "AST", "FG3M", "PR", "PA", "RA", "PRA")
# Who feeds against Denver (angles 27 and 29, karma): their help defence gives up the most drive assists in the
# league and Jokic in drop gives the opposing big room; their wins are close (27 Nov: UTA's George 24+,
# Peterson 20+, Nurkic 10 ast in a 2-point game at the 4th).
DEN_NOTE = ("v DEN: the other side's three-level drivers (points, points + assists), its big (assists or points + "
            "rebounds) and catch-and-shoot threes off the help; DEN games stay close, so stars play the 4th")
MIN_LIFT = 0.03                                         # an upside pick needs the read to add 3+ points of chance
# Tonight's minutes: the slate projection, but never below his usual. Backtest 1-25 Nov 2026 (~106k player-rung
# checks, ~/bt/mins/bt.py): reads at the projection ran 5 points LOW in the 25-60% zone (41.7% read, 46.4%
# landed) because the projection sometimes cuts a starter by 4-5 minutes (27 Nov: Zion 29 v 33, Murphy 27 v 33);
# at max(projection, usual) the same zone read 41.7% and landed 42.0%.
MINUTES_FLOOR_USUAL = True
# The opponent adjustment (his games v them, what they allow his role). H2H_K: v-them games count n/(n+K) of
# the way. OPP_CAP: the most it can move his rate (threes, the rest). H2H_ALONE: with H2H_ALONE+ games v them,
# his own record stands on its own instead of being averaged with what they allow.
H2H_K = 8
OPP_CAP = (0.12, 0.18)
H2H_ALONE = None
# What a "Read says" chance actually landed at in that backtest (read at the higher minutes): pairs of
# (read, landed). The ends were overconfident (reads under 10% landed 13%, reads over 90% landed 85%), the
# middle was right. calibrated() maps through these points; it replaces the flat -2 / -8 points of 26 Nov,
# which were measured at usual minutes and double-penalised the projection-minutes read.
CAL_POINTS = ((0.0, 0.10), (0.024, 0.13), (0.163, 0.22), (0.404, 0.41), (0.664, 0.635), (0.821, 0.757),
              (0.966, 0.85), (1.0, 0.87))
COIN_FLIP = 0.03                                        # within +/-3 points of the price: too close to call


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


# Roles from what a player does, not how he is listed (karma, 28 Nov: "separate and pinpoint their position";
# he checked the roles and said "that looks perfect"). Per 36 minutes this season, plus height. The opponent read
# groups them into guard / wing / big. Backtest 1-25 Nov, players whose group changed (6.4k player-rungs): Brier
# 0.1930 -> 0.1912, better for every stat (threes 0.2063 -> 0.1978, assists 0.1904 -> 0.1880).
ROLE_GROUP = {"lead guard": "guard", "scoring guard": "guard", "guard": "guard", "point forward": "wing",
              "rebounding wing": "wing", "shooting wing": "wing", "wing": "wing", "big": "big"}
_ROLES = {}


def player_role(pid, logs=None, season=None):
    """lead guard / scoring guard / guard / point forward / rebounding wing / shooting wing / wing / big, or None
    with under 6 games of 10+ minutes this season."""
    key = (int(pid), season)
    if key in _ROLES:
        return _ROLES[key]
    role = None
    if logs is None:
        import opp_allows as oa
        logs = oa.logs(season or pc.season_of(dt.date.today()))
    rows = [r for r in logs.get(pid, logs.get(int(pid), logs.get(str(pid), []))) if pc.nf(r["MIN"]) >= 10]
    if len(rows) >= 6:
        m = sum(pc.nf(r["MIN"]) for r in rows)
        per = lambda k: 36 * sum(pc.nf(r.get(k)) for r in rows) / m  # noqa: E731
        reb, ast, tpa, pts = per("REB"), per("AST"), per("FG3A"), per("PTS")
        pos, tall, _ = _bodies().get(int(pid), ("", 0, 0))
        if pos in ("C", "C-F", "F-C") or tall >= 83 or (reb >= 10.5 and tpa < 3 and ast < 5):
            role = "big"
        elif ast >= 6.5:
            role = "point forward" if tall >= 79 else "lead guard"
        elif tall and tall <= 78 and pos.startswith("G") and pts >= 22:
            role = "scoring guard"
        elif reb >= 7.0 and tall >= 77:
            role = "rebounding wing"
        elif tall and tall <= 77 and pos.startswith("G"):
            role = "guard"
        elif tpa >= 6:
            role = "shooting wing"
        elif tall:
            role = "wing"
    _ROLES[key] = role
    return role


def opponent_allows(season):
    """{stat: {opp: {kind: (ratio, n)}}}: what each team gives up to guards / wings / bigs this season, by role."""
    import opp_allows as oa
    logs = oa.logs(season)

    def kind(pid):
        role = player_role(pid, logs, season)
        return ROLE_GROUP[role] if role else player_type(pid)
    return {stat: oa.allows_all(stat, L=logs, kind_fn=kind) for stat in STATS + COMBOS}, kind


def player_read(pid, name, team, opp, day, minutes=None, lift=None, allows=None, kind=None):
    """His expected line and spread tonight, by stat, with every adjustment named. None if too few games."""
    last = pc.prev_season(pc.season_of(day))
    prior = [x for x in pc.rows_for(pid, [pc.prev_season(last), last, pc.season_of(day)]) if pc._date(x) < day]
    season = [x for x in prior if pc.season_of(pc._date(x)) == pc.season_of(day) and pc.nf(x["MIN"]) >= 10]
    if len(season) < 5:
        return None
    usual = st.mean(pc.nf(x["MIN"]) for x in season[:10])
    minutes = (max(minutes, usual) if MINUTES_FLOOR_USUAL else minutes) if minutes else usual
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
            move = (st.mean(ratios) - 1) * len(ratios) / (len(ratios) + H2H_K)
            if abs(move) >= 0.02:
                why.append(f"v {opp} he has run at {st.mean(ratios):.2f}x his usual rate over {len(ratios)} games")
                opp_moves.append(move)
        if allows and kind and not (H2H_ALONE and len(ratios) >= H2H_ALONE):
            ratio, n = allows.get(stat, {}).get(opp, {}).get(kind(pid), (1.0, 0))
            if n >= 15 and abs(ratio - 1) >= 0.04:
                why.append(f"{opp} allow {kind(pid)}s {ratio:.2f}x their usual {NAMES[stat]}")
                opp_moves.append((ratio - 1) * 0.5)
        if opp_moves:
            cap = OPP_CAP[0] if stat == "FG3M" else OPP_CAP[1]   # threes swing the most from night to night
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
        if stat == "FG3M":                              # every game v this opponent, for the threes list
            v_log = [{"date": pc._date(x).isoformat(), "team": pc.ALIAS.get(pc.team_from_rows([x]), pc.team_from_rows([x])),
                      "min": round(pc.nf(x["MIN"])), "made": int(pc.nf(x.get("FG3M"))), "att": int(pc.nf(x.get("FG3A")))}
                     for x in prior if pc.ALIAS.get(pc.opp_of(x), pc.opp_of(x)) == opp]
            att = round(st.mean(pc.nf(x.get("FG3A")) for x in season), 1)
        out["stats"][stat] = {"expected": round(per_min * minutes * mult, 1), "season": round(per_min * usual, 1),
                              "mult": round(mult, 3), "why": why,
                              "safe_record": record(safe), "upside_record": record(upside),
                              "safe_lift": adds(safe), "upside_lift": adds(upside),
                              "safe": safe, "safe_share": round(share(safe), 2) if safe else None,
                              "upside": upside, "upside_share": round(share(upside), 2) if upside else None,
                              "best": round(max(games), 0),
                              "season_raw": round(st.mean(raw), 2) if raw else 0.0,
                              "last10": [round(v, 1) for v in raw[:10]],      # newest first, untouched by the read
                              "v_vals": [round(v, 1) for v in versus],         # every game v them, newest first
                              "v_min": round(v_min) if versus else None,
                              # share of his games, as read, reaching every whole number: the read's chance for
                              # any rung or line Sportsbet offers (string keys so it survives the JSON feed)
                              "chance": {str(r): round(share(r), 3) for r in range(1, int(max(games)) + 2)}}
        if stat == "FG3M":
            out["stats"][stat].update(v_log=v_log, season_att=att)
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
            wanted = PASS_FIRST_STATS if p["player"] in PASS_FIRST else STATS
            for stat, s in p["stats"].items():
                if stat not in wanted or s["expected"] < VOLUME[stat]:
                    continue                            # not a stat he produces in volume (PA for pass-first drivers)
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
            k["edge"] = (round(calibrated(k["share"]) - 1 / k["price"], 3) if k.get("price") else None)
            k["backed"] = backed(k["edge"])
        picks.sort(key=lambda k: (-k["lift"], -k["share"]))      # most chance added by the read first
        steady = []
        for p in players:
            best = None
            for stat in STEADY_STATS:
                s = p["stats"].get(stat)
                if not s or len(s.get("last10") or []) < 8 or s["expected"] < VOLUME.get(stat, 0):
                    continue
                last = s["last10"]
                for rung in sorted(RUNGS[stat], reverse=True):
                    hit = sum(v >= rung for v in last) / len(last)
                    if hit < STEADY_SHARE:
                        continue
                    pr = price.get((p["pid"], stat, float(rung))) or price.get((p["pid"], stat, rung))
                    if not pr:
                        continue
                    share = s["chance"].get(str(rung), 0.0)
                    edge = round(calibrated(share) - 1 / pr, 3)
                    leg = {"player": p["player"], "pid": p["pid"], "team": p["team"], "stat": stat, "rung": rung,
                           "last10": f"{sum(v >= rung for v in last)} of {len(last)}", "share": share, "price": pr,
                           "edge": edge, "backed": backed(edge), "karma": p["player"] in STEADY, "dtd": p["dtd"]}
                    if best is None or edge > best["edge"]:
                        best = leg
                    break                                   # his highest steady rung for this stat
            if best and (best["karma"] or best["edge"] > -COIN_FLIP):
                steady.append(best)
        steady.sort(key=lambda k: (not k["karma"], -k["edge"]))
        vthem = vthem_list(players, price)
        away, home = g.split("@")
        shape = game_shape(away, home)
        if "DEN" in (away, home):
            shape = list(shape) + [DEN_NOTE]
        out[g] = {"shape": shape, "players": sorted(players, key=lambda p: -p["minutes"]), "picks": picks,
                  "steady": steady, "vthem": vthem}
    return out


def vthem_list(players, price):
    """One row per player and stat: his highest rung (above the lowest) cleared in VTHEM_SHARE+ of his games v
    tonight's opponent, with his last 10 and season at that rung, the read's chance and the price if Sportsbet
    has it. Most games v them first, then the highest share. A record to look at, not a pick: the 28 Nov
    backtest found the read's v-them weighting already as strong as it should be."""
    rows = []
    for p in players:
        for stat in STATS + COMBOS:
            s = p["stats"].get(stat)
            vals = (s or {}).get("v_vals") or []
            if len(vals) < VTHEM_MIN or s["season"] < VOLUME.get(stat, 0) * 0.6:
                continue
            for rung in sorted(RUNGS[stat], reverse=True)[:-1]:
                made = sum(v >= rung for v in vals)
                if made / len(vals) < VTHEM_SHARE:
                    continue
                last = s.get("last10") or []
                read = calibrated(s["chance"].get(str(rung), 0.0))
                pr = price.get((p["pid"], stat, float(rung))) or price.get((p["pid"], stat, rung))
                edge = round(read - 1 / pr, 3) if pr else None
                rows.append({"player": p["player"], "pid": p["pid"], "team": p["team"], "opp": p.get("opp"),
                             "stat": stat, "rung": rung, "v": f"{made} of {len(vals)}",
                             "v_share": round(made / len(vals), 2), "v_n": len(vals), "v_min": s.get("v_min"),
                             "v_vals": vals[:8], "minutes": p["minutes"],
                             "last10": f"{sum(v >= rung for v in last)} of {len(last)}" if last else "--",
                             "read": round(read, 3), "price": pr, "edge": edge, "backed": backed(edge),
                             "dtd": p.get("dtd", False)})
                break                                   # his highest rung at 80%+ for this stat
    rows.sort(key=lambda k: (-k["v_n"], -k["v_share"], -k["read"]))
    return rows


# karma, 28 Nov 2026: "this is what i've been asking" -- one same-game multi per game from our own read.
# Backtest 1-27 Nov (158 games, saved lines, point in time; claude/sgm-backtest-2026-11-28.md):
#   anchor alone (the game's top backed 'Biggest advantages' pick) as a straight: 36.1% v 34.3% implied, +5% ROI
#   anchor + the most likely backed legs: 6.7% v 10.9% implied (fillers the read loves land 56% v 59% implied)
#   anchor + the market's shortest legs: 14.0% v 17.2% implied, -21%
#   anchor + 80%+ v-them legs (not too short): 14.0% v 13.8% implied, -8% at the product of the prices
# So the SGM is anchor + v-them legs, a PAPER TEST until it shows more: Sportsbet prices an SGM below the
# product of the singles, so -8% there is worse on the real slip.
SGM_FILLERS = 3
SGM_STATS = ("PTS", "REB", "AST", "PR", "PA", "RA", "PRA")   # threes are straights only (karma's rule)


def game_sgm(night, legs):
    """{game: {"complete", "legs": [anchor + up to SGM_FILLERS v-them legs], "price", "read"}} for every game
    with a backed anchor. Fillers are other players' priced v-them legs (read not under the price by
    COIN_FLIP+). "complete" is False with fewer than 2 fillers: then the anchor stands as a straight.
    One leg per player, overs and X+ rungs, no threes, no day-to-day or out players."""
    out = {}
    keep = ("player", "pid", "team", "stat", "kind", "th", "price", "read", "read_edge", "backed", "actual", "hit")
    for g, r in night.items():
        pool = [x for x in legs if x.get("game") == g and x["kind"] in ("alt", "over") and x["stat"] in SGM_STATS
                and x.get("read") is not None and not x.get("dtd") and not x.get("is_out")]
        anchor = None
        for k in [k for k in r["picks"] if k["kind"] == "upside" and k.get("backed") == "backed"]:
            anchor = next((x for x in pool if x["pid"] == k["pid"] and x["stat"] == k["stat"] and x["kind"] == "alt"
                           and int(round(x["th"])) == k["rung"] and 1.15 <= x["price"] <= 4.0), None)
            if anchor:
                break
        if not anchor:
            continue
        vt = {(k["pid"], k["stat"], k["rung"]) for k in r.get("vthem", [])}
        fill = sorted((x for x in pool if x["kind"] == "alt" and (x["pid"], x["stat"], int(round(x["th"]))) in vt
                       and (x.get("read_edge") or -1) > -COIN_FLIP and x["price"] >= 1.15),
                      key=lambda x: -x["read"])
        chosen = [anchor]
        for x in fill:
            if len(chosen) > SGM_FILLERS:
                break
            if all(c["pid"] != x["pid"] for c in chosen):
                chosen.append(x)
        out[g] = {"complete": len(chosen) >= 3,          # under 3 legs: the anchor is a straight, not an SGM
                  "legs": [{k: x.get(k) for k in keep} for x in chosen],
                  "price": round(math.prod(x["price"] for x in chosen), 2),
                  "read": round(math.prod(x["read"] for x in chosen), 3)}
    return out


def calibrated(share):
    """The read's chance mapped to what such reads actually landed at (CAL_POINTS, straight lines between)."""
    share = _clip(share, 0.0, 1.0)
    for (x0, y0), (x1, y1) in zip(CAL_POINTS, CAL_POINTS[1:]):
        if share <= x1:
            return round(y0 + (y1 - y0) * (share - x0) / (x1 - x0), 3)
    return CAL_POINTS[-1][1]


def backed(edge):
    """The plain verdict for a read against the price: backed, coin flip (within COIN_FLIP) or not backed."""
    if edge is None:
        return None
    return "backed" if edge >= COIN_FLIP else "coin flip" if edge > -COIN_FLIP else "not backed"


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
        if rung > top:                                  # past his best game: no calibration lifts it
            read = 1.0 if x["kind"] == "under" else 0.0
        else:
            read = (1 - calibrated(over)) if x["kind"] == "under" else calibrated(over)
        x["read"] = round(min(max(read, 0.01), 0.99), 3)
        x["read_edge"] = round(x["read"] - 1 / x["price"], 3)
        x["backed"] = backed(x["read_edge"])
    return legs


def threes_table(night, legs):
    """karma, 27 Nov 2026: "i want a list of 3s made vs them by players and the read". One row per player
    with 2+ games v tonight's opponent (last three seasons) who averages 1+ three as read: his makes in
    those games (and how many were for another team), his season average, what the read expects, and the
    Sportsbet threes leg our read rates best against its price. Most above his season average v them first."""
    priced = collections.defaultdict(list)
    for x in legs:
        if (x["stat"] == "FG3M" and x["kind"] in ("alt", "over") and x.get("read_edge") is not None
                and 1.3 <= x["price"] <= 4.0):           # a leg worth looking at, not a 20-to-1 rung
            priced[x["pid"]].append(x)
    rows = []
    for g, r in night.items():
        for p in r["players"]:
            s = p["stats"].get("FG3M") or {}
            log = s.get("v_log") or []
            if len(log) < 2 or s.get("expected", 0) < 1.0:
                continue
            best = max(priced.get(p["pid"], []), key=lambda x: x["read_edge"], default=None)
            v_avg = st.mean(v["made"] for v in log)
            # karma, 28 Nov: "what is most likely". His highest number of threes made in 70%+ of his games v them.
            made = [v["made"] for v in log]
            likely = max((r for r in range(1, max(made) + 1) if sum(m >= r for m in made) / len(made) >= 0.70), default=0)
            last = s.get("last10") or []
            at = next((x for x in legs if x["pid"] == p["pid"] and x["stat"] == "FG3M" and x["kind"] == "alt"
                       and int(round(x["th"])) == likely), None) if likely else None
            rows.append({
                "player": p["player"], "pid": p["pid"], "team": p["team"], "opp": p["opp"], "game": g,
                "v_games": len(log), "v_makes": [v["made"] for v in log[:8]], "v_avg": round(v_avg, 2),
                "v_att": round(st.mean(v["att"] for v in log), 1), "v_min": round(st.mean(v["min"] for v in log)),
                "v_other_team": sum(v["team"] != p["team"] for v in log),
                "season_avg": s.get("season_raw", 0.0), "season_att": s.get("season_att"), "expects": s["expected"],
                "minutes": p["minutes"], "dtd": p.get("dtd", False),
                "likely": likely, "likely_v": f"{sum(m >= likely for m in made)} of {len(made)}" if likely else "--",
                "likely_share": round(sum(m >= likely for m in made) / len(made), 2) if likely else 0.0,
                "likely_last10": f"{sum(v >= likely for v in last)} of {len(last)}" if likely and last else "--",
                "likely_price": at["price"] if at else None,
                "likely_backed": backed(at.get("read_edge")) if at else None,
                "leg": best and {k: best.get(k) for k in ("kind", "th", "price", "read", "read_edge", "hr_season",
                                                          "n_season", "hr_l8", "h2h_hits", "h2h_n")}})
    # most likely first: the most threes he makes nearly every time v them, then how often, then how many games
    rows.sort(key=lambda x: (-x["likely"], -x["likely_share"], -x["v_games"]))
    return rows


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
                price = (f"Sportsbet {k['price']:.2f} (read v price {100 * k['edge']:+.0f}%)" if k["price"]
                         else "no price saved")
                print(f"    {pick_label(k):34s} read {100 * k['share']:.0f}% (read adds {100 * k['lift']:+.0f}%) | actually {k['season']} this season, {k['v_opp']} v them"
                      f" | expects {k['expected']:g} | read x{k['mult']:.2f} | {price}{' | DAY-TO-DAY' if k['dtd'] else ''}")
                for w in k["why"]:
                    print(f"        - {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
