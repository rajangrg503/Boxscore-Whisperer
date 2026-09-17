"""
Point-in-time backtest of POOLED PRIORS for the Full Matchup "player
marked out" layer: should each with/without ratio be shrunk toward
something other than 1.0?

WHY THIS EXISTS: out_redistribution_sweep.py showed the raw ratio
r = mean(games without teammate) / mean(all games) is 3.2% worse than
no adjustment, and that shrinking toward 1.0,
r' = 1 + (r - 1) * n / (n + k), is best at k=80 (rel-MAE 0.9992). That
is production now (teammates.OUT_RATIO_SHRINK_K / shrink_ratio). But at
k=80 a benched star's production mostly vanishes from the projected box
score: on treated cases the unadjusted predictions are biased LOW
(PTS -0.98, AST -0.27, FG3A -0.26 per player). The hypothesis tested
here: shrink toward a pooled prior m -- the typical league-wide bump a
player gets when a key teammate sits -- instead of 1.0:
    r' = m + (r - m) * n / (n + k),   k in {0, 10, 20, 40, 80, 160, inf}
(k = inf is the prior alone, no player-specific data.)

PRIORS (per absent teammate i, per stat s):
  * P0      m = 1 (production; P0_k80 is the reference-to-beat).
  * P1_mean / P1_median   m_s = mean / median of the raw ratios of
            training-season entries that meet production's 3/3 sample
            thresholds (descriptive, not fitted to error).
  * P1_fit  m_s fitted to minimise training MAE (grid 0.90-1.30).
  * P2s     m_s = 1 + beta_s * A_is, A_is = the absent teammate's share
            of the team's per-game stat s (his per-game average for this
            team / the team's per-game total, both season-to-date,
            strictly before the target date, from cached box scores).
  * P2m     same with A_i = his share of team minutes (mpg / team
            minutes per game), one share for every stat.
  * P2a     same with A_is = the absent player's own per-game average
            / LEAGUE_UNIT_s (fixed rounded league team per-game totals) --
            the production-friendly form: it needs only the out player's
            gamelog, which get_out_redistribution_adjustment already has.
  * P2c     m_s = 1 / (1 - A_is), no fitted parameter: "everyone left
            picks up the absent production pro rata" (full conservation).
  * P3      m_s = 1 + beta_s * A_is * (p_s / 0.20) ** gamma_s, p_s = the
            TARGET player's own share of team stat s (point-in-time
            season average / team per-game total). 0.20 is a fixed,
            data-free constant (an even starter's share), so the fit
            stays leak-free; gamma in {0.5, 1, 2} and beta are fitted.
  * "_comb" (P2s_comb, P3_comb): the alternative multi-out rule -- the
    prior comes from the COMBINED absent share (sum of A_i) and each
    teammate contributes only its shrunk deviation from its own prior:
    mult = m(sum A) * prod_i(r'_i / m_i). Identical to the base family
    for single-out cases; reported separately.
  * "_pos" (P1_fit_pos, P2s_pos, P2m_pos, P2a_pos, P3_pos, P2s_comb_pos):
    the fit is restricted to the redistribution direction (m >= 1,
    beta >= 0). See ARTEFACT below for why this matters.
  Stacking: production's rule -- product over absent teammates, clamped
  to OUT_STACK_CLAMP (0.75, 1.35) when 2+ contribute. For P0 "contribute"
  = meets 3/3 (exactly production); for a pooled prior every absent
  teammate contributes, because the prior needs no player data. Entries
  failing 3/3 get weight 0, i.e. the prior alone (P0: 1.0).

NO LEAKAGE: every fitted constant (m, beta, gamma, and P1_mean/median)
is estimated leave-one-season-out -- fitted on the two other seasons,
scored on the held-out one -- and the out-of-sample predictions of the
three folds are stitched together before any metric is computed. Each
fit is 1-D/2-D grid search per stat (per-stat MAE is separable, so this
is the exact pooled-rel-MAE minimiser on the grid). "kpick" rows also
choose k inside the fold (lowest training rel-MAE), so they carry no
selection over k at all; picking the best fixed k from the printed OOS
table is a (mild) look at the held-out data -- quote kpick when in
doubt. A forward-chaining check (fit on strictly earlier seasons only)
is printed for the best family. "fit_all" coefficients (all 3 seasons)
are the ones to paste into code; they are never scored.

METRICS / SUBSETS / CIs: exactly out_redistribution_sweep.py's --
pooled rel-MAE = mean over 9 stats of MAE / no-adjustment MAE, bias,
paired cluster bootstrap by player-season (shrinkage_k_sweep's
cluster_bootstrap_diff), subsets all_treated / single_out / multi_out /
per season. Case set, treatment definition and ratio arithmetic are
imported from out_redistribution_sweep.build() (verified there against
the real get_out_redistribution_adjustment and app.py's
combine_out_redistributions). The ratio check and the
backtest_results.csv cross-check are re-run here; P0_k80 is asserted
equal to that script's vectorised m3_k80 row, whose stack rule that
script checks against app.py).

ARTEFACT (read before quoting an unconstrained win): MAE is minimised by
the median, and per-game counts (BLK, STL, OREB, TOV, FG3M ...) are
right-skewed, so scaling ANY season-average prediction down lowers MAE
even with nobody missing. The unconstrained fits exploit that (BLK beta
hits the grid floor, bias gets worse). Two controls are printed: the
MAE-optimal flat per-stat scale on UNTREATED cases, and
"P0_k80_xscale" = production x that scale (fitted leave-one-season-out
on untreated training cases only). The unconstrained families' gain over
P0 is mostly that scale, i.e. a baseline-calibration question, not a
teammate-out effect -- and it moves team totals the wrong way.

TEAM-TOTAL CONSERVATION (treated team-games = a team's game in which
>= 1 key teammate sat, with >= 1 case-set player):
  * case_only: sum of predictions over the team's CASE-SET players in
    that game minus their actual sum. The case set is run_backtest's
    top-150-by-minutes list (and only games with >= MIN_BASELINE_GAMES
    prior games), so this covers ~3 players per team -- it measures
    whether the projected players' pickup is under-projected, NOT the
    whole box score.
  * extended: case-set predictions + (every other player who actually
    played for the team that night: his season-to-date per-game
    box-score average, any team, x the same opponent-defense
    multiplier; 0 if he had no prior game) minus the actual team total.
    Using who actually played stands in for the lineup a user knows;
    those non-case players get NO out adjustment here.
  * extended_prior: same, but the non-case players also get the
    family's prior alone (k = inf; P0 leaves them at 1.0).
  * control: extended, reference predictions, on UNTREATED team-games --
    the extension's own bias with nobody missing.
  Printed for PTS/AST/FG3A (all 9 stats in the CSV).

LIMITS: everything out_redistribution_sweep.py lists (same-season
ratios only, "without" games ignore who else was missing, traded
players' old-team games count as "without", ratio 0 possible at k=0,
user marks exactly the absent key teammates). Shares come from box
scores for THIS team only (a player's games for a previous team don't
count toward A); p_s uses the gamelog baseline, which does include a
traded player's old-team games. Minutes of the target game are not
used. Box scores give no team rows, so team totals are summed player
rows.

Usage:
    python3 out_prior_sweep.py
    (OUT_PRIOR_SWEEP_BUILD_CACHE=/some/file.pkl caches the ~90s case
    build between runs; the production checks are skipped when loading.)
Writes out_prior_sweep_results.csv (tidy: scope, subset, config, family,
k, stat, n, mae, bias, rel_mae_vs_no_adjustment, coef, gamma).
scope = player | team_case_only | team_extended | team_extended_prior |
team_control_untreated | coef (fitted constants per fold; subset =
holdout_<season> or fit_all; coef = m for P1, beta for P2/P3).
"""

