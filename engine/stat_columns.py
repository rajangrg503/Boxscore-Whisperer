"""The tracked stat columns -- single source of truth for app.py and
every engine/ module that needs them (same reason engine/season.py
exists: engine/ modules can't import this from app.py without creating
a circular import)."""

STAT_COLUMNS = [
    ("PTS", "Points"),
    ("AST", "Assists"),
    ("REB", "Rebounds"),
    ("STL", "Steals"),
    ("BLK", "Blocks"),
    ("FG3M", "3-Pointers Made"),
    ("TOV", "Turnovers"),
]
