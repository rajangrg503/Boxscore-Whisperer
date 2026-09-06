"""Tests for analytics/layer_accuracy.py. All point LOG_PATH at a temp
file (monkeypatched) so they never touch the real prediction_log.csv."""

import json

import pandas as pd
import pytest

from engine import tracker
from analytics import layer_accuracy


@pytest.fixture
def temp_log(tmp_path, monkeypatch):
    log_path = tmp_path / "prediction_log.csv"
    monkeypatch.setattr(tracker, "LOG_PATH", str(log_path))
    monkeypatch.setattr(tracker, "LOCK_PATH", str(log_path) + ".lock")
    return log_path


def _make_row(id_, base, actual, multiplier, saved_at="2026-09-06T00:00:00",
              status="resolved", layer_name="opponent_defense", applied=True):
    row = {c: None for c in tracker.LOG_COLUMNS}
    row.update({
        "id": id_, "status": status, "saved_at": saved_at,
        "PTS_base": base, "PTS_actual": actual,
        "layers_json": json.dumps({
            layer_name: {"applied": applied, "data_quality": "real_current",
                         "sample_n": 0, "value": {"_all": multiplier}},
        }),
    })
    return row


def _write_rows(temp_log, rows):
    pd.DataFrame(rows).to_csv(temp_log, index=False)


def test_context_only_by_design_short_circuits_without_touching_log(temp_log):
    # No log file even exists at this path -- if this tried to read it,
    # load_prediction_log() would just return empty, so this alone
    # doesn't prove it short-circuited. The real proof is n=0 with the
    # specific reason, which only the short-circuit path produces.
    result = layer_accuracy.layer_hit_rate("defender_matchup", "PTS")
    assert result.hit_rate is None
    assert result.reason == "context_only_by_design"
    assert result.n == 0


def test_insufficient_data_returns_none_with_reason(temp_log):
    _write_rows(temp_log, [_make_row("r1", 21.3, 14, 0.997)])
    result = layer_accuracy.layer_hit_rate("opponent_defense", "PTS")
    assert result.hit_rate is None
    assert result.reason == "insufficient_data"
    assert result.n == 1


def test_enough_data_returns_real_percentage(temp_log):
    rows = [
        _make_row("hit1", 21.3, 14, 0.997),   # predicted down, actual down -> HIT
        _make_row("hit2", 20.0, 22.0, 1.05),  # predicted up, actual up -> HIT
        _make_row("hit3", 15.0, 12.0, 0.90),  # predicted down, actual down -> HIT
        _make_row("miss1", 10.0, 8.0, 1.10),  # predicted up, actual down -> MISS
        _make_row("miss2", 25.0, 27.0, 0.95), # predicted down, actual up -> MISS
    ]
    _write_rows(temp_log, rows)
    result = layer_accuracy.layer_hit_rate("opponent_defense", "PTS")
    assert result.n == 5
    assert result.hit_rate == 60.0
    assert result.reason is None


def test_skips_rows_where_layer_not_applied(temp_log):
    rows = [_make_row(f"r{i}", 20.0, 15.0, 0.9, applied=False) for i in range(10)]
    _write_rows(temp_log, rows)
    result = layer_accuracy.layer_hit_rate("opponent_defense", "PTS")
    assert result.n == 0
    assert result.reason == "insufficient_data"


def test_skips_rows_with_no_directional_assertion(temp_log):
    rows = [_make_row(f"r{i}", 20.0, 15.0, 1.0) for i in range(10)]  # multiplier == 1.0
    _write_rows(temp_log, rows)
    result = layer_accuracy.layer_hit_rate("opponent_defense", "PTS")
    assert result.n == 0


def test_skips_unresolved_rows(temp_log):
    rows = [_make_row(f"r{i}", 20.0, 15.0, 0.9, status="pending") for i in range(10)]
    _write_rows(temp_log, rows)
    result = layer_accuracy.layer_hit_rate("opponent_defense", "PTS")
    assert result.n == 0


def test_skips_rows_where_layer_absent_from_layers_json(temp_log):
    rows = [_make_row(f"r{i}", 20.0, 15.0, 0.9, layer_name="scheme") for i in range(10)]
    _write_rows(temp_log, rows)
    result = layer_accuracy.layer_hit_rate("opponent_defense", "PTS")
    assert result.n == 0
