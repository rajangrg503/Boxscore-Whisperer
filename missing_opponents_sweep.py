"""
Point-in-time backtest of the "missing opponent players" layer
(engine/adjustments/missing_players.py get_opponent_missing_adjustment)
-- does its uniform x(1 + w/240 * 0.35) bump beat no adjustment, which
strength is best, does the net-rating quality weighting earn its
keep, and would one coefficient per stat do better out of sample?

WHY THIS EXISTS: the layer has never been backtested. For each missing
opponent it takes season MPG (engine/career_stats.resolve_season_mpg on
cached career_stats_{pid}) times a quality multiplier
max(0.2, 1 + E_NET_RATING/10), sums that to w, and multiplies EVERY
stat of EVERY player facing that team by 1 + (w/240) * 0.35. Live:
SGA out -> x1.130, SGA + Holmgren out -> x1.242. The Single Player tool
uses it, and on this branch Full Matchup applies it to a whole roster at
once, so a wrong strength moves ~10 projections per click. The 0.35 and
the uniform shape (an absent opposing big should matter more for REB /
OREB / BLK than for FG3A) were picked by judgment.

WHAT IT TESTS -- production's formula, fed point-in-time inputs:
  * Reference = point_in_time_baseline x get_defense_adjustment(point-
    in-time rating), i.e. build_backtest_row(); cross-checked against
    backtest_results.csv's *_predicted (shrinkage_k_sweep helper).
  * Two MPG sources per absent opponent:
      - "pit": his season-to-date MPG from box scores strictly before
        the target date, all teams pooled (the TOT-row analogue;
        production uses FULL-season totals, which a backtest can't).
      - "prod_prev": production's exact call, resolve_season_mpg(
        career_df, previous_season), on the cached career_stats_{pid}
        truncated to seasons <= previous_season. The app passes
        PREVIOUS_SEASON today (app.py Single Player and Full Matchup),
        so this is what users actually get, lagged one season. The
        truncation matters: resolve_season_mpg falls back to
        career_df.tail(1) when the season row is missing, which on the
        untruncated cache is a LATER season (a leak); the number of
        entries where that fallback fires is printed. No cached career
        file (production would try a live call) or no usable row ->
        the player is skipped, as production's except/None paths do.
  * Net rating: only player_estimated_metrics for the season strictly
    before the target season (point-in-time safe). Cached:
    2023-24, 2024-25, 2025-26 -> 2023-24 targets have NO quality signal
    (2022-23 not cached) and fall back to raw MPG, exactly as
    production does for a player missing from the metrics frame.
    Production would additionally try PREVIOUS_SEASON (2025-26) there,
    a future leak for a backtest, so that fallback is not replayed.
  * A sample of cases is re-run through the REAL
    get_opponent_missing_adjustment (get_player_id / cached_or_live /
    time.sleep monkeypatched to the point-in-time frames) and asserted
    equal to the prod_prev x quality x 0.35 variant.

CASE SELECTION (read before quoting a number):
  * run_backtest.py's case set (3 seasons, top-150 by minutes, regular
    season, >= MIN_BASELINE_GAMES prior games, opponent resolvable).
  * "Key player" of the OPPONENT team T before date d: the same
    definition as out_redistribution_sweep.py -- >= 10 games for T this
    season before d, averaging >= 24.0 minutes in them.
  * TREATED: >= 1 key opponent did not play in the target game (absent
    from the box score or 0 minutes) AND played for T again later that
    season. The "played again later" filter uses future information for
    CASE SELECTION only (it stands in for a user marking an injured /
    resting star out, not a traded one); it never enters a prediction.
  * This is the "user marks exactly the absent key opponents" scenario.
    Absent non-key opponents are ignored; the player's own missing
    teammates are not modelled (confounder, same for every variant).

VARIANTS: reference; mpg_source in {pit, prod_prev} x quality weighting
{on, off} x strength s in STRENGTHS (0.35 = production); and, per
(mpg_source, quality), leave-one-season-out fits of (a) one uniform s
and (b) one s per stat, multiplier_stat = 1 + (w/240) * s_stat, each
chosen on two seasons from a fine grid FIT_GRID and scored on the third.
Controls, because MAE rewards predicting the MEDIAN and these stats are
right-skewed (so "scale everything down" wins MAE whatever w is):
(c) a w-free per-stat constant multiplier (placebo), (d) constant x
(1 + x * s_stat), and (e) the mean-unbiased strength per stat from OLS
actual = ref * (c + d * x) over all cases (x = 0 untreated), s = d/c,
with a cluster-bootstrap CI.
"production" = prod_prev + quality + 0.35 (the app today).

METRICS: out_redistribution_sweep.py conventions -- per stat MAE, mean
signed error (bias, pred - actual), pooled rel-MAE = mean over the 9
stats of |err| / reference MAE on the same subset (< 1 beats no
adjustment), paired cluster bootstrap resampling whole player-seasons.
The "best uniform" config is picked in-sample over the grid (s > 0).
Subsets: all_treated, single_out, multi_out, prod_applied, per season;
plus untreated (reference only) for the bias comparison.

LIMITS: cached box scores start in 2023-24, so pit MPG is same-season
only; name resolution (get_player_id) is bypassed -- ids are known
here, the app resolves typed names and can pick the wrong namesake;
minutes of the target game (and the opponent's other lineup changes)
are not used; the defense rating already partly reflects games the
absent players missed.

Usage:
    python3 missing_opponents_sweep.py
Writes missing_opponents_sweep_results.csv (tidy: row_type in
{eval, fit, mean_effect}, subset, variant, mpg_source, quality,
strength, fold, stat, n_cases, n_moved, mae, bias,
rel_mae_vs_reference, fitted_s, ci_lo, ci_hi).
"""

