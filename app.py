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
    boxscoretraditionalv2,
    synergyplaytypes,
    leagueseasonmatchups,
)

CURRENT_SEASON = "2026-27"   # update each year
PREVIOUS_SEASON = "2025-26"

# ---------- Local-to-cloud data cache ----------
# nba_api works fine when this app runs locally, but is blocked by the
# NBA's unofficial stats site when running on Streamlit Community
# Cloud's shared IP range (a known, confirmed limitation -- see the
# module docstring above). Rather than the app simply breaking on the
# cloud, every real nba_api call below routes through cached_or_live():
# it tries the live call first (works locally, and would work on any
# host nba_api isn't blocking), and falls back to a cached local copy
# of the same data if the live call fails.
#
# WORKFLOW: run this app locally periodically (or run refresh_cache.py,
# see below) to populate/update data_cache/*.json with fresh data, then
# commit and push that folder to GitHub. The deployed cloud app reads
# whatever is in data_cache/ at deploy time -- it never needs to write
# there itself, since Streamlit Cloud's filesystem doesn't persist
# writes between sessions anyway.
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_cache")


def _cache_key_to_path(key):
    safe_key = "".join(c if (c.isalnum() or c in "_-") else "_" for c in key)
    return os.path.join(CACHE_DIR, f"{safe_key}.json")


def _save_df_cache(key, df):
    """Best-effort local cache write -- safe to fail silently (e.g. on
    a read-only filesystem). Caching is a local-machine workflow; the
    deployed cloud app only ever reads these files."""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        payload = {
            "cached_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "data": df.to_dict(orient="records"),
        }
        with open(_cache_key_to_path(key), "w") as f:
            import json
            json.dump(payload, f)
    except Exception:
        pass


def _load_df_cache(key):
    """Returns (dataframe, cached_at_string) if a cache file exists for
    this key, else (None, None)."""
    path = _cache_key_to_path(key)
    if not os.path.exists(path):
        return None, None
    try:
        import json
        with open(path) as f:
            payload = json.load(f)
        return pd.DataFrame(payload["data"]), payload.get("cached_at")
    except Exception:
        return None, None


def cached_or_live(key, fetch_fn):
    """Try a live nba_api call first; fall back to a cached local copy
    if the live call raises (e.g. nba_api blocked on this host).
    Returns (dataframe, source_label) where source_label is "live" or
    "cached (<timestamp>)", so callers can show which one was actually
    used. Re-raises the live error only if no cached copy exists
    either -- at that point there's genuinely nothing to show."""
    try:
        df = fetch_fn()
        _save_df_cache(key, df)
        return df, "live"
    except Exception as live_error:
        cached_df, cached_at = _load_df_cache(key)
        if cached_df is not None:
            label = f"cached copy from {cached_at}" if cached_at else "cached copy"
            return cached_df, label
        raise live_error


SCHEME_ADJUSTMENTS = {
    "Drop coverage": 1.03,             # big sags back in the paint on PnR -- favors pull-up scorers
    "Switch everything": 0.97,         # limits easy paint/rim looks, creates size mismatches
    "Aggressive double-team / blitz": 0.90,   # meaningfully suppresses usage on the ball-handler
    "Zone defense": 1.05,              # often favors driving/playmaking scorers, weaker vs. shooters
    "Man-to-man (standard)": 1.00,     # neutral baseline
    "Nail help": 0.96,                 # help from the foul-line area collapses driving lanes
    "Weak-side / tagging": 0.97,       # help from the far side discourages drives, allows more kick-outs
    "Bigs roaming (free safety)": 0.95,  # a big leaves his man to protect the rim, suppresses paint scoring
    "Ice / blue (PnR sideline)": 0.97,   # forces ball-handler away from a sideline screen
    "Hard hedge (no full blitz)": 0.95,  # big shows hard on PnR without fully trapping
    "Deny / face-guard": 0.88,         # denies the ball entirely to a specific shooter off screens
    "Full-court press": 0.93,          # pressures possessions, forces tempo/turnovers
    "Pack the paint": 1.02,            # packs the paint against non-shooters, can favor perimeter scorers
    "None / unsure": 1.00,
}

