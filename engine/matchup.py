"""Which archetype a defence is short of, and who can attack it.

Karma's framing, and the reason this exists: "which player has the most
advantage in this game. okc vs dallas, pj washington has advantage
because okc doesnt have big wing defender. only guards and big."

A team's coverage is its minutes split across guard / wing / big (see
engine/archetype.py), and a HOLE is a share that trails the league
MEDIAN by more than a few points -- relative, not a number somebody
picked, because every team plays fewer wings than guards and what
matters is being short compared with everyone else.

This lived in tools/matchup_holes.py (the slate CLI) and
tools/matchup_edge.py (the per-leg read). It moved here on 31 Oct 2026
so the app can show it beside a projection without importing a CLI,
and so there is one definition.

WHAT IT IS NOT
A minutes share is not a defender. A team can be short of wings and
still have the one wing who can guard this player; a team at league
share can be playing all of it badly. The night Oklahoma City lost
both its point-of-attack defenders their guard SHARE barely moved and
they allowed 117 instead of 96. This says where the bodies are, which
is a fact about a roster. It is shown beside a projection and never
folded into it.
"""
from collections import defaultdict

from engine.archetype import ARCHETYPES, archetype, pounds, of_player
from engine.cache import read_payload
from engine.team_ids import TEAM_ID_BY_ABBR

GAP = 4.0          # percentage points below the league median that count as a hole
_baseline_cache = {}


def minutes_played(player_id, season):
    """Total minutes this season, and games. Zero when nothing is cached,
    which is correct: a player with no logged minutes covers nobody."""
    payload = read_payload(f"gamelog_{player_id}_{season}")
    rows = (payload or {}).get("data") or []
    total = 0.0
    for row in rows:
        try:
            total += float(row.get("MIN") or 0)
        except (TypeError, ValueError):
            continue
    return total, len(rows)


def team_profile(abbr, season, exclude=()):
    """Minutes-weighted archetype coverage for one team.

    `exclude` is a set of NBA PLAYER_IDs who are not available tonight,
    from tools/availability.py. Without it this counts a season-ending
    injury as live coverage: on 31 Oct 2026 it credited Oklahoma City
    with Jalen Williams at 31.7 mpg while he was out for the year.
    """
    team_id = TEAM_ID_BY_ABBR.get(abbr)
    payload = read_payload(f"roster_{team_id}") if team_id else None
    if payload is None:
        return None

    players, sat = [], []
    for row in (payload.get("data") or []):
        kind = archetype(row.get("POSITION"), row.get("HEIGHT"), row.get("WEIGHT"))
        if kind is None:
            continue
        if int(row["PLAYER_ID"]) in exclude:
            sat.append(row["PLAYER"])
            continue
        mins, games = minutes_played(int(row["PLAYER_ID"]), season)
        players.append({
            "name": row["PLAYER"],
            "id": int(row["PLAYER_ID"]),
            "pos": row.get("POSITION"),
            "height": row.get("HEIGHT"),
            "weight": pounds(row.get("WEIGHT")),
            "archetype": kind,
            "minutes": mins,
            "games": games,
            "mpg": round(mins / games, 1) if games else 0.0,
        })

    total = sum(p["minutes"] for p in players) or 1.0
    share = defaultdict(float)
    for p in players:
        share[p["archetype"]] += p["minutes"]
    return {
        "abbr": abbr,
        "players": sorted(players, key=lambda p: -p["minutes"]),
        "share": {k: round(100.0 * share[k] / total, 1) for k in ARCHETYPES},
        "total_minutes": total,
        "excluded": sat,
    }


def league_baseline(season, teams=None, exclude=()):
    """League median share per archetype, so 'a hole' means something
    relative rather than a number somebody picked.

    The baseline is built WITHOUT tonight's exclusions by default: the
    median should describe how teams are normally built, not how two of
    them happen to be shorthanded this evening.
    """
    shares = defaultdict(list)
    for abbr in (teams or TEAM_ID_BY_ABBR):
        prof = team_profile(abbr, season, exclude)
        if not prof or prof["total_minutes"] < 1:
            continue
        for kind in ARCHETYPES:
            shares[kind].append(prof["share"][kind])
    median = {}
    for kind in ARCHETYPES:
        vals = sorted(shares[kind])
        median[kind] = vals[len(vals) // 2] if vals else 0.0
    return median


def holes(profile, baseline, gap=4.0):
    """Archetypes where this team's coverage trails the league median by
    more than `gap` percentage points of its own minutes."""
    out = []
    for kind in ARCHETYPES:
        deficit = baseline[kind] - profile["share"][kind]
        if deficit >= gap:
            out.append((kind, round(deficit, 1)))
    return sorted(out, key=lambda t: -t[1])


def exploiters(profile, kind, min_mpg=12.0):
    """Players on this team of the given archetype who actually play."""
    return [p for p in profile["players"]
            if p["archetype"] == kind and p["mpg"] >= min_mpg]




def baseline(season):
    """League median per archetype, cached per process."""
    if season not in _baseline_cache:
        _baseline_cache[season] = league_baseline(season)
    return _baseline_cache[season]


def read(player_id, opponent, season, exclude=(), kind=None):
    """{kind, gap, edge, verdict, share, league, opponent} for one player
    against one opponent tonight, or None when it cannot be said.

    `exclude`: the opponent's players who are out, by NBA PLAYER_ID, so a
    team is not credited with coverage it will not have on the floor.
    """
    kind = kind or of_player(player_id)
    if not kind or not opponent or opponent not in TEAM_ID_BY_ABBR:
        return None
    try:
        profile = team_profile(opponent, season, exclude=set(exclude or ()))
        median = baseline(season)
    except Exception:                       # noqa: BLE001 -- a missing cache is not an error here
        return None
    if not profile:
        return None
    share, league = profile["share"].get(kind), median.get(kind)
    if share is None or league is None:
        return None
    deficit = round(league - share, 1)
    if deficit >= GAP:
        edge, verdict = "with", (f"attacks {opponent}'s {kind} coverage, "
                                 f"{deficit:.1f}pt below the league median")
    elif deficit <= -GAP:
        edge, verdict = "against", (f"{opponent} is {abs(deficit):.1f}pt deep at {kind} "
                                    f"against the league median")
    else:
        edge, verdict = "neutral", f"{opponent} covers {kind}s at about league level"
    return {"kind": kind, "gap": deficit, "edge": edge, "verdict": verdict,
            "share": round(share, 1), "league": round(league, 1), "opponent": opponent,
            "excluded": profile.get("excluded", [])}
