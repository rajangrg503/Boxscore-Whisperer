"""
Point-in-time backtest of the Full Matchup "player marked out"
adjustment (engine/adjustments/teammates.py
get_out_redistribution_adjustment + app.py combine_out_redistributions)
-- does it help at all, and which minimum sample / shrinkage / cap makes
it help most?

WHY THIS EXISTS: the out-redistribution ratio is
mean(games without the out player) / mean(all games), per stat, from
as few as 3 "without" games, with no cap on a single player's ratio
(OUT_STACK_CLAMP only binds when 2+ out players stack). Live examples
look alarming -- Jalen Williams PTS x1.52 from 4 games without SGA,
Jokic STL x1.52 / BLK x0.29 from 5 games without Murray. Nothing in
run_backtest.py exercises this layer, so whether it beats "no
adjustment at all" has never been measured.

WHAT IT TESTS -- the exact production math:
  * Per absent teammate, the ratio is get_out_redistribution_adjustment's
    formula, computed point-in-time: player_df = the player's cached
    gamelog rows strictly before the target date (the same frame
    point_in_time_baseline() averages), and the out player's game set =
    games strictly before the target date in which he logged > 0
    minutes (any team), from data_cache/boxscore_*.json. A sample of
    entries is re-run through the REAL function (its
    fetch_combined_game_log monkeypatched to that point-in-time frame)
    and asserted equal; a second sample swaps in the out player's own
    cached gamelog (date-filtered) to check box-score membership equals
    gamelog membership.
  * Several absent teammates are folded with app.py's
    combine_out_redistributions, exec'd from app.py's source via `ast`
    (same trick as shrinkage_k_sweep.py) and asserted equal to the
    vectorised version on a sample of 2+-out cases.
  * Baseline x defense is point_in_time_baseline x
    get_defense_adjustment(point-in-time rating), i.e.
    build_backtest_row(); the no-adjustment reference is cross-checked
    against backtest_results.csv's *_predicted.

CASE SELECTION (read before quoting a number):
  * run_backtest.py's case set (3 seasons, top-150 by minutes, regular
    season, >= MIN_BASELINE_GAMES prior games, opponent resolvable).
  * "Key teammate" of player P on team T before date d: >= 10 games
    played for T this season before d, averaging >= 24.0 minutes in
    those games (box-score minutes > 0 counts as played).
  * A case is TREATED when >= 1 key teammate did not play in the
    target game (absent from the box score or 0 minutes) AND played
    for T again at a later date that season. The "played again later"
    filter uses future information for CASE SELECTION only -- it
    stands in for what a live user knows when marking someone out
    (injured/resting, not traded). It never enters a prediction.
  * Every treated case is scored under every variant; where a
    variant's sample thresholds aren't met the prediction is simply
    the reference (the app shows the unadjusted number then), so all
    variants are compared on the same cases.
  * This is the "user marks exactly the absent key teammates" scenario.
    Real users may mark bench players out, or miss someone; neither is
    simulated. Absent non-key teammates are ignored.

VARIANTS (per absent teammate i, raw ratio r_i, n_i "without" games):
  * reference "no_adjustment": baseline x defense.
  * grid: min_sample m (without-side; with-side stays >= 3) x
    shrinkage k, r' = 1 + (r - 1) * n / (n + k) x per-player cap
    (none, 0.75-1.35, 0.85-1.20); then the production stack rule
    (product, clamped to OUT_STACK_CLAMP only when 2+ contribute).
    "production" here = the ORIGINAL layer (m=3, k=0, cap=none). This
    sweep's result set production to k=80 (teammates.OUT_RATIO_SHRINK_K),
    so today's app corresponds to the m=3, k=80 grid row; the
    verification step shrinks the reference ratio by that constant
    before comparing. k goes past the requested 0-20
    because rel-MAE was still falling at 20; it turns between 80 and 160.

METRICS: same conventions as shrinkage_k_sweep.py -- per stat MAE,
pooled rel-MAE = mean over the 9 stats of |err| / reference MAE
(< 1 beats no adjustment), mean signed error (bias), and a paired
cluster bootstrap (whole player-seasons resampled) for CIs. The best
config is picked in-sample over the grid; a leave-one-season-out pick
is printed to show how much that selection flatters it. Subsets:
all_treated, single_out (exactly 1 absent key teammate), multi_out
(2+), prod_applied (cases whose prediction production actually moved),
and each season.

LIMITS: cached box scores/gamelogs start in 2023-24, so every ratio is
same-season only (as in production); minutes of the target game are
not used; the with/without split ignores who ELSE was missing in the
"without" games (production does the same, which double-counts
teammates who are often out together). Diagnostics printed but not
"fixed" here because production does the same: a traded player's games
for his previous team all count as "without" games, and a stat whose
"without" mean is 0 gets a ratio of exactly 0 (the prediction becomes 0).

Usage:
    python3 out_redistribution_sweep.py
Writes out_redistribution_sweep_results.csv (tidy: subset, config,
min_sample, k, cap, stat, n_cases, n_moved, mae, bias,
rel_mae_vs_no_adjustment).
"""

