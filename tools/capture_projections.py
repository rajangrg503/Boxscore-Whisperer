#!/usr/bin/env python3
"""Record what this app projected, before the games are played.

    python3 tools/capture_projections.py --players 1628983 203999
    python3 tools/capture_projections.py --from-snapshot line_snapshots/2026-10-21/...json
    python3 tools/capture_projections.py --summarise

WHY THIS EXISTS
tools/capture_lines.py records what the market said. On its own that is
worthless: a folder of odds snapshots and no way to score them. An
accuracy claim needs both sides, recorded before tip-off and neither
one revisable afterwards.

This is the other half. For each player, it computes the projection the
app would show -- from the same engine, through the same seam -- and
writes it next to the line capture with the same digest treatment.

WHY IT IS NOT app.py
app.py is a Streamlit script: importing it runs it. The projection
maths does not live there, though. engine/baseline_stats.py exists
precisely so the live app and the backtest compute a baseline in one
place rather than two, and engine/backtest_point_in_time.py already
reaches it without Streamlit. This is a third caller of that same seam,
not a reimplementation -- if these numbers ever drift from the page,
that is a bug in one shared function rather than a difference of
opinion between two copies.

WHAT IT DELIBERATELY LEAVES OUT
The opponent-defence multiplier, missing teammates, the defender and
the scheme. Those are choices a reader makes on the page, and a forward
test should measure the thing the app produces on its own, not a
particular set of toggles somebody picked on one night. What is
recorded is the baseline projection and its calibrated distribution --
the number the page shows before any adjustment, which is also the one
the hit rates use when no line is entered.

That is a narrower claim than "the app was right", and it is the one
the data can actually support.

WHAT IS RECORDED
Per player and stat: the projected value, the 80% interval, and the
number of prior games behind it. Plus a SHA-256 of the raw record, for
the same reason capture_lines.py has one -- a projection published in
October cannot be quietly improved in March.

Unlike the line snapshots, these are OURS. There is no licence reason
to keep them out of the repository, and every reason to commit them:
this file is the claim.
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.baseline_stats import stats_from_gamelog          # noqa: E402
from engine.cache import read_payload                          # noqa: E402
from engine.distribution import distribution_for               # noqa: E402
from engine.odds_snapshot import nba_player_ids                # noqa: E402
from engine.stat_columns import STAT_COLUMNS                   # noqa: E402

import pandas as pd                                            # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECTION_DIR = os.path.join(REPO_ROOT, "projections")

# The interval the page shows, and the one the calibration work measured
# at about 80% real coverage. Kept as a name rather than a literal so a
# recorded projection can never mean a different thing from the page.
RANGE_NOMINAL = 0.8

# Below this, the app itself declines to lean, and a projection built on
# a handful of games is not a claim worth scoring.
MIN_PRIOR_GAMES = 5


def projection_for(player_id, season):
    """The app's baseline projection for one player, or None.

    Returns None rather than guessing when the cache has no game log --
    a forward test must not quietly score a player it never projected.
    """
    payload = read_payload(f"gamelog_{player_id}_{season}")
    if payload is None:
        return None
    frame = pd.DataFrame(payload.get("data") or [])
    if len(frame) < MIN_PRIOR_GAMES:
        return None

    stats, n_games = stats_from_gamelog(frame)
    out = {}
    for col, _label in STAT_COLUMNS:
        mean, spread = stats[col]
        if pd.isna(mean):
            continue
        dist = distribution_for(col, mean, spread, n_games)
        low, high = (dist.interval(RANGE_NOMINAL) if dist is not None
                     else (max(0.0, mean - spread * 0.6), mean + spread * 0.6))
        out[col] = {
            "projected": round(float(mean), 3),
            "low": round(float(low), 3),
            "high": round(float(high), 3),
            "calibrated": dist is not None,
        }
    if not out:
        return None
    return {"n_prior_games": int(n_games), "stats": out}


def build_record(player_ids, season, captured_at):
    """One record for this capture: every player we could project."""
    players = {}
    skipped = []
    for player_id in player_ids:
        projection = projection_for(player_id, season)
        if projection is None:
            skipped.append(str(player_id))
        else:
            players[str(player_id)] = projection
    body = {
        "captured_at": captured_at.isoformat(timespec="seconds"),
        "season": season,
        "range_nominal": RANGE_NOMINAL,
        "players": players,
    }
    return body, skipped


def write_record(body, captured_at):
    """Write the projections and return (path, sha256).

    Digest over the bytes on disk, so verifying needs nothing but
    sha256sum -- the same property the line captures have.
    """
    os.makedirs(PROJECTION_DIR, exist_ok=True)
    stamp = captured_at.strftime("%Y-%m-%dT%H%M%SZ")
    path = os.path.join(PROJECTION_DIR, f"{stamp}.json")
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    with open(path, "wb") as handle:
        handle.write(raw)
    return path, hashlib.sha256(raw).hexdigest()


def player_ids_from_snapshot(path):
    """Who to project tonight, as NBA player ids.

    This used to walk the snapshot for anything that looked like a
    player id. In the real schema that returns the feed's own slugs --
    CADE_CUNNINGHAM_1_NBA -- and a slug finds no cached game log, so
    every player would have been skipped as having too little history.
    On an ordinary night in January that is indistinguishable from the
    off-season, in a job nobody watches.

    engine/odds_snapshot.py resolves them to NBA ids and reports what
    it could not match, and it is the same reader the scorer uses, so
    the players we project and the players we score cannot drift apart.
    """
    ids, _report = nba_player_ids(path)
    return ids


def summarise():
    if not os.path.isdir(PROJECTION_DIR):
        print("no projections recorded yet")
        return 0
    files = sorted(os.listdir(PROJECTION_DIR))
    total_players = 0
    for name in files:
        try:
            with open(os.path.join(PROJECTION_DIR, name)) as handle:
                body = json.load(handle)
            count = len(body.get("players", {}))
        except (OSError, ValueError):
            count = 0
        total_players += count
        print(f"  {name}  {count} player(s)")
    print(f"\n{len(files)} capture(s), {total_players} player-projections")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--players", nargs="*", default=None,
                        help="NBA player ids to project")
    parser.add_argument("--from-snapshot", default=None,
                        help="read the player ids out of a line snapshot")
    parser.add_argument("--season", default=None,
                        help="season to project from (default: the app's current)")
    parser.add_argument("--summarise", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="project and report, write nothing")
    args = parser.parse_args(argv)

    if args.summarise:
        return summarise()

    if args.season:
        season = args.season
    else:
        from engine.season import CURRENT_SEASON
        season = CURRENT_SEASON

    player_ids = list(args.players or [])
    if args.from_snapshot:
        player_ids += player_ids_from_snapshot(args.from_snapshot)
    player_ids = sorted(set(player_ids))

    if not player_ids:
        print("no players given -- pass --players or --from-snapshot", file=sys.stderr)
        return 2

    captured_at = datetime.now(timezone.utc)
    body, skipped = build_record(player_ids, season, captured_at)

    if not body["players"]:
        # Not an error: out of season the current-season logs are empty,
        # so there is nothing to project and nothing worth recording.
        print(f"{captured_at.isoformat(timespec='seconds')}  "
              f"nothing to project for {season} "
              f"({len(skipped)} player(s) with too little history) -- nothing written")
        return 0

    if args.dry_run:
        print(f"{captured_at.isoformat(timespec='seconds')}  "
              f"{len(body['players'])} projected, {len(skipped)} skipped -- dry run")
        return 0

    path, digest = write_record(body, captured_at)
    print(f"{captured_at.isoformat(timespec='seconds')}  "
          f"{len(body['players'])} projected, {len(skipped)} skipped\n"
          f"  {os.path.relpath(path, REPO_ROOT)}  sha256 {digest[:16]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
