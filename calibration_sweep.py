"""How wide is the range really, and what is the chance a stat clears a
line? -- fits and scores candidate predictive distributions for one
player-game, out of sample.

WHY THIS EXISTS
The app shows "Likely range = prediction +/- 0.6 x the player's own
game-to-game standard deviation". Measured on the 29,910 point-in-time
backtest games, that band contains the real result only ~42% of the
time (PTS 42.4%, REB 42.6%, AST 42.3%, FG3A 45.8%) -- the words promise
far more than the arithmetic delivers, and engine/tracker.py scores a
saved prediction as a "hit" against exactly this band, so the tracker's
hit rate is capped near 40% however good the projection is.

A range is just one readout of a predictive DISTRIBUTION. The same
distribution answers the question a bettor actually asks -- "what are
the chances he clears 27.5?" -- so this sweep fits distributions, not
intervals, and scores them on:
  * coverage   -- does the nominal 50%/80% interval contain the result
                  50%/80% of the time?
  * Brier / log loss for P(actual > line) over a grid of lines
  * reliability -- bucket the predicted probabilities, compare with
                  what actually happened (the honest calibration check;
                  no bookmaker line needed)

CANDIDATES (mu = the app's prediction for that game, s = the player's
point-in-time standard deviation, n = his prior games)
  shipped   normal(mu, s), the band read at +/- 0.6 s
  normal_k  normal(mu, k*s), k fitted per stat on the training seasons
  empirical pooled empirical quantiles of z = (actual - mu)/s
  nb_pool   negative binomial, mean mu, Var = mu + mu^2/r, r per stat
  nb_std    negative binomial, Var = c * s^2, c per stat
  nb_shrunk negative binomial, per-player 1/r shrunk toward the pooled
            value by n/(n + K)
Fitting is leave-one-season-out: parameters come from the other two
seasons, every score is on the held-out one.

Counts are discrete and lines are half-points, so P(over) is computed
as 1 - CDF(floor(line)) with no tie handling needed.

Usage:
    python3 calibration_sweep.py           # writes engine/stat_distribution.json
    python3 calibration_sweep.py --dry-run # scores only, writes nothing
"""

import json
import math
import os
import sys

import numpy as np
import pandas as pd

from engine.stat_columns import STAT_COLUMNS

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
BACKTEST_PATH = os.path.join(REPO_ROOT, "backtest_results.csv")
POPULATION_PATH = os.path.join(REPO_ROOT, "backtest_population.csv")
OUTPUT_PATH = os.path.join(REPO_ROOT, "engine", "stat_distribution.json")
RESULTS_PATH = os.path.join(REPO_ROOT, "calibration_sweep_results.csv")
STATS = [col for col, _ in STAT_COLUMNS]
MIN_PRIOR_GAMES = 5
LINE_OFFSETS = [-3, -2, -1, 0, 1, 2, 3]   # half-point lines around the prediction
NOMINAL = [0.5, 0.8]
SHRINK_K = 20.0
LOGLOSS_TIE = 0.004
RELIABILITY_BINS = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]


