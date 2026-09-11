"""Season constants -- single source of truth for app.py and every
engine/ module that needs them (avoids a circular import: engine/
modules can't import from app.py, since app.py imports from engine/).

Note: the six batch_cache_*.py scripts still each hardcode their own
copies of these two constants -- that's pre-existing drift risk this
change doesn't fix (out of scope for this move), not something
introduced here.
"""

CURRENT_SEASON = "2026-27"   # update each year
PREVIOUS_SEASON = "2025-26"


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
