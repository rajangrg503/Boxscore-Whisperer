"""
Point-in-time backtest + fit of the Single Player "strong lean"
(engine/lean.py): per stat, will the actual line land ABOVE or BELOW
the player's season-to-date average -- and when is a model confident
enough to say so?

WHY THIS EXISTS: the published directional accuracy (opponent-defense
layer) is ~51-52%, i.e. a coin flip. A probe on PTS found that recent
form, minutes trend and shot volume carry a little signal overall and
noticeably more in the model's most confident calls. This script
turns that into something the live app can show honestly: a lean only
where a threshold chosen out of sample was historically right >= 60%
of the time, with the out-of-sample hit rate attached.

WHAT IT TESTS -- the exact live feature code, point in time:
  * Case set = run_backtest.py's (3 seasons, top-150 by minutes,
    regular-season targets, opponent resolvable), further restricted to
    >= engine.lean.MIN_GAMES (10) prior same-season regular-season
    games -- the live app hides the lean below that.
  * Features are engine/lean.py's compute_features() on the player's
    cached gamelog rows strictly before the game (regular season only,
    date-sorted). They're computed vectorised here; every
    VERIFY_EVERY-th case goes through the real compute_features() and
    must match to 1e-9, and point_in_time_baseline() must agree on the
    game count and season average.
  * Defense multiplier = get_defense_adjustment() on the point-in-time
    monthly checkpoint (build_backtest_row's inputs), cross-checked
    against backtest_results.csv's *_predicted / *_base.
  * Only what the live Single Player tool knows is used: no home/away,
    no back-to-back (the app knows neither for the upcoming game).
  * Target: actual > season average (1) vs actual < season average (0).
    Exact ties are neither -- dropped from fitting and scoring, but
    still in the coverage denominator.

MODEL / THRESHOLD (per stat):
  * Plain L2-regularised logistic regression (IRLS) on standardised
    features, leave-one-season-out (LOSO).
  * Threshold, nested: for held-out season S, the model is fit on the
    other two seasons A, B; the cutoff c on |p - 0.5| is chosen from
    A-predicted-by-B and B-predicted-by-A (so the cutoff itself never
    sees S or in-sample fits): the SMALLEST c on CUTOFFS whose
    calls are >= TARGET_TRAIN_ACC (0.62, a margin above the 60% bar to
    absorb selection optimism) right, with >= MIN_COVERAGE (5%) of the
    training games called. That cutoff is then applied to S.
  * Reported "strong lean" accuracy/coverage = the three held-out
    seasons pooled, each with its own fold cutoff. 95% CI = cluster
    bootstrap over player-seasons.
  * Shipped model = the same regression refit on ALL three seasons;
    shipped cutoff = the MEDIAN of the three fold cutoffs (not re-picked
    on all data). The row "shipped_cutoff_on_loso" shows that cutoff on
    the LOSO predictions (mildly optimistic: each season helped choose
    two of the three fold cutoffs).
  * A stat ships only if every fold found a cutoff, the pooled held-out
    accuracy is >= SHIP_ACC (60%), pooled held-out coverage is
    >= MIN_COVERAGE, AND the strong leans beat a "level-only" model
    (same regression on log_level + log_games only) on the SAME games
    by a cluster-bootstrap CI that excludes zero -- see SKEW below.

SKEW (read before quoting a number): per-game counts are right-skewed,
so the actual lands BELOW the season average more often than above --
mildly for PTS, heavily for STL/BLK/OREB/FG3M (a 0.4-blocks player
records 0 most nights). A model that simply says "below" for
low-average players can look 60-80% accurate with no insight at all.
So every row reports: base rate of "above"; balanced accuracy (mean of
the hit rates on actual-above and actual-below games) for all calls;
the share of strong leans that say "below"; what "always the majority
direction" and the level-only model score on the same strong-lean
games; and the lift over level-only, which is the ship gate.

LIMITS:
  * The cutoff grid search is still selection on noisy accuracy; the
    nested choice + 0.62 target reduce but don't remove optimism.
  * The live app's defense multiplier is the season-to-date rating
    (not a monthly checkpoint); for >= 10-game players both are
    current-season ratings. Everything else is identical.
  * Top-150-by-minutes players only; bench players' leans are
    extrapolation.
  * Early in a season the app falls back to last season's full log --
    the lean must be hidden then (app.py checks the season).

Usage:
    python3 lean_model_sweep.py
Writes lean_model_sweep_results.csv (one row per stat x evaluation)
and engine/lean_models.json (the constants engine/lean.py loads).
Re-run it whenever the case set, the features in engine/lean.py, or
the threshold rule changes; the JSON and lean.py then stay in sync.
"""