# ---- data -----------------------------------------------------------------
def load_cases(population=False):
    """One row per backtest game per stat context: the app's prediction
    (mu), the player's point-in-time mean/std/n from his prior games in
    that season, and what he actually did.

    population=True uses backtest_population.csv -- every player, with
    eligibility decided as of the game date (build_backtest_population.py)
    -- instead of the old top-150-by-end-of-season-minutes set, which
    already carries the prior std and game count."""
    if population:
        cases = pd.read_csv(POPULATION_PATH, dtype={"player_id": str, "game_id": str})
        return cases[cases["n_prior"] >= MIN_PRIOR_GAMES].reset_index(drop=True)
    bt = pd.read_csv(BACKTEST_PATH, dtype={"player_id": str, "game_id": str})
    frames = []
    for (pid, season), _g in bt.groupby(["player_id", "season"]):
        path = os.path.join(REPO_ROOT, "data_cache", f"gamelog_{pid}_{season}.json")
        with open(path) as f:
            df = pd.DataFrame(json.load(f)["data"])
        df = df[df["Game_ID"].astype(str).str.startswith("0022")].copy()
        df["_d"] = pd.to_datetime(df["GAME_DATE"])
        df = df.sort_values("_d", kind="mergesort")
        out = {"gid": df["Game_ID"].astype(str).str.zfill(10).values,
               "n_prior": np.arange(len(df), dtype=float)}
        for col in STATS:
            x = pd.to_numeric(df[col], errors="coerce")
            out[f"{col}_std_prior"] = x.shift(1).expanding().std().values
        frames.append(pd.DataFrame(out).assign(player_id=pid, season=season))
    prior = pd.concat(frames, ignore_index=True)
    bt["gid"] = bt["game_id"].astype(str).str.zfill(10)
    cases = bt.merge(prior, on=["player_id", "season", "gid"], how="left")
    return cases[cases["n_prior"] >= MIN_PRIOR_GAMES].reset_index(drop=True)


# ---- negative binomial (no scipy: engine/ must stay dependency-free) ------
def nb_r_from_var(mu, var):
    """r of a negative binomial with this mean and variance; large r
    (i.e. Poisson-like) when the variance isn't above the mean."""
    var = np.maximum(var, mu * 1.000001 + 1e-9)
    return mu * mu / (var - mu)


def nb_sf(k, mu, r):
    """P(X > k) for integer k >= -1, vectorised over mu/r via the
    regularised incomplete beta function (betainc without scipy)."""
    mu = np.maximum(np.asarray(mu, dtype=float), 1e-9)
    r = np.maximum(np.asarray(r, dtype=float), 1e-9)
    k = np.asarray(k, dtype=float)
    p = r / (r + mu)                      # P(success); mean = r(1-p)/p
    out = np.empty_like(mu, dtype=float)
    flat = np.nditer([k, r, p, out], op_flags=[["readonly"]] * 3 + [["writeonly"]])
    for kk, rr, pp, o in flat:
        if kk < 0:
            o[...] = 1.0
        else:
            o[...] = _betainc(float(kk) + 1.0, float(rr), 1.0 - float(pp))
    return np.clip(out, 0.0, 1.0)


