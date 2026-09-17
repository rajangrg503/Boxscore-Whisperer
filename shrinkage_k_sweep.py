"""
Point-in-time sweep for blend_baseline_stats()'s default shrinkage_k
(app.py) -- picks the number from data instead of intuition.

WHY THIS EXISTS: patch_shrinkage_k.py lowered the default from 8 to 4
by judgment alone. run_backtest.py can't answer the question, because
engine/backtest_point_in_time.py never calls blend_baseline_stats() --
the published backtest is season baseline x opponent_defense only,
i.e. exactly the "Season average (default)" UI path. shrinkage_k only
does anything once the user picks "Last 5/10 games vs. this opponent",
so it has never been measured at all.

WHAT IT TESTS -- the exact production math, not a re-derivation:
  * blend_baseline_stats() is pulled out of app.py's source via `ast`
    and exec'd (app.py itself can't be imported: it's a Streamlit
    script). If someone edits the function in app.py, this sweep
    tests the edited version automatically.
  * The defense layer is engine/adjustments/defense.py's
    get_defense_adjustment(), passed team_h2h_weight=the blend's
    weights["team_h2h"] exactly like app.py's tab1 does -- so a
    smaller k also shrinks the defense multiplier, and that coupling is
    part of what's being measured.
  * Season baseline, opponent-defense checkpoints, the regular-season
    filter and MIN_BASELINE_GAMES all come from
    engine/backtest_point_in_time.py; the case set is run_backtest.py's
    (3 seasons, top-150 by minutes, regular season, >= 5 prior
    in-season games, opponent resolvable).
  * The team head-to-head sample mirrors app.py's
    get_head_to_head_log()/get_head_to_head_baseline(), made
    point-in-time: the player's cached gamelogs for the target season
    plus up to 3 prior seasons (playoffs INCLUDED, as
    fetch_combined_game_log() includes them), MATCHUP containing the
    opponent abbreviation (same substring rule as the app), GAME_DATE
    strictly before the target game, most-recent-first, head(N) for
    N in {5, 10} (the two UI options).

VARIANTS: k in K_VALUES, plus two references -- "season_only" (no h2h
at all; the default UI path and the published backtest) and "raw_h2h"
(shrinkage_k=0, the "Use only this source" checkbox, which also turns
the defense layer fully off since team_h2h weight becomes 1.0).

SCOPE / LIMITS -- read before quoting any number from this:
  * Only cases with at least one prior h2h game are affected by k, so
    every metric is reported on that subset (its size is printed).
  * data_cache/ only has gamelogs from 2023-24 on, so 2023-24 targets
    get in-season h2h only (at most 3-4 meetings), 2024-25 gets one
    prior season, 2025-26 gets two. The live app always has up to
    4 seasons, so live h2h samples are fuller than most here --
    coverage is printed per target season.
  * Same scope limits as run_backtest.py: baseline + opponent_defense
    only; missing_teammates / new_teammate / missing_opponents /
    defender / scheme and vs-player "extra_sources" are not exercised.
  * k is selected on the same data it is scored on (no held-out
    season). With 5 candidates the optimism is small, but the
    per-season breakdown is printed so stability can be judged.
  * Directional accuracy uses analytics/layer_accuracy.py's definition
    (hit = the adjustment and the real outcome land on the same side
    of the unadjusted season baseline; ties on either side are not
    scored), with "the adjustment" here being h2h-blend x defense
    together. Because blended - season = w * (h2h - season), its sign
    is the same for every k > 0 -- directional accuracy can barely
    move with k by construction. It is also skew-biased: per-game
    stats are right-skewed, so the outcome lands BELOW the season mean
    more often than above, and a small-sample h2h mean tends to sit
    below the season mean too -- a blend "wins" directionally partly
    by leaning down. The summary prints an "always predict below the
    season average" reference on the same scored cells for that
    reason. MAE is the metric that decides k.

Usage:
    python3 shrinkage_k_sweep.py
Writes shrinkage_k_sweep_results.csv (tidy: N, k, stat, n_cases, mae,
directional_acc, directional_n, rel_mae_vs_season_only).
"""

