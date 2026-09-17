"""Shared test setup.

The prediction log has two storage backends (engine/log_store.py). By
default every test uses the CSV backend. To run the same tracker tests
against Postgres, point BW_TEST_DATABASE_URL at a disposable database:

    BW_TEST_DATABASE_URL=postgresql://... pytest tests/test_tracker.py \
        tests/test_layer_accuracy.py tests/test_log_store.py

With it set, DATABASE_URL is pointed there for every test and the table
is emptied before each one. Never point it at the production database.
"""

import os

import pytest

TEST_DB_URL = os.environ.get("BW_TEST_DATABASE_URL", "").strip()


@pytest.fixture(autouse=True)
def _prediction_log_backend(monkeypatch):
    from analytics import layer_accuracy
    layer_accuracy._recent_cache.clear()
    if not TEST_DB_URL:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        yield
        return
    monkeypatch.setenv("DATABASE_URL", TEST_DB_URL)
    from engine import log_store
    with log_store.db_transaction(lock=True) as conn:
        conn.execute(f"TRUNCATE {log_store.TABLE}")
    yield


def write_raw_log(df, path):
    """Store `df` exactly as an on-disk prediction_log.csv would hold it
    (old schemas, odd ids), in whichever backend the test run uses. The
    Postgres backend stores the same df.to_csv() cell text."""
    if TEST_DB_URL:
        from engine import log_store
        log_store.save_df(df)
    else:
        df.to_csv(path, index=False)