import ast
import glob
import json
import os
import time
from functools import lru_cache

import numpy as np
import pandas as pd

# Importing the k sweep installs its offline import stubs (a no-op where
# streamlit/nba_api are importable) and memoises bpit._load_df_cache.
import shrinkage_k_sweep as sks  # noqa: E402
from shrinkage_k_sweep import (  # noqa: E402
    REPO_ROOT,
    _memo_load,
    cluster_bootstrap_diff,
    defense_for,
    validate_season_only_against_published,
)
import engine.adjustments.teammates as teammates_mod  # noqa: E402
from engine.adjustments.base import AdjustmentResult  # noqa: E402
from engine.adjustments.teammates import (  # noqa: E402
    OUT_REDISTRIBUTION_LAYER,
    get_out_redistribution_adjustment,
)
from engine.backtest_point_in_time import (  # noqa: E402
    MIN_BASELINE_GAMES,
    _is_regular_season_game_id,
    get_point_in_time_opponent_defense,
    point_in_time_baseline,
)
from engine.stat_columns import STAT_COLUMNS  # noqa: E402
from engine.team_ids import TEAM_ID_BY_ABBR  # noqa: E402
from run_backtest import SEASONS, load_fixed_player_list  # noqa: E402

APP_PATH = os.path.join(REPO_ROOT, "app.py")
CACHE_DIR = os.path.join(REPO_ROOT, "data_cache")
OUTPUT_PATH = os.path.join(REPO_ROOT, "out_redistribution_sweep_results.csv")
STATS = [col for col, _ in STAT_COLUMNS]

KEY_MIN_GAMES = 10
KEY_MIN_MPG = 24.0
PRODUCTION_MIN_WITH = 3
MIN_SAMPLES = [3, 5, 8, 12]
K_VALUES = [0, 3, 5, 10, 20, 40, 80, 160, 320]   # 40+ added: the requested 0-20 range never turned
CAPS = {"none": None, "0.75-1.35": (0.75, 1.35), "0.85-1.20": (0.85, 1.20)}
PRODUCTION = (3, 0, "none")
BAND = (0.75, 1.35)
VERIFY_EVERY = 25          # every Nth teammate entry goes through the real function
SEED = 20260917


# ---- production code, pulled from app.py's source ------------------------
def load_production_combine():
    """app.py's combine_out_redistributions + OUT_STACK_CLAMP +
    _DATA_QUALITY_RANK, compiled from app.py's own source text."""
    with open(APP_PATH) as f:
        source = f.read()
    tree = ast.parse(source)
    wanted_assign = {"OUT_STACK_CLAMP", "_DATA_QUALITY_RANK"}
    chunks = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in wanted_assign for t in node.targets):
            chunks.append(ast.get_source_segment(source, node))
        elif isinstance(node, ast.FunctionDef) and node.name == "combine_out_redistributions":
            if node.decorator_list:
                raise RuntimeError("combine_out_redistributions gained a decorator -- re-check extraction")
            chunks.append(ast.get_source_segment(source, node))
    if len(chunks) != 3:
        raise RuntimeError(f"expected 3 top-level definitions from app.py, found {len(chunks)}")
    namespace = {"STAT_COLUMNS": STAT_COLUMNS, "AdjustmentResult": AdjustmentResult,
                 "OUT_REDISTRIBUTION_LAYER": OUT_REDISTRIBUTION_LAYER}
    exec(compile("\n\n".join(chunks), APP_PATH, "exec"), namespace)
    return namespace["combine_out_redistributions"], tuple(namespace["OUT_STACK_CLAMP"])


