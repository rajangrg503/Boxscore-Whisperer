"""How old is the data behind tonight's numbers?

WHY THIS EXISTS
stats.nba.com blocks the machines this app could run a refresh on --
Streamlit Community Cloud, and (measured on 19 Sep 2026 via
tools/probe_sources.py) GitHub's runners too: three calls timed out at
25s and cdn.nba.com answered 403. So data_cache/ only moves when
someone runs the refresh on a machine the NBA does answer and pushes
the result.

That makes staleness the app's quietest failure mode. Nothing breaks;
the projections just keep describing last week. A reader has no way to
tell, which for someone deciding anything on these numbers is the worst
kind of wrong. This module measures the age of the cache so the app can
say it out loud.

WHAT IT MEASURES
  * team stats  -- the league-wide file every prediction's opponent
                   adjustment reads, rewritten by every refresh
  * game logs   -- the per-player files, by modification time (no JSON
                   parsed: there are over a thousand of them)
  * in season?  -- whether the current season has actually started,
                   from the same team-stats file. Out of season the
                   data is SUPPOSED to sit still, so age is not a
                   warning then.

Nothing here fetches anything; it reads what is already on disk.
"""

import datetime
import glob
import json
import os

# How old the data may get before the app says so, in days.
FRESH_DAYS = 2
STALE_DAYS = 5


def _parse(value):
    try:
        return datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _team_stats_state(cache_dir, season):
    """(cached_at, games_played_max) for the current season's team stats
    file, or (None, 0) when it isn't there."""
    path = os.path.join(cache_dir, f"team_stats_advanced_{season}.json")
    try:
        with open(path) as f:
            payload = json.load(f)
        rows = payload.get("data") or []
        gp = max((int(r.get("GP") or 0) for r in rows), default=0)
        return _parse(payload.get("cached_at")), gp
    except (OSError, ValueError, TypeError, AttributeError):
        return None, 0


def _newest_gamelog(cache_dir, season):
    """Modification time of the most recently written game log for this
    season -- stat() only, since there are over a thousand files."""
    newest = None
    for path in glob.glob(os.path.join(cache_dir, f"gamelog_*_{season}.json")):
        try:
            mtime = datetime.datetime.fromtimestamp(os.path.getmtime(path))
        except OSError:
            continue
        if newest is None or mtime > newest:
            newest = mtime
    return newest


def cache_age(cache_dir, season, now=None):
    """{"age_days", "level", "in_season", "team_stats_at", "gamelogs_at"}.

    age_days is the age of the OLDER of the two things a prediction
    leans on, so a refresh that updated only half the cache still reads
    as half-refreshed. level is "fresh", "aging", "stale" or "unknown",
    and is always "off_season" quiet when the season hasn't started:
    out of season the numbers aren't meant to move."""
    now = now or datetime.datetime.now()
    team_at, games_played = _team_stats_state(cache_dir, season)
    logs_at = _newest_gamelog(cache_dir, season)
    in_season = games_played > 0

    known = [t for t in (team_at, logs_at) if t is not None]
    age_days = (now - min(known)).total_seconds() / 86400 if known else None

    if age_days is None:
        level = "unknown"
    elif not in_season:
        level = "off_season"
    elif age_days <= FRESH_DAYS:
        level = "fresh"
    elif age_days <= STALE_DAYS:
        level = "aging"
    else:
        level = "stale"
    return {"age_days": age_days, "level": level, "in_season": in_season,
            "team_stats_at": team_at, "gamelogs_at": logs_at,
            "games_played": games_played}


def describe(state):
    """One short line for the app, or None when there's nothing worth
    saying (no data at all, or an off-season cache doing its job)."""
    level, age = state["level"], state["age_days"]
    if level in {"unknown", "off_season"}:
        return None
    if age < 1:
        age_text = "today"
    elif age < 2:
        age_text = "yesterday"
    else:
        age_text = f"{int(age)} days ago"
    if level == "fresh":
        return f"Stats updated {age_text}"
    if level == "aging":
        return f"Stats updated {age_text} — a refresh is due"
    return f"Stats last updated {age_text} — tonight's numbers may be out of date"
