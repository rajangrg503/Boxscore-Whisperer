"""Fast unit tests for engine/tracker.py. All tests point LOG_PATH at a
temp file (monkeypatched) so they never touch the real prediction_log.csv.
"""

import datetime

import pandas as pd
import pytest

from engine import tracker
from engine.adjustments.base import AdjustmentResult, ALL_STATS


@pytest.fixture
def temp_log(tmp_path, monkeypatch):
    log_path = tmp_path / "prediction_log.csv"
    monkeypatch.setattr(tracker, "LOG_PATH", str(log_path))
    monkeypatch.setattr(tracker, "LOCK_PATH", str(log_path) + ".lock")
    return log_path


def _sample_predictions():
    return {
        col: {"low": 20.0, "predicted": 25.0, "high": 30.0, "base": 24.0}
        for col, _ in tracker.STAT_COLUMNS
    }


def test_load_empty_log_returns_correct_columns(temp_log):
    df = tracker.load_prediction_log()
    assert df.empty
    assert list(df.columns) == tracker.LOG_COLUMNS


def test_append_then_load_roundtrip(temp_log):
    new_id = tracker.append_prediction_to_log(
        2544, "LeBron James", "Boston Celtics", "BOS",
        datetime.date(2026, 9, 10), _sample_predictions(),
    )
    df = tracker.load_prediction_log()
    assert len(df) == 1
    assert df.iloc[0]["id"] == new_id
    assert df.iloc[0]["PTS_base"] == 24.0
    assert df.iloc[0]["layers_json"] == "{}"
    # saved_by_email defaults to "" when not passed -- old call shapes
    # (and any future caller that forgets it) still work. Note: pandas'
    # CSV round-trip reads an empty string back as NaN, not "" -- same
    # class of quirk as the documented id-as-int64 flake. app.py's
    # filter already accounts for this via .fillna("") before comparing.
    assert pd.isna(df.iloc[0]["saved_by_email"]) or df.iloc[0]["saved_by_email"] == ""


def test_saved_by_email_is_normalized(temp_log):
    tracker.append_prediction_to_log(
        2544, "LeBron James", "Boston Celtics", "BOS",
        datetime.date(2026, 9, 10), _sample_predictions(),
        saved_by_email="  Someone@Example.com  ",
    )
    df = tracker.load_prediction_log()
    assert df.iloc[0]["saved_by_email"] == "someone@example.com"


def test_saved_by_email_distinguishes_rows_for_filtering(temp_log):
    # This is the property app.py's sidebar filter relies on: two
    # different emails' rows are distinguishable after a normal
    # load_prediction_log() call, with no extra filtering logic needed
    # inside the tracker module itself.
    tracker.append_prediction_to_log(
        2544, "LeBron James", "Boston Celtics", "BOS",
        datetime.date(2026, 9, 10), _sample_predictions(),
        saved_by_email="alice@example.com",
    )
    tracker.append_prediction_to_log(
        201939, "Stephen Curry", "Los Angeles Lakers", "LAL",
        datetime.date(2026, 9, 11), _sample_predictions(),
        saved_by_email="bob@example.com",
    )
    df = tracker.load_prediction_log()
    alice_rows = df[df["saved_by_email"] == "alice@example.com"]
    bob_rows = df[df["saved_by_email"] == "bob@example.com"]
    assert len(alice_rows) == 1
    assert len(bob_rows) == 1
    assert alice_rows.iloc[0]["player_full_name"] == "LeBron James"
    assert bob_rows.iloc[0]["player_full_name"] == "Stephen Curry"


def test_layers_json_captures_adjustment_results(temp_log):
    layer_results = {
        "opponent_defense": AdjustmentResult(
            layer="opponent_defense", value={ALL_STATS: 1.05}, note="n/a",
            data_quality="real_current", sample_n=0, applied=True,
        ),
        "missing_teammates": AdjustmentResult(
            layer="missing_teammates", value={"PTS": 0.9, "AST": 1.1}, note="n/a",
            data_quality="real_current", sample_n=8, applied=True,
        ),
    }
    tracker.append_prediction_to_log(
        2544, "LeBron James", "Boston Celtics", "BOS",
        datetime.date(2026, 9, 10), _sample_predictions(), layer_results=layer_results,
    )
    df = tracker.load_prediction_log()
    import json
    parsed = json.loads(df.iloc[0]["layers_json"])
    assert parsed["opponent_defense"]["applied"] is True
    assert parsed["opponent_defense"]["value"] == {"_all": 1.05}
    assert parsed["missing_teammates"]["sample_n"] == 8
    assert parsed["missing_teammates"]["value"] == {"PTS": 0.9, "AST": 1.1}
    # note/layer are deliberately excluded
    assert "note" not in parsed["opponent_defense"]
    assert "layer" not in parsed["opponent_defense"]


