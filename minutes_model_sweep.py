"""
Point-in-time backtest of a MINUTES PROJECTION, for player lines and for
Full Matchup team totals.

WHY THIS EXISTS
Every projection in the app is a season per-game average (times the
opponent-defense multiplier). Two known weaknesses:
  * Direction is a coin flip (~52%). An earlier probe found that knowing
    a player's ACTUAL minutes would lift direction accuracy to ~67%, so a
    better guess at tonight's minutes is the biggest available lever.
  * Full Matchup sums per-game averages over the whole roster, and every
    per-game average is "given that he played". Players who often don't
    play (injured, deep bench) still add their full line, so projected
    team totals run high.

WHAT IS TESTED (all inputs strictly before the target game)
Source: cached box scores for every regular-season game of 2023-24,
2024-25 and 2025-26 (data_cache/boxscore_<game_id>.json -- both teams,
every listed player, including DNP rows). Game dates come from the
cached player gamelogs; the few games no gamelog covers fall back to
game-id order within the season.

Opponent defense: the monthly point-in-time checkpoint and
get_defense_adjustment(), exactly as run_backtest.py (no adjustment in a
season's first calendar month). It multiplies every model equally.

A. PLAYER LINES (conditional on playing, like the app's lines)
   Case = a player's game with minutes > 0 and >= 5 earlier played games
   this season (any team). Subsets: "all" and "top150" (run_backtest's
   list, the published case set).
   * base:        season-to-date per-game average (= production).
   * min_N_w:     season per-minute rate x projected minutes,
                  projected = w * mean(last N games' minutes)
                          + (1 - w) * season MPG.   (w = 0 is base exactly)
   * ewm_h:       rate x exponentially weighted minutes (half-life h
                  games, all season-to-date games).
   * form_N_w:    CONTROL -- w * mean(last N per-game stat) + (1-w) * base.
                  Tells whether a minutes model beats plain recent form.
   Metrics per stat: MAE, bias, pooled rel-MAE (mean over 9 stats of
   MAE / base MAE), direction accuracy against the season average
   (call = sign(pred - season avg), truth = sign(actual - season avg),
   ties dropped), balanced accuracy, call coverage.
   Selection: the config is chosen leave-one-season-out on pooled rel-MAE
   ("pick" rows); a fixed-config table is printed too. 95% CI on the
   rel-MAE difference vs base = cluster bootstrap over player-seasons.

B. TEAM TOTALS (Full Matchup)
   Team-games from each team's 15th game on. Roster proxy for the app's
   "current roster": every player listed for the team (played or not)
   in its previous 20 games whose most recent listing is with this team.
   Each roster player needs >= 1 earlier played game this season (the
   app skips players without data).
     T0   sum of per-game averages (production, nobody marked out)
     T1   sum of P_i x average, P_i = his games played for this team /
          team games since his first listing with it (shrunk to 0.7
          with A_PRIOR pseudo-games)
     T2   T1 with minutes normalised: scaled so sum(P_i x MPG_i) = 240
     T2s  T2 with the availability the live app can compute:
          P_i = his season games (any team) / his current team's games
          so far, capped at 1 and shrunk the same way
     T3   T2 with projected minutes (0.5 x last-5 average + 0.5 x MPG,
          the part-A minutes model) in place of MPG
   "outs known" scenario: the user marks out every roster player who was
   not dressed (absent from the box score, or listed DND / NWT / injured).
   Coach's-decision DNPs stay in, as they would in the app.
     T0o  sum of averages over the rest (the out-redistribution
          multipliers are NOT modelled here -- production would add a
          partial pickup; see out_prior_sweep.py for that layer)
     T1o  P'_i = played / dressed games for this team (shrunk)
     T2o  T1o normalised to 240 minutes
   Metrics: bias and MAE of team totals per stat.

RESULTS (17 Sep 2026 run)
  A. Minutes model, chosen leave-one-season-out (min_3_0.5 on all
     players; min_5_0.5 on top150):
       all players (70,944):  rel-MAE 0.9910 (95% CI of -1: -1.00%..-0.78%),
                              direction 50.5% -> 53.3% (balanced 50.8 -> 54.1)
       top150 (29,914):       rel-MAE 0.9967 (-0.43%..-0.25%),
                              direction 50.6% -> 51.8%
     Real but small, and it would change Single Player lines too, so it
     is NOT shipped yet (the recent-form control, form_*, is close
     behind; the lean model already uses minutes trend).
  B. Team totals (6,120 team-games), PTS bias / MAE:
       nobody marked out:  T0 +35.4 / 35.6   T1 +1.2 / 11.0
                           T2 -1.2 / 10.1    T2s -0.7 / 10.0   T3 -1.2 / 10.1
       outs known:         T0o +1.1 / 17.9   T1o -9.7 / 18.6
                           T2o -2.1 / 10.7   T2so -2.0 / 10.6  T3o -2.0 / 10.7
     Roster proxy averaged 16.8 players whose MPG summed to 323.
     SHIPPED: T2s -- engine/team_total.py, Full Matchup's
     "Team total (expected)" row.

LIMITS
  * Box-score roster proxy: truly inactive players are rarely listed, so
    a long-injured player drops out of the proxy after 20 team games.
    The app's live roster keeps him, so the production bias (T0) is
    probably UNDER-stated here.
  * Players with fewer than 5 earlier games still count in team totals
    (on their season-to-date average); the app would use last season.
  * Overtime: actual totals include OT; the 240 target doesn't.

Usage:  python3 minutes_model_sweep.py
Writes minutes_model_sweep_results.csv (tidy: part, scope, subset,
config, stat, n, mae, bias, rel_mae, dir_acc, dir_bal_acc, coverage).
"""

