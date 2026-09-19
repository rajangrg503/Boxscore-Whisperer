"""The backtest population, decided the way the live app decides it --
point in time -- instead of from end-of-season knowledge.

WHY THIS EXISTS
run_backtest.py scores the top 150 players by MINUTES PLAYED OVER THE
WHOLE SEASON (run_backtest.py:47-53, reading data_cache/player_totals_
{season}.json). That list can only be known in April. Choosing it in
advance quietly drops exactly the players whose season fell apart --
injuries, benchings, trades into a smaller role -- so every number
computed on it (baseline error, the opponent-defence layer, strong-lean
accuracy, the range calibration) describes a friendlier world than the
one the live app runs in, and it flatters any feature whose whole job is
to catch collapsing minutes.

WHAT THIS BUILDS
Every player-game in the three cached seasons, from the 3,690 cached box
scores (data_cache/boxscore_*.json -- all 1,230 regular-season games per
season), with eligibility decided as of the game date:

  * a player enters the set for game G when he has already PLAYED
    MIN_BASELINE_GAMES games that season before G, the same rule
    engine/backtest_point_in_time.py applies to the old set
  * his baseline is the mean/std of those prior games only
  * no filter on what he does later in the season

Games he didn't play are not targets (there is no stat line to predict)
but they ARE what makes the population honest: a player who stops
playing simply stops appearing, instead of being retroactively deleted
from the whole season.

Box scores carry no game date, so dates come from the cached game logs
(every one of the 3,690 game ids appears in one, checked). Opponent
defence uses the same month-end checkpoints as the old harness, via
engine/backtest_point_in_time.py, so the two sets differ ONLY in who is
in them.

Output: backtest_population.csv -- the same columns as
backtest_results.csv plus minutes, starter, n_prior and team_id, which
later work (minutes, role, availability) needs and the old set can't
supply.

Usage:
    python3 build_backtest_population.py
"""

import glob
import json
import os
from datetime import date

import pandas as pd

# engine/ imports streamlit and nba_api at module level. Where they are
# installed (the app, and the test environment's shims) nothing else is
# needed; where they are not, shrinkage_k_sweep installs the same offline
# stubs the other sweeps use. Importing it unconditionally would also
# memoise the engine's cache loader, which leaks into tests that patch
# the cache, so it is imported only when it is actually required.
try:  # noqa: SIM105
    import streamlit  # noqa: F401
except ImportError:  # pragma: no cover - exercised when running standalone
    import shrinkage_k_sweep  # noqa: F401

from engine.adjustments.defense import get_defense_adjustment  # noqa: E402
from engine.backtest_point_in_time import (  # noqa: E402
    MIN_BASELINE_GAMES,
    _is_regular_season_game_id,
    get_point_in_time_opponent_defense,
)
from engine import minutes  # noqa: E402
from engine.stat_columns import STAT_COLUMNS  # noqa: E402
from engine.tracker import _build_layers_json  # noqa: E402
from run_backtest import SEASONS  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(REPO_ROOT, "data_cache")
OUTPUT_PATH = os.path.join(REPO_ROOT, "backtest_population.csv")
# Every played game, including the first few that are too early to be a
# target: rolling windows (last 5, last 10) for a player's 6th game need
# the games before it, so the sweeps that fit on this population read
# this file rather than the filtered one.
GAMES_PATH = os.path.join(REPO_ROOT, "backtest_player_games.csv")

# box-score field -> our stat column
BOX_FIELDS = {
    "PTS": "points",
    "AST": "assists",
    "REB": "reboundsTotal",
    "STL": "steals",
    "BLK": "blocks",
    "FG3M": "threePointersMade",
    "FG3A": "threePointersAttempted",
    "TOV": "turnovers",
    "OREB": "reboundsOffensive",
}
# volume columns engine/lean.py's features need (FGA for points and
# three-point attempts, FG3A for threes made)
EXTRA_FIELDS = {"FGA": "fieldGoalsAttempted", "FGM": "fieldGoalsMade",
                "FTA": "freeThrowsAttempted"}
SEASON_BY_PREFIX = {"00223": "2023-24", "00224": "2024-25", "00225": "2025-26"}


def game_dates():
    """{game_id: date} from every cached game log."""
    out = {}
    for path in glob.glob(os.path.join(CACHE, "gamelog_*.json")):
        with open(path) as f:
            for row in json.load(f)["data"]:
                out[str(row["Game_ID"]).zfill(10)] = row["GAME_DATE"]
    return out