# Where a real Synergy play-type roughly overlaps with one of the
# manual scheme labels above, we use REAL team defensive data instead
# of the guessed multiplier. Not every scheme has a genuine Synergy
# equivalent (e.g. "Zone defense" and "Man-to-man" aren't tracked as
# distinct play types), so those fall back to the manual estimate.
SCHEME_TO_SYNERGY_PLAYTYPE = {
    "Drop coverage": "PRBallHandler",
    "Switch everything": "PRBallHandler",
    "Aggressive double-team / blitz": "PRBallHandler",
    "Ice / blue (PnR sideline)": "PRBallHandler",
    "Hard hedge (no full blitz)": "PRBallHandler",
    "Nail help": "Isolation",
    "Weak-side / tagging": "Isolation",
    "Bigs roaming (free safety)": "PRRollman",
    "Deny / face-guard": "OffScreen",
    "Full-court press": "Transition",
    "Pack the paint": "Postup",
}


# ---------- Data functions (same logic as the terminal version) ----------

def get_player_id(name):
    match = players.find_players_by_full_name(name)
    if not match:
        return None, None
    return match[0]["id"], match[0]["full_name"]


def get_team_id(name):
    match = teams.find_teams_by_full_name(name)
    if not match:
        return None, None, None
    return match[0]["id"], match[0]["full_name"], match[0]["abbreviation"]


STAT_COLUMNS = [
    ("PTS", "Points"),
    ("AST", "Assists"),
    ("REB", "Rebounds"),
    ("STL", "Steals"),
    ("BLK", "Blocks"),
    ("FG3M", "3-Pointers Made"),
    ("TOV", "Turnovers"),
]

# ---------- Prediction tracker: local file-based log ----------
# Uses a plain CSV sitting next to app.py. This is the right call for
# a locally-run app -- it persists across restarts on this machine.
# KNOWN LIMITATION: if this app is later deployed to a cloud host
# (e.g. Streamlit Community Cloud), the filesystem there is typically
# ephemeral -- this log would NOT reliably survive redeploys or app
# sleep/wake cycles. A real database would be needed for that. Not a
# concern for local use, which is where this stands today.
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prediction_log.csv")

LOG_COLUMNS = ["id", "saved_at", "player_id", "player_full_name", "opponent_full_name",
               "opponent_abbr", "game_date", "status"]
for _col, _ in STAT_COLUMNS:
    LOG_COLUMNS += [f"{_col}_low", f"{_col}_mid", f"{_col}_high", f"{_col}_actual", f"{_col}_hit"]


def load_prediction_log():
    if os.path.exists(LOG_PATH):
        try:
            return pd.read_csv(LOG_PATH)
        except Exception:
            return pd.DataFrame(columns=LOG_COLUMNS)
    return pd.DataFrame(columns=LOG_COLUMNS)


def save_prediction_log(df):
    df.to_csv(LOG_PATH, index=False)


def append_prediction_to_log(player_id, player_full_name, opponent_full_name,
                              opponent_abbr, game_date, predictions):
    """predictions is the same dict built in main(): {col: {"low", "predicted", "high", ...}}"""
    row = {
        "id": uuid.uuid4().hex[:8],
        "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "player_id": player_id,
        "player_full_name": player_full_name,
        "opponent_full_name": opponent_full_name,
        "opponent_abbr": opponent_abbr,
        "game_date": game_date.isoformat() if game_date else "",
        "status": "pending",
    }
    for col, _ in STAT_COLUMNS:
        p = predictions[col]
        row[f"{col}_low"] = round(p["low"], 1)
        row[f"{col}_mid"] = round(p["predicted"], 1)
        row[f"{col}_high"] = round(p["high"], 1)
        row[f"{col}_actual"] = None
        row[f"{col}_hit"] = None

    df = load_prediction_log()
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    save_prediction_log(df)
    return row["id"]


def try_resolve_prediction(row):
    """Look up the player's actual game log for the saved game_date and
    opponent. If a matching game is found, fill in actual values and
    mark hit/miss per stat (within the predicted low-high range).
    Returns the updated row (as a dict) whether or not it resolved."""
    row = dict(row)
    if not row.get("game_date") or pd.isna(row.get("game_date")) or row["game_date"] == "":
        return row  # nothing to check against

    try:
        game_date = pd.to_datetime(row["game_date"]).date()
    except Exception:
        return row

    if game_date > datetime.date.today():
        return row  # game hasn't happened yet

    player_id = int(row["player_id"])
    opponent_abbr = row["opponent_abbr"]

    match_df = get_head_to_head_log(player_id, opponent_abbr)
    if match_df.empty:
        if (datetime.date.today() - game_date).days > 1:
            row["status"] = "no_game_found"
        return row

    match_df["GAME_DATE_ONLY"] = match_df["GAME_DATE"].dt.date
    game_row = match_df[match_df["GAME_DATE_ONLY"] == game_date]
    if game_row.empty:
        if (datetime.date.today() - game_date).days > 1:
            row["status"] = "no_game_found"
        return row

    actual = game_row.iloc[0]
    for col, _ in STAT_COLUMNS:
        if col not in actual:
            continue
        actual_val = actual[col]
        row[f"{col}_actual"] = actual_val
        low, high = row.get(f"{col}_low"), row.get(f"{col}_high")
        if pd.notna(low) and pd.notna(high):
            row[f"{col}_hit"] = bool(low <= actual_val <= high)
    row["status"] = "resolved"
    return row


