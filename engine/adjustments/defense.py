"""Opponent-defense adjustment -- moved from app.py.

The five rating-fetching functions below moved verbatim (same logic,
same caching decorators, same return signatures) -- they're used by
multiple callers (tab1's roster-change branch, tab2's
predict_player_vs_opponent, get_team_profiles which stays in app.py)
and changing their signatures would ripple further than this move
warrants.

get_defense_adjustment() is new: it's the one function both tab1
(Single Player) and tab2 (Full Matchup) now call to turn an
already-resolved DEF_RATING/league-avg pair into an AdjustmentResult.
Previously each tab computed this identical
def_gap_pct/effective_strength/def_adjustment formula inline,
separately -- tab1's copy additionally scaled the strength down by
team_h2h_weight (to avoid double-counting when a team head-to-head
baseline is already blended in), tab2's copy never had that scaling at
all. team_h2h_weight defaults to 0.0 here, which reproduces tab2's old
unscaled x0.5 behavior exactly.

Note on data_quality here: this function only knows the ALREADY-
RESOLVED rating/league-avg/note it was handed -- it doesn't itself
know whether that came from the current season, a previous-season
fallback, or a post-roster-change window (the caller already tracks
that distinction separately via def_source_note's text and, for tab1's
roster-change path, post_change_thin_sample). So data_quality here is
simply "real_current" whenever a real rating was found, "unavailable"
otherwise -- coarser than some other layers' classification, not
because the nuance doesn't exist, but because plumbing it through
would require changing these five functions' return signatures for
every caller. Revisit in Phase 5 if finer granularity turns out to
matter for confidence scoring.
"""

import streamlit as st

from nba_api.stats.endpoints import leaguedashteamstats

from engine.cache import cached_or_live
from engine.season import CURRENT_SEASON, PREVIOUS_SEASON
from engine.adjustments.base import AdjustmentResult, ALL_STATS

LAYER = "opponent_defense"


def get_league_advanced_team_stats(season):
    """Full-league snapshot of DEF_RATING and PACE for every team, in a
    single API call -- this powers both get_team_defensive_rating
    (below) and the 'Opponent Defensive Profile' dropdown labels, so
    they never make two separate calls for the same season.

    Tries a live nba_api call first; falls back to a cached local copy
    (see cached_or_live) if the live call fails -- e.g. nba_api is
    blocked on Streamlit Community Cloud but this season was already
    fetched and cached from a local run."""
    def _fetch():
        stats = leaguedashteamstats.LeagueDashTeamStats(
            season=season, measure_type_detailed_defense="Advanced",
            timeout=5,
        )
        df = stats.get_data_frames()[0]
        cols = ["TEAM_ID", "TEAM_NAME", "DEF_RATING", "PACE", "GP"]
        return df[[c for c in cols if c in df.columns]].copy()

    df, _source = cached_or_live(f"team_stats_advanced_{season}", _fetch)
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def get_team_defensive_rating(team_id, season):
    df = get_league_advanced_team_stats(season)
    if df.empty or "DEF_RATING" not in df.columns:
        return None, None, None
    league_avg = df["DEF_RATING"].mean()
    team_row = df[df["TEAM_ID"] == team_id]
    if team_row.empty:
        return None, league_avg, None
    games_played = team_row["GP"].values[0] if "GP" in team_row.columns else None
    return team_row["DEF_RATING"].values[0], league_avg, games_played


@st.cache_data(ttl=3600, show_spinner=False)
def get_opponent_defense_with_fallback(team_id):
    """Prefer the CURRENT season's defensive rating, since it reflects
    the team's actual roster right now -- trades, injuries, coaching
    changes and all. Last season's full-year number can be genuinely
    stale (e.g. a team that traded away a key defender). Falls back to
    last season only if the current season doesn't have enough games
    played yet to be a reliable read."""
    MIN_GAMES_FOR_CURRENT_SEASON = 5

    def_rating, league_avg, games_played = get_team_defensive_rating(team_id, CURRENT_SEASON)
    if def_rating is not None and games_played is not None and games_played >= MIN_GAMES_FOR_CURRENT_SEASON:
        return def_rating, league_avg, f"{CURRENT_SEASON} so far ({games_played} games) -- reflects current roster"

    prev_def_rating, prev_league_avg, prev_games = get_team_defensive_rating(team_id, PREVIOUS_SEASON)
    note = (
        f"{PREVIOUS_SEASON} full season -- {CURRENT_SEASON} doesn't have enough games "
        f"played yet ({games_played or 0}); this may not reflect recent trades or "
        f"roster changes."
    )
    return prev_def_rating, prev_league_avg, note


