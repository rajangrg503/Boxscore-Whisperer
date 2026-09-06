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
