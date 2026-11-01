#!/usr/bin/env python3
"""Refresh THIS season's player game logs, and nothing else.

    python3 tools/refresh_current_season.py --dry-run
    python3 tools/refresh_current_season.py
    python3 tools/refresh_current_season.py --pace 1.5 --limit 50

WHY THIS EXISTS
refresh_cache.py refreshes every active player across four seasons, both
season types: roughly 550 x 4 x 2 = 4,400 requests at 0.6s apart, so about
45 minutes of continuous traffic at best. Run straight after refresh_all.py
has already pulled 1,253 box scores, that is enough to get the machine
rate-limited by stats.nba.com -- which is what happened on 24 Oct 2026, when
every single request in the game-log pass came back as a 30-second read
timeout.

In October the only thing actually missing is the CURRENT season. The three
prior seasons are already cached and have not changed; the playoffs have not
been played. That is 550 requests instead of 4,400 -- an eighth of the
traffic, and the part that matters.

WHAT IT FIXES BESIDES THE VOLUME
refresh_cache.py prints "(no games found for {season} -- skipping)" both when
a player genuinely has not played and when the request timed out. Those are
very different things and the run cannot be read afterwards to tell which
happened: a fully rate-limited run looks like a quiet, successful one.

This separates them, counts them, and stops early rather than grinding
through hundreds of timeouts: CONSECUTIVE_FAILURE_LIMIT in a row means the
host has stopped answering, and continuing just wastes 30 seconds a time.

It is resumable. Players whose current-season log is already cached with
games in it are skipped, so re-running after a rate limit clears picks up
where it stopped.

--recent N: KEEPING IT CURRENT (1 Nov 2026)
The default mode fills gaps; it never refetches a player who already has
games cached, so it cannot pick up last night. --recent N asks ESPN which
teams played on each of the last N US dates and refetches only the
players on those rosters whose cached log lacks that game -- about 200
requests after a 7-game night, a few minutes, instead of the full
4,400-request refresh_cache.py pass that times out half-way. Each
failure is retried once after a pause before it counts. Run by
tools/install_logs_job.sh twice a day: after the US slate finishes, and
again before the next one tips.
"""
import argparse
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nba_api.stats.endpoints import playergamelog                # noqa: E402

from engine.cache import _save_df_cache as save_df_cache         # noqa: E402
from engine.cache import read_payload                            # noqa: E402
from engine.season import CURRENT_SEASON                         # noqa: E402
from engine.team_ids import TEAM_ID_BY_ABBR                      # noqa: E402

# Enough consecutive failures to mean the host has stopped answering rather
# than one request being unlucky.
CONSECUTIVE_FAILURE_LIMIT = 8

DEFAULT_PACE = 1.2       # seconds between requests; refresh_cache.py uses 0.6
REQUEST_TIMEOUT = 20
RETRY_PAUSE = 10         # seconds before the one retry of a failed request


def roster_players():
    """Everyone on a cached roster: who could plausibly have played."""
    seen = {}
    for abbr, team_id in TEAM_ID_BY_ABBR.items():
        payload = read_payload(f"roster_{team_id}")
        if payload is None:
            continue
        for row in (payload.get("data") or []):
            seen[int(row["PLAYER_ID"])] = (row["PLAYER"], abbr)
    return seen


def already_cached(player_id, season):
    """True when this player's season log is cached AND has games in it.

    An empty cached entry is treated as not cached: it is indistinguishable
    from a player who had not debuted when the cache was packed.
    """
    payload = read_payload(f"gamelog_{player_id}_{season}")
    return bool(payload and (payload.get("data") or []))


def _stamp(day):
    """date -> the game log's GAME_DATE spelling, 'Oct 31, 2026'."""
    return day.strftime("%b %d, %Y")


