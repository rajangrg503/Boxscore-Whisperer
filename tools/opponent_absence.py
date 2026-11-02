#!/usr/bin/env python3
"""Does a player produce more when the OTHER team is missing its key man
at his position?

    python3 tools/opponent_absence.py
    python3 tools/opponent_absence.py --seasons 2025-26 2026-27 --boot 2000

WHY THIS EXISTS
On 2 Nov 2026 (AEDT) Indiana played Brooklyn without Zubac, its starting
centre. Day'Ron Sharpe, who usually gets 6-8 rebounds, had 13 (4
offensive) in 26 minutes. Karma read it as "lack of size on the Pacers".
roster_state.py only asks who is missing from a player's OWN team, so the
card took Sharpe at 6+ when the matchup pointed higher. This measures
whether that read is a general effect, for rebounds, assists and points.

METHOD
1. Every cached game log is read, and each row is tied to its game and
   team (the MATCHUP column names the player's team first).
2. Each team-season gets one KEY player per position group (G, F, C,
   from the current rosters' POSITION): the one with the most USAGE per
   game for that team (shots + 0.44 free throws + turnovers + assists),
   provided he played at least MIN_KEY_GAMES for it. (Until 2 Nov 2026
   this was most minutes; Curry, managed to 28 a night, was not Golden
   State's key guard by minutes.)
3. A team game "misses its key C" when the team played and the key C
   has no row for that game.
4. For every player at the same position group on the other side (at
   least MIN_MINUTES that night), his per-minute rate is compared
   between games where the opponent's key man sat and games where he
   played. Each player is compared only with himself, so team quality
   and role wash out of the comparison. Minutes are compared too, since
   a rate change and a minutes change are different bets.
5. The headline number pools those within-player ratios, weighted by
   the minutes behind the absent side. The 90% interval comes from
   resampling PLAYERS, not games, because one player's games are not
   independent of each other.

WHAT IT CANNOT DO
Positions come from today's rosters, so a player who has left the league
has no position and drops out. "Key man" is minutes, not quality: a
team whose key C is a backup-level player will show little effect. And
the absent games are not random: a team resting its centre may also be
resting others.
"""
import argparse, collections, glob, json, os, random, statistics as st, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from engine.cache import read_payload                      # noqa: E402
from engine.team_ids import TEAM_ID_BY_ABBR                # noqa: E402

MIN_KEY_GAMES = 25
MIN_MINUTES = 12
MIN_ABSENT_GAMES = 2      # a player needs this many games facing the absence
STATS = ("REB", "OREB", "DREB", "AST", "PTS")


def group(pos):
    pos = (pos or "").upper()
    if pos.startswith("C"):
        return "C"
    if pos.startswith("F"):
        return "C" if pos == "F-C" else "F"
    if pos.startswith("G"):
        return "G"
    return None


def positions():
    out = {}
    for abbr, tid in TEAM_ID_BY_ABBR.items():
        for row in (read_payload(f"roster_{tid}") or {}).get("data") or []:
            out[int(row["PLAYER_ID"])] = group(row.get("POSITION"))
    return out


def load(seasons):
    """{(season, game_id): {team: {pid: row}}}"""
    games = collections.defaultdict(lambda: collections.defaultdict(dict))
    for season in seasons:
        for path in glob.glob(os.path.join(ROOT, "data_cache", f"gamelog_*_{season}.json")):
            pid = int(os.path.basename(path).split("_")[1])
            payload = read_payload(os.path.basename(path)[:-5]) or {}
            for row in payload.get("data") or []:
                if not row.get("MIN"):
                    continue
                team = row["MATCHUP"].split()[0]
                games[(season, row["Game_ID"])][team][pid] = row
    return games


def usage(row):
    """Possessions a player finishes, roughly: shots, trips to the line,
    turnovers, plus the plays he makes for others. A key man is who the
    offence runs through, not who stands on the floor longest -- Curry
    played fewer minutes than Podziemski in October 2026."""
    f = lambda k: float(row.get(k) or 0)                          # noqa: E731
    return f("FGA") + 0.44 * f("FTA") + f("TOV") + f("AST")