import ast
import inspect
import json
import os
import sys
import time
import types
from datetime import date
from functools import lru_cache

import numpy as np
import pandas as pd

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


def _install_offline_import_stubs():
    """engine/ imports streamlit and nba_api at module level even though
    nothing this sweep calls needs them to do real work. On a machine
    that has both installed (the normal dev setup) this is a no-op.
    Where they can't be installed, stand-ins are registered so the
    import chain resolves: streamlit's decorators become identity
    functions, nba_api endpoints raise on use (this sweep never makes a
    live call), and nba_api's static team list -- the one piece of real
    data engine/team_ids.py needs -- is rebuilt from cached box scores."""
    try:
        import streamlit  # noqa: F401
    except ImportError:
        st = types.ModuleType("streamlit")
        st.session_state = {}

        def cache_data(*args, **kwargs):
            if args and callable(args[0]) and not kwargs:
                return args[0]
            return lambda f: f

        st.cache_data = cache_data
        st.cache_resource = cache_data
        sys.modules["streamlit"] = st

    try:
        import nba_api  # noqa: F401
        return
    except ImportError:
        pass

    def _endpoint_stub(name):
        mod = types.ModuleType(f"nba_api.stats.endpoints.{name}")

        def _no_live_calls(*_a, **_k):
            raise ConnectionError("offline stub -- shrinkage_k_sweep.py never fetches live data")
        mod.__getattr__ = lambda _attr: _no_live_calls
        sys.modules[mod.__name__] = mod
        return mod

    teams_by_abbr = {}
    cache_dir = os.path.join(REPO_ROOT, "data_cache")
    for fname in sorted(os.listdir(cache_dir)):
        if not fname.startswith("boxscore_"):
            continue
        with open(os.path.join(cache_dir, fname)) as f:
            for r in json.load(f)["data"]:
                teams_by_abbr[r["teamTricode"]] = r["teamId"]
        if len(teams_by_abbr) >= 30:
            break
    if len(teams_by_abbr) < 30:
        raise RuntimeError(f"Could only rebuild {len(teams_by_abbr)}/30 teams from cached box scores")

    root = types.ModuleType("nba_api")
    stats = types.ModuleType("nba_api.stats")
    endpoints = types.ModuleType("nba_api.stats.endpoints")
    endpoints.__getattr__ = _endpoint_stub
    static = types.ModuleType("nba_api.stats.static")
    teams = types.ModuleType("nba_api.stats.static.teams")
    teams.get_teams = lambda: [{"id": tid, "abbreviation": abbr} for abbr, tid in teams_by_abbr.items()]
    players = types.ModuleType("nba_api.stats.static.players")
    players.get_players = lambda: []
    for m in (root, stats, endpoints, static, teams, players):
        sys.modules[m.__name__] = m
    root.stats, stats.endpoints, stats.static = stats, endpoints, static
    static.teams, static.players = teams, players


_install_offline_import_stubs()

import engine.backtest_point_in_time as bpit  # noqa: E402
from engine.backtest_point_in_time import (  # noqa: E402
    MIN_BASELINE_GAMES,
    _is_regular_season_game_id,
    get_point_in_time_opponent_defense,
    point_in_time_baseline,
)
from engine.adjustments.defense import get_defense_adjustment  # noqa: E402
from engine.cache import _load_df_cache  # noqa: E402
from engine.stat_columns import STAT_COLUMNS  # noqa: E402
from engine.team_ids import TEAM_ID_BY_ABBR  # noqa: E402
from run_backtest import SEASONS, load_fixed_player_list  # noqa: E402

