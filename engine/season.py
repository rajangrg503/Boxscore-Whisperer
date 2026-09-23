"""Season constants -- single source of truth for app.py and every
engine/ module that needs them (avoids a circular import: engine/
modules can't import from app.py, since app.py imports from engine/).

Note: the six batch_cache_*.py scripts still each hardcode their own
copies of these two constants -- that's pre-existing drift risk this
change doesn't fix (out of scope for this move), not something
introduced here.
"""

import datetime



def season_for_date(game_date):
    """Which season string ("YYYY-YY") a real calendar date belongs to
    -- a genuinely different question from CURRENT_SEASON/PREVIOUS_SEASON
    above, which answer "what season represents right now". This
    answers "what season did THIS date belong to", needed when
    resolving a tracked prediction against a game that may have been
    played months ago, against a season that might not be
    CURRENT_SEASON or PREVIOUS_SEASON by the time it resolves.

    NBA seasons start in October: a date in July or later belongs to
    the season starting that same calendar year; a date from January
    through June belongs to the season that started the PREVIOUS
    calendar year."""
    start_year = game_date.year if game_date.month >= 7 else game_date.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def season_offset(season, years_back):
    """"2026-27", 1 -> "2025-26"."""
    start_year = int(season[:4]) - years_back
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def recent_seasons(n, current=None):
    """The n most recent season strings, newest first."""
    current = current or CURRENT_SEASON
    return [season_offset(current, i) for i in range(n)]


# Derived from today's date instead of hand-edited every year: from
# July 1 the "current" season is the one about to start, exactly as
# season_for_date() defines it. Before a new season has real games,
# every consumer already falls back to PREVIOUS_SEASON (see
# resolve_season_gamelog and engine.adjustments.defense), which is the
# state the app is designed to run in over the off-season. Set on
# import, so a long-running app process picks up the new season on its
# next restart.
CURRENT_SEASON = season_for_date(datetime.date.today())
PREVIOUS_SEASON = season_offset(CURRENT_SEASON, 1)


def game_date_for(projections, override=None):
    """The US date the games were played on.

    Captures run from Australia, so the capture timestamp's UTC date is
    the game date only by luck. Eastern is what the NBA schedules in,
    and October straddles a DST change, so this asks the timezone
    database rather than subtracting a fixed number of hours.
    """
    if override:
        return override
    captured = projections.get("captured_at")
    if not captured:
        return None
    when = datetime.datetime.fromisoformat(captured)
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return str(when.astimezone(ZoneInfo("America/New_York")).date())
    except Exception:
        return None
