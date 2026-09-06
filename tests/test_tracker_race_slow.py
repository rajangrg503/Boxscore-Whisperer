"""Real two-process concurrency test for the prediction tracker's file
locking -- proves append_prediction_to_log() survives two
near-simultaneous saves, rather than one silently overwriting the
other.

Marked @pytest.mark.slow and excluded from the default `pytest tests/`
run (see pytest.ini's `addopts = -m "not slow"`). Run explicitly with:
    pytest tests/test_tracker_race_slow.py -m slow

Uses real OS processes (multiprocessing.Process), not threads --
threads share the GIL in ways that could mask a real file-level race;
separate processes force genuine concurrent file access, same as two
different Streamlit Community Cloud visitors hitting the same shared
process's filesystem at the same time.
"""

import datetime
import multiprocessing
import time

import pandas as pd
import pytest


def _sample_predictions():
    from engine.stat_columns import STAT_COLUMNS
    return {col: {"low": 1.0, "predicted": 2.0, "high": 3.0, "base": 2.0} for col, _ in STAT_COLUMNS}


def _worker_locked(log_path, player_id, barrier):
    """Uses the real, locked append_prediction_to_log()."""
    from engine import tracker
    tracker.LOG_PATH = log_path
    tracker.LOCK_PATH = log_path + ".lock"
    barrier.wait()  # both processes start their append as close together as possible
    tracker.append_prediction_to_log(
        player_id, f"Player {player_id}", "Opp", "OPP",
        datetime.date(2026, 9, 10), _sample_predictions(),
    )


def _worker_unlocked_forced_race(log_path, player_id, barrier):
    """CONTROL ONLY -- deliberately reimplements the OLD unlocked
    read-modify-write (no _locked() call at all), with a forced delay
    between read and write to GUARANTEE the interleaving that causes a
    lost update, rather than relying on OS scheduling luck. This
    proves the test harness itself is sensitive to the race -- if this
    control ever stops losing a row, the real test below would not be
    meaningful proof of anything."""
    from engine import tracker
    tracker.LOG_PATH = log_path
    tracker.LOCK_PATH = log_path + ".lock"
    barrier.wait()
    df = tracker.load_prediction_log()  # READ
    time.sleep(0.3)  # force both processes to finish reading before either writes
    row = {c: None for c in tracker.LOG_COLUMNS}
    row.update({"id": f"row{player_id}", "player_id": player_id, "status": "pending"})
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    tracker.save_prediction_log(df)  # WRITE -- whichever process writes last wins, alone


@pytest.mark.slow
def test_locked_appends_both_survive(tmp_path):
    log_path = str(tmp_path / "race_log.csv")
    barrier = multiprocessing.Barrier(2)
    p1 = multiprocessing.Process(target=_worker_locked, args=(log_path, 1, barrier))
    p2 = multiprocessing.Process(target=_worker_locked, args=(log_path, 2, barrier))
    p1.start()
    p2.start()
    p1.join(timeout=10)
    p2.join(timeout=10)

    df = pd.read_csv(log_path)
    assert len(df) == 2, f"expected 2 rows, got {len(df)} -- a concurrent append was silently lost"
    assert set(df["player_id"]) == {1, 2}


@pytest.mark.slow
def test_control_unlocked_race_loses_a_row(tmp_path):
    log_path = str(tmp_path / "race_log_control.csv")
    barrier = multiprocessing.Barrier(2)
    p1 = multiprocessing.Process(target=_worker_unlocked_forced_race, args=(log_path, 1, barrier))
    p2 = multiprocessing.Process(target=_worker_unlocked_forced_race, args=(log_path, 2, barrier))
    p1.start()
    p2.start()
    p1.join(timeout=10)
    p2.join(timeout=10)

    df = pd.read_csv(log_path)
    assert len(df) == 1, (
        f"expected the forced race to lose a row (1 survivor), got {len(df)} -- "
        f"if this control test is failing, it means the harness itself isn't "
        f"reproducing the race, which would make the real locked test above "
        f"meaningless as proof"
    )