APP_PATH = os.path.join(REPO_ROOT, "app.py")
OUTPUT_PATH = os.path.join(REPO_ROOT, "shrinkage_k_sweep_results.csv")
PUBLISHED_BACKTEST_PATH = os.path.join(REPO_ROOT, "backtest_results.csv")
K_VALUES = [2, 4, 8, 16, 32]
N_VALUES = [5, 10]            # the two "Last N games vs. this opponent" UI options
MAX_PRIOR_SEASONS = 3         # target season + up to 3 prior = app's 4-season window
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20260915
STATS = [col for col, _ in STAT_COLUMNS]
VARIANTS = ["season_only"] + [str(k) for k in K_VALUES] + ["raw_h2h"]


def load_production_blend():
    """app.py's blend_baseline_stats, compiled from app.py's own source
    text -- the real function, not a copy. Returns (fn, default_k)."""
    with open(APP_PATH) as f:
        source = f.read()
    tree = ast.parse(source)
    node = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "blend_baseline_stats"),
        None,
    )
    if node is None:
        raise RuntimeError("blend_baseline_stats not found at app.py's top level")
    if node.decorator_list:
        raise RuntimeError("blend_baseline_stats gained a decorator -- re-check this extraction")
    namespace = {"pd": pd, "STAT_COLUMNS": STAT_COLUMNS}
    exec(compile(ast.get_source_segment(source, node), APP_PATH, "exec"), namespace)
    fn = namespace["blend_baseline_stats"]
    default_k = inspect.signature(fn).parameters["shrinkage_k"].default
    return fn, default_k


# ---- in-memory caching of cache-file reads ---------------------------------
# point_in_time_baseline()/get_point_in_time_opponent_defense() re-read a JSON
# file on every call (~30k games x several files). Both only read the frame,
# never mutate it, so handing them one shared parsed copy is safe.
_memo_load = lru_cache(maxsize=None)(_load_df_cache)
bpit._load_df_cache = _memo_load


def _prior_season(season, back):
    start = int(season[:4]) - back
    return f"{start}-{str(start + 1)[-2:]}"


@lru_cache(maxsize=None)
def h2h_pool(player_id, season):
    """Every cached game (regular season + playoffs) for this player in
    `season` and up to MAX_PRIOR_SEASONS earlier seasons, with parsed
    dates. Returns (pool_df, n_prior_seasons_cached)."""
    frames, n_prior = [], 0
    for back in range(0, MAX_PRIOR_SEASONS + 1):
        df, _ = _memo_load(f"gamelog_{player_id}_{_prior_season(season, back)}")
        if df is None or df.empty:
            continue
        if back > 0:
            n_prior += 1
        frame = df.copy()
        frame["_season_back"] = back
        frames.append(frame)
    pool = pd.concat(frames, ignore_index=True)
    pool["_date"] = pd.to_datetime(pool["GAME_DATE"])
    return pool, n_prior


@lru_cache(maxsize=None)
def h2h_vs_team(player_id, season, opponent_abbr):
    """app.py get_head_to_head_log()'s filter + sort (MATCHUP substring,
    most-recent-first), before any date cut."""
    pool, _ = h2h_pool(player_id, season)
    matched = pool[pool["MATCHUP"].str.contains(opponent_abbr, na=False)]
    return matched.sort_values("_date", ascending=False).reset_index(drop=True)


def point_in_time_h2h(player_id, season, opponent_abbr, game_date, num_games):
    """app.py get_head_to_head_baseline(), restricted to games strictly
    before game_date. Returns (stats_dict or None, n, subset_df)."""
    matched = h2h_vs_team(player_id, season, opponent_abbr)
    subset = matched[matched["_date"] < pd.Timestamp(game_date)].head(num_games)
    if subset.empty:
        return None, 0, subset
    stats = {col: (subset[col].mean(), subset[col].std()) for col in STATS}
    return stats, len(subset), subset