import glob
import json
import os
import pickle
import time
from functools import lru_cache

import numpy as np
import pandas as pd

import out_redistribution_sweep as ors  # installs offline stubs via shrinkage_k_sweep
import shrinkage_k_sweep as sks
from shrinkage_k_sweep import REPO_ROOT, cluster_bootstrap_diff, defense_for, \
    validate_season_only_against_published
from engine.adjustments.teammates import OUT_RATIO_SHRINK_K
from run_backtest import SEASONS

OUTPUT_PATH = os.path.join(REPO_ROOT, "out_prior_sweep_results.csv")
CACHE_DIR = ors.CACHE_DIR
STATS = ors.STATS
NS = len(STATS)
BOX_FIELDS = {"PTS": "points", "AST": "assists", "REB": "reboundsTotal", "STL": "steals",
              "BLK": "blocks", "FG3M": "threePointersMade", "TOV": "turnovers",
              "FG3A": "threePointersAttempted", "OREB": "reboundsOffensive"}
K_VALUES = [0, 10, 20, 40, 80, 160, np.inf]
SCALE_GRID = np.round(np.arange(0.30, 1.20001, 0.0025), 4)
P1_GRID = np.round(np.arange(0.60, 1.40001, 0.0025), 4)
BETA_GRID = np.round(np.arange(-4.0, 4.00001, 0.05), 3)
GAMMAS = [0.5, 1.0, 2.0]
# Fixed, rounded league per-team-game totals (not fitted) -- the unit for
# P2a, whose share uses only the absent player's own per-game average,
# the one input production already has (his gamelog). beta is refitted,
# so this only sets the grid scale; the run prints the measured values.
LEAGUE_UNIT = np.array([114.0, 26.0, 44.0, 8.0, 5.0, 13.0, 13.5, 36.0, 11.0])
ROLE_REF = 0.20            # data-free normaliser for the player's own share (P3)
FITTED = ["P1_fit", "P2s", "P2m", "P3", "P2s_comb", "P3_comb",
          "P1_fit_pos", "P2s_pos", "P2m_pos", "P3_pos", "P2s_comb_pos", "P2a", "P2a_pos"]
UNFITTED = ["P0", "P1_mean", "P1_median", "P2c"]
FAMILIES = UNFITTED + FITTED
TEAM_STATS_PRINT = ["PTS", "AST", "FG3A"]


def kname(k):
    return "inf" if np.isinf(k) else str(int(k))


