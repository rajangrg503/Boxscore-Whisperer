"""
Train/test investigation: does any alternative opponent_defense formula
beat the current one (engine/adjustments/defense.py's
DEF_ADJUSTMENT_STRENGTH=0.5, linear in def_gap_pct)? Follows up on the
backtest finding that opponent_defense is only marginally better than a
coin flip (51.8% PTS down to 49.2% TOV) and shows no improvement even
at the largest DEF_RATING-gap quartile.

Design (agreed before this script was written -- see
~/.claude/plans/hazy-jumping-glade.md for the full discussion):

TRAIN = 2023-24 + 2024-25 (19,993 rows). TEST = 2025-26 (9,921 rows),
held out and untouched by this module's train-side functions. Time-
based, not random -- a season's rows aren't independent draws (shared
baseline drift, shared pace/rule environment), so random splitting
would leak correlated signal across train/test.

KEY TRICK: every row in backtest_results.csv was produced with
team_h2h_weight=0.0 (confirmed via engine/backtest_point_in_time.py:193,196
-- every get_defense_adjustment() call in the backtest engine uses the
default), so effective_strength was always exactly
DEF_ADJUSTMENT_STRENGTH=0.5. That makes the stored multiplier losslessly
invertible back to the raw signal:
    def_gap_pct = (stored_multiplier - 1) / 0.5
No new data fetch, no re-running the point-in-time engine -- every
candidate below is just a different function of this one recovered
column, computed once.

Six pre-registered candidates (not an open-ended search) plus the
implicit 50% (coin-flip) floor -- see module docstring section further
down for what each one tests. C5/C6 are calibrated on TRAIN ONLY so
their average adjustment magnitude matches C1 (current), isolating
"does the shape matter" from "is the average strength different."

Structure enforces the train/test discipline: explore_candidates_on_train()
touches ONLY train rows. validate_on_test() is a separate entry point,
never called by this module's __main__ -- must be invoked deliberately,
later, against the single candidate (if any) that survives train.
"""

import json

import numpy as np
import pandas as pd

from engine.stat_columns import STAT_COLUMNS

RESULTS_PATH = "backtest_results.csv"
TRAIN_SEASONS = ["2023-24", "2024-25"]
TEST_SEASON = "2025-26"
CURRENT_STRENGTH = 0.5
NOISE_MARGIN_PCT = 0.7  # ~2 binomial standard errors at train's ~20k-row scale; pre-set, not tuned after seeing results


def _recover_gap(df):
    """Restrict to rows where opponent_defense was actually applied
    (excludes the season's-first-calendar-month rows, which carry a
    neutral placeholder multiplier, not real signal), and add the
    losslessly-recovered def_gap_pct column."""
    def _get_multiplier(layers_json):
        entry = json.loads(layers_json).get("opponent_defense", {})
        if not entry.get("applied"):
            return None
        return entry.get("value", {}).get("_all")

    out = df.copy()
    out["_multiplier"] = out["layers_json"].apply(_get_multiplier)
    out = out[out["_multiplier"].notna()].copy()
    out["def_gap_pct"] = (out["_multiplier"] - 1.0) / CURRENT_STRENGTH
    return out


def build_candidates(df):
    """df must already have def_gap_pct (see _recover_gap). Returns
    {candidate_name: multiplier_series}. C5/C6's constants are derived
    from df -- call this with TRAIN rows only when scoping candidates,
    never with test rows mixed in."""
    g = df["def_gap_pct"]
    c1 = 1 + g * CURRENT_STRENGTH
    target_mean_abs = (c1 - 1).abs().mean()  # C1's average |adjustment| -- the shared calibration target

    sign_g = np.sign(g)
    sqrt_abs_g = np.sqrt(g.abs())
    k = target_mean_abs / sqrt_abs_g.mean()

    return {
        "C1 current (strength=0.50, linear)": c1,
        "C2 half strength (0.25, linear)": 1 + g * 0.25,
        "C3 1.5x strength (0.75, linear)": 1 + g * 0.75,
        "C4 double strength (1.00, linear)": 1 + g * 1.0,
        "C5 sign-only fixed step": 1 + sign_g * target_mean_abs,
        "C6 sqrt-compressed magnitude": 1 + sign_g * sqrt_abs_g * k,
    }, target_mean_abs