def recent_todo(days, players, season, today=None):
    """(todo, played_on) for --recent: roster players whose team played on
    one of the last `days` US dates and whose cached log has no row for
    that date. Asks ESPN for the slates, which a home connection reaches."""
    import datetime
    from zoneinfo import ZoneInfo
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import availability as av
    today = today or datetime.datetime.now(ZoneInfo("America/New_York")).date()
    played_on = {}
    for back in range(1, days + 1):
        day = today - datetime.timedelta(days=back)
        for g in av.slate(day.strftime("%Y%m%d")):
            status = (g.get("status") or "").lower()
            if status and not status.startswith("final"):
                continue                      # not finished: nothing to fetch yet
            for team in (g["away"], g["home"]):
                played_on[team] = max(played_on.get(team, day), day)
    todo = []
    for pid, (name, abbr) in players.items():
        day = played_on.get(abbr)
        if day is None:
            continue
        rows = (read_payload(f"gamelog_{pid}_{season}") or {}).get("data") or []
        if not any((r.get("GAME_DATE") or "") == _stamp(day) for r in rows):
            todo.append((pid, name, abbr))
    return todo, played_on


def fetch(player_id, season):
    """(frame, error). Regular season only -- October has no playoffs.

    Returns the error rather than swallowing it, so the caller can tell a
    timeout apart from a player who simply has not played.
    """
    try:
        log = playergamelog.PlayerGameLog(
            player_id=player_id, season=season,
            season_type_all_star="Regular Season", timeout=REQUEST_TIMEOUT)
        return log.get_data_frames()[0], None
    except Exception as exc:                      # noqa: BLE001 -- reported, not hidden
        return pd.DataFrame(), f"{type(exc).__name__}: {str(exc)[:90]}"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", default=CURRENT_SEASON)
    parser.add_argument("--pace", type=float, default=DEFAULT_PACE,
                        help=f"seconds between requests (default {DEFAULT_PACE})")
    parser.add_argument("--limit", type=int, default=None,
                        help="stop after this many players, for a cautious first run")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan and fetch nothing")
    parser.add_argument("--recent", type=int, default=None, metavar="N",
                        help="refetch players whose team played in the last N US days "
                             "and whose cached log is missing that game")
    args = parser.parse_args(argv)

    players = roster_players()
    if args.recent:
        todo, played_on = recent_todo(args.recent, players, args.season)
        print(f"teams that played in the last {args.recent} US day(s): "
              f"{len(played_on)}  ({', '.join(sorted(played_on))})")
    else:
        todo = [(pid, name, abbr) for pid, (name, abbr) in players.items()
                if not already_cached(pid, args.season)]
    todo.sort(key=lambda t: (t[2], t[1]))
    if args.limit:
        todo = todo[:args.limit]

    print(f"season:            {args.season}")
    print(f"on cached rosters: {len(players)}")
    print(f"already cached:    {len(players) - len([p for p in players if not already_cached(p, args.season)])}")
    print(f"to fetch:          {len(todo)}")
    print(f"estimated time:    {len(todo) * args.pace / 60:.0f} min at {args.pace}s apart")
    if args.dry_run:
        print("\ndry run, nothing fetched")
        return 0

    cached = empty = failed = 0
    consecutive = 0
    for i, (pid, name, abbr) in enumerate(todo, 1):
        frame, error = fetch(pid, args.season)
        if error:
            # One retry after a pause: a single slow answer is common, and
            # a timed-out player otherwise waits a whole day for the next run.
            time.sleep(RETRY_PAUSE)
            frame, error = fetch(pid, args.season)
        if error:
            failed += 1
            consecutive += 1
            print(f"  [{i}/{len(todo)}] {name:26s} {abbr}  FAILED  {error}")
            if consecutive >= CONSECUTIVE_FAILURE_LIMIT:
                print(f"\nSTOPPED: {consecutive} failures in a row. The host has "
                      f"stopped answering -- almost certainly rate limiting.")
                print("Wait for it to clear and run this again; it resumes.")
                break
        elif frame.empty:
            empty += 1
            consecutive = 0
            print(f"  [{i}/{len(todo)}] {name:26s} {abbr}  no games yet")
        else:
            save_df_cache(f"gamelog_{pid}_{args.season}", frame)
            cached += 1
            consecutive = 0
            print(f"  [{i}/{len(todo)}] {name:26s} {abbr}  cached {len(frame)} games")
        time.sleep(args.pace)

    print(f"\ncached {cached} | genuinely no games {empty} | failed {failed}")
    if cached:
        print("Now run tools/pack_cache.py and commit data_cache.zip.")
    if failed:
        print(f"{failed} player(s) were NOT refreshed. Re-run to pick them up.")
    return 1 if failed and not cached else 0


if __name__ == "__main__":
    raise SystemExit(main())