import glob
import json
import os
import time
from collections import defaultdict
from datetime import date

import numpy as np
import pandas as pd

from engine.adjustments.defense import get_defense_adjustment
from engine.backtest_point_in_time import get_point_in_time_opponent_defense
from engine.stat_columns import STAT_COLUMNS

SEASONS = [
    ("2023-24", date(2023, 10, 24), "00223"),
    ("2024-25", date(2024, 10, 22), "00224"),
    ("2025-26", date(2025, 10, 21), "00225"),
]
STATS = [c for c, _ in STAT_COLUMNS]
BOX_FIELDS = {
    "PTS": "points", "AST": "assists", "REB": "reboundsTotal", "STL": "steals",
    "BLK": "blocks", "FG3M": "threePointersMade", "TOV": "turnovers",
    "FG3A": "threePointersAttempted", "OREB": "reboundsOffensive",
}
MIN_PRIOR_GAMES = 5
TEAM_FROM_GAME = 15
ROSTER_WINDOW = 20
TEAM_MINUTES = 240.0
P_PRIOR = 0.7
A_PRIOR = 5.0
WINDOWS = [3, 5, 10]
WEIGHTS = [0.25, 0.5, 0.75, 1.0]
HALF_LIVES = [2, 4, 8, 16]
BOOT = 400
OUT_CSV = "minutes_model_sweep_results.csv"
rng = np.random.default_rng(20260917)


# ------------------------------------------------------------------ data

def parse_minutes(text):
    if not text:
        return 0.0
    mm, _, ss = str(text).partition(":")
    try:
        return float(mm) + (float(ss) / 60 if ss else 0.0)
    except ValueError:
        return 0.0


def game_dates():
    dates = {}
    for path in glob.glob("data_cache/gamelog_*_*.json"):
        with open(path) as f:
            for r in json.load(f)["data"]:
                dates.setdefault(str(r["Game_ID"]), r["GAME_DATE"])
    return {k: pd.to_datetime(v).date() for k, v in dates.items()}


def top150(season):
    with open(f"data_cache/player_totals_{season}.json") as f:
        raw = json.load(f)["data"]
    rows = sorted(raw, key=lambda r: r.get("MIN", 0), reverse=True)[:150]
    return {int(r["PLAYER_ID"]) for r in rows}