import os
import time
from functools import lru_cache

import numpy as np
import pandas as pd

# Importing out_redistribution_sweep imports shrinkage_k_sweep, which
# installs its offline import stubs and memoises bpit._load_df_cache.
import shrinkage_k_sweep as sks  # noqa: E402
from out_redistribution_sweep import SeasonIndex, STATS  # noqa: E402
from shrinkage_k_sweep import (  # noqa: E402
    REPO_ROOT,
    _memo_load,
    cluster_bootstrap_diff,
    defense_for,
    validate_season_only_against_published,
)
import engine.adjustments.missing_players as mp_mod  # noqa: E402
from engine.backtest_point_in_time import (  # noqa: E402
    MIN_BASELINE_GAMES,
    _is_regular_season_game_id,
    get_point_in_time_opponent_defense,
    point_in_time_baseline,
)
from engine.career_stats import resolve_season_mpg  # noqa: E402
from engine.team_ids import TEAM_ID_BY_ABBR  # noqa: E402
from run_backtest import SEASONS, load_fixed_player_list  # noqa: E402

OUTPUT_PATH = os.path.join(REPO_ROOT, "missing_opponents_sweep_results.csv")
PRODUCTION_STRENGTH = 0.35
QUALITY_SCALE = 10.0            # mirrors missing_players.py's local constants
MIN_QUALITY_MULTIPLIER = 0.2
STRENGTHS = [0.0, 0.05, 0.10, 0.15, 0.20, 0.35, 0.50]
FIT_GRID = np.round(np.arange(-3.0, 1.0 + 1e-9, 0.01), 2)
CONST_GRID = np.round(np.arange(0.70, 1.10 + 1e-9, 0.01), 2)   # placebo: w-free per-stat multiplier
JOINT_S_GRID = np.round(np.arange(-3.0, 1.0 + 1e-9, 0.02), 2)
MPG_SOURCES = ["pit", "prod_prev"]
QUALITY = [True, False]
PRODUCTION = ("prod_prev", True, PRODUCTION_STRENGTH)
VERIFY_N = 300
SEED = 20260917


def prev_season(season):
    return sks._prior_season(season, 1)


def variant_name(src, q, s):
    return f"{src}_{'q' if q else 'noq'}_s{s:.2f}"


@lru_cache(maxsize=None)
def metrics_for(season):
    """{player_id(int): E_NET_RATING} for a cached season, else None."""
    df, _ = _memo_load(f"player_estimated_metrics_{season}")
    if df is None or df.empty:
        return None
    return dict(zip(df["PLAYER_ID"], df["E_NET_RATING"]))


@lru_cache(maxsize=None)
def career_frame(pid, max_season):
    """Cached career stats truncated to SEASON_ID <= max_season (None if
    not cached), plus the untruncated frame for the leak diagnostic."""
    df, _ = _memo_load(f"career_stats_{pid}")
    if df is None:
        return None, None
    return df[df["SEASON_ID"] <= max_season].reset_index(drop=True), df


@lru_cache(maxsize=None)
def production_mpg(pid, season):
    """Production's resolve_season_mpg(career_df, season) on the
    point-in-time frame. Returns (mpg or nan, status)."""
    trunc, full = career_frame(pid, season)
    if trunc is None:
        return np.nan, "no_career_cache"
    has_row = (trunc["SEASON_ID"] == season).any()
    try:
        mpg, _reason = resolve_season_mpg(trunc, season)
    except Exception:
        return np.nan, "no_rows"            # production's except: continue
    if mpg is None:
        return np.nan, "zero_gp"
    if not has_row:
        # tail(1) fallback; on the untruncated cache it would read a later season
        leak_mpg, _ = resolve_season_mpg(full, season)
        return float(mpg), "fallback_earlier" if leak_mpg == mpg else "fallback_earlier_leak_avoided"
    return float(mpg), "ok"