def build_cases():
    """One dict per backtest case -- same case set as run_backtest.py."""
    cases = []
    for season, season_start in SEASONS:
        players = load_fixed_player_list(season)
        t0 = time.time()
        for player_id, player_name in players:
            df, _ = _memo_load(f"gamelog_{player_id}_{season}")
            if df is None:
                raise FileNotFoundError(f"gamelog_{player_id}_{season}.json missing (see run_backtest.py)")
            _, n_prior = h2h_pool(player_id, season)
            for _, game_row in df.iterrows():
                if not _is_regular_season_game_id(game_row["Game_ID"]):
                    continue
                game_date = pd.to_datetime(game_row["GAME_DATE"]).date()
                season_stats, n_games = point_in_time_baseline(player_id, season, game_date)
                if n_games < MIN_BASELINE_GAMES:
                    continue
                opponent_abbr = game_row["MATCHUP"].split()[-1]
                opponent_team_id = TEAM_ID_BY_ABBR.get(opponent_abbr)
                if opponent_team_id is None:
                    continue
                defense_lookup = get_point_in_time_opponent_defense(
                    season, season_start, opponent_team_id, game_date
                )
                case = {
                    "player_id": player_id, "season": season,
                    "game_id": str(game_row["Game_ID"]), "game_date": game_date,
                    "own_abbr": game_row["MATCHUP"].split()[0], "opponent_abbr": opponent_abbr,
                    "season_stats": season_stats, "defense_lookup": defense_lookup,
                    "actual": {col: game_row[col] for col in STATS},
                    "n_prior_seasons_cached": n_prior,
                }
                for num in N_VALUES:
                    stats, n, subset = point_in_time_h2h(player_id, season, opponent_abbr, game_date, num)
                    case[f"h2h_{num}"] = (stats, n)
                    # rows that only "match" because the PLAYER was on the
                    # opponent team at the time (MATCHUP starts with it)
                    case[f"own_team_rows_{num}"] = int(
                        (subset["MATCHUP"].str.split().str[0] == opponent_abbr).sum()
                    ) if n else 0
                    case[f"prior_season_rows_{num}"] = int((subset["_season_back"] > 0).sum()) if n else 0
                cases.append(case)
        print(f"  {season}: {len(players)} players, cases so far {len(cases)} ({time.time() - t0:.0f}s)")
    return cases


def defense_for(case, team_h2h_weight):
    """Exactly app.py tab1's call, fed the backtest's point-in-time rating."""
    lookup = case["defense_lookup"]
    if lookup is None:
        return get_defense_adjustment(None, None, "season's first calendar month -- excluded")
    def_rating, league_avg, note = lookup
    return get_defense_adjustment(def_rating, league_avg, note, team_h2h_weight=team_h2h_weight)


def predict_all(cases, blend, num_games):
    """Arrays (n_cases x n_variants x n_stats) of predictions, plus
    season-only base and actual (n_cases x n_stats), for num_games."""
    pred = np.full((len(cases), len(VARIANTS), len(STATS)), np.nan)
    for i, case in enumerate(cases):
        season_stats = case["season_stats"]
        h2h_stats, h2h_n = case[f"h2h_{num_games}"]
        for v, variant in enumerate(VARIANTS):
            if variant == "season_only" or h2h_n == 0:
                # app.py tab1: team_h2h_n == 0 -> season stats, weight 0
                baseline, weights = season_stats, {"season": 1.0, "team_h2h": 0.0}
            else:
                k = 0 if variant == "raw_h2h" else int(variant)
                baseline, weights = blend(season_stats, shrinkage_k=k,
                                          team_h2h=h2h_stats, team_h2h_n=h2h_n)
            defense = defense_for(case, weights["team_h2h"])
            for s, col in enumerate(STATS):
                pred[i, v, s] = baseline[col][0] * defense.multiplier_for(col)
    base = np.array([[c["season_stats"][col][0] for col in STATS] for c in cases], dtype=float)
    actual = np.array([[c["actual"][col] for col in STATS] for c in cases], dtype=float)
    return pred, base, actual


def directional_arrays(pred, base, actual):
    """analytics/layer_accuracy.py's definition, with the unadjusted
    baseline = point-in-time season average and the "layer" = whatever
    moved the prediction off it. Returns (hit, scored) boolean arrays."""
    pdir = np.sign(pred - base[:, None, :])
    adir = np.sign(actual - base)[:, None, :]
    scored = (pdir != 0) & (adir != 0) & ~np.isnan(pred)
    hit = scored & (pdir == adir)
    return hit, scored


