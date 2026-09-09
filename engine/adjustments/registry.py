"""Single source of truth for adjustment-layer display order and
human-readable labels -- shared by the "how this was built" expander
(app.py, via analytics/layer_accuracy.py's build_layer_lines) and the
per-prediction confidence scorer (engine/confidence.py, Phase 5), so a
label or ordering change can never drift between the two, and a new
layer only needs to be added in one place.

defender_matchup stays in LAYER_DISPLAY (the expander still shows it
as a numbered step) even though it's excluded from confidence scoring
and accuracy tracking -- see NEVER_APPLIED_BY_DESIGN below and
engine/adjustments/defender.py's CONTEXT_ONLY_BY_DESIGN.
"""

from engine.adjustments.defender import CONTEXT_ONLY_BY_DESIGN as DEFENDER_CONTEXT_ONLY

LAYER_DISPLAY = [
    ("opponent_defense", "Opponent defense"),
    ("missing_teammates", "Missing teammates"),
    ("missing_opponents", "Missing opponent players"),
    ("new_teammate", "New teammate arriving"),
    ("defender_matchup", "Primary defender"),
    ("scheme", "Scheme"),
]

# Layers whose `applied` is ALWAYS False by design (AdjustmentResult
# rule 3, base.py) -- excluded entirely from confidence scoring/reasons
# (engine/confidence.py) and short-circuited in accuracy tracking
# (analytics/layer_accuracy.py). One source, both consumers, so neither
# can drift from engine/adjustments/defender.py's own flag.
NEVER_APPLIED_BY_DESIGN = {
    "defender_matchup": DEFENDER_CONTEXT_ONLY,
}
