"""Per-prediction confidence -- how much of THIS number rests on data we
have actually tested, not historical accuracy (that's
analytics/layer_accuracy.py's job). A transparent, documented rubric,
not a calibrated model.

WHY IT'S A DEDUCTION RUBRIC (rewritten Sep 2026)
The first version ADDED points for every layer that applied, so the
more optional adjustments a user stacked, the more confident the badge
looked: plain Jokic vs OKC was Medium, Jokic with every advanced option
was High. The backtests say the opposite. Only the season baseline and
the opponent-defense layer are backtested as the default path; the
teammate-out ratios made predictions worse until they were shrunk
(out_redistribution_sweep.py), the missing-opponents layer made them
worse at every strength (missing_opponents_sweep.py), and the scheme
and new-teammate layers can't be backtested at all. So confidence now
starts from the baseline and every thin or untested adjustment takes
points away, with the reason named.

Rubric:
  baseline sample   >= 20 games: 80    5-19: 55    < 5: 20
  opponent defense  last season's ratings (early season):   -5
  missing teammates applied from < 10 games without them:  -15
                    applied from 10-19 games:                -5
  new teammate      applied (never backtested):             -10
  scheme            applied (manual estimate, untestable):  -15
  any other applied layer on a thin / manual / unavailable
  sample (safety net for layers added later):                -5
  missing opponents is context-only (never applied) and defender
  matchup is context-only by design: neither scores.
Bands: High >= 70, Medium >= 40, Low below that.
"""

from dataclasses import dataclass, field
from typing import List

from engine.adjustments.registry import LAYER_DISPLAY, NEVER_APPLIED_BY_DESIGN

# baseline_sample_n (real games behind the season/blended baseline).
BASELINE_HIGH_GAMES = 20
BASELINE_LOW_GAMES = 5
BASELINE_POINTS = {"high": 80, "low": 55, "thin": 20}

DEFENSE_FALLBACK_PENALTY = 5
TEAMMATE_THIN_GAMES = 10        # < this many games without the teammate
TEAMMATE_SOME_GAMES = 20        # < this many
TEAMMATE_THIN_PENALTY = 15
TEAMMATE_SOME_PENALTY = 5
NEW_TEAMMATE_PENALTY = 10
SCHEME_PENALTY = 15
OTHER_WEAK_LAYER_PENALTY = 5
WEAK_DATA_QUALITY = {"real_thin_sample", "manual_estimate", "unavailable"}

HIGH_THRESHOLD = 70
MEDIUM_THRESHOLD = 40


@dataclass
class PredictionConfidence:
    label: str             # "High" | "Medium" | "Low"
    score: int              # rubric total, 0-80. Internal/debugging only;
                             # never shown raw in the UI.
    reasons: List[str] = field(default_factory=list)  # what lowered it;
                             # empty when nothing did


def _layer_penalty(layer_key, label, result):
    """(points to subtract, reason or None) for one APPLIED layer."""
    if layer_key == "opponent_defense":
        if result.data_quality == "real_fallback_season":
            return DEFENSE_FALLBACK_PENALTY, f"{label}: using last season's ratings"
        if result.data_quality in WEAK_DATA_QUALITY:
            return OTHER_WEAK_LAYER_PENALTY, f"{label}: {result.data_quality.replace('_', ' ')}"
        return 0, None
    if layer_key == "missing_teammates":
        n = result.sample_n
        if n < TEAMMATE_THIN_GAMES:
            return TEAMMATE_THIN_PENALTY, f"{label}: only {n} game(s) without them to learn from"
        if n < TEAMMATE_SOME_GAMES:
            return TEAMMATE_SOME_PENALTY, f"{label}: {n} games without them to learn from"
        return 0, None
    if layer_key == "new_teammate":
        return NEW_TEAMMATE_PENALTY, f"{label}: not backtested"
    if layer_key == "scheme":
        return SCHEME_PENALTY, f"{label}: an untested estimate"
    if result.data_quality in WEAK_DATA_QUALITY:
        return OTHER_WEAK_LAYER_PENALTY, f"{label}: {result.data_quality.replace('_', ' ')}"
    return 0, None


def score_prediction(layer_results: dict, baseline_sample_n: int) -> PredictionConfidence:
    """layer_results: {layer_key: AdjustmentResult} -- the same dict
    app.py builds for tracker.py's layers_json. A layer that wasn't
    applied (nothing selected, or context-only) is neutral. Layers
    flagged NEVER_APPLIED_BY_DESIGN are skipped entirely."""
    reasons = []

    if baseline_sample_n >= BASELINE_HIGH_GAMES:
        score = BASELINE_POINTS["high"]
    elif baseline_sample_n >= BASELINE_LOW_GAMES:
        score = BASELINE_POINTS["low"]
        reasons.append(f"Only {baseline_sample_n} game(s) back this baseline")
    else:
        score = BASELINE_POINTS["thin"]
        reasons.append(f"Only {baseline_sample_n} game(s) back this baseline")

    for layer_key, label in LAYER_DISPLAY:
        if NEVER_APPLIED_BY_DESIGN.get(layer_key):
            continue
        result = layer_results.get(layer_key)
        if result is None or not result.applied:
            continue
        penalty, reason = _layer_penalty(layer_key, label, result)
        score -= penalty
        if reason:
            reasons.append(reason)

    score = max(score, 0)
    return PredictionConfidence(label=_label_for_score(score), score=score, reasons=reasons)


def _label_for_score(score: int) -> str:
    """Pure banding logic, split out so the >= boundaries can be
    unit-tested at exact integers (39/40, 69/70)."""
    if score >= HIGH_THRESHOLD:
        return "High"
    if score >= MEDIUM_THRESHOLD:
        return "Medium"
    return "Low"