# ---- box scores with full stat lines ---------------------------------------
class TeamBox:
    """Every player row (minutes > 0) of a season's cached box scores,
    with dates from the cached gamelogs, plus point-in-time lookups."""

    def __init__(self, season):
        dates = {}
        for path in glob.glob(os.path.join(CACHE_DIR, f"gamelog_*_{season}.json")):
            with open(path) as f:
                for r in json.load(f)["data"]:
                    dates.setdefault(str(r["Game_ID"]), r["GAME_DATE"])
        rows = []
        for path in sorted(glob.glob(os.path.join(CACHE_DIR, f"boxscore_{ors.season_prefix(season)}*.json"))):
            with open(path) as f:
                for r in json.load(f)["data"]:
                    m = ors._minutes(r["minutes"])
                    if m > 0:
                        rows.append([str(r["gameId"]), r["teamId"], str(r["personId"]), m]
                                    + [float(r[BOX_FIELDS[s]] or 0) for s in STATS])
        box = pd.DataFrame(rows, columns=["game_id", "team_id", "person_id", "minutes"] + STATS)
        box["date"] = box["game_id"].map({g: pd.Timestamp(d) for g, d in dates.items()})
        self.box = box.dropna(subset=["date"]).sort_values(["date", "game_id"]).reset_index(drop=True)
        self.game_date = dict(zip(self.box["game_id"], self.box["date"]))
        self.by_team = {t: df for t, df in self.box.groupby("team_id")}
        self.team_game = {key: df for key, df in self.box.groupby(["game_id", "team_id"])}
        self.person = {}
        for p, df in self.box.groupby("person_id"):
            self.person[p] = (df["date"].values, np.cumsum(df[STATS].values, axis=0))

    @lru_cache(maxsize=None)
    def profile(self, team_id, date):
        """(team per-game means [minutes + STATS], player per-game means
        for this team) over the team's games strictly before date."""
        df = self.by_team[team_id]
        prior = df[df["date"] < date]
        n_games = prior["game_id"].nunique()
        cols = ["minutes"] + STATS
        team_mean = prior[cols].sum() / n_games if n_games else pd.Series(np.nan, index=cols)
        return team_mean, prior.groupby("person_id")[cols].mean()

    def shares(self, team_id, date, person_id):
        """(stat shares (9,), minutes share, per-game average / LEAGUE_UNIT (9,))
        of person_id for team_id before date."""
        team_mean, pm = self.profile(team_id, date)
        if person_id not in pm.index:
            return np.zeros(NS), 0.0, np.zeros(NS)
        row = pm.loc[person_id]
        avg = row[STATS].values.astype(float)
        return avg / team_mean[STATS].values.astype(float), \
            float(row["minutes"] / team_mean["minutes"]), avg / LEAGUE_UNIT

    def season_avg_before(self, person_id, date):
        """Per-game box-score average (any team) strictly before date; zeros if none."""
        if person_id not in self.person:
            return np.zeros(NS), 0
        dates, cum = self.person[person_id]
        n = int(np.searchsorted(dates, np.datetime64(date), side="left"))
        return (cum[n - 1] / n if n else np.zeros(NS)), n


# ---- multiplier machinery ---------------------------------------------------
class Entries:
    """One row per (row, absent teammate); rows sorted and all present."""

    def __init__(self, row_idx, n_wo, n_w, ratios, a_stat, a_min, a_abs, ps, n_rows):
        order = np.argsort(row_idx, kind="stable")
        self.row_idx = row_idx[order]
        if not np.array_equal(np.unique(self.row_idx), np.arange(n_rows)):
            raise AssertionError("every row needs >= 1 entry")
        self.starts = np.flatnonzero(np.r_[True, np.diff(self.row_idx) != 0])
        self.n_wo, self.n_w = n_wo[order], n_w[order]
        self.applied = (self.n_wo >= 3) & (self.n_w >= ors.PRODUCTION_MIN_WITH)
        self.ratios = ratios[order]
        self.a_stat = a_stat[order]
        self.a_min = np.repeat(a_min[order][:, None], NS, axis=1)
        self.a_abs = a_abs[order]
        self.ps = ps                                   # (n_rows, 9)
        self.n_rows = n_rows
        self.n_entries = np.bincount(self.row_idx, minlength=n_rows)
        self.n_applied = np.bincount(self.row_idx, weights=self.applied, minlength=n_rows)
        self.weights = {}

    def weight(self, k):
        if k not in self.weights:
            if np.isinf(k):
                w = np.zeros(len(self.n_wo))
            elif k == 0:
                w = np.ones(len(self.n_wo))
            else:
                w = self.n_wo / (self.n_wo + k)
            self.weights[k] = np.where(self.applied, w, 0.0)
        return self.weights[k]

    def row_sum(self, x):
        return np.add.reduceat(x, self.starts, axis=0)


def prior_from_share(fam, theta, share, ps):
    base = fam.split("_")[0]
    if base == "P2c":
        return 1.0 / (1.0 - np.minimum(share, 0.9))
    if base in ("P2s", "P2m", "P2a"):
        return 1.0 + theta["beta"] * share
    if base == "P3":
        return 1.0 + theta["beta"] * share * (np.maximum(ps, 0.0) / ROLE_REF) ** theta["gamma"]
    raise ValueError(fam)


def multipliers(fam, theta, k, E, stack_clamp):
    """(n_rows, 9) combined out-multipliers for family fam."""
    base = fam.split("_")[0]
    share = {"P2m": E.a_min, "P2a": E.a_abs}.get(base, E.a_stat)
    if base == "P0":
        prior = np.ones_like(E.ratios)
    elif base == "P1":
        prior = np.broadcast_to(np.asarray(theta["m"], float), E.ratios.shape)
    else:
        prior = prior_from_share(fam, theta, share, E.ps[E.row_idx])
    prior = np.maximum(prior, 0.05)
    r_eff = np.where(E.applied[:, None], E.ratios, prior)
    rp = prior + (r_eff - prior) * E.weight(k)[:, None]
    if "_comb" in fam:
        prior_tot = np.maximum(prior_from_share(fam, theta, E.row_sum(share), E.ps), 0.05)
        mult = prior_tot * np.multiply.reduceat(rp / prior, E.starts, axis=0)
    else:
        mult = np.multiply.reduceat(rp, E.starts, axis=0)
    count = E.n_applied if base == "P0" else E.n_entries
    stacked = count >= 2
    mult[stacked] = np.clip(mult[stacked], stack_clamp[0], stack_clamp[1])
    return mult


def grid_thetas(fam):
    """Grid for a fitted family; "_pos" keeps only the redistribution
    direction (m >= 1, beta >= 0)."""
    base = fam.split("_")[0]
    pos = fam.endswith("_pos")
    if base == "P1":
        return [{"m": g} for g in P1_GRID if not pos or g >= 1.0]
    betas = [b for b in BETA_GRID if not pos or b >= 0.0]
    if base in ("P2s", "P2m", "P2a"):
        return [{"beta": b} for b in betas]
    if base == "P3":
        return [{"beta": b, "gamma": g} for g in GAMMAS for b in betas]
    raise ValueError(fam)


def per_stat_theta(thetas, pick):
    """Assemble a per-stat theta from grid indices pick (9,)."""
    keys = thetas[0].keys()
    return {key: np.array([thetas[i][key] for i in pick], dtype=float) for key in keys}