class OppIndex(SeasonIndex):
    """SeasonIndex plus season-to-date minutes per person (all teams)."""

    def __init__(self, season):
        super().__init__(season)
        self.minutes_by_person = {}
        for p, df in self.app.groupby("person_id"):
            df = df.sort_values("date")
            self.minutes_by_person[p] = (df["date"].values, np.cumsum(df["minutes"].values))
        self.teams_in_game = self.app.groupby("game_id")["team_id"].agg(set).to_dict()

    def mpg_before(self, person_id, date):
        dates, cum = self.minutes_by_person[person_id]
        n = int(np.searchsorted(dates, np.datetime64(date), side="left"))
        return cum[n - 1] / n if n else np.nan, n


def build():
    cases, entries = [], []
    entry_cache = {}
    for season, season_start in SEASONS:
        idx = OppIndex(season)
        prev = prev_season(season)
        nr_map = metrics_for(prev)
        print(f"  {season}: estimated metrics for {prev} "
              f"{'cached (' + str(len(nr_map)) + ' players)' if nr_map else 'NOT cached -> raw MPG only'}")
        players = load_fixed_player_list(season)
        t0 = time.time()
        n_season = n_opp_missing_in_box = 0
        for player_id, _name in players:
            df, _ = _memo_load(f"gamelog_{player_id}_{season}")
            if df is None:
                raise FileNotFoundError(f"gamelog_{player_id}_{season}.json missing")
            parsed = pd.to_datetime(df["GAME_DATE"])
            for i, game_row in df.iterrows():
                if not _is_regular_season_game_id(game_row["Game_ID"]):
                    continue
                game_date = parsed[i].date()
                season_stats, n_games = point_in_time_baseline(player_id, season, game_date)
                if n_games < MIN_BASELINE_GAMES:
                    continue
                opponent_abbr = game_row["MATCHUP"].split()[-1]
                opp = TEAM_ID_BY_ABBR.get(opponent_abbr)
                if opp is None:
                    continue
                n_season += 1
                game_id = str(game_row["Game_ID"])
                case = {
                    "player_id": player_id, "season": season, "game_id": game_id,
                    "base": [season_stats[c][0] for c in STATS],
                    "actual": [game_row[c] for c in STATS],
                    "defense_lookup": get_point_in_time_opponent_defense(
                        season, season_start, opp, game_date),
                    "n_absent": 0, "absent": (),
                }
                ts = pd.Timestamp(game_date)
                if opp not in idx.teams_in_game.get(game_id, ()):
                    n_opp_missing_in_box += 1
                elif opp in idx.by_team:
                    absent = sorted(a for a in idx.key_teammates(opp, ts)
                                    if a != player_id
                                    and a not in idx.played_in.get(game_id, ())
                                    and idx.last_date.get((opp, a), ts) > ts)
                    if absent:
                        case["n_absent"] = len(absent)
                        case["absent"] = tuple(absent)
                        for a in absent:
                            key = (season, a, ts)
                            if key not in entry_cache:
                                pit, n_pit = idx.mpg_before(a, ts)
                                pm, status = production_mpg(int(a), prev)
                                nr = nr_map.get(int(a), np.nan) if nr_map else np.nan
                                entry_cache[key] = (pit, n_pit, pm, status, nr)
                            entries.append((len(cases),) + entry_cache[key] + (a,))
                cases.append(case)
        print(f"  {season}: {len(players)} players, {n_season} cases, "
              f"{sum(c['n_absent'] > 0 for c in cases if c['season'] == season)} treated, "
              f"{n_opp_missing_in_box} with the opponent absent from the box score "
              f"({time.time() - t0:.0f}s)")
    return cases, entries


