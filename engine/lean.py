"""Strong leans -- "will tonight's line land ABOVE or BELOW his season
average?", shown only when a small per-stat logistic model is
confident enough that, in out-of-sample backtests, calls like it were
right at least 60% of the time.

WHY THIS EXISTS: the published directional accuracy of the opponent-
defense layer is ~51-52% -- a coin flip. Most games genuinely have no
usable direction. A few do (a player whose minutes and shot volume
jumped over the last 5-10 games, etc.), and this module surfaces only
those, with the backtested hit rate attached.

WHAT IT USES -- only what the live Single Player tool knows at
prediction time: the player's season gamelog (recent form and minutes
trend vs his season-to-date average, shot-volume trend, games played,
his average level) and the opponent-defense multiplier. Nothing about
the upcoming game's venue or schedule (the app doesn't know them).

SCOPE / LIMITS:
  * Regular-season rows only (Game_ID "0022..."), >= MIN_GAMES of them.
    The caller must also check the gamelog is the CURRENT season --
    resolve_season_gamelog() falls back to last season's full log
    early in a season, and a lean computed from that would describe
    last April, not tonight.
  * The fitted numbers live in engine/lean_models.json, written by
    lean_model_sweep.py (repo root) -- regenerate with
    `python3 lean_model_sweep.py`. Stats absent from that file have no
    lean (they never reached the bar out of sample). This module never
    fits anything.
  * "historical_accuracy"/"coverage" are the sweep's pooled
    leave-one-season-out results, not a promise about any single game.
"""

import json
import math
import os

import numpy as np
import pandas as pd

from engine.stat_columns import STAT_COLUMNS

STATS = [col for col, _ in STAT_COLUMNS]
MIN_GAMES = 10
REL_FLOOR = 1.0        # denominator floor for relative trends (keeps 0.3-block averages sane)
REL_CLIP = 3.0         # a relative trend is clipped to +/- this
VOLUME_COLUMN = {"PTS": "FGA", "FG3M": "FG3A", "FG3A": "FGA"}
MODELS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lean_models.json")


def feature_names(stat):
    """The model inputs for one stat, in coefficient order."""
    names = ["form_l5", "form_l10", "min_l5", "min_l10"]
    if stat in VOLUME_COLUMN:
        names.append("volume_l10")
    return names + ["log_games", "defense", "log_level"]


def _load_models(path=MODELS_PATH):
    try:
        with open(path) as f:
            return json.load(f)["models"]
    except (OSError, ValueError, KeyError):
        return {}


LEAN_MODELS = _load_models()


def _rel(recent, season):
    return float(np.clip((recent - season) / max(season, REL_FLOOR), -REL_CLIP, REL_CLIP))


def _defense_for(defense_multiplier, stat):
    if defense_multiplier is None:
        return 1.0
    if isinstance(defense_multiplier, dict):
        return float(defense_multiplier.get(stat, 1.0))
    return float(defense_multiplier)


def compute_features(gamelog_df, defense_multiplier):
    """{"n_games": n, stat: {feature: value, ..., "season_avg": avg}}
    from a season gamelog (any row order; non-regular-season rows are
    dropped), or None with fewer than MIN_GAMES regular-season games.
    defense_multiplier: a float applied to every stat, a {stat: float}
    dict, or None (= 1.0)."""
    if gamelog_df is None or len(gamelog_df) == 0:
        return None
    df = gamelog_df
    if "Game_ID" in df.columns:
        df = df[df["Game_ID"].astype(str).str.startswith("0022")]
    if len(df) < MIN_GAMES:
        return None
    df = df.assign(_date=pd.to_datetime(df["GAME_DATE"])).sort_values("_date", kind="mergesort")
    last5, last10 = df.tail(5), df.tail(10)

    def col(frame, name):
        return pd.to_numeric(frame[name], errors="coerce").astype(float)

    min_season = col(df, "MIN").mean()
    out = {"n_games": int(len(df))}
    for stat in STATS:
        base = col(df, stat).mean()
        f = {
            "season_avg": float(base),
            "form_l5": _rel(col(last5, stat).mean(), base),
            "form_l10": _rel(col(last10, stat).mean(), base),
            "min_l5": _rel(col(last5, "MIN").mean(), min_season),
            "min_l10": _rel(col(last10, "MIN").mean(), min_season),
            "log_games": math.log(len(df)),
            "defense": _defense_for(defense_multiplier, stat) - 1.0,
            "log_level": math.log1p(max(float(base), 0.0)),
        }
        if stat in VOLUME_COLUMN:
            vol = VOLUME_COLUMN[stat]
            f["volume_l10"] = _rel(col(last10, vol).mean(), col(df, vol).mean())
        out[stat] = f
    return out


def probability_above(model, feature_values):
    """P(actual > season average) for one stat's model and its
    {feature: value} dict."""
    x = np.array([feature_values[name] for name in model["features"]], dtype=float)
    z = (x - np.asarray(model["mean"], dtype=float)) / np.asarray(model["scale"], dtype=float)
    return float(1.0 / (1.0 + math.exp(-(model["intercept"] + float(np.dot(model["coef"], z))))))


def lean_for(stat, features, models=None):
    """None, or {"direction", "probability", "historical_accuracy",
    "coverage"} when this stat has a model and the call clears its
    confidence threshold. `probability` is P(the called direction)."""
    models = LEAN_MODELS if models is None else models
    model = models.get(stat)
    if model is None or not features or stat not in features:
        return None
    p = probability_above(model, features[stat])
    if abs(p - 0.5) < model["threshold"] or p == 0.5:
        return None
    above = p > 0.5
    return {
        "direction": "above" if above else "below",
        "probability": p if above else 1.0 - p,
        "historical_accuracy": model["oos_accuracy"],
        "coverage": model["oos_coverage"],
    }


def strong_lean_lines(gamelog_df, gamelog_season, current_season, defense_multiplier,
                      stat_labels=None, models=None):
    """What the Single Player tab renders under the stat cards:
    (kind, lines) with kind "leans" (one line per stat with a lean),
    "none" (no stat cleared its threshold) or "not_yet" (not the
    current season, or too few games) -- the last two carry a single
    caption line."""
    stat_labels = stat_labels or dict(STAT_COLUMNS)
    features = compute_features(gamelog_df, defense_multiplier) if gamelog_season == current_season else None
    if features is None:
        return "not_yet", [
            f"Strong leans start once a player has {MIN_GAMES} regular-season games this season."
        ]
    lines = []
    for stat in STATS:
        lean = lean_for(stat, features, models=models)
        if lean is None:
            continue
        every = max(1, round(1.0 / lean["coverage"])) if lean["coverage"] > 0 else None
        freq = f" (about 1 in {every} games gets a call)" if every else ""
        lines.append(
            f"{stat_labels.get(stat, stat)}: leans {lean['direction'].upper()} his season average "
            f"({features[stat]['season_avg']:.1f}) — calls like this were right "
            f"{lean['historical_accuracy']:.0%} of the time in 3 seasons of backtests{freq}"
        )
    if not lines:
        return "none", ["No strong lean for this game — most games don't have one."]
    return "leans", lines
