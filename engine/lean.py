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
  * "historical_accuracy"/"coverage" are held-out (leave-one-season-
    out) results, not a promise about any single game. With
    engine/lean_tiers.json present (written by clearest_read_sweep.py)
    the accuracy is the lean's GRADE's: stronger leans were right more
    often (about 57% just past the threshold, 71% well past it), so
    each lean carries the number for its own strength, grades under 60%
    aren't shown, and the "clearest read" is the lean whose grade has
    the best record. Graded leans are only shown for players averaging
    MIN_MPG+ minutes and for stats he averages MIN_SHOWN_AVG+ of (the
    backtest's population). Without the file, the stat's pooled number
    is used
    and every lean past the threshold is shown (the original behaviour).
"""

import json
import math
import os

import numpy as np
import pandas as pd

from engine.stat_columns import STAT_COLUMNS

STATS = [col for col, _ in STAT_COLUMNS]
MIN_GAMES = 10
# Graded leans are only shown for players like the backtest's (the top
# 150 by minutes each season: 98% of its games had a season-to-date
# average of 20+ minutes) and for stats he actually racks up (a "below
# his 0.1 three-point attempts" call is trivially true and says nothing).
# clearest_read_sweep.py applies the same two filters before grading.
MIN_MPG = 20.0
MIN_SHOWN_AVG = 1.0
REL_FLOOR = 1.0        # denominator floor for relative trends (keeps 0.3-block averages sane)
REL_CLIP = 3.0         # a relative trend is clipped to +/- this
VOLUME_COLUMN = {"PTS": "FGA", "FG3M": "FG3A", "FG3A": "FGA"}
MODELS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lean_models.json")
TIERS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lean_tiers.json")


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


def _load_tiers(path=TIERS_PATH):
    """(per-stat tiers, summary) from lean_tiers.json, or ({}, {})."""
    try:
        with open(path) as f:
            payload = json.load(f)
        return dict(payload["stats"]), dict(payload.get("summary", {}))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}, {}


LEAN_TIERS, LEAN_TIER_SUMMARY = _load_tiers()


def tier_for(stat, margin, tiers):
    """The tier dict a lean `margin` past the threshold falls in, or
    None when the stat has no tiers."""
    stat_tiers = (tiers or {}).get(stat, {}).get("tiers") or []
    chosen = None
    for t in stat_tiers:
        if margin >= t["min_margin"] - 1e-12:
            chosen = t
    return chosen


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
    out = {"n_games": int(len(df)), "mpg": float(min_season)}
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


def lean_for(stat, features, models=None, tiers=None):
    """None, or {"stat", "direction", "probability", "margin", "tier",
    "historical_accuracy", "historical_calls", "coverage"} when this stat
    has a model, the call clears its confidence threshold and (with
    tiers) its tier is one that's shown. `probability` is P(the called
    direction); `margin` is how far |p - 0.5| is past the threshold.
    tiers defaults to the shipped LEAN_TIERS only when models does too
    (explicit models without tiers = untiered, the original rule)."""
    if tiers is None:
        tiers = LEAN_TIERS if models is None else {}
    models = LEAN_MODELS if models is None else models
    model = models.get(stat)
    if model is None or not features or stat not in features:
        return None
    p = probability_above(model, features[stat])
    if abs(p - 0.5) < model["threshold"] or p == 0.5:
        return None
    margin = abs(p - 0.5) - model["threshold"]
    tier = tier_for(stat, margin, tiers)
    if tier is not None:
        if not tier["shown"]:
            return None
        avg, mpg = features[stat].get("season_avg"), features.get("mpg")
        if (avg is not None and avg < MIN_SHOWN_AVG) or (mpg is not None and mpg < MIN_MPG):
            return None
    above = p > 0.5
    stat_tiers = (tiers or {}).get(stat, {})
    return {
        "stat": stat,
        "direction": "above" if above else "below",
        "probability": p if above else 1.0 - p,
        "margin": margin,
        "tier": tier["label"] if tier else None,
        "historical_accuracy": tier["accuracy"] if tier else model["oos_accuracy"],
        "historical_calls": tier["n"] if tier else model.get("n"),
        "coverage": stat_tiers.get("shown_coverage", model["oos_coverage"]) if tier else model["oos_coverage"],
    }


def leans_for_game(features, models=None, tiers=None):
    """Every shown lean for one game, clearest first: with grades, the
    lean whose grade has the best backtested record (ties: larger
    margin); without, the larger margin. Element 0 is the clearest read.
    [] when there are none."""
    if not features:
        return []
    found = [lean_for(stat, features, models=models, tiers=tiers) for stat in STATS if stat in features]
    found = [x for x in found if x]
    if any(x["tier"] for x in found):
        # graded: the best backtested record first, then the larger margin
        return sorted(found, key=lambda x: (-x["historical_accuracy"], -x["margin"]))
    return sorted(found, key=lambda x: -x["margin"])


def describe_lean(lean, season_avg, stat_labels=None, with_frequency=True):
    """One plain-English line for a lean (no betting words)."""
    stat_labels = stat_labels or dict(STAT_COLUMNS)
    strength = f"{lean['tier']} lean — " if lean.get("tier") else ""
    calls = f", {lean['historical_calls']:,} calls" if lean.get("tier") and lean.get("historical_calls") else ""
    every = max(1, round(1.0 / lean["coverage"])) if with_frequency and lean["coverage"] > 0 else None
    freq = f" (about 1 in {every} games gets a call)" if every else ""
    return (
        f"{stat_labels.get(lean['stat'], lean['stat'])}: leans {lean['direction'].upper()} his season "
        f"average ({season_avg:.1f}) — {strength}calls like this were right "
        f"{lean['historical_accuracy']:.0%} of the time in 3 seasons of backtests{calls}{freq}"
    )


def strong_lean_lines(gamelog_df, gamelog_season, current_season, defense_multiplier,
                      stat_labels=None, models=None, tiers=None):
    """What the Single Player tab renders under the stat cards:
    (kind, lines) with kind "leans" (one line per stat with a lean),
    "none" (no stat cleared its threshold) or "not_yet" (not the
    current season, or too few games) -- the last two carry a single
    caption line. Lean lines are strongest first."""
    stat_labels = stat_labels or dict(STAT_COLUMNS)
    features = compute_features(gamelog_df, defense_multiplier) if gamelog_season == current_season else None
    if features is None:
        return "not_yet", [
            f"Strong leans start once a player has {MIN_GAMES} regular-season games this season."
        ]
    leans = leans_for_game(features, models=models, tiers=tiers)
    if not leans:
        return "none", ["No strong lean for this game — most games don't have one."]
    return "leans", [describe_lean(x, features[x["stat"]]["season_avg"], stat_labels) for x in leans]


def clearest_read(gamelog_df, gamelog_season, current_season, defense_multiplier,
                  models=None, tiers=None):
    """The strongest shown lean for one player's game plus his season
    average for that stat, or None (not the current season, too few
    games, or no lean). {"lean": ..., "season_avg": float}."""
    if gamelog_season != current_season:
        return None
    features = compute_features(gamelog_df, defense_multiplier)
    leans = leans_for_game(features, models=models, tiers=tiers)
    if not leans:
        return None
    return {"lean": leans[0], "season_avg": features[leans[0]["stat"]]["season_avg"]}
