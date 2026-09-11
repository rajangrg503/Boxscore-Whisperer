"""
Boxscore Whisperer -- Streamlit web app version.

Run locally with: streamlit run app.py
Deploy for free at: share.streamlit.io (Streamlit Community Cloud)

IMPORTANT HONESTY NOTE:
This is a transparent ADJUSTMENT tool, not a trained machine learning
model. Early in a new season especially, there won't be much current-
season data to work with, so this leans on last season's numbers plus
rough percentage adjustments. Treat the output as an informed estimate
range, not a confident forecast.

KNOWN RISK: nba_api may be blocked or rate-limited when running on a
cloud host (like Streamlit Community Cloud), even though it works fine
locally. If data fetching fails after deployment, that's the likely
cause -- see the error message shown in the app for details.
"""

import time
import os
import uuid
import datetime
import pandas as pd
import altair as alt
import streamlit as st
from nba_api.stats.static import players, teams
from nba_api.stats.endpoints import (
    playergamelog,
    playercareerstats,
    leaguedashteamstats,
    boxscoretraditionalv3,
    synergyplaytypes,
    leagueseasonmatchups,
)

from engine.players import get_player_id, get_team_id
from engine.career_stats import resolve_season_mpg
from engine.season import CURRENT_SEASON, PREVIOUS_SEASON
from engine.stat_columns import STAT_COLUMNS
from engine.tracker import (
    LOG_PATH,
    load_prediction_log,
    append_prediction_to_log,
    append_predictions_batch,
    refresh_pending_predictions,
)
from engine.game_log import fetch_combined_game_log, resolve_season_gamelog

# ---------- Local-to-cloud data cache ----------
# Moved to engine/cache.py (CACHE_DIR, cached_or_live, etc.) -- see that
# module's docstring for the cloud-blocking rationale. WORKFLOW: run
# refresh_all.py locally to populate/update data_cache/*.json (each
# endpoint gated by data_watchdog/ before it's allowed to refresh), then
# commit and push data_cache/ so the deployed app picks it up.
from engine.cache import cached_or_live
from engine.adjustments.missing_players import get_opponent_missing_adjustment
from engine.adjustments.defender import get_defender_matchup_adjustment
from engine.adjustments.scheme import get_synergy_scheme_adjustment, SCHEME_ADJUSTMENTS
from engine.adjustments.teammates import (
    get_teammate_availability_adjustment,
    get_new_teammate_impact_adjustment,
    get_out_redistribution_adjustment,
)
from engine.adjustments.defense import (
    get_league_advanced_team_stats,
    get_opponent_defense_with_fallback,
    get_opponent_defense_post_change,
    get_defense_adjustment,
)
from analytics.layer_accuracy import build_layer_lines
from engine.confidence import score_prediction
from engine.baseline_stats import stats_from_gamelog


# ---------- Data functions (same logic as the terminal version) ----------
# get_player_id / get_team_id now live in engine/players.py (imported above) --
# get_player_id disambiguates same-name players (e.g. an active vs. a retired
# "Brandon Williams") instead of blindly trusting the first regex match.

# Prediction tracker (LOG_PATH, load/save/append/resolve) now lives in
# engine/tracker.py (imported above) -- hardened with file locking
# (fixes a lost-update race between concurrent saves) and atomic
# writes (fixes torn writes on a crash mid-write). Schema extended
# with {STAT}_base and layers_json, built from AdjustmentResult
# objects at save time.


HEAD_TO_HEAD_SEASONS = [CURRENT_SEASON, PREVIOUS_SEASON, "2024-25", "2023-24"]


@st.cache_data(ttl=3600, show_spinner=False)
def get_head_to_head_log(player_id, opponent_abbr, cutoff_date=None):
    """Pull the player's actual game-by-game history specifically
    against this opponent, across the last few seasons -- including
    seasons where the player was on a different team, since that
    context (like Luka as a Maverick vs. as a Laker) matters.

    cutoff_date, if given (a datetime.date), excludes games before that
    date. Use this when the opponent just made a major roster change --
    games against their old roster represent a functionally different
    opponent and would otherwise pollute the average."""
    frames = []
    for season in HEAD_TO_HEAD_SEASONS:
        try:
            df = fetch_combined_game_log(player_id, season)
        except Exception:
            continue  # this season unavailable (blocked live + not cached) -- skip, don't fail the whole lookup
        if df.empty:
            continue
        matched = df[df["MATCHUP"].str.contains(opponent_abbr, na=False)]
        if not matched.empty:
            frames.append(matched)
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    combined["GAME_DATE"] = pd.to_datetime(combined["GAME_DATE"])
    combined = combined.sort_values("GAME_DATE", ascending=False).reset_index(drop=True)
    if cutoff_date is not None:
        combined = combined[combined["GAME_DATE"].dt.date >= cutoff_date].reset_index(drop=True)
    return combined


def get_head_to_head_baseline(player_id, opponent_abbr, num_games, cutoff_date=None):
    """Build a baseline (mean/std per stat) from the player's most
    recent N games against this specific opponent, instead of their
    full season average. Falls back gracefully (returns None) if no
    head-to-head games exist at all. Returns (stats_dict, source_label,
    actual_n) -- actual_n lets the caller apply shrinkage blending
    against the season baseline (see blend_baseline_stats)."""
    h2h_df = get_head_to_head_log(player_id, opponent_abbr, cutoff_date=cutoff_date)
    if h2h_df.empty:
        note = "No head-to-head games found vs. this opponent -- falling back to season average."
        if cutoff_date is not None:
            note = (f"No head-to-head games found since {cutoff_date.isoformat()} "
                     f"(post-roster-change) -- falling back to season average.")
        return None, note, 0

    subset = h2h_df.head(num_games)  # already sorted most-recent-first
    stats_dict = {}
    for col, _ in STAT_COLUMNS:
        stats_dict[col] = (subset[col].mean(), subset[col].std())

    actual_n = len(subset)
    shortfall_note = f" (only {actual_n} available)" if actual_n < num_games else ""
    since_note = f", since {cutoff_date.isoformat()} only" if cutoff_date is not None else ""
    source = (f"Last {actual_n} game(s) vs. this opponent{shortfall_note}, "
              f"spanning {', '.join(HEAD_TO_HEAD_SEASONS)}{since_note}")
    return stats_dict, source, actual_n


def blend_baseline_stats(season_stats, shrinkage_k=4, team_h2h=None, team_h2h_n=0,
                          extra_sources=None):
    """Blend season average, team head-to-head, and any number of
    extra sources -- e.g. head-to-head vs. one specific opponent
    player, or vs. a specific combination of opponent players on the
    floor together -- weighted by how many real games back each one.
    The season average always contributes as if it had shrinkage_k
    games, so a handful of head-to-head games can't swing the estimate
    on their own; any source earns more real influence as more actual
    games accumulate.

    extra_sources: list of (label, stats_dict, n) tuples. stats_dict
    may be None (no data found) -- such entries are skipped in the
    blend but still reported at weight 0 in the returned dict, so the
    caller can show why a given source didn't contribute.

    Returns (blended_stats_dict, weights_dict)."""
    extra_sources = extra_sources or []
    total_n = shrinkage_k + team_h2h_n + sum(n for _, stats, n in extra_sources if stats is not None)
    blended = {}
    for col, _ in STAT_COLUMNS:
        season_mean, season_std = season_stats[col]
        season_std = season_std if pd.notna(season_std) else 0
        acc_mean = shrinkage_k * season_mean
        spreads = [season_std]
        if team_h2h is not None and team_h2h_n > 0:
            t_mean, t_std = team_h2h[col]
            acc_mean += team_h2h_n * t_mean
            spreads.append(t_std if pd.notna(t_std) else 0)
        for _label, stats_dict, n in extra_sources:
            if stats_dict is None or n <= 0:
                continue
            m, s = stats_dict[col]
            acc_mean += n * m
            spreads.append(s if pd.notna(s) else 0)
        blended_mean = acc_mean / total_n
        # Keep the widest spread among contributing sources -- blending
        # several averages together shouldn't look more confident than
        # any single source actually supports.
        blended[col] = (blended_mean, max(spreads))
    weights = {
        "season": shrinkage_k / total_n,
        "team_h2h": team_h2h_n / total_n,
    }
    for label, stats_dict, n in extra_sources:
        weights[label] = (n / total_n) if stats_dict is not None else 0.0
    return blended, weights


@st.cache_data(ttl=3600, show_spinner=False)
def get_multi_season_log(player_id, seasons=HEAD_TO_HEAD_SEASONS):
    """A player's combined game log (regular season + playoffs) across
    several seasons, sorted most-recent-first. Shared helper used by
    the vs-specific-player matchup lookup below."""
    frames = []
    for season in seasons:
        try:
            df = fetch_combined_game_log(player_id, season)
        except Exception:
            continue  # this season unavailable -- skip, don't fail the whole lookup
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    combined["GAME_DATE"] = pd.to_datetime(combined["GAME_DATE"])
    combined = combined.sort_values("GAME_DATE", ascending=False).reset_index(drop=True)
    return combined


def get_head_to_head_vs_player(player_id, opponent_player_id, seasons=HEAD_TO_HEAD_SEASONS):
    """Real games where player_id faced opponent_player_id as an
    opponent -- found by intersecting each player's own Game_ID list,
    NOT by filtering on a team abbreviation. This is the right lens
    for a matchup that has followed a specific player across a trade:
    team-based head-to-head would silently drop every game before the
    trade even though the actual opposing player (and much of the
    defensive assignment) is the same person. Since neither player is
    ever on both sides of a game, a shared Game_ID between their two
    logs always means they were opponents that night, on whatever team
    the opponent happened to be playing for at the time.
    Returns (matched_games_df, note)."""
    target_log = get_multi_season_log(opponent_player_id, seasons)
    if target_log.empty:
        return pd.DataFrame(), "No games found for that player in this window."

    player_log = get_multi_season_log(player_id, seasons)
    if player_log.empty:
        return pd.DataFrame(), "No games found for this player in this window."

    shared_ids = set(target_log["Game_ID"]) & set(player_log["Game_ID"])
    if not shared_ids:
        return pd.DataFrame(), "No shared games found between these two players in this window."

    matched = player_log[player_log["Game_ID"].isin(shared_ids)].copy()
    return matched, f"{len(matched)} game(s) found across {', '.join(seasons)}"