def verify_against_production(sample):
    """Real get_opponent_missing_adjustment on sampled treated cases,
    fed the same point-in-time frames. sample: (season, absent ids,
    expected multiplier, expected applied)."""
    orig = (mp_mod.get_player_id, mp_mod.cached_or_live, mp_mod.time.sleep)
    bad = 0
    try:
        mp_mod.time.sleep = lambda _s: None
        mp_mod.get_player_id = lambda name: (int(name), name, None)
        for season, absent, expect, expect_applied in sample:
            prev = prev_season(season)

            def fake_cached_or_live(key, _fetch, _prev=prev):
                if key.startswith("career_stats_"):
                    trunc, _full = career_frame(int(key.rsplit("_", 1)[1]), _prev)
                    if trunc is None:
                        raise ConnectionError("offline: not cached")
                    return trunc, "cache"
                if key == f"player_estimated_metrics_{_prev}":
                    df, _ = _memo_load(key)
                    if df is not None:
                        return df, "cache"
                raise ConnectionError("offline / not point-in-time safe")

            mp_mod.cached_or_live = fake_cached_or_live
            res = mp_mod.get_opponent_missing_adjustment([str(a) for a in absent], prev)
            got = res.multiplier_for(STATS[0])
            if res.applied != expect_applied or abs(got - expect) > 1e-12:
                bad += 1
    finally:
        mp_mod.get_player_id, mp_mod.cached_or_live, mp_mod.time.sleep = orig
    print(f"  real get_opponent_missing_adjustment on {len(sample)} sampled treated cases: "
          f"{bad} mismatches vs the prod_prev x quality x {PRODUCTION_STRENGTH} variant")
    if bad:
        raise AssertionError("re-implementation diverges from production")


def weighted_minutes(case_idx, mpg, nr, n_cases, quality):
    """Sum over absent opponents of mpg x quality multiplier (skipped
    where mpg is nan). Returns (w, n_contributing)."""
    ok = ~np.isnan(mpg)
    q = np.ones(len(mpg))
    if quality:
        has_nr = ~np.isnan(nr)
        q[has_nr] = np.maximum(MIN_QUALITY_MULTIPLIER, 1 + nr[has_nr] / QUALITY_SCALE)
    w = np.bincount(case_idx[ok], weights=(mpg * q)[ok], minlength=n_cases)
    return w, np.bincount(case_idx[ok], minlength=n_cases)


def fit_uniform(ref, actual, x, sel):
    """Grid s minimising pooled rel-MAE on sel."""
    r, a, xx = ref[sel], actual[sel], x[sel]
    scale = np.abs(r - a).mean(axis=0)
    loss = np.zeros(len(FIT_GRID))
    for st in range(len(STATS)):
        p = r[:, st][:, None] * (1 + xx[:, None] * FIT_GRID[None, :])
        loss += np.abs(p - a[:, st][:, None]).mean(axis=0) / scale[st]
    return float(FIT_GRID[int(np.argmin(loss))])


def fit_per_stat(ref, actual, x, sel):
    r, a, xx = ref[sel], actual[sel], x[sel]
    out = []
    for st in range(len(STATS)):
        p = r[:, st][:, None] * (1 + xx[:, None] * FIT_GRID[None, :])
        out.append(float(FIT_GRID[int(np.argmin(np.abs(p - a[:, st][:, None]).mean(axis=0)))]))
    return np.array(out)


def fit_const(ref, actual, sel):
    """Placebo: per-stat constant multiplier, no dependence on w. If it
    matches the w-scaled per-stat fit, that fit is only exploiting
    right-skew (the MAE-optimal point is below the mean), not the
    missing opponents."""
    r, a = ref[sel], actual[sel]
    out = []
    for st in range(len(STATS)):
        p = r[:, st][:, None] * CONST_GRID[None, :]
        out.append(float(CONST_GRID[int(np.argmin(np.abs(p - a[:, st][:, None]).mean(axis=0)))]))
    return np.array(out)


def fit_const_plus_w(ref, actual, x, sel):
    """Per-stat c x (1 + x * s), joint grid. Returns (c, s) arrays."""
    r, a, xx = ref[sel], actual[sel], x[sel]
    cs, ss = [], []
    for st in range(len(STATS)):
        base = r[:, st][:, None] * (1 + xx[:, None] * JOINT_S_GRID[None, :])
        best = (np.inf, None, None)
        for c in CONST_GRID:
            loss = np.abs(c * base - a[:, st][:, None]).mean(axis=0)
            j = int(np.argmin(loss))
            if loss[j] < best[0]:
                best = (loss[j], c, JOINT_S_GRID[j])
        cs.append(float(best[1]))
        ss.append(float(best[2]))
    return np.array(cs), np.array(ss)