import json
import os
import time

import numpy as np
import pandas as pd

# Importing the k sweep installs its offline import stubs (a no-op where
# streamlit/nba_api are importable) and memoises bpit._load_df_cache.
import shrinkage_k_sweep as sks  # noqa: E402
from shrinkage_k_sweep import REPO_ROOT, _memo_load, cluster_bootstrap_diff, defense_for  # noqa: E402
from engine import lean  # noqa: E402
from engine.backtest_point_in_time import (  # noqa: E402
    _is_regular_season_game_id,
    get_point_in_time_opponent_defense,
    point_in_time_baseline,
)
from engine.team_ids import TEAM_ID_BY_ABBR  # noqa: E402
from run_backtest import SEASONS, load_fixed_player_list  # noqa: E402

OUTPUT_PATH = os.path.join(REPO_ROOT, "lean_model_sweep_results.csv")
MODELS_PATH = lean.MODELS_PATH
PUBLISHED_BACKTEST_PATH = sks.PUBLISHED_BACKTEST_PATH
STATS = lean.STATS
CUTOFFS = np.round(np.arange(0.0, 0.4501, 0.005), 3)
TARGET_TRAIN_ACC = 0.62
SHIP_ACC = 0.60
MIN_COVERAGE = 0.05
L2 = 1.0
LEVEL_ONLY = ["log_level", "log_games"]
VERIFY_EVERY = 20
SEED = 20260917


# ---- dataset ---------------------------------------------------------------
def _rel(recent, season):
    return np.clip((recent - season) / np.maximum(season, lean.REL_FLOOR), -lean.REL_CLIP, lean.REL_CLIP)


def season_features(reg):
    """Vectorised compute_features for every row of a date-sorted
    regular-season log: row i uses rows 0..i-1 only."""
    k = np.arange(len(reg), dtype=float)
    k[0] = np.nan
    raw = {}
    for col in set(STATS) | {"MIN"} | set(lean.VOLUME_COLUMN.values()):
        x = pd.to_numeric(reg[col], errors="coerce").astype(float)
        raw[col] = ((x.cumsum().shift(1) / k).values,
                    x.rolling(5).mean().shift(1).values,
                    x.rolling(10).mean().shift(1).values)
    m_base, m5, m10 = raw["MIN"]
    feats = {}
    for stat in STATS:
        base, l5, l10 = raw[stat]
        f = {"season_avg": base, "form_l5": _rel(l5, base), "form_l10": _rel(l10, base),
             "min_l5": _rel(m5, m_base), "min_l10": _rel(m10, m_base),
             "log_games": np.log(k), "log_level": np.log1p(np.maximum(base, 0.0))}
        if stat in lean.VOLUME_COLUMN:
            v_base, _v5, v10 = raw[lean.VOLUME_COLUMN[stat]]
            f["volume_l10"] = _rel(v10, v_base)
        feats[stat] = f
    return feats


