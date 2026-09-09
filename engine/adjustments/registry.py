"""Single source of truth for adjustment-layer display order and
human-readable labels -- shared by the "how this was built" expander
(app.py, via analytics/layer_accuracy.py's build_layer_lines) and the
per-prediction confidence scorer (engine/confidence.py, Phase 5), so a
label or ordering change can never drift between the two, and a new
layer only needs to be added in one place.

defender_matchup stays in this list (the expander still shows it as a
numbered step) even though it's excluded from confidence scoring --
see engine/adjustments/defender.py's CONTEXT_ONLY_BY_DESIGN.
"""

LAYER_DISPLAY = [
    ("opponent_defense", "Opponent defense"),
    ("missing_teammates", "Missing teammates"),
    ("missing_opponents", "Missing opponent players"),
    ("new_teammate", "New teammate arriving"),
    ("defender_matchup", "Primary defender"),
    ("scheme", "Scheme"),
]