# ---- main ---------------------------------------------------------------
def load_cases():
    cache = os.environ.get("OUT_PRIOR_SWEEP_BUILD_CACHE")
    if cache and os.path.exists(cache):
        print(f"(loading case build from {cache}; production checks skipped)")
        with open(cache, "rb") as f:
            cases, entries = pickle.load(f)[:2]
        return cases, entries, None
    cases, entries, verify = ors.build()
    if cache:
        with open(cache, "wb") as f:
            pickle.dump((cases, entries), f)
    return cases, entries, verify


def main():
    t_start = time.time()
    combine, stack_clamp = ors.load_production_combine()
    print(f"production stack clamp {stack_clamp}; production k = {OUT_RATIO_SHRINK_K}")
    print("Building point-in-time cases (out_redistribution_sweep.build) ...")
    cases, entries, verify = load_cases()
    defense_all = np.array([[defense_for(c, 0.0).multiplier_for(col) for col in STATS] for c in cases])
    base_all = np.array([c["base"] for c in cases], dtype=float)
    ref_all = base_all * defense_all
    actual_all = np.array([c["actual"] for c in cases], dtype=float)
    if verify is not None:
        validate_season_only_against_published(cases, ref_all[:, None, :])
        ors.verify_against_production(verify)

    print("Loading box scores with full stat lines ...")
    boxes = {s: TeamBox(s) for s, _ in SEASONS}
    team_of = {}
    for s, tb in boxes.items():
        team_of.update({(s, g, p): t for g, p, t in
                        zip(tb.box["game_id"], tb.box["person_id"], tb.box["team_id"])})
    for c in cases:
        c["team_id"] = team_of.get((c["season"], c["game_id"], c["player_id"]))
        c["date"] = boxes[c["season"]].game_date.get(c["game_id"])

    treated = np.array([c["n_absent"] > 0 for c in cases])
    t_rows = np.flatnonzero(treated)
    t_index = -np.ones(len(cases), dtype=int)
    t_index[t_rows] = np.arange(len(t_rows))
    n_t = len(t_rows)
    tcases = [cases[i] for i in t_rows]
    ref = ref_all[t_rows]
    actual = actual_all[t_rows]
    seasons = np.array([c["season"] for c in tcases])
    season_list = sorted(set(seasons))
    n_absent = np.array([c["n_absent"] for c in tcases])
    clusters = np.array([f"{c['player_id']}_{c['season']}" for c in tcases])

    # ---- shares per entry / per case ------------------------------------
    print("Computing point-in-time team shares ...")
    e_row = t_index[np.array([e[0] for e in entries])]
    e_nwo = np.array([e[1] for e in entries], dtype=float)
    e_nw = np.array([e[2] for e in entries], dtype=float)
    e_ratios = np.vstack([e[3] for e in entries])
    e_astat = np.zeros((len(entries), NS))
    e_amin = np.zeros(len(entries))
    e_aabs = np.zeros((len(entries), NS))
    for j, e in enumerate(entries):
        c = cases[e[0]]
        _pid, absent_id, _gid, date_str = e[5]
        e_astat[j], e_amin[j], e_aabs[j] = boxes[c["season"]].shares(c["team_id"], pd.Timestamp(date_str), absent_id)
    ps = np.zeros((n_t, NS))
    for r, c in enumerate(tcases):
        team_mean, _ = boxes[c["season"]].profile(c["team_id"], c["date"])
        ps[r] = base_all[t_rows[r]] / team_mean[STATS].values.astype(float)
    E = Entries(e_row, e_nwo, e_nw, e_ratios, e_astat, e_amin, e_aabs, ps, n_t)
    league = np.mean([tb.box.groupby(["game_id", "team_id"])[STATS].sum().mean().values
                      for tb in boxes.values()], axis=0)
    print("  league team per-game totals (3-season box scores) vs LEAGUE_UNIT: " + ", ".join(
        f"{c} {league[i]:.1f}/{LEAGUE_UNIT[i]:g}" for i, c in enumerate(STATS)))
    print(f"  {len(entries)} absent-teammate entries; A_stat PTS median {np.median(e_astat[:, 0]):.3f}, "
          f"A_min median {np.median(e_amin):.3f}; player PTS share median {np.median(ps[:, 0]):.3f}")

    # ---- check P0 against out_redistribution_sweep's production row -----
    p0_mult = multipliers("P0", {}, OUT_RATIO_SHRINK_K, E, stack_clamp)
    ors_mult, _ = ors.combined_multipliers(e_row, e_nwo, e_nw, e_ratios, n_t, 3, OUT_RATIO_SHRINK_K,
                                           None, stack_clamp)
    if not np.allclose(p0_mult, ors_mult, atol=1e-12):
        raise AssertionError("P0 diverges from out_redistribution_sweep's m3_k80 row")
    print(f"  P0_k{OUT_RATIO_SHRINK_K} == out_redistribution_sweep m3_k{OUT_RATIO_SHRINK_K}_capnone: OK")

    season_masks = {s: seasons == s for s in season_list}
    ref_sae = {s: np.abs(ref - actual)[m].sum(axis=0) for s, m in season_masks.items()}
    counts = {s: int(m.sum()) for s, m in season_masks.items()}

    def sae_by_season(mult):
        err = np.abs(ref * mult - actual)
        return np.stack([err[season_masks[s]].sum(axis=0) for s in season_list])   # (seasons, 9)

    def train_relmae(sae, train):
        num = sum(sae[season_list.index(s)] for s in train)
        den = sum(ref_sae[s] for s in train)
        return (num / den).mean(), num / den

    # ---- fit every family x k, leave-one-season-out ---------------------
    print("Fitting priors (grid search, leave-one-season-out) ...")
    folds = {f"holdout_{s}": [x for x in season_list if x != s] for s in season_list}
    folds["fit_all"] = list(season_list)
    thetas = {}      # (fam, k, fold) -> theta
    train_score = {}  # (fam, k, fold) -> training pooled rel-MAE
    for fam in FAMILIES:
        t0 = time.time()
        for k in K_VALUES:
            if fam in FITTED:
                grid = grid_thetas(fam)
                sae = np.stack([sae_by_season(multipliers(fam, th, k, E, stack_clamp)) for th in grid])
                for fold, train in folds.items():
                    rel = sum(sae[:, season_list.index(s)] for s in train) / sum(ref_sae[s] for s in train)
                    # prefer the most neutral value among exact ties: grid order is ascending, so
                    # break ties by |param distance from neutral|
                    neutral_dist = np.array([abs(th.get("m", 1.0) - 1.0) + abs(th.get("beta", 0.0))
                                             for th in grid])
                    pick = np.lexsort((np.broadcast_to(neutral_dist[:, None], rel.shape), rel), axis=0)[0]
                    thetas[(fam, k, fold)] = per_stat_theta(grid, pick)
                    train_score[(fam, k, fold)] = rel[pick, np.arange(NS)].mean()
            else:
                for fold, train in folds.items():
                    if fam in ("P1_mean", "P1_median"):
                        in_train = np.isin(seasons[E.row_idx], train) & E.applied
                        vals = E.ratios[in_train]
                        m = vals.mean(axis=0) if fam == "P1_mean" else np.median(vals, axis=0)
                        th = {"m": m}
                    else:
                        th = {}
                    thetas[(fam, k, fold)] = th
                    sae = sae_by_season(multipliers(fam, th, k, E, stack_clamp))
                    train_score[(fam, k, fold)] = train_relmae(sae, train)[0]
        print(f"  {fam}: {time.time() - t0:.0f}s")

    # ---- out-of-sample predictions --------------------------------------
    preds = {}
    kpick_choice = {}
    for fam in FAMILIES:
        kpick_mult = np.ones((n_t, NS))
        for k in K_VALUES:
            mult = np.ones((n_t, NS))
            for s in season_list:
                m = multipliers(fam, thetas[(fam, k, f"holdout_{s}")], k, E, stack_clamp)
                mult[season_masks[s]] = m[season_masks[s]]
            preds[f"{fam}_k{kname(k)}"] = (fam, k, ref * mult)
        for s in season_list:
            fold = f"holdout_{s}"
            kbest = min(K_VALUES, key=lambda kk: train_score[(fam, kk, fold)])
            kpick_choice[(fam, s)] = kbest
            m =multipliers(fam, thetas[(fam, kbest, fold)], kbest, E, stack_clamp)
            kpick_mult[season_masks[s]] = m[season_masks[s]]
        preds[f"{fam}_kpick"] = (fam, "pick", ref * kpick_mult)
        if fam in FITTED:
            print(f"  {fam} k picked in-fold: " + ", ".join(
                f"{s}->{kname(kpick_choice[(fam, s)])}" for s in season_list))
    ref_name = "no_adjustment"
    p0_name = f"P0_k{OUT_RATIO_SHRINK_K}"
    # artefact control: P0_k80 x a flat per-stat scale fitted (LOSO) on UNTREATED cases only
    u_ref_all, u_act_all = ref_all[~treated], actual_all[~treated]
    u_season = np.array([c["season"] for c in cases])[~treated]
    scale_mult = np.ones((n_t, NS))
    for s in season_list:
        tr_u = u_season != s
        sae_c = np.stack([np.abs(u_ref_all[tr_u] * c - u_act_all[tr_u]).sum(axis=0) for c in SCALE_GRID])
        scale_mult[season_masks[s]] = SCALE_GRID[sae_c.argmin(axis=0)]
    preds[f"{p0_name}_xscale"] = ("P0", "xscale", preds[p0_name][2] * scale_mult)
    if not np.allclose(preds[p0_name][2], ref * p0_mult):
        raise AssertionError("P0 OOS assembly broken")

    # ---- per-player metrics ---------------------------------------------
    subsets = {"all_treated": np.ones(n_t, bool), "single_out": n_absent == 1, "multi_out": n_absent >= 2}
    for s in season_list:
        subsets[f"season_{s}"] = season_masks[s]
    rows, norms = [], {}
    variants = [(ref_name, "reference", None, ref)] + [(n, f, k, p) for n, (f, k, p) in preds.items()]
    for sub, sel in subsets.items():
        scale = np.abs(ref[sel] - actual[sel]).mean(axis=0)
        norms[sub] = {}
        for name, fam, k, p in variants:
            err = p[sel] - actual[sel]
            ae = np.abs(err)
            norms[sub][name] = (ae / scale).mean(axis=1)
            common = {"scope": "player", "subset": sub, "config": name, "family": fam,
                      "k": k if k is None or isinstance(k, str) else kname(k), "n": int(sel.sum())}
            for si, col in enumerate(STATS):
                rows.append({**common, "stat": col, "mae": ae[:, si].mean(), "bias": err[:, si].mean(),
                             "rel_mae_vs_no_adjustment": ae[:, si].mean() / scale[si]})
            rows.append({**common, "stat": "POOLED", "mae": np.nan, "bias": np.nan,
                         "rel_mae_vs_no_adjustment": norms[sub][name].mean()})
    res = pd.DataFrame(rows)
    pooled = res[(res["stat"] == "POOLED")].pivot(index="config", columns="subset",
                                                   values="rel_mae_vs_no_adjustment")
    cols = ["all_treated", "single_out", "multi_out"] + [f"season_{s}" for s in season_list]
    pooled = pooled[cols]

    print("\n=== Pooled rel-MAE vs no adjustment, OUT OF SAMPLE (<1 = helps) ===")
    grid_tab = pd.DataFrame({fam: {kname(k): pooled.loc[f"{fam}_k{kname(k)}", "all_treated"]
                                   for k in K_VALUES} | {"pick": pooled.loc[f"{fam}_kpick", "all_treated"]}
                             for fam in FAMILIES}).T
    print("all_treated, family x k:")
    print(grid_tab.round(4).to_string())
    print("\nsingle_out, family x k:")
    print(pd.DataFrame({fam: {kname(k): pooled.loc[f"{fam}_k{kname(k)}", "single_out"] for k in K_VALUES}
                        for fam in FAMILIES}).T.round(4).to_string())
    print("\nmulti_out, family x k:")
    print(pd.DataFrame({fam: {kname(k): pooled.loc[f"{fam}_k{kname(k)}", "multi_out"] for k in K_VALUES}
                        for fam in FAMILIES}).T.round(4).to_string())

    fixed_k = pooled.loc[[n for n in pooled.index if n != ref_name and not n.startswith("P0")
                          and not n.endswith("_kpick")]]
    print("\nArtefact control, P0_k80 x flat per-stat scale fitted on untreated training-season cases:")
    print(pooled.loc[[p0_name, f"{p0_name}_xscale"]].round(4).to_string())
    best_name = fixed_k["all_treated"].idxmin()
    best_pos_name = fixed_k.loc[[n for n in fixed_k.index if "_pos" in n or n.startswith("P2c")],
                                "all_treated"].idxmin()
    bests = {}
    for label, name in [("best", best_name), ("best_pos", best_pos_name)]:
        fam, k, _ = preds[name]
        bests[label] = (name, fam, k, f"{fam}_kpick")
    show = [ref_name, p0_name, f"P0_k{kname(0)}", best_name, bests["best"][3], best_pos_name,
            bests["best_pos"][3]] + list(fixed_k.sort_values("all_treated").index[1:8])
    print(f"\nBest fixed (prior, k) by OOS all_treated: {best_name}; best with the prior constrained "
          f"to redistribution direction (m >= 1 / beta >= 0): {best_pos_name}")
    print(pooled.loc[list(dict.fromkeys(show))].round(4).to_string())

    print("\nPaired cluster bootstrap (player-season), 95% CI, pooled rel-MAE difference:")
    boot_rng = np.random.default_rng(sks.BOOTSTRAP_SEED)
    for sub in ["all_treated", "single_out", "multi_out"] + [f"season_{s}" for s in season_list]:
        sel = subsets[sub]
        print(f"  [{sub}, n={int(sel.sum())}]")
        d, lo, hi = cluster_bootstrap_diff(np.column_stack([norms[sub][p0_name], norms[sub][ref_name]]),
                                           clusters[sel], 0, 1, boot_rng)
        print(f"    {p0_name} - no_adjustment: {d:+.4f}  [{lo:+.4f}, {hi:+.4f}]")
        for name in list(dict.fromkeys([best_name, bests["best"][3], best_pos_name, bests["best_pos"][3],
                                        "P2s_pos_k80", "P2a_pos_k80", "P2m_k80", f"{p0_name}_xscale"])):
            per_case = np.column_stack([norms[sub][name], norms[sub][p0_name]])
            d, lo, hi = cluster_bootstrap_diff(per_case, clusters[sel], 0, 1, boot_rng)
            print(f"    {name} - {p0_name}: {d:+.4f}  [{lo:+.4f}, {hi:+.4f}]")

    print("\nForward-chaining check (fit on strictly earlier seasons, score the next):")
    for name, fam, k, _ in bests.values():
        for i, s in enumerate(season_list[1:], start=1):
            train = season_list[:i]
            sel = season_masks[s]
            scale = np.abs(ref[sel] - actual[sel]).mean(axis=0)
            th = fit_on(fam, k, train, E, stack_clamp, sae_by_season, ref_sae, season_list, seasons)
            p = ref * multipliers(fam, th, k, E, stack_clamp)
            rel = (np.abs(p[sel] - actual[sel]) / scale).mean()
            rel0 = (np.abs(preds[p0_name][2][sel] - actual[sel]) / scale).mean()
            print(f"  train {[str(x) for x in train]} -> {s}: {name} {rel:.4f} vs {p0_name} {rel0:.4f}")

    show_cfg = [ref_name, p0_name, best_name, best_pos_name]
    print("\nPer-stat, all treated (MAE / bias / rel-MAE), OOS:")
    sub = res[(res["subset"] == "all_treated") & (res["stat"] != "POOLED") & res["config"].isin(show_cfg)]
    print(sub.pivot(index="stat", columns="config",
                    values=["mae", "bias", "rel_mae_vs_no_adjustment"]).loc[STATS].round(4).to_string())
    for sub_name in ["single_out", "multi_out"]:
        sub = res[(res["subset"] == sub_name) & (res["stat"] != "POOLED") & res["config"].isin(show_cfg[1:])]
        print(f"\nPer-stat rel-MAE / bias, {sub_name}:")
        print(sub.pivot(index="stat", columns="config",
                        values=["rel_mae_vs_no_adjustment", "bias"]).loc[STATS].round(4).to_string())
    print("\nPer-stat best family/k (OOS all_treated rel-MAE, fixed k, non-P0):")
    ps_tab = res[(res["subset"] == "all_treated") & (res["stat"] != "POOLED")
                 & res["config"].isin(fixed_k.index)]
    for col in STATS:
        r = ps_tab[ps_tab["stat"] == col].sort_values("rel_mae_vs_no_adjustment").iloc[0]
        rp = ps_tab[(ps_tab["stat"] == col) & ps_tab["config"].str.contains("_pos")] \
            .sort_values("rel_mae_vs_no_adjustment").iloc[0]
        p0r = res[(res["subset"] == "all_treated") & (res["stat"] == col) & (res["config"] == p0_name)]
        print(f"  {col:5s} {r['config']:18s} {r['rel_mae_vs_no_adjustment']:.4f} | constrained "
              f"{rp['config']:18s} {rp['rel_mae_vs_no_adjustment']:.4f} "
              f"(P0_k80 {p0r['rel_mae_vs_no_adjustment'].iloc[0]:.4f})")

    # MAE-vs-median artefact control: a flat per-stat scale on UNTREATED cases
    print("\nControl -- flat per-stat scale c applied to UNTREATED cases (nobody out); if the MAE-optimal "
          "c < 1 here too, a sub-1 prior on treated cases is the skewed-count median effect, not "
          "redistribution:")
    u = ~treated
    u_ref, u_act = ref_all[u], actual_all[u]
    u_scale = np.abs(u_ref - u_act).mean(axis=0)
    u_rel = np.stack([(np.abs(u_ref * c - u_act)).mean(axis=0) / u_scale for c in P1_GRID])
    fit_all_m = thetas[("P1_fit", np.inf, "fit_all")]["m"]
    for si, col in enumerate(STATS):
        j = u_rel[:, si].argmin()
        at_m = (np.abs(u_ref[:, si] * fit_all_m[si] - u_act[:, si])).mean() / u_scale[si]
        print(f"  {col:5s} untreated-optimal c={P1_GRID[j]:.4f} (rel-MAE {u_rel[j, si]:.4f}); "
              f"treated P1_fit_kinf m={fit_all_m[si]:.4f} -> on untreated rel-MAE {at_m:.4f}")

    # ---- coefficients ----------------------------------------------------
    coef_rows = []
    for (fam, k, fold), th in thetas.items():
        if not th:
            continue
        for si, col in enumerate(STATS):
            coef = th["m"][si] if "m" in th else th["beta"][si]
            coef_rows.append({"scope": "coef", "subset": fold, "config": f"{fam}_k{kname(k)}",
                              "family": fam, "k": kname(k), "stat": col, "coef": float(coef),
                              "gamma": float(th["gamma"][si]) if "gamma" in th else np.nan})
    coefs = pd.DataFrame(coef_rows)
    for name, fam, k, _ in bests.values():
        print(f"\nFitted constants for {name} (coef = m for P1, beta for P2/P3):")
        cb = coefs[coefs["config"] == name]
        print(cb.pivot(index="stat", columns="subset", values="coef").loc[STATS].round(3).to_string())
        if fam.startswith("P3"):
            print(cb.pivot(index="stat", columns="subset", values="gamma").loc[STATS].to_string())
    for extra in ["P2s_k80", "P2m_k80", "P1_fit_k80", "P2s_pos_k80", "P2m_pos_k80", "P1_fit_pos_k80",
                  "P2a_pos_k80", "P2s_comb_pos_k80"]:
        ce = coefs[(coefs["config"] == extra) & (coefs["subset"] == "fit_all")]
        print(f"  {extra} fit_all: " + ", ".join(f"{r.stat}={r.coef:.3f}" for r in ce.itertuples()))

    # ---- team-total conservation ----------------------------------------
    print("\n=== Team-total conservation ===")
    team_names = list(dict.fromkeys([ref_name, p0_name, best_name, bests["best"][3], best_pos_name,
                                     bests["best_pos"][3], "P2s_pos_k80", "P2a_pos_k80", "P2s_pos_kinf",
                                     "P2c_k80", "P2c_kinf"]))
    specs = {}
    for name in team_names:
        if name == ref_name:
            specs[name] = {s: ("P0", np.inf, {}) for s in season_list}
        else:
            fam, k, _ = preds[name]
            specs[name] = {}
            for s in season_list:
                kk = kpick_choice[(fam, s)] if k == "pick" else k
                specs[name][s] = (fam, kk, thetas[(fam, kk, f"holdout_{s}")])
    case_preds = {name: (ref if name == ref_name else preds[name][2]) for name in team_names}
    team_rows = team_totals(cases, t_index, ref_all, actual_all, defense_all, boxes, entries,
                            case_preds, specs, stack_clamp)

    out = pd.concat([res, pd.DataFrame(team_rows), coefs], ignore_index=True)
    out = out[["scope", "subset", "config", "family", "k", "stat", "n", "mae", "bias",
               "rel_mae_vs_no_adjustment", "coef", "gamma"]]
    out.to_csv(OUTPUT_PATH, index=False)
    print(f"\nWrote {len(out)} rows to {OUTPUT_PATH} ({time.time() - t_start:.0f}s).")
    print("SCOPE: out_redistribution x opponent_defense x season baseline, treated = >= 1 absent key "
          "teammate who played again later; priors fitted leave-one-season-out -- see module docstring.")


