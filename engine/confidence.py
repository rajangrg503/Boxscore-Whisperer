"""Per-prediction confidence -- data completeness for THIS specific
query, not historical accuracy (that's analytics/layer_accuracy.py's
job). A transparent, documented rubric over real inputs, not a
calibrated model -- see AdjustmentResult's docstring
(engine/adjustments/base.py) and the architecture plan's System 3."""

from dataclasses import dataclass, field
from typing import List

from engine.adjustments.registry import LAYER_DISPLAY, NEVER_APPLIED_BY_DESIGN

# baseline_sample_n (real games behind the season/blended baseline).
BASELINE_HIGH_GAMES = 20   # >= this many real games -> +40
BASELINE_LOW_GAMES = 5     # >= this many (but < HIGH) -> +20; below -> +0

# Points per applied layer, by its AdjustmentResult.data_quality.
DATA_QUALITY_WEIGHTS = {
    "real_current": 15,
    "real_fallback_season": 5,
    "real_thin_sample": 5,
    "manual_estimate": 0,
    "unavailable": 0,
}

HIGH_THRESHOLD = 70
MEDIUM_THRESHOLD = 40


@dataclass
class PredictionConfidence:
    label: str             # "High" | "Medium" | "Low"
    score: int              # rubric total -- NOT a percentage, can exceed 100.
                             # Internal/debugging only; never shown raw in the UI.
    reasons: List[str] = field(default_factory=list)  # genuine gaps only;
                             # empty when there's nothing to caveat


def score_prediction(layer_results: dict, baseline_sample_n: int) -> PredictionConfidence:
    """layer_results: {layer_key: AdjustmentResult} -- the same dict
    already built in app.py for tracker.py's layers_json. Scores only
    what's actually present for THIS query: a thin baseline, or a
    layer that had to fall back to weaker data, lowers the score and
    is named in `reasons`. A layer nobody asked for (applied=False,
    nothing selected) is neutral -- not a gap, just not applicable --
    so it contributes neither points nor a reason. Layers flagged
    NEVER_APPLIED_BY_DESIGN (e.g. defender_matchup, context-only by
    design) are skipped entirely: their applied is always False by
    design, not because data was missing, so scoring them the same as
    an unused optional layer would be misleading."""
    score = 0
    reasons = []

    if baseline_sample_n >= BASELINE_HIGH_GAMES:
        score += 40
    elif baseline_sample_n >= BASELINE_LOW_GAMES:
        score += 20
        reasons.append(f"Only {baseline_sample_n} game(s) back this baseline")
    else:
        reasons.append(f"Only {baseline_sample_n} game(s) back this baseline")

    for layer_key, label in LAYER_DISPLAY:
        if NEVER_APPLIED_BY_DESIGN.get(layer_key):
            continue
        result = layer_results.get(layer_key)
        if result is None or not result.applied:
            continue
        weight = DATA_QUALITY_WEIGHTS.get(result.data_quality, 0)
        score += weight
        if result.data_quality != "real_current":
            reasons.append(f"{label}: {result.data_quality.replace('_', ' ')}")

    return PredictionConfidence(label=_label_for_score(score), score=score, reasons=reasons)


def _label_for_score(score: int) -> str:
    """Pure banding logic, split out from score_prediction() so the
    >= boundaries can be unit-tested directly at exact integers (e.g.
    39/40, 69/70) -- score_prediction()'s real weights (baseline in
    {0,20,40}, each layer in {0,5,15}) mean every ACHIEVABLE score is a
    multiple of 5, so a boundary like 69 can never actually occur from
    real inputs; testing this function directly proves the comparison
    itself is correct rather than only trusting a value that happened
    to land on a reachable multiple of 5."""
    if score >= HIGH_THRESHOLD:
        return "High"
    if score >= MEDIUM_THRESHOLD:
        return "Medium"
    return "Low"