# ---- box scores ------------------------------------------------------------
def _minutes(text):
    if not text:
        return 0.0
    if ":" in text:
        mm, ss = text.split(":")
        return int(mm) + int(ss) / 60.0
    return float(text)


def season_prefix(season):
    return f"002{season[2:4]}"


def load_season_boxscores(season):
    """(appearances df [game_id, date, team_id, person_id, minutes>0],
    game_date map). Dates come from every cached gamelog of the season
    (box scores carry none)."""
    dates = {}
    for path in glob.glob(os.path.join(CACHE_DIR, f"gamelog_*_{season}.json")):
        with open(path) as f:
            for r in json.load(f)["data"]:
                dates.setdefault(str(r["Game_ID"]), r["GAME_DATE"])
    rows = []
    n_files = 0
    for path in sorted(glob.glob(os.path.join(CACHE_DIR, f"boxscore_{season_prefix(season)}*.json"))):
        n_files += 1
        with open(path) as f:
            for r in json.load(f)["data"]:
                m = _minutes(r["minutes"])
                if m > 0:
                    rows.append((str(r["gameId"]), r["teamId"], str(r["personId"]), m))
    app = pd.DataFrame(rows, columns=["game_id", "team_id", "person_id", "minutes"])
    date_map = {g: pd.Timestamp(d) for g, d in dates.items()}
    app["date"] = app["game_id"].map(date_map)
    undated = app.loc[app["date"].isna(), "game_id"].nunique()
    print(f"  {season}: {n_files} box scores, {app['game_id'].nunique()} with players, "
          f"{undated} without a date from cached gamelogs (dropped)")
    return app.dropna(subset=["date"]).reset_index(drop=True)


class SeasonIndex:
    def __init__(self, season):
        self.season = season
        self.app = load_season_boxscores(season)
        self.played_in = {g: set(df["person_id"]) for g, df in self.app.groupby("game_id")}
        self.team_in_game = {(g, p): t for g, p, t in
                             zip(self.app["game_id"], self.app["person_id"], self.app["team_id"])}
        self.by_team = {t: df.sort_values("date") for t, df in self.app.groupby("team_id")}
        self.last_date = self.app.groupby(["team_id", "person_id"])["date"].max().to_dict()
        self.person_games = {p: (df["date"].values, df["game_id"].values)
                             for p, df in self.app.groupby("person_id")}

    @lru_cache(maxsize=None)
    def key_teammates(self, team_id, date):
        df = self.by_team[team_id]
        prior = df[df["date"] < date]
        g = prior.groupby("person_id")["minutes"].agg(["size", "mean"])
        return frozenset(g.index[(g["size"] >= KEY_MIN_GAMES) & (g["mean"] >= KEY_MIN_MPG)])

    def games_before(self, person_id, date):
        dates, gids = self.person_games.get(person_id, (np.array([], dtype="datetime64[ns]"), np.array([])))
        return set(gids[dates < np.datetime64(date)])


# ---- the ratio (production formula, point in time) ------------------------
def raw_ratio(player_prior, out_game_ids):
    """Exactly get_out_redistribution_adjustment's arithmetic, without
    its sample thresholds. Returns (ratios, n_without, n_with)."""
    mask = player_prior["Game_ID"].astype(str).isin(out_game_ids).values
    without = player_prior[~mask]
    ratios = []
    for col in STATS:
        avg_without = without[col].mean()
        avg_overall = player_prior[col].mean()
        ratios.append(avg_without / avg_overall if avg_overall else 1.0)
    return np.array(ratios, dtype=float), int((~mask).sum()), int(mask.sum())


def call_production(player_id, out_id, season, player_prior, out_frame):
    orig = teammates_mod.fetch_combined_game_log
    teammates_mod.fetch_combined_game_log = lambda _pid, _season: out_frame
    try:
        return get_out_redistribution_adjustment(player_id, out_id, season, player_prior)
    finally:
        teammates_mod.fetch_combined_game_log = orig


