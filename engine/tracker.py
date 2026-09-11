"""Prediction tracker: local file-based log -- moved from app.py, and
hardened against two real risks the original had. See PR/commit
message for the full walkthrough; short version:

1. TORN WRITES: writing directly to the real file (`df.to_csv(LOG_PATH)`)
   can leave it truncated if the process dies mid-write. Fixed by
   writing to a temp file in the same directory and atomically
   replacing the real file with os.replace() once the write is
   complete -- there is no window where a reader can observe a
   half-written file, even across a crash.

2. LOST UPDATES: append_prediction_to_log() and
   refresh_pending_predictions() both read-modify-write the whole file
   with no lock between the steps. Two saves close together (Streamlit
   Community Cloud runs ONE shared process for every visitor, so two
   different users clicking "Save to tracker" within the same second
   both hit this file) can each read the same starting state, and
   whichever write finishes last silently discards the other's change.
   Atomic writes alone do NOT fix this -- each individual write stays
   clean, they just cleanly clobber each other's *result*. Fixed with
   a real file lock (fcntl.flock) held across the ENTIRE
   read-modify-write span, so a second writer blocks until the first
   one's full cycle is done and only then reads the updated state.

POSIX assumption: fcntl.flock is POSIX-only. Verified today (per
Streamlit's own docs, docs.streamlit.io/deploy/streamlit-community-cloud/status
and .../app-dependencies) that Streamlit Community Cloud runs on
Debian 11 ("bullseye") Linux, and local dev here is macOS -- both
POSIX, so this holds for every environment this app actually runs in
today. This is a third-party platform detail, not something under
this app's control, so it isn't "guaranteed forever": if the
deployment target ever became non-POSIX (e.g. a Windows host), this
locking implementation would need to switch to msvcrt.locking() or a
cross-platform library (e.g. portalocker) -- flagging that now rather
than assuming it silently, per explicit request.

Schema extension: LOG_COLUMNS now also includes {STAT}_base (the
unadjusted baseline value per stat, already computed as
predictions[col]["base"] in app.py, just not previously logged) and
layers_json (one JSON blob per saved prediction summarizing every
AdjustmentResult that fired: {"applied", "data_quality", "sample_n",
"value"} per layer name -- built from the actual AdjustmentResult
objects at save time, not reconstructed from note text). Additive
only: old rows read back fine with these columns as NaN/missing,
pandas' pd.concat handles the column-set union automatically.

Privacy scoping (saved_by_email): lightweight, NOT real authentication
-- no password, no verification, just a plain string a visitor types
into the sidebar before saving. app.py filters the sidebar's display
to rows whose saved_by_email matches whatever's currently entered, so
a random visitor doesn't see every other visitor's saved predictions
by default (the original audit finding). Anyone who knows/guesses
another person's email can still type it in and see that email's
rows -- this solves "zero-effort visibility to strangers," not
"data is protected from someone who has the email." Same additive
principle as the columns above: rows saved before this existed have
no email, normalize to "", and are therefore not visible to anyone
through the filtered UI -- an accepted, honest consequence of not
being able to retroactively invent an owner, not a bug.

Full Matchup tracking (source, append_predictions_batch): Full Matchup
projects a whole roster at once (10-15+ players), not one player, so
saving its predictions means writing many rows in one action, not one.
_build_row() factors the per-row construction out of
append_prediction_to_log() so append_predictions_batch() can reuse it
without duplicating the stat-column/layers_json logic. The batch
function holds ONE _locked() acquisition across building AND writing
ALL rows -- deliberately not append_prediction_to_log() called in a
loop. A loop would still be safe (each call is independently lock-
protected, so no corruption or lost update) but NOT atomic as a whole
batch: a concurrent reader could observe a partial roster (e.g. 6 of
15 players) mid-save. One lock for the whole batch closes that window
instead of just tolerating it. `source` ("single_player" |
"full_matchup") is additive, defaulted for every existing call site,
so downstream analysis can eventually ask whether the two tools'
different adjustment stacks (six layers vs. two) predict differently
-- a question this column exists to make answerable later, not to
answer itself now.

Post-game context capture (_capture_post_game_context and its
helpers): pure descriptive instrumentation, captured on a successful
resolve, never read by anything yet -- consistent with the plan
file's "self-evolving, not ever-changing" post-game feedback
guardrails (quiet observation first, no acting on this data). A
capture failure never blocks the hit/miss resolution it rides
alongside; every new field defaults to None and stays None if its own
data source isn't available, same "leave it blank, don't guess"
principle as every other insufficient-data path in this app.

Four things worth knowing before touching this block:
1. Season here means "the season the resolved GAME belongs to", via
   engine.season.season_for_date() -- NOT CURRENT_SEASON/PREVIOUS_SEASON,
   which answer "what season represents right now" and would be wrong
   for a game resolved months after it was played.
2. Shooting percentages are computed as sum(makes)/sum(attempts) across
   real season games, never an average of per-game percentages (wrong
   when attempt counts differ game to game).
3. Teammate-out detection matches by personId, never by name -- this
   codebase already hit real same-team, same-last-name collisions
   (the Brandon Williams bug, and two Williamses on one real OKC
   roster this session), so name-string matching is never trusted here.
4. _key_teammates_for() uses the CURRENT roster cache, not the
   historical roster as of the resolved game -- a deliberate, accepted
   simplification for near-term resolution of recent/current-season
   games (the actual use case), not built for accurately resolving a
   years-old game where a trade happened in between. The backtest
   project's dedicated point-in-time roster handling exists for that
   different problem and isn't reused here.
"""