def _directional_accuracy(df, multiplier, stat_col):
    """multiplier: a Series aligned to df's index. Same methodology as
    analytics/layer_accuracy.py and backtest_diagnostics.py: exclude
    rows with no real outcome signal (actual == base) and rows where
    this candidate makes no directional prediction (multiplier == 1
    exactly -- e.g. sign(gap)==0)."""
    base_col, actual_col = f"{stat_col}_base", f"{stat_col}_actual"
    scored = df[[base_col, actual_col]].copy()
    scored["multiplier"] = multiplier
    scored = scored[scored[base_col].notna() & scored[actual_col].notna()]
    scored = scored[scored[actual_col] != scored[base_col]]
    scored = scored[scored["multiplier"] != 1.0]
    if scored.empty:
        return None, 0
    predicted_dir = np.sign(scored["multiplier"] - 1.0)
    actual_dir = np.sign(scored[actual_col] - scored[base_col])
    hits = int((predicted_dir == actual_dir).sum())
    n = len(scored)
    return hits / n * 100, n


def _binomial_se_pct(n, p=0.5):
    return (p * (1 - p) / n) ** 0.5 * 100


def explore_candidates_on_train():
    """TRAIN ONLY. Prints per-stat and mean directional accuracy for
    every candidate, the 50% floor, and applies the pre-committed
    selection rule. Returns (train_df, candidates_dict, summary_df) for
    inspection -- never touches TEST_SEASON rows."""
    df = pd.read_csv(RESULTS_PATH)
    train = df[df["season"].isin(TRAIN_SEASONS)]
    train = _recover_gap(train)
    print(f"Train rows (opponent_defense applied): {len(train)} "
          f"(seasons: {TRAIN_SEASONS})")

    candidates, target_mean_abs = build_candidates(train)
    print(f"C1's mean |adjustment| on train (the C5/C6 calibration target): {target_mean_abs:.4f}\n")

    rows = []
    for name, mult in candidates.items():
        per_stat = []
        for col, _label in STAT_COLUMNS:
            acc, n = _directional_accuracy(train, mult, col)
            per_stat.append((col, acc, n))
        valid = [(a, n) for _c, a, n in per_stat if a is not None]
        mean_acc = sum(a for a, _n in valid) / len(valid) if valid else None
        total_n = sum(n for _a, n in valid)
        rows.append({"candidate": name, "mean_directional_accuracy": mean_acc,
                      "total_n": total_n, "per_stat": per_stat})

    print(f"{'Candidate':<38} {'Mean acc':>10} {'vs 50%':>10} {'vs C1':>10} {'Total N':>10}")
    print("-" * 82)
    c1_mean = next(r["mean_directional_accuracy"] for r in rows if r["candidate"].startswith("C1"))
    for r in rows:
        se = _binomial_se_pct(r["total_n"] / len(STAT_COLUMNS))  # rough, per-stat-scale SE for display
        vs_floor = r["mean_directional_accuracy"] - 50.0
        vs_c1 = r["mean_directional_accuracy"] - c1_mean
        print(f"{r['candidate']:<38} {r['mean_directional_accuracy']:>9.2f}% "
              f"{vs_floor:>+9.2f}pt {vs_c1:>+9.2f}pt {r['total_n']:>10}")

    print(f"\nPer-stat breakdown:")
    for r in rows:
        print(f"\n  {r['candidate']}:")
        for col, acc, n in r["per_stat"]:
            acc_str = f"{acc:.1f}%" if acc is not None else "N/A"
            print(f"    {col:<6} {acc_str:>8}  (N={n})")

    print(f"\nPre-set noise margin: {NOISE_MARGIN_PCT} percentage points "
          f"(~2 binomial SE at train scale). A candidate must beat BOTH "
          f"50% AND C1's {c1_mean:.2f}% by more than this margin to advance.")

    survivors = [
        r for r in rows
        if not r["candidate"].startswith("C1")
        and r["mean_directional_accuracy"] - 50.0 > NOISE_MARGIN_PCT
        and r["mean_directional_accuracy"] - c1_mean > NOISE_MARGIN_PCT
    ]
    print(f"\nCandidates clearing both bars: {[r['candidate'] for r in survivors] or 'NONE'}")
    if survivors:
        winner = max(survivors, key=lambda r: r["mean_directional_accuracy"])
        print(f"Single candidate that would advance to the held-out 2025-26 test: {winner['candidate']}")
    else:
        print("No candidate clears the pre-set bar -- per the pre-committed rule, "
              "the investigation stops here. Nothing advances to the test set.")

    return train, candidates, rows


