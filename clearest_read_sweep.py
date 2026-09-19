"""Clearest read -- is a STRONGER strong lean right more often, and which
lean should the app put first?

The Single Player tab already shows a strong lean for each stat whose
model clears its threshold (engine/lean.py). This sweep asks whether
those leans can be ranked:

  1. Within strong leans, does accuracy rise with the model's
     confidence? Leans are bucketed by how far |p - 0.5| is past the
     stat's own cutoff.
  2. On games where two or more stats have a lean, is the lean picked
     by a ranking rule right more often than the others?

Everything is out of sample: leave-one-season-out predictions, each
held-out season with the nested fold cutoff lean_model_sweep.py uses.
The case set is exactly lean_model_sweep.py's (same build()).

Candidate ranking rules (each picks one lean per game):
  * prob      -- the highest called probability
  * margin    -- the largest |p - 0.5| minus that stat's cutoff
  * hist      -- the stat with the best pooled held-out accuracy
                 (ties broken by prob)

Tiers (written to engine/lean_tiers.json, read by engine/lean.py):
per stat, leans are bucketed by margin (BUCKET_EDGES); a bucket with
fewer than MIN_TIER_CALLS held-out calls is merged into the one below
it; a bucket is SHOWN only if its held-out accuracy is at least
SHOW_ACC (the same 60% bar a stat needs to ship at all). The live app
shows each lean with its own tier's accuracy instead of the stat's
pooled number, hides leans in unshown tiers, and puts the lean with
first, as the "clearest read", the shown lean whose grade has the best
record (ties: larger margin). On games with 2+ shown leans that pick
was right more often than the others; ranking by raw margin or
probability across stats did about as well. Tier labels follow the
margin buckets.

Caveat, stated in the app's methodology: the tier edges were fixed
before looking at results, but which tiers are shown was decided on
these same held-out results, so the shown accuracy is mildly
optimistic. The live app measures margin against the SHIPPED threshold
(median of the fold cutoffs), the sweep against each fold's cutoff.

Output: clearest_read_sweep_results.csv, engine/lean_tiers.json, and a
printed summary.

Usage:
    python3 clearest_read_sweep.py
"""

import json
import os
import sys

import numpy as np
import pandas as pd

import lean_model_sweep as lms
from engine import lean
from shrinkage_k_sweep import REPO_ROOT

OUTPUT_PATH = os.path.join(REPO_ROOT, "clearest_read_sweep_results.csv")
BUCKET_EDGES = [0.0, 0.02, 0.05, 0.10, 1.0]
TIER_LABELS = ["Borderline", "Solid", "Strong", "Very strong"]
MIN_TIER_CALLS = 300
SHOW_ACC = 0.60
TIERS_PATH = lean.TIERS_PATH
BOOT = 2000
SEED = 20260917


def loso_leans(data, stats):
    """One row per (case, stat) strong lean: held-out probability of the
    called direction, the fold cutoff, and whether it was right."""
    seasons = data["season"].values
    season_list = [s for s, _ in lms.SEASONS]
    out = []
    for stat in stats:
        names = lean.feature_names(stat)
        X = data[[f"{stat}:{n}" for n in names]].values
        diff = data[f"{stat}:actual"].values - data[f"{stat}:season_avg"].values
        scored = ~np.isclose(diff, 0.0, atol=1e-9)
        y = (diff > 0).astype(float)
        for held in season_list:
            tr, te = seasons != held, seasons == held
            m = lms.fit_logistic(X[tr & scored], y[tr & scored])
            p = lms.predict(m, X[te])
            a, b = [s for s in season_list if s != held]
            p_inner = np.full(len(data), np.nan)
            for fit_s, pred_s in [(a, b), (b, a)]:
                fs, ps = seasons == fit_s, seasons == pred_s
                p_inner[ps] = lms.predict(lms.fit_logistic(X[fs & scored], y[fs & scored]), X[ps])
            cut = lms.pick_cutoff(p_inner[tr], y[tr], scored[tr])
            if cut is None:
                continue
            idx = np.where(te)[0]
            called = (np.abs(p - 0.5) >= cut) & (p != 0.5) & scored[te]
            for i, pi in zip(idx[called], p[called]):
                out.append({
                    "case": i, "season": held, "player_id": data["player_id"].iat[i], "stat": stat,
                    "prob": max(pi, 1 - pi), "cutoff": cut, "margin": abs(pi - 0.5) - cut,
                    "hit": float((pi > 0.5) == (y[i] == 1)),
                    "season_avg": float(data[f"{stat}:season_avg"].iat[i]),
                    "mpg": float(data[f"{stat}:mpg"].iat[i]),
                })
    return pd.DataFrame(out)


def cluster_ci(df, value, rng):
    """95% CI of the mean of `value`, resampling player-seasons."""
    keys = (df["player_id"].astype(str) + "_" + df["season"]).values
    uniq, inv = np.unique(keys, return_inverse=True)
    sums = np.bincount(inv, weights=df[value].values)
    counts = np.bincount(inv)
    stats = []
    for _ in range(BOOT):
        pick = rng.integers(0, len(uniq), len(uniq))
        stats.append(sums[pick].sum() / counts[pick].sum())
    return np.percentile(stats, [2.5, 97.5])