def keys(games, pos, metric="usage"):
    """{(season, team): {group: pid}} -- the most usage (or minutes) per
    game at each position group, among players with MIN_KEY_GAMES games."""
    tot = collections.defaultdict(lambda: collections.defaultdict(float))
    count = collections.defaultdict(lambda: collections.defaultdict(int))
    for (season, _), teams in games.items():
        for team, rows in teams.items():
            for pid, row in rows.items():
                tot[(season, team)][pid] += usage(row) if metric == "usage" else float(row["MIN"] or 0)
                count[(season, team)][pid] += 1
    out = {}
    for k, m in tot.items():
        best = {}
        per_game = {pid: v / count[k][pid] for pid, v in m.items() if count[k][pid] >= MIN_KEY_GAMES}
        for pid, _ in sorted(per_game.items(), key=lambda kv: -kv[1]):
            g = pos.get(pid)
            if g and g not in best:
                best[g] = pid
        out[k] = best
    return out


def collect(games, pos, key):
    """{group: {pid: {"absent": [rows], "present": [rows]}}}"""
    data = collections.defaultdict(lambda: collections.defaultdict(lambda: {"absent": [], "present": []}))
    for (season, gid), teams in games.items():
        if len(teams) != 2:
            continue
        (a, ra), (b, rb) = teams.items()
        for mine, opp, opp_rows in ((ra, b, rb), (rb, a, ra)):
            kmap = key.get((season, opp), {})
            for pid, row in mine.items():
                g = pos.get(pid)
                if not g or g not in kmap or float(row["MIN"] or 0) < MIN_MINUTES:
                    continue
                side = "present" if kmap[g] in opp_rows else "absent"
                data[g][pid][side].append(row)
    return data


def ratio(rows_a, rows_p, stat):
    ma = sum(float(r["MIN"]) for r in rows_a)
    mp = sum(float(r["MIN"]) for r in rows_p)
    sa = sum(float(r.get(stat) or 0) for r in rows_a)
    sp = sum(float(r.get(stat) or 0) for r in rows_p)
    if not (ma and mp and sp):
        return None
    return (sa / ma) / (sp / mp), ma


def pooled(players, stat):
    num = den = 0.0
    for a, p in players:
        r = ratio(a, p, stat)
        if r:
            num += r[0] * r[1]
            den += r[1]
    return num / den if den else None


def minutes_change(players):
    num = den = 0.0
    for a, p in players:
        ma = st.mean(float(r["MIN"]) for r in a)
        mp = st.mean(float(r["MIN"]) for r in p)
        num += (ma / mp) * len(a)
        den += len(a)
    return num / den if den else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seasons", nargs="+", default=["2025-26", "2026-27"])
    ap.add_argument("--boot", type=int, default=1000)
    ap.add_argument("--json", default=None, help="write the table here too")
    args = ap.parse_args(argv)

    pos = positions()
    games = load(args.seasons)
    key = keys(games, pos)
    data = collect(games, pos, key)
    rng = random.Random(7)
    table = {}
    print(f"{len(games)} games, {len(pos)} players with a position\n")
    print(f"{'opponent missing key':22s}{'players':>8s}{'games':>7s}{'min':>7s}"
          + "".join(f"{s:>16s}" for s in STATS))
    for g in ("C", "F", "G"):
        players = [(v["absent"], v["present"]) for v in data[g].values()
                   if len(v["absent"]) >= MIN_ABSENT_GAMES and len(v["present"]) >= 5]
        n_games = sum(len(a) for a, _ in players)
        row = {"players": len(players), "games": n_games, "minutes": minutes_change(players)}
        cells = []
        for stat in STATS:
            est = pooled(players, stat)
            boots = []
            for _ in range(args.boot):
                sample = [players[rng.randrange(len(players))] for _ in players]
                b = pooled(sample, stat)
                if b:
                    boots.append(b)
            boots.sort()
            lo, hi = boots[int(0.05 * len(boots))], boots[int(0.95 * len(boots)) - 1]
            row[stat] = {"ratio": est, "lo": lo, "hi": hi}
            cells.append(f"{est - 1:+6.1%} [{lo - 1:+.0%},{hi - 1:+.0%}]")
        table[g] = row
        print(f"{'their ' + g + ' -> our ' + g + 's':22s}{len(players):8d}{n_games:7d}"
              f"{row['minutes'] - 1:+7.1%}" + "".join(f"{c:>16s}" for c in cells))
    if args.json:
        with open(args.json, "w") as f:
            json.dump(table, f, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