MIN_RELATIVE_IMPROVEMENT_PCT = 2.0  # pre-set practical-significance floor: a candidate
                                     # must beat C1 by at least this much relative MAE
                                     # reduction, not just be statistically distinguishable
                                     # from it -- guards against a real-but-trivial win at
                                     # this sample size counting as a "winner"
N_BOOTSTRAP = 1000


def build_candidates_with_baseline(df):
    """Same six candidates as build_candidates(), plus C0 (no adjustment
    -- multiplier == 1.0 for every row), which directional accuracy
    could not score (no sign to predict) but MAE/RMSE can score
    naturally. This is the actual "no adjustment" comparator the
    magnitude-sensitive re-analysis needed."""
    candidates, target_mean_abs = build_candidates(df)
    candidates = dict(candidates)
    candidates["C0 no adjustment"] = pd.Series(1.0, index=df.index)
    return candidates, target_mean_abs


def _errors_by_stat(df, multiplier):
    """Returns {stat_col: (predicted - actual) Series, index-aligned to
    df, NaN-dropped} for one candidate's multiplier. predicted = base *
    multiplier -- exactly how the live formula and the backtest engine
    both compute a prediction from a layer's multiplier."""
    out = {}
    for col, _label in STAT_COLUMNS:
        base_col, actual_col = f"{col}_base", f"{col}_actual"
        valid = df[[base_col, actual_col]].dropna()
        predicted = valid[base_col] * multiplier.reindex(valid.index)
        out[col] = predicted - valid[actual_col]
    return out


def _mae_rmse_by_stat(errors_by_stat):
    mae, rmse = {}, {}
    for col, err in errors_by_stat.items():
        mae[col] = err.abs().mean()
        rmse[col] = (err ** 2).mean() ** 0.5
    return mae, rmse


def _avg_pct_improvement(mae_by_stat, mae_c0_by_stat):
    """Per-stat % MAE reduction vs C0, averaged equally across the 7
    stats (not row-weighted) -- same per-stat-equal-weighting
    convention as the directional-accuracy round, so PTS's naturally
    larger absolute errors don't dominate a stat like STL's."""
    pct = [
        (mae_c0_by_stat[col] - mae_by_stat[col]) / mae_c0_by_stat[col] * 100
        for col, _label in STAT_COLUMNS
    ]
    return sum(pct) / len(pct)