def main():
    population = "--population" in sys.argv
    print("Building point-in-time cases (same set as lean_model_sweep.py) "
          + ("over the full point-in-time population ..." if population else "..."))
    data, _verify = lms.build_population() if population else lms.build()
    shipped = sorted(lean.LEAN_MODELS)
    print(f"{len(data)} cases; shipped lean stats: {shipped}\n")
    leans = loso_leans(data, shipped)
    # Same eligibility as the live app (engine/lean.py MIN_MPG / MIN_SHOWN_AVG).
    n_before = len(leans)
    leans = leans[(leans["mpg"] >= lean.MIN_MPG) & (leans["season_avg"] >= lean.MIN_SHOWN_AVG)].reset_index(drop=True)
    data = data[data["PTS:mpg"] >= lean.MIN_MPG]
    print(f"eligibility (>= {lean.MIN_MPG:g} mpg, season avg >= {lean.MIN_SHOWN_AVG:g}): "
          f"{len(leans)} of {n_before} leans, {len(data)} games\n")
    rng = np.random.default_rng(SEED)
    rows = []

    print("=== 1. Accuracy by how far past the cutoff the lean is (held out) ===")
    leans["bucket"] = pd.cut(leans["margin"], BUCKET_EDGES, right=False)
    for stat in shipped + ["ALL"]:
        sub = leans if stat == "ALL" else leans[leans["stat"] == stat]
        for bucket, g in sub.groupby("bucket", observed=True):
            lo, hi = cluster_ci(g, "hit", rng)
            rows.append({"analysis": "by_margin", "stat": stat, "group": str(bucket), "n": len(g),
                         "accuracy": g["hit"].mean(), "ci_lo": lo, "ci_hi": hi})
            print(f"  {stat:5s} margin {str(bucket):14s} n={len(g):6d} acc={g['hit'].mean():.3f} "
                  f"[{lo:.3f}, {hi:.3f}]")

    hist = leans.groupby("stat")["hit"].mean().to_dict()
    leans["hist"] = leans["stat"].map(hist)
    per_case = leans.groupby("case")
    n_cases_with = per_case.ngroups
    print(f"\n=== 2. Picking one lean per game ===")
    print(f"  games with >= 1 lean: {n_cases_with} of {len(data)} ({n_cases_with / len(data):.1%})")
    multi = leans[leans["case"].map(per_case.size()) >= 2]
    print(f"  games with >= 2 leans: {multi['case'].nunique()}")
    rows.append({"analysis": "coverage", "stat": "ALL", "group": ">=1 lean", "n": n_cases_with,
                 "accuracy": np.nan, "ci_lo": np.nan, "ci_hi": np.nan,
                 "share_of_games": n_cases_with / len(data)})
    all_leans_acc = leans["hit"].mean()
    for rule, keys in [("prob", ["prob"]), ("margin", ["margin"]), ("hist", ["hist", "prob"])]:
        for label, frame in [("all games with a lean", leans), ("games with 2+ leans", multi)]:
            top = frame.sort_values(keys, ascending=False, kind="mergesort").groupby("case").head(1)
            rest = frame.drop(top.index)
            lo, hi = cluster_ci(top, "hit", rng)
            rows.append({"analysis": f"pick_{rule}", "stat": "ALL", "group": label, "n": len(top),
                         "accuracy": top["hit"].mean(), "ci_lo": lo, "ci_hi": hi,
                         "others_accuracy": rest["hit"].mean() if len(rest) else np.nan})
            print(f"  rule={rule:6s} {label:22s} picked n={len(top):6d} acc={top['hit'].mean():.3f} "
                  f"[{lo:.3f}, {hi:.3f}]  others acc={rest['hit'].mean() if len(rest) else float('nan'):.3f}"
                  f"  (all leans {all_leans_acc:.3f})")
        top = leans.sort_values(keys, ascending=False, kind="mergesort").groupby("case").head(1)
        print("      picked stat mix: " + ", ".join(f"{k} {v:.0%}" for k, v in
                                                    top["stat"].value_counts(normalize=True).items()))
        for season, g in top.groupby("season"):
            rows.append({"analysis": f"pick_{rule}_by_season", "stat": "ALL", "group": season, "n": len(g),
                         "accuracy": g["hit"].mean(), "ci_lo": np.nan, "ci_hi": np.nan})
            print(f"      {season}: n={len(g)} acc={g['hit'].mean():.3f}")

    tiers = build_tiers(leans, shipped, rng)
    shown = leans[[tier_shown(tiers, r.stat, r.margin) for r in leans.itertuples()]].copy()
    shown["grade_acc"] = [lean.tier_for(r.stat, r.margin, tiers)["accuracy"] for r in shown.itertuples()]
    # Clearest read = the shown lean whose grade has the best record, then the
    # larger margin (engine/lean.py leans_for_game uses the same order).
    per_game = shown.sort_values(["grade_acc", "margin"], ascending=False, kind="mergesort").groupby("case").head(1)
    multi = shown[shown["case"].map(shown.groupby("case").size()) >= 2]
    multi_top = multi.sort_values(["grade_acc", "margin"], ascending=False, kind="mergesort").groupby("case").head(1)
    lo, hi = cluster_ci(shown, "hit", rng)
    plo, phi = cluster_ci(per_game, "hit", rng)
    summary = {
        "n_games": int(len(data)),
        "shown_calls": int(len(shown)), "shown_accuracy": float(shown["hit"].mean()),
        "shown_accuracy_ci": [float(lo), float(hi)],
        "games_with_a_read": int(len(per_game)), "games_with_a_read_share": float(len(per_game) / len(data)),
        "clearest_read_accuracy": float(per_game["hit"].mean()), "clearest_read_accuracy_ci": [float(plo), float(phi)],
        "clearest_read_by_season": {k: float(v) for k, v in per_game.groupby("season")["hit"].mean().items()},
        "multi_lean_games": int(len(multi_top)),
        "multi_lean_top_accuracy": float(multi_top["hit"].mean()),
        "multi_lean_rest_accuracy": float(multi.drop(multi_top.index)["hit"].mean()),
        "eligibility": {"min_mpg": lean.MIN_MPG, "min_season_avg": lean.MIN_SHOWN_AVG},
        "hidden_calls": int(len(leans) - len(shown)), "hidden_accuracy": float(leans.drop(shown.index)["hit"].mean()),
    }
    for stat in shipped:
        g = shown[shown["stat"] == stat]
        tiers[stat]["shown_calls"] = int(len(g))
        tiers[stat]["shown_accuracy"] = float(g["hit"].mean()) if len(g) else None
        tiers[stat]["shown_coverage"] = float(len(g) / len(data))
    print("\n=== 3. Shown tiers ===")
    for stat in shipped:
        t = tiers[stat]
        print(f"  {stat}: shown {t['shown_calls']} calls ({t['shown_coverage']:.1%} of games), "
              f"acc {t['shown_accuracy']:.3f}; tiers " + "; ".join(
                  f"{x['label']} >= {x['min_margin']:.2f}: {x['accuracy']:.3f} n={x['n']}"
                  f"{'' if x['shown'] else ' (hidden)'}" for x in t["tiers"]))
    print("  summary:", json.dumps(summary, indent=1))
    with open(TIERS_PATH, "w") as f:
        json.dump({"generated_by": "clearest_read_sweep.py",
                   "rule": (f"held-out (LOSO, nested fold cutoff) accuracy of strong leans bucketed by "
                            f"|p-0.5| minus the cutoff at {BUCKET_EDGES[:-1]}; buckets with < {MIN_TIER_CALLS} "
                            f"calls merged downward; shown if accuracy >= {SHOW_ACC}; clearest read = shown "
                            f"lean whose grade has the best record, then the largest margin; only players "
                            f"averaging >= {lean.MIN_MPG:g} min and stats averaging >= {lean.MIN_SHOWN_AVG:g}"),
                   "summary": summary, "stats": tiers}, f, indent=1, sort_keys=True)
    pd.DataFrame(rows).to_csv(OUTPUT_PATH, index=False)
    print(f"\nWrote {OUTPUT_PATH} and {TIERS_PATH}")