def _betainc(a, b, x):
    """Regularised incomplete beta I_x(a, b) by continued fraction
    (Numerical Recipes betacf), enough for our small integer a."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + b * math.log1p(-x) + a * math.log(x)
    ) * _betacf(b, a, 1.0 - x) / b


def _betacf(a, b, x, itmax=300, eps=1e-12):
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > 1e-30 else 1e-30)
    h = d
    for m in range(1, itmax + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > 1e-30 else 1e-30)
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > 1e-30 else 1e-30)
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def nb_quantile(q, mu, r, hi=200):
    """The q-quantile with a continuity correction: the discrete CDF is
    read at k + 0.5 and interpolated between whole numbers. Without this
    a "50% range" on a stat that only takes values 0,1,2 lands at 80%+
    coverage; with it the interval lands much closer to its label."""
    out = np.full(mu.shape, float(hi))
    done = np.zeros(mu.shape, dtype=bool)
    prev_cdf = np.zeros(mu.shape)
    for k in range(hi):
        cdf = 1.0 - nb_sf(np.full(mu.shape, k), mu, r)
        hit = (~done) & (cdf >= q)
        if hit.any():
            # linear interpolation between (k-0.5, prev_cdf) and (k+0.5, cdf)
            span = np.maximum(cdf - prev_cdf, 1e-12)
            out[hit] = (k - 0.5 + (q - prev_cdf[hit]) / span[hit])
            done |= hit
        if done.all():
            break
        prev_cdf = cdf
    return np.maximum(out, 0.0)


# ---- candidate distributions ---------------------------------------------
NORMAL_Z = {0.5: 0.6744897501960817, 0.8: 1.2815515655446004}


class Candidate:
    """fit(train) -> params; sf(x, params) -> P(actual > x); interval(p)."""

    def __init__(self, name):
        self.name = name

    def fit(self, mu, s, n, y):
        return {}

    def sf(self, line, mu, s, n, params):
        raise NotImplementedError

    def interval(self, nominal, mu, s, n, params):
        raise NotImplementedError


class NormalK(Candidate):
    """normal(mu, k*s); k = 1 recovers the textbook normal, k = 0.6 is
    what the app ships today."""

    def __init__(self, name, fixed_k=None):
        super().__init__(name)
        self.fixed_k = fixed_k

    def fit(self, mu, s, n, y):
        if self.fixed_k is not None:
            return {"k": self.fixed_k}
        z = (y - mu) / np.maximum(s, 1e-6)
        return {"k": float(np.sqrt(np.mean(z ** 2)))}

    def sf(self, line, mu, s, n, params):
        sd = np.maximum(params["k"] * s, 1e-6)
        return 0.5 * np.array([math.erfc((l - m) / (sd_i * math.sqrt(2)))
                               for l, m, sd_i in zip(np.ravel(line), np.ravel(mu), np.ravel(sd))])

    def interval(self, nominal, mu, s, n, params):
        z = NORMAL_Z[nominal]
        sd = params["k"] * s
        return np.maximum(mu - z * sd, 0.0), mu + z * sd


class Empirical(Candidate):
    """Pooled empirical distribution of z = (actual - mu) / s."""

    def fit(self, mu, s, n, y):
        z = (y - mu) / np.maximum(s, 1e-6)
        return {"z": np.sort(z[np.isfinite(z)])}

    def sf(self, line, mu, s, n, params):
        z = (np.ravel(line) - np.ravel(mu)) / np.maximum(np.ravel(s), 1e-6)
        idx = np.searchsorted(params["z"], z, side="right")
        return 1.0 - idx / len(params["z"])

    def interval(self, nominal, mu, s, n, params):
        lo_q, hi_q = (1 - nominal) / 2, 1 - (1 - nominal) / 2
        lo_z, hi_z = np.quantile(params["z"], [lo_q, hi_q])
        return np.maximum(mu + lo_z * s, 0.0), mu + hi_z * s


class NegBin(Candidate):
    """Negative binomial around mu. variance_mode:
       "pool"   Var = mu + mu^2/r, one r per stat
       "std"    Var = c * s^2
       "shrunk" per-player 1/r from his own s and mu, shrunk to pooled
    """

    def __init__(self, name, variance_mode):
        super().__init__(name)
        self.mode = variance_mode

    def fit(self, mu, s, n, y):
        resid_var = float(np.mean((y - mu) ** 2))
        mean_mu = float(np.mean(mu))
        if self.mode == "std":
            c = resid_var / max(float(np.mean(s ** 2)), 1e-9)
            return {"c": c}
        # method of moments on the pooled residual: Var = mu + mu^2 * alpha
        alpha = max((resid_var - mean_mu) / max(float(np.mean(mu ** 2)), 1e-9), 1e-6)
        return {"alpha": alpha}

    def _r(self, mu, s, n, params):
        if self.mode == "std":
            var = params["c"] * np.maximum(s, 1e-6) ** 2
        elif self.mode == "pool":
            var = mu + params["alpha"] * mu ** 2
        else:
            own = np.maximum((np.maximum(s, 1e-6) ** 2 - mu) / np.maximum(mu ** 2, 1e-9), 0.0)
            w = n / (n + SHRINK_K)
            alpha = w * own + (1 - w) * params["alpha"]
            var = mu + np.maximum(alpha, 1e-6) * mu ** 2
        return nb_r_from_var(np.maximum(mu, 1e-6), var)

    def sf(self, line, mu, s, n, params):
        r = self._r(mu, s, n, params)
        return nb_sf(np.floor(line), np.maximum(mu, 1e-6), r)

    def interval(self, nominal, mu, s, n, params):
        r = self._r(mu, s, n, params)
        lo_q, hi_q = (1 - nominal) / 2, 1 - (1 - nominal) / 2
        return (nb_quantile(lo_q, np.maximum(mu, 1e-6), r),
                nb_quantile(hi_q, np.maximum(mu, 1e-6), r))


CANDIDATES = [
    NormalK("shipped_0.6", fixed_k=0.6),
    NormalK("normal_k"),
    Empirical("empirical_z"),
    NegBin("nb_pool", "pool"),
    NegBin("nb_std", "std"),
    NegBin("nb_shrunk", "shrunk"),
]


# ---- scoring --------------------------------------------------------------
def line_grid(mu):
    """Half-point lines around the prediction, like a book's."""
    base = np.floor(mu) + 0.5
    return [np.maximum(base + off, 0.5) for off in LINE_OFFSETS]