def build():
    rows, verify = [], []
    for season, season_start in SEASONS:
        t0 = time.time()
        n0 = len(rows)
        for player_id, _name in load_fixed_player_list(season):
            df, _ = _memo_load(f"gamelog_{player_id}_{season}")
            if df is None:
                raise FileNotFoundError(f"gamelog_{player_id}_{season}.json missing (see run_backtest.py)")
            reg = df[df["Game_ID"].map(_is_regular_season_game_id)].copy()
            reg["_date"] = pd.to_datetime(reg["GAME_DATE"])
            reg = reg.sort_values("_date", kind="mergesort").reset_index(drop=True)
            if reg["_date"].duplicated().any():
                raise AssertionError(f"duplicate game dates for {player_id} {season}")
            feats = season_features(reg)
            for i in range(lean.MIN_GAMES, len(reg)):
                g = reg.iloc[i]
                opp = TEAM_ID_BY_ABBR.get(g["MATCHUP"].split()[-1])
                if opp is None:
                    continue
                game_date = g["_date"].date()
                lookup = get_point_in_time_opponent_defense(season, season_start, opp, game_date)
                mult = defense_for({"defense_lookup": lookup}, 0.0).multiplier_for("PTS")
                row = {"player_id": player_id, "season": season, "game_id": str(g["Game_ID"]),
                       "n_games": i, "defense_mult": mult}
                for stat in STATS:
                    for name, arr in feats[stat].items():
                        row[f"{stat}:{name}"] = float(arr[i])
                    row[f"{stat}:defense"] = mult - 1.0
                    row[f"{stat}:actual"] = float(g[stat])
                rows.append(row)
                if len(rows) % VERIFY_EVERY == 0:
                    verify.append((player_id, season, df, game_date, mult, row))
        print(f"  {season}: {len(rows) - n0} eligible cases ({time.time() - t0:.0f}s)")
    return pd.DataFrame(rows), verify


def verify_features(verify):
    """Sampled cases through the REAL engine.lean.compute_features (fed
    the unsorted, playoff-including cached log cut at the game date) and
    point_in_time_baseline."""
    bad = 0
    for player_id, season, df, game_date, mult, row in verify:
        prior = df[pd.to_datetime(df["GAME_DATE"]) < pd.Timestamp(game_date)]
        live = lean.compute_features(prior.sample(frac=1.0, random_state=0), mult)
        pit, n = point_in_time_baseline(player_id, season, game_date)
        ok = live is not None and live["n_games"] == row["n_games"] == n
        for stat in STATS:
            ok = ok and abs(pit[stat][0] - row[f"{stat}:season_avg"]) < 1e-9
            for name in lean.feature_names(stat) + ["season_avg"]:
                ok = ok and abs(live[stat][name] - row[f"{stat}:{name}"]) < 1e-9
        bad += not ok
    print(f"  engine.lean.compute_features on {len(verify)} sampled cases (shuffled rows): {bad} mismatches")
    if bad:
        raise AssertionError("vectorised features diverge from engine/lean.py")


def cross_check_published(data):
    if not os.path.exists(PUBLISHED_BACKTEST_PATH):
        print("  (backtest_results.csv not found -- skipping cross-check)")
        return
    pub = pd.read_csv(PUBLISHED_BACKTEST_PATH, dtype={"player_id": str, "game_id": str})
    j = data.merge(pub, on=["player_id", "season", "game_id"], how="left")
    missing = int(j["PTS_base"].isna().sum())
    base_diff = max((j[f"{s}:season_avg"] - j[f"{s}_base"]).abs().max() for s in STATS)
    pts = j["PTS_base"] > 0
    mult_diff = (j.loc[pts, "PTS_predicted"] / j.loc[pts, "PTS_base"] - j.loc[pts, "defense_mult"]).abs().max()
    print(f"  cross-check vs backtest_results.csv: {len(data)} cases, {missing} not in the published set, "
          f"max |season avg diff| = {base_diff:.2e}, max |defense multiplier diff| = {mult_diff:.2e}")
    if missing or base_diff > 1e-9 or mult_diff > 1e-9:
        raise AssertionError("case set / baseline / defense diverge from the published backtest")


# ---- model -----------------------------------------------------------------
def _sigmoid(t):
    return 1.0 / (1.0 + np.exp(-t))