def minutes_to_float(value):
    if not value:
        return 0.0
    text = str(value)
    if ":" in text:
        mm, _, ss = text.partition(":")
        try:
            return float(mm) + float(ss) / 60.0
        except ValueError:
            return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def load_player_games(dates):
    """One row per player per game he was on the roster for, from the
    cached box scores."""
    rows = []
    for path in glob.glob(os.path.join(CACHE, "boxscore_*.json")):
        game_id = os.path.basename(path)[len("boxscore_"):-len(".json")]
        if not _is_regular_season_game_id(game_id):
            continue
        season = SEASON_BY_PREFIX.get(game_id[:5])
        game_date = dates.get(game_id)
        if season is None or game_date is None:
            continue
        with open(path) as f:
            data = json.load(f)["data"]
        teams = {r["teamId"] for r in data}
        if len(teams) != 2:
            continue
        for r in data:
            opponent = next(t for t in teams if t != r["teamId"])
            minutes = minutes_to_float(r.get("minutes"))
            rows.append({
                "player_id": str(r["personId"]),
                "player_name": f"{r.get('firstName', '')} {r.get('familyName', '')}".strip(),
                "season": season,
                "game_id": game_id,
                "game_date": pd.to_datetime(game_date),
                "team_id": r["teamId"],
                "opponent_team_id": opponent,
                "minutes": minutes,
                "played": minutes > 0,
                "starter": bool(r.get("position")),
                "comment": r.get("comment", "") or "",
                **{col: float(r.get(field) or 0) for col, field in BOX_FIELDS.items()},
                **{col: float(r.get(field) or 0) for col, field in EXTRA_FIELDS.items()},
            })
    return pd.DataFrame(rows)


def add_point_in_time_baselines(games):
    """Mean/std/count of each player's PRIOR played games that season,
    plus the minutes-aware baseline the app actually projects from.

    Two baselines are carried deliberately:
      {col}_flat  the flat per-game average. Still what the lean models
                  call "season average" and measure direction against,
                  and the control every sweep compares to.
      {col}_base  per-minute rate x projected minutes -- what
                  engine/baseline_stats.py now returns, so the numbers
                  fitted here are the numbers the app serves.
    Keeping both means the leans and the calibration can disagree about
    what "baseline" means without either being quietly wrong.
    """
    games = games.sort_values(["player_id", "season", "game_date"], kind="mergesort").copy()
    played = games["played"]
    grp = games.assign(**{f"_{c}": games[c].where(played) for c in BOX_FIELDS}).groupby(
        ["player_id", "season"], sort=False)
    games["n_prior"] = grp["played"].transform(lambda s: s.shift(1).fillna(0).cumsum())
    for col in BOX_FIELDS:
        shifted = grp[f"_{col}"].shift(1)
        by = shifted.groupby([games["player_id"], games["season"]], sort=False)
        games[f"{col}_flat"] = by.transform(lambda s: s.expanding().mean())
        games[f"{col}_std"] = by.transform(lambda s: s.expanding().std())
        games[f"{col}_sum_prior"] = by.transform(lambda s: s.expanding().sum())
    minutes_played = games["minutes"].where(played)
    by_min = minutes_played.groupby([games["player_id"], games["season"]], sort=False)
    games["mpg_prior"] = by_min.transform(lambda s: s.shift(1).expanding().mean())
    games["min_sum_prior"] = by_min.transform(lambda s: s.shift(1).expanding().sum())
    # The recent window counts PLAYED games, not calendar games: a player
    # back from a three-game absence must not have his last-three-minutes
    # window land entirely on games he missed (that gave 1,299 NaNs, and
    # it would have been a silent hole in the fit). engine/minutes.py
    # filters to played games before taking its window; so does this.
    played_only = games.loc[played, ["player_id", "season", "minutes"]]
    recent_played = played_only.groupby(["player_id", "season"], sort=False)["minutes"].transform(
        lambda s: s.shift(1).rolling(minutes.RECENT_WINDOW, min_periods=1).mean())
    games["min_recent"] = recent_played.reindex(games.index)

    # projected = w * recent + (1 - w) * season, exactly engine/minutes.py
    projected = (minutes.RECENT_WEIGHT * games["min_recent"]
                 + (1.0 - minutes.RECENT_WEIGHT) * games["mpg_prior"])
    games["min_projected"] = projected
    enough = games["min_sum_prior"] > 0
    for col in BOX_FIELDS:
        rate = games[f"{col}_sum_prior"].where(enough) / games["min_sum_prior"].where(enough)
        games[f"{col}_base"] = (rate * projected).where(enough, games[f"{col}_flat"])
    return games


