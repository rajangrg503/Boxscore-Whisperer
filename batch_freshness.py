"""Is this cache file recent enough to leave alone?

WHY THIS EXISTS
Every batch_cache_*.py script decides whether to re-fetch by asking
whether the file is already there:

    if cache_path.exists():
        return "skipped"

For boxscore_{game_id} that is right, and it is the reason a refresh
takes two hours instead of two days: a finished game's box score never
changes, and there are 148,000 of them.

For anything that is still accruing it is wrong, and wrong in the
quietest possible way -- the file is there, the script says "skipping",
the run reports success, and the data is from whenever it was first
fetched. Found on 3 Oct 2026: every team roster in the cache was from
10 September. Brandon Ingram was still a Raptor, Kawhi Leonard was not,
and the app had been projecting that lineup for three weeks. The
docstring of batch_cache_rosters.py said it "skips any team that's
already cached today". There was no date check in it at all.

WHY cached_at AND NOT THE FILE'S MTIME
tools/scheduled_refresh.sh begins each run with

    pack_cache.py --unpack

which expands data_cache.zip over the folder and rewrites every mtime
to now. A freshness check built on mtime would therefore see every file
as seconds old at the start of every run, skip all of them, and never
expire -- the same bug with extra steps. The payload's own "cached_at"
is the only honest record of when the data was fetched.

WHAT IT DELIBERATELY DOES NOT DO
It does not decide the window. Rosters, season aggregates and career
totals change at completely different rates, and matchup data is
expensive enough that re-fetching it daily would cost hours. Each
caller names its own age, so the cost of that choice is visible in the
script that pays it.
"""

import datetime
import json
import os

# Shorter than the daily cadence of tools/scheduled_refresh.sh ON
# PURPOSE. The job runs at 08:30; if a run ever starts a minute earlier
# than the day before, a flat 24 hours would judge yesterday's file
# fresh and skip it, and the data would refresh every OTHER day while
# every log line still read "skipping". A window shorter than the gap
# between runs cannot do that.
DAILY = 20


def cached_within(path, max_age_hours=DAILY, now=None):
    """True when the payload at `path` was FETCHED less than
    `max_age_hours` ago, by its own "cached_at" stamp.

    False -- meaning fetch it again -- for every other case, and the
    reasons are worth stating because each one is a decision:

      * no file: nothing to keep.
      * unreadable or not JSON: a half-written file from an interrupted
        run should be replaced, not preserved by being unparseable.
      * no "cached_at" key: written before the format had one. Fetching
        once gains the stamp and every later run can judge it properly.
      * a stamp in the FUTURE: a clock-skewed write would otherwise be
        treated as fresh forever, which is the exact failure this module
        exists to end. Costing one extra fetch is the cheaper mistake.

    `now` is injectable so the tests do not have to sleep.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return False

    if not isinstance(payload, dict):
        return False

    stamp = payload.get("cached_at")
    if not stamp:
        return False

    try:
        fetched = datetime.datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return False

    now = now or datetime.datetime.now()
    age = now - fetched
    if age < datetime.timedelta(0):
        return False
    return age < datetime.timedelta(hours=max_age_hours)


def settled_or_fresh(path, season, current_season, max_age_hours=DAILY, now=None):
    """Skip rule for a cache keyed by season.

    A season that has finished is finished: its hustle totals and its
    estimated metrics will read the same in five years as they do now,
    so merely having the file is reason enough to leave it alone. That
    is the same judgement boxscore_{game_id} makes, and it is the whole
    reason a refresh is affordable.

    The CURRENT season is a different kind of thing wearing the same
    shape. It accrues every night, and skipping it on existence means
    the first fetch of the season wins forever -- on 20 Oct 2026 that
    would be a one-game sample standing in for a whole year of opponent
    defence, in every projection the app makes, with nothing anywhere
    reporting a problem.

    One API call per season, so refreshing the live one daily costs
    about two calls a run.
    """
    if season != current_season:
        return os.path.exists(path)
    return cached_within(path, max_age_hours, now=now)