def fit_logistic(X, y, l2=L2):
    mean = X.mean(axis=0)
    scale = X.std(axis=0)
    scale[scale == 0] = 1.0
    A = np.column_stack([np.ones(len(X)), (X - mean) / scale])
    w = np.zeros(A.shape[1])
    pen = np.full(A.shape[1], l2)
    pen[0] = 0.0
    for _ in range(100):
        p = _sigmoid(A @ w)
        grad = A.T @ (y - p) - pen * w
        hess = (A * (p * (1 - p))[:, None]).T @ A + np.diag(pen)
        step = np.linalg.solve(hess, grad)
        w += step
        if np.abs(step).max() < 1e-10:
            break
    return {"mean": mean, "scale": scale, "intercept": float(w[0]), "coef": w[1:]}


def predict(model, X):
    return _sigmoid(model["intercept"] + ((X - model["mean"]) / model["scale"]) @ model["coef"])


def call_stats(p, y, scored, cutoff):
    called = (np.abs(p - 0.5) >= cutoff) & (p != 0.5)
    s = called & scored
    hits = (p > 0.5) == (y == 1)
    return called, s, hits


def pick_cutoff(p, y, scored):
    for c in CUTOFFS:
        called, s, hits = call_stats(p, y, scored, c)
        if called.mean() < MIN_COVERAGE:
            return None
        if s.any() and hits[s].mean() >= TARGET_TRAIN_ACC:
            return float(c)
    return None


def balanced_accuracy(p, y, s):
    above = s & (y == 1)
    below = s & (y == 0)
    return 0.5 * ((p[above] > 0.5).mean() + (p[below] < 0.5).mean())