def mean_effect(ref_all, actual_all, x_all, clusters_all, rng):
    """Least squares actual = ref * (c + d * x) over ALL cases (x = 0 when
    untreated), per stat; s = d / c is the mean-unbiased strength (what
    the layer's formula means if predictions are meant as means).
    Cluster bootstrap CI over player-seasons."""
    codes, uniq = pd.factorize(clusters_all)
    n_cl = len(uniq)
    rows = []
    w = rng.multinomial(n_cl, np.full(n_cl, 1 / n_cl), size=sks.BOOTSTRAP_RESAMPLES).astype(float)
    for st, col in enumerate(STATS):
        r, a = ref_all[:, st], actual_all[:, st]
        u, v = r, r * x_all
        # per-cluster sufficient statistics for the 2x2 normal equations
        S = {k: np.bincount(codes, weights=val, minlength=n_cl)
             for k, val in {"uu": u * u, "uv": u * v, "vv": v * v, "ua": u * a, "va": v * a}.items()}

        def solve(weights):
            uu, uv, vv = weights @ S["uu"], weights @ S["uv"], weights @ S["vv"]
            ua, va = weights @ S["ua"], weights @ S["va"]
            det = uu * vv - uv * uv
            c = (vv * ua - uv * va) / det
            d = (uu * va - uv * ua) / det
            return d / c
        point = solve(np.ones(n_cl))
        boot = np.array([solve(wi) for wi in w])
        lo, hi = np.percentile(boot, [2.5, 97.5])
        rows.append({"stat": col, "s_mean": point, "lo": lo, "hi": hi})
    return pd.DataFrame(rows)