def test_backward_compatible_with_old_schema_rows(temp_log):
    old_columns = ["id", "saved_at", "player_id", "player_full_name", "opponent_full_name",
                   "opponent_abbr", "game_date", "status"]
    for col, _ in tracker.STAT_COLUMNS:
        old_columns += [f"{col}_low", f"{col}_mid", f"{col}_high", f"{col}_actual", f"{col}_hit"]
    old_row = {c: None for c in old_columns}
    old_row.update({
        "id": "oldrow1", "saved_at": "2026-08-01T00:00:00", "player_id": 1,
        "player_full_name": "Old Player", "opponent_full_name": "X", "opponent_abbr": "X",
        "game_date": "2026-08-05", "status": "pending", "PTS_low": 10.0, "PTS_mid": 15.0,
        "PTS_high": 20.0,
    })
    pd.DataFrame([old_row]).to_csv(temp_log, index=False)

    loaded = tracker.load_prediction_log()
    assert len(loaded) == 1
    assert loaded.iloc[0]["PTS_mid"] == 15.0

    new_id = tracker.append_prediction_to_log(
        2, "New Player", "Y", "Y", datetime.date(2026, 9, 10), _sample_predictions(),
    )
    final = tracker.load_prediction_log()
    assert len(final) == 2
    old = final[final["id"] == "oldrow1"].iloc[0]
    new = final[final["id"] == new_id].iloc[0]
    assert old["PTS_mid"] == 15.0
    assert pd.isna(old["PTS_base"])  # old row never had this column -- NaN, not corrupted
    assert new["PTS_base"] == 24.0
    # A pre-existing row has no saved_by_email -- accepted consequence of
    # the privacy-scoping migration: it stays in the file (not deleted,
    # not corrupted) but can't match any real email a viewer types in,
    # so it becomes unfindable through app.py's filtered sidebar. Confirm
    # it doesn't accidentally match app.py's filter, which normalizes
    # missing values with .fillna("") before comparing to the entered email.
    assert pd.isna(old["saved_by_email"]) or old["saved_by_email"] == ""
    assert final["saved_by_email"].fillna("").iloc[0] != "somebody@example.com"


def test_load_reindexes_missing_columns_without_keyerror(temp_log):
    # Reproduces a real bug hit during live verification: a
    # prediction_log.csv written before saved_by_email existed, read
    # back with NO append_prediction_to_log() call in between (the
    # sidebar's actual real-world flow -- a visitor just opens the app).
    # Before load_prediction_log() reindexed to LOG_COLUMNS, direct
    # column indexing (df["saved_by_email"]) raised KeyError here,
    # because pd.read_csv() only returns whatever headers are literally
    # in the file -- the "additive schema" guarantee only held for
    # callers using row.get(...), not whole-column indexing.
    old_columns = ["id", "saved_at", "player_id", "player_full_name", "opponent_full_name",
                   "opponent_abbr", "game_date", "status"]
    for col, _ in tracker.STAT_COLUMNS:
        old_columns += [f"{col}_low", f"{col}_mid", f"{col}_high", f"{col}_actual", f"{col}_hit"]
    old_row = {c: None for c in old_columns}
    old_row.update({"id": "oldrow1", "player_id": 1, "player_full_name": "Old Player",
                     "status": "pending"})
    pd.DataFrame([old_row]).to_csv(temp_log, index=False)

    df = tracker.load_prediction_log()
    assert list(df.columns) == tracker.LOG_COLUMNS
    assert pd.isna(df["saved_by_email"].iloc[0])
    assert pd.isna(df["PTS_base"].iloc[0])
    assert pd.isna(df["layers_json"].iloc[0])
    # The exact operation that crashed live: filtering by email on a
    # DataFrame loaded from a file that predates the column.
    filtered = df[df["saved_by_email"].fillna("") == "alice@example.com"]
    assert filtered.empty


def test_atomic_write_leaves_no_temp_file_behind(temp_log):
    tracker.append_prediction_to_log(
        2544, "LeBron James", "Boston Celtics", "BOS",
        datetime.date(2026, 9, 10), _sample_predictions(),
    )
    leftover_tmp_files = list(temp_log.parent.glob(f"{temp_log.name}.tmp.*"))
    assert leftover_tmp_files == []