def build():
    cases, entries, verify = [], [], []
    for season, season_start in SEASONS:
        idx = SeasonIndex(season)
        players = load_fixed_player_list(season)
        t0 = time.time()
        n_all = 0
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
                opponent_team_id = TEAM_ID_BY_ABBR.get(opponent_abbr)
                if opponent_team_id is None:
                    continue
                n_all += 1
                game_id = str(game_row["Game_ID"])
                case = {
                    "player_id": player_id, "season": season, "game_id": game_id,
                    "base": [season_stats[c][0] for c in STATS],
                    "actual": [game_row[c] for c in STATS],
                    "defense_lookup": get_point_in_time_opponent_defense(
                        season, season_start, opponent_team_id, game_date),
                    "n_absent": 0,
                }
                ts = pd.Timestamp(game_date)
                team_id = idx.team_in_game.get((game_id, player_id))
                if team_id is not None:
                    absent = [a for a in idx.key_teammates(team_id, ts)
                              if a != player_id
                              and a not in idx.played_in.get(game_id, ())
                              and idx.last_date.get((team_id, a), ts) > ts]
                    if absent:
                        prior = df[parsed < ts]
                        case["n_absent"] = len(absent)
                        for a in sorted(absent):
                            out_ids = idx.games_before(a, ts)
                            r, n_wo, n_w = raw_ratio(prior, out_ids)
                            other_team = any(idx.team_in_game.get((str(g), player_id)) != team_id
                                             for g in prior["Game_ID"])
                            entries.append((len(cases), n_wo, n_w, r, other_team,
                                            (player_id, a, game_id, str(game_date))))
                            if len(entries) % VERIFY_EVERY == 0:
                                verify.append((player_id, a, season, prior, out_ids, ts, r, n_wo, n_w))
                cases.append(case)
        print(f"  {season}: {len(players)} players, {n_all} cases, "
              f"{sum(c['n_absent'] > 0 for c in cases if c['season'] == season)} treated "
              f"({time.time() - t0:.0f}s)")
    return cases, entries, verify


def verify_against_production(verify):
    """Re-run sampled entries through the real function: with the
    box-score game set (must match exactly), and with the out player's
    date-filtered cached gamelog where one exists (membership check)."""
    exact_bad = 0
    gl_n = gl_bad = 0
    for player_id, a, season, prior, out_ids, ts, r, n_wo, n_w in verify:
        frame = pd.DataFrame({"Game_ID": sorted(out_ids)}) if out_ids else pd.DataFrame()
        res = call_production(player_id, a, season, prior, frame)
        applied = n_wo >= 3 and n_w >= 3
        # Production now shrinks each ratio (teammates.OUT_RATIO_SHRINK_K,
        # set from this sweep); the unshrunk ratio is this script's
        # "production" (k=0) variant.
        k_prod = getattr(teammates_mod, "OUT_RATIO_SHRINK_K", 0)
        shrunk = np.array([1.0 + (x - 1.0) * n_wo / (n_wo + k_prod) if n_wo > 0 else 1.0 for x in r])
        expect = shrunk if applied else np.ones(len(STATS))
        got = np.array([res.multiplier_for(c) for c in STATS])
        if res.applied != applied or not np.allclose(got, expect, rtol=0, atol=1e-12, equal_nan=True) \
                or res.sample_n != n_wo:
            exact_bad += 1
        gl, _ = _memo_load(f"gamelog_{a}_{season}")
        if gl is not None:
            gl_n += 1
            gl_prior = gl[pd.to_datetime(gl["GAME_DATE"]) < ts]
            res2 = call_production(player_id, a, season, prior, gl_prior)
            got2 = np.array([res2.multiplier_for(c) for c in STATS])
            if res2.applied != res.applied or not np.allclose(got2, got, atol=1e-12, equal_nan=True):
                gl_bad += 1
    print(f"  real get_out_redistribution_adjustment on {len(verify)} sampled entries: "
          f"{exact_bad} mismatches vs this script's ratio")
    print(f"  box-score vs cached-gamelog out-player membership on {gl_n} of them: {gl_bad} differ")
    if exact_bad:
        raise AssertionError("re-implementation diverges from production")


def combined_multipliers(case_idx, n_wo, n_w, ratios, n_cases, min_sample, k, cap, stack_clamp):
    applied = (n_wo >= min_sample) & (n_w >= PRODUCTION_MIN_WITH)
    r = 1.0 + (ratios - 1.0) * (n_wo / (n_wo + k))[:, None] if k else ratios.copy()
    if cap is not None:
        r = np.clip(r, cap[0], cap[1])
    r[~applied] = 1.0
    mult = np.ones((n_cases, len(STATS)))
    np.multiply.at(mult, case_idx, r)
    n_contrib = np.bincount(case_idx, weights=applied, minlength=n_cases)
    stacked = n_contrib >= 2
    mult[stacked] = np.clip(mult[stacked], stack_clamp[0], stack_clamp[1])
    return mult, n_contrib