def main():
    print("Building point-in-time cases ...")
    cases, entries = build()
    n_all = len(cases)

    defense = np.array([[defense_for(c, 0.0).multiplier_for(col) for col in STATS] for c in cases])
    base_all = np.array([c["base"] for c in cases], dtype=float)
    ref_all = base_all * defense
    actual_all = np.array([c["actual"] for c in cases], dtype=float)
    validate_season_only_against_published(cases, ref_all[:, None, :])

    treated = np.array([c["n_absent"] > 0 for c in cases])
    t_index = -np.ones(n_all, dtype=int)
    t_index[treated] = np.arange(treated.sum())
    tcases = [c for c in cases if c["n_absent"] > 0]
    n_t = len(tcases)
    case_idx = t_index[np.array([e[0] for e in entries])]
    pit_mpg = np.array([e[1] for e in entries], dtype=float)
    prod_mpg = np.array([e[3] for e in entries], dtype=float)
    status = np.array([e[4] for e in entries])
    nr = np.array([e[5] for e in entries], dtype=float)
    ref = ref_all[treated]
    actual = actual_all[treated]
    n_absent = np.array([c["n_absent"] for c in tcases])
    seasons = np.array([c["season"] for c in tcases])
    clusters = np.array([f"{c['player_id']}_{c['season']}" for c in tcases])
    e_season = seasons[case_idx]

    # ---- coverage --------------------------------------------------------
    print(f"\n=== Coverage: {n_all} cases, {n_t} treated ({n_t / n_all:.1%}) ===")
    tab = pd.DataFrame({"season": seasons, "n_absent": n_absent}).groupby("season").agg(
        treated=("n_absent", "size"),
        single_out=("n_absent", lambda s: int((s == 1).sum())),
        multi_out=("n_absent", lambda s: int((s >= 2).sum())),
        max_out=("n_absent", "max"))
    tab.insert(0, "all_cases", pd.Series([c["season"] for c in cases]).value_counts().sort_index())
    print(tab.to_string())
    ent = pd.DataFrame({"season": e_season, "status": status, "has_nr": ~np.isnan(nr),
                        "pit_mpg": pit_mpg, "prod_mpg": prod_mpg})
    print(f"  absent-opponent entries (case x absent player): {len(entries)}; distinct absent "
          f"player-seasons: {len({(s, e[-1]) for s, e in zip(e_season, entries)})}")
    print("  prod_prev MPG status by season (resolve_season_mpg on career stats <= previous season):")
    print(ent.groupby(["season", "status"]).size().unstack(fill_value=0).to_string())
    print("  net-rating coverage (previous-season estimated metrics) by season:")
    print(ent.groupby("season")["has_nr"].mean().round(3).to_string())
    both = ~np.isnan(prod_mpg)
    print(f"  pit vs prod_prev MPG where both exist: mean {pit_mpg[both].mean():.2f} vs "
          f"{prod_mpg[both].mean():.2f}, mean |diff| {np.abs(pit_mpg[both] - prod_mpg[both]).mean():.2f}, "
          f"corr {np.corrcoef(pit_mpg[both], prod_mpg[both])[0, 1]:.3f}")
    qm = np.where(np.isnan(nr), np.nan, np.maximum(MIN_QUALITY_MULTIPLIER, 1 + nr / QUALITY_SCALE))
    print(f"  quality multiplier where available: mean {np.nanmean(qm):.3f}, p05 {np.nanpercentile(qm, 5):.3f}, "
          f"p95 {np.nanpercentile(qm, 95):.3f}, share at the 0.2 floor {np.nanmean(qm == 0.2):.3%}")

    # ---- weighted minutes and multipliers --------------------------------
    W, NCONTRIB = {}, {}
    for src in MPG_SOURCES:
        mpg = pit_mpg if src == "pit" else prod_mpg
        for q in QUALITY:
            W[(src, q)], NCONTRIB[(src, q)] = weighted_minutes(case_idx, mpg, nr, n_t, q)
    wp = W[("prod_prev", True)]
    prod_applied = NCONTRIB[("prod_prev", True)] > 0
    mult_prod = 1 + wp / 240 * PRODUCTION_STRENGTH
    print(f"  production (prod_prev, quality, 0.35) multiplier on treated cases: applied to "
          f"{prod_applied.sum()} ({prod_applied.mean():.1%}); mean {mult_prod.mean():.3f}, "
          f"p50 {np.median(mult_prod):.3f}, p95 {np.percentile(mult_prod, 95):.3f}, max {mult_prod.max():.3f}")
    for src in MPG_SOURCES:
        for q in QUALITY:
            w = W[(src, q)]
            print(f"    w ({src}, quality={q}): mean {w.mean():.1f}, single_out {w[n_absent == 1].mean():.1f}, "
                  f"multi_out {w[n_absent >= 2].mean():.1f} min-equivalents")

    # verification through the real function
    rng = np.random.default_rng(SEED)
    pick = rng.choice(n_t, size=min(VERIFY_N, n_t), replace=False)
    verify_against_production([(seasons[i], tcases[i]["absent"], mult_prod[i], bool(prod_applied[i]))
                               for i in pick])

    fit_rows = []
    # ---- bias of the reference ------------------------------------------
    print("\n=== Reference bias (pred - actual), treated vs untreated ===")
    ref_u, act_u = ref_all[~treated], actual_all[~treated]
    bias_rows = []
    for s_i, col in enumerate(STATS):
        bt, bu = (ref[:, s_i] - actual[:, s_i]).mean(), (ref_u[:, s_i] - act_u[:, s_i]).mean()
        rt = actual[:, s_i].sum() / ref[:, s_i].sum()
        ru = act_u[:, s_i].sum() / ref_u[:, s_i].sum()
        bias_rows.append({"stat": col, "bias_treated": bt, "bias_untreated": bu,
                          "bias_single": (ref[n_absent == 1, s_i] - actual[n_absent == 1, s_i]).mean(),
                          "bias_multi": (ref[n_absent >= 2, s_i] - actual[n_absent >= 2, s_i]).mean(),
                          "actual/ref_treated": rt, "actual/ref_untreated": ru,
                          "excess_ratio": rt / ru - 1,
                          "implied_s_pit_q": (rt / ru - 1) / (W[("pit", True)] / 240).mean()})
    print(pd.DataFrame(bias_rows).round(4).to_string(index=False))
    clusters_all = np.array([f"{c['player_id']}_{c['season']}" for c in cases])
    for src, q in [("pit", True), ("prod_prev", True), ("pit", False)]:
        x_all = np.zeros(n_all)
        x_all[treated] = W[(src, q)] / 240
        me = mean_effect(ref_all, actual_all, x_all, clusters_all, np.random.default_rng(SEED))
        print(f"  mean-unbiased strength (OLS actual = ref*(c + d*x), all cases, s = d/c), {src}, quality={q}:")
        print("    " + ", ".join(f"{r.stat}={r.s_mean:+.3f} [{r.lo:+.3f},{r.hi:+.3f}]" for r in me.itertuples()))
        for r in me.itertuples():
            fit_rows.append({"row_type": "mean_effect", "subset": "all_cases", "variant": f"ols_{src}_{'q' if q else 'noq'}",
                             "mpg_source": src, "quality": q, "fold": "fit_all_seasons", "stat": r.stat,
                             "fitted_s": r.s_mean, "mae": np.nan, "bias": np.nan,
                             "rel_mae_vs_reference": np.nan, "n_cases": n_all,
                             "ci_lo": r.lo, "ci_hi": r.hi})

    # ---- grid predictions ------------------------------------------------
    preds, moved = {}, {}
    for src in MPG_SOURCES:
        for q in QUALITY:
            x = W[(src, q)] / 240
            for s in STRENGTHS:
                preds[(src, q, s)] = ref * (1 + x * s)[:, None]
                moved[(src, q, s)] = (NCONTRIB[(src, q)] > 0) & (s != 0)

    # ---- leave-one-season-out fits --------------------------------------
    loso_pred, loso_uni_pred = {}, {}
    uniq_seasons = sorted(set(seasons))
    print("\n=== Leave-one-season-out fits (MAE, grid -3..1 step 0.01; const 0.70..1.10) ===")
    for src in MPG_SOURCES:
        for q in QUALITY:
            x = W[(src, q)] / 240
            ps, pu, pc, pcw = (np.empty_like(ref) for _ in range(4))
            tag = f"{src}_{'q' if q else 'noq'}"
            for h in uniq_seasons + ["all"]:
                train = seasons != h
                s_stat = fit_per_stat(ref, actual, x, train)
                s_uni = fit_uniform(ref, actual, x, train)
                fits = {f"loso_perstat_{tag}": s_stat, f"loso_uniform_{tag}": [s_uni]}
                if (src, q) == ("pit", True) or h == "all":
                    c_only = fit_const(ref, actual, train)
                    c_j, s_j = fit_const_plus_w(ref, actual, x, train)
                    fits.update({"loso_const_perstat": c_only, f"loso_constw_{tag}_c": c_j,
                                 f"loso_constw_{tag}_s": s_j})
                if h != "all":
                    test = seasons == h
                    ps[test] = ref[test] * (1 + x[test][:, None] * s_stat[None, :])
                    pu[test] = ref[test] * (1 + x[test] * s_uni)[:, None]
                    if (src, q) == ("pit", True):
                        pc[test] = ref[test] * c_only[None, :]
                        pcw[test] = ref[test] * c_j[None, :] * (1 + x[test][:, None] * s_j[None, :])
                label = f"held_out_{h}" if h != "all" else "fit_all_seasons"
                for fname, vals in fits.items():
                    print(f"  {label}, {fname}: " + (f"{vals[0]:+.2f}" if len(vals) == 1 else ", ".join(
                        f"{c}={v:+.2f}" for c, v in zip(STATS, vals))))
                    for c, v in zip(STATS if len(vals) > 1 else ["UNIFORM"], vals):
                        fit_rows.append({"row_type": "fit", "subset": "all_treated", "variant": fname,
                                         "mpg_source": src, "quality": q, "fold": label,
                                         "stat": c, "fitted_s": v, "n_cases": int(train.sum())})
            loso_pred[(src, q)], loso_uni_pred[(src, q)] = ps, pu
            if (src, q) == ("pit", True):
                loso_const_pred, loso_constw_pred = pc, pcw

    # ---- evaluation ------------------------------------------------------
    subsets = {"all_treated": np.ones(n_t, bool), "single_out": n_absent == 1,
               "multi_out": n_absent >= 2, "prod_applied": prod_applied}
    for s in uniq_seasons:
        subsets[f"season_{s}"] = seasons == s

    variants = [("reference", None, None, None, ref, np.zeros(n_t, bool))]
    for (src, q, s), p in preds.items():
        if s != 0:
            variants.append((variant_name(src, q, s), src, q, s, p, moved[(src, q, s)]))
    for (src, q), p in loso_pred.items():
        mv = NCONTRIB[(src, q)] > 0
        variants.append((f"loso_perstat_{src}_{'q' if q else 'noq'}", src, q, "per_stat_loso", p, mv))
        variants.append((f"loso_uniform_{src}_{'q' if q else 'noq'}", src, q, "uniform_loso",
                         loso_uni_pred[(src, q)], mv))
    variants.append(("loso_const_perstat", None, None, "const_loso", loso_const_pred, np.ones(n_t, bool)))
    variants.append(("loso_constw_pit_q", "pit", True, "const_plus_w_loso", loso_constw_pred,
                     np.ones(n_t, bool)))

    out_rows, norms = [], {}
    for sub_name, sel in subsets.items():
        a = actual[sel]
        scale = np.abs(ref[sel] - a).mean(axis=0)
        norms[sub_name] = {}
        for name, src, q, s, p, mv in variants:
            err = p[sel] - a
            abs_err = np.abs(err)
            norms[sub_name][name] = (abs_err / scale).mean(axis=1)
            common = {"row_type": "eval", "subset": sub_name, "variant": name, "mpg_source": src,
                      "quality": q, "strength": s, "n_cases": int(sel.sum()), "n_moved": int(mv[sel].sum())}
            for s_i, col in enumerate(STATS):
                out_rows.append({**common, "stat": col, "mae": abs_err[:, s_i].mean(),
                                 "bias": err[:, s_i].mean(),
                                 "rel_mae_vs_reference": abs_err[:, s_i].mean() / scale[s_i]})
            out_rows.append({**common, "stat": "POOLED", "rel_mae_vs_reference": norms[sub_name][name].mean()})
    u_err = ref_u - act_u
    for s_i, col in enumerate(STATS):
        out_rows.append({"row_type": "eval", "subset": "untreated", "variant": "reference",
                         "n_cases": int((~treated).sum()), "n_moved": 0, "stat": col,
                         "mae": np.abs(u_err[:, s_i]).mean(), "bias": u_err[:, s_i].mean(),
                         "rel_mae_vs_reference": 1.0})
    res = pd.DataFrame(out_rows + fit_rows)
    cols = ["row_type", "subset", "variant", "mpg_source", "quality", "strength", "fold", "stat",
            "n_cases", "n_moved", "mae", "bias", "rel_mae_vs_reference", "fitted_s", "ci_lo", "ci_hi"]
    res = res[cols]
    res.to_csv(OUTPUT_PATH, index=False)

    pooled = res[(res["row_type"] == "eval") & (res["stat"] == "POOLED")].pivot(
        index="variant", columns="subset", values="rel_mae_vs_reference")
    show = ["all_treated", "single_out", "multi_out", "prod_applied"] + [f"season_{s}" for s in uniq_seasons]
    prod_name = variant_name(*PRODUCTION)
    print("\n=== Pooled rel-MAE vs reference (<1 = the layer helps) ===")
    print(pooled[show].sort_values("all_treated").round(4).to_string())
    grid_names = [variant_name(*k) for k in preds if k[2] != 0]
    best_name = pooled.loc[grid_names, "all_treated"].idxmin()
    print(f"\n  production = {prod_name}; best uniform grid config (in-sample) = {best_name}")
    print("  pooled rel-MAE by strength, all treated:")
    tbl = pd.DataFrame({f"{src}_{'q' if q else 'noq'}":
                        [1.0 if s == 0 else pooled.loc[variant_name(src, q, s), "all_treated"] for s in STRENGTHS]
                        for src in MPG_SOURCES for q in QUALITY}, index=STRENGTHS)
    print(tbl.round(4).to_string())

    print("\nPaired cluster bootstrap (player-season resampling), 95% CI, pooled rel-MAE difference:")
    boot_rng = np.random.default_rng(sks.BOOTSTRAP_SEED)
    main_cfg = "pit_q"
    comparisons = [
        (prod_name, "reference"),
        (variant_name("pit", True, PRODUCTION_STRENGTH), "reference"),
        (best_name, "reference"),
        (best_name, prod_name),
        (f"loso_uniform_{main_cfg}", "reference"),
        (f"loso_perstat_{main_cfg}", "reference"),
        (f"loso_perstat_{main_cfg}", prod_name),
        (f"loso_perstat_{main_cfg}", f"loso_uniform_{main_cfg}"),
        (f"loso_perstat_{main_cfg}", "loso_const_perstat"),
        ("loso_constw_pit_q", "loso_const_perstat"),
        ("loso_perstat_prod_prev_q", "loso_uniform_prod_prev_q"),
        ("loso_perstat_pit_noq", f"loso_perstat_{main_cfg}"),
        (variant_name("pit", False, 0.10), variant_name("pit", True, 0.10)),
    ]
    for sub_name in ["all_treated", "single_out", "multi_out"]:
        sel = subsets[sub_name]
        print(f"  [{sub_name}, n={int(sel.sum())}, clusters={len(set(clusters[sel]))}]")
        for a_name, b_name in comparisons:
            per_case = np.column_stack([norms[sub_name][a_name], norms[sub_name][b_name]])
            d, lo, hi = cluster_bootstrap_diff(per_case, clusters[sel], 0, 1, boot_rng)
            print(f"    {a_name} - {b_name}: {d:+.4f}  [{lo:+.4f}, {hi:+.4f}]")

    print("\nPer-stat, all treated (MAE / bias / rel-MAE):")
    keep = ["reference", prod_name, best_name, f"loso_uniform_{main_cfg}", f"loso_perstat_{main_cfg}",
            "loso_const_perstat", "loso_constw_pit_q"]
    sub = res[(res["row_type"] == "eval") & (res["subset"] == "all_treated") & (res["stat"] != "POOLED")
              & res["variant"].isin(keep)]
    for val in ["mae", "bias", "rel_mae_vs_reference"]:
        print(f"  {val}:")
        print(sub.pivot(index="stat", columns="variant", values=val).loc[STATS, keep].round(4).to_string())

    print(f"\nWrote {len(res)} rows to {OUTPUT_PATH}.")
    print("SCOPE: missing_opponents x opponent_defense x season baseline, regular-season targets, "
          "treated = >= 1 absent key opponent who played for that team again later -- see module docstring.")


if __name__ == "__main__":
    main()