def refresh_pending_predictions():
    """Try to resolve every pending prediction in the log against real
    results. Safe to call repeatedly -- already-resolved rows are
    skipped."""
    df = load_prediction_log()
    if df.empty:
        return df
    updated_rows = []
    for _, row in df.iterrows():
        if row.get("status") == "pending":
            updated_rows.append(try_resolve_prediction(row))
        else:
            updated_rows.append(dict(row))
    new_df = pd.DataFrame(updated_rows)
    save_prediction_log(new_df)
    return new_df


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_combined_game_log(player_id, season):
    """Fetch a player's game log for a season, blending regular season
    and playoff games into one combined dataset. Playoffs come from a
    separate season_type query and simply get concatenated on -- a
    missing playoff log is normal (most players didn't make the
    playoffs that year) and isn't treated as an error.

    A failure on the Regular Season call specifically is a different
    story -- every rostered player has some current/recent regular
    season log, so that failing means the live API call itself broke
    (e.g. nba_api blocked on this host). In that case this falls back
    to a cached local copy via cached_or_live() instead of silently
    returning an empty, columnless DataFrame that breaks every
    downstream stat lookup with a confusing KeyError."""
    frames = []
    regular_season_error = None
    for season_type in ["Regular Season", "Playoffs"]:
        try:
            log = playergamelog.PlayerGameLog(
                player_id=player_id, season=season, season_type_all_star=season_type
            )
            df = log.get_data_frames()[0]
            if not df.empty:
                frames.append(df)
        except Exception as e:
            if season_type == "Regular Season":
                regular_season_error = e
            continue

    cache_key = f"gamelog_{player_id}_{season}"

    if regular_season_error is not None and not frames:
        cached_df, _cached_at = _load_df_cache(cache_key)
        if cached_df is not None:
            return cached_df
        raise ConnectionError(
            f"Live NBA data fetch failed for player {player_id}, season {season}, "
            f"and no cached copy exists yet."
        ) from regular_season_error

    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not combined.empty:
        _save_df_cache(cache_key, combined)
    return combined


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


