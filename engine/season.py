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


# The first night that counts. Before this date the NBA is playing
# exhibitions, and nothing about them is evidence:
#
#   * rotations are meaningless -- starters play about twenty minutes,
#     so a projection built from last season's full-game rates runs
#     high by a third before anyone tips off;
#   * and no preseason box score reaches our cache at all, because
#     engine/game_log.py asks the API for "Regular Season" and
#     "Playoffs" only. Every preseason night therefore scores as
#     all-void, which means a preseason CLAIM CAN NEVER BE SETTLED.
#
# The second one is why engine/slip.py refuses to write a card before
# this date. A card that can never be graded is a public claim with no
# result coming, which is the opposite of what the nightly post
# promises. The minutes would only make its numbers wrong; the
# settlement makes it dishonest.
#
# UPDATE EACH SEASON. Left stale, the date simply passes and the
# restrictions stop applying -- everything degrades to normal behaviour
# rather than silently skipping a real slate.
SEASON_OPENS = "2026-10-20"


def tonight_eastern():
    """Today's date where the NBA schedules, as a string.

    The same reasoning as game_date_for(): this runs from Australia,
    where the local date is a day ahead of the US for most of the
    working day. Asking date.today() would call the season open a day
    early every year, and the whole point of the date is that it is the
    boundary of a claim.
    """
    try:
        from zoneinfo import ZoneInfo
        return str(datetime.datetime.now(ZoneInfo("America/New_York")).date())
    except Exception:
        # No tz database. A day either side of the opener is a far
        # smaller error than refusing to answer, and the only thing
        # downstream is whether a note is shown.
        return str(datetime.date.today())


def before_opener(today=None):
    """True while the NBA is still playing exhibitions.

    The page uses this to say so. It does NOT gate any number: the
    projection during preseason is a real projection of a real player's
    rates, it is simply built on minutes he will not play. Refusing to
    project would be less useful than projecting and saying what the
    number assumes -- which is the same choice engine/slip.py makes
    differently, and for a different reason (a card is a public claim
    that can never be settled; a page is a reader asking a question).

    When the date passes, this goes false on its own and every caller
    degrades to normal behaviour. See SEASON_OPENS above.
    """
    return (today or tonight_eastern()) < SEASON_OPENS