def score(cand, params, mu, s, n, y):
    out = {}
    for nominal in NOMINAL:
        lo, hi = cand.interval(nominal, mu, s, n, params)
        out[f"coverage_{int(nominal * 100)}"] = float(np.mean((y >= lo) & (y <= hi)))
        out[f"width_{int(nominal * 100)}"] = float(np.mean(hi - lo))
    briers, lls, probs, hits = [], [], [], []
    for line in line_grid(mu):
        p = np.clip(cand.sf(line, mu, s, n, params), 1e-6, 1 - 1e-6)
        hit = (y > line).astype(float)
        briers.append((p - hit) ** 2)
        lls.append(-(hit * np.log(p) + (1 - hit) * np.log(1 - p)))
        probs.append(p)
        hits.append(hit)
    out["brier"] = float(np.mean(np.concatenate(briers)))
    out["log_loss"] = float(np.mean(np.concatenate(lls)))
    p_all, h_all = np.concatenate(probs), np.concatenate(hits)
    # calibration error: mean |predicted - actual| over probability bins
    idx = np.digitize(p_all, RELIABILITY_BINS[1:-1])
    ece, rel = 0.0, []
    for b in range(len(RELIABILITY_BINS) - 1):
        m = idx == b
        if not m.any():
            continue
        gap = abs(float(p_all[m].mean()) - float(h_all[m].mean()))
        ece += gap * m.mean()
        rel.append((RELIABILITY_BINS[b], int(m.sum()), float(p_all[m].mean()), float(h_all[m].mean())))
    out["ece"] = float(ece)
    out["_reliability"] = rel
    return out


