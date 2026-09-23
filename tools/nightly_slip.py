#!/usr/bin/env python3
"""Write tonight's card before tip-off; account for it in the morning.

    python3 tools/nightly_slip.py                 # commit tonight's card
    python3 tools/nightly_slip.py --settle        # last night's result

Two runs, in that order, on different days. The first writes
slips/<date>.json and prints the post. The second reads that file back
alongside results/<date>.json and prints the morning post.

WHY THE CARD IS WRITTEN AND COMMITTED, NOT RE-DERIVED
engine/slip.py has the argument in full. In short: if the morning post
decided what to report, it would select for the claims that landed,
every morning, and nobody would have to intend it. The file written
tonight is the commitment. --settle may only report what it says.

Which is also why this does not post anything itself. It prints text.
A human copies it. Automating the posting would be the easy half and
would add nothing; the discipline is in the file.

WHAT IT TAKES FROM THE FEED: NOTHING
The card is built from projections/, which is ours. No line, no price,
no book name goes near it -- the repository is public and the
SportsGameOdds terms forbid redistributing their data. See the licence
tests in tests/test_pipeline_end_to_end.py.
"""

import argparse
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
from engine import slip  # noqa: E402
from engine.season import game_date_for  # noqa: E402

PROJECTION_DIR = os.path.join(REPO_ROOT, "projections")
RESULT_DIR = os.path.join(REPO_ROOT, "results")
SLIP_DIR = os.path.join(REPO_ROOT, "slips")

# Sentinel, not a card: a preseason night wrote nothing and that is
# the correct outcome, which is different from failing to write.
PRESEASON = object()


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def latest_projections(directory):
    """The most recent capture. The nightly job writes one per run, so
    the last one is the one closest to tip-off."""
    if not os.path.isdir(directory):
        return None
    names = sorted(n for n in os.listdir(directory) if n.endswith(".json"))
    return os.path.join(directory, names[-1]) if names else None


def commit(game_date, projections_path, slip_dir, size):
    projections = _read(projections_path)
    card = slip.commit(projections, game_date, size=size)
    if card is None:
        # Not a failure. Exhibition nights are a normal state for a
        # fortnight of the year, and a job that reports failure every
        # night for a fortnight trains everybody to ignore it.
        return PRESEASON, (f"{game_date} is before the season opens "
                           f"({slip.SEASON_OPENS}) -- no card written: a "
                           f"preseason claim can never be settled")
    if not card["claims"]:
        return None, "no projection in that capture could be carded"

    os.makedirs(slip_dir, exist_ok=True)
    path = os.path.join(slip_dir, f"{game_date}.json")
    if os.path.exists(path):
        # Rewriting tonight's card after the fact is the one move this
        # whole design exists to prevent. Refuse, loudly.
        return None, (f"{path} already exists -- tonight's card is "
                      f"already committed and is not rewritten")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(card, handle, indent=2)
    return card, path


def settle(game_date, slip_dir, result_dir):
    slip_path = os.path.join(slip_dir, f"{game_date}.json")
    if not os.path.exists(slip_path):
        return None, f"no card was committed for {game_date}"
    result_path = os.path.join(result_dir, f"{game_date}.json")
    if not os.path.exists(result_path):
        # Not an error. The scorer has not run yet, or the night had
        # nothing to score.
        return None, f"{game_date} has not been scored yet"
    return slip.settle(_read(slip_path), _read(result_path)), slip_path


def game_date_of(projections_path):
    """The US date the games were played on, for this capture.

    NOT today's date. Captures run from Australia, so the local date is
    a day ahead of the slate for most of the evening, and
    tools/score_forward_test.py files its results under the US Eastern
    date. A card filed under the local date would never find a result
    to settle against, and the failure would be silent -- no error, just
    a morning post that never appears.

    So this asks the scorer for the same answer the scorer will use.
    One function, one convention, no second copy to drift.
    """
    return game_date_for(_read(projections_path))


def latest_settleable(slip_dir, result_dir):
    """The newest committed card that has a result waiting for it.

    Avoids doing date arithmetic in the caller: "yesterday" in Australia
    is not the game date, and the morning job should not have to know
    that.
    """
    if not os.path.isdir(slip_dir):
        return None
    for name in sorted(os.listdir(slip_dir), reverse=True):
        if not name.endswith(".json"):
            continue
        game_date = name[:-len(".json")]
        if os.path.exists(os.path.join(result_dir, f"{game_date}.json")):
            return game_date
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--date", help="YYYY-MM-DD (default: the game "
                        "date of the capture, in US Eastern)")
    parser.add_argument("--settle", action="store_true",
                        help="print the morning post for a committed card")
    parser.add_argument("--projections", help="a capture to build from")
    parser.add_argument("--slips", default=SLIP_DIR)
    parser.add_argument("--results", default=RESULT_DIR)
    parser.add_argument("--size", type=int, default=slip.CARD_SIZE)
    args = parser.parse_args(argv)

    if args.settle:
        game_date = args.date or latest_settleable(args.slips, args.results)
        if game_date is None:
            print("no committed card has a result to settle yet",
                  file=sys.stderr)
            return 1
        settled, why = settle(game_date, args.slips, args.results)
        if settled is None:
            print(why, file=sys.stderr)
            return 1
        print(slip.render_result(settled))
        return 0

    path = args.projections or latest_projections(PROJECTION_DIR)
    if path is None:
        print("no projections to build a card from", file=sys.stderr)
        return 1
    game_date = args.date or game_date_of(path)
    if game_date is None:
        print(f"{path} carries no usable captured_at, so there is no "
              f"game date to file the card under", file=sys.stderr)
        return 1
    card, why = commit(game_date, path, args.slips, args.size)
    if card is PRESEASON:
        print(why, file=sys.stderr)
        return 0
    if card is None:
        print(why, file=sys.stderr)
        return 1
    print(slip.render_card(card))
    print(f"\nwritten: {os.path.relpath(why, REPO_ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