def main():
    combine, stack_clamp = load_production_combine()
    print(f"combine_out_redistributions loaded from app.py (OUT_STACK_CLAMP={stack_clamp})")
    print("Building point-in-time cases ...")
    cases, entries, verify = build()

    defense = np.array([[defense_for(c, 0.0).multiplier_for(col) for col in STATS] for c in cases])
    base_all = np.array([c["base"] for c in cases], dtype=float)
    ref_all = base_all * defense
    validate_season_only_against_published(cases, ref_all[:, None, :])
    verify_against_production(verify)

    treated = np.array([c["n_absent"] > 0 for c in cases])
    t_index = -np.ones(len(cases), dtype=int)
    t_index[treated] = np.arange(treated.sum())
    tcases = [c for c in cases if c["n_absent"] > 0]
    n_t = len(tcases)
    case_idx = t_index[np.array([e[0] for e in entries])]
    n_wo = np.array([e[1] for e in entries], dtype=float)
    n_w = np.array([e[2] for e in entries], dtype=float)
    ratios = np.vstack([e[3] for e in entries])
    ref = ref_all[treated]
    actual = np.array([c["actual"] for c in tcases], dtype=float)
    n_absent = np.array([c["n_absent"] for c in tcases])
    seasons = np.array([c["season"] for c in tcases])
    clusters = np.array([f"{c['player_id']}_{c['season']}" for c in tcases])

    print(f"\n=== Coverage: {len(cases)} cases, {n_t} treated ({n_t / len(cases):.1%}) ===")
    cov = pd.DataFrame({"season": seasons, "n_absent": n_absent})
    tab = cov.groupby("season").agg(treated=("n_absent", "size"),
                                    single_out=("n_absent", lambda s: int((s == 1).sum())),
                                    multi_out=("n_absent", lambda s: int((s >= 2).sum())))
    tab.insert(0, "all_cases", pd.Series([c["season"] for c in cases]).value_counts().sort_index())
    print(tab.to_string())
    prod_applied = (n_wo >= 3) & (n_w >= 3)
    print(f"  absent-teammate entries: {len(entries)}; production thresholds (3/3) met by "
          f"{prod_applied.sum()} ({prod_applied.mean():.1%}); failing on with-side only: "
          f"{((n_wo >= 3) & (n_w < 3)).sum()}")
    other_team = np.array([e[4] for e in entries])
    print(f"  entries where the player's prior games include games for ANOTHER team (traded; those "
          f"all count as 'without'): {other_team.sum()} ({other_team.mean():.1%}), "
          f"{(other_team & prod_applied).sum()} of them meeting 3/3")
    zero = prod_applied & (ratios == 0).any(axis=1)
    print(f"  entries meeting 3/3 with at least one stat ratio exactly 0 (prediction forced to 0): "
          f"{zero.sum()} ({zero.sum() / prod_applied.sum():.1%}); by stat: " + ", ".join(
              f"{c}={int((ratios[prod_applied][:, s] == 0).sum())}" for s, c in enumerate(STATS)))
    for j in np.flatnonzero(prod_applied & (ratios[:, 0] < 0.3))[:5]:
        print(f"    low PTS ratio example: player/out/game/date={entries[j][5]}, "
              f"n_without={int(n_wo[j])}, n_with={int(n_w[j])}, PTS ratio={ratios[j, 0]:.3f}, "
              f"other_team={other_team[j]}")
    print("  'without' n among entries meeting 3/3: " + ", ".join(
        f"{lab}:{int(v)}" for lab, v in pd.cut(n_wo[prod_applied], [2, 4, 7, 11, 20, 90],
                                                labels=["3-4", "5-7", "8-11", "12-20", "21+"])
        .value_counts().sort_index().items()))

    print("\n=== Production single-player ratios outside the (0.75, 1.35) band (entries meeting 3/3) ===")
    pr = ratios[prod_applied]
    band_rows = []
    for s, col in enumerate(STATS):
        out = (pr[:, s] < BAND[0]) | (pr[:, s] > BAND[1])
        band_rows.append({"stat": col, "outside_%": out.mean() * 100,
                          "below_%": (pr[:, s] < BAND[0]).mean() * 100,
                          "above_%": (pr[:, s] > BAND[1]).mean() * 100,
                          "min": np.nanmin(pr[:, s]), "p05": np.nanpercentile(pr[:, s], 5),
                          "median": np.nanmedian(pr[:, s]), "p95": np.nanpercentile(pr[:, s], 95),
                          "max": np.nanmax(pr[:, s])})
    print(pd.DataFrame(band_rows).round(3).to_string(index=False))
    n_small = n_wo[prod_applied]
    for lo, hi in [(3, 4), (5, 7), (8, 11), (12, 99)]:
        sel = (n_small >= lo) & (n_small <= hi)
        if sel.any():
            print(f"    n_without {lo}-{hi}: {sel.sum()} entries, share of stat-ratios outside band "
                  f"{(((pr[sel] < BAND[0]) | (pr[sel] > BAND[1])).mean()) * 100:.1f}%")

    # ---- the grid ---------------------------------------------------------
    configs = [(m, k, cap) for m in MIN_SAMPLES for k in K_VALUES for cap in CAPS]
    preds, moved = {}, {}
    for cfg in configs:
        mult, n_contrib = combined_multipliers(case_idx, n_wo, n_w, ratios, n_t,
                                               cfg[0], cfg[1], CAPS[cfg[2]], stack_clamp)
        preds[cfg] = ref * mult
        moved[cfg] = n_contrib > 0

    # production combine check on a sample of 2+-out cases
    rng = np.random.default_rng(SEED)
    multi_cases = np.flatnonzero(n_absent >= 2)
    bad = 0
    for ci in rng.choice(multi_cases, size=min(300, len(multi_cases)), replace=False):
        results = []
        for j in np.flatnonzero(case_idx == ci):
            ok = n_wo[j] >= 3 and n_w[j] >= 3
            results.append(AdjustmentResult(
                layer=OUT_REDISTRIBUTION_LAYER,
                value=dict(zip(STATS, ratios[j])) if ok else {c: 1.0 for c in STATS},
                note="", data_quality="real_current" if ok else "unavailable",
                sample_n=int(n_wo[j]), applied=bool(ok)))
        comb = combine(results)
        got = ref[ci] * np.array([comb.multiplier_for(c) for c in STATS])
        if not np.allclose(got, preds[PRODUCTION][ci], atol=1e-12):
            bad += 1
    print(f"\n  app.py combine_out_redistributions on {min(300, len(multi_cases))} sampled 2+-out cases: "
          f"{bad} mismatches vs the vectorised production variant")
    if bad:
        raise AssertionError("stack rule diverges from production")

    subsets = {
        "all_treated": np.ones(n_t, dtype=bool),
        "single_out": n_absent == 1,
        "multi_out": n_absent >= 2,
        "prod_applied": moved[PRODUCTION],
    }
    for s in sorted(set(seasons)):
        subsets[f"season_{s}"] = seasons == s

    out_rows = []
    summaries = {}
    for sub_name, sel in subsets.items():
        a = actual[sel]
        ref_err = np.abs(ref[sel] - a)
        scale = ref_err.mean(axis=0)
        variants = [("no_adjustment", None, None, None, ref[sel], np.zeros(sel.sum(), bool))] + [
            (f"m{m}_k{k}_cap{cap}", m, k, cap, preds[(m, k, cap)][sel], moved[(m, k, cap)][sel])
            for m, k, cap in configs]
        norm = {}
        for name, m, k, cap, p, mv in variants:
            err = p - a
            abs_err = np.abs(err)
            norm[name] = (abs_err / scale).mean(axis=1)
            common = {"subset": sub_name, "config": name, "min_sample": m, "k": k, "cap": cap,
                      "n_cases": int(sel.sum()), "n_moved": int(mv.sum())}
            for s_i, col in enumerate(STATS):
                out_rows.append({**common, "stat": col, "mae": abs_err[:, s_i].mean(),
                                 "bias": err[:, s_i].mean(),
                                 "rel_mae_vs_no_adjustment": abs_err[:, s_i].mean() / scale[s_i]})
            out_rows.append({**common, "stat": "POOLED", "mae": np.nan, "bias": np.nan,
                             "rel_mae_vs_no_adjustment": norm[name].mean()})
        summaries[sub_name] = (norm, sel)

    res = pd.DataFrame(out_rows)
    res.to_csv(OUTPUT_PATH, index=False)

    pooled = res[res["stat"] == "POOLED"].pivot(index="config", columns="subset",
                                                 values="rel_mae_vs_no_adjustment")
    grid_only = pooled.drop(index="no_adjustment")
    prod_name = "m{}_k{}_cap{}".format(*PRODUCTION)
    best_name = grid_only["all_treated"].idxmin()
    print("\n=== Pooled rel-MAE vs no adjustment (<1 = adjustment helps) ===")
    cols = ["all_treated", "single_out", "multi_out", "prod_applied"] + \
           [c for c in pooled.columns if c.startswith("season_")]
    print("Production:", prod_name)
    print(pooled.loc[[prod_name]][cols].round(4).to_string())
    print("\nTop 12 configs on all treated cases:")
    print(grid_only.sort_values("all_treated")[cols].head(12).round(4).to_string())
    print("\nWorst 5 configs:")
    print(grid_only.sort_values("all_treated")[cols].tail(5).round(4).to_string())

    print("\nrel-MAE by k (best m/cap for each k), all treated:")
    g = res[(res["stat"] == "POOLED") & (res["subset"] == "all_treated") & res["k"].notna()]
    print(g.groupby("k")["rel_mae_vs_no_adjustment"].min().round(4).to_string())
    print("rel-MAE by (min_sample, cap) with k=0 (no shrinkage), all treated:")
    print(g[g["k"] == 0].pivot(index="min_sample", columns="cap",
                               values="rel_mae_vs_no_adjustment").round(4).to_string())
    print("rel-MAE by (k, cap) with min_sample=3, all treated:")
    print(g[g["min_sample"] == 3].pivot(index="k", columns="cap",
                                        values="rel_mae_vs_no_adjustment").round(4).to_string())

    # leave-one-season-out selection
    print("\nLeave-one-season-out: pick best config on the other two seasons, score on the held-out one:")
    for s in sorted(set(seasons)):
        others = seasons != s
        norm_all, _ = summaries["all_treated"]
        # rel-MAE with the held-out season's own scale is reported, the pick uses the other two
        pick = min((n for n in norm_all if n != "no_adjustment"),
                   key=lambda n: norm_all[n][others].mean())
        norm_s, _ = summaries[f"season_{s}"]
        print(f"  held out {s}: picked {pick}; held-out rel-MAE {norm_s[pick].mean():.4f} "
              f"(production {norm_s[prod_name].mean():.4f}, in-sample best {norm_s[best_name].mean():.4f})")

    print("\nPaired cluster bootstrap (player-season resampling), 95% CI, pooled rel-MAE difference:")
    boot_rng = np.random.default_rng(sks.BOOTSTRAP_SEED)
    for sub_name in ["all_treated", "single_out", "multi_out", "prod_applied"]:
        norm, sel = summaries[sub_name]
        names = ["no_adjustment", prod_name, best_name]
        per_case = np.column_stack([norm[n] for n in names])
        cl = clusters[sel]
        print(f"  [{sub_name}, n={int(sel.sum())}]")
        for a_i, b_i, label in [(1, 0, "production - no_adjustment"),
                                (2, 1, f"best ({best_name}) - production"),
                                (2, 0, "best - no_adjustment")]:
            d, lo, hi = cluster_bootstrap_diff(per_case, cl, a_i, b_i, boot_rng)
            print(f"    {label}: {d:+.4f}  [{lo:+.4f}, {hi:+.4f}]")

    print("\nPer-stat, all treated cases (MAE / bias / rel-MAE):")
    sub = res[(res["subset"] == "all_treated") & (res["stat"] != "POOLED")
              & res["config"].isin(["no_adjustment", prod_name, best_name])]
    print(sub.pivot(index="stat", columns="config",
                    values=["mae", "bias", "rel_mae_vs_no_adjustment"]).loc[STATS].round(4).to_string())
    for sub_name in ["single_out", "multi_out"]:
        print(f"\nPer-stat rel-MAE, {sub_name}:")
        sub = res[(res["subset"] == sub_name) & (res["stat"] != "POOLED")
                  & res["config"].isin([prod_name, best_name])]
        print(sub.pivot(index="stat", columns="config",
                        values="rel_mae_vs_no_adjustment").loc[STATS].round(4).to_string())

    print(f"\nWrote {len(res)} rows to {OUTPUT_PATH}.")
    print("SCOPE: out_redistribution x opponent_defense x season baseline, regular-season targets, "
          "treated = >= 1 absent key teammate who played again later -- see module docstring.")


if __name__ == "__main__":
    main()