def load_box_rows():
    dates = game_dates()
    rows = []
    missing_dates = 0
    for season, season_start, prefix in SEASONS:
        files = sorted(glob.glob(f"data_cache/boxscore_{prefix}*.json"))
        for path in files:
            game_id = os.path.basename(path)[len("boxscore_"):-len(".json")]
            with open(path) as f:
                data = json.load(f)["data"]
            gdate = dates.get(game_id)
            if gdate is None:
                missing_dates += 1
            teams = sorted({r["teamId"] for r in data})
            for r in data:
                minutes = parse_minutes(r.get("minutes"))
                comment = (r.get("comment") or "").upper()
                row = {
                    "season": season, "season_start": season_start,
                    "game_id": game_id, "date": gdate,
                    "team_id": r["teamId"],
                    "opp_id": next((t for t in teams if t != r["teamId"]), None),
                    "player_id": r["personId"],
                    "min": minutes,
                    "played": minutes > 0,
                    "dressed": minutes > 0 or comment.startswith("DNP - COACH"),
                }
                for col, field in BOX_FIELDS.items():
                    row[col] = float(r.get(field) or 0) if minutes > 0 else 0.0
                rows.append(row)
    df = pd.DataFrame(rows)
    # Games without a gamelog date: order by game id inside the season,
    # dated like the nearest earlier dated game.
    df["gid_num"] = df["game_id"].astype(int)
    df = df.sort_values(["season", "gid_num"])
    df["date"] = df.groupby("season")["date"].transform(lambda s: s.ffill().bfill())
    print(f"box rows {len(df):,}; games without a gamelog date: {missing_dates // 2 if missing_dates else 0} (filled)")
    return df


def defense_multipliers(df):
    cache = {}
    out = np.ones((len(df), len(STATS)))
    keys = df[["season", "season_start", "opp_id", "date"]].itertuples(index=False)
    for i, (season, start, opp, gdate) in enumerate(keys):
        key = (season, opp, gdate.year, gdate.month)
        if key not in cache:
            lookup = get_point_in_time_opponent_defense(season, start, opp, gdate)
            res = get_defense_adjustment(*lookup) if lookup else get_defense_adjustment(None, None, "first month")
            cache[key] = [res.multiplier_for(c) for c in STATS]
        out[i] = cache[key]
    return out


# ------------------------------------------------------------ part A

def player_cases(box):
    played = box[box["played"]].copy()
    played = played.sort_values(["season", "player_id", "date", "gid_num"])
    g = played.groupby(["season", "player_id"], sort=False)
    played["n_prior"] = g.cumcount()
    played["min_sum_prior"] = g["min"].cumsum() - played["min"]
    for col in STATS:
        played[f"{col}_sum_prior"] = g[col].cumsum() - played[col]
    for n in WINDOWS:
        played[f"min_last{n}"] = g["min"].transform(lambda s: s.shift(1).rolling(n, min_periods=1).mean())
        for col in STATS:
            played[f"{col}_last{n}"] = g[col].transform(lambda s: s.shift(1).rolling(n, min_periods=1).mean())
    for h in HALF_LIVES:
        played[f"min_ewm{h}"] = g["min"].transform(lambda s: s.shift(1).ewm(halflife=h).mean())
    cases = played[played["n_prior"] >= MIN_PRIOR_GAMES].reset_index(drop=True)
    cases["cluster"] = cases["season"] + "_" + cases["player_id"].astype(str)
    return cases


def predictions(cases, mult):
    n = cases["n_prior"].to_numpy(float)
    mpg = cases["min_sum_prior"].to_numpy() / n
    base = np.column_stack([cases[f"{c}_sum_prior"].to_numpy() / n for c in STATS])
    rate = np.column_stack([
        np.divide(cases[f"{c}_sum_prior"].to_numpy(), cases["min_sum_prior"].to_numpy(),
                  out=np.zeros(len(cases)), where=cases["min_sum_prior"].to_numpy() > 0)
        for c in STATS
    ])
    preds = {"base": base * mult}
    for nwin in WINDOWS:
        recent_min = cases[f"min_last{nwin}"].to_numpy()
        recent_stat = np.column_stack([cases[f"{c}_last{nwin}"].to_numpy() for c in STATS])
        for w in WEIGHTS:
            proj = w * recent_min + (1 - w) * mpg
            preds[f"min_{nwin}_{w}"] = rate * proj[:, None] * mult
            preds[f"form_{nwin}_{w}"] = (w * recent_stat + (1 - w) * base) * mult
    for h in HALF_LIVES:
        preds[f"ewm_{h}"] = rate * cases[f"min_ewm{h}"].to_numpy()[:, None] * mult
    return base, preds