def cluster_bootstrap_diff(per_case, clusters, a, b, rng, weights_count=None):
    """95% CI of mean(per_case[:, a]) - mean(per_case[:, b]) resampling
    whole player-seasons (a player's games aren't independent). If
    weights_count is given, it's a ratio estimator: sum(num)/sum(den)."""
    codes, uniq = pd.factorize(clusters)
    n_cl = len(uniq)
    def sums(x):
        return np.bincount(codes, weights=x, minlength=n_cl)
    if weights_count is None:
        num_a, num_b = sums(per_case[:, a]), sums(per_case[:, b])
        den_a = den_b = np.bincount(codes, minlength=n_cl).astype(float)
    else:
        num_a, num_b = sums(per_case[:, a]), sums(per_case[:, b])
        den_a, den_b = sums(weights_count[:, a]), sums(weights_count[:, b])
    point = num_a.sum() / den_a.sum() - num_b.sum() / den_b.sum()
    w = rng.multinomial(n_cl, np.full(n_cl, 1 / n_cl), size=BOOTSTRAP_RESAMPLES).astype(float)
    boot = (w @ num_a) / (w @ den_a) - (w @ num_b) / (w @ den_b)
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return point, lo, hi


def validate_season_only_against_published(cases, pred):
    """The season_only variant must reproduce backtest_results.csv's
    *_predicted columns exactly -- proves the case set and the
    baseline/defense wiring match the published backtest."""
    if not os.path.exists(PUBLISHED_BACKTEST_PATH):
        print("  (backtest_results.csv not found -- skipping cross-check)")
        return
    pub = pd.read_csv(PUBLISHED_BACKTEST_PATH, dtype={"player_id": str, "game_id": str})
    pub = pub.set_index(["player_id", "season", "game_id"])
    mine = pd.DataFrame(pred[:, 0, :], columns=[f"{c}_predicted" for c in STATS])
    mine.index = pd.MultiIndex.from_tuples(
        [(c["player_id"], c["season"], c["game_id"]) for c in cases], names=pub.index.names)
    joined = mine.join(pub[[f"{c}_predicted" for c in STATS]], rsuffix="_pub", how="outer")
    missing = joined.isna().any(axis=1).sum()
    max_diff = max((joined[f"{c}_predicted"] - joined[f"{c}_predicted_pub"]).abs().max() for c in STATS)
    print(f"  cross-check vs backtest_results.csv: {len(mine)} cases here, {len(pub)} published, "
          f"{missing} unmatched rows, max |season_only - published| = {max_diff:.2e}")