import contextlib
import datetime
import fcntl
import json
import os
import uuid

import pandas as pd
import streamlit as st
from nba_api.stats.endpoints import boxscoreadvancedv3

from engine.stat_columns import STAT_COLUMNS
from engine.season import season_for_date
from engine.team_ids import TEAM_ID_BY_ABBR
from engine.cache import _load_df_cache, _save_df_cache
from engine.game_log import fetch_combined_game_log
from engine.adjustments.defense import get_league_advanced_team_stats

LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prediction_log.csv"
)
LOCK_PATH = LOG_PATH + ".lock"

LOG_COLUMNS = ["id", "saved_at", "player_id", "player_full_name", "opponent_full_name",
               "opponent_abbr", "game_date", "status", "saved_by_email", "source"]
for _col, _ in STAT_COLUMNS:
    LOG_COLUMNS += [f"{_col}_low", f"{_col}_mid", f"{_col}_high", f"{_col}_actual", f"{_col}_hit", f"{_col}_base"]
LOG_COLUMNS += ["layers_json"]
# Post-game context -- populated only on a successful resolve (see
# _capture_post_game_context), NaN otherwise. Additive, same pattern
# as every column above.
LOG_COLUMNS += [
    "game_id",
    "min_actual", "min_season_avg",
    "fg_pct_actual", "fg_pct_season_avg",
    "fg3_pct_actual", "fg3_pct_season_avg",
    "opp_def_rating_actual", "opp_def_rating_season_avg",
    "key_teammate_out", "key_teammate_out_names",
]

KEY_TEAMMATE_GAMES_PLAYED_PCT = 0.70  # a "key" teammate is one who played in
# at least this fraction of their team's real games that season -- a casual
# "this person's clearly a regular" bar, not a precise statistical cutoff.
# Reconsider this threshold, not the mechanism, if it turns out too strict
# or too loose in practice (same spirit as MIN_BASELINE_GAMES elsewhere).