def get_vs_player_baseline(player_id, opponent_player_id, num_games=10):
    """Baseline (mean/std per stat) built from the player's most recent
    games specifically against another player, regardless of which
    team that player was on. Returns (stats_dict, source_label,
    actual_n) -- same shape as get_head_to_head_baseline, so it slots
    into blend_baseline_stats the same way."""
    matched_df, note = get_head_to_head_vs_player(player_id, opponent_player_id)
    if matched_df.empty:
        return None, note, 0

    subset = matched_df.head(num_games)
    stats_dict = {}
    for col, _ in STAT_COLUMNS:
        stats_dict[col] = (subset[col].mean(), subset[col].std())
    actual_n = len(subset)
    source = f"{actual_n} game(s) vs. this player specifically (any team), spanning {', '.join(HEAD_TO_HEAD_SEASONS)}"
    return stats_dict, source, actual_n


def get_head_to_head_vs_player_combo(player_id, opponent_player_ids, seasons=HEAD_TO_HEAD_SEASONS):
    """Real games where player_id faced ALL of opponent_player_ids at
    once, on the same opposing team -- found by intersecting every
    player's Game_ID list together, same logic as
    get_head_to_head_vs_player but extended to a group. Since a game
    only has two teams and player_id is never on both sides, every
    opponent player whose Game_ID appears in this intersection was
    necessarily on the SAME opposing team that night.

    This is the right question for a newly formed pairing (e.g. two
    stars who just became teammates via trade): it directly answers
    whether this specific combination has ever been faced before,
    rather than approximating it from each player's separate history.
    An empty result here is itself the informative answer -- it means
    the combination is genuinely unprecedented, not a data gap to
    paper over.
    Returns (matched_games_df, note)."""
    if len(opponent_player_ids) < 2:
        return pd.DataFrame(), "Need at least 2 players to check a combination."

    player_log = get_multi_season_log(player_id, seasons)
    if player_log.empty:
        return pd.DataFrame(), "No games found for this player in this window."

    shared_ids = set(player_log["Game_ID"])
    for opp_id in opponent_player_ids:
        opp_log = get_multi_season_log(opp_id, seasons)
        if opp_log.empty:
            return pd.DataFrame(), "No games found for one of these players in this window."
        shared_ids &= set(opp_log["Game_ID"])
        if not shared_ids:
            break

    if not shared_ids:
        return pd.DataFrame(), "No games found with all of these players on the same team, in this window."

    matched = player_log[player_log["Game_ID"].isin(shared_ids)].copy()
    return matched, f"{len(matched)} game(s) found across {', '.join(seasons)}"


@st.cache_data(ttl=3600, show_spinner=False)
def get_season_baseline(player_id, player_name):
    """Returns (stats_dict, source_label, n_games). stats_dict maps each
    stat column in STAT_COLUMNS to a (mean, std) tuple -- named here
    instead of listed out, since a hardcoded list in this docstring has
    already drifted out of date twice (missing FG3A, now missing OREB
    too) as new stats were added elsewhere without updating this
    comment. Using a dict here instead of a long positional tuple avoids
    the kind of unpacking-count bugs that come from adding a new stat
    later and forgetting to update every call site. n_games is the real
    game count this baseline is drawn from -- same number already
    embedded in source_label's prose, exposed as an actual int here so
    callers (e.g. engine/confidence.py's baseline_sample_n) don't have
    to parse it back out of a display string.

    Season selection (CURRENT_SEASON, falling back to PREVIOUS_SEASON
    when there aren't enough current-season games yet or the live fetch
    fails outright) is resolve_season_gamelog()'s job now -- moved
    there verbatim so engine/adjustments/teammates.py's redistribution
    adjustment can share the exact same season-resolution rule instead
    of re-deriving it. This function's own return shape and behavior
    are unchanged by that move."""
    df, _season, source = resolve_season_gamelog(player_id)
    stats_dict, n_games = stats_from_gamelog(df)

    return stats_dict, source, n_games


# get_league_advanced_team_stats, get_team_defensive_rating,
# get_opponent_defense_with_fallback now live in
# engine/adjustments/defense.py (imported above).


@st.cache_data(ttl=3600, show_spinner=False)
def get_team_profiles():
    """Build a pace/defense label for every team, using the same
    current-season-with-fallback rule as get_opponent_defense_with_fallback
    (prefer current season once it has enough games played, else last
    season's full-season numbers). Labels are computed from each team's
    percentile rank within this snapshot, not hardcoded -- so they stay
    accurate as the season progresses. Returns a dict keyed by TEAM_ID:
    {"pace_label", "def_label", "def_rating", "pace", "source_season"}.
    Returns {} if data couldn't be fetched (e.g. blocked on a cloud host)."""
    MIN_GAMES_FOR_CURRENT_SEASON = 5

    try:
        current_df = get_league_advanced_team_stats(CURRENT_SEASON)
    except Exception:
        current_df = pd.DataFrame()
    try:
        prev_df = get_league_advanced_team_stats(PREVIOUS_SEASON)
    except Exception:
        prev_df = pd.DataFrame()

    enough_current_games = (
        not current_df.empty
        and "GP" in current_df.columns
        and current_df["GP"].max() >= MIN_GAMES_FOR_CURRENT_SEASON
    )
    df = current_df if enough_current_games else prev_df
    source_season = CURRENT_SEASON if enough_current_games else PREVIOUS_SEASON

    if df.empty or "PACE" not in df.columns or "DEF_RATING" not in df.columns:
        return {}

    # Percentile rank within this snapshot. Pace: higher PACE = faster,
    # so rank ascending (higher percentile = faster team). DEF_RATING:
    # LOWER = better defense (fewer points allowed per 100 possessions),
    # so we rank ascending too but read low percentile as "best defense".
    pace_rank = df["PACE"].rank(pct=True)
    def_rank = df["DEF_RATING"].rank(pct=True, ascending=True)

    def _pace_label(pct):
        if pct >= 0.66:
            return "Fast"
        if pct >= 0.33:
            return "Medium"
        return "Slow"

    def _def_label(pct):
        # low pct = low DEF_RATING = best defense
        if pct <= 0.25:
            return "Elite Def"
        if pct <= 0.5:
            return "Top Def"
        if pct <= 0.75:
            return "Med Def"
        return "Weak Def"

    profiles = {}
    for idx, row in df.iterrows():
        profiles[row["TEAM_ID"]] = {
            "pace_label": _pace_label(pace_rank[idx]),
            "def_label": _def_label(def_rank[idx]),
            "def_rating": row["DEF_RATING"],
            "pace": row["PACE"],
            "source_season": source_season,
        }
    return profiles


# get_league_advanced_team_stats_since, get_opponent_defense_post_change,
# and get_defense_adjustment now live in engine/adjustments/defense.py
# (imported above).


def get_opponent_dropdown_options():
    """Team full names labeled with a quick pace/defense read, e.g.
    'Boston Celtics (Balanced / Top Def)' -- sourced from real season
    stats via get_team_profiles(), not hardcoded presets. Falls back to
    the plain team name if profile data isn't available (e.g. the API
    call failed) so the dropdown still works either way. Returns
    (options_list, label_to_full_name_dict)."""
    all_teams = teams.get_teams()
    profiles = get_team_profiles()
    options = []
    label_to_name = {}
    for t in sorted(all_teams, key=lambda x: x["full_name"]):
        profile = profiles.get(t["id"])
        if profile:
            pace_tag = "Balanced" if profile["pace_label"] == "Medium" else profile["pace_label"]
            label = f'{t["full_name"]} ({pace_tag} / {profile["def_label"]})'
        else:
            label = t["full_name"]
        options.append(label)
        label_to_name[label] = t["full_name"]
    return options, label_to_name


# get_teammate_availability_adjustment and get_new_teammate_impact_adjustment
# now live in engine/adjustments/teammates.py (imported above) -- return
# an AdjustmentResult instead of a raw tuple.


# get_opponent_missing_adjustment now lives in engine/adjustments/missing_players.py
# (imported above) -- returns an AdjustmentResult instead of a raw tuple.

# _safe_float and get_defender_matchup_adjustment now live in
# engine/adjustments/defender.py (imported above) -- returns an
# AdjustmentResult instead of a raw tuple.

# get_synergy_scheme_adjustment, SCHEME_ADJUSTMENTS, and
# SCHEME_TO_SYNERGY_PLAYTYPE now live in engine/adjustments/scheme.py
# (imported above) -- returns an AdjustmentResult instead of a raw tuple.

@st.cache_data(ttl=3600, show_spinner=False)
def get_full_game_log(player_id, season):
    """Fetch a player's full game log for a season (regular season +
    playoffs blended), sorted most-recent first. Used for the hit-rate
    table and trend chart."""
    try:
        df = fetch_combined_game_log(player_id, season)
    except Exception:
        return pd.DataFrame()
    if df.empty:
        return df
    df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"])
    df = df.sort_values("GAME_DATE", ascending=False).reset_index(drop=True)
    return df


# ---------------------------- Streamlit UI ----------------------------

st.set_page_config(page_title="Boxscore Whisperer", page_icon="🏀", layout="centered")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800;900&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, sans-serif;
}

.stApp {
    background-color: #0b0e14;
}

/* Hero header, StatMuse-style bold title on dark background */
.hero-title {
    font-size: 44px;
    font-weight: 900;
    color: #ffffff;
    text-align: center;
    letter-spacing: -1px;
    margin-bottom: 4px;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 12px;
}
.hero-title svg {
    width: 44px;
    height: 44px;
    flex-shrink: 0;
}
.hero-subtitle {
    font-size: 16px;
    color: #9ca3af;
    text-align: center;
    margin-bottom: 48px;  /* patch_hero_breathing_room */
}
.legal-disclaimer {
    max-width: 640px;
    margin: 0 auto 32px auto;
    padding: 12px 18px;
    border: 1px solid #262a33;
    border-radius: 12px;
    background-color: #171a21;
    color: #9ca3af;
    font-size: 11.5px;
    line-height: 1.5;
    text-align: center;
}
.legal-disclaimer a {
    color: #34d399;
    text-decoration: underline;
}
.legal-disclaimer a:hover {
    color: #00e676;
}
.methodology-teaser {
    max-width: 640px;
    margin: 0 auto 4px auto;
    padding: 12px 18px;
    border: 1px solid #262a33;
    border-radius: 12px;
    background-color: #171a21;
    color: #9ca3af;
    font-size: 11.5px;
    line-height: 1.5;
    text-align: center;
}
.methodology-table {
    width: 100%;
    border-collapse: collapse;
    margin: 16px 0;
    font-size: 13px;
}
.methodology-table th, .methodology-table td {
    padding: 8px 12px;
    text-align: center;
    border-bottom: 1px solid #262a33;
}
.methodology-table th {
    color: #9ca3af;
    font-weight: 700;
    text-transform: uppercase;
    font-size: 11px;
    letter-spacing: 0.5px;
}
.methodology-table td {
    color: #ffffff;
}

