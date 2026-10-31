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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import archetype as _arch                           # noqa: E402
from engine import matchup as _m                                # noqa: E402
from engine.team_ids import TEAM_ID_BY_ABBR                     # noqa: E402,F401

GAP = _m.GAP


def player_archetype(player_id, team=None):
    """Kept for callers; the engine looks a player up across every roster."""
    return _arch.of_player(player_id)


def archetype_anywhere(player_id):
    return _arch.of_player(player_id)


def read(player_id, team, opponent, season, exclude=()):
    """See engine.matchup.read. `team` is accepted and unused: the player's
    archetype comes from wherever he is on a roster now."""
    return _m.read(player_id, opponent, season, exclude=exclude)


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