def build_tiers(leans, stats, rng):
    """{stat: {"tiers": [{label, min_margin, accuracy, ci, n, shown}, ...]}}
    in increasing margin order; the last tier is open-ended."""
    out = {}
    for stat in stats:
        sub = leans[leans["stat"] == stat]
        buckets = []
        for i, lo_edge in enumerate(BUCKET_EDGES[:-1]):
            g = sub[(sub["margin"] >= lo_edge) & (sub["margin"] < BUCKET_EDGES[i + 1])]
            buckets.append({"label": TIER_LABELS[i], "min_margin": lo_edge, "rows": g})
        merged = []
        for b in buckets:
            if merged and len(b["rows"]) < MIN_TIER_CALLS:
                merged[-1]["rows"] = pd.concat([merged[-1]["rows"], b["rows"]])
            else:
                merged.append(b)
        tiers = []
        for b in merged:
            g = b["rows"]
            lo, hi = cluster_ci(g, "hit", rng) if len(g) else (np.nan, np.nan)
            acc = float(g["hit"].mean()) if len(g) else 0.0
            tiers.append({"label": b["label"], "min_margin": b["min_margin"], "accuracy": acc,
                          "ci": [float(lo), float(hi)], "n": int(len(g)), "shown": bool(acc >= SHOW_ACC)})
        out[stat] = {"tiers": tiers}
    return out


def tier_shown(tiers, stat, margin):
    chosen = None
    for t in tiers[stat]["tiers"]:
        if margin >= t["min_margin"]:
            chosen = t
    return bool(chosen and chosen["shown"])


if __name__ == "__main__":
    main()