def score(pred, actual, base, idx):
    p, a, b = pred[idx], actual[idx], base[idx]
    err = np.abs(p - a)
    out = {"mae": err.mean(0), "bias": (p - a).mean(0)}
    call, truth = np.sign(p - b), np.sign(a - b)
    ok = (call != 0) & (truth != 0)
    hits = (call == truth) & ok
    out["coverage"] = ok.mean(0)
    with np.errstate(invalid="ignore", divide="ignore"):
        out["dir_acc"] = hits.sum(0) / ok.sum(0)
        up = ok & (truth > 0)
        down = ok & (truth < 0)
        out["dir_bal_acc"] = 0.5 * ((hits & up).sum(0) / up.sum(0) + (hits & down).sum(0) / down.sum(0))
    return out


def pooled_rel(pred, actual, base_pred, idx):
    return float(np.mean(np.abs(pred[idx] - actual[idx]).mean(0) / np.abs(base_pred[idx] - actual[idx]).mean(0)))


def cluster_ci(pred, ref, actual, clusters, idx):
    """95% CI of pooled rel-MAE(pred) - 1 (vs ref), bootstrap over clusters."""
    sub = clusters[idx]
    codes, inv = np.unique(sub, return_inverse=True)
    e_p = np.abs(pred[idx] - actual[idx])
    e_r = np.abs(ref[idx] - actual[idx])
    sp = np.zeros((len(codes), len(STATS)))
    sr = np.zeros((len(codes), len(STATS)))
    np.add.at(sp, inv, e_p)
    np.add.at(sr, inv, e_r)
    draws = []
    for _ in range(BOOT):
        pick = rng.integers(0, len(codes), len(codes))
        draws.append(np.mean(sp[pick].sum(0) / sr[pick].sum(0)) - 1)
    return np.percentile(draws, [2.5, 97.5])


def part_a(box, mult_all, records):
    cases = player_cases(box)
    mult = mult_all[cases["_row"].to_numpy()]
    actual = cases[STATS].to_numpy()
    base, preds = predictions(cases, mult)
    clusters = cases["cluster"].to_numpy()
    seasons = cases["season"].to_numpy()
    tops = {s: top150(s) for s, _, _ in SEASONS}
    is_top = np.array([pid in tops[s] for s, pid in zip(seasons, cases["player_id"])])
    subsets = {"all": np.ones(len(cases), bool), "top150": is_top}
    names = [k for k in preds if k != "base"]

    for subset, mask in subsets.items():
        # leave-one-season-out pick among minutes configs (and, separately, form configs)
        picked = {}
        for family in ("min_", "ewm_", "form_"):
            fam = [k for k in names if k.startswith(family)]
            stitched = np.array(preds["base"])
            choice = {}
            for s, _, _ in SEASONS:
                train = mask & (seasons != s)
                best = min(fam, key=lambda k: pooled_rel(preds[k], actual, preds["base"], train))
                choice[s] = best
                test = mask & (seasons == s)
                stitched[test] = preds[best][test]
            picked[f"pick_{family.rstrip('_')}"] = (stitched, choice)

        rows = [("base", preds["base"])] + [(k, preds[k]) for k in names] + [(k, v[0]) for k, v in picked.items()]
        for config, pred in rows:
            for scope, idx in [("pooled", mask)] + [(s, mask & (seasons == s)) for s, _, _ in SEASONS]:
                sc = score(pred, actual, base, idx)
                rel = pooled_rel(pred, actual, preds["base"], idx)
                for j, col in enumerate(STATS):
                    records.append({
                        "part": "player", "scope": scope, "subset": subset, "config": config,
                        "stat": col, "n": int(idx.sum()), "mae": sc["mae"][j], "bias": sc["bias"][j],
                        "rel_mae": np.abs(pred[idx][:, j] - actual[idx][:, j]).mean()
                        / np.abs(preds["base"][idx][:, j] - actual[idx][:, j]).mean(),
                        "dir_acc": sc["dir_acc"][j], "dir_bal_acc": sc["dir_bal_acc"][j],
                        "coverage": sc["coverage"][j], "pooled_rel_mae": rel,
                    })

        print(f"\n== A. player lines, subset={subset}, n={int(mask.sum()):,} ==")
        table = []
        for config, pred in rows:
            sc = score(pred, actual, base, mask)
            table.append((config, pooled_rel(pred, actual, preds["base"], mask),
                          float(np.nanmean(sc["dir_acc"])), float(np.nanmean(sc["dir_bal_acc"])),
                          float(np.mean(sc["coverage"])), sc["dir_acc"][0], sc["bias"][0]))
        table.sort(key=lambda t: t[1])
        print(f"{'config':<14}{'relMAE':>8}{'dir':>7}{'bal':>7}{'cov':>6}{'PTSdir':>8}{'PTSbias':>9}")
        for t in table[:12] + [x for x in table if x[0] in ("base",) or x[0].startswith("pick")]:
            print(f"{t[0]:<14}{t[1]:8.4f}{t[2]:7.3f}{t[3]:7.3f}{t[4]:6.2f}{t[5]:8.3f}{t[6]:9.3f}")
        for key, (stitched, choice) in picked.items():
            lo, hi = cluster_ci(stitched, preds["base"], actual, clusters, mask)
            print(f"  {key}: folds chose {choice}; rel-MAE-1 95% CI [{lo:+.4f}, {hi:+.4f}]")
            per_stat = score(stitched, actual, base, mask)
            print("   per stat dir_acc: " + ", ".join(
                f"{c} {a:.3f}" for c, a in zip(STATS, per_stat["dir_acc"])))
            print("   per stat relMAE:  " + ", ".join(
                f"{c} {np.abs(stitched[mask][:, j] - actual[mask][:, j]).mean() / np.abs(preds['base'][mask][:, j] - actual[mask][:, j]).mean():.4f}"
                for j, c in enumerate(STATS)))
    return cases, preds