/* Search-bar-style container around the form */
div[data-testid="stForm"] {
    background-color: #171a21;
    border-radius: 20px;
    padding: 28px 24px 12px 24px;
    border: 1px solid #262a33;
    box-shadow: 0 2px 12px rgba(0,0,0,0.3);
}

/* Green accent button, StatMuse-style */
div[data-testid="stFormSubmitButton"] button {
    background-color: #00c853;
    color: white;
    font-weight: 700;
    border-radius: 12px;
    border: none;
    padding: 10px 28px;
    font-size: 16px;
    width: 100%;
}
div[data-testid="stFormSubmitButton"] button:hover {
    background-color: #00b34a;
    color: white;
}

/* Success banner */
div[data-testid="stAlertContentSuccess"] {
    background-color: #0d2818;
    border-left: 4px solid #00c853;
}

/* st.caption() text, forced visible against the dark background */
div[data-testid="stCaptionContainer"],
div[data-testid="stCaptionContainer"] p {
    color: #9ca3af !important;
    text-align: center;
}

/* Custom stat cards -- replaces st.metric, which had unreliable
   label contrast. These use the same badge classes as hit-rate. */
.stat-card-row {
    display: flex;
    gap: 12px;
    margin: 8px 0 24px 0;
}
.stat-card {
    flex: 1;
    text-align: center;
    background-color: #171a21;
    border: 1px solid #262a33;
    border-radius: 16px;
    padding: 16px 12px;
}
.stat-card .stat-title {
    font-size: 13px;
    font-weight: 700;
    color: #9ca3af;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 6px;
}
.stat-card .stat-value {
    font-size: 26px;
    font-weight: 800;
    color: #ffffff;
}
.stat-card .stat-midpoint {
    font-size: 12px;
    font-weight: 700;
    color: #00e676;
    margin-top: 4px;
}

/* Player avatar -- designed circle, not a real photo/likeness. See
   TEAM_COLORS + get_player_team_and_number() for why. */
.player-avatar-wrap {
    display: flex;
    flex-direction: column;
    align-items: center;
    margin: 20px 0 4px 0;
}
.player-avatar-circle {
    width: 120px;
    height: 120px;
    border-radius: 50%;
    border: 3px solid #262a33;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 2px;
}
.player-avatar-circle svg {
    width: 42px;
    height: 42px;
    opacity: 0.9;
}
.player-avatar-jersey {
    font-size: 20px;
    font-weight: 700;
    color: #ffffff;
    line-height: 1;
}
.player-avatar-name {
    margin-top: 10px;
    font-size: 14px;
    color: #e5e7eb;
    font-weight: 500;
}
.player-avatar-team {
    font-size: 12px;
    color: #9ca3af;
}

div[data-testid="stForm"] label,
div[data-testid="stForm"] label p {
    color: #e5e7eb !important;
    font-weight: 600 !important;
    font-size: 14px !important;
}

/* Fix: input/select text and placeholder contrast on dark inputs */
div[data-testid="stForm"] input,
div[data-testid="stForm"] select {
    color: #ffffff !important;
    background-color: #0e1117 !important;
}
div[data-testid="stForm"] input::placeholder {
    color: #6b7280 !important;
}
/* Search-first hero row: big player/opponent dropdowns */
.search-row div[data-baseweb="select"] {
    border-radius: 14px;
}
div[data-testid="stForm"] div[data-baseweb="select"] > div {
    background-color: #0e1117;
    border: 1px solid #2a2e38;
}
div[data-testid="stForm"] div[data-baseweb="select"] input {
    color: #ffffff !important;
}

/* Advanced options expander -- understated, secondary */
div[data-testid="stExpander"] {
    border: none;
    background-color: transparent;
}
div[data-testid="stExpander"] summary {
    color: #9ca3af;
    font-weight: 600;
    font-size: 14px;
}
/* Text inside the opened expander body, dark-theme readable */
div[data-testid="stExpander"] div[data-testid="stMarkdownContainer"],
div[data-testid="stExpander"] div[data-testid="stMarkdownContainer"] p {
    color: #d1d5db !important;
}

/* Per-prediction confidence badge -- ONE per prediction (not per stat
   card, since score_prediction() scores the whole prediction), shown
   prominently between the header and the stat cards, not buried in
   the "how this was built" expander. Reuses the same dark-tinted-bg +
   bright-text pairing used for other status pills in this app (same
   visual language), plus one new amber pair for Medium. */
