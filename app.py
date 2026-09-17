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

import html
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

from engine.players import get_player_id, get_team_id, player_search_label
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
from engine.adjustments.scheme import get_synergy_scheme_adjustment, SCHEME_ADJUSTMENTS, NO_SCHEME
from engine.adjustments.base import AdjustmentResult
from engine.adjustments.teammates import (
    OUT_REDISTRIBUTION_LAYER,
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
from engine.adjustments.registry import LAYER_DISPLAY
from engine.baseline_stats import stats_from_gamelog
from engine.lean import LEAN_MODELS, strong_lean_lines


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
        # MATCHUP is "<player's team> vs. <opp>" or "<player's team> @ <opp>".
        # Match only the last token: a substring test also matched games the
        # player played FOR this team (before or after a trade).
        matched = df[df["MATCHUP"].astype(str).str.split().str[-1] == opponent_abbr]
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


def blend_baseline_stats(season_stats, shrinkage_k=32, team_h2h=None, team_h2h_n=0,
                          extra_sources=None):
    """Blend season average, team head-to-head, and any number of
    extra sources -- e.g. head-to-head vs. one specific opponent
    player, or vs. a specific combination of opponent players on the
    floor together -- weighted by how many real games back each one.
    The season average always contributes as if it had shrinkage_k
    games, so a source needs roughly that many real games of its own
    to weigh as heavily as the season average; below that it still
    earns real influence, just proportionally less.

    shrinkage_k=32 comes from shrinkage_k_sweep.py, not intuition.
    Replayed point-in-time over run_backtest.py's case set, error
    relative to season-only was k=2 1.042, k=4 1.018, k=8 1.005,
    k=16 0.999, k=32 0.998 (best for every stat), and raw head-to-head
    1.158. The old k=4 was reliably worse than ignoring head-to-head
    entirely; k=32 is only marginally better than season-only. Weight
    is n / (k + total n), so a 5-game sample now gets 13% and 10
    meetings plus 10 defender games leave the season at 62%. The
    vs.-specific-player sources share this k but can't be backtested
    (no historical defender assignments).

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


def get_hit_rate_table(game_log_df, line, stat_col="PTS"):
    """Mimics props.cash's L5/L10/L20/season hit-rate columns: what
    percent of games did the player clear a given line. Since we
    don't have real sportsbook lines, this uses whatever number the
    user enters (or the season average as a transparent default)."""
    windows = {"L5": 5, "L10": 10, "L20": 20}
    results = {}
    for label, n in windows.items():
        subset = game_log_df.head(n)
        if len(subset) == 0:
            results[label] = (None, 0)
        else:
            hits = (subset[stat_col] > line).sum()
            results[label] = (hits / len(subset) * 100, len(subset))
    season_hits = (game_log_df[stat_col] > line).sum()
    results["Season"] = (
        season_hits / len(game_log_df) * 100 if len(game_log_df) > 0 else None,
        len(game_log_df),
    )
    return results


# ---------------------------- Streamlit UI ----------------------------

st.set_page_config(
    page_title="Boxscore Whisperer: NBA statline estimates",
    page_icon=os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "favicon.png"),
    layout="wide",
)

# ---------------------------- Design system ----------------------------
# One set of tokens for every custom element below. Streamlit's own
# widgets take their colours from .streamlit/config.toml, which pins the
# dark theme; keep the two in step (accent #22c55e, background #0a0d12).
st.markdown("""
<style>
:root {
    --bw-bg: #0a0d12;
    --bw-surface: #11151c;
    --bw-surface-2: #161a23;
    --bw-border: #252b38;
    --bw-border-soft: #1b202b;
    --bw-text: #e7eaf0;
    --bw-muted: #9aa3b2;
    --bw-faint: #6b7385;
    --bw-accent: #22c55e;
    --bw-accent-hover: #34d073;
    --bw-accent-soft: rgba(34, 197, 94, 0.10);
    --bw-accent-line: rgba(34, 197, 94, 0.28);
    --bw-accent-text: #4ade80;
    --bw-red-soft: rgba(248, 113, 113, 0.10);
    --bw-red-line: rgba(248, 113, 113, 0.26);
    --bw-red-text: #f87171;
    --bw-amber-soft: rgba(251, 191, 36, 0.10);
    --bw-amber-line: rgba(251, 191, 36, 0.26);
    --bw-amber-text: #fbbf24;
}

/* ---- Page frame ---- */
.stApp {
    background:
        radial-gradient(900px 420px at 50% -160px, rgba(34, 197, 94, 0.09), transparent 70%),
        var(--bw-bg);
}
header[data-testid="stHeader"] {
    background: transparent;
}
[data-testid="stMainMenu"], [data-testid="stAppDeployButton"], footer {
    display: none !important;
}
/* Wide layout, capped: room for the 10-column matchup tables without
   stretching the form across a whole monitor. */
[data-testid="stMainBlockContainer"], .block-container {
    max-width: 1060px;
    padding-top: 2.5rem;
    padding-bottom: 3rem;
}
.stApp .num, .stat-value, .stat-midpoint, .hit-rate-badge .pct, .methodology-table td {
    font-variant-numeric: tabular-nums;
}

/* ---- Hero ---- */
.bw-hero {
    text-align: center;
    margin: 4px auto 28px auto;
}
.bw-eyebrow {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 5px 12px;
    border: 1px solid var(--bw-border);
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.02);
    color: var(--bw-muted);
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.02em;
}
.bw-eyebrow .dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--bw-accent);
    box-shadow: 0 0 0 3px var(--bw-accent-soft);
}
.bw-wordmark {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 14px;
    margin: 18px 0 12px 0;
    color: #ffffff;
    font-size: clamp(34px, 5.2vw, 54px);
    font-weight: 800;
    letter-spacing: -0.035em;
    line-height: 1.05;
}
.bw-wordmark svg {
    width: 1.05em;
    height: 1.05em;
    flex-shrink: 0;
}
.bw-tagline {
    max-width: 560px;
    margin: 0 auto;
    color: var(--bw-muted);
    font-size: 17px;
    line-height: 1.55;
}
.bw-proof {
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 8px 22px;
    margin-top: 20px;
    color: var(--bw-muted);
    font-size: 13px;
}
.bw-proof span {
    display: inline-flex;
    align-items: center;
    gap: 6px;
}
.bw-proof svg {
    width: 15px;
    height: 15px;
    color: var(--bw-accent);
}
.bw-proof b {
    color: var(--bw-text);
    font-weight: 600;
}
.bw-notice {
    margin-top: 14px;
    color: var(--bw-faint);
    font-size: 12px;
}

/* ---- Tabs: centred segmented control ----
   Streamlit 1.64 renders tabs with react-aria (role=tablist/tab and a
   .react-aria-SelectionIndicator underline); the data-baseweb selectors
   cover older builds. */
.stTabs [role="tablist"], .stTabs [data-baseweb="tab-list"] {
    gap: 4px;
    align-self: center;
    width: fit-content;
    height: auto;
    margin: 8px auto 22px auto;
    padding: 4px;
    border: 1px solid var(--bw-border-soft);
    border-radius: 12px;
    background: var(--bw-surface);
}
.stTabs [role="tablist"]::after,
.stTabs .react-aria-SelectionIndicator,
.stTabs [data-baseweb="tab-highlight"],
.stTabs [data-baseweb="tab-border"] {
    display: none !important;
}
.stTabs [role="tab"] {
    height: 38px;
    padding: 0 20px;
    border-radius: 9px;
    color: var(--bw-muted);
    transition: background 120ms ease, color 120ms ease;
}
.stTabs [role="tab"] p {
    font-size: 14px;
    font-weight: 600;
}
.stTabs [role="tab"]:hover {
    color: var(--bw-text);
}
.stTabs [role="tab"][aria-selected="true"] {
    background: var(--bw-surface-2);
    color: #ffffff;
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.05), 0 1px 3px rgba(0, 0, 0, 0.45);
}

/* ---- Panels: the Single Player form and the Full Matchup box ---- */
div[data-testid="stForm"], .st-key-matchup_box {
    padding: 26px 26px 22px 26px;
    border: 1px solid var(--bw-border);
    border-radius: 18px;
    background: linear-gradient(180deg, #131821 0%, #0f131a 100%);
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.03), 0 24px 48px -28px rgba(0, 0, 0, 0.7);
}
.stApp [data-testid="stWidgetLabel"] p {
    color: #c9ced8;
    font-size: 13px;
    font-weight: 600;
}

