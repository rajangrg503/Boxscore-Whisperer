#!/usr/bin/env python3
"""Where we and the market see tonight's games differently.

    python3 tools/tonights_gaps.py
    python3 tools/tonights_gaps.py --limit 10
    python3 tools/tonights_gaps.py --snapshot line_snapshots/2026-10-21/...json \
                                   --projections projections/2026-10-21T...json

WHAT THIS PRODUCES
A ranked list of disagreements, biggest first. Not a tip sheet -- see
engine/disagreement.py for why that distinction is the whole point and
what would have to happen before the wording could change.

It reads the two files the nightly capture already writes: the market's
snapshot and our own projections for the players in it. Both are
recorded before tip-off, which is also what makes them scoreable
afterwards.

WHY IT IS A TOOL AND NOT A PAGE, FOR NOW
Two reasons, and the second is the real one.

The licence: the ranking is ours, derived from the feed, and carries
none of their numbers -- but the ordering exists only because their
lines do, and a public page is a different thing from a local report.
Starting local costs nothing and keeps the question open.

The evidence: there is none yet. Putting a ranked list of props in
front of readers implies the ranking is worth acting on, and nothing
has measured whether it is. tools/score_forward_test.py will, over the
season, and the honest order is to find out first.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.disagreement import rank, sentence                    # noqa: E402
from engine.odds_snapshot import read as read_snapshot            # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOT_DIR = os.path.join(REPO_ROOT, "line_snapshots")
PROJECTION_DIR = os.path.join(REPO_ROOT, "projections")


def newest(directory, suffix=".json"):
    """The most recent file under a directory tree, or None."""
    best, best_time = None, None
    for folder, _dirs, files in os.walk(directory or ""):
        for name in files:
            if not name.endswith(suffix):
                continue
            path = os.path.join(folder, name)
            stamp = os.path.getmtime(path)
            if best_time is None or stamp > best_time:
                best, best_time = path, stamp
    return best


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", default=None,
                        help="a line snapshot (default: the most recent)")
    parser.add_argument("--projections", default=None,
                        help="a projections record (default: the most recent)")
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--json", action="store_true",
                        help="print the ranking as JSON instead of text")
    args = parser.parse_args(argv)

    snapshot = args.snapshot or newest(SNAPSHOT_DIR)
    projections_path = args.projections or newest(PROJECTION_DIR)
    if not snapshot:
        print("no line snapshot to read -- has the capture job run?",
              file=sys.stderr)
        return 2
    if not projections_path:
        print("no projections to compare against -- has the capture job run?",
              file=sys.stderr)
        return 2

    legs, _report = read_snapshot(snapshot)
    with open(projections_path) as handle:
        projections = json.load(handle)

    rows = rank(legs, projections, limit=args.limit)

    if args.json:
        print(json.dumps(rows, indent=2, sort_keys=True))
        return 0

    print(f"snapshot    {os.path.relpath(snapshot, REPO_ROOT)}")
    print(f"projections {os.path.relpath(projections_path, REPO_ROOT)}")
    print(f"{len(legs)} leg(s) priced, {len(rows)} worth looking at\n")

    if not rows:
        # Not a failure. A night where we agree with the market on
        # everything is a real answer, and a common one.
        print("nothing we see very differently tonight.")
        return 0

    for index, row in enumerate(rows, start=1):
        print(f"{index:2d}. {sentence(row)}")
        print(f"     80% range {row['low']:.1f}-{row['high']:.1f}, "
              f"{row['n_prior']} prior games")

    print("\nThese are disagreements, not recommendations: nothing has yet")
    print("measured whether our disagreements are right. The forward test")
    print("is scoring exactly these, all season, to find out.")
    thin = [row for row in rows if (row.get("n_prior") or 0) < 15]
    if thin:
        # In the season's first weeks every projection comes from last
        # year's log, and the biggest "disagreements" are usually the
        # market pricing a change the model has not seen yet.
        print(f"\n{len(thin)} of these rest on fewer than 15 prior games. Early in a")
        print("season that usually means the market has priced a change our")
        print("projection has not seen, not that the market is wrong.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