def main():
    blend, production_k = load_production_blend()
    print(f"blend_baseline_stats loaded from app.py (production default shrinkage_k={production_k})")
    print("Building point-in-time cases ...")
    cases = build_cases()
    print(f"{len(cases)} backtest cases (run_backtest.py's case set)\n")

    case_df = pd.DataFrame([{
        "season": c["season"], "n_prior_seasons_cached": c["n_prior_seasons_cached"],
        **{f"h2h_n_{n}": c[f"h2h_{n}"][1] for n in N_VALUES},
        **{f"own_team_rows_{n}": c[f"own_team_rows_{n}"] for n in N_VALUES},
        **{f"prior_season_rows_{n}": c[f"prior_season_rows_{n}"] for n in N_VALUES},
    } for c in cases])

    print("=== Coverage (h2h pool = target season + cached prior seasons) ===")
    cov = case_df.groupby("season").agg(
        cases=("h2h_n_10", "size"),
        prior_seasons_cached=("n_prior_seasons_cached", "mean"),
        with_h2h=("h2h_n_10", lambda s: int((s > 0).sum())),
        mean_n_N5=("h2h_n_5", lambda s: s[s > 0].mean()),
        mean_n_N10=("h2h_n_10", lambda s: s[s > 0].mean()),
        share_N10_rows_from_prior=("prior_season_rows_10", "sum"),
    )
    cov["share_N10_rows_from_prior"] = cov["share_N10_rows_from_prior"] / case_df.groupby("season")["h2h_n_10"].sum()
    print(cov.round(2).to_string())
    for n in N_VALUES:
        dist = case_df.loc[case_df[f"h2h_n_{n}"] > 0, f"h2h_n_{n}"].value_counts().sort_index()
        print(f"  N={n}: h2h n distribution among affected cases: " + ", ".join(f"{k}:{v}" for k, v in dist.items()))
    for n in N_VALUES:
        contaminated = int((case_df[f"own_team_rows_{n}"] > 0).sum())
        print(f"  N={n}: {contaminated} cases ({case_df[f'own_team_rows_{n}'].sum()} h2h rows) include games the "
              f"player played FOR the opponent (MATCHUP substring match on his own team)")
    print()

    rng = np.random.default_rng(BOOTSTRAP_SEED)
    out_rows = []
    for num in N_VALUES:
        pred, base, actual = predict_all(cases, blend, num)
        if num == N_VALUES[0]:
            validate_season_only_against_published(cases, pred)
        affected = np.array([c[f"h2h_{num}"][1] > 0 for c in cases])
        p, b, a = pred[affected], base[affected], actual[affected]
        clusters = np.array([f"{c['player_id']}_{c['season']}" for c, keep in zip(cases, affected) if keep])
        seasons = np.array([c["season"] for c, keep in zip(cases, affected) if keep])
        n_aff = int(affected.sum())

        abs_err = np.abs(p - a[:, None, :])                     # cases x variants x stats
        mae = abs_err.mean(axis=0)                              # variants x stats
        scale = mae[0]                                          # season_only MAE per stat
        norm_loss = (abs_err / scale).mean(axis=2)              # cases x variants; mean == pooled rel-MAE
        hit, scored = directional_arrays(p, b, a)
        dir_acc = hit.sum(axis=0) / np.maximum(scored.sum(axis=0), 1) * 100

        for v, variant in enumerate(VARIANTS):
            for s, col in enumerate(STATS):
                out_rows.append({
                    "N": num, "k": variant, "stat": col, "n_cases": n_aff,
                    "mae": mae[v, s],
                    "directional_acc": dir_acc[v, s] if scored[:, v, s].any() else np.nan,
                    "directional_n": int(scored[:, v, s].sum()),
                    "rel_mae_vs_season_only": mae[v, s] / scale[s],
                })
            out_rows.append({
                "N": num, "k": variant, "stat": "POOLED", "n_cases": n_aff, "mae": np.nan,
                "directional_acc": hit[:, v, :].sum() / max(scored[:, v, :].sum(), 1) * 100,
                "directional_n": int(scored[:, v, :].sum()),
                "rel_mae_vs_season_only": norm_loss[:, v].mean(),
            })

        print(f"\n=== N={num}: {n_aff} of {len(cases)} cases have >= 1 prior h2h game ===")
        print("POOLED rel-MAE = mean over the 9 stats of MAE / season_only MAE (<1 beats season-only);")
        print("POOLED dir = hits/scored summed over all 9 stats (layer_accuracy.py definition).")
        table = pd.DataFrame({
            "variant": VARIANTS,
            "rel_MAE": norm_loss.mean(axis=0),
            "dir_acc%": [hit[:, v, :].sum() / scored[:, v, :].sum() * 100 for v in range(len(VARIANTS))],
            "dir_n": [int(scored[:, v, :].sum()) for v in range(len(VARIANTS))],
        })
        for s, col in enumerate(STATS):
            table[col] = mae[:, s]
        print(table.round(4).to_string(index=False))
        actual_down = (np.sign(a - b) < 0)[:, None, :]
        down_share = ((np.sign(p - b[:, None, :]) < 0) & scored).sum(axis=(0, 2)) / scored.sum(axis=(0, 2))
        always_down = (actual_down & scored).sum(axis=(0, 2)) / scored.sum(axis=(0, 2)) * 100
        print("  skew check -- share of scored calls that say 'below season avg': " + ", ".join(
            f"{VARIANTS[v]}={down_share[v]:.3f}" for v in range(len(VARIANTS))))
        print("  skew check -- 'always predict below season avg' dir_acc on each variant's scored cells: " + ", ".join(
            f"{VARIANTS[v]}={always_down[v]:.2f}%" for v in range(len(VARIANTS))))

        k_idx = [VARIANTS.index(str(k)) for k in K_VALUES]
        best_v = k_idx[int(np.argmin(norm_loss.mean(axis=0)[k_idx]))]
        prod_v = VARIANTS.index(str(production_k)) if str(production_k) in VARIANTS else None
        print(f"\n  best k by pooled rel-MAE: {VARIANTS[best_v]}")
        print("  best k per stat (MAE): " + ", ".join(
            f"{col}={VARIANTS[k_idx[int(np.argmin(mae[k_idx, s]))]]}" for s, col in enumerate(STATS)))
        spread = (mae[k_idx].max(axis=0) - mae[k_idx].min(axis=0)) / scale * 100
        print("  MAE spread across k, % of season-only MAE: " + ", ".join(
            f"{col}={spread[s]:.2f}%" for s, col in enumerate(STATS)))

        print("  paired cluster-bootstrap (player-season resampling), 95% CI, pooled rel-MAE difference:")
        comparisons = []
        if prod_v is not None and prod_v != best_v:
            comparisons.append((prod_v, best_v))
        comparisons += [(best_v, 0), (VARIANTS.index("raw_h2h"), 0)]
        if prod_v is not None:
            comparisons.append((prod_v, 0))
        for va, vb in comparisons:
            d, lo, hi = cluster_bootstrap_diff(norm_loss, clusters, va, vb, rng)
            print(f"    k={VARIANTS[va]} minus {VARIANTS[vb]}: {d:+.4f}  [{lo:+.4f}, {hi:+.4f}]")
        h = hit.sum(axis=2).astype(float)
        sc = scored.sum(axis=2).astype(float)
        if prod_v is not None:
            d, lo, hi = cluster_bootstrap_diff(h, clusters, prod_v, 0, rng, weights_count=sc)
            print(f"    directional acc, k={VARIANTS[prod_v]} minus season_only: "
                  f"{d * 100:+.2f} pts  [{lo * 100:+.2f}, {hi * 100:+.2f}]")

        print("  pooled rel-MAE by target season (stability of the k choice):")
        by_season = pd.DataFrame(
            {VARIANTS[v]: pd.Series(norm_loss[:, v]).groupby(seasons).mean() for v in range(len(VARIANTS))})
        by_season.insert(0, "cases", pd.Series(norm_loss[:, 0]).groupby(seasons).size())
        print(by_season.round(4).to_string())

        print("  pooled rel-MAE by h2h sample size n:")
        n_arr = np.array([c[f"h2h_{num}"][1] for c, keep in zip(cases, affected) if keep])
        buckets = pd.cut(n_arr, bins=[0, 2, 4, 7, 10], labels=["n=1-2", "n=3-4", "n=5-7", "n=8-10"])
        by_n = pd.DataFrame(
            {VARIANTS[v]: pd.Series(norm_loss[:, v]).groupby(buckets, observed=True).mean()
             for v in range(len(VARIANTS))})
        by_n.insert(0, "cases", pd.Series(norm_loss[:, 0]).groupby(buckets, observed=True).size())
        print(by_n.round(4).to_string())

    pd.DataFrame(out_rows).to_csv(OUTPUT_PATH, index=False)
    print(f"\nWrote {len(out_rows)} rows to {OUTPUT_PATH}.")
    print("SCOPE: team-h2h blend + opponent_defense, regular-season targets, subset with >= 1 "
          "prior h2h game -- see this script's module docstring for the coverage limits.")


if __name__ == "__main__":
    main()