/* ---- Buttons ---- */
div[data-testid="stFormSubmitButton"] button,
.st-key-predict_matchup_btn button {
    min-height: 46px;
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 11px;
    background: var(--bw-accent);
    color: #03140a;
    font-size: 15px;
    font-weight: 700;
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.25), 0 10px 24px -14px rgba(34, 197, 94, 0.8);
    transition: background 120ms ease, transform 120ms ease;
}
div[data-testid="stFormSubmitButton"] button:hover,
.st-key-predict_matchup_btn button:hover {
    background: var(--bw-accent-hover);
    color: #03140a;
    border-color: rgba(255, 255, 255, 0.16);
}
div[data-testid="stFormSubmitButton"] button:active,
.st-key-predict_matchup_btn button:active {
    transform: translateY(1px);
}
div[data-testid="stFormSubmitButton"] button p,
.st-key-predict_matchup_btn button p {
    font-weight: 700;
}

/* ---- Expanders ---- */
div[data-testid="stExpander"] details {
    border: 1px solid var(--bw-border-soft);
    border-radius: 12px;
    background: rgba(255, 255, 255, 0.015);
}
div[data-testid="stExpander"] summary p {
    color: var(--bw-muted);
    font-size: 14px;
    font-weight: 600;
}
div[data-testid="stExpander"] summary:hover p {
    color: var(--bw-text);
}
div[data-testid="stExpander"] div[data-testid="stMarkdownContainer"] p,
div[data-testid="stExpander"] div[data-testid="stMarkdownContainer"] li {
    color: #cfd4dd;
    font-size: 14px;
    line-height: 1.6;
}

/* ---- Supporting text ---- */
div[data-testid="stCaptionContainer"],
div[data-testid="stCaptionContainer"] p {
    color: var(--bw-muted) !important;
    font-size: 13px;
    line-height: 1.55;
}
hr {
    border-color: var(--bw-border-soft) !important;
}
div[data-testid="stAlertContentSuccess"] {
    color: var(--bw-accent-text);
}

/* ---- Section headings (section_heading helper) ---- */
.bw-section {
    margin: 36px 0 12px 0;
    padding-top: 22px;
    border-top: 1px solid var(--bw-border-soft);
}
.bw-section.first {
    margin-top: 8px;
    padding-top: 0;
    border-top: none;
}
.bw-section-title {
    color: #ffffff;
    font-size: 18px;
    font-weight: 700;
    letter-spacing: -0.01em;
}
.bw-section-sub {
    margin-top: 3px;
    color: var(--bw-muted);
    font-size: 13px;
    line-height: 1.5;
}

/* ---- Result header ---- */
.bw-player {
    display: flex;
    align-items: center;
    gap: 18px;
    margin: 36px 0 12px 0;
    padding: 18px 22px;
    border: 1px solid var(--bw-border-soft);
    border-radius: 16px;
    background: var(--bw-surface);
}
/* Designed circle in team colours, not a real photo or likeness. See
   TEAM_COLORS + get_player_team_and_number() for why. */
.bw-avatar {
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    width: 64px;
    height: 64px;
    border-radius: 50%;
    background:
        radial-gradient(circle at 30% 25%, rgba(255, 255, 255, 0.28), transparent 62%),
        var(--team);
    box-shadow: 0 0 0 3px rgba(255, 255, 255, 0.06);
    color: #ffffff;
    font-size: 22px;
    font-weight: 700;
    letter-spacing: -0.02em;
    text-shadow: 0 1px 2px rgba(0, 0, 0, 0.35);
}
.bw-player-meta {
    flex: 1;
    min-width: 0;
}
.bw-kicker {
    color: var(--bw-accent-text);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
}
.bw-player-name {
    color: #ffffff;
    font-size: 26px;
    font-weight: 700;
    letter-spacing: -0.02em;
    line-height: 1.2;
}
.bw-player-sub {
    margin-top: 2px;
    color: var(--bw-muted);
    font-size: 14px;
}
.bw-player-sub .sep {
    margin: 0 7px;
    color: var(--bw-faint);
}
.bw-player-sub b {
    color: var(--bw-text);
    font-weight: 600;
}

/* ---- Confidence pill (one per prediction) ---- */
.confidence-badge {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 7px 13px;
    border: 1px solid;
    border-radius: 999px;
    font-size: 13px;
    font-weight: 600;
    white-space: nowrap;
}
.confidence-badge i {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: currentColor;
}
.confidence-high { background: var(--bw-accent-soft); border-color: var(--bw-accent-line); color: var(--bw-accent-text); }
.confidence-medium { background: var(--bw-amber-soft); border-color: var(--bw-amber-line); color: var(--bw-amber-text); }
.confidence-low { background: var(--bw-red-soft); border-color: var(--bw-red-line); color: var(--bw-red-text); }

/* ---- Stat cards ---- */
.stat-card-row {
    display: grid;
    grid-template-columns: repeat(var(--cols, 4), minmax(0, 1fr));
    gap: 10px;
    margin: 10px 0;
}
.stat-card {
    padding: 14px 16px;
    border: 1px solid var(--bw-border-soft);
    border-radius: 14px;
    background: var(--bw-surface);
}
.stat-card.lead {
    border-color: var(--bw-accent-line);
    background: linear-gradient(180deg, rgba(34, 197, 94, 0.10) 0%, rgba(34, 197, 94, 0.02) 100%);
}
.stat-card .stat-title {
    overflow: hidden;
    color: var(--bw-muted);
    font-size: 12px;
    font-weight: 600;
    white-space: nowrap;
    text-overflow: ellipsis;
}
.stat-card .stat-value {
    margin-top: 4px;
    color: #ffffff;
    font-size: 32px;
    font-weight: 700;
    letter-spacing: -0.02em;
    line-height: 1.1;
}
.stat-card-row.compact .stat-value {
    font-size: 22px;
}
.stat-card .stat-midpoint {
    margin-top: 6px;
    color: var(--bw-faint);
    font-size: 12px;
}
.stat-card .stat-midpoint b {
    color: var(--bw-muted);
    font-weight: 600;
}

/* ---- Hit rates: one row per stat ---- */
.hr-row {
    display: grid;
    grid-template-columns: 190px minmax(0, 1fr);
    align-items: center;
    gap: 16px;
    padding: 10px 0;
    border-bottom: 1px solid var(--bw-border-soft);
}
.hr-stat {
    color: #ffffff;
    font-size: 14px;
    font-weight: 600;
}
.hr-line {
    color: var(--bw-muted);
    font-size: 12px;
}
.hit-rate-row {
    display: grid;
    grid-auto-columns: minmax(0, 1fr);
    grid-auto-flow: column;
    gap: 8px;
}
.hit-rate-badge {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    padding: 8px 12px;
    border: 1px solid transparent;
    border-radius: 10px;
}
.hit-rate-badge .label {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.04em;
    opacity: 0.85;
}
.hit-rate-badge .pct {
    font-size: 16px;
    font-weight: 700;
}
.hit-rate-green { background: var(--bw-accent-soft); border-color: rgba(34, 197, 94, 0.18); color: var(--bw-accent-text); }
.hit-rate-red { background: var(--bw-red-soft); border-color: rgba(248, 113, 113, 0.18); color: var(--bw-red-text); }
.hit-rate-gray { background: rgba(255, 255, 255, 0.025); border-color: var(--bw-border-soft); color: var(--bw-faint); }

/* ---- Methodology tables ---- */
.methodology-table {
    width: 100%;
    margin: 12px 0 20px 0;
    border-collapse: collapse;
    font-size: 13px;
}
.methodology-table th, .methodology-table td {
    padding: 9px 12px;
    border-bottom: 1px solid var(--bw-border-soft);
    text-align: right;
}
.methodology-table th:first-child, .methodology-table td:first-child {
    text-align: left;
}
.methodology-table th {
    color: var(--bw-faint);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.05em;
    text-transform: uppercase;
}
.methodology-table td {
    color: var(--bw-text);
}

