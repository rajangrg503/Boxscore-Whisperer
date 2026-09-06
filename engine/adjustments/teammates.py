"""Missing-teammate and new-teammate-impact adjustments -- moved from
app.py. Both compare a player's REAL historical stats in games with
vs. without a specific teammate -- a genuine natural experiment, not a
guess -- and both vary by stat column (a center's rebounds and a
guard's assists can move differently), so their `value` dict uses real
STAT_COLUMNS keys rather than the ALL_STATS sentinel.

get_teammate_availability_adjustment is the highest-stakes function
moved so far: it's the one Phase 1 fixed most significantly (removing
the early _live_nba_api_blocked short-circuit and migrating the
per-game boxscore fetch onto cached_or_live() keyed by
boxscore_{game_id}). That fixed behavior is preserved verbatim here --
this move changes the return shape (AdjustmentResult instead of a raw
tuple), not the fetch/caching logic itself.
"""

import time

import pandas as pd
from nba_api.stats.endpoints import boxscoretraditionalv3

from engine.cache import cached_or_live
from engine.game_log import fetch_combined_game_log
from engine.players import get_player_id
from engine.stat_columns import STAT_COLUMNS
from engine.adjustments.base import AdjustmentResult

MISSING_TEAMMATES_LAYER = "missing_teammates"
NEW_TEAMMATE_LAYER = "new_teammate"


def get_teammate_availability_adjustment(player_id, missing_names, season) -> AdjustmentResult:
    """Measures how this player's production differs in real games
    where a specific teammate did NOT play vs. games where they did,
    within the same season -- a genuine natural experiment, not a
    guess.

    Requires a real sample of games BOTH missing the teammate AND with
    them present. If a teammate has left the team entirely, every game
    this season trivially "misses" them -- that's not a genuine
    comparison, so it's detected and skipped rather than silently
    returning a near-meaningless ratio."""
    neutral = {col: 1.0 for col, _ in STAT_COLUMNS}

    if not missing_names:
        return AdjustmentResult(
            layer=MISSING_TEAMMATES_LAYER, value=neutral,
            note="No missing teammates specified -- no adjustment.",
            data_quality="unavailable", sample_n=0, applied=False,
        )

    try:
        df = fetch_combined_game_log(player_id, season)
    except Exception:
        return AdjustmentResult(
            layer=MISSING_TEAMMATES_LAYER, value=neutral,
            note=(f"No game log available for {season} (live fetch failed, not yet "
                  f"cached) -- skipping this adjustment."),
            data_quality="unavailable", sample_n=0, applied=False,
        )

    matching_games = []
    consecutive_failures = 0
    games_checked = 0
    MAX_CONSECUTIVE_FAILURES = 3  # after this many in a row, assume the
                                   # live host is blocked for this whole
                                   # request and stop paying the timeout
                                   # cost on every remaining game
    MAX_GAMES_TO_CHECK = 20        # bound the worst case even when the
                                    # live host IS reachable

    for _, row in df.iterrows():
        if games_checked >= MAX_GAMES_TO_CHECK:
            break
        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            break
        game_id = row["Game_ID"]
        games_checked += 1
        try:
            def _fetch_box():
                box = boxscoretraditionalv3.BoxScoreTraditionalV3(game_id=game_id, timeout=5)
                return box.get_data_frames()[0]

            box_df, _source = cached_or_live(f"boxscore_{game_id}", _fetch_box)
            players_in_game = set(box_df["firstName"] + " " + box_df["familyName"])
        except Exception:
            consecutive_failures += 1
            continue
        consecutive_failures = 0  # reset streak on any success
        time.sleep(0.5)
        if all(name not in players_in_game for name in missing_names):
            matching_games.append(row)

    present_count = games_checked - len(matching_games)

    if len(matching_games) < 3 or present_count < 3:
        return AdjustmentResult(
            layer=MISSING_TEAMMATES_LAYER, value=neutral,
            note=(f"Found {len(matching_games)} game(s) missing {missing_names} "
                  f"out of {games_checked} checked (and {present_count} with them "
                  f"present) -- not enough real contrast in both directions to "
                  f"trust a comparison, skipping this adjustment."),
            data_quality="unavailable", sample_n=len(matching_games), applied=False,
        )

    matched_df = pd.DataFrame(matching_games)
    adjustments = {}
    per_stat_notes = []
    for col, _label in STAT_COLUMNS:
        avg_with_missing = matched_df[col].mean()
        avg_overall = df[col].mean()
        ratio = avg_with_missing / avg_overall if avg_overall else 1.0
        adjustments[col] = ratio
        per_stat_notes.append(
            f"{col} {avg_with_missing:.1f} vs {avg_overall:.1f} overall ({(ratio - 1) * 100:+.1f}%)"
        )

    summary = ", ".join(per_stat_notes)
    sample_n = len(matching_games)
    note = (
        f"Found {sample_n} games missing {missing_names} (vs. {present_count} "
        f"with them present), stat-by-stat: {summary}."
    )
    return AdjustmentResult(
        layer=MISSING_TEAMMATES_LAYER, value=adjustments, note=note,
        data_quality="real_current", sample_n=sample_n, applied=True,
    )


