"""
Diagnostic analysis of backtest_results.csv -- reproduces the two
segmentation questions asked after the first real backtest run (see
~/.claude/plans/backtest-engine-plan.md, "First real backtest result"):
does opponent_defense's directional accuracy vary by data_quality
tier, and does it vary by the magnitude of the DEF_RATING gap from
league average?

Deliberately read-only / diagnostic -- this does NOT tune
DEF_ADJUSTMENT_STRENGTH or any other formula constant in response to
what it finds. Doing so would be fitting to this exact evaluation set,
producing a misleading result rather than a real improvement.

Usage:
    python3 backtest_diagnostics.py
"""

import json

import pandas as pd

from analytics.layer_accuracy import layer_hit_rate
from engine.stat_columns import STAT_COLUMNS

RESULTS_PATH = "backtest_results.csv"


def headline_by_stat(df):
    print(f"{'Stat':<8} {'Hit Rate':<10} {'N':<10}")
    print("-" * 30)
    for col, _label in STAT_COLUMNS:
        result = layer_hit_rate("opponent_defense", col, df=df)
        hr = f"{result.hit_rate:.1f}%" if result.hit_rate is not None else "N/A"
        print(f"{col:<8} {hr:<10} {result.n:<10}")


def data_quality_breakdown(df):
    def get_dq(layers_json):
        return json.loads(layers_json)["opponent_defense"]["data_quality"]

    dq = df["layers_json"].apply(get_dq)
    print(dq.value_counts().to_string())


def gap_magnitude_breakdown(df, stat_col="PTS"):
    def get_multiplier(layers_json):
        entry = json.loads(layers_json).get("opponent_defense", {})
        if not entry.get("applied"):
            return None
        return entry.get("value", {}).get("_all", 1.0)

    scored = df.copy()
    scored["multiplier"] = scored["layers_json"].apply(get_multiplier)
    scored = scored[scored["multiplier"].notna()]
    scored = scored[abs(scored["multiplier"] - 1.0) >= 1e-9]

    base_col, actual_col = f"{stat_col}_base", f"{stat_col}_actual"
    scored = scored[scored[base_col].notna() & scored[actual_col].notna()]
    scored = scored[scored[actual_col] != scored[base_col]]

    scored["gap_magnitude"] = abs(scored["multiplier"] - 1.0)
    scored["predicted_dir"] = (scored["multiplier"] > 1.0).astype(int) * 2 - 1
    scored["actual_dir"] = (scored[actual_col] > scored[base_col]).astype(int) * 2 - 1
    scored["hit"] = scored["predicted_dir"] == scored["actual_dir"]

    scored["quartile"] = pd.qcut(
        scored["gap_magnitude"], 4,
        labels=["Q1 (smallest gap)", "Q2", "Q3", "Q4 (largest gap)"],
    )
    summary = scored.groupby("quartile", observed=True).agg(
        n=("hit", "size"), hit_rate=("hit", "mean"), avg_gap=("gap_magnitude", "mean"),
    )
    summary["hit_rate"] = (summary["hit_rate"] * 100).round(1)
    summary["avg_gap"] = summary["avg_gap"].round(4)
    print(summary.to_string())


def main():
    df = pd.read_csv(RESULTS_PATH)
    print(f"Loaded {len(df)} rows from {RESULTS_PATH}\n")

    print("=== Headline: opponent_defense directional accuracy by stat ===")
    headline_by_stat(df)

    print("\n=== Diagnostic 1: data_quality tier breakdown ===")
    data_quality_breakdown(df)

    print("\n=== Diagnostic 2: PTS accuracy by DEF_RATING gap magnitude quartile ===")
    gap_magnitude_breakdown(df, stat_col="PTS")


if __name__ == "__main__":
    main()