def explore_candidates_on_train_mae():
    """TRAIN ONLY, magnitude-sensitive re-analysis of the SAME six
    pre-registered candidates (plus C0), using the SAME train/test
    split -- following up on the directional-accuracy round, which
    turned out to be structurally blind to magnitude (see this
    project's plan file, Known Issues). Never touches TEST_SEASON."""
    df = pd.read_csv(RESULTS_PATH)
    train = df[df["season"].isin(TRAIN_SEASONS)]
    train = _recover_gap(train)
    print(f"Train rows (opponent_defense applied): {len(train)} (seasons: {TRAIN_SEASONS})\n")

    candidates, _target = build_candidates_with_baseline(train)

    # Point estimates
    mae_by_candidate, rmse_by_candidate = {}, {}
    for name, mult in candidates.items():
        errors = _errors_by_stat(train, mult)
        mae, rmse = _mae_rmse_by_stat(errors)
        mae_by_candidate[name] = mae
        rmse_by_candidate[name] = rmse

    mae_c0 = mae_by_candidate["C0 no adjustment"]
    avg_improvement = {
        name: _avg_pct_improvement(mae, mae_c0)
        for name, mae in mae_by_candidate.items()
    }
    c1_name = next(n for n in candidates if n.startswith("C1"))

    print(f"{'Candidate':<38} {'Avg % MAE↓ vs C0':>18} {'vs C1':>10}")
    print("-" * 70)
    for name in candidates:
        vs_c1 = avg_improvement[name] - avg_improvement[c1_name]
        print(f"{name:<38} {avg_improvement[name]:>+17.3f}% {vs_c1:>+9.3f}pt")

    print(f"\nPer-stat MAE (predicted vs. actual, same units as the stat):")
    header = f"{'':<10}" + "".join(f"{n.split()[0]:>10}" for n in candidates)
    print(header)
    for col, _label in STAT_COLUMNS:
        row = f"{col:<10}" + "".join(f"{mae_by_candidate[n][col]:>10.3f}" for n in candidates)
        print(row)

    print(f"\nPer-stat RMSE:")
    print(header)
    for col, _label in STAT_COLUMNS:
        row = f"{col:<10}" + "".join(f"{rmse_by_candidate[n][col]:>10.3f}" for n in candidates)
        print(row)

    # Bootstrap: resample ROWS (not per-stat independently -- a row/game
    # is the real unit of observation, touching all 7 stats together),
    # recompute (candidate avg-improvement - C1 avg-improvement) on each
    # resample, take the empirical 95% CI of that difference.
    rng = np.random.default_rng(seed=20260910)  # fixed seed: reproducible, not re-rollable to chase a result
    n = len(train)
    diffs = {name: [] for name in candidates if name != c1_name}

    for _ in range(N_BOOTSTRAP):
        idx = train.index[rng.integers(0, n, size=n)]
        boot = train.loc[idx]
        boot_mae = {}
        for name, mult in candidates.items():
            mult_boot = mult.reindex(idx)
            mult_boot.index = range(len(idx))  # avoid duplicate-index alignment issues from sampling with replacement
            boot_reset = boot.reset_index(drop=True)
            errors = _errors_by_stat(boot_reset, mult_boot)
            mae, _ = _mae_rmse_by_stat(errors)
            boot_mae[name] = mae
        boot_c0 = boot_mae["C0 no adjustment"]
        boot_improvement = {name: _avg_pct_improvement(mae, boot_c0) for name, mae in boot_mae.items()}
        for name in diffs:
            diffs[name].append(boot_improvement[name] - boot_improvement[c1_name])

    print(f"\n{N_BOOTSTRAP}-resample bootstrap: 95% CI for (candidate's avg %-improvement) minus (C1's), "
          f"pre-set practical floor = {MIN_RELATIVE_IMPROVEMENT_PCT} relative-% points:")
    print(f"{'Candidate':<38} {'point est.':>12} {'95% CI':>24}")
    print("-" * 76)
    survivors = []
    for name, d in diffs.items():
        d_arr = np.array(d)
        lo, hi = np.percentile(d_arr, [2.5, 97.5])
        point = avg_improvement[name] - avg_improvement[c1_name]
        excludes_zero = lo > 0 or hi < 0
        clears_practical = point > MIN_RELATIVE_IMPROVEMENT_PCT
        print(f"{name:<38} {point:>+11.3f}pt   [{lo:>+7.3f}, {hi:>+7.3f}]"
              f"{'  <-- candidate' if (excludes_zero and clears_practical) else ''}")
        if excludes_zero and clears_practical:
            survivors.append((name, point))

    print(f"\nCandidates clearing both bars (CI excludes zero AND >{MIN_RELATIVE_IMPROVEMENT_PCT}pt practical floor): "
          f"{[s[0] for s in survivors] or 'NONE'}")
    if survivors:
        winner = max(survivors, key=lambda s: s[1])
        print(f"Single candidate that would advance to the held-out 2025-26 test: {winner[0]}")
    else:
        print("No candidate clears the pre-set bar -- per the pre-committed rule, "
              "the investigation stops here. Nothing advances to the test set.")

    return train, candidates, mae_by_candidate, rmse_by_candidate, avg_improvement, diffs


if __name__ == "__main__":
    explore_candidates_on_train()