def get_new_teammate_impact_adjustment(player_id, new_teammate_name, season) -> AdjustmentResult:
    """Measures how a specific teammate's HEAVY on-court presence has
    historically correlated with this player's production, using real
    shared games -- not a guess. Splits games where both players were
    on the same team into "teammate played heavy minutes" vs. "teammate
    played light/no minutes", and compares this player's stats between
    those two buckets.

    Only works when the two players have actual shared game history on
    the same team -- a brand-new pairing that has never shared the
    floor has no data to measure an effect from yet, and this function
    says so plainly rather than guessing."""
    neutral = {col: 1.0 for col, _ in STAT_COLUMNS}

    if not new_teammate_name:
        return AdjustmentResult(
            layer=NEW_TEAMMATE_LAYER, value=neutral,
            note="No new teammate specified -- no adjustment.",
            data_quality="unavailable", sample_n=0, applied=False,
        )

    teammate_id, _teammate_full_name, ambiguity_note = get_player_id(new_teammate_name)
    if teammate_id is None:
        return AdjustmentResult(
            layer=NEW_TEAMMATE_LAYER, value=neutral,
            note=f"No player found named '{new_teammate_name}' -- check spelling, skipping.",
            data_quality="unavailable", sample_n=0, applied=False,
        )

    try:
        player_df = fetch_combined_game_log(player_id, season)
        teammate_df = fetch_combined_game_log(teammate_id, season)
    except Exception:
        return AdjustmentResult(
            layer=NEW_TEAMMATE_LAYER, value=neutral,
            note=("Game log unavailable for this season (live fetch failed, not "
                  "yet cached) -- skipping this adjustment."),
            data_quality="unavailable", sample_n=0, applied=False,
        )

    if player_df.empty or teammate_df.empty:
        return AdjustmentResult(
            layer=NEW_TEAMMATE_LAYER, value=neutral,
            note=(f"No shared game history found between this player and "
                  f"{new_teammate_name} yet -- likely a brand-new pairing. This "
                  f"adjustment needs real games played together to measure an "
                  f"effect, so it's skipped rather than guessed at."),
            data_quality="unavailable", sample_n=0, applied=False,
        )

    # Only trust games where they were on the SAME team, not games where
    # they happened to face each other as opponents (that's a different
    # question, already covered by the head-to-head features).
    player_df = player_df.copy()
    teammate_df = teammate_df.copy()
    player_df["_team_abbr"] = player_df["MATCHUP"].str.split().str[0]
    teammate_df["_team_abbr"] = teammate_df["MATCHUP"].str.split().str[0]

    shared = player_df.merge(
        teammate_df[["Game_ID", "MIN", "_team_abbr"]],
        on="Game_ID", suffixes=("", "_teammate"),
    )
    shared = shared[shared["_team_abbr"] == shared["_team_abbr_teammate"]]

    if len(shared) < 5:
        return AdjustmentResult(
            layer=NEW_TEAMMATE_LAYER, value=neutral,
            note=(f"Only found {len(shared)} shared game(s) as teammates with "
                  f"{new_teammate_name} this season -- too few to trust, "
                  f"skipping this adjustment."),
            data_quality="unavailable", sample_n=len(shared), applied=False,
        )

    HEAVY_MINUTES_THRESHOLD = 25
    heavy = shared[shared["MIN_teammate"] >= HEAVY_MINUTES_THRESHOLD]
    light = shared[shared["MIN_teammate"] < HEAVY_MINUTES_THRESHOLD]

    if len(heavy) < 3 or len(light) < 3:
        return AdjustmentResult(
            layer=NEW_TEAMMATE_LAYER, value=neutral,
            note=(f"Found {len(shared)} shared games with {new_teammate_name}, but "
                  f"not enough of a split between heavy-minute and light-minute "
                  f"games ({len(heavy)} vs {len(light)}) to trust a comparison -- "
                  f"skipping."),
            data_quality="unavailable", sample_n=len(shared), applied=False,
        )

    adjustments = {}
    per_stat_notes = []
    for col, _label in STAT_COLUMNS:
        avg_heavy = heavy[col].mean()
        avg_light = light[col].mean()
        ratio = avg_heavy / avg_light if avg_light else 1.0
        adjustments[col] = ratio
        per_stat_notes.append(
            f"{col} {avg_heavy:.1f} vs {avg_light:.1f} ({(ratio - 1) * 100:+.1f}%)"
        )

    summary = ", ".join(per_stat_notes)
    ambiguity_prefix = f"{ambiguity_note} " if ambiguity_note else ""
    sample_n = len(shared)
    note = (
        f"{ambiguity_prefix}"
        f"Found {sample_n} shared games with {new_teammate_name} -- comparing the "
        f"{len(heavy)} game(s) where {new_teammate_name} played "
        f"{HEAVY_MINUTES_THRESHOLD}+ minutes vs. the {len(light)} game(s) with lighter "
        f"minutes, stat-by-stat: {summary}."
    )
    return AdjustmentResult(
        layer=NEW_TEAMMATE_LAYER, value=adjustments, note=note,
        data_quality="real_current", sample_n=sample_n, applied=True,
    )