@contextlib.contextmanager
def _locked():
    """Exclusive lock held for an entire read-modify-write cycle --
    NOT just around the write. A second caller blocks here until the
    first caller's full cycle (read + modify + write) is done."""
    with open(LOCK_PATH, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _atomic_write_csv(df, path):
    """Write to a temp file in the same directory, then atomically
    replace the real file. os.replace() is atomic on POSIX and
    Windows -- no reader can ever observe a half-written file, even
    across a crash mid-write. The temp file lives in the same
    directory as the target so os.replace() is a same-filesystem
    rename, not a cross-filesystem copy (which wouldn't be atomic)."""
    tmp_path = f"{path}.tmp.{os.getpid()}"
    df.to_csv(tmp_path, index=False)
    os.replace(tmp_path, path)


def load_prediction_log():
    """Loads the CSV and guarantees every column in LOG_COLUMNS is
    present, in that order -- reindex() adds any column the on-disk
    file predates as NaN, rather than leaving it simply absent.

    This matters beyond just "doesn't crash": every additive schema
    change so far (PTS_base, layers_json, now saved_by_email) happened
    to be safe for existing callers only because they all read columns
    via row.get(...) on individual rows, which tolerates a missing key.
    Direct column indexing on the whole DataFrame (df["saved_by_email"],
    which app.py's sidebar filter needs) does NOT tolerate a missing
    column -- pandas raises KeyError, not NaN. A real on-disk
    prediction_log.csv that predates this file's current LOG_COLUMNS
    (any file that hasn't had a row appended since the last schema
    change) hits exactly this. Reindexing here once, centrally, makes
    the "additive schema, old rows stay valid" guarantee actually true
    for every caller, not just the ones that happened to use .get().

    dtype={"id": str} fixes the flaky-test issue documented in the
    project plan's Known Issues: ids are 8-char hex
    (uuid.uuid4().hex[:8]), and pandas' default type inference sometimes
    reads one as a number instead of a string, breaking any later
    string comparison (e.g. `df["id"] == some_id_string`). Confirmed
    two independent triggers, not just one: an all-digit id (e.g.
    "41502247") gets read as int64, AND an id that merely *contains* a
    single "e" with only digits after it (e.g. "5e123456" -- "e" is a
    legal hex digit) gets misread as scientific-notation float, since
    pandas' parser only needs the string to match a numeric grammar,
    not be all-digit. Forcing the dtype here fixes both uniformly and
    is correct regardless of what any id happens to look like, rather
    than trying to constrain id generation against every string shape
    pandas' inference might misfire on -- see
    test_id_column_survives_pandas_numeric_misparse for both cases
    pinned as a permanent regression test.

    dtype={..., "game_id": str} guards against the exact same failure
    mode for a second column: real NBA Game_IDs are zero-padded strings
    (e.g. "0022400604", from nba_api's own Game_ID column, itself
    already dtype str at the source -- confirmed directly against a
    real cached gamelog). Without this, an all-digit Game_ID read back
    via plain pd.read_csv() silently loses its leading zeros as int64
    ("22400604") -- caught live during this column's own verification,
    not theoretically."""
    if os.path.exists(LOG_PATH):
        try:
            df = pd.read_csv(LOG_PATH, dtype={"id": str, "game_id": str})
            return df.reindex(columns=LOG_COLUMNS)
        except Exception:
            return pd.DataFrame(columns=LOG_COLUMNS)
    return pd.DataFrame(columns=LOG_COLUMNS)


def save_prediction_log(df):
    _atomic_write_csv(df, LOG_PATH)


def _build_layers_json(layer_results):
    """layer_results: dict of {layer_name: AdjustmentResult}, or None.
    Deliberately excludes `note` (large, human-facing text, not needed
    for machine analysis) and `layer` (redundant -- it's already the
    dict key here)."""
    if not layer_results:
        return "{}"
    return json.dumps({
        name: {
            "applied": res.applied,
            "data_quality": res.data_quality,
            "sample_n": res.sample_n,
            "value": res.value,
        }
        for name, res in layer_results.items()
    })


def _build_row(player_id, player_full_name, opponent_full_name, opponent_abbr,
                game_date, predictions, layer_results=None, saved_by_email=None,
                source="single_player"):
    """One fully-formed row dict, ready to append -- factored out of
    append_prediction_to_log() so append_predictions_batch() can reuse
    the exact same per-row construction (stat columns, layers_json)
    without duplicating it. predictions is {col: {"low", "predicted",
    "high", "base"}}. layer_results is {layer_name: AdjustmentResult}
    for every layer that fired -- optional. source distinguishes which
    tool produced this row ("single_player" | "full_matchup"); see
    module docstring."""
    row = {
        "id": uuid.uuid4().hex[:8],
        "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "player_id": player_id,
        "player_full_name": player_full_name,
        "opponent_full_name": opponent_full_name,
        "opponent_abbr": opponent_abbr,
        "game_date": game_date.isoformat() if game_date else "",
        "status": "pending",
        "saved_by_email": (saved_by_email or "").strip().lower(),
        "source": source,
    }
    for col, _ in STAT_COLUMNS:
        p = predictions[col]
        row[f"{col}_low"] = round(p["low"], 1)
        row[f"{col}_mid"] = round(p["predicted"], 1)
        row[f"{col}_high"] = round(p["high"], 1)
        row[f"{col}_actual"] = None
        row[f"{col}_hit"] = None
        row[f"{col}_base"] = round(p["base"], 1) if "base" in p else None
    row["layers_json"] = _build_layers_json(layer_results)
    return row


def append_prediction_to_log(player_id, player_full_name, opponent_full_name,
                              opponent_abbr, game_date, predictions, layer_results=None,
                              saved_by_email=None, source="single_player"):
    """saved_by_email is the plain string typed into the sidebar's tracker
    email input -- optional; see this module's docstring for what this
    does and doesn't protect against. See _build_row() for the shared
    per-row construction."""
    row = _build_row(
        player_id, player_full_name, opponent_full_name, opponent_abbr,
        game_date, predictions, layer_results=layer_results,
        saved_by_email=saved_by_email, source=source,
    )
    with _locked():
        df = load_prediction_log()
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        save_prediction_log(df)
    return row["id"]


def append_predictions_batch(rows_input, saved_by_email=None, source="full_matchup"):
    """Saves many predictions (a whole Full Matchup roster) as ONE
    atomic unit -- one _locked() acquisition across building AND
    writing every row, not append_prediction_to_log() called once per
    player. See module docstring for why that distinction matters (a
    loop is safe but not atomic as a whole batch).

    rows_input: list of dicts, each with the same fields
    append_prediction_to_log() takes per call (player_id,
    player_full_name, opponent_full_name, opponent_abbr, game_date,
    predictions, and optionally layer_results). saved_by_email and
    source apply uniformly to the whole batch -- a Full Matchup save is
    one action by one visitor for one matchup, not a mix.

    Returns the list of new row ids, in the same order as rows_input.
    Returns [] without touching the file if rows_input is empty."""
    if not rows_input:
        return []
    built_rows = [
        _build_row(
            r["player_id"], r["player_full_name"], r["opponent_full_name"],
            r["opponent_abbr"], r["game_date"], r["predictions"],
            layer_results=r.get("layer_results"), saved_by_email=saved_by_email,
            source=source,
        )
        for r in rows_input
    ]
    with _locked():
        df = load_prediction_log()
        df = pd.concat([df, pd.DataFrame(built_rows)], ignore_index=True)
        save_prediction_log(df)
    return [r["id"] for r in built_rows]


def _load_or_fetch_advanced_box_score(game_id):
    """Real single-game advanced box score -- player-level and
    team-level frames from ONE BoxScoreAdvancedV3 call, each cached
    under its own key. Deliberately not cached_or_live() (which caches
    one dataframe per call): this is one real API response split into
    two frames, not two separate fetches, so it mirrors
    cached_or_live()'s live-then-cached-fallback behavior by hand via
    _load_df_cache/_save_df_cache directly rather than issuing the
    live call twice.

    Returns (player_df, team_df) -- either may be None if that half
    was never successfully cached (see this module's docstring for why
    a partial-write mismatch can only ever surface as one side being
    None here, never as silently-wrong paired data)."""
    player_key = f"boxscore_advanced_player_{game_id}"
    team_key = f"boxscore_advanced_team_{game_id}"

    if not st.session_state.get("_live_nba_api_blocked"):
        try:
            box = boxscoreadvancedv3.BoxScoreAdvancedV3(game_id=game_id, timeout=5)
            frames = box.get_data_frames()
            player_df, team_df = frames[0], frames[1]
            _save_df_cache(player_key, player_df)
            _save_df_cache(team_key, team_df)
            return player_df, team_df
        except Exception:
            st.session_state["_live_nba_api_blocked"] = True

    player_df, _ = _load_df_cache(player_key)
    team_df, _ = _load_df_cache(team_key)
    return player_df, team_df


def _season_shooting_and_minutes(player_id, season):
    """Real season-long minutes average and shooting percentages for
    player_id in `season`, from their cached/fetched full-season
    gamelog (fetch_combined_game_log -- the same shared source used
    everywhere else, not a new fetch mechanism). Percentages are
    sum(makes)/sum(attempts) across real games, never an average of
    per-game percentages (wrong when attempt counts differ game to
    game). Returns None if the season's gamelog can't be obtained
    (blocked live + not cached) -- degrades by leaving its columns
    blank, never by guessing."""
    try:
        df = fetch_combined_game_log(player_id, season)
    except Exception:
        return None
    if df is None or df.empty:
        return None

    fga_sum, fgm_sum = df["FGA"].sum(), df["FGM"].sum()
    fg3a_sum, fg3m_sum = df["FG3A"].sum(), df["FG3M"].sum()
    return {
        "min_avg": df["MIN"].mean(),
        "fg_pct": (fgm_sum / fga_sum) if fga_sum else None,
        "fg3_pct": (fg3m_sum / fg3a_sum) if fg3a_sum else None,
    }


def _key_teammates_for(team_id, season):
    """Real players on team_id's CURRENT cached roster who played in at
    least KEY_TEAMMATE_GAMES_PLAYED_PCT of the team's real games that
    season -- the same "count real games played from a cached gamelog"
    technique engine/adjustments/teammates.py's
    get_out_redistribution_adjustment already uses, pointed at defining
    "regular" instead of "out".

    Uses the roster CACHE as-is, not the historical roster as of that
    season -- see this module's docstring for why that's a deliberate,
    accepted simplification for this feature's actual use case (near-
    term resolution of recent games), not a point-in-time-correct
    historical reconstruction.

    Returns [(player_id, player_name), ...], possibly empty."""
    roster_df, _ = _load_df_cache(f"roster_{team_id}")
    if roster_df is None or roster_df.empty:
        return []

    try:
        league_df = get_league_advanced_team_stats(season)
        team_row = league_df[league_df["TEAM_ID"] == team_id]
        if team_row.empty:
            return []
        team_games = int(team_row.iloc[0]["GP"])
    except Exception:
        return []
    if team_games <= 0:
        return []

    key_teammates = []
    for _, r in roster_df.iterrows():
        pid, name = r["PLAYER_ID"], r["PLAYER"]
        try:
            gamelog = fetch_combined_game_log(pid, season)
        except Exception:
            continue
        if gamelog is None or gamelog.empty:
            continue
        if len(gamelog) / team_games >= KEY_TEAMMATE_GAMES_PLAYED_PCT:
            key_teammates.append((pid, name))
    return key_teammates


def _capture_post_game_context(row, actual, game_id):
    """Pure descriptive instrumentation for a prediction that just
    resolved -- see module docstring for the full design and the
    guardrails this stays inside (capture only, nothing reads these
    yet). Never raises: every field defaults to None and a failure
    anywhere inside just leaves the remaining fields None too, so a
    capture problem can never take down the hit/miss resolution it
    rides alongside.

    row: the row being resolved (already has player_id, opponent_abbr,
    game_date). actual: the resolved game's own box-score row, already
    fetched by try_resolve_prediction to compute {col}_actual -- reused
    here for MIN/FG_PCT/FG3_PCT/MATCHUP instead of re-fetching. game_id:
    this specific game's real Game_ID, from `actual`."""
    context = {
        "game_id": game_id,
        "min_actual": None, "min_season_avg": None,
        "fg_pct_actual": None, "fg_pct_season_avg": None,
        "fg3_pct_actual": None, "fg3_pct_season_avg": None,
        "opp_def_rating_actual": None, "opp_def_rating_season_avg": None,
        "key_teammate_out": None, "key_teammate_out_names": None,
    }

    try:
        if pd.notna(actual.get("MIN")):
            context["min_actual"] = float(actual["MIN"])
        if pd.notna(actual.get("FG_PCT")):
            context["fg_pct_actual"] = float(actual["FG_PCT"])
        if pd.notna(actual.get("FG3_PCT")):
            context["fg3_pct_actual"] = float(actual["FG3_PCT"])
    except Exception:
        pass

    try:
        game_date = pd.to_datetime(row["game_date"]).date()
        season = season_for_date(game_date)
    except Exception:
        return context  # can't determine season -- nothing below is derivable

    season_stats = _season_shooting_and_minutes(int(row["player_id"]), season)
    if season_stats:
        context["min_season_avg"] = season_stats["min_avg"]
        context["fg_pct_season_avg"] = season_stats["fg_pct"]
        context["fg3_pct_season_avg"] = season_stats["fg3_pct"]

    opponent_abbr = row.get("opponent_abbr")
    matchup = actual.get("MATCHUP")
    player_team_abbr = str(matchup).split()[0] if matchup else None

    player_box, team_box = _load_or_fetch_advanced_box_score(game_id)

    if team_box is not None and opponent_abbr:
        opp_row = team_box[team_box["teamTricode"] == opponent_abbr]
        if not opp_row.empty:
            context["opp_def_rating_actual"] = float(opp_row.iloc[0]["defensiveRating"])

    opp_team_id = TEAM_ID_BY_ABBR.get(opponent_abbr)
    if opp_team_id is not None:
        try:
            league_df = get_league_advanced_team_stats(season)
            opp_season_row = league_df[league_df["TEAM_ID"] == opp_team_id]
            if not opp_season_row.empty:
                context["opp_def_rating_season_avg"] = float(opp_season_row.iloc[0]["DEF_RATING"])
        except Exception:
            pass

    player_team_id = TEAM_ID_BY_ABBR.get(player_team_abbr)
    if player_box is not None and player_team_abbr and player_team_id is not None:
        played_ids = set(player_box[player_box["teamTricode"] == player_team_abbr]["personId"])
        key_teammates = _key_teammates_for(player_team_id, season)
        missing = [name for pid, name in key_teammates
                   if pid != int(row["player_id"]) and pid not in played_ids]
        context["key_teammate_out"] = bool(missing)
        context["key_teammate_out_names"] = ", ".join(missing) if missing else None

    return context


def try_resolve_prediction(row, get_head_to_head_log):
    """Look up the player's actual game log for the saved game_date and
    opponent. If a matching game is found, fill in actual values and
    mark hit/miss per stat (within the predicted low-high range).
    Returns the updated row (as a dict) whether or not it resolved.

    get_head_to_head_log is injected (not imported) to avoid a
    circular import -- it lives in app.py, which imports this module."""
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

    game_id = actual.get("Game_ID")
    try:
        context = _capture_post_game_context(row, actual, game_id)
        row.update(context)
    except Exception:
        pass  # capture failure never blocks the resolution above

    row["status"] = "resolved"
    return row


def refresh_pending_predictions(get_head_to_head_log):
    """Try to resolve every pending prediction in the log against real
    results. Safe to call repeatedly -- already-resolved rows are
    skipped. get_head_to_head_log is injected -- see try_resolve_prediction."""
    with _locked():
        df = load_prediction_log()
        if df.empty:
            return df
        updated_rows = []
        for _, row in df.iterrows():
            if row.get("status") == "pending":
                updated_rows.append(try_resolve_prediction(row, get_head_to_head_log))
            else:
                updated_rows.append(dict(row))
        new_df = pd.DataFrame(updated_rows)
        save_prediction_log(new_df)
        return new_df
