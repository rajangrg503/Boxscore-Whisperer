"""Point-in-time helpers for the retroactive backtesting project (see
~/.claude/plans/backtest-engine-plan.md) -- functions that turn "what
would this prediction have used on this real past date" into concrete,
testable logic, kept separate from the live prediction path in
engine/adjustments/ since these only make sense for backtesting.

_checkpoint_date_for() specifically: the piece the whole backtest's
point-in-time-correctness claim rests on. Get this wrong and the
backtest silently leaks future data into past predictions -- see its
docstring and tests/test_backtest_point_in_time.py's boundary cases
for why each one exists.
"""

from datetime import date, timedelta
from typing import Optional

import pandas as pd

from engine.cache import _load_df_cache
from engine.baseline_stats import stats_from_gamelog
from engine.stat_columns import STAT_COLUMNS
from engine.adjustments.defense import get_defense_adjustment
from engine.tracker import _build_layers_json
from engine.team_ids import TEAM_ID_BY_ABBR


def point_in_time_baseline(player_id, season, target_date: date):
    """Returns (stats_dict, n_games) using ONLY games strictly before
    target_date -- the backtest's counterpart to app.py's
    get_season_baseline(), sharing its exact stat computation via
    stats_from_gamelog() rather than re-deriving it.

    Reads the gamelog via _load_df_cache() directly, NOT
    fetch_combined_game_log() -- that function is @st.cache_data/
    session-state-dependent and would attempt a live fetch on a miss.
    Phase B1 already guaranteed every (player, season) pair needed for
    the fixed backtest player list is cached; a miss here means a real
    gap, so this raises rather than silently returning empty stats."""
    cache_key = f"gamelog_{player_id}_{season}"
    df, _cached_at = _load_df_cache(cache_key)
    if df is None:
        raise FileNotFoundError(
            f"Missing required gamelog cache: {cache_key}.json -- should have "
            f"been fetched in Phase B1. Refusing to attempt a live fetch or "
            f"silently return empty stats for a backtest."
        )

    parsed_dates = pd.to_datetime(df["GAME_DATE"])
    past_games = df[parsed_dates < pd.Timestamp(target_date)]
    return stats_from_gamelog(past_games)


def point_in_time_missing_teammate_games(df: pd.DataFrame, target_date: date, max_games: int = 20) -> pd.DataFrame:
    """The subset of a player's cached gamelog `df` that
    engine.adjustments.teammates.get_teammate_availability_adjustment()'s
    presence-check loop may honestly iterate for a backtest prediction
    on target_date: games strictly BEFORE target_date, sorted
    most-recent-first, THEN capped at max_games -- in that order.

    Filtering before capping is the whole point: the cached df's raw
    row order is NOT reliably chronological (fetch_combined_game_log
    simply appends Playoffs games after Regular Season games, not
    date-merged), so capping first risks handing the loop an arbitrary
    mix of past and future games -- not just "too many recent future
    ones.\""""
    parsed = df.copy()
    parsed["_pit_date"] = pd.to_datetime(parsed["GAME_DATE"])
    past = parsed[parsed["_pit_date"] < pd.Timestamp(target_date)]
    past = past.sort_values("_pit_date", ascending=False)
    return past.head(max_games).drop(columns=["_pit_date"])


def get_point_in_time_opponent_defense(season: str, season_start: date, team_id, game_date: date):
    """Resolves a (season, team, game_date) backtest case to the
    correct cached opponent-defense checkpoint, or raises if it isn't
    cached -- NEVER falls back to a live fetch or any other data
    source, since a substitute here is exactly the look-ahead leak
    this whole design exists to prevent.

    Returns None (not an error) when game_date falls in the season's
    first calendar month -- _checkpoint_date_for()'s documented,
    intentional exclusion, not a missing-data bug."""
    checkpoint_date = _checkpoint_date_for(season_start, game_date)
    if checkpoint_date is None:
        return None

    cache_key = f"team_stats_advanced_{season}_through_{checkpoint_date.isoformat()}"
    df, _cached_at = _load_df_cache(cache_key)
    if df is None:
        raise FileNotFoundError(
            f"Missing required checkpoint cache: {cache_key}.json -- should have "
            f"been fetched in Phase B1. Refusing to substitute a live fetch or "
            f"any other data source."
        )

    team_row = df[df["TEAM_ID"] == team_id]
    if team_row.empty:
        raise ValueError(f"Team {team_id} not found in checkpoint {cache_key}")

    def_rating = team_row["DEF_RATING"].values[0]
    league_avg = df["DEF_RATING"].mean()
    return def_rating, league_avg, f"as of {checkpoint_date.isoformat()}"


def _checkpoint_date_for(season_start: date, game_date: date) -> Optional[date]:
    """The opponent-defense monthly checkpoint a prediction for
    `game_date` may honestly use, given the season it belongs to
    started on `season_start`.

    Checkpoints are calendar-month-aligned: the checkpoint for any
    game is the LAST DAY OF THE CALENDAR MONTH IMMEDIATELY BEFORE
    game_date's own month -- never game_date's own month, even on
    that month's last day, since some of that month's games may not
    have been played yet as of any given day within it. Correctness
    comes from the checkpoint's own date_to_nullable scoping the
    underlying query (e.g. Oct 31), not from when the checkpoint file
    happened to be fetched -- a November game can never see November
    data because the October checkpoint was never queried past Oct 31
    in the first place.

    Returns None for a game in the season's own first calendar month:
    no prior checkpoint exists within this season, and reaching into
    an earlier, uncached, unverified season was explicitly rejected
    (see the plan's Phase B1 notes) -- these games are excluded from
    opponent_defense scoring entirely, the same MIN_SAMPLE/reason-
    excluded pattern analytics/layer_accuracy.py already uses, rather
    than approximated with a disclosed caveat."""
    if (game_date.year, game_date.month) == (season_start.year, season_start.month):
        return None
    first_of_this_month = game_date.replace(day=1)
    return first_of_this_month - timedelta(days=1)