def blend_baseline_stats(season_stats, shrinkage_k=8, team_h2h=None, team_h2h_n=0,
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
    """Returns (stats_dict, source_label). stats_dict maps each stat
    column (PTS, AST, REB, STL, BLK, FG3M, TOV) to a (mean, std) tuple.
    Using a dict here instead of a long positional tuple avoids the
    kind of unpacking-count bugs that come from adding a new stat
    later and forgetting to update every call site.

    Tries CURRENT_SEASON first; falls through to PREVIOUS_SEASON both
    when there aren't enough current-season games yet (early in a new
    season) AND when fetch_combined_game_log raises outright (e.g. a
    live nba_api failure with no cached copy for the current season
    specifically -- previous seasons are far more likely to already be
    cached, since a whole season's worth of games existed to fetch)."""
    try:
        df = fetch_combined_game_log(player_id, CURRENT_SEASON)
    except Exception:
        df = pd.DataFrame()

    if len(df) >= 5:
        source = f"{CURRENT_SEASON} season so far, incl. playoffs ({len(df)} games)"
    else:
        df = fetch_combined_game_log(player_id, PREVIOUS_SEASON)  # let this one raise if it fails -- nothing left to fall back to
        source = f"{PREVIOUS_SEASON} full season, incl. playoffs ({len(df)} games)"

    stats_dict = {}
    for col, _ in STAT_COLUMNS:
        stats_dict[col] = (df[col].mean(), df[col].std())

    return stats_dict, source


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
            season=season, measure_type_detailed_defense="Advanced"
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


def get_teammate_availability_adjustment(player_id, missing_names, season):
    if not missing_names:
        return 1.0, "No missing teammates specified -- no adjustment."

    try:
        df = fetch_combined_game_log(player_id, season)
    except Exception:
        return 1.0, (f"No game log available for {season} (live fetch failed, not yet "
                      f"cached) -- skipping this adjustment.")

    matching_games = []
    for _, row in df.iterrows():
        game_id = row["Game_ID"]
        try:
            box = boxscoretraditionalv2.BoxScoreTraditionalV2(game_id=game_id)
            box_df = box.get_data_frames()[0]
            players_in_game = set(box_df["PLAYER_NAME"])
        except Exception:
            continue
        time.sleep(0.5)
        if all(name not in players_in_game for name in missing_names):
            matching_games.append(row)

    if len(matching_games) < 3:
        return 1.0, (f"Only found {len(matching_games)} past games missing "
                      f"{missing_names} -- too few to trust, skipping this adjustment.")

    matched_df = pd.DataFrame(matching_games)
    avg_with_missing = matched_df["PTS"].mean()
    avg_overall = df["PTS"].mean()
    ratio = avg_with_missing / avg_overall if avg_overall else 1.0
    return ratio, (f"Found {len(matching_games)} games missing {missing_names}: "
                    f"averaged {avg_with_missing:.1f} pts vs. {avg_overall:.1f} pts overall "
                    f"({(ratio - 1) * 100:+.1f}%)")


def get_opponent_missing_adjustment(missing_opponents, season):
    if not missing_opponents:
        return 1.0, "No missing opponent players specified -- no adjustment."

    total_mpg = 0.0
    found_players = []
    for name in missing_opponents:
        try:
            match = players.find_players_by_full_name(name)
            if not match:
                continue
            pid = match[0]["id"]
            career = playercareerstats.PlayerCareerStats(player_id=pid)
            df = career.get_data_frames()[0]
            season_row = df[df["SEASON_ID"] == season]
            if season_row.empty:
                season_row = df.tail(1)
            mpg = season_row["MIN"].values[0] / season_row["GP"].values[0]
            total_mpg += mpg
            found_players.append((name, round(mpg, 1)))
            time.sleep(0.5)
        except Exception:
            continue

    if not found_players:
        return 1.0, f"Could not find stats for {missing_opponents} -- skipping adjustment."

    minutes_fraction = total_mpg / 240
    adjustment = 1 + (minutes_fraction * 0.35)
    detail = ", ".join(f"{n} ({m} MPG)" for n, m in found_players)
    return adjustment, (f"Missing: {detail} -> {total_mpg:.1f} combined MPG out "
                         f"-> adjustment x{adjustment:.3f}")


@st.cache_data(ttl=3600, show_spinner=False)
def _safe_float(value):
    """The matchups endpoint sometimes returns numeric fields as
    strings (occasionally MM:SS format for minutes). Coerce to a
    float where possible, otherwise return None so callers can skip
    that detail cleanly instead of crashing."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        value = value.strip()
        if ":" in value:  # "MM:SS" format
            try:
                mins, secs = value.split(":")
                return float(mins) + float(secs) / 60
            except ValueError:
                return None
        try:
            return float(value)
        except ValueError:
            return None
    return None


def get_defender_matchup_adjustment(player_id, player_full_name, defender_name, season):
    """Pull REAL player-vs-player matchup data (from NBA's tracking
    cameras) showing exactly how this player has performed specifically
    when guarded by the named defender -- not team-wide stats. Coverage
    depends on how much these two have actually matched up; small
    sample sizes are flagged rather than treated as a hard adjustment."""
    if not defender_name:
        return 1.0, "No primary defender specified -- no adjustment."

    match = players.find_players_by_full_name(defender_name)
    if not match:
        return 1.0, f"No player found named '{defender_name}' -- check spelling, skipping."
    defender_id = match[0]["id"]

    for try_season in [season, PREVIOUS_SEASON]:
        def _fetch():
            data = leagueseasonmatchups.LeagueSeasonMatchups(
                off_player_id_nullable=player_id,
                def_player_id_nullable=defender_id,
                season=try_season,
            )
            return data.get_data_frames()[0]

        try:
            df, _source = cached_or_live(
                f"matchup_{player_id}_{defender_id}_{try_season}", _fetch
            )
        except Exception:
            continue

        if df.empty:
            continue

        row = df.iloc[0]
        details = []
        matchup_min = _safe_float(row.get("MATCHUP_MIN"))
        partial_poss = _safe_float(row.get("PARTIAL_POSS"))
        player_pts = _safe_float(row.get("PLAYER_PTS"))
        fg_pct = _safe_float(row.get("MATCHUP_FG_PCT"))

        if matchup_min is not None:
            details.append(f"{matchup_min:.1f} matchup minutes")
        if partial_poss is not None:
            details.append(f"{partial_poss:.1f} possessions")
        if player_pts is not None:
            details.append(f"{player_pts:.0f} points scored in those minutes")
        if fg_pct is not None:
            details.append(f"{fg_pct*100:.0f}% FG in the matchup")

        if not details:
            continue

        sample_flag = ""
        if matchup_min is not None and matchup_min < 10:
            sample_flag = " -- small sample, treat as context not a hard signal."

        return 1.0, (
            f"REAL matchup data ({try_season}): {defender_name} has guarded "
            f"{player_full_name} for {', '.join(details)}.{sample_flag} Shown as "
            f"context -- not folded into the number above since sample sizes here "
            f"are usually too small to trust as a hard multiplier."
        )

    return 1.0, (
        f"No recorded head-to-head matchup minutes found between this player and "
        f"{defender_name} in {season} or {PREVIOUS_SEASON} -- skipping."
    )


@st.cache_data(ttl=3600, show_spinner=False)
def get_synergy_scheme_adjustment(team_id, scheme_label, season):
    """For scheme labels with a real Synergy play-type equivalent,
    pull actual team defensive efficiency (points per possession) for
    that play type and use it instead of the guessed multiplier.
    Falls back to the manual estimate if no mapping exists or the
    data can't be fetched."""
    play_type = SCHEME_TO_SYNERGY_PLAYTYPE.get(scheme_label)
    manual_value = SCHEME_ADJUSTMENTS[scheme_label]

    if play_type is None:
        return manual_value, (
            f"'{scheme_label}' has no real Synergy play-type equivalent -- "
            f"using your manual estimate x{manual_value:.3f} (not data-backed)."
        )

    def _fetch():
        data = synergyplaytypes.SynergyPlayTypes(
            league_id="00",
            per_mode_simple="PerGame",
            player_or_team_abbreviation="T",
            season_type_all_star="Regular Season",
            season=season,
            type_grouping_nullable="defensive",
            play_type_nullable=play_type,
            timeout=5,
        )
        return data.get_data_frames()[0]

    try:
        df, source = cached_or_live(f"synergy_{play_type}_{season}", _fetch)
        team_row = df[df["TEAM_ID"] == team_id]
        if team_row.empty or len(df) < 5:
            return manual_value, (
                f"Synergy '{play_type}' data unavailable for this team -- "
                f"falling back to manual estimate x{manual_value:.3f}."
            )
        team_ppp = team_row["PPP"].values[0]
        league_avg_ppp = df["PPP"].mean()
        gap_pct = (team_ppp - league_avg_ppp) / league_avg_ppp
        real_adjustment = 1 + (gap_pct * 0.5)  # same damping as opponent DEF_RATING
        source_note = "" if source == "live" else f" (from a {source})"
        return real_adjustment, (
            f"REAL DATA{source_note}: {scheme_label} maps to Synergy '{play_type}' "
            f"defense -- team allows {team_ppp:.2f} PPP vs. league avg "
            f"{league_avg_ppp:.2f} PPP -> adjustment x{real_adjustment:.3f}."
        )
    except Exception as e:
        return manual_value, (
            f"Synergy data fetch failed ({e}) -- falling back to manual estimate "
            f"x{manual_value:.3f}."
        )


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
    margin-bottom: 32px;
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

/* Player headshot */
.player-headshot-wrap {
    display: flex;
    justify-content: center;
    margin: 20px 0 4px 0;
}
.player-headshot-wrap img {
    width: 120px;
    height: 120px;
    object-fit: cover;
    border-radius: 50%;
    border: 3px solid #262a33;
    background-color: #171a21;
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

/* Hit-rate badges, props.cash style, tuned for dark background */
.hit-rate-row {
    display: flex;
    gap: 10px;
    justify-content: center;
    margin: 8px 0 24px 0;
}
.hit-rate-badge {
    flex: 1;
    text-align: center;
    border-radius: 12px;
    padding: 12px 8px;
}
.hit-rate-badge .label {
    font-size: 13px;
    font-weight: 800;
    opacity: 1;
    letter-spacing: 0.3px;
    text-shadow: 0 1px 2px rgba(0,0,0,0.4);
}
.hit-rate-badge .pct {
    font-size: 20px;
    font-weight: 800;
}
.hit-rate-green {
    background-color: #0d2818;
    color: #34d399;
}
.hit-rate-red {
    background-color: #2d1215;
    color: #f87171;
}
.hit-rate-gray {
    background-color: #1a1d24;
    color: #9ca3af;
}
</style>
""", unsafe_allow_html=True)

# ---------- Prediction Tracker (sidebar, always visible) ----------
with st.sidebar:
    st.markdown("### 📊 Prediction Tracker")
    if st.button("🔄 Check for results", key="refresh_tracker_btn"):
        with st.spinner("Checking saved predictions against real results..."):
            refresh_pending_predictions()

    log_df = load_prediction_log()
    if log_df.empty:
        st.caption("No saved predictions yet. Save one after running a prediction below.")
    else:
        resolved = log_df[log_df["status"] == "resolved"]
        pending = log_df[log_df["status"] == "pending"]
        no_game = log_df[log_df["status"] == "no_game_found"]

        st.caption(
            f"{len(log_df)} saved -- {len(resolved)} resolved, "
            f"{len(pending)} pending, {len(no_game)} no game found."
        )

        if not resolved.empty:
            st.markdown("**Accuracy so far (Points):**")
            pts_hits = resolved["PTS_hit"].dropna()
            if len(pts_hits) > 0:
                hit_rate = pts_hits.mean() * 100
                st.metric("Points landed in range", f"{hit_rate:.0f}%", f"{int(pts_hits.sum())}/{len(pts_hits)}")

        with st.expander("View all saved predictions"):
            display_log = log_df[[
                "saved_at", "player_full_name", "opponent_full_name", "game_date",
                "status", "PTS_low", "PTS_mid", "PTS_high", "PTS_actual", "PTS_hit",
            ]].rename(columns={
                "saved_at": "Saved", "player_full_name": "Player",
                "opponent_full_name": "Opponent", "game_date": "Game Date",
                "status": "Status", "PTS_low": "Pts Low", "PTS_mid": "Pts Mid",
                "PTS_high": "Pts High", "PTS_actual": "Pts Actual", "PTS_hit": "Pts Hit?",
            })
            st.dataframe(display_log, use_container_width=True, hide_index=True)
            st.caption(
                "Showing Points only here for space -- all 7 tracked stats are saved "
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

    with st.expander("Track more stats (steals, blocks, 3-pointers, turnovers -- optional)"):
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

    with st.expander("Advanced options (injuries, defender, scheme -- optional)"):
        adv1, adv2 = st.columns(2)
        with adv1:
            missing_teammates = st.multiselect(
                "Missing teammates", options=player_names, default=[],
                placeholder="Search and select players...",
            )
            defender_input = st.selectbox(
                "Primary defender assigned", options=player_names, index=None,
                placeholder="Search a defender...",
            )
        with adv2:
            missing_opponents = st.multiselect(
                "Missing opponent players", options=player_names, default=[],
                placeholder="Search and select players...",
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

        key_players_input = st.multiselect(
            "Also check history vs. specific opposing player(s) (optional)",
            options=player_names, default=[],
            placeholder="e.g. a star who just changed teams...",
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
        player_id, player_full_name = get_player_id(player_input)
        if player_id is None:
            st.error(f"No player found for '{player_input}'. Check spelling.")
            st.stop()

        opponent_id, opponent_full_name, opponent_abbr = get_team_id(opponent_input)
        if opponent_id is None:
            st.error(f"No team found for '{opponent_input}'. Use the full team name.")
            st.stop()

        roster_change_active = roster_change_checked and roster_change_date is not None
        h2h_cutoff = roster_change_date if roster_change_active else None

        try:
            season_stats, season_source = get_season_baseline(player_id, player_full_name)

            team_h2h_stats, team_h2h_n = None, 0
            team_h2h_note = None
            if baseline_source_input != "Season average (default)":
                num_games = 5 if "Last 5" in baseline_source_input else 10
                team_h2h_stats, team_h2h_note, team_h2h_n = get_head_to_head_baseline(
                    player_id, opponent_abbr, num_games, cutoff_date=h2h_cutoff
                )

            # Resolve every selected key player to an ID up front.
            key_player_ids = {}
            for name in key_players_input:
                pid, full_name = get_player_id(name)
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
        DEF_ADJUSTMENT_STRENGTH = 0.5
        team_h2h_weight = blend_weights["team_h2h"]
        if team_def_rating is not None:
            def_gap_pct = (team_def_rating - league_avg_def) / league_avg_def
            effective_strength = DEF_ADJUSTMENT_STRENGTH * (1 - team_h2h_weight)
            def_adjustment = 1 + (def_gap_pct * effective_strength)
            def_note = (f"{opponent_full_name} DEF_RATING: {team_def_rating:.1f} "
                        f"(league avg {league_avg_def:.1f}, source: {def_source_note}) "
                        f"-> adjustment x{def_adjustment:.3f}")
            if team_h2h_weight > 0:
                def_note += (f" (scaled down from the usual x{DEF_ADJUSTMENT_STRENGTH} strength "
                             f"since the baseline already carries {team_h2h_weight:.0%} team head-to-head weight)")
        else:
            def_adjustment = 1.0
            def_note = "Opponent defensive rating unavailable -- no adjustment."

        teammate_adj, teammate_note = get_teammate_availability_adjustment(
            player_id, missing_teammates, CURRENT_SEASON
        )
        opp_missing_adj, opp_missing_note = get_opponent_missing_adjustment(
            missing_opponents, PREVIOUS_SEASON
        )
        _, defender_note = get_defender_matchup_adjustment(
            player_id, player_full_name, defender_input, CURRENT_SEASON
        )

        scheme_adj, scheme_note = get_synergy_scheme_adjustment(
            opponent_id, scheme_input, PREVIOUS_SEASON
        )

        # Same set of adjustments applied proportionally to every
        # tracked stat -- reasonable since opponent strength, missing
        # teammates, and scheme plausibly affect all of them together,
        # though this is less rigorously tested for stats other than
        # points specifically.
        total_multiplier = def_adjustment * teammate_adj * opp_missing_adj * scheme_adj

        line_inputs = {
            "PTS": pts_line_input,
            "AST": ast_line_input,
            "REB": reb_line_input,
            "STL": stl_line_input,
            "BLK": blk_line_input,
            "FG3M": fg3m_line_input,
            "TOV": tov_line_input,
        }

        # A thin post-roster-change sample (a team's new-look defense
        # with only a handful of games played) is a genuinely less
        # certain read than a full-season number -- widen the likely
        # range rather than presenting the same false precision.
        THIN_SAMPLE_SPREAD_MULTIPLIER = 1.5

        predictions = {}
        for col, _label in STAT_COLUMNS:
            base_mean, base_std = baseline_stats[col]
            predicted = base_mean * total_multiplier
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
        "def_note": def_note,
        "teammate_note": teammate_note,
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
    def_note = r["def_note"]
    teammate_note = r["teammate_note"]
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
    no_combo_data = r["no_combo_data"]
    valid_ids = r["valid_ids"]

    headshot_url = f"https://cdn.nba.com/headshots/nba/latest/1040x760/{player_id}.png"
    st.markdown(
        f'<div class="player-headshot-wrap">'
        f'<img src="{headshot_url}" onerror="this.style.display=\'none\'">'
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

    def render_stat_card_row(stat_cols):
        html = '<div class="stat-card-row">'
        for col in stat_cols:
            label = dict(STAT_COLUMNS)[col]
            p = predictions[col]
            html += (
                f'<div class="stat-card">'
                f'<div class="stat-title">{label}</div>'
                f'<div class="stat-value">{p["predicted"]:.1f}</div>'
                f'<div class="stat-midpoint">likely range {p["low"]:.0f}-{p["high"]:.0f}</div>'
                f'</div>'
            )
        html += '</div>'
        st.markdown(html, unsafe_allow_html=True)

    render_stat_card_row(["PTS", "AST", "REB"])
    render_stat_card_row(["STL", "BLK", "FG3M", "TOV"])
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
                new_id = append_prediction_to_log(
                    player_id, player_full_name, opponent_full_name,
                    opponent_abbr, tracked_game_date, predictions,
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

    line_layers = [
        alt.Chart(chart_df)
        .mark_line(point=alt.OverlayMarkDef(color="#00c853", size=60), color="#00c853")
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
        .configure(background="#171a21")
        .configure_axis(labelColor="#9ca3af", titleColor="#9ca3af",
                         gridColor="#262a33", domainColor="#262a33")
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
        display_cols = ["GAME_DATE", "MATCHUP", "MIN", "PTS", "REB", "AST", "STL", "BLK", "FG3M", "TOV"]
        display_cols = [c for c in display_cols if c in h2h_df.columns]
        h2h_display = h2h_df[display_cols].copy()
        h2h_display["GAME_DATE"] = h2h_display["GAME_DATE"].dt.strftime("%-m/%-d/%Y")
        h2h_display = h2h_display.rename(columns={
            "GAME_DATE": "Date", "MATCHUP": "Matchup", "MIN": "Min", "PTS": "Pts",
            "REB": "Reb", "AST": "Ast", "STL": "Stl", "BLK": "Blk",
            "FG3M": "3PM", "TOV": "TOV",
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

    if key_players_input:
        for name in key_players_input:
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
            vp_display_cols = ["GAME_DATE", "MATCHUP", "MIN", "PTS", "REB", "AST", "STL", "BLK", "FG3M", "TOV"]
            vp_display_cols = [c for c in vp_display_cols if c in vs_player_df.columns]
            vp_display = vs_player_df[vp_display_cols].copy()
            vp_display["GAME_DATE"] = vp_display["GAME_DATE"].dt.strftime("%-m/%-d/%Y")
            vp_display = vp_display.rename(columns={
                "GAME_DATE": "Date", "MATCHUP": "Matchup", "MIN": "Min", "PTS": "Pts",
                "REB": "Reb", "AST": "Ast", "STL": "Stl", "BLK": "Blk",
                "FG3M": "3PM", "TOV": "TOV",
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
                combo_display_cols = ["GAME_DATE", "MATCHUP", "MIN", "PTS", "REB", "AST", "STL", "BLK", "FG3M", "TOV"]
                combo_display_cols = [c for c in combo_display_cols if c in combo_df.columns]
                combo_display = combo_df[combo_display_cols].copy()
                combo_display["GAME_DATE"] = combo_display["GAME_DATE"].dt.strftime("%-m/%-d/%Y")
                combo_display = combo_display.rename(columns={
                    "GAME_DATE": "Date", "MATCHUP": "Matchup", "MIN": "Min", "PTS": "Pts",
                    "REB": "Reb", "AST": "Ast", "STL": "Stl", "BLK": "Blk",
                    "FG3M": "3PM", "TOV": "TOV",
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
        st.markdown(
            f'<div style="text-align:center; color:#9ca3af; font-size:13px; '
            f'margin-top:8px;">Hit rates below are also team-specific -- based on '
            f'{len(game_log_for_hitrate)} game(s) vs. {opponent_full_name} only, '
            f'not the full season.</div>',
            unsafe_allow_html=True,
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

        st.markdown(
            f'<div style="text-align:center; color:#ffffff; font-weight:700; '
            f'font-size:16px; margin-top:20px;">{stat_label} '
            f'<span style="color:#9ca3af; font-weight:500; font-size:13px;">'
            f'-- hit rate vs. {effective_line} ({line_source_note})</span></div>',
            unsafe_allow_html=True,
        )
        badges_html = '<div class="hit-rate-row">'
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
        badges_html += '</div>'
        st.markdown(badges_html, unsafe_allow_html=True)

    st.caption(
        "Note on Turnovers: green here just means the player exceeded the line more "
        "often than not -- for turnovers, going OVER is bad for the player, so green "
        "doesn't mean \"good\" the way it does for the other stats."
    )

    with st.expander("See how this estimate was built (every adjustment step)"):
        baseline_summary = ", ".join(
            f"{predictions[col]['base']:.1f} {col}" for col, _ in STAT_COLUMNS
        )
        st.write(f"**[1] Baseline** ({source}): {baseline_summary}")
        st.write(f"**[2] Opponent defense:** {def_note}")
        st.write(f"**[3] Missing teammates:** {teammate_note}")
        st.write(f"**[4] Missing opponent players:** {opp_missing_note}")
        st.write(f"**[5] Primary defender:** {defender_note}")
        st.write(f"**[6] Scheme:** {scheme_note}")
        if scheme_executor_input:
            st.write(f"**[7] Scheme executed by (reference only):** {scheme_executor_input} "
                     f"-- not used in the calculation, no data exists to attribute schemes to individual players.")
        if post_change_thin_sample:
            st.write(
                f"**[8] Roster-change uncertainty:** the likely range above was widened "
                f"x{THIN_SAMPLE_SPREAD_MULTIPLIER} since the post-change sample is small -- "
                f"treat this prediction as a rougher estimate than usual until more games "
                f"have been played with the new roster."
            )

    st.caption(
        "Remember: this is a transparent estimate built from a handful of adjustments, "
        "not a trained predictive model. Treat it as a starting point for your own analysis."
    )