.confidence-badge-wrap {
    display: flex;
    justify-content: center;
    margin: 4px 0 16px 0;
}
.confidence-badge {
    display: inline-block;
    padding: 8px 20px;
    border-radius: 999px;
    font-size: 14px;
    font-weight: 800;
    letter-spacing: 0.3px;
    text-shadow: 0 1px 2px rgba(0,0,0,0.4);
}
.confidence-high {
    background-color: #0d2818;
    color: #34d399;
}
.confidence-medium {
    background-color: #2d2410;
    color: #fbbf24;
}
.confidence-low {
    background-color: #2d1215;
    color: #f87171;
}
</style>
""", unsafe_allow_html=True)

# ---------- Prediction Tracker (sidebar, always visible) ----------
with st.sidebar:
    st.markdown("### 📊 Prediction Tracker")
    tracker_email = st.text_input(
        "Your email (to find your saved predictions)",
        key="tracker_email_input",
        placeholder="you@example.com",
    )
    st.caption(
        "Just a filter, not a login -- no password, nothing verified. "
        "Anyone who enters this exact email sees the same predictions."
    )
    if st.button("🔄 Check for results", key="refresh_tracker_btn"):
        with st.spinner("Checking saved predictions against real results..."):
            refresh_pending_predictions(get_head_to_head_log)

    entered_email = tracker_email.strip().lower()
    if not entered_email:
        st.caption("Enter your email above to see your saved predictions.")
    else:
        log_df = load_prediction_log()
        my_log_df = log_df[log_df["saved_by_email"].fillna("") == entered_email]

        if my_log_df.empty:
            st.caption("No saved predictions yet for this email. Save one after running a prediction below.")
        else:
            resolved = my_log_df[my_log_df["status"] == "resolved"]
            pending = my_log_df[my_log_df["status"] == "pending"]
            no_game = my_log_df[my_log_df["status"] == "no_game_found"]

            st.caption(
                f"{len(my_log_df)} saved -- {len(resolved)} resolved, "
                f"{len(pending)} pending, {len(no_game)} no game found."
            )

            if not resolved.empty:
                st.markdown("**Accuracy so far (Points):**")
                pts_hits = resolved["PTS_hit"].dropna()
                if len(pts_hits) > 0:
                    hit_rate = pts_hits.mean() * 100
                    st.metric("Points landed in range", f"{hit_rate:.0f}%", f"{int(pts_hits.sum())}/{len(pts_hits)}")

            with st.expander("View all saved predictions"):
                display_log = my_log_df[[
                    "saved_at", "source", "player_full_name", "opponent_full_name", "game_date",
                    "status", "PTS_low", "PTS_mid", "PTS_high", "PTS_actual", "PTS_hit",
                ]].copy()
                # Every row saved before Full Matchup tracking existed
                # predates the source column entirely (NaN, not "" --
                # this column never had an empty-string default the way
                # saved_by_email does) -- but every one of those rows
                # WAS a single-player save, since Full Matchup had no
                # save feature until now. Filling the display value is
                # an accurate inference from when the column was added,
                # not a fabricated guess.
                display_log["source"] = display_log["source"].fillna("single_player")
                display_log = display_log.rename(columns={
                    "saved_at": "Saved", "source": "Source", "player_full_name": "Player",
                    "opponent_full_name": "Opponent", "game_date": "Game Date",
                    "status": "Status", "PTS_low": "Pts Low", "PTS_mid": "Pts Mid",
                    "PTS_high": "Pts High", "PTS_actual": "Pts Actual", "PTS_hit": "Pts Hit?",
                })
                st.dataframe(display_log, use_container_width=True, hide_index=True)
                st.caption(
                    "Showing Points only here for space -- all 8 tracked stats are saved "
                    f"in the underlying file at {os.path.basename(LOG_PATH)}."
                )

st.markdown(
    '''<div class="hero-title">
    <svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg">
        <path d="M6 24 Q1 32 6 40" stroke="#00c853" stroke-width="3.5" fill="none" stroke-linecap="round" opacity="0.55"/>
        <path d="M11 20 Q4 32 11 44" stroke="#00c853" stroke-width="3.5" fill="none" stroke-linecap="round"/>
        <circle cx="40" cy="32" r="19" fill="#ff8c42"/>
        <path d="M21 32 A19 19 0 0 1 59 32" stroke="#171a21" stroke-width="2" fill="none"/>
        <line x1="40" y1="13" x2="40" y2="51" stroke="#171a21" stroke-width="2"/>
        <path d="M24 19 Q40 32 24 45" stroke="#171a21" stroke-width="2" fill="none"/>
        <path d="M56 19 Q40 32 56 45" stroke="#171a21" stroke-width="2" fill="none"/>
    </svg>
    Boxscore Whisperer
    </div>''',
    unsafe_allow_html=True,
)
st.markdown(
    '<div class="hero-subtitle">A transparent statline estimate tool -- not a trained ML model. '
    'Every adjustment is shown so you can judge it yourself.</div>',
    unsafe_allow_html=True,
)
st.markdown(
    '<div class="legal-disclaimer">'
    'Boxscore Whisperer is an independent, unofficial statistical tool and is not '
    'affiliated with, endorsed by, or connected to the NBA, its teams, or the National '
    'Basketball Players Association. All player and team data is sourced from publicly '
    'available statistics. Predictions are transparent statistical estimates, not '
    'guarantees -- for entertainment and informational purposes only, not betting advice. '
    'If sports betting is a concern for you, resources are available at '
    '<a href="https://ncpgambling.org" target="_blank">ncpgambling.org</a> '
    'or 1-800-GAMBLER. Must be 18+ (or the legal age in your jurisdiction) to use any '
    'information here in connection with wagering.'
    '</div>',
    unsafe_allow_html=True,
)
st.markdown(
    '<div class="methodology-teaser">'
    'Backtested across 29,914 real player-games from the last 3 NBA seasons, using only '
    'data available before each game — no lookahead. See methodology ↓'
    '</div>',
    unsafe_allow_html=True,
)
with st.expander("📊 See methodology"):
    st.markdown(
        "**How we know this works**\n\n"
        "Most prediction tools show you a number and ask you to trust it. We'd rather "
        "show you the evidence.\n\n"
        "Before launch, we ran Boxscore Whisperer's core prediction engine against every "
        "eligible regular-season game from the last three NBA seasons -- 2023-24 through "
        "2025-26 -- using only data that would have genuinely been available before each "
        "game was played. No lookahead, no using a season's final stats to \"predict\" "
        "its opening week. That's 29,914 real, independently verified predictions.\n\n"
        "Here's what we found: our season-baseline predictions are solid. Our "
        "opponent-defense adjustment currently adds a small, statistically real but "
        "practically modest edge over the raw baseline -- and for some stats, "
        "essentially none yet. We're not going to round that up. We think a tool that "
        "only tells you the flattering parts isn't one you should trust with real "
        "decisions, so we're publishing this now, and we'll publish updates as we keep "
        "working on it."
    )
    st.markdown(
        '<table class="methodology-table">'
        '<tr><th>Stat</th><th>Directional Accuracy</th><th>N</th></tr>'
        '<tr><td>PTS</td><td>51.8%</td><td>29,791</td></tr>'
        '<tr><td>AST</td><td>51.6%</td><td>29,610</td></tr>'
        '<tr><td>REB</td><td>50.6%</td><td>29,663</td></tr>'
        '<tr><td>STL</td><td>51.1%</td><td>29,456</td></tr>'
        '<tr><td>BLK</td><td>50.4%</td><td>29,339</td></tr>'
        '<tr><td>FG3M</td><td>50.4%</td><td>28,138</td></tr>'
        '<tr><td>TOV</td><td>49.2%</td><td>29,523</td></tr>'
        '<tr><td>FG3A</td><td>50.6%</td><td>28,849</td></tr>'
        '<tr><td>OREB</td><td>50.2%</td><td>29,540</td></tr>'
        '</table>',
        unsafe_allow_html=True,
    )

# Pull the current name lists once per session for the searchable
# dropdowns -- typing inside these boxes filters the list live, no
# typos possible since selections come from a real, known list.
@st.cache_data(ttl=86400, show_spinner=False)
def get_player_name_list():
    active = players.get_active_players()
    return sorted(p["full_name"] for p in active)

@st.cache_data(ttl=86400, show_spinner=False)
def get_team_name_list():
    all_teams = teams.get_teams()
    return sorted(t["full_name"] for t in all_teams)

player_names = get_player_name_list()
team_names = get_team_name_list()

# Real official team primary colors, for the player-avatar circle
# background (see get_player_team_and_number() below). Hardcoded because
# neither nba_api.stats.static.teams nor any cached data in this repo
# provides team colors -- confirmed by checking both before adding this.
# Where a team's brand has both a dark and a light official color (e.g.
# Warriors blue/yellow, Nuggets navy/gold, Suns purple/orange), the
# darker one is used here so white icon/text stays legible -- verified
# via WCAG contrast ratio, worst case (Hawks/Blazers red) is 4.34:1,
# every team clears the 3:1 minimum for large graphics/text.
TEAM_COLORS = {
    1610612737: "#E03A3E",  # ATL Hawks
    1610612738: "#007A33",  # BOS Celtics
    1610612751: "#000000",  # BKN Nets
    1610612766: "#1D1160",  # CHA Hornets
    1610612741: "#CE1141",  # CHI Bulls
    1610612739: "#860038",  # CLE Cavaliers
    1610612742: "#00538C",  # DAL Mavericks
    1610612743: "#0E2240",  # DEN Nuggets
    1610612765: "#C8102E",  # DET Pistons
    1610612744: "#1D428A",  # GSW Warriors
    1610612745: "#CE1141",  # HOU Rockets
    1610612754: "#002D62",  # IND Pacers
    1610612746: "#C8102E",  # LAC Clippers
    1610612747: "#552583",  # LAL Lakers
    1610612763: "#5D76A9",  # MEM Grizzlies
    1610612748: "#98002E",  # MIA Heat
    1610612749: "#00471B",  # MIL Bucks
    1610612750: "#0C2340",  # MIN Timberwolves
    1610612740: "#0C2340",  # NOP Pelicans
    1610612752: "#006BB6",  # NYK Knicks
    1610612760: "#007AC1",  # OKC Thunder
    1610612753: "#0077C0",  # ORL Magic
    1610612755: "#006BB6",  # PHI 76ers
    1610612756: "#1D1160",  # PHX Suns
    1610612757: "#E03A3E",  # POR Trail Blazers
    1610612758: "#5A2D81",  # SAC Kings
    1610612759: "#000000",  # SAS Spurs
    1610612761: "#CE1141",  # TOR Raptors
    1610612762: "#002B5C",  # UTA Jazz
    1610612764: "#002B5C",  # WAS Wizards
}
TEAM_COLOR_FALLBACK = "#262a33"  # this app's existing border-gray, used when a player isn't found on any cached roster


def _load_roster_df(team_id):
    """Shared cache read behind get_team_roster() and
    get_player_team_and_number() -- one cached_or_live() call per team,
    not duplicated between the two callers. Returns the raw roster
    DataFrame (all commonteamroster columns), or None if neither a live
    fetch nor a cached copy is available. Same timeout=5/10 retry and
    Cloud-fallback behavior as before (see get_team_roster's docstring).
    """  # patch_roster_cache_fallback
    from nba_api.stats.endpoints import commonteamroster

    def _fetch():
        last_error = None
        for attempt_timeout in (5, 10):
            try:
                roster = commonteamroster.CommonTeamRoster(
                    team_id=team_id, season=CURRENT_SEASON, timeout=attempt_timeout
                )
                return roster.get_data_frames()[0]
            except Exception as e:
                last_error = e
                print(
                    f"[_load_roster_df] attempt (timeout={attempt_timeout}) failed "
                    f"for team_id={team_id}: {type(e).__name__}: {e}"
                )
        raise last_error if last_error else RuntimeError("_load_roster_df: no attempts made")

    try:
        df, _source = cached_or_live(f"roster_{team_id}", _fetch)
    except Exception as e:
        print(f"[_load_roster_df] no live or cached roster for team_id={team_id}: {e}")
        return None
    return df


def get_team_roster(team_id):
    """Pull current roster for a team (cached, with live fallback -- see
    _load_roster_df()). Returns a list of (player_id, player_name)
    tuples, or [] if no roster is available."""
    df = _load_roster_df(team_id)
    if df is None or df.empty:
        return []
    return list(zip(df["PLAYER_ID"], df["PLAYER"]))


def get_player_team_and_number(player_id):
    """Resolve a player's current team and jersey number by scanning
    every team's cached roster (data_cache/roster_{team_id}.json, the
    same cache get_team_roster() reads) for this player_id -- no new
    fetch, this data is already on disk for all 30 teams.

    Returns (team_id, team_abbr, team_full_name, jersey) where jersey is
    a string like "23", or (None, None, None, "-") if the player isn't
    found on any current roster (e.g. a very recent signing not yet
    cached, or a free agent) -- callers must degrade gracefully, not
    assume a match.
    """
    for t in teams.get_teams():
        df = _load_roster_df(t["id"])
        if df is None or df.empty:
            continue
        match = df[df["PLAYER_ID"] == player_id]
        if not match.empty:
            num = match.iloc[0]["NUM"]
            jersey = str(int(num)) if pd.notna(num) else "-"
            return t["id"], t["abbreviation"], t["full_name"], jersey
    return None, None, None, "-"


tab1, tab2 = st.tabs(["Single Player", "Full Matchup"])  # patch_tabs_split

with tab1:
    with st.form("predictor_form"):
        st.markdown('<div class="search-row">', unsafe_allow_html=True)
        col1, col2 = st.columns(2)
        with col1:
            player_input = st.selectbox(
                "Player", options=player_names, index=None, placeholder="Search a player..."
            )
        with col2:
            opponent_options, opponent_label_to_name = get_opponent_dropdown_options()
            opponent_label_input = st.selectbox(
                "Opponent — pace / defense at a glance", options=opponent_options, index=None,
                placeholder="Search a team...", key="opponent_select",
            )
            opponent_input = opponent_label_to_name.get(opponent_label_input) if opponent_label_input else None
        st.markdown('</div>', unsafe_allow_html=True)
        st.caption(
            "Pace and defense tags are computed live from this season's team stats "
            "(falling back to last season early in the year) — not fixed presets."
        )

        baseline_source_input = st.selectbox(
            "Baseline source",
            ["Season average (default)", "Last 5 games vs. this opponent", "Last 10 games vs. this opponent"],
            index=0,
        )
        st.caption(
            "Head-to-head baselines use real games vs. this specific opponent -- more "
            "relevant if a player has a real history against this team, but based on a "
            "much smaller sample than a full season."
        )
        raw_baseline_input = st.checkbox(
            "Use only this source, no season blending",
            value=False,
        )
        st.caption(
            "By default, even a head-to-head baseline is blended with the season "
            "average for reliability (a handful of games can't fully override a "
            "full season on their own). Check this to use the selected baseline "
            "source on its own instead -- only applies when a head-to-head option "
            "is selected above."
        )

        with st.expander("Advanced options (injuries, defender, scheme -- optional)"):
            adv1, adv2 = st.columns(2)
            with adv1:
                missing_teammates = st.multiselect(
                    "Missing teammates", options=player_names, default=[],
                    placeholder="Search and select players...", max_selections=5,
                )
                new_teammate_input = st.selectbox(
                    "New teammate arriving (optional)", options=player_names, index=None,
                    placeholder="Search a player who just joined...",
                )
                st.caption(
                    "Uses real shared games to compare this player's stats when this "
                    "teammate played heavy minutes vs. light minutes. Needs actual "
                    "shared game history to work -- a pairing that hasn't shared the "
                    "floor yet will be flagged, not guessed at."
                )
                defender_input = st.selectbox(
                    "Primary defender assigned", options=player_names, index=None,
                    placeholder="Search a defender...",
                )
            with adv2:
                missing_opponents = st.multiselect(
                    "Missing opponent players", options=player_names, default=[],
                    placeholder="Search and select players...", max_selections=5,
                )
                scheme_input = st.selectbox("Defensive scheme", list(SCHEME_ADJUSTMENTS.keys()))
                scheme_executor_input = st.selectbox(
                    "Scheme executed primarily by (reference only -- optional)",
                    options=player_names, index=None, placeholder="Search a player...",
                )
                st.caption(
                    "No public data tracks which player runs a specific scheme, "
                    "so this name is stored for your own reference only -- it "
                    "doesn't affect the calculation."
                )

            st.markdown("---")
            roster_change_checked = st.checkbox(
                "Opponent just made a major roster change (trade, etc.)",
            )
            roster_change_date = None
            if roster_change_checked:
                roster_change_date = st.date_input(
                    "Change effective date", value=None,
                )
                st.caption(
                    "When set, opponent defense and head-to-head history use only "
                    "games since this date. The season-long average otherwise blends "
                    "pre- and post-change games together -- misleading right after a "
                    "big trade (e.g. a star player switching teams). Predictions "
                    "based on a very small post-change sample will show a wider "
                    "likely range to reflect the extra uncertainty."
                )

            st.markdown("---")  # patch_expander_spacing
            key_players_input = st.multiselect(
                "Also check history vs. specific opposing player(s) (optional)",
                options=player_names, default=[],
                placeholder="e.g. a star who just changed teams...", max_selections=4,
            )
            st.caption(
                "Finds every real game this player has faced them, on whatever team "
                "they were on at the time -- not just games against their current "
                "team. Select two or more players (e.g. a new frontcourt pairing) "
                "to also check whether they've ever shared the floor as opponents "
                "before -- if not, that's flagged rather than papered over."
            )

        submitted = st.form_submit_button("Predict statline")

    if submitted:
        if not player_input or not opponent_input:
            st.error("Please select both a player and an opponent team.")
            st.stop()

        with st.spinner("Pulling data and calculating..."):
            player_id, player_full_name, player_ambiguity_note = get_player_id(player_input)
            if player_id is None:
                st.error(f"No player found for '{player_input}'. Check spelling.")
                st.stop()
            if player_ambiguity_note:
                st.warning(player_ambiguity_note)

            opponent_id, opponent_full_name, opponent_abbr = get_team_id(opponent_input)
            if opponent_id is None:
                st.error(f"No team found for '{opponent_input}'. Use the full team name.")
                st.stop()

            roster_change_active = roster_change_checked and roster_change_date is not None
            h2h_cutoff = roster_change_date if roster_change_active else None

            try:
                season_stats, season_source, season_n = get_season_baseline(player_id, player_full_name)

                team_h2h_stats, team_h2h_n = None, 0
                team_h2h_note = None
                if baseline_source_input != "Season average (default)":
                    num_games = 5 if "Last 5" in baseline_source_input else 10
                    team_h2h_stats, team_h2h_note, team_h2h_n = get_head_to_head_baseline(
                        player_id, opponent_abbr, num_games, cutoff_date=h2h_cutoff
                    )

                # Fold the primary defender into the "vs specific player(s)"
                # comparison too, so picking one field doesn't leave the other's
                # real-game head-to-head data and hit-rate tables empty -- no
                # need to type the same name twice. The visible multiselect
                # widget above is untouched; this only affects what gets
                # calculated and shown.
                effective_key_players_input = list(key_players_input)
                if defender_input and defender_input not in effective_key_players_input:
                    effective_key_players_input.append(defender_input)

                # Resolve every selected key player to an ID up front.
                key_player_ids = {}
                for name in effective_key_players_input:
                    pid, full_name, _key_player_ambiguity_note = get_player_id(name)
                    key_player_ids[name] = (pid, full_name)

                combo_stats, combo_n, combo_note = None, 0, None
                no_combo_data = False
                valid_ids = [pid for pid, _ in key_player_ids.values() if pid is not None]
                if len(valid_ids) >= 2:
                    combo_df, combo_note = get_head_to_head_vs_player_combo(player_id, valid_ids)
                    if not combo_df.empty:
                        subset = combo_df.head(10)
                        combo_stats = {col: (subset[col].mean(), subset[col].std()) for col, _ in STAT_COLUMNS}
                        combo_n = len(subset)
                    else:
                        no_combo_data = True

                extra_sources = []
                individual_notes = {}  # name -> (stats, note, n), for display + build-panel
                if combo_stats is not None:
                    # The exact combination has real data -- use it directly
                    # instead of each player's separate history, since the
                    # combo already captures whatever interaction effect
                    # exists between them (spacing, shared minutes, etc.)
                    # that blending two individual signals could not.
                    names_label = " + ".join(key_player_ids.keys())
                    extra_sources.append((f"vs. {names_label} together", combo_stats, combo_n))
                else:
                    # No combo data (or fewer than 2 players selected) --
                    # fall back to each player's individual history, kept
                    # as separate weighted sources.
                    for name, (pid, full_name) in key_player_ids.items():
                        if pid is None:
                            individual_notes[name] = (None, "No player found with that name.", 0)
                            continue
                        stats, note, n = get_vs_player_baseline(player_id, pid)
                        individual_notes[name] = (stats, note, n)
                        extra_sources.append((f"vs. {name}", stats, n))

                any_extra_data = any(n > 0 for _, stats, n in extra_sources if stats is not None)
                if team_h2h_n == 0 and not any_extra_data:
                    baseline_stats, source = season_stats, season_source
                    blend_weights = {"season": 1.0, "team_h2h": 0.0}
                else:
                    if raw_baseline_input and team_h2h_n > 0:
                        baseline_stats, blend_weights = blend_baseline_stats(
                            season_stats, shrinkage_k=0,
                            team_h2h=team_h2h_stats, team_h2h_n=team_h2h_n,
                            extra_sources=extra_sources,
                        )
                    else:
                        baseline_stats, blend_weights = blend_baseline_stats(
                            season_stats, team_h2h=team_h2h_stats, team_h2h_n=team_h2h_n,
                            extra_sources=extra_sources,
                        )
                    parts = [f"season avg {blend_weights['season']:.0%}"]
                    if team_h2h_n > 0:
                        parts.append(f"team h2h {blend_weights['team_h2h']:.0%} ({team_h2h_note})")
                    elif baseline_source_input != "Season average (default)" and team_h2h_note:
                        parts.append(f"team h2h unavailable ({team_h2h_note})")
                    for label, stats, n in extra_sources:
                        if n > 0 and stats is not None:
                            parts.append(f"{label} {blend_weights[label]:.0%} ({n} game(s))")
                        else:
                            parts.append(f"{label} unavailable")
                    source = "Blended baseline -- " + "; ".join(parts)
                    if no_combo_data:
                        combo_label = " + ".join(key_player_ids.keys())
                        source += (f" -- NOTE: no historical games found with {combo_label} on the same "
                                   f"team together ({combo_note}); this combination appears to be new, "
                                   f"so the estimate reflects each individually, not their combined effect")

                # Real games actually behind `baseline_stats` above -- season_n
                # plus every real (non-shrinkage-prior) source that contributed,
                # for engine/confidence.py's baseline_sample_n input. Computed
                # the same way regardless of which branch above fired: in the
                # season-only branch team_h2h_n and the extra_sources sum are
                # both 0 by the very condition that selected that branch, so
                # this reduces to season_n there without a separate case.
                baseline_sample_n = season_n + team_h2h_n + sum(
                    n for _, stats, n in extra_sources if stats is not None
                )
            except Exception as e:
                st.error(
                    f"Couldn't fetch data from the NBA stats API: {e}\n\n"
                    "This can happen when running on a cloud server -- the NBA's unofficial "
                    "API sometimes blocks requests from hosting providers even though it "
                    "works fine locally."
                )
                st.stop()

            post_change_thin_sample = False
            if roster_change_active:
                pt_def_rating, pt_league_avg, pt_note, pt_games, pt_thin = get_opponent_defense_post_change(
                    opponent_id, roster_change_date
                )
                if pt_def_rating is not None:
                    team_def_rating, league_avg_def, def_source_note = pt_def_rating, pt_league_avg, pt_note
                    post_change_thin_sample = pt_thin
                else:
                    team_def_rating, league_avg_def, def_source_note = get_opponent_defense_with_fallback(opponent_id)
                    def_source_note = f"{def_source_note} (post-change data unavailable: {pt_note})"
            else:
                team_def_rating, league_avg_def, def_source_note = get_opponent_defense_with_fallback(opponent_id)

            # The defense-strength multiplier is scaled down only by the
            # TEAM head-to-head weight, not the vs-player weight -- a team
            # h2h average already implicitly reflects that team's overall
            # defense, so stacking the full team-wide DEF_RATING adjustment
            # on top would partly double-count it. A vs-player average
            # reflects that one matchup, not the rest of the team's
            # defense, so it doesn't create the same redundancy.
            team_h2h_weight = blend_weights["team_h2h"]
            defense_result = get_defense_adjustment(
                team_def_rating, league_avg_def, def_source_note,
                opponent_full_name=opponent_full_name, team_h2h_weight=team_h2h_weight,
            )
            def_note = defense_result.note

            if missing_teammates:
                teammate_result = None
                for _try_season in HEAD_TO_HEAD_SEASONS:
                    teammate_result = get_teammate_availability_adjustment(
                        player_id, missing_teammates, _try_season
                    )
                    if teammate_result.applied:
                        break
            else:
                teammate_result = get_teammate_availability_adjustment(
                    player_id, missing_teammates, CURRENT_SEASON
                )
            teammate_note = teammate_result.note
            if new_teammate_input:
                # Check the full multi-season window (same one used for
                # opponent head-to-head elsewhere) rather than stopping
                # after just one fallback season -- a real pairing can sit
                # further back if one of the two players has since been
                # traded away. Uses AdjustmentResult.applied (True only
                # when a genuine comparison was found) to decide whether
                # to keep looking, not fragile text-matching.
                new_teammate_result = None
                for _try_season in HEAD_TO_HEAD_SEASONS:
                    new_teammate_result = get_new_teammate_impact_adjustment(
                        player_id, new_teammate_input, _try_season
                    )
                    if new_teammate_result.applied:
                        break
            else:
                new_teammate_result = get_new_teammate_impact_adjustment(
                    player_id, new_teammate_input, CURRENT_SEASON
                )
            new_teammate_note = new_teammate_result.note
            opp_missing_result = get_opponent_missing_adjustment(
                missing_opponents, PREVIOUS_SEASON
            )
            opp_missing_note = opp_missing_result.note
            defender_result = get_defender_matchup_adjustment(
                player_id, player_full_name, defender_input, CURRENT_SEASON
            )
            defender_note = defender_result.note

            scheme_result = get_synergy_scheme_adjustment(
                opponent_id, scheme_input, PREVIOUS_SEASON
            )
            scheme_note = scheme_result.note

            # Same set of adjustments applied proportionally to every
            # tracked stat -- reasonable since opponent strength, missing
            # teammates, and scheme plausibly affect all of them together,
            # though this is less rigorously tested for stats other than
            # points specifically. Each AdjustmentResult is looked up
            # per-stat below via multiplier_for() -- opp_missing/scheme/
            # defense are uniform across stats today, teammate/new_teammate
            # genuinely vary by stat.

            # A thin post-roster-change sample (a team's new-look defense
            # with only a handful of games played) is a genuinely less
            # certain read than a full-season number -- widen the likely
            # range rather than presenting the same false precision.
            THIN_SAMPLE_SPREAD_MULTIPLIER = 1.5

            predictions = {}
            for col, _label in STAT_COLUMNS:
                base_mean, base_std = baseline_stats[col]
                stat_multiplier = (
                    defense_result.multiplier_for(col)
                    * opp_missing_result.multiplier_for(col)
                    * scheme_result.multiplier_for(col)
                    * teammate_result.multiplier_for(col)
                    * new_teammate_result.multiplier_for(col)
                )
                predicted = base_mean * stat_multiplier
                spread = base_std if pd.notna(base_std) else predicted * 0.2
                if post_change_thin_sample:
                    spread *= THIN_SAMPLE_SPREAD_MULTIPLIER
                low = max(0, predicted - spread * 0.6)
                high = predicted + spread * 0.6
                predictions[col] = {
                    "base": base_mean,
                    "predicted": predicted,
                    "low": low,
                    "high": high,
                }

            # If a head-to-head baseline was chosen, keep hit rates and the
            # trend chart consistent with that same team-specific context
            # instead of mixing a head-to-head baseline with season-wide
            # hit rates. Falls back to season-wide if no h2h games exist.
            using_h2h = baseline_source_input != "Season average (default)"
            if using_h2h:
                game_log_for_hitrate = get_head_to_head_log(player_id, opponent_abbr, cutoff_date=h2h_cutoff)
                if game_log_for_hitrate.empty:
                    using_h2h = False  # nothing to show -- fall back below

            if not using_h2h:
                try:
                    current_season_check = fetch_combined_game_log(player_id, CURRENT_SEASON)
                except Exception:
                    current_season_check = pd.DataFrame()
                hitrate_season = CURRENT_SEASON if len(current_season_check) >= 5 else PREVIOUS_SEASON
                game_log_for_hitrate = get_full_game_log(player_id, hitrate_season)

        # Stash everything needed to render results into session_state.
        # This matters because the trend-chart stat picker below is a
        # widget OUTSIDE this form -- changing it triggers a script rerun
        # where `submitted` goes back to False (the button wasn't clicked
        # in that rerun). Without session_state, the whole results section
        # would vanish the moment someone touched the chart picker.
        st.session_state["results"] = {
            "player_id": player_id,
            "player_full_name": player_full_name,
            "opponent_full_name": opponent_full_name,
            "opponent_abbr": opponent_abbr,
            "source": source,
            "predictions": predictions,
            # Additive, for layers_json (engine/tracker.py) -- duplicates the
            # *_note strings below by design, not by oversight (those stay
            # for the existing "how this was built" display panel, which
            # reads them directly; revisit if that panel gets refactored).
            "layer_results": {
                res.layer: res for res in [
                    defense_result, opp_missing_result, scheme_result,
                    teammate_result, new_teammate_result, defender_result,
                ]
            },
            # Additive, for engine/confidence.py (Phase 5) -- real games
            # actually behind `baseline_stats` above, computed once at
            # save time (see the "Real games actually behind..." comment
            # near blend_baseline_stats' call sites).
            "baseline_sample_n": baseline_sample_n,
            "def_note": def_note,
            "teammate_note": teammate_note,
            "new_teammate_note": new_teammate_note,
            "opp_missing_note": opp_missing_note,
            "defender_note": defender_note,
            "scheme_note": scheme_note,
            "scheme_executor_input": scheme_executor_input,
            "game_log": game_log_for_hitrate,
            "using_h2h": using_h2h,
            "h2h_cutoff": h2h_cutoff,
            "roster_change_active": roster_change_active,
            "post_change_thin_sample": post_change_thin_sample,
            "key_player_ids": key_player_ids,
            "effective_key_players_input": effective_key_players_input,
            "no_combo_data": no_combo_data,
            "valid_ids": valid_ids,
        }

    if "results" in st.session_state:
        r = st.session_state["results"]
        player_id = r["player_id"]
        player_full_name = r["player_full_name"]
        opponent_full_name = r["opponent_full_name"]
        opponent_abbr = r["opponent_abbr"]
        source = r["source"]
        predictions = r["predictions"]
        layer_results = r["layer_results"]
        baseline_sample_n = r["baseline_sample_n"]
        def_note = r["def_note"]
        teammate_note = r["teammate_note"]
        new_teammate_note = r["new_teammate_note"]
        opp_missing_note = r["opp_missing_note"]
        defender_note = r["defender_note"]
        scheme_note = r["scheme_note"]
        scheme_executor_input = r["scheme_executor_input"]
        game_log_for_hitrate = r["game_log"]
        using_h2h = r["using_h2h"]
        h2h_cutoff = r["h2h_cutoff"]
        roster_change_active = r["roster_change_active"]
        post_change_thin_sample = r["post_change_thin_sample"]
        key_player_ids = r["key_player_ids"]
        effective_key_players_input = r["effective_key_players_input"]
        no_combo_data = r["no_combo_data"]
        valid_ids = r["valid_ids"]

        _avatar_team_id, _avatar_team_abbr, _avatar_team_full, _avatar_jersey = get_player_team_and_number(player_id)
        _avatar_color = TEAM_COLORS.get(_avatar_team_id, TEAM_COLOR_FALLBACK)
        _avatar_team_label = _avatar_team_full or "Team unavailable"
        st.markdown(
            f'<div class="player-avatar-wrap">'
            f'<div class="player-avatar-circle" style="background-color:{_avatar_color};">'
            f'<svg viewBox="0 0 24 24" fill="#ffffff"><path d="M12 12c2.7 0 4.9-2.2 4.9-4.9S14.7 2.2 12 2.2 7.1 4.4 7.1 7.1 9.3 12 12 12zm0 2.4c-3.3 0-9.8 1.6-9.8 4.9v2.5h19.6v-2.5c0-3.3-6.5-4.9-9.8-4.9z"/></svg>'
            f'<span class="player-avatar-jersey">#{_avatar_jersey}</span>'
            f'</div>'
            f'<div class="player-avatar-name">{player_full_name}</div>'
            f'<div class="player-avatar-team">{_avatar_team_label}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        st.markdown(
            f'<div style="text-align:center; font-size:22px; font-weight:800; '
            f'color:#ffffff; margin: 8px 0 16px 0;">'
            f'{player_full_name} <span style="color:#9ca3af; font-weight:600;">vs</span> {opponent_full_name}'
            f'</div>',
            unsafe_allow_html=True,
        )

        confidence_result = score_prediction(layer_results, baseline_sample_n)
        _confidence_css_class = {
            "High": "confidence-high",
            "Medium": "confidence-medium",
            "Low": "confidence-low",
        }[confidence_result.label]
        st.markdown(
            f'<div class="confidence-badge-wrap">'
            f'<span class="confidence-badge {_confidence_css_class}">{confidence_result.label.upper()} CONFIDENCE</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        if confidence_result.label != "High" and confidence_result.reasons:
            st.caption(" • ".join(confidence_result.reasons[:2]))

        def render_stat_card_row(stat_cols):
            html = '<div class="stat-card-row">'
            for col in stat_cols:
                label = dict(STAT_COLUMNS)[col]
                p = predictions[col]
                html += (
                    f'<div class="stat-card">'
                    f'<div class="stat-title">{label}</div>'
                    f'<div class="stat-value">{p["predicted"]:.1f}</div>'
                    f'<div class="stat-midpoint" title="Likely range reflects prediction uncertainty -- narrower with more data, wider with thin samples.">likely range {p["low"]:.0f}-{p["high"]:.0f}</div><!-- patch_likely_range_tooltip -->'
                    f'</div>'
                )
            html += '</div>'
            st.markdown(html, unsafe_allow_html=True)

        render_stat_card_row(["PTS", "AST", "REB", "OREB"])
        render_stat_card_row(["STL", "BLK", "FG3M", "FG3A", "TOV"])
        st.caption(
            "This isn't a raw season average -- it's that average adjusted for opponent "
            "defense, missing teammates, and scheme, using the math shown in \"See how this "
            "estimate was built\" below. The unadjusted season average is shown separately "
            "there in step [1] for comparison. \"Likely range\" reflects this player's "
            "real game-to-game variability."
        )

        # Save this prediction to the tracker, so you can come back after
        # the actual game and see how close it was.
        with st.container():
            save_col1, save_col2 = st.columns([2, 1])
            with save_col1:
                tracked_game_date = st.date_input(
                    "Game date (for tracking accuracy later -- optional)",
                    value=None,
                    key="tracked_game_date_input",
                )
            with save_col2:
                st.write("")  # vertical spacer to align button with input
                if st.button("💾 Save to tracker", key="save_prediction_btn"):
                    save_email = st.session_state.get("tracker_email_input", "").strip().lower()
                    if not save_email:
                        st.warning(
                            "Enter your email in the Prediction Tracker (sidebar) first, "
                            "so you can find this prediction again."
                        )
                    else:
                        new_id = append_prediction_to_log(
                            player_id, player_full_name, opponent_full_name,
                            opponent_abbr, tracked_game_date, predictions,
                            layer_results=layer_results, saved_by_email=save_email,
                        )
                        st.success(f"Saved (id: {new_id}). Check the Prediction Tracker in the sidebar later.")

        # Recent trend chart -- reuses the same game log already fetched
        # for hit rates, no extra API call. Lives outside the form so
        # switching stats doesn't require resubmitting the whole prediction.
        st.markdown(
            '<div style="text-align:center; color:#ffffff; font-weight:700; '
            'font-size:16px; margin-top:28px;">Recent Trend</div>',
            unsafe_allow_html=True,
        )
        trend_stat_label = st.selectbox(
            "Stat to chart",
            [label for _col, label in STAT_COLUMNS],
            index=0,
            key="trend_stat_selector",
        )
        trend_stat_col = {label: col for col, label in STAT_COLUMNS}[trend_stat_label]

        recent_games = game_log_for_hitrate.head(15).copy()
        recent_games = recent_games.sort_values("GAME_DATE")  # oldest -> newest, left to right
        chart_df = recent_games[["GAME_DATE", "MATCHUP", trend_stat_col]].rename(
            columns={trend_stat_col: "value"}
        )

        trend_chart = (
            alt.Chart(chart_df)
            .mark_line(point=alt.OverlayMarkDef(color="#00c853", size=60), color="#00c853")
            .encode(
                x=alt.X("GAME_DATE:T", title="Game date"),
                y=alt.Y("value:Q", title=trend_stat_label),
                tooltip=["GAME_DATE:T", "MATCHUP:N", "value:Q"],
            )
            .properties(height=280)
            .configure(background="#171a21")
            .configure_axis(labelColor="#9ca3af", titleColor="#9ca3af",
                             gridColor="#262a33", domainColor="#262a33")
            .configure_view(strokeWidth=0)
        )
        st.altair_chart(trend_chart, use_container_width=True)
        trend_context = f"vs. {opponent_full_name} only" if using_h2h else "overall"
        st.caption(f"Last {len(recent_games)} games ({trend_context}).")

        # Head-to-head history vs this specific opponent, across the last
        # few seasons -- including seasons on a different team, since that
        # context (e.g. a player traded to a new team) genuinely matters
        # for how they've performed against this particular opponent.
        st.markdown(
            f'<div style="text-align:center; color:#ffffff; font-weight:700; '
            f'font-size:18px; margin-top:32px;">Head-to-Head vs {opponent_full_name}</div>',
            unsafe_allow_html=True,
        )
        h2h_df = get_head_to_head_log(player_id, opponent_abbr, cutoff_date=h2h_cutoff)
        if h2h_df.empty:
            if roster_change_active:
                st.caption(
                    f"No games found against {opponent_full_name} since "
                    f"{roster_change_date.isoformat()} (post-roster-change) yet."
                )
            else:
                st.caption(f"No games found against {opponent_full_name} in the last few seasons.")
        else:
            display_cols = ["GAME_DATE", "MATCHUP", "MIN", "PTS", "REB", "OREB", "AST", "STL", "BLK", "FG3M", "FG3A", "TOV"]
            display_cols = [c for c in display_cols if c in h2h_df.columns]
            h2h_display = h2h_df[display_cols].copy()
            h2h_display["GAME_DATE"] = h2h_display["GAME_DATE"].dt.strftime("%-m/%-d/%Y")
            h2h_display = h2h_display.rename(columns={
                "GAME_DATE": "Date", "MATCHUP": "Matchup", "MIN": "Min", "PTS": "Pts",
                "REB": "Reb", "OREB": "OReb", "AST": "Ast", "STL": "Stl", "BLK": "Blk",
                "FG3M": "3PM", "FG3A": "3PA", "TOV": "TOV",
            })
            st.dataframe(h2h_display, use_container_width=True, hide_index=True)
            if roster_change_active:
                st.caption(
                    f"{len(h2h_df)} game(s) since {roster_change_date.isoformat()} only -- "
                    f"earlier games are excluded since they were against this opponent's old roster."
                )
            else:
                st.caption(
                    f"{len(h2h_df)} game(s) found across the last few seasons "
                    f"({', '.join(HEAD_TO_HEAD_SEASONS)}), including any prior teams."
                )

        if effective_key_players_input:
            for name in effective_key_players_input:
                pid, _full_name = key_player_ids.get(name, (None, None))
                st.markdown(
                    f'<div style="text-align:center; color:#ffffff; font-weight:700; '
                    f'font-size:18px; margin-top:32px;">Head-to-Head vs {name} '
                    f'<span style="color:#9ca3af; font-weight:500; font-size:13px;">(any team)</span></div>',
                    unsafe_allow_html=True,
                )
                if pid is None:
                    st.caption(f"No player found for '{name}'.")
                    continue
                vs_player_df, _ = get_head_to_head_vs_player(player_id, pid)
                if vs_player_df.empty:
                    st.caption(f"No shared games found against {name} in this window.")
                    continue
                vp_display_cols = ["GAME_DATE", "MATCHUP", "MIN", "PTS", "REB", "OREB", "AST", "STL", "BLK", "FG3M", "FG3A", "TOV"]
                vp_display_cols = [c for c in vp_display_cols if c in vs_player_df.columns]
                vp_display = vs_player_df[vp_display_cols].copy()
                vp_display["GAME_DATE"] = vp_display["GAME_DATE"].dt.strftime("%-m/%-d/%Y")
                vp_display = vp_display.rename(columns={
                    "GAME_DATE": "Date", "MATCHUP": "Matchup", "MIN": "Min", "PTS": "Pts",
                    "REB": "Reb", "OREB": "OReb", "AST": "Ast", "STL": "Stl", "BLK": "Blk",
                    "FG3M": "3PM", "FG3A": "3PA", "TOV": "TOV",
                })
                st.dataframe(vp_display, use_container_width=True, hide_index=True)
                st.caption(
                    f"{len(vs_player_df)} game(s) found against {name}, on whatever "
                    f"team they were playing for at the time -- across {', '.join(HEAD_TO_HEAD_SEASONS)}."
                )

            if len(valid_ids) >= 2:
                combo_label = " + ".join(key_players_input)
                st.markdown(
                    f'<div style="text-align:center; color:#ffffff; font-weight:700; '
                    f'font-size:18px; margin-top:32px;">Combined: {combo_label} '
                    f'<span style="color:#9ca3af; font-weight:500; font-size:13px;">'
                    f'(same team, at the same time)</span></div>',
                    unsafe_allow_html=True,
                )
                if no_combo_data:
                    st.warning(
                        f"No historical games found with {combo_label} on the same team "
                        f"together, across {', '.join(HEAD_TO_HEAD_SEASONS)}. This exact "
                        f"pairing appears to be new -- the prediction above reflects each "
                        f"player's individual history, not any interaction effect between "
                        f"them (spacing, shared rim protection, etc.), since that genuinely "
                        f"can't be measured from data that doesn't exist yet."
                    )
                else:
                    combo_df, _ = get_head_to_head_vs_player_combo(player_id, valid_ids)
                    combo_display_cols = ["GAME_DATE", "MATCHUP", "MIN", "PTS", "REB", "OREB", "AST", "STL", "BLK", "FG3M", "FG3A", "TOV"]
                    combo_display_cols = [c for c in combo_display_cols if c in combo_df.columns]
                    combo_display = combo_df[combo_display_cols].copy()
                    combo_display["GAME_DATE"] = combo_display["GAME_DATE"].dt.strftime("%-m/%-d/%Y")
                    combo_display = combo_display.rename(columns={
                        "GAME_DATE": "Date", "MATCHUP": "Matchup", "MIN": "Min", "PTS": "Pts",
                        "REB": "Reb", "OREB": "OReb", "AST": "Ast", "STL": "Stl", "BLK": "Blk",
                        "FG3M": "3PM", "FG3A": "3PA", "TOV": "TOV",
                    })
                    st.dataframe(combo_display, use_container_width=True, hide_index=True)
                    st.caption(
                        f"{len(combo_df)} game(s) found with {combo_label} on the same team "
                        f"together, across {', '.join(HEAD_TO_HEAD_SEASONS)}."
                    )

        with st.expander("See how this estimate was built (every adjustment step)"):
            baseline_summary = ", ".join(
                f"{predictions[col]['base']:.1f} {col}" for col, _ in STAT_COLUMNS
            )
            st.write(f"**[1] Baseline** ({source}): {baseline_summary}")
            notes_by_layer = {
                "opponent_defense": def_note,
                "missing_teammates": teammate_note,
                "missing_opponents": opp_missing_note,
                "new_teammate": new_teammate_note,
                "defender_matchup": defender_note,
                "scheme": scheme_note,
            }
            for line in build_layer_lines(notes_by_layer):
                st.write(line)
            if scheme_executor_input:
                st.write(f"**[8] Scheme executed by (reference only):** {scheme_executor_input} "
                         f"-- not used in the calculation, no data exists to attribute schemes to individual players.")
            if post_change_thin_sample:
                st.write(
                    f"**[9] Roster-change uncertainty:** the likely range above was widened "
                    f"x{THIN_SAMPLE_SPREAD_MULTIPLIER} since the post-change sample is small -- "
                    f"treat this prediction as a rougher estimate than usual until more games "
                    f"have been played with the new roster."
                )

        st.caption(
            "Remember: this is a transparent estimate built from a handful of adjustments, "
            "not a trained predictive model. Treat it as a starting point for your own analysis."
        )

with tab2:
    st.subheader("Predict a full matchup")
    st.caption(
        "Projects a full box score for both teams using each player's live "
        "current roster spot (so departed players drop off and new arrivals "
        "show up automatically) and the same season-baseline + "
        "opponent-defense engine as the single-player tool above. This does "
        "not model rotations or minutes -- every player is projected at "
        "their own adjusted season-average rate, not a coach's actual "
        "rotation plan. Per-player nuance (missing/new teammates, primary "
        "defender, scheme) stays in the single-player tool for now."
    )

def predict_player_vs_opponent(player_id, player_name, opponent_id, out_player_id=None):
    """MVP matchup-predictor engine: season baseline + opponent-defense
    adjustment, plus an optional out-redistribution adjustment when
    out_player_id is given (Full Matchup's "mark a player as out"
    feature -- see engine/adjustments/teammates.py's
    get_out_redistribution_adjustment for the real "games with vs.
    without" comparison and why it's a distinct, narrowly-scoped
    mechanic rather than a reuse of the single-player tool's general
    missing_teammates layer). Still deliberately excludes missing/new-
    teammate, primary defender, and scheme adjustments -- that nuance
    stays in the single-player tool, per the approved v1 scope.

    out_player_id=None (the default, and every call site before this
    parameter existed): the redistribution branch below never runs,
    redistribution_result is always None, and the returned predictions
    are exactly what this function always computed -- season baseline
    times defense_result only.

    Returns None if there isn't enough real data for this player (e.g. a
    true rookie with no NBA history) so the caller can flag it rather
    than silently guessing.

    Returns (predictions, season_source, def_source_note, layer_results).
    predictions now includes "low"/"high"/"base" alongside "predicted"
    -- the exact same spread formula tab1 already uses (base_std when
    available, else predicted*0.2 as a fallback spread; +/-0.6*spread
    for the range), added so a saved Full Matchup prediction can be
    checked against a real result the same way a single-player one can
    (engine/tracker.py's {col}_hit needs a real low/high to check
    against, not just a point estimate). No post_change_thin_sample
    widening here -- that's a tab1-only roster-change concept tab2
    doesn't have. layer_results is {"opponent_defense": defense_result}
    plus "out_redistribution" only when it was actually computed (never
    a None value in the dict -- a caller iterating layer_results and
    calling .applied on every value would break on that) -- this is
    what a caller needs to build a real layers_json when saving, not
    just the note strings tab2's table already showed.
    """
    try:
        season_stats, season_source, _season_n = get_season_baseline(player_id, player_name)
    except Exception:
        return None
    if not season_stats:
        return None

    team_def_rating, league_avg_def, def_source_note = get_opponent_defense_with_fallback(opponent_id)
    # No team_h2h_weight here (tab2 has no head-to-head baseline blending
    # at all) -- defaults to 0.0, reproducing this tab's old unscaled
    # x0.5 strength exactly. See engine/adjustments/defense.py.
    defense_result = get_defense_adjustment(team_def_rating, league_avg_def, def_source_note)

    redistribution_result = None
    if out_player_id is not None:
        try:
            player_df, season, _source = resolve_season_gamelog(player_id)
        except Exception:
            player_df = pd.DataFrame()
        if not player_df.empty:
            redistribution_result = get_out_redistribution_adjustment(
                player_id, out_player_id, season, player_df
            )

    predictions = {}
    for col, _label in STAT_COLUMNS:
        base_mean, base_std = season_stats[col]
        multiplier = defense_result.multiplier_for(col)
        if redistribution_result is not None:
            multiplier *= redistribution_result.multiplier_for(col)
        predicted = base_mean * multiplier
        spread = base_std if pd.notna(base_std) else predicted * 0.2
        low = max(0, predicted - spread * 0.6)
        high = predicted + spread * 0.6
        predictions[col] = {"base": base_mean, "predicted": predicted, "low": low, "high": high}

    layer_results = {"opponent_defense": defense_result}
    if redistribution_result is not None:
        layer_results["out_redistribution"] = redistribution_result

    return predictions, season_source, def_source_note, layer_results



with tab2:
    with st.form("matchup_form"):
        mcol1, mcol2 = st.columns(2)
        with mcol1:
            default_a = team_names.index("Oklahoma City Thunder") if "Oklahoma City Thunder" in team_names else None
            team_a_input = st.selectbox(
                "Team A", options=team_names, index=default_a,
                placeholder="Search a team..."
            )
        with mcol2:
            default_b = team_names.index("San Antonio Spurs") if "San Antonio Spurs" in team_names else None
            team_b_input = st.selectbox(
                "Team B", options=team_names, index=default_b,
                placeholder="Search a team..."
            )
        matchup_submitted = st.form_submit_button("Predict matchup")

    if matchup_submitted:
        if not team_a_input or not team_b_input:
            st.error("Please select both teams.")
            st.stop()
        if team_a_input == team_b_input:
            st.error("Please select two different teams.")
            st.stop()

        team_a_id, team_a_full, team_a_abbr = get_team_id(team_a_input)
        team_b_id, team_b_full, team_b_abbr = get_team_id(team_b_input)
        st.session_state["matchup_context"] = {
            "team_a_id": team_a_id, "team_a_full": team_a_full, "team_a_abbr": team_a_abbr,
            "team_b_id": team_b_id, "team_b_full": team_b_full, "team_b_abbr": team_b_abbr,
        }
        # A freshly-submitted matchup starts with nobody marked out --
        # also avoids a stale selection from a PREVIOUS matchup (a
        # different team's roster) surviving into this one, which
        # would otherwise point at a player id that isn't even on the
        # newly selected team.
        st.session_state.pop("team_a_out_input", None)
        st.session_state.pop("team_b_out_input", None)

    if "matchup_context" in st.session_state:
        ctx = st.session_state["matchup_context"]
        team_a_id, team_a_full, team_a_abbr = ctx["team_a_id"], ctx["team_a_full"], ctx["team_a_abbr"]
        team_b_id, team_b_full, team_b_abbr = ctx["team_b_id"], ctx["team_b_full"], ctx["team_b_abbr"]

        def build_team_projection(team_id, opponent_id, out_player_id=None):
            """out_player_id: excluded entirely from the projected rows
            (not called through predict_player_vs_opponent at all --
            there's nothing to project for a player marked out), and
            passed through to every remaining player's prediction so
            engine/adjustments/teammates.py's
            get_out_redistribution_adjustment can apply. Returns
            (rows, skipped, unadjusted, out_name, trackable):
            `skipped` is the existing "not enough data to project at
            all" case; `unadjusted` is a distinct, narrower case -- the
            player WAS projected, but there wasn't enough real "games
            with vs. without the out player" history to trust a
            redistribution adjustment for them specifically, so their
            row shows their normal, unadjusted number instead of a
            fabricated one. `trackable` is one dict per successfully-
            projected player (player_id, player_full_name, predictions,
            layer_results) -- everything needed to later save this
            player's prediction, without threading opponent/game-date
            context through here (the caller adds that; this function
            doesn't know the game being tracked, only the matchup)."""
            roster = get_team_roster(team_id)
            rows = []
            skipped = []
            unadjusted = []
            out_name = None
            trackable = []
            for pid, pname in roster:
                if pid == out_player_id:
                    out_name = pname
                    continue
                result = predict_player_vs_opponent(pid, pname, opponent_id, out_player_id=out_player_id)
                if result is None:
                    skipped.append(pname)
                    continue
                predictions, _season_source, _def_source_note, layer_results = result
                redistribution_result = layer_results.get("out_redistribution")
                if redistribution_result is not None and not redistribution_result.applied:
                    unadjusted.append(pname)
                row = {"Player": pname}
                for col, label in STAT_COLUMNS:
                    row[label] = round(predictions[col]["predicted"], 1)
                rows.append(row)
                trackable.append({
                    "player_id": pid, "player_full_name": pname,
                    "predictions": predictions, "layer_results": layer_results,
                })
            return rows, skipped, unadjusted, out_name, trackable

        def render_team_projection(team_id, team_full, opponent_id, opponent_full, opponent_abbr, out_key):
            st.markdown(f"**{team_full}** projected box score")
            roster = get_team_roster(team_id)
            out_choice = st.selectbox(
                f"Mark a {team_full} player as out (optional)",
                options=["None"] + [pname for _pid, pname in roster],
                key=out_key,
            )
            out_id = None
            if out_choice != "None":
                out_id = next((pid for pid, pname in roster if pname == out_choice), None)

            with st.spinner("Calculating..."):
                rows, skipped, unadjusted, out_name, trackable = build_team_projection(
                    team_id, opponent_id, out_player_id=out_id
                )

            if rows:
                st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
            else:
                st.info("No players with enough data to project.")
            if out_name:
                st.caption(
                    f"Marked out: {out_name}. Remaining players' numbers above are "
                    f"adjusted using their real historical games with vs. without "
                    f"{out_name} this season, where enough real data exists."
                )
            if unadjusted:
                st.caption(
                    f"Not enough real head-to-head history with {out_name} to trust "
                    f"an adjustment -- shown at their normal projection instead: "
                    f"{', '.join(unadjusted)}"
                )
            if skipped:
                st.caption(f"Not enough data to project: {', '.join(skipped)}")

            return [
                {**t, "opponent_full_name": opponent_full, "opponent_abbr": opponent_abbr}
                for t in trackable
            ]

        team_a_trackable = render_team_projection(
            team_a_id, team_a_full, team_b_id, team_b_full, team_b_abbr, "team_a_out_input"
        )
        team_b_trackable = render_team_projection(
            team_b_id, team_b_full, team_a_id, team_a_full, team_a_abbr, "team_b_out_input"
        )

        st.markdown("---")
        matchup_game_date = st.date_input(
            "Game date (required to save this matchup for tracking)",
            value=None, key="matchup_game_date_input",
        )
        if st.button("💾 Save this matchup's predictions", key="save_matchup_btn"):
            save_email = st.session_state.get("tracker_email_input", "").strip().lower()
            all_trackable = team_a_trackable + team_b_trackable
            if not save_email:
                st.warning(
                    "Enter your email in the Prediction Tracker (sidebar) first, "
                    "so you can find these predictions again."
                )
            elif not matchup_game_date:
                st.warning(
                    "Enter the game date first -- required to track this matchup "
                    "against its real result later."
                )
            elif not all_trackable:
                st.warning("No players with enough data to save for this matchup.")
            else:
                rows_input = [
                    {
                        "player_id": t["player_id"], "player_full_name": t["player_full_name"],
                        "opponent_full_name": t["opponent_full_name"], "opponent_abbr": t["opponent_abbr"],
                        "game_date": matchup_game_date, "predictions": t["predictions"],
                        "layer_results": t["layer_results"],
                    }
                    for t in all_trackable
                ]
                new_ids = append_predictions_batch(rows_input, saved_by_email=save_email, source="full_matchup")
                st.success(
                    f"Saved {len(new_ids)} player predictions for this matchup. "
                    f"Check the Prediction Tracker in the sidebar later."
                )