# ------------------------------------------------------------ part B

def shrink_p(played, games):
    return (played + A_PRIOR * P_PRIOR) / (games + A_PRIOR)


def part_b(box, mult_all, records):
    box = box.sort_values(["season", "date", "gid_num"])
    results = defaultdict(list)
    for season, _start, _prefix in SEASONS:
        sb = box[box["season"] == season]
        # per player season-to-date aggregates keyed by date order
        games = (sb[["game_id", "date", "gid_num"]].drop_duplicates()
                 .sort_values(["date", "gid_num"])["game_id"].tolist())
        by_game = {gid: grp for gid, grp in sb.groupby("game_id")}
        agg = defaultdict(lambda: {"g": 0, "min": 0.0, "mins": [], **{c: 0.0 for c in STATS}})
        team_games_played = defaultdict(int)
        last_team = {}
        team_hist = defaultdict(list)          # team -> list of (game_id, {pid: (played, dressed)})
        tenure = defaultdict(lambda: {"team_games": 0, "played": 0, "dressed": 0})  # (team, pid)
        for gid in games:
            grp = by_game[gid]
            for team_id, tg in grp.groupby("team_id"):
                hist = team_hist[team_id]
                if len(hist) >= TEAM_FROM_GAME - 1:
                    listed = set()
                    for _g, members in hist[-ROSTER_WINDOW:]:
                        listed.update(members)
                    roster = [p for p in listed if last_team.get(p) == team_id and agg[p]["g"] > 0]
                    dressed_now = set(tg.loc[tg["dressed"], "player_id"])
                    mult = mult_all[tg["_row"].iloc[0]]
                    actual = tg[STATS].sum().to_numpy()
                    ot = max(1.0, tg["min"].sum() / 5 / TEAM_MINUTES)
                    for scenario, members in (("full", roster),
                                              ("outs_known", [p for p in roster if p in dressed_now])):
                        if not members:
                            continue
                        avg = np.array([[agg[p][c] / agg[p]["g"] for c in STATS] for p in members])
                        mpg = np.array([agg[p]["min"] / agg[p]["g"] for p in members])
                        if scenario == "full":
                            p_play = np.array([shrink_p(tenure[(team_id, p)]["played"],
                                                        tenure[(team_id, p)]["team_games"]) for p in members])
                        else:
                            p_play = np.array([shrink_p(tenure[(team_id, p)]["played"],
                                                        tenure[(team_id, p)]["dressed"]) for p in members])
                        t0 = avg.sum(0)
                        t1 = (p_play[:, None] * avg).sum(0)
                        exp_min = float((p_play * mpg).sum())
                        scale = TEAM_MINUTES / exp_min if exp_min > 0 else 1.0
                        t2 = t1 * scale
                        tg_games = max(team_games_played[team_id], 1)
                        p_simple = np.array([shrink_p(min(agg[p]["g"], tg_games), tg_games) for p in members])
                        t2s = (p_simple[:, None] * avg).sum(0) * (TEAM_MINUTES / float((p_simple * mpg).sum()))
                        proj_min = np.array([0.5 * np.mean(agg[p]["mins"][-5:]) + 0.5 * agg[p]["min"] / agg[p]["g"]
                                             for p in members])
                        rate = avg / mpg[:, None]
                        t3_raw = (p_play[:, None] * rate * proj_min[:, None]).sum(0)
                        t3 = t3_raw * (TEAM_MINUTES / float((p_play * proj_min).sum()))
                        tag = "" if scenario == "full" else "o"
                        for name, pred in ((f"T0{tag}", t0), (f"T1{tag}", t1), (f"T2{tag}", t2),
                                           (f"T2s{tag}", t2s), (f"T3{tag}", t3)):
                            results[(scenario, name)].append((season, pred * mult, actual))
                        results[(scenario, "_minutes")].append((season, np.array([mpg.sum(), exp_min, 240 * ot]), np.zeros(3)))
                        results[(scenario, "_size")].append((season, np.array([len(members)]), np.zeros(1)))
                # update tenure for everyone listed now or on the roster proxy
                for p in set(tg["player_id"]) | {p for p, t in last_team.items() if t == team_id}:
                    if last_team.get(p) == team_id or p in set(tg["player_id"]):
                        t = tenure[(team_id, p)]
                        t["team_games"] += 1
                hist.append((gid, set(tg["player_id"])))
                team_games_played[team_id] += 1
            # after the game: update player aggregates and last team
            for r in grp.itertuples(index=False):
                if r.played:
                    a = agg[r.player_id]
                    a["g"] += 1
                    a["min"] += r.min
                    a["mins"].append(r.min)
                    for c in STATS:
                        a[c] += getattr(r, c)
                last_team[r.player_id] = r.team_id
                t = tenure[(r.team_id, r.player_id)]
                t["played"] += int(r.played)
                t["dressed"] += int(r.dressed)

    print("\n== B. Full Matchup team totals ==")
    for (scenario, name), items in sorted(results.items()):
        if name.startswith("_"):
            vals = np.array([v for _s, v, _a in items])
            label = "roster size" if name == "_size" else "minutes: sum MPG / expected / actual(incl OT)"
            print(f"  [{scenario}] {label}: {np.round(vals.mean(0), 1)}")
            continue
        preds = np.array([p for _s, p, _a in items])
        acts = np.array([a for _s, _p, a in items])
        seas = np.array([s for s, _p, _a in items])
        for scope in ["pooled"] + [s for s, _, _ in SEASONS]:
            idx = np.ones(len(items), bool) if scope == "pooled" else seas == scope
            err = preds[idx] - acts[idx]
            for j, col in enumerate(STATS):
                records.append({"part": "team", "scope": scope, "subset": scenario, "config": name,
                                "stat": col, "n": int(idx.sum()), "mae": np.abs(err[:, j]).mean(),
                                "bias": err[:, j].mean()})
        err = preds - acts
        print(f"  [{scenario}] {name}: n={len(items):,}  PTS bias {err[:, 0].mean():+6.2f} MAE {np.abs(err[:, 0]).mean():5.2f}"
              f" | AST bias {err[:, 1].mean():+5.2f} | REB bias {err[:, 2].mean():+5.2f}"
              f" | FG3A bias {err[:, 7].mean():+5.2f} MAE {np.abs(err[:, 7]).mean():5.2f}")


def main():
    t0 = time.time()
    box = load_box_rows().reset_index(drop=True)
    box["_row"] = np.arange(len(box))
    mult_all = defense_multipliers(box)
    print(f"loaded in {time.time() - t0:.0f}s")
    records = []
    part_a(box, mult_all, records)
    part_b(box, mult_all, records)
    pd.DataFrame(records).to_csv(OUT_CSV, index=False)
    print(f"\nwrote {OUT_CSV} ({len(records):,} rows) in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