def fit_on(fam, k, train, E, stack_clamp, sae_by_season, ref_sae, season_list, seasons):
    if fam in UNFITTED:
        if fam in ("P1_mean", "P1_median"):
            in_train = np.isin(seasons[E.row_idx], train) & E.applied
            vals = E.ratios[in_train]
            return {"m": vals.mean(axis=0) if fam == "P1_mean" else np.median(vals, axis=0)}
        return {}
    grid = grid_thetas(fam)
    sae = np.stack([sae_by_season(multipliers(fam, th, k, E, stack_clamp)) for th in grid])
    rel = sum(sae[:, season_list.index(s)] for s in train) / sum(ref_sae[s] for s in train)
    return per_stat_theta(grid, rel.argmin(axis=0))


def team_totals(cases, t_index, ref_all, actual_all, defense_all, boxes, entries,
                case_preds, specs, stack_clamp):
    """Team-sum errors per team-game (see module docstring). Returns tidy rows."""
    absent_of = {}
    for e in entries:
        absent_of.setdefault(e[0], []).append(e[5][1])
    groups = {}
    for i, c in enumerate(cases):
        if c["team_id"] is not None and c["date"] is not None:
            groups.setdefault((c["season"], c["game_id"], c["team_id"]), []).append(i)

    tg_keys = list(groups)
    n_tg = len(tg_keys)
    tg_season = np.array([k[0] for k in tg_keys])
    tg_treated = np.zeros(n_tg, bool)
    team_actual = np.zeros((n_tg, NS))
    case_actual = np.zeros((n_tg, NS))
    case_ref = np.zeros((n_tg, NS))
    n_case_players = np.zeros(n_tg)
    ext_tg, ext_ref, ext_ps, ext_entries = [], [], [], []   # non-case players
    n_no_prior = 0
    for g, key in enumerate(tg_keys):
        season, game_id, team_id = key
        rows = groups[key]
        tb = boxes[season]
        date = cases[rows[0]]["date"]
        n_abs = {cases[i]["n_absent"] for i in rows}
        if len(n_abs) != 1:
            raise AssertionError(f"inconsistent treatment within team-game {key}")
        absent = sorted(set(absent_of.get(rows[0], [])))
        tg_treated[g] = bool(absent)
        box = tb.team_game[(game_id, team_id)]
        team_actual[g] = box[STATS].sum().values
        case_actual[g] = actual_all[rows].sum(axis=0)
        case_ref[g] = ref_all[rows].sum(axis=0)
        n_case_players[g] = len(rows)
        case_players = {cases[i]["player_id"] for i in rows}
        team_mean, _ = tb.profile(team_id, date)
        shares = [tb.shares(team_id, date, a) for a in absent]
        for p in box["person_id"]:
            if p in case_players:
                continue
            avg, n_prior = tb.season_avg_before(p, date)
            n_no_prior += n_prior == 0
            ext_tg.append(g)
            ext_ref.append(avg * defense_all[rows[0]])
            if absent:
                ext_ps.append(avg / team_mean[STATS].values.astype(float))
                ext_entries.append((len(ext_ps) - 1, shares))
    ext_tg = np.array(ext_tg)
    ext_ref = np.array(ext_ref)
    ext_ref_sum = np.zeros((n_tg, NS))
    np.add.at(ext_ref_sum, ext_tg, ext_ref)

    # prior-alone multipliers for the non-case players of treated team-games
    ext_treated_rows = np.flatnonzero(tg_treated[ext_tg])
    e_row, e_astat, e_amin, e_aabs = [], [], [], []
    for r, shares in ext_entries:
        for a_stat, a_min, a_abs in shares:
            e_row.append(r)
            e_astat.append(a_stat)
            e_amin.append(a_min)
            e_aabs.append(a_abs)
    n_e = len(e_row)
    E_ext = Entries(np.array(e_row), np.zeros(n_e), np.zeros(n_e), np.ones((n_e, NS)),
                    np.array(e_astat), np.array(e_amin), np.array(e_aabs), np.nan_to_num(np.array(ext_ps)), len(ext_ps))

    print(f"  team-games with >= 1 case-set player: {n_tg} ({tg_treated.sum()} treated); "
          f"case-set players per team-game {n_case_players.mean():.2f}, other players who played "
          f"{len(ext_tg) / n_tg:.2f} ({n_no_prior} player-games with no prior box score -> 0)")
    tr, ut = tg_treated, ~tg_treated
    rows_out = []

    def emit(scope, name, sel, err):
        for si, col in enumerate(STATS):
            rows_out.append({"scope": scope, "subset": "treated_team_games" if scope != "team_control_untreated"
                             else "untreated_team_games", "config": name, "stat": col, "n": int(sel.sum()),
                             "mae": np.abs(err[sel, si]).mean(), "bias": err[sel, si].mean()})

    emit("team_control_untreated", "no_adjustment", ut, case_ref + ext_ref_sum - team_actual)
    ctrl = case_ref + ext_ref_sum - team_actual
    print(f"  control (untreated team-games, extended, no adjustment) mean error: " + ", ".join(
        f"{c} {ctrl[ut, STATS.index(c)].mean():+.2f}" for c in TEAM_STATS_PRINT))
    table = []
    for name, cp in case_preds.items():
        # replace treated case rows' reference with this variant's prediction
        diff = np.zeros((n_tg, NS))
        for g, key in enumerate(tg_keys):
            if tg_treated[g]:
                idx = t_index[groups[key]]
                diff[g] = (cp[idx] - ref_all[groups[key]]).sum(axis=0)
        case_sum = case_ref + diff
        ext_mult = np.ones((len(ext_ps), NS))
        for s, (fam, k, th) in specs[name].items():
            m = multipliers(fam, th, np.inf, E_ext, stack_clamp)
            rows_s = np.flatnonzero(tg_season[ext_tg[ext_treated_rows]] == s)
            ext_mult[rows_s] = m[rows_s]
        ext_adj = ext_ref.copy()
        ext_adj[ext_treated_rows] = ext_ref[ext_treated_rows] * ext_mult
        ext_prior_sum = np.zeros((n_tg, NS))
        np.add.at(ext_prior_sum, ext_tg, ext_adj)
        errs = {"team_case_only": case_sum - case_actual,
                "team_extended": case_sum + ext_ref_sum - team_actual,
                "team_extended_prior": case_sum + ext_prior_sum - team_actual}
        line = {"config": name}
        for scope, err in errs.items():
            emit(scope, name, tr, err)
            for c in TEAM_STATS_PRINT:
                line[f"{scope.replace('team_', '')}:{c}"] = err[tr, STATS.index(c)].mean()
        table.append(line)
    print("  mean team-sum error (pred - actual) on treated team-games:")
    print(pd.DataFrame(table).set_index("config").round(2).T.to_string())
    ref_rows = {(r["scope"], r["stat"]): r["mae"] for r in rows_out if r["config"] == "no_adjustment"}
    for r in rows_out:
        r["family"] = None
        r["k"] = None
        r["rel_mae_vs_no_adjustment"] = r["mae"] / ref_rows[(r["scope"], r["stat"])]
    return rows_out


if __name__ == "__main__":
    main()