def main():
    dry_run = "--dry-run" in sys.argv
    population = "--population" in sys.argv
    cases = load_cases(population=population)
    print(("point-in-time population (every player)" if population
           else "old set (top 150 by end-of-season minutes)"))
    seasons = sorted(cases["season"].unique())
    print(f"{len(cases)} point-in-time games with >= {MIN_PRIOR_GAMES} prior games; seasons {seasons}\n")

    rows, chosen = [], {}
    for col in STATS:
        mu_all = cases[f"{col}_predicted"].values.astype(float)
        y_all = cases[f"{col}_actual"].values.astype(float)
        s_all = cases[f"{col}_std_prior"].values.astype(float)
        n_all = cases["n_prior"].values.astype(float)
        ok = np.isfinite(mu_all) & np.isfinite(y_all) & np.isfinite(s_all) & (s_all > 0)
        season_all = cases["season"].values
        print(f"=== {col} ({int(ok.sum())} games) ===")
        for cand in CANDIDATES:
            per_season = {}
            for held in seasons:
                tr = ok & (season_all != held)
                te = ok & (season_all == held)
                params = cand.fit(mu_all[tr], s_all[tr], n_all[tr], y_all[tr])
                per_season[held] = score(cand, params, mu_all[te], s_all[te], n_all[te], y_all[te])
            agg = {k: float(np.mean([per_season[s][k] for s in seasons]))
                   for k in per_season[seasons[0]] if not k.startswith("_")}
            rows.append({"stat": col, "model": cand.name, **agg,
                         **{f"{k}_{s}": per_season[s][k] for s in seasons for k in ("coverage_80", "brier")}})
            print(f"  {cand.name:12s} cover50 {agg['coverage_50']:.3f}  cover80 {agg['coverage_80']:.3f}"
                  f"  width80 {agg['width_80']:5.1f}  brier {agg['brier']:.4f}"
                  f"  logloss {agg['log_loss']:.4f}  ece {agg['ece']:.4f}")
        # Ship the candidate with the lowest held-out log loss. Tie-break
        # (within LOGLOSS_TIE) on how close its 50%/80% intervals land to
        # their labels, since the range is a headline number in the app.
        stat_rows = [r for r in rows if r["stat"] == col]
        floor_ll = min(r["log_loss"] for r in stat_rows)
        contenders = [r for r in stat_rows if r["log_loss"] <= floor_ll + LOGLOSS_TIE
                      and r["model"] != "shipped_0.6"]
        best = min(contenders, key=lambda r: abs(r["coverage_50"] - 0.5) + abs(r["coverage_80"] - 0.8))
        chosen[col] = best["model"]
        print(f"  -> chosen: {best['model']} (log loss {best['log_loss']:.4f} vs best "
              f"{floor_ll:.4f}; coverage {best['coverage_50']:.3f}/{best['coverage_80']:.3f})\n")

    pd.DataFrame(rows).to_csv(RESULTS_PATH, index=False)
    print(f"Wrote {RESULTS_PATH}")
    print("chosen:", chosen)

    if dry_run:
        return
    # Refit each stat's chosen family on ALL seasons for shipping. The
    # scores stored alongside stay the leave-one-season-out ones.
    payload = {"generated_by": "calibration_sweep.py",
               "rule": ("per stat, the candidate with the lowest held-out log loss (ties within "
                        f"{LOGLOSS_TIE} broken on interval coverage); parameters refitted on all "
                        "three seasons, scores are leave-one-season-out"),
               "min_prior_games": MIN_PRIOR_GAMES,
               "shrink_k": SHRINK_K,
               "line_offsets": LINE_OFFSETS,
               "stats": {}}
    for col in STATS:
        cand = next(c for c in CANDIDATES if c.name == chosen[col])
        mu_all = cases[f"{col}_predicted"].values.astype(float)
        y_all = cases[f"{col}_actual"].values.astype(float)
        s_all = cases[f"{col}_std_prior"].values.astype(float)
        n_all = cases["n_prior"].values.astype(float)
        ok = np.isfinite(mu_all) & np.isfinite(y_all) & np.isfinite(s_all) & (s_all > 0)
        params = cand.fit(mu_all[ok], s_all[ok], n_all[ok], y_all[ok])
        entry = {"model": chosen[col], "n_fit": int(ok.sum())}
        for k, v in params.items():
            # the empirical model's parameter is the z distribution itself:
            # store it as percentiles, which is all the engine needs
            entry[k] = ([round(float(x), 4) for x in np.quantile(v, np.arange(0, 1001) / 1000.0)]
                        if k == "z" else float(v))
        loso = [r for r in rows if r["stat"] == col and r["model"] == chosen[col]][0]
        entry["held_out"] = {k: round(loso[k], 4) for k in
                             ("coverage_50", "coverage_80", "width_50", "width_80",
                              "brier", "log_loss", "ece")}
        entry["held_out"]["baseline_shipped_0.6"] = {
            k: round([r for r in rows if r["stat"] == col and r["model"] == "shipped_0.6"][0][k], 4)
            for k in ("coverage_50", "coverage_80", "brier", "log_loss", "ece")}
        payload["stats"][col] = entry
    with open(OUTPUT_PATH, "w") as f:
        json.dump(payload, f, indent=1, sort_keys=True)
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