# Scope: baseline + opponent_defense ONLY, REGULAR SEASON games only.
#
# missing_teammates, new_teammate, missing_opponents, and defender_matchup
# are all driven by user-supplied input in the live app -- a historical
# replay has no equivalent signal to recover from a box score alone, so
# they stay structurally unexercised here (neutral/not-applied, the same
# state any live query leaves them in when nobody fills in those optional
# fields) -- NOT data-limited, unlike scheme's exclusion.
#
# Playoff games are excluded entirely (see _is_regular_season_game_id()):
# a genuinely different scenario (tighter rotations, elevated defensive
# intensity, small samples) than what this backtest measures, and
# cached gamelogs blend both season types together by default via
# fetch_combined_game_log(), so this has to be filtered explicitly, not
# assumed absent. Discovered concretely, not theoretically: a small-scale
# trial run surfaced a 2024 Finals participant (Jayson Tatum) with games
# into June 2024, past the last cached point-in-time opponent-defense
# checkpoint (March 31) -- excluding playoffs sidesteps both that gap and
# the more fundamental distribution-shift question, rather than papering
# over it with more checkpoint caching.
#
# Any surfaced "backtested across N games" claim must say it measures
# baseline + opponent_defense, REGULAR SEASON games only, not all six
# layers and not the postseason, so this doesn't risk overclaiming later.
MIN_BASELINE_GAMES = 5


def _is_regular_season_game_id(game_id) -> bool:
    """NBA game IDs are prefixed by season type: "0022..." for regular
    season, "0042..." for playoffs (preseason "0012...", All-Star
    "0032...", Play-In uses yet another prefix). Deliberately an
    ALLOWLIST (only "0022..." passes), not a playoffs-specific
    denylist -- any other/unexpected game type is excluded by default
    too, not just the one category this was written to catch."""
    return str(game_id).startswith("0022")


def build_backtest_row(player_id, player_name, season, season_start: date, game_row, min_baseline_games=MIN_BASELINE_GAMES):
    """One real historical game -> one backtest result row, using ONLY
    point-in-time-correct inputs. Returns None (the whole row skipped,
    not just one layer) when fewer than min_baseline_games real prior
    games exist -- there's nothing honest to predict from without a
    baseline at all -- or when the MATCHUP opponent abbreviation can't
    be resolved to a real team_id (skip rather than guess)."""
    game_date = pd.to_datetime(game_row["GAME_DATE"]).date()

    baseline_stats, n_games = point_in_time_baseline(player_id, season, game_date)
    if n_games < min_baseline_games:
        return None

    opponent_abbr = game_row["MATCHUP"].split()[-1]
    opponent_team_id = TEAM_ID_BY_ABBR.get(opponent_abbr)
    if opponent_team_id is None:
        return None

    defense_lookup = get_point_in_time_opponent_defense(season, season_start, opponent_team_id, game_date)
    if defense_lookup is None:
        defense_result = get_defense_adjustment(None, None, "season's first calendar month -- excluded, no valid point-in-time checkpoint")
    else:
        def_rating, league_avg, note = defense_lookup
        defense_result = get_defense_adjustment(def_rating, league_avg, note)

    row = {
        "player_id": player_id,
        "player_name": player_name,
        "season": season,
        "game_id": game_row["Game_ID"],
        "game_date": game_date.isoformat(),
        "opponent_team_id": opponent_team_id,
        "status": "resolved",  # backtest rows are always already-resolved, unlike live pending predictions
    }
    for col, _label in STAT_COLUMNS:
        base_mean, _base_std = baseline_stats[col]
        multiplier = defense_result.multiplier_for(col)
        row[f"{col}_base"] = base_mean
        row[f"{col}_predicted"] = base_mean * multiplier
        row[f"{col}_actual"] = game_row[col]
    row["layers_json"] = _build_layers_json({"opponent_defense": defense_result})
    return row


def run_backtest_for_player(player_id, player_name, season, season_start: date):
    """All backtest result rows for one player's full REGULAR SEASON --
    loads their gamelog once (fails loudly if not cached, same
    principle as point_in_time_baseline), excludes any playoff (or
    other non-regular-season) games via _is_regular_season_game_id()
    -- cached gamelogs blend both season types together by default, so
    this must be filtered explicitly, not assumed absent -- then builds
    one row per remaining game via build_backtest_row(), skipping (not
    erroring on) games that don't have enough prior data yet."""
    cache_key = f"gamelog_{player_id}_{season}"
    df, _cached_at = _load_df_cache(cache_key)
    if df is None:
        raise FileNotFoundError(
            f"Missing required gamelog cache: {cache_key}.json -- should have "
            f"been fetched in Phase B1."
        )

    rows = []
    for _, game_row in df.iterrows():
        if not _is_regular_season_game_id(game_row["Game_ID"]):
            continue
        row = build_backtest_row(player_id, player_name, season, season_start, game_row)
        if row is not None:
            rows.append(row)
    return rows