def apply_opponent_defense(games):
    """The same month-end checkpoint defence multiplier the old harness
    uses, cached per (season, opponent, month) so 80k rows cost 3 x 30 x
    6 lookups rather than 80k."""
    season_start = dict(SEASONS)
    cache = {}
    rows = []
    for row in games.itertuples():
        game_date = row.game_date.date()
        key = (row.season, row.opponent_team_id, game_date.year, game_date.month)
        if key not in cache:
            lookup = get_point_in_time_opponent_defense(
                row.season, season_start[row.season], row.opponent_team_id, game_date)
            if lookup is None:
                result = get_defense_adjustment(
                    None, None, "season's first calendar month -- excluded, no valid "
                                "point-in-time checkpoint")
            else:
                def_rating, league_avg, note = lookup
                result = get_defense_adjustment(def_rating, league_avg, note)
            cache[key] = result
        rows.append(cache[key])
    return rows


def main():
    print("Reading cached box scores ...")
    dates = game_dates()
    games = load_player_games(dates)
    print(f"  {len(games):,} player-games, {games['game_id'].nunique():,} games, "
          f"{games['player_id'].nunique():,} players")
    games = add_point_in_time_baselines(games)

    eligible = games[(games["n_prior"] >= MIN_BASELINE_GAMES) & games["played"]].copy()
    print(f"  {len(eligible):,} eligible targets (played, and {MIN_BASELINE_GAMES}+ prior "
          f"played games that season), {eligible['player_id'].nunique():,} players")

    print("Applying point-in-time opponent defence ...")
    defense_results = apply_opponent_defense(eligible)

    out = pd.DataFrame({
        "player_id": eligible["player_id"].values,
        "player_name": eligible["player_name"].values,
        "season": eligible["season"].values,
        "game_id": eligible["game_id"].values,
        "game_date": [d.date().isoformat() for d in eligible["game_date"]],
        "opponent_team_id": eligible["opponent_team_id"].values,
        "team_id": eligible["team_id"].values,
        "status": "resolved",
        "minutes": eligible["minutes"].values,
        "mpg_prior": eligible["mpg_prior"].values,
        "starter": eligible["starter"].values,
        "n_prior": eligible["n_prior"].values,
    })
    out["min_projected"] = eligible["min_projected"].values
    for col, _label in STAT_COLUMNS:
        base = eligible[f"{col}_base"].values
        mult = [r.multiplier_for(col) for r in defense_results]
        out[f"{col}_flat"] = eligible[f"{col}_flat"].values
        out[f"{col}_base"] = base
        out[f"{col}_predicted"] = base * mult
        out[f"{col}_actual"] = eligible[col].values
        out[f"{col}_std_prior"] = eligible[f"{col}_std"].values
    out["layers_json"] = [_build_layers_json({"opponent_defense": r}) for r in defense_results]
    out.to_csv(OUTPUT_PATH, index=False)
    print(f"\nWrote {OUTPUT_PATH} ({len(out):,} rows)")

    played = games[games["played"]].copy()
    played["game_date"] = [d.date().isoformat() for d in played["game_date"]]
    played[["player_id", "player_name", "season", "game_id", "game_date", "team_id",
            "opponent_team_id", "minutes", "starter"]
           + list(BOX_FIELDS) + list(EXTRA_FIELDS)].to_csv(GAMES_PATH, index=False)
    print(f"Wrote {GAMES_PATH} ({len(played):,} played games)")

    old_path = os.path.join(REPO_ROOT, "backtest_results.csv")
    if os.path.exists(old_path):
        old = pd.read_csv(old_path, dtype={"player_id": str, "game_id": str})
        old_players = set(zip(old["player_id"], old["season"]))
        new_players = set(zip(out["player_id"], out["season"]))
        print(f"old set: {len(old):,} rows, {len(old_players):,} player-seasons")
        print(f"new set: {len(out):,} rows, {len(new_players):,} player-seasons "
              f"({len(new_players - old_players):,} of them never in the old set)")
        print(f"minutes per game in the new set: median {out['minutes'].median():.1f}, "
              f"{100 * (out['minutes'] < 20).mean():.0f}% under 20 minutes")


if __name__ == "__main__":
    main()
