"""Shared contract every adjustment-layer function returns, so the
per-prediction confidence scorer (engine/confidence.py, Phase 5) and
the per-layer accuracy tracker (analytics/layer_accuracy.py, Phase 4)
can treat every layer identically instead of special-casing each one's
return shape.

Two design rules, settled deliberately after tonight's shape-ambiguity
rework -- not defaults, not guesses:

1. `value` is ALWAYS a dict, never a bare float. A layer whose effect
   is uniform across every stat (e.g. opponent defense, scheme) uses
   the single sentinel key ALL_STATS; a layer that varies by stat
   (e.g. missing teammates) uses real STAT_COLUMNS keys. There is no
   second code path for "the scalar case" -- every consumer calls
   result.multiplier_for(stat_col) and never needs to know which shape
   produced the answer.

2. `sample_n` is the single source of truth for any count that appears
   in `note`'s prose. Build `note` FROM the same variable passed as
   `sample_n` -- never compute a second, parallel count expression for
   display. The two must be the same Python value by construction, so
   they can never silently drift apart.

3. `applied` and `data_quality` answer different questions -- don't
   conflate them. `applied` means "this layer's value was folded into
   the predicted number." Some layers (e.g. get_defender_matchup_adjustment
   in defender.py) are context-only BY DESIGN and report applied=False
   ALWAYS, even when real data was found -- because the layer never
   changes the prediction, not because the data was missing. A consumer
   that needs to tell "no data" apart from "real but unused/thin data"
   for such a layer must check data_quality/sample_n, not applied.
"""

from dataclasses import dataclass
from typing import Dict

# Sentinel key for a value dict that applies uniformly to every stat,
# instead of varying per stat column.
ALL_STATS = "_all"


@dataclass
class AdjustmentResult:
    layer: str            # e.g. "opponent_defense", "scheme", "missing_teammates"
    value: Dict[str, float]
    note: str             # human-readable explanation; must be built from sample_n (see above)
    data_quality: str     # "real_current" | "real_fallback_season" | "real_thin_sample" |
                           # "manual_estimate" | "unavailable"
    sample_n: int         # real games/data points backing this; 0 if none
    applied: bool          # False for a neutral no-op (e.g. no missing teammates selected)

    def multiplier_for(self, stat_col: str) -> float:
        """The one lookup pattern every consumer should use -- handles
        both the uniform (ALL_STATS) and per-stat value shapes
        identically, so nobody re-derives this fallback independently."""
        return self.value.get(stat_col, self.value.get(ALL_STATS, 1.0))
