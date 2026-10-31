#!/usr/bin/env python3
"""Does THIS player have an advantage against THIS opponent tonight?

Karma's framing, 31 Oct 2026, and the reason tools/matchup_holes.py
exists at all:

    "you need to look at it this way. which player has the most
     advantage in this game. For example: okc vs dallas, players like
     pj washington has advantage because okc doesnt have big wing
     defender. Only guards and big."

matchup_holes answers that for a whole slate, team by team. This answers
it for one leg on a slip: take the player, find what he is (guard, wing
or big, from his listed size), look at how the opponent spends its
minutes across the three, and say whether the opponent is short of
bodies where this player lives.

A hole is measured against the LEAGUE MEDIAN, not against zero: every
team has fewer wings than guards, so what matters is being short
relative to how everyone else covers that archetype.

WHAT THIS IS NOT
A minutes share is not a defender. A team can be short of wings and
still have the one wing who can guard this player, and a team with a
league-average share can be playing all of it badly. This says where the
bodies are, which is a question about the roster, not a prediction about
the night. It carries no number into the projection for that reason --
it is shown beside the chance, never folded into it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matchup_holes as mh                                    # noqa: E402
from engine.cache import read_payload                         # noqa: E402
from engine.team_ids import TEAM_ID_BY_ABBR                    # noqa: E402

GAP = 4.0                 # percentage points below the league median
_baseline_cache = {}


def _baseline(season):
    if season not in _baseline_cache:
        _baseline_cache[season] = mh.league_baseline(season)
    return _baseline_cache[season]


def player_archetype(player_id, team):
    """guard / wing / big from the cached roster row, or None."""
    tid = TEAM_ID_BY_ABBR.get(team)
    if not tid:
        return None
    for row in ((read_payload(f"roster_{tid}") or {}).get("data") or []):
        if int(row["PLAYER_ID"]) == int(player_id):
            return mh.archetype(row.get("POSITION"), row.get("HEIGHT"), row.get("WEIGHT"))
    return None


def archetype_anywhere(player_id):
    """guard / wing / big, looked up across every roster.

    player_archetype() takes a team and is right for the man you are
    betting on. It is wrong for a former teammate: Jerami Grant was
    looked up on Portland, where he no longer plays, came back None, and
    was filed as "plays a different position" from Deni Avdija -- the
    one teammate whose absence actually moves Avdija's shot volume.
    """
    for abbr in TEAM_ID_BY_ABBR:
        kind = player_archetype(player_id, abbr)
        if kind:
            return kind
    return None


def read(player_id, team, opponent, season, exclude=()):
    """{kind, gap, verdict, share, league} -- or None when it cannot be said.

    `exclude` takes the opponent's players who are out tonight, so a team
    is not credited with coverage it will not have on the floor.
    """
    kind = player_archetype(player_id, team)
    if not kind or not opponent or opponent not in TEAM_ID_BY_ABBR:
        return None
    try:
        profile = mh.team_profile(opponent, season, exclude=exclude)
        baseline = _baseline(season)
    except Exception:                      # noqa: BLE001 -- a missing cache is not an error here
        return None
    share = profile["share"].get(kind)
    league = baseline.get(kind)
    if share is None or league is None:
        return None
    deficit = round(league - share, 1)
    if deficit >= GAP:
        verdict = f"attacks {opponent}'s {kind} coverage, {deficit:.1f}pt below the league median"
        edge = "with"
    elif deficit <= -GAP:
        verdict = f"{opponent} is {abs(deficit):.1f}pt deep at {kind} against the league median"
        edge = "against"
    else:
        verdict = f"{opponent} covers {kind}s at about league level"
        edge = "neutral"
    return {"kind": kind, "gap": deficit, "edge": edge, "verdict": verdict,
            "share": round(share, 1), "league": round(league, 1), "opponent": opponent}


def main(argv=None):
    import argparse
    from engine.season import CURRENT_SEASON
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("player_id", type=int)
    ap.add_argument("team")
    ap.add_argument("opponent")
    ap.add_argument("--season", default=CURRENT_SEASON)
    args = ap.parse_args(argv)
    out = read(args.player_id, args.team, args.opponent, args.season)
    print(out["verdict"] if out else "no read available")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
