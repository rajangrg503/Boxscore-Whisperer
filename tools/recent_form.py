#!/usr/bin/env python3
"""Is a player's baseline an average of two different jobs?

    python3 tools/recent_form.py "Cooper Flagg" "Jrue Holiday"
    python3 tools/recent_form.py --stat REB --recent 15 "Joel Embiid"

WHY THIS EXISTS
A season median describes the season. When a player's role changed
partway through it -- promoted into a starting job, displaced out of one,
minutes cut on a tanking team in March -- the median describes neither
the player he was nor the one he is, and the market is pricing the one he
is. That is contamination #3 in
claude/rate-baselines-and-the-three-contaminations, and the one the 20
Oct pre-registration confirmed.

This prints the season middle beside the last-N middle, and the same for
minutes, so the split is visible before anyone reads a gap against a
line as a disagreement.

WHAT IT IS NOT
It is not a filter, and the numbers it prints are not a screen that has
been shown to work. A screen built on this -- cut any row whose line
falls outside the band these two medians span -- was tried against the
scored 20 Oct card and separated nothing: outside-band rows went 1-3,
inside-band rows 2-4, on a sample far too small to conclude anything in
either direction. It is printed here as context for a human reading one
player, not as a rule for a job to apply to a hundred.
"""
import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.cache import read_payload                          # noqa: E402
from engine.players import fold_diacritics                     # noqa: E402
from engine.team_ids import TEAM_ID_BY_ABBR                    # noqa: E402

DEFAULT_RECENT = 20


def roster_index():
    """{folded name: (name, player_id, team abbr)} across every roster."""
    index = {}
    for abbr, team_id in TEAM_ID_BY_ABBR.items():
        payload = read_payload(f"roster_{team_id}")
        if payload is None:
            continue
        for row in (payload.get("data") or []):
            name = row["PLAYER"]
            index[fold_diacritics(name).lower()] = (name, int(row["PLAYER_ID"]), abbr)
    return index


def find(fragment, index):
    """The one roster entry matching a fragment, or None if 0 or many."""
    needle = fold_diacritics(fragment).lower()
    hits = [v for k, v in index.items() if needle in k]
    return hits[0] if len(hits) == 1 else None


def split(player_id, season, stat, recent):
    payload = read_payload(f"gamelog_{player_id}_{season}")
    if payload is None:
        return None
    frame = pd.DataFrame(payload.get("data") or [])
    if frame.empty or stat not in frame.columns:
        return None
    # Sorted explicitly: the cached logs are not guaranteed to arrive
    # oldest-first, and "the last twenty games" taken off an unsorted
    # frame is the first twenty half the time.
    if "GAME_DATE" in frame.columns:
        frame = frame.sort_values("GAME_DATE")
    tail = frame.tail(recent)
    minutes = "MIN" if "MIN" in frame.columns else None
    return {
        "n": len(frame),
        "season": float(frame[stat].median()),
        "recent": float(tail[stat].median()),
        "min_season": float(frame[minutes].median()) if minutes else float("nan"),
        "min_recent": float(tail[minutes].median()) if minutes else float("nan"),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("players", nargs="+", help="name fragments")
    parser.add_argument("--stat", default="PTS")
    parser.add_argument("--season", default=None,
                        help="default: the previous season, which is what "
                             "October baselines are built from")
    parser.add_argument("--recent", type=int, default=DEFAULT_RECENT)
    args = parser.parse_args(argv)

    from engine.season import PREVIOUS_SEASON
    season = args.season or PREVIOUS_SEASON
    index = roster_index()

    print(f"{season}, {args.stat}, last {args.recent} games\n")
    for fragment in args.players:
        hit = find(fragment, index)
        if hit is None:
            print(f"{fragment:26s} no single roster match")
            continue
        name, player_id, abbr = hit
        row = split(player_id, season, args.stat, args.recent)
        if row is None:
            print(f"{name:26s} {abbr}  no {season} log")
            continue
        print(f"{name:26s} {abbr}  n={row['n']:3d}  "
              f"{args.stat} season {row['season']:5.1f}  "
              f"last{args.recent} {row['recent']:5.1f}   "
              f"MIN season {row['min_season']:4.1f}  "
              f"last{args.recent} {row['min_recent']:4.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