def get_league_advanced_team_stats_since(date_from_str):
    """Same idea as get_league_advanced_team_stats, but restricted to
    games from date_from_str (MM/DD/YYYY) onward, current season only.
    Used when a team just made a major trade -- the season-long
    DEF_RATING/PACE blends pre- and post-trade games together, which
    is actively misleading right after a roster shakeup like adding a
    superstar."""
    def _fetch():
        stats = leaguedashteamstats.LeagueDashTeamStats(
            season=CURRENT_SEASON, measure_type_detailed_defense="Advanced",
            date_from_nullable=date_from_str,
            timeout=5,
        )
        df = stats.get_data_frames()[0]
        cols = ["TEAM_ID", "TEAM_NAME", "DEF_RATING", "PACE", "GP"]
        return df[[c for c in cols if c in df.columns]].copy()

    safe_date = date_from_str.replace("/", "-")
    df, _source = cached_or_live(f"team_stats_since_{safe_date}", _fetch)
    return df


def get_opponent_defense_post_change(team_id, change_date):
    """Defensive rating computed only from games since a flagged
    roster-change date. Returns (def_rating, league_avg, note,
    games_played, is_thin_sample). is_thin_sample is True when there
    aren't enough post-change games yet to trust the number much --
    the caller should widen the prediction's uncertainty range in
    that case rather than presenting a false-precision estimate."""
    MIN_GAMES_POST_CHANGE = 3
    date_str = change_date.strftime("%m/%d/%Y")
    try:
        df = get_league_advanced_team_stats_since(date_str)
    except Exception as e:
        return None, None, f"Couldn't fetch post-change data: {e}", 0, True

    if df.empty or "DEF_RATING" not in df.columns:
        return None, None, f"No games found since {change_date.isoformat()} yet.", 0, True

    league_avg = df["DEF_RATING"].mean()
    team_row = df[df["TEAM_ID"] == team_id]
    if team_row.empty:
        return None, None, "Team not found in the post-change window.", 0, True

    games_played = int(team_row["GP"].values[0]) if "GP" in team_row.columns else 0
    is_thin = games_played < MIN_GAMES_POST_CHANGE
    note = (
        f"games since {change_date.isoformat()} only ({games_played} game(s)) -- "
        + ("very small sample, treat this prediction as high-uncertainty"
           if is_thin else "reflects the new roster")
    )
    return team_row["DEF_RATING"].values[0], league_avg, note, games_played, is_thin


def get_defense_adjustment(team_def_rating, league_avg_def, def_source_note,
                            opponent_full_name="the opponent", team_h2h_weight=0.0) -> AdjustmentResult:
    """Turn an already-resolved DEF_RATING/league-avg pair into an
    AdjustmentResult. See module docstring for why this takes resolved
    values rather than fetching them itself, and for the
    team_h2h_weight/data_quality design notes."""
    DEF_ADJUSTMENT_STRENGTH = 0.5

    if team_def_rating is None:
        return AdjustmentResult(
            layer=LAYER, value={ALL_STATS: 1.0},
            note="Opponent defensive rating unavailable -- no adjustment.",
            data_quality="unavailable", sample_n=0, applied=False,
        )

    def_gap_pct = (team_def_rating - league_avg_def) / league_avg_def
    effective_strength = DEF_ADJUSTMENT_STRENGTH * (1 - team_h2h_weight)
    def_adjustment = 1 + (def_gap_pct * effective_strength)
    note = (f"{opponent_full_name} DEF_RATING: {team_def_rating:.1f} "
            f"(league avg {league_avg_def:.1f}, source: {def_source_note}) "
            f"-> adjustment 🛡️ Opponent Defense Layer Applied — x{def_adjustment:.3f}")
    if team_h2h_weight > 0:
        note += (f" (scaled down from the usual x{DEF_ADJUSTMENT_STRENGTH} strength "
                 f"since the baseline already carries {team_h2h_weight:.0%} team head-to-head weight)")

    return AdjustmentResult(
        layer=LAYER, value={ALL_STATS: def_adjustment}, note=note,
        data_quality="real_current", sample_n=0, applied=True,
    )