/* ---- Sidebar ---- */
.bw-side-title {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 4px 0 2px 0;
    color: #ffffff;
    font-size: 15px;
    font-weight: 700;
}
.bw-side-title svg {
    width: 16px;
    height: 16px;
    color: var(--bw-accent);
}
.bw-side-sub {
    margin-bottom: 10px;
    color: var(--bw-muted);
    font-size: 13px;
    line-height: 1.5;
}

/* ---- Footer ---- */
.bw-footer {
    margin-top: 72px;
    padding-top: 24px;
    border-top: 1px solid var(--bw-border-soft);
    color: var(--bw-faint);
    font-size: 12px;
    line-height: 1.65;
}
.bw-footer .brand {
    margin-bottom: 6px;
    color: var(--bw-muted);
    font-size: 13px;
    font-weight: 600;
}
.bw-footer a {
    color: var(--bw-muted);
    text-decoration: underline;
    text-underline-offset: 2px;
}
.bw-footer a:hover {
    color: var(--bw-text);
}

/* ---- Phones ---- */
@media (max-width: 640px) {
    [data-testid="stMainBlockContainer"], .block-container {
        padding-top: 1.5rem;
    }
    .bw-wordmark { flex-direction: column; gap: 10px; }
    .bw-wordmark svg { width: 52px; height: 52px; }
    .bw-tagline { font-size: 15px; }
    .bw-player { flex-wrap: wrap; gap: 14px; padding: 16px; }
    .bw-avatar { width: 52px; height: 52px; font-size: 18px; }
    .bw-player-name { font-size: 22px; }
    .bw-player-conf { width: 100%; }
    .stat-card-row, .stat-card-row.compact { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .stat-card .stat-value { font-size: 26px; }
    .hr-row { grid-template-columns: minmax(0, 1fr); gap: 8px; }
    .hit-rate-badge { flex-direction: column; align-items: flex-start; gap: 2px; padding: 7px 10px; }
    .stTabs [role="tab"] { padding: 0 14px; }
}
</style>
""", unsafe_allow_html=True)

BW_LOGO_SVG = (
    '<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">'
    '<path d="M11 25 Q7.5 32 11 39" stroke="#22c55e" stroke-width="3.2" fill="none" stroke-linecap="round" opacity="0.5"/>'
    '<path d="M16.5 21 Q11 32 16.5 43" stroke="#22c55e" stroke-width="3.2" fill="none" stroke-linecap="round"/>'
    '<circle cx="38" cy="32" r="16" fill="#ff8c42"/>'
    '<line x1="22" y1="32" x2="54" y2="32" stroke="#0a0d12" stroke-width="1.8"/>'
    '<line x1="38" y1="16" x2="38" y2="48" stroke="#0a0d12" stroke-width="1.8"/>'
    '<path d="M26.5 20.5 Q33 32 26.5 43.5" stroke="#0a0d12" stroke-width="1.8" fill="none"/>'
    '<path d="M49.5 20.5 Q43 32 49.5 43.5" stroke="#0a0d12" stroke-width="1.8" fill="none"/>'
    '</svg>'
)
_CHECK_ICON = (
    '<svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" '
    'd="M16.7 5.3a1 1 0 0 1 0 1.4l-8 8a1 1 0 0 1-1.4 0l-4-4a1 1 0 1 1 1.4-1.4L8 12.6l7.3-7.3a1 1 0 0 1 1.4 0z" '
    'clip-rule="evenodd"/></svg>'
)


def section_heading(title, sub=None, first=False):
    """Left-aligned section title with an optional one-line subtitle.
    `title` is escaped; `sub` is trusted HTML built in this file."""
    sub_html = f'<div class="bw-section-sub">{sub}</div>' if sub else ""
    st.markdown(
        f'<div class="bw-section{" first" if first else ""}">'
        f'<div class="bw-section-title">{html.escape(title)}</div>{sub_html}</div>',
        unsafe_allow_html=True,
    )


# Short labels where the full STAT_COLUMNS label doesn't fit a card or a
# table header. Display only -- every calculation keys on the column.
STAT_SHORT_LABELS = {
    "OREB": "Off. rebounds",
    "FG3M": "3PT made",
    "FG3A": "3PT attempts",
}
STAT_TABLE_LABELS = {
    "PTS": "PTS", "AST": "AST", "REB": "REB", "OREB": "OREB", "STL": "STL",
    "BLK": "BLK", "FG3M": "3PM", "FG3A": "3PA", "TOV": "TOV",
}

# ---------- Prediction Tracker (sidebar, always visible) ----------
with st.sidebar:
    st.markdown(
        '<div class="bw-side-title">'
        '<svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path d="M3 3h2v13h12v2H3V3zm4 8h2v4H7v-4zm4-4h2v8h-2V7zm4-3h2v11h-2V4z"/></svg>'
        'Prediction tracker</div>'
        '<div class="bw-side-sub">Save a prediction, then check it against the real result after the game.</div>',
        unsafe_allow_html=True,
    )
    tracker_email = st.text_input(
        "Your email (to find your saved predictions)",
        key="tracker_email_input",
        placeholder="you@example.com",
        help=(
            "Just a filter, not a login — no password, nothing verified. "
            "Anyone who enters this exact email sees the same predictions."
        ),
    )
    if st.button("Check for results", key="refresh_tracker_btn",
                 icon=":material/refresh:", width="stretch"):
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
                f"{len(my_log_df)} saved — {len(resolved)} resolved, "
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
                    "Showing Points only here for space — all 8 tracked stats are saved "
                    f"in the underlying file at {os.path.basename(LOG_PATH)}."
                )

st.markdown(
    '<div class="bw-hero">'
    '<span class="bw-eyebrow"><span class="dot"></span>NBA statline estimates</span>'
    f'<div class="bw-wordmark">{BW_LOGO_SVG}<span>Boxscore Whisperer</span></div>'
    '<p class="bw-tagline">Transparent player projections, built from real games. '
    'Every adjustment is shown, so you can judge it yourself.</p>'
    '<div class="bw-proof">'
    f'<span>{_CHECK_ICON}<span><b>29,914</b> player-games backtested</span></span>'
    f'<span>{_CHECK_ICON}No lookahead</span>'
    f'<span>{_CHECK_ICON}Not a black-box model</span>'
    '</div>'
    '<div class="bw-notice">Independent and unofficial. For information and entertainment, '
    'not betting advice. 18+. Full notice at the bottom of the page.</div>'
    '</div>',
    unsafe_allow_html=True,
)
with st.expander("Methodology and backtest results"):
    st.markdown(
        "**How we know this works**\n\n"
        "Most prediction tools show you a number and ask you to trust it. We'd rather "
        "show you the evidence.\n\n"
        "Before launch, we ran Boxscore Whisperer's core prediction engine against every "
        "eligible regular-season game from the last three NBA seasons — 2023-24 through "
        "2025-26 — using only data that would have genuinely been available before each "
        "game was played. No lookahead, no using a season's final stats to \"predict\" "
        "its opening week. That's 29,914 real, independently verified predictions.\n\n"
        "Here's what we found: our season-baseline predictions are solid. Our "
        "opponent-defense adjustment currently adds a small, statistically real but "
        "practically modest edge over the raw baseline — and for some stats, "
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
    # Strong leans (engine/lean.py) -- numbers come straight from
    # engine/lean_models.json (written by lean_model_sweep.py), so this
    # table can't drift from what the Single Player tab actually shows.
    if LEAN_MODELS:
        _lean_calls = sum(m["n"] for m in LEAN_MODELS.values())
        _lean_acc = sum(m["oos_accuracy"] * m["n"] for m in LEAN_MODELS.values()) / _lean_calls
        _lean_names = [label.lower() for col, label in STAT_COLUMNS if col in LEAN_MODELS]
        _all_calls = [m["all_calls_accuracy"] for m in LEAN_MODELS.values()]
        st.markdown(
            "**Strong leans.** Calling every game above or below a player's season average "
            "is close to a coin flip — the table above, and still only "
            f"{min(_all_calls) * 100:.0f}-{max(_all_calls):.0%} with the recent-form model below. "
            "Strong leans are the rare games where that model is "
            f"confident; they were right {_lean_acc:.0%} of the time in seasons the model "
            "never saw (each season held out in turn). Only "
            + ", ".join(_lean_names[:-1]) + f" and {_lean_names[-1]} get them — for the "
            "other stats the apparent accuracy came from those stats usually landing below "
            "average, not from the model."
        )
        st.markdown(
            '<table class="methodology-table">'
            '<tr><th>Stat</th><th>Strong-lean Accuracy</th><th>95% CI</th>'
            '<th>Games With A Lean</th><th>Calls</th></tr>'
            + "".join(
                f'<tr><td>{col}</td><td>{m["oos_accuracy"]:.1%}</td>'
                f'<td>{m["oos_accuracy_ci"][0]:.1%}-{m["oos_accuracy_ci"][1]:.1%}</td>'
                f'<td>{m["oos_coverage"]:.0%}</td><td>{m["n"]:,}</td></tr>'
                for col, _label in STAT_COLUMNS
                for m in [LEAN_MODELS.get(col)] if m
            )
            + '</table>',
            unsafe_allow_html=True,
        )

# Pull the current name lists once per session for the searchable
# dropdowns -- typing inside these boxes filters the list live, no
# typos possible since selections come from a real, known list.
#
# Player widgets use IDS as their options (not names) -- see
# player_search_label's module docstring in engine/players.py for why:
# Streamlit's client-side dropdown search does a plain substring match
# against each option's rendered label (format_func output), so
# player_search_label can make "Jokic" findable there regardless of
# what the underlying option actually is. Using ids means the widget
# hands back a real id directly -- no name string round-trip, and
# nothing downstream breaks if the rendered label gets noisier (e.g.
# gains a folded-alias suffix). Every widget converts its returned
# id(s) back to the canonical name via player_id_to_name immediately
# after the widget call, so every existing consumer below (which
# expects a name string, e.g. get_player_id(name)) is unchanged.
@st.cache_data(ttl=86400, show_spinner=False)
def get_player_id_name_list():
    active = players.get_active_players()
    return sorted(((p["id"], p["full_name"]) for p in active), key=lambda t: t[1])

@st.cache_data(ttl=86400, show_spinner=False)
def get_team_name_list():
    all_teams = teams.get_teams()
    return sorted(t["full_name"] for t in all_teams)

player_id_name_list = get_player_id_name_list()
player_ids = [pid for pid, _ in player_id_name_list]
player_id_to_name = {pid: name for pid, name in player_id_name_list}
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
            player_input_id = st.selectbox(
                "Player", options=player_ids, index=None, placeholder="Search a player...",
                format_func=lambda pid: player_search_label(player_id_to_name[pid]),
            )
            player_input = player_id_to_name.get(player_input_id) if player_input_id is not None else None
        with col2:
            opponent_options, opponent_label_to_name = get_opponent_dropdown_options()
            opponent_label_input = st.selectbox(
                "Opponent", options=opponent_options, index=None,
                placeholder="Search a team...", key="opponent_select",
                help=(
                    "Each team shows its pace and defense tags, computed live from this "
                    "season's team stats (falling back to last season early in the year), "
                    "not fixed presets."
                ),
            )
            opponent_input = opponent_label_to_name.get(opponent_label_input) if opponent_label_input else None
        st.markdown('</div>', unsafe_allow_html=True)

        base_col, blend_col = st.columns([1, 1], vertical_alignment="bottom")
        with base_col:
            baseline_source_input = st.selectbox(
                "Baseline source",
                ["Season average (default)", "Last 5 meetings", "Last 10 meetings"],
                index=0,
                help=(
                    "Head-to-head baselines use real games vs. this specific opponent — more "
                    "relevant if a player has a real history against this team, but based on a "
                    "much smaller sample than a full season."
                ),
            )
        with blend_col:
            raw_baseline_input = st.checkbox(
                "Use only this source, no season blending",
                value=False,
                help=(
                    "By default, even a head-to-head baseline is blended with the season "
                    "average for reliability (a handful of games can't fully override a "
                    "full season on their own). Check this to use the selected baseline "
                    "source on its own instead — only applies when a head-to-head option "
                    "is selected."
                ),
            )

        line1, line2, line3 = st.columns(3)
        with line1:
            pts_line_input = st.number_input(
                "Points line (0 = season average)", min_value=0.0, value=0.0, step=0.5
            )
        with line2:
            ast_line_input = st.number_input(
                "Assists line (0 = season average)", min_value=0.0, value=0.0, step=0.5
            )
        with line3:
            reb_line_input = st.number_input(
                "Rebounds line (0 = season average)", min_value=0.0, value=0.0, step=0.5
            )

        with st.expander("More stat lines (steals, blocks, 3s, turnovers, offensive boards)"):
            line4, line5, line6, line7 = st.columns(4)
            with line4:
                stl_line_input = st.number_input(
                    "Steals line (0 = season avg)", min_value=0.0, value=0.0, step=0.5
                )
            with line5:
                blk_line_input = st.number_input(
                    "Blocks line (0 = season avg)", min_value=0.0, value=0.0, step=0.5
                )
            with line6:
                fg3m_line_input = st.number_input(
                    "3PM line (0 = season avg)", min_value=0.0, value=0.0, step=0.5
                )
            with line7:
                tov_line_input = st.number_input(
                    "Turnovers line (0 = season avg)", min_value=0.0, value=0.0, step=0.5
                )
            # FG3A and OREB were added as tracked stats after hit rates were
            # first removed; every STAT_COLUMNS entry needs a line input,
            # because the hit-rate loop below reads line_inputs[col] for each.
            line8, line9 = st.columns(2)
            with line8:
                fg3a_line_input = st.number_input(
                    "3PA line (0 = season avg)", min_value=0.0, value=0.0, step=0.5
                )
            with line9:
                oreb_line_input = st.number_input(
                    "Off. rebounds line (0 = season avg)", min_value=0.0, value=0.0, step=0.5
                )

        with st.expander("Advanced options (injuries, defender, scheme)"):
            adv1, adv2 = st.columns(2)
            with adv1:
                missing_teammates_ids = st.multiselect(
                    "Missing teammates", options=player_ids, default=[],
                    placeholder="Search and select players...", max_selections=5,
                    format_func=lambda pid: player_search_label(player_id_to_name[pid]),
                )
                missing_teammates = [player_id_to_name[pid] for pid in missing_teammates_ids]
                new_teammate_input_id = st.selectbox(
                    "New teammate arriving (optional)", options=player_ids, index=None,
                    placeholder="Search a player who just joined...",
                    format_func=lambda pid: player_search_label(player_id_to_name[pid]),
                    help=(
                        "Uses real shared games to compare this player's stats when this "
                        "teammate played heavy minutes vs. light minutes. Needs actual "
                        "shared game history to work — a pairing that hasn't shared the "
                        "floor yet will be flagged, not guessed at."
                    ),
                )
                new_teammate_input = player_id_to_name.get(new_teammate_input_id) if new_teammate_input_id is not None else None
                defender_input_id = st.selectbox(
                    "Primary defender assigned", options=player_ids, index=None,
                    placeholder="Search a defender...",
                    format_func=lambda pid: player_search_label(player_id_to_name[pid]),
                )
                defender_input = player_id_to_name.get(defender_input_id) if defender_input_id is not None else None
            with adv2:
                missing_opponents_ids = st.multiselect(
                    "Missing opponent players", options=player_ids, default=[],
                    placeholder="Search and select players...", max_selections=5,
                    format_func=lambda pid: player_search_label(player_id_to_name[pid]),
                )
                missing_opponents = [player_id_to_name[pid] for pid in missing_opponents_ids]
                _scheme_options = list(SCHEME_ADJUSTMENTS.keys())
                scheme_input = st.selectbox(
                    "Defensive scheme", _scheme_options,
                    index=_scheme_options.index(NO_SCHEME),
                )
                scheme_executor_input_id = st.selectbox(
                    "Scheme executed primarily by (reference only — optional)",
                    options=player_ids, index=None, placeholder="Search a player...",
                    format_func=lambda pid: player_search_label(player_id_to_name[pid]),
                    help=(
                        "No public data tracks which player runs a specific scheme, "
                        "so this name is stored for your own reference only — it "
                        "doesn't affect the calculation."
                    ),
                )
                scheme_executor_input = player_id_to_name.get(scheme_executor_input_id) if scheme_executor_input_id is not None else None

            st.markdown("---")
            roster_change_checked = st.checkbox(
                "Opponent just made a major roster change (trade, etc.)",
                help=(
                    "When set, opponent defense and head-to-head history use only "
                    "games since the date below. The season-long average otherwise blends "
                    "pre- and post-change games together — misleading right after a "
                    "big trade (e.g. a star player switching teams). Predictions "
                    "based on a very small post-change sample show a wider "
                    "likely range to reflect the extra uncertainty."
                ),
            )
            # Always rendered: this sits inside st.form, which doesn't rerun
            # when the checkbox is ticked, so a conditional date input only
            # appeared after a first submit. Ignored unless the box is ticked.
            roster_change_date_input = st.date_input(
                "Change effective date (used only when the box above is ticked)", value=None,
            )
            roster_change_date = roster_change_date_input if roster_change_checked else None

            st.markdown("---")  # patch_expander_spacing
            key_players_input_ids = st.multiselect(
                "Also check history vs. specific opposing player(s) (optional)",
                options=player_ids, default=[],
                placeholder="e.g. a star who just changed teams...", max_selections=4,
                format_func=lambda pid: player_search_label(player_id_to_name[pid]),
                help=(
                    "Finds every real game this player has faced them, on whatever team "
                    "they were on at the time — not just games against their current "
                    "team. Select two or more players (e.g. a new frontcourt pairing) "
                    "to also check whether they've ever shared the floor as opponents "
                    "before — if not, that's flagged rather than papered over."
                ),
            )
            key_players_input = [player_id_to_name[pid] for pid in key_players_input_ids]

        submitted = st.form_submit_button("Predict statline", type="primary", width="stretch")

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
                    source = "Blended baseline — " + "; ".join(parts)
                    if no_combo_data:
                        combo_label = " + ".join(key_player_ids.keys())
                        source += (f" — NOTE: no historical games found with {combo_label} on the same "
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
                    "This can happen when running on a cloud server — the NBA's unofficial "
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
            # engine/lean.py's models were fit on the UNSCALED multiplier
            # (team_h2h_weight=0, the backtest's), so the strong-lean block
            # gets that one regardless of the baseline source picked.
            lean_defense_multiplier = get_defense_adjustment(
                team_def_rating, league_avg_def, def_source_note,
            ).multiplier_for("PTS")

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
            # The matchup-tracking signal above (defender_result) never
            # becomes a multiplier -- see engine/adjustments/defender.py's
            # module docstring, that part is genuinely unchanged. But the
            # SAME defender_input name is separately folded into
            # effective_key_players_input above and can end up as a real
            # "vs. {name}" extra_source in the baseline blend -- so it CAN
            # reshape the prediction, just via that different mechanism.
            # Surface the real weight here rather than let defender_note's
            # "never folded in" framing read as a blanket claim it isn't.
            if defender_input:
                # Match the source label exactly: "vs. {name}" is the
                # defender's own history; "vs. A + B together" is a combined
                # source the defender is only part of. A substring test
                # attributed the whole combined weight to the defender.
                solo_label = f"vs. {defender_input}"
                solo_weight = 0.0
                combo_parts = []
                for label, stats, n in extra_sources:
                    if stats is None or n <= 0:
                        continue
                    weight = blend_weights.get(label, 0.0)
                    if label == solo_label:
                        solo_weight += weight
                    elif label.endswith(" together"):
                        members = label[len("vs. "):-len(" together")].split(" + ")
                        if defender_input in members:
                            combo_parts.append((label, weight))
                if solo_weight > 0:
                    defender_note += (
                        f' Separately, {defender_input}\'s own head-to-head history IS '
                        f'folded into the baseline above via the "vs. specific player" '
                        f'blend (a different mechanism from the matchup-tracking signal '
                        f'above) — {solo_weight:.0%} of the blended baseline weight.'
                    )
                for label, weight in combo_parts:
                    if weight > 0:
                        defender_note += (
                            f' Separately, {defender_input} is part of the combined '
                            f'"{label}" source in the baseline above ({weight:.0%} of the '
                            f'blended weight) — that weight covers the whole combination, '
                            f'not {defender_input} alone.'
                        )

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
            line_inputs = {
                "PTS": pts_line_input,
                "AST": ast_line_input,
                "REB": reb_line_input,
                "STL": stl_line_input,
                "BLK": blk_line_input,
                "FG3M": fg3m_line_input,
                "TOV": tov_line_input,
                "FG3A": fg3a_line_input,
                "OREB": oreb_line_input,
            }

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
            "line_inputs": line_inputs,
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
            "lean_defense_multiplier": lean_defense_multiplier,
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
        line_inputs = r["line_inputs"]
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
        _initials = "".join(part[0] for part in player_full_name.split()[:2] if part).upper()
        _jersey_html = (
            f'<span class="sep">·</span>#{html.escape(str(_avatar_jersey))}'
            if _avatar_jersey not in (None, "", "-") else ""
        )

        confidence_result = score_prediction(layer_results, baseline_sample_n)
        _confidence_css_class = {
            "High": "confidence-high",
            "Medium": "confidence-medium",
            "Low": "confidence-low",
        }[confidence_result.label]
        st.markdown(
            f'<div class="bw-player">'
            f'<div class="bw-avatar" style="--team:{_avatar_color};">{html.escape(_initials)}</div>'
            f'<div class="bw-player-meta">'
            f'<div class="bw-kicker">Projected statline</div>'
            f'<div class="bw-player-name">{html.escape(player_full_name)}</div>'
            f'<div class="bw-player-sub">{html.escape(_avatar_team_label)}{_jersey_html}'
            f'<span class="sep">·</span>vs <b>{html.escape(opponent_full_name)}</b></div>'
            f'</div>'
            f'<div class="bw-player-conf">'
            f'<span class="confidence-badge {_confidence_css_class}"><i></i>{confidence_result.label} confidence</span>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        if confidence_result.label != "High" and confidence_result.reasons:
            st.caption(" • ".join(confidence_result.reasons[:2]))

        def render_stat_card_row(stat_cols, compact=False, lead=None):
            row_html = (
                f'<div class="stat-card-row{" compact" if compact else ""}" '
                f'style="--cols:{len(stat_cols)};">'
            )
            for col in stat_cols:
                label = STAT_SHORT_LABELS.get(col, dict(STAT_COLUMNS)[col])
                p = predictions[col]
                row_html += (
                    f'<div class="stat-card{" lead" if col == lead else ""}">'
                    f'<div class="stat-title">{label}</div>'
                    f'<div class="stat-value">{p["predicted"]:.1f}</div>'
                    f'<div class="stat-midpoint" title="Likely range reflects prediction uncertainty — narrower with more data, wider with thin samples.">Likely range <b>{p["low"]:.0f}–{p["high"]:.0f}</b></div><!-- patch_likely_range_tooltip -->'
                    f'</div>'
                )
            row_html += '</div>'
            st.markdown(row_html, unsafe_allow_html=True)

        render_stat_card_row(["PTS", "AST", "REB", "OREB"], lead="PTS")
        render_stat_card_row(["STL", "BLK", "FG3M", "FG3A", "TOV"], compact=True)
        _applied_labels = [
            label.lower() for key, label in LAYER_DISPLAY
            if key in layer_results and layer_results[key].applied
        ]
        if not _applied_labels:
            _applied_phrase = "no adjustments applied this time, so the number shown is the baseline itself"
        elif len(_applied_labels) == 1:
            _applied_phrase = f"adjusted for {_applied_labels[0]}"
        else:
            _applied_phrase = (
                "adjusted for " + ", ".join(_applied_labels[:-1]) + " and " + _applied_labels[-1]
            )
        st.caption(
            f"This isn't a raw season average — it's the baseline {_applied_phrase}, using "
            "the math shown in \"See how this estimate was built\" below. The unadjusted "
            "baseline is shown separately there in step [1] for comparison. \"Likely range\" "
            "reflects this player's real game-to-game variability."
        )

        # Strong leans (engine/lean.py) -- only from the CURRENT season's
        # own gamelog (resolve_season_gamelog is cached; early in a season
        # it returns last season's log, and strong_lean_lines then says
        # leans haven't started). Never changes the numbers above.
        try:
            _lean_log, _lean_season, _lean_source = resolve_season_gamelog(player_id)
        except Exception:
            _lean_log, _lean_season = None, None
        _lean_multiplier = r.get("lean_defense_multiplier")
        if _lean_multiplier is None and "opponent_defense" in layer_results:
            _lean_multiplier = layer_results["opponent_defense"].multiplier_for("PTS")
        _lean_kind, _lean_lines = strong_lean_lines(
            _lean_log, _lean_season, CURRENT_SEASON, _lean_multiplier,
        )
        if _lean_kind == "leans":
            section_heading(
                "Strong leans",
                "Above or below his season average only, not a prediction of the exact "
                "number. Most games get no lean; see the methodology for the backtest.",
            )
            st.markdown("\n".join(f"- {line}" for line in _lean_lines))
        else:
            st.caption(_lean_lines[0])

        # Save this prediction to the tracker, so you can come back after
        # the actual game and see how close it was.
        section_heading(
            "Track this prediction",
            "Save it with your email, then use the tracker in the sidebar after the game.",
        )
        with st.container():
            save_col1, save_col2 = st.columns([2, 1], vertical_alignment="bottom")
            with save_col1:
                tracked_game_date = st.date_input(
                    "Game date (optional)",
                    value=None,
                    key="tracked_game_date_input",
                )
            with save_col2:
                if st.button("Save to tracker", key="save_prediction_btn",
                             icon=":material/bookmark_add:", width="stretch"):
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
        section_heading("Recent trend")
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

        line_layers = [
            alt.Chart(chart_df)
            .mark_line(point=alt.OverlayMarkDef(color="#22c55e", size=50), color="#22c55e", strokeWidth=2)
            .encode(
                x=alt.X("GAME_DATE:T", title="Game date"),
                y=alt.Y("value:Q", title=trend_stat_label),
                tooltip=["GAME_DATE:T", "MATCHUP:N", "value:Q"],
            )
        ]
        entered_line = line_inputs.get(trend_stat_col, 0)
        if entered_line and entered_line > 0:
            rule_df = pd.DataFrame({"y": [entered_line]})
            line_layers.append(
                alt.Chart(rule_df).mark_rule(color="#f87171", strokeDash=[6, 4]).encode(y="y:Q")
            )

        trend_chart = (
            alt.layer(*line_layers)
            .properties(height=280)
            .configure(background="#11151c", padding=12)
            .configure_axis(labelColor="#9aa3b2", titleColor="#9aa3b2",
                             gridColor="#1b202b", domainColor="#252b38", tickColor="#252b38",
                             labelFont="Inter", titleFont="Inter", titleFontWeight=600)
            .configure_view(strokeWidth=0)
        )
        st.altair_chart(trend_chart, use_container_width=True)
        trend_context = f"vs. {opponent_full_name} only" if using_h2h else "overall"
        st.caption(
            f"Last {len(recent_games)} games ({trend_context}). Dashed red line marks the "
            f"line you entered for {trend_stat_label}, if any."
        )

        # Head-to-head history vs this specific opponent, across the last
        # few seasons -- including seasons on a different team, since that
        # context (e.g. a player traded to a new team) genuinely matters
        # for how they've performed against this particular opponent.
        section_heading(f"Head-to-head vs {opponent_full_name}")
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
                    f"{len(h2h_df)} game(s) since {roster_change_date.isoformat()} only — "
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
                section_heading(f"Head-to-head vs {name}", "Any team he was on at the time")
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
                    f"team they were playing for at the time — across {', '.join(HEAD_TO_HEAD_SEASONS)}."
                )

            if len(valid_ids) >= 2:
                combo_label = " + ".join(key_players_input)
                section_heading(f"Combined: {combo_label}", "Same team, at the same time")
                if no_combo_data:
                    st.warning(
                        f"No historical games found with {combo_label} on the same team "
                        f"together, across {', '.join(HEAD_TO_HEAD_SEASONS)}. This exact "
                        f"pairing appears to be new — the prediction above reflects each "
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

        # Hit-rate tables, props.cash style, for all three stats. Each
        # defaults to that stat's season average if the user left the
        # line at 0, clearly labeled which source is being used.
        hit_rate_configs = [
            (label, line_inputs[col], predictions[col]["base"], col)
            for col, label in STAT_COLUMNS
        ]

        if using_h2h:
            section_heading(
                "Hit rates",
                f"How often he went over each line. Team-specific here: based on "
                f"{len(game_log_for_hitrate)} game(s) vs. {html.escape(opponent_full_name)} only, "
                f"not the full season.",
            )
        else:
            section_heading(
                "Hit rates",
                "How often he went over each line. L5, L10 and L20 are his last 5, 10 and 20 games.",
            )

        for stat_label, line_val, base_val, col in hit_rate_configs:
            effective_line = line_val if line_val > 0 else round(base_val, 1)
            if line_val > 0:
                line_source_note = "your line"
            elif using_h2h:
                line_source_note = f"head-to-head avg vs. {opponent_full_name}"
            else:
                line_source_note = "season average"

            hit_rates = get_hit_rate_table(game_log_for_hitrate, effective_line, col)
            if using_h2h:
                # "Season" doesn't mean much for a head-to-head-only log --
                # relabel it to reflect what it actually represents here.
                hit_rates = {("All H2H" if k == "Season" else k): v for k, v in hit_rates.items()}

            badges_html = (
                f'<div class="hr-row"><div class="hr-label">'
                f'<div class="hr-stat">{stat_label}</div>'
                f'<div class="hr-line">Line {effective_line} · {html.escape(line_source_note)}</div>'
                f'</div><div class="hit-rate-row">'
            )
            for label, (pct, n) in hit_rates.items():
                if pct is None or n == 0:
                    css_class = "hit-rate-gray"
                    display = "N/A"
                else:
                    css_class = "hit-rate-green" if pct >= 50 else "hit-rate-red"
                    display = f"{pct:.0f}%"
                badges_html += (
                    f'<div class="hit-rate-badge {css_class}">'
                    f'<div class="label">{label}</div>'
                    f'<div class="pct">{display}</div>'
                    f'</div>'
                )
            badges_html += '</div></div>'
            st.markdown(badges_html, unsafe_allow_html=True)

        st.caption(
            "Note on Turnovers: green here just means the player exceeded the line more "
            "often than not — for turnovers, going OVER is bad for the player, so green "
            "doesn't mean \"good\" the way it does for the other stats."
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
                         f"— not used in the calculation, no data exists to attribute schemes to individual players.")
            if post_change_thin_sample:
                st.write(
                    f"**[9] Roster-change uncertainty:** the likely range above was widened "
                    f"x{THIN_SAMPLE_SPREAD_MULTIPLIER} since the post-change sample is small — "
                    f"treat this prediction as a rougher estimate than usual until more games "
                    f"have been played with the new roster."
                )

        st.caption(
            "Remember: this is a transparent estimate built from a handful of adjustments, "
            "not a trained predictive model. Treat it as a starting point for your own analysis."
        )

with tab2:
    section_heading(
        "Predict a full matchup",
        "A projected box score for both teams from current rosters, using the same "
        "season-baseline and opponent-defense engine as the Single Player tab.",
        first=True,
    )
    with st.expander("What this does and doesn't model"):
        st.markdown(
            "Each team is built from its live current roster, so departed players drop off "
            "and new arrivals show up automatically. This does not model rotations or "
            "minutes — every player is projected at their own adjusted season-average "
            "rate, not a coach's actual rotation plan. Players marked out are handled "
            "here; new teammates, primary defender and scheme stay in the Single Player tab."
        )

# Bounds the COMBINED out-redistribution multiplier per stat, and only
# when two or more out players actually stack. One player's result is
# passed through untouched (see combine_out_redistributions), because
# single ratios on low-count stats often fall outside this band by
# themselves. The clamp exists so several small-sample ratios can't
# compound into a number no evidence supports. See this patch's module
# docstring for the worked example.
OUT_STACK_CLAMP = (0.75, 1.35)

_DATA_QUALITY_RANK = {
    "unavailable": 0,
    "real_thin_sample": 1,
    "manual_estimate": 2,
    "real_fallback_season": 3,
    "real_current": 4,
}


def combine_out_redistributions(results):
    """Fold several out-redistribution AdjustmentResults into one, so
    downstream consumers still see a single "out_redistribution" layer
    with the shape engine/adjustments/base.py documents.

    Returns None when nothing was computed at all, so the caller can
    keep its existing "no redistribution" branch unchanged.

    Only results with .applied True contribute to the product -- a
    layer that found no usable sample returns a neutral value AND
    applied=False, and multiplying by its neutral 1.0 would be
    harmless but would wrongly drag sample_n and data_quality down."""
    if not results:
        return None

    contributing = [r for r in results if r.applied]
    if not contributing:
        # Nothing usable: hand back the first neutral result so the
        # caller's "computed but not applied" messaging still works.
        return results[0]

    if len(contributing) == 1:
        # Nothing to stack, so nothing to clamp: hand back that layer's
        # own result untouched (its per-stat note included). Without
        # this, the band would bind on a SINGLE player's low-count
        # stats -- a 0.4 -> 0.6 BLK swing is already a 1.5x ratio --
        # and one-out-player behaviour would silently change.
        return contributing[0]

    lo, hi = OUT_STACK_CLAMP
    value = {}
    clamped_stats = []
    for col, _label in STAT_COLUMNS:
        product = 1.0
        for r in contributing:
            product *= r.multiplier_for(col)
        bounded = max(lo, min(hi, product))
        if bounded != product:
            clamped_stats.append(col)
        value[col] = bounded

    # The weakest evidence in the stack is what the combined number is
    # really worth -- and `note` below is built from this same variable,
    # never a second count expression (base.py contract, rule 2).
    sample_n = min(r.sample_n for r in contributing)
    data_quality = min(
        (r.data_quality for r in contributing),
        key=lambda q: _DATA_QUALITY_RANK.get(q, 0),
    )

    note = (
        f"Combined redistribution from {len(contributing)} player(s) marked out, "
        f"backed by at least {sample_n} real game(s) for the thinnest of them."
    )
    if clamped_stats:
        note += (
            f" Combined effect capped to the {lo:g}-{hi:g} band for "
            f"{', '.join(clamped_stats)} — stacked small-sample ratios "
            f"compounded past what the evidence supports."
        )

    return AdjustmentResult(
        layer=OUT_REDISTRIBUTION_LAYER,
        value=value,
        note=note,
        data_quality=data_quality,
        sample_n=sample_n,
        applied=True,
    )


@st.cache_data(ttl=3600, show_spinner=False)
def get_opponent_missing_adjustment_cached(missing_names, season):
    """Full Matchup calls this once per team on every rerun, and every
    widget change reruns the script. On a cache miss the underlying
    layer does a career-stats lookup and a 0.5s pause per player, so
    it's memoised here. missing_names must be a tuple (hashable, and
    order-stable so the same selection hits the same cache entry)."""
    return get_opponent_missing_adjustment(list(missing_names), season)


def predict_player_vs_opponent(player_id, player_name, opponent_id, out_player_ids=None,
                               opponent_missing_result=None):
    """MVP matchup-predictor engine: season baseline + opponent-defense
    adjustment, plus an optional out-redistribution adjustment when
    out_player_ids is given (Full Matchup's "mark players as out"
    feature -- see engine/adjustments/teammates.py's
    get_out_redistribution_adjustment for the real "games with vs.
    without" comparison and why it's a distinct, narrowly-scoped
    mechanic rather than a reuse of the single-player tool's general
    missing_teammates layer). Also applies the opponent's absences via
    opponent_missing_result (see below). Still deliberately excludes
    missing/new-teammate, primary defender, and scheme adjustments --
    that nuance stays in the single-player tool, per the approved v1
    scope.

    opponent_missing_result: an AdjustmentResult from
    get_opponent_missing_adjustment_cached() for the players marked out
    on the OTHER team, or None when nobody is. The caller computes it
    once per team, not per player -- its value doesn't depend on which
    player is being projected. Its multiplier is folded in only when
    .applied is True; it's recorded in layer_results either way, so a
    saved row shows "opponent absences were given but couldn't be
    weighted" rather than silently nothing.

    out_player_ids accepts None, a single id, or a list. Several out
    players each get their own redistribution against the same resolved
    gamelog; combine_out_redistributions() folds them into ONE
    AdjustmentResult under the existing "out_redistribution" layer key,
    with the combined per-stat multiplier bounded by OUT_STACK_CLAMP so
    stacked small-sample ratios can't compound past their evidence.

    out_player_ids=None (the default, and every call site before this
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
    plus "missing_opponents" when opponent_missing_result was given,
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

    # Accepts None, a single id (every pre-multi-out call site), or a
    # list -- normalised here so there is one code path below.
    if out_player_ids is None:
        out_ids = []
    elif isinstance(out_player_ids, (list, tuple, set)):
        out_ids = [pid for pid in out_player_ids if pid is not None]
    else:
        out_ids = [out_player_ids]

    redistribution_result = None
    if out_ids:
        try:
            player_df, season, _source = resolve_season_gamelog(player_id)
        except Exception:
            player_df = pd.DataFrame()
        if not player_df.empty:
            # One resolve_season_gamelog call for all of them -- each
            # out player is a Game_ID set-membership test against the
            # same log, so this adds no extra live fetches per extra
            # player marked out.
            per_out = [
                get_out_redistribution_adjustment(player_id, out_id, season, player_df)
                for out_id in out_ids
            ]
            redistribution_result = combine_out_redistributions(per_out)

    predictions = {}
    for col, _label in STAT_COLUMNS:
        base_mean, base_std = season_stats[col]
        multiplier = defense_result.multiplier_for(col)
        if redistribution_result is not None:
            multiplier *= redistribution_result.multiplier_for(col)
        if opponent_missing_result is not None and opponent_missing_result.applied:
            multiplier *= opponent_missing_result.multiplier_for(col)
        predicted = base_mean * multiplier
        spread = base_std if pd.notna(base_std) else predicted * 0.2
        low = max(0, predicted - spread * 0.6)
        high = predicted + spread * 0.6
        predictions[col] = {"base": base_mean, "predicted": predicted, "low": low, "high": high}

    layer_results = {"opponent_defense": defense_result}
    if opponent_missing_result is not None:
        layer_results["missing_opponents"] = opponent_missing_result
    if redistribution_result is not None:
        layer_results["out_redistribution"] = redistribution_result

    return predictions, season_source, def_source_note, layer_results



with tab2:
    # The "who's out" pickers sit ABOVE the Predict button: each team's
    # roster is known as soon as the team is picked, so there's no reason
    # to make people predict first and then mark players out. That means
    # the team pickers can't live in an st.form (a form doesn't rerun when
    # a selection changes, so the out pickers couldn't follow the teams).

    def build_team_projection(team_id, opponent_id, out_player_ids=None,
                              opponent_missing_result=None):
        """out_player_ids: a list -- each is excluded entirely from
        the projected rows (not called through
        predict_player_vs_opponent at all -- there's nothing to
        project for a player marked out), and the whole list is
        passed through to every remaining player's prediction so
        engine/adjustments/teammates.py's
        get_out_redistribution_adjustment can apply per out player.
        opponent_missing_result: computed once by the caller for the
        OTHER team's out list, passed unchanged to every player.
        Returns (rows, skipped, unadjusted, out_names, trackable):
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
        out_ids = list(out_player_ids or [])
        rows = []
        skipped = []
        unadjusted = []
        out_names = []
        trackable = []
        for pid, pname in roster:
            if pid in out_ids:
                out_names.append(pname)
                continue
            result = predict_player_vs_opponent(
                pid, pname, opponent_id, out_player_ids=out_ids,
                opponent_missing_result=opponent_missing_result,
            )
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
        return rows, skipped, unadjusted, out_names, trackable

    def pick_out_players(team_id, team_full, out_key):
        """One team's "who's out" picker. Rendered for BOTH teams
        before either table is built, because each table depends on
        both out lists. Returns (out_ids, out_names), both in
        roster order."""
        roster = get_team_roster(team_id)
        roster_id_to_name = dict(roster)
        out_ids = st.multiselect(
            f"Mark {team_full} players as out (optional)",
            options=[pid for pid, _pname in roster],
            format_func=lambda pid: player_search_label(roster_id_to_name[pid]),
            key=out_key,
            help=(
                "Each player marked out is compared against this team's real "
                "games with vs. without them. Stacked effects are capped, so "
                "marking several players out won't compound into a projection "
                "the sample can't support."
            ),
        )

        chosen = set(out_ids)
        out_pairs = [(pid, pname) for pid, pname in roster if pid in chosen]
        return [pid for pid, _ in out_pairs], [pname for _, pname in out_pairs]

    def render_team_projection(team_id, team_full, opponent_id, opponent_full, opponent_abbr,
                               out_ids, opponent_out_names):
        section_heading(team_full, "Projected box score")
        # Same season as the Single Player tool's call -- see this
        # patch's docstring; change both together.
        opponent_missing_result = (
            get_opponent_missing_adjustment_cached(tuple(opponent_out_names), PREVIOUS_SEASON)
            if opponent_out_names else None
        )

        with st.spinner("Calculating..."):
            rows, skipped, unadjusted, out_names, trackable = build_team_projection(
                team_id, opponent_id, out_player_ids=out_ids,
                opponent_missing_result=opponent_missing_result,
            )

        if rows:
            table_df = pd.DataFrame(rows)
            stat_labels = [label for _col, label in STAT_COLUMNS]
            team_total = table_df[stat_labels].sum().round(1)
            total_row = {"Player": "Team total", **team_total.to_dict()}
            _label_to_col = {label: col for col, label in STAT_COLUMNS}
            st.dataframe(
                pd.concat([table_df, pd.DataFrame([total_row])], ignore_index=True),
                width="stretch", hide_index=True,
                column_config={
                    "Player": st.column_config.TextColumn("Player", pinned=True),
                    **{
                        label: st.column_config.NumberColumn(
                            STAT_TABLE_LABELS[_label_to_col[label]], format="%.1f", help=label,
                        )
                        for label in stat_labels
                    },
                },
            )
            pts_label = STAT_COLUMNS[0][1]
            if out_ids:
                # Same projection with nobody out, for comparison. Each
                # player's baseline/defense lookups are cached, so this
                # costs little. Nothing forces the two totals to match:
                # the backtest showed per-player accuracy is best when
                # teammates only pick up part of an absent player's load.
                full_rows = build_team_projection(team_id, opponent_id)[0]
                full_pts = round(sum(r[pts_label] for r in full_rows), 1)
                st.caption(
                    f"Projected team total: {team_total[pts_label]:.1f} points with "
                    f"these players out, vs {full_pts:.1f} at full strength. "
                    f"Teammates pick up only part of an absent player's load here — "
                    f"that's what tested most accurately player by player — so "
                    f"the team total drops."
                )
            if skipped:
                st.caption(
                    "Team totals only include players projected above, so they run "
                    "low when players without enough NBA data would also play."
                )
        else:
            st.info("No players with enough data to project.")
        out_label = ", ".join(out_names)
        if out_names:
            st.caption(
                f"Marked out: {out_label}. Remaining players' numbers above are "
                f"adjusted using their real historical games with vs. without "
                f"those players this season, where enough real data exists."
            )
        if unadjusted:
            st.caption(
                f"Not enough real history without {out_label} to trust "
                f"an adjustment — shown at their normal projection instead: "
                f"{', '.join(unadjusted)}"
            )
        if opponent_missing_result is not None:
            opp_label = ", ".join(opponent_out_names)
            if opponent_missing_result.applied:
                opp_mult = opponent_missing_result.multiplier_for(STAT_COLUMNS[0][0])
                st.caption(
                    f"{opponent_full} without {opp_label}: every stat above is "
                    f"scaled x{opp_mult:.3f}, weighted by their real minutes and "
                    f"estimated net rating — the same missing-opponent adjustment "
                    f"the Single Player tool uses. It's one multiplier for every "
                    f"stat, not a per-stat estimate."
                )
            elif opponent_missing_result.sample_n > 0:
                st.caption(
                    f"{opponent_full} without {opp_label}: shown for context, not "
                    f"applied. Backtested over 16,101 real games, an opponent's "
                    f"missing players didn't make individual stat lines more "
                    f"accurate, so these numbers don't change."
                )
            else:
                st.caption(
                    f"{opponent_full} without {opp_label}: no adjustment applied "
                    f"— {opponent_missing_result.note}"
                )
        if skipped:
            st.caption(f"Not enough data to project: {', '.join(skipped)}")

        return [
            {**t, "opponent_full_name": opponent_full, "opponent_abbr": opponent_abbr}
            for t in trackable
        ]

    with st.container(border=True, key="matchup_box"):
        mcol1, mcol2 = st.columns(2)
        with mcol1:
            default_a = team_names.index("Oklahoma City Thunder") if "Oklahoma City Thunder" in team_names else None
            team_a_input = st.selectbox(
                "Team A", options=team_names, index=default_a,
                placeholder="Search a team...", key="matchup_team_a",
            )
        with mcol2:
            default_b = team_names.index("San Antonio Spurs") if "San Antonio Spurs" in team_names else None
            team_b_input = st.selectbox(
                "Team B", options=team_names, index=default_b,
                placeholder="Search a team...", key="matchup_team_b",
            )

        teams_ready = bool(team_a_input and team_b_input and team_a_input != team_b_input)
        if team_a_input and team_b_input and team_a_input == team_b_input:
            st.error("Please select two different teams.")

        if teams_ready:
            team_a_id, team_a_full, team_a_abbr = get_team_id(team_a_input)
            team_b_id, team_b_full, team_b_abbr = get_team_id(team_b_input)
            pick_a, pick_b = st.columns(2)
            # Keyed by team id: switching a team gives a fresh, empty
            # picker instead of carrying over ids from another roster.
            with pick_a:
                team_a_out_ids, team_a_out_names = pick_out_players(
                    team_a_id, team_a_full, f"out_input_{team_a_id}"
                )
            with pick_b:
                team_b_out_ids, team_b_out_names = pick_out_players(
                    team_b_id, team_b_full, f"out_input_{team_b_id}"
                )
            st.caption(
                "Optional: mark anyone who won't play. A player marked out is "
                "removed from his team's table, and his teammates' lines are "
                "adjusted from real games without him. The other team's lines "
                "are noted but not changed."
            )

        matchup_submitted = st.button("Predict matchup", key="predict_matchup_btn",
                                      type="primary", width="stretch")

    if matchup_submitted:
        if not teams_ready:
            st.error("Please select two different teams.")
        else:
            st.session_state["matchup_pair"] = (team_a_id, team_b_id)

    if teams_ready and st.session_state.get("matchup_pair") == (team_a_id, team_b_id):
        team_a_trackable = render_team_projection(
            team_a_id, team_a_full, team_b_id, team_b_full, team_b_abbr,
            team_a_out_ids, team_b_out_names,
        )
        team_b_trackable = render_team_projection(
            team_b_id, team_b_full, team_a_id, team_a_full, team_a_abbr,
            team_b_out_ids, team_a_out_names,
        )

        section_heading(
            "Track this matchup",
            "Save every player's projection, then check them in the sidebar tracker after the game.",
        )
        save_m1, save_m2 = st.columns([2, 1], vertical_alignment="bottom")
        with save_m1:
            matchup_game_date = st.date_input(
                "Game date (required to save)",
                value=None, key="matchup_game_date_input",
            )
        with save_m2:
            save_matchup_clicked = st.button(
                "Save matchup", key="save_matchup_btn",
                icon=":material/bookmark_add:", width="stretch",
            )
        if save_matchup_clicked:
            save_email = st.session_state.get("tracker_email_input", "").strip().lower()
            all_trackable = team_a_trackable + team_b_trackable
            if not save_email:
                st.warning(
                    "Enter your email in the Prediction Tracker (sidebar) first, "
                    "so you can find these predictions again."
                )
            elif not matchup_game_date:
                st.warning(
                    "Enter the game date first — required to track this matchup "
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

# ---------------------------- Footer ----------------------------
# The full legal notice lives here, on every page view; the hero carries
# a one-line version above the fold.
st.markdown(
    '<div class="bw-footer">'
    '<div class="brand">Boxscore Whisperer</div>'
    'Boxscore Whisperer is an independent, unofficial statistical tool and is not '
    'affiliated with, endorsed by, or connected to the NBA, its teams, or the National '
    'Basketball Players Association. All player and team data is sourced from publicly '
    'available statistics. Predictions are transparent statistical estimates, not '
    'guarantees — for entertainment and informational purposes only, not betting advice. '
    'If sports betting is a concern for you, resources are available at '
    '<a href="https://ncpgambling.org" target="_blank" rel="noopener">ncpgambling.org</a> '
    'or 1-800-GAMBLER. Must be 18+ (or the legal age in your jurisdiction) to use any '
    'information here in connection with wagering.'
    '</div>',
    unsafe_allow_html=True,
)
