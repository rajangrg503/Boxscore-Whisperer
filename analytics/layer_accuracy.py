"""Per-layer directional accuracy, computed from the resolved
prediction log -- never hardcoded, never faked. See
engine/adjustments/base.py's docstring (rule 3) and this module's
NEVER_APPLIED_BY_DESIGN handling for why "applied=False" and "no data
yet" are treated as genuinely different things, not the same "no
signal" bucket.

Directionality, not magnitude: for a given (layer, stat) pair on one
resolved prediction, "hit" means the layer's multiplier and the real
outcome agreed on which SIDE of the unadjusted baseline the result
would land -- not whether the final blended range contained the
actual value. A layer can be directionally correct even when the
overall prediction missed by a wide margin; that's a genuinely
different (and more honest) question than "was the whole prediction
right," which is why this lives as its own metric rather than being
folded into the tracker's existing hit/miss columns.
"""

import json
import pandas as pd
from dataclasses import dataclass
from typing import Optional

from engine.tracker import load_prediction_log
from engine.adjustments.base import AdjustmentResult
from engine.adjustments.defender import CONTEXT_ONLY_BY_DESIGN as DEFENDER_CONTEXT_ONLY
from engine.adjustments.registry import LAYER_DISPLAY

MIN_SAMPLE = 5

# Maps layer_name -> whether that layer's applied is ALWAYS False by
# design (AdjustmentResult rule 3). Sourced directly from each layer
# module's own flag, not re-declared here or inferred from log data --
# see the Phase 4 discussion for why inference from data is unreliable
# for this specific distinction.
NEVER_APPLIED_BY_DESIGN = {
    "defender_matchup": DEFENDER_CONTEXT_ONLY,
}


@dataclass
class LayerAccuracy:
    hit_rate: Optional[float]  # None when there's nothing honest to report
    n: int                     # real number of scored (row, stat) pairs
    reason: Optional[str] = None  # set only when hit_rate is None:
                                   # "context_only_by_design" | "insufficient_data"


def layer_hit_rate(layer_name: str, stat_col: str, window: int = 50) -> LayerAccuracy:
    """Over the last `window` RESOLVED predictions where this layer
    was applied and asserted a real direction for this stat: what
    fraction had the actual value land on the same side of the
    baseline as the layer pushed it?

    Returns hit_rate=None (with a reason) rather than a fabricated
    number whenever there's nothing honest to report -- either the
    layer structurally never applies, or fewer than MIN_SAMPLE real
    directional data points exist yet."""
    if NEVER_APPLIED_BY_DESIGN.get(layer_name):
        return LayerAccuracy(hit_rate=None, n=0, reason="context_only_by_design")

    df = load_prediction_log()
    resolved = df[df["status"] == "resolved"]
    if "saved_at" in resolved.columns:
        resolved = resolved.sort_values("saved_at", ascending=False)
    resolved = resolved.head(window)

    base_col = f"{stat_col}_base"
    actual_col = f"{stat_col}_actual"

    hits = 0
    scored = 0
    for _, row in resolved.iterrows():
        layers_json = row.get("layers_json")
        if not layers_json or (isinstance(layers_json, float)):  # NaN for old rows
            continue
        try:
            layers = json.loads(layers_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue

        layer_entry = layers.get(layer_name)
        if not layer_entry or not layer_entry.get("applied"):
            continue

        temp_result = AdjustmentResult(
            layer=layer_name,
            value=layer_entry.get("value", {}),
            note="",
            data_quality=layer_entry.get("data_quality", "unavailable"),
            sample_n=layer_entry.get("sample_n", 0),
            applied=layer_entry.get("applied", False),
        )
        multiplier = temp_result.multiplier_for(stat_col)
        if abs(multiplier - 1.0) < 1e-9:
            continue  # no directional assertion for this stat

        base = row.get(base_col)
        actual = row.get(actual_col)
        if base is None or actual is None or pd.isna(base) or pd.isna(actual):
            continue
        if actual == base:
            continue  # no directional signal in the outcome itself

        predicted_direction = 1 if multiplier > 1.0 else -1
        actual_direction = 1 if actual > base else -1

        scored += 1
        if predicted_direction == actual_direction:
            hits += 1

    if scored < MIN_SAMPLE:
        return LayerAccuracy(hit_rate=None, n=scored, reason="insufficient_data")
    return LayerAccuracy(hit_rate=(hits / scored) * 100, n=scored, reason=None)


def format_layer_accuracy(result: LayerAccuracy) -> str:
    """One honest sentence for the "how this was built" panel -- never
    claims a track record that isn't there."""
    if result.reason == "context_only_by_design":
        return "shown as context only -- never adjusts the prediction, so there's no accuracy to track."
    if result.reason == "insufficient_data":
        return "not enough resolved predictions yet to show a reliability track record for this layer."
    return (f"this type of adjustment has been directionally correct "
            f"{result.hit_rate:.0f}% of the time over the last {result.n} resolved prediction(s).")


def build_layer_lines(notes_by_layer: dict) -> list:
    """The [2]-[7] numbered lines for the "how this was built" expander,
    in the fixed order and with the labels from
    engine.adjustments.registry.LAYER_DISPLAY -- single source of
    truth, so the panel can't drift out of sync with that list.

    notes_by_layer: {layer_key: note_string}, one entry per key in
    LAYER_DISPLAY. Numbering starts at 2 since [1] (the unadjusted
    baseline) isn't a layer and is rendered separately by the caller."""
    lines = []
    for i, (layer_key, label) in enumerate(LAYER_DISPLAY, start=2):
        note = notes_by_layer[layer_key]
        lines.append(
            f"**[{i}] {label}:** {note} "
            f"_{format_layer_accuracy(layer_hit_rate(layer_key, 'PTS'))}_"
        )
    return lines
