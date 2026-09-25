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
import time

import pandas as pd
from dataclasses import dataclass
from typing import Optional

from engine import log_store
from engine.tracker import TrackerStorageError, load_prediction_log, is_hypothetical
from engine.adjustments.base import AdjustmentResult
from engine.adjustments.registry import LAYER_DISPLAY, NEVER_APPLIED_BY_DESIGN

MIN_SAMPLE = 5

# With the Postgres log, every results page would otherwise query the
# database on every rerun (each widget change), which keeps the free
# Neon compute awake and eats its monthly allowance. The track record
# is a slow-moving summary, so a few minutes of staleness is fine.
RECENT_CACHE_SECONDS = 600
_recent_cache = {}


@dataclass
class LayerAccuracy:
    hit_rate: Optional[float]  # None when there's nothing honest to report
    n: int                     # real number of scored (row, stat) pairs
    reason: Optional[str] = None  # set only when hit_rate is None:
                                   # "context_only_by_design" | "insufficient_data"


def _recent_resolved(window):
    """The live log's `window` most recently saved resolved rows, minus
    any row a reader marked as a hypothesis.

    Cached for RECENT_CACHE_SECONDS when the log is in Postgres; the
    CSV backend is read fresh every time, as before."""
    url = log_store.database_url()
    key = (url, window)
    if url is not None:
        hit = _recent_cache.get(key)
        if hit is not None and hit[0] > time.monotonic():
            return hit[1].copy()
    df = load_prediction_log()
    resolved = df[df["status"] == "resolved"]
    # Drop the reader's own hypotheses BEFORE taking the window, not
    # after. This line is shown to every visitor as a track record, and
    # the sample is this shared log -- everybody's rows. Filtering after
    # .head(window) would mean a run of scenario saves quietly shrank
    # the sample instead of being skipped over: twelve hypotheticals in
    # the latest fifty rows and the "last 50 resolved predictions"
    # becomes thirty-eight, with nothing on screen saying so. Filtering
    # first reaches further back and keeps the window's promise.
    if "hypothetical" in resolved.columns:
        real = ~resolved["hypothetical"].map(is_hypothetical)
        resolved = resolved[real]
    if "saved_at" in resolved.columns:
        resolved = resolved.sort_values("saved_at", ascending=False)
    resolved = resolved.head(window)
    if url is not None:
        _recent_cache[key] = (time.monotonic() + RECENT_CACHE_SECONDS, resolved.copy())
    return resolved


def layer_hit_rate(layer_name: str, stat_col: str, window: int = 50, df: pd.DataFrame = None) -> LayerAccuracy:
    """Over the last `window` RESOLVED predictions where this layer
    was applied and asserted a real direction for this stat: what
    fraction had the actual value land on the same side of the
    baseline as the layer pushed it?

    Returns hit_rate=None (with a reason) rather than a fabricated
    number whenever there's nothing honest to report -- either the
    layer structurally never applies, or fewer than MIN_SAMPLE real
    directional data points exist yet.

    df=None (the default, used by the live app): loads from the live
    tracked prediction log via load_prediction_log() and applies the
    rolling `window` limit, exactly as before. df=<a dataframe> (the
    backtesting project): scores every row in the given dataframe
    directly, ignoring `window` entirely -- a backtest wants the full
    real sample, not a rolling recent-N window built for the live
    app's continuously-growing tracker."""
    if NEVER_APPLIED_BY_DESIGN.get(layer_name):
        return LayerAccuracy(hit_rate=None, n=0, reason="context_only_by_design")

    if df is None:
        resolved = _recent_resolved(window)
    else:
        resolved = df[df["status"] == "resolved"]

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
        return (
            "this matchup-tracking signal never becomes a multiplier on its own, so "
            "there's no accuracy to track for it -- though the same defender's name "
            "can separately reshape the baseline above via the \"vs. specific player\" "
            "blend; check the note above for whether that happened and its weight."
        )
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
    # Load the log once for every layer (it may be a database round
    # trip), not once per layer. Passing the already-windowed rows as
    # df gives the same result as layer_hit_rate's own df=None path.
    try:
        recent = _recent_resolved(50)
    except TrackerStorageError:
        recent = None
    lines = []
    for i, (layer_key, label) in enumerate(LAYER_DISPLAY, start=2):
        note = notes_by_layer[layer_key]
        if recent is None and not NEVER_APPLIED_BY_DESIGN.get(layer_key):
            accuracy_text = "track record unavailable right now (the prediction tracker can't be reached)."
        else:
            accuracy_text = format_layer_accuracy(layer_hit_rate(layer_key, "PTS", df=recent))
        lines.append(f"**[{i}] {label}:** {note} _{accuracy_text}_")
    return lines
