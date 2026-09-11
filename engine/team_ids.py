"""Team abbreviation -> team_id lookup, shared across engine/ modules
that need it. Extracted from engine/backtest_point_in_time.py (moved
verbatim, not reimplemented) once a second consumer
(engine/tracker.py's post-game instrumentation) needed the exact same
mapping -- same reason engine/season.py's constants and
engine/game_log.py's resolve_season_gamelog() were factored out in
their own turns: one source of truth instead of two copies drifting
apart."""

from nba_api.stats.static import teams

TEAM_ID_BY_ABBR = {t["abbreviation"]: t["id"] for t in teams.get_teams()}
