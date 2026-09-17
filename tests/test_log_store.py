"""engine/log_store.py: backend selection everywhere, and Postgres
behaviour when BW_TEST_DATABASE_URL points at a disposable database
(see tests/conftest.py). The Postgres tests are skipped otherwise."""

import datetime
import multiprocessing
import os

import pandas as pd
import pytest

from engine import log_store, tracker
from tests.conftest import TEST_DB_URL

needs_db = pytest.mark.skipif(not TEST_DB_URL, reason="BW_TEST_DATABASE_URL not set")


def _predictions(v=25.0):
    return {col: {"low": v - 5, "predicted": v, "high": v + 5, "base": v - 1}
            for col, _ in tracker.STAT_COLUMNS}


# ---------------------------------------------------------------- selection

def test_no_database_url_means_csv(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert log_store.database_url() is None
    assert not log_store.using_database()


def test_env_database_url_selects_postgres(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "  postgresql://u:p@h/db  ")
    assert log_store.database_url() == "postgresql://u:p@h/db"
    assert log_store.using_database()


def test_blank_env_falls_back_to_streamlit_secrets(monkeypatch):
    import streamlit as st
    monkeypatch.setenv("DATABASE_URL", "   ")
    monkeypatch.setattr(st, "secrets", {"DATABASE_URL": "postgresql://from-secrets/db"}, raising=False)
    assert log_store.database_url() == "postgresql://from-secrets/db"


def test_secrets_errors_mean_not_configured(monkeypatch):
    import streamlit as st

    class Broken:
        def get(self, *a, **k):
            raise FileNotFoundError("no secrets.toml")

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(st, "secrets", Broken(), raising=False)
    assert log_store.database_url() is None


def test_csv_text_roundtrip_keeps_extra_columns():
    rows = [{"id": "a", "status": "pending"}, {"id": "b", "status": "resolved", "new_col": "x"}]
    text = log_store._rows_to_csv_text(rows, ["id", "status", "PTS_mid"])
    assert text.splitlines()[0] == "id,status,PTS_mid,new_col"
    assert text.splitlines()[1] == "a,pending,,"


def test_duplicate_ids_get_distinct_keys():
    used = set()
    keys = [log_store._row_key({"id": "abc"}, 0, used), log_store._row_key({"id": "abc"}, 1, used),
            log_store._row_key({"id": ""}, 2, used)]
    assert keys == ["abc", "abc#2", "_row_2"]


# ---------------------------------------------------------------- postgres

@needs_db
def test_postgres_matches_csv_backend_exactly(tmp_path, monkeypatch):
    """Same operations through both backends give identical DataFrames,
    including the id / game_id string guarantees and NaN handling."""
    def exercise():
        tracker.append_prediction_to_log(
            2544, "LeBron James", "Boston Celtics", "BOS",
            datetime.date(2026, 1, 5), _predictions(), saved_by_email="A@x.com ",
        )
        tracker.append_predictions_batch([
            {"player_id": 1, "player_full_name": "One", "opponent_full_name": "Opp",
             "opponent_abbr": "OPP", "game_date": None, "predictions": _predictions(3.0)},
            {"player_id": 2, "player_full_name": "Two, Jr.", "opponent_full_name": "Opp",
             "opponent_abbr": "OPP", "game_date": datetime.date(2026, 1, 6),
             "predictions": _predictions(7.5)},
        ], saved_by_email="b@x.com")
        with tracker._locked():
            df = tracker.load_prediction_log()
            df.loc[0, "id"] = "41502247"          # all-digit id
            df.loc[1, "id"] = "5e123456"          # scientific-notation-looking id
            df.loc[2, "id"] = "00abcdef"          # leading zeros
            df.loc[0, "game_id"] = "0022400604"   # zero-padded game id
            df.loc[0, "status"] = "resolved"
            df.loc[0, "PTS_actual"] = 31.0
            df["PTS_hit"] = df["PTS_hit"].astype(object)
            df.loc[0, "PTS_hit"] = False
            df.loc[2, "layers_json"] = '{"scheme": {"applied": true, "note": "a, \\"quoted\\"\\nline"}}'
            tracker.save_prediction_log(df)
        return tracker.load_prediction_log()

    db_df = exercise()

    monkeypatch.delenv("DATABASE_URL")
    monkeypatch.setattr(tracker, "LOG_PATH", str(tmp_path / "log.csv"))
    monkeypatch.setattr(tracker, "LOCK_PATH", str(tmp_path / "log.csv.lock"))
    csv_df = exercise()

    volatile = ["saved_at"]
    pd.testing.assert_frame_equal(db_df.drop(columns=volatile), csv_df.drop(columns=volatile))
    assert db_df.loc[0, "id"] == "41502247" and db_df.loc[1, "id"] == "5e123456"
    assert db_df.loc[0, "game_id"] == "0022400604"
    assert "\\n" in db_df.loc[2, "layers_json"] or "\n" in db_df.loc[2, "layers_json"]


@needs_db
def test_save_updates_in_place_and_deletes_removed_rows():
    ids = [tracker.append_prediction_to_log(
        i, f"P{i}", "Opp", "OPP", None, _predictions()) for i in range(3)]
    with tracker._locked():
        df = tracker.load_prediction_log()
        df = df[df["id"] != ids[1]].copy()
        df.loc[df["id"] == ids[2], "status"] = "no_game_found"
        tracker.save_prediction_log(df)
    out = tracker.load_prediction_log()
    assert list(out["id"]) == [ids[0], ids[2]]          # order kept, row removed
    assert list(out["status"]) == ["pending", "no_game_found"]
    with log_store.db_transaction(lock=False) as conn:
        assert conn.execute(f"SELECT count(*) FROM {log_store.TABLE}").fetchone()[0] == 2


@needs_db
def test_refresh_resolves_without_duplicating(monkeypatch):
    tracker.append_prediction_to_log(1, "P", "Opp", "OPP", datetime.date(2026, 1, 5), _predictions())
    tracker.append_prediction_to_log(2, "Q", "Opp", "OPP", None, _predictions())

    def fake_resolve(row, _h2h):
        row = dict(row)
        if row["player_id"] == 1:
            row["status"] = "resolved"
            row["PTS_actual"] = 22.0
            row["PTS_hit"] = True
        return row

    monkeypatch.setattr(tracker, "try_resolve_prediction", fake_resolve)
    tracker.refresh_pending_predictions(lambda *a: None)
    out = tracker.load_prediction_log()
    assert len(out) == 2
    assert list(out["status"]) == ["resolved", "pending"]
    assert out.loc[0, "PTS_actual"] == 22.0


@needs_db
def test_failed_cycle_rolls_back():
    tracker.append_prediction_to_log(1, "P", "Opp", "OPP", None, _predictions())
    with pytest.raises(ZeroDivisionError):
        with tracker._locked():
            df = tracker.load_prediction_log()
            tracker.save_prediction_log(df.iloc[0:0])   # would delete everything...
            1 / 0                                        # ...but the cycle fails
    assert len(tracker.load_prediction_log()) == 1


@needs_db
def test_unreachable_database_raises_and_never_blanks(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:x@127.0.0.1:1/none?connect_timeout=2")
    with pytest.raises(log_store.TrackerStorageError):
        tracker.load_prediction_log()
    with pytest.raises(log_store.TrackerStorageError):
        tracker.append_prediction_to_log(1, "P", "Opp", "OPP", None, _predictions())


def _db_worker(url, player_id, barrier):
    os.environ["DATABASE_URL"] = url
    from engine import tracker as t
    barrier.wait()
    for _ in range(5):
        t.append_prediction_to_log(player_id, f"P{player_id}", "Opp", "OPP", None, _predictions())


@needs_db
def test_concurrent_processes_lose_nothing():
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(4)
    procs = [ctx.Process(target=_db_worker, args=(TEST_DB_URL, pid, barrier)) for pid in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
        assert p.exitcode == 0
    out = tracker.load_prediction_log()
    assert len(out) == 20
    assert out["id"].is_unique


@needs_db
def test_track_record_is_read_once_per_render_and_cached(monkeypatch):
    from analytics import layer_accuracy
    calls = {"n": 0}
    real = layer_accuracy.load_prediction_log

    def counting():
        calls["n"] += 1
        return real()

    monkeypatch.setattr(layer_accuracy, "load_prediction_log", counting)
    notes = {key: "note" for key, _ in layer_accuracy.LAYER_DISPLAY}
    layer_accuracy.build_layer_lines(notes)
    layer_accuracy.build_layer_lines(notes)
    assert calls["n"] == 1


def test_track_record_survives_database_outage(monkeypatch):
    from analytics import layer_accuracy

    def down():
        raise log_store.TrackerStorageError("down")

    monkeypatch.setattr(layer_accuracy, "load_prediction_log", down)
    notes = {key: "note" for key, _ in layer_accuracy.LAYER_DISPLAY}
    lines = layer_accuracy.build_layer_lines(notes)
    assert len(lines) == len(layer_accuracy.LAYER_DISPLAY)
    assert any("unavailable right now" in line for line in lines)
