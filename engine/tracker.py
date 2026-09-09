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
"""

import contextlib
import datetime
import fcntl
import json
import os
import uuid

import pandas as pd

from engine.stat_columns import STAT_COLUMNS

LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prediction_log.csv"
)
LOCK_PATH = LOG_PATH + ".lock"

LOG_COLUMNS = ["id", "saved_at", "player_id", "player_full_name", "opponent_full_name",
               "opponent_abbr", "game_date", "status", "saved_by_email"]
for _col, _ in STAT_COLUMNS:
    LOG_COLUMNS += [f"{_col}_low", f"{_col}_mid", f"{_col}_high", f"{_col}_actual", f"{_col}_hit", f"{_col}_base"]
LOG_COLUMNS += ["layers_json"]


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
    pinned as a permanent regression test."""
    if os.path.exists(LOG_PATH):
        try:
            df = pd.read_csv(LOG_PATH, dtype={"id": str})
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


def append_prediction_to_log(player_id, player_full_name, opponent_full_name,
                              opponent_abbr, game_date, predictions, layer_results=None,
                              saved_by_email=None):
    """predictions is the same dict built in main(): {col: {"low", "predicted",
    "high", "base"}}. layer_results is {layer_name: AdjustmentResult} for every
    layer that fired on this prediction -- optional, so old call shapes without
    it still work (layers_json is just "{}" in that case). saved_by_email is the
    plain string typed into the sidebar's tracker email input -- optional and
    normalized here (stripped, lowercased) so app.py's filter comparison doesn't
    have to repeat that; see this module's docstring for what this does and
    doesn't protect against."""
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

    with _locked():
        df = load_prediction_log()
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        save_prediction_log(df)
    return row["id"]


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