def main():
    print("Building point-in-time cases ...")
    data, verify = build()
    verify_features(verify)
    cross_check_published(data)
    seasons = data["season"].values
    season_list = [s for s, _ in SEASONS]
    clusters = (data["player_id"] + "_" + data["season"]).values
    boot_rng = np.random.default_rng(sks.BOOTSTRAP_SEED)
    n_all = len(data)
    print(f"{n_all} cases with >= {lean.MIN_GAMES} prior games\n")

    out_rows, shipped = [], {}
    for stat in STATS:
        names = lean.feature_names(stat)
        X = data[[f"{stat}:{n}" for n in names]].values
        XL = data[[f"{stat}:{n}" for n in LEVEL_ONLY]].values
        diff = data[f"{stat}:actual"].values - data[f"{stat}:season_avg"].values
        tie = np.isclose(diff, 0.0, atol=1e-9)
        y = (diff > 0).astype(float)
        scored = ~tie
        base_rate = y[scored].mean()

        p_oos = np.full(n_all, np.nan)
        pl_oos = np.full(n_all, np.nan)
        fold_cut = {}
        strong = np.zeros(n_all, dtype=bool)
        majority_dir = np.zeros(n_all)
        for held in season_list:
            tr, te = seasons != held, seasons == held
            m = fit_logistic(X[tr & scored], y[tr & scored])
            ml = fit_logistic(XL[tr & scored], y[tr & scored])
            p_oos[te], pl_oos[te] = predict(m, X[te]), predict(ml, XL[te])
            majority_dir[te] = float(y[tr & scored].mean() > 0.5)
            # nested: each training season predicted by the other one
            p_inner = np.full(n_all, np.nan)
            a, b = [s for s in season_list if s != held]
            for fit_s, pred_s in [(a, b), (b, a)]:
                fs, ps = seasons == fit_s, seasons == pred_s
                p_inner[ps] = predict(fit_logistic(X[fs & scored], y[fs & scored]), X[ps])
            fold_cut[held] = pick_cutoff(p_inner[tr], y[tr], scored[tr])
            if fold_cut[held] is not None:
                called, _, _ = call_stats(p_oos, y, scored, fold_cut[held])
                strong |= called & te

        _, s_all, hits_all = call_stats(p_oos, y, scored, 0.0)
        hits_level = (pl_oos > 0.5) == (y == 1)
        hits_major = majority_dir == y
        common = {"stat": stat, "base_rate_above": base_rate, "n_games": n_all,
                  "n_scored": int(scored.sum())}
        out_rows.append({**common, "evaluation": "all_calls_loso", "season": "pooled", "cutoff": 0.0,
                         "n_calls": int((p_oos != 0.5).sum()), "coverage": 1.0,
                         "accuracy": hits_all[s_all].mean(),
                         "balanced_accuracy": balanced_accuracy(p_oos, y, s_all),
                         "level_only_accuracy_same_games": hits_level[s_all].mean(),
                         "level_only_balanced_accuracy": balanced_accuracy(pl_oos, y, s_all),
                         "majority_accuracy_same_games": hits_major[s_all].mean()})

        def strong_row(label, season_label, called_mask, cutoff, sel):
            s = called_mask & scored & sel
            row = {**common, "evaluation": label, "season": season_label, "cutoff": cutoff,
                   "n_games": int(sel.sum()), "n_scored": int((scored & sel).sum()),
                   "n_calls": int((called_mask & sel).sum()),
                   "coverage": (called_mask & sel).sum() / max(sel.sum(), 1)}
            if not s.any():
                return row
            per_case = np.column_stack([hits_all[s], hits_level[s]]).astype(float)
            wc = np.ones_like(per_case)
            acc, lo, hi = cluster_bootstrap_diff(
                np.column_stack([per_case[:, 0], np.zeros(s.sum())]), clusters[s], 0, 1, boot_rng,
                weights_count=wc)
            lift, llo, lhi = cluster_bootstrap_diff(per_case, clusters[s], 0, 1, boot_rng, weights_count=wc)
            row.update({"accuracy": acc, "acc_ci_lo": lo, "acc_ci_hi": hi,
                        "balanced_accuracy": balanced_accuracy(p_oos, y, s) if (y[s] == 1).any()
                        and (y[s] == 0).any() else np.nan,
                        "share_below_calls": (p_oos[s] < 0.5).mean(),
                        "accuracy_above_calls": hits_all[s & (p_oos > 0.5)].mean()
                        if (s & (p_oos > 0.5)).any() else np.nan,
                        "accuracy_below_calls": hits_all[s & (p_oos < 0.5)].mean()
                        if (s & (p_oos < 0.5)).any() else np.nan,
                        "level_only_accuracy_same_games": hits_level[s].mean(),
                        "majority_accuracy_same_games": hits_major[s].mean(),
                        "lift_vs_level_only": lift, "lift_ci_lo": llo, "lift_ci_hi": lhi})
            return row

        everywhere = np.ones(n_all, dtype=bool)
        pooled = strong_row("strong_lean_loso", "pooled",
                            strong, np.nan if None in fold_cut.values() else
                            float(np.median(list(fold_cut.values()))), everywhere)
        out_rows.append(pooled)
        for held in season_list:
            out_rows.append(strong_row("strong_lean_loso", held, strong, fold_cut[held], seasons == held))

        feasible = None not in fold_cut.values()
        ship_cut = float(np.median(list(fold_cut.values()))) if feasible else None
        if feasible:
            called_ship, _, _ = call_stats(p_oos, y, scored, ship_cut)
            out_rows.append(strong_row("shipped_cutoff_on_loso", "pooled", called_ship, ship_cut, everywhere))
        ok = (feasible and pooled.get("accuracy", 0) >= SHIP_ACC and pooled["coverage"] >= MIN_COVERAGE
              and pooled.get("lift_ci_lo", -1) > 0)
        for r in out_rows:
            if r["stat"] == stat:
                r["shipped"] = bool(ok)
        if ok:
            full = fit_logistic(X[scored], y[scored])
            shipped[stat] = {
                "features": names,
                "mean": [float(v) for v in full["mean"]],
                "scale": [float(v) for v in full["scale"]],
                "coef": [float(v) for v in full["coef"]],
                "intercept": full["intercept"],
                "threshold": ship_cut,
                "fold_thresholds": fold_cut,
                "oos_accuracy": float(pooled["accuracy"]),
                "oos_accuracy_ci": [float(pooled["acc_ci_lo"]), float(pooled["acc_ci_hi"])],
                "oos_coverage": float(pooled["coverage"]),
                "n": int(pooled["n_calls"]),
                "n_games": int(n_all),
                "base_rate_above": float(base_rate),
                "all_calls_accuracy": float(hits_all[s_all].mean()),
                "all_calls_balanced_accuracy": float(balanced_accuracy(p_oos, y, s_all)),
            }
        print(f"{stat}: base rate above {base_rate:.3f}; fold cutoffs {fold_cut}; "
              f"{'SHIPS' if ok else 'no lean'}")

    res = pd.DataFrame(out_rows)
    res.to_csv(OUTPUT_PATH, index=False)
    with open(MODELS_PATH, "w") as f:
        json.dump({"generated_by": "lean_model_sweep.py",
                   "rule": (f"LOSO; nested fold cutoff = smallest |p-0.5| with training accuracy >= "
                            f"{TARGET_TRAIN_ACC} and coverage >= {MIN_COVERAGE}; shipped threshold = "
                            f"median of fold cutoffs; coefficients refit on all seasons; ship if pooled "
                            f"held-out accuracy >= {SHIP_ACC}, coverage >= {MIN_COVERAGE}, lift over "
                            f"level-only CI > 0"),
                   "models": shipped}, f, indent=1, sort_keys=True)
    # engine/lean.py must reproduce the refit models from the file just written
    reloaded = lean._load_models(MODELS_PATH)
    rng = np.random.default_rng(SEED)
    worst = 0.0
    for stat, m in shipped.items():
        full = {"mean": np.array(m["mean"]), "scale": np.array(m["scale"]),
                "intercept": m["intercept"], "coef": np.array(m["coef"])}
        for i in rng.choice(n_all, size=200, replace=False):
            feats = {n: data.at[i, f"{stat}:{n}"] for n in m["features"]}
            want = float(predict(full, np.array([[feats[n] for n in m["features"]]]))[0])
            worst = max(worst, abs(lean.probability_above(reloaded[stat], feats) - want))
    print(f"  engine.lean.probability_above vs this script on 200 cases per shipped stat: max diff {worst:.1e}")
    if worst > 1e-9:
        raise AssertionError("engine/lean.py out of sync with lean_models.json")

    pd.set_option("display.width", 250)
    cols = ["stat", "base_rate_above", "accuracy", "balanced_accuracy", "level_only_accuracy_same_games",
            "level_only_balanced_accuracy", "majority_accuracy_same_games"]
    print("\n=== All calls (LOSO, p vs 0.5) ===")
    print(res[res["evaluation"] == "all_calls_loso"][cols].round(4).to_string(index=False))
    cols = ["stat", "season", "cutoff", "n_calls", "coverage", "accuracy", "acc_ci_lo", "acc_ci_hi",
            "balanced_accuracy", "share_below_calls", "accuracy_above_calls", "accuracy_below_calls",
            "level_only_accuracy_same_games", "majority_accuracy_same_games", "lift_vs_level_only",
            "lift_ci_lo", "lift_ci_hi", "shipped"]
    print("\n=== Strong leans, held-out seasons (each with its own nested fold cutoff) ===")
    print(res[res["evaluation"] == "strong_lean_loso"][cols].round(4).to_string(index=False))
    print("\n=== Shipped cutoff (median of fold cutoffs) applied to the LOSO predictions (mildly optimistic) ===")
    print(res[res["evaluation"] == "shipped_cutoff_on_loso"][cols].round(4).to_string(index=False))
    print(f"\nShipped leans: {sorted(shipped) or 'none'}")
    for stat, m in shipped.items():
        print(f"  {stat}: threshold {m['threshold']}, coef " + ", ".join(
            f"{n}={c:+.3f}" for n, c in zip(m["features"], m["coef"])) + f", intercept {m['intercept']:+.3f}")
    print(f"\nWrote {len(res)} rows to {OUTPUT_PATH} and {len(shipped)} models to {MODELS_PATH}.")
    print("SCOPE: top-150 players, regular season, >= 10 prior games, features = engine/lean.py's "
          "(no venue/schedule) -- see module docstring.")


if __name__ == "__main__":
    main()
