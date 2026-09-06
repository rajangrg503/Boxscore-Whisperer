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


def test_atomic_write_leaves_no_temp_file_behind(temp_log):
    tracker.append_prediction_to_log(
        2544, "LeBron James", "Boston Celtics", "BOS",
        datetime.date(2026, 9, 10), _sample_predictions(),
    )
    leftover_tmp_files = list(temp_log.parent.glob(f"{temp_log.name}.tmp.*"))
    assert leftover_tmp_files == []
