"""Where the prediction log lives: a local CSV file, or Postgres.

WHY THIS EXISTS
Streamlit Community Cloud's filesystem is wiped on every reboot and
redeploy, so prediction_log.csv silently lost every saved prediction
each time the app restarted. When a DATABASE_URL is configured (an
environment variable, or a root-level key in Streamlit secrets), the log
is stored in Postgres instead (a free Neon database in production);
without one, everything stays exactly as before: the CSV next to the
repo, which is what local development and the test suite use.

HOW THE POSTGRES BACKEND KEEPS THE CSV'S BEHAVIOUR
engine/tracker.py works on whole DataFrames (load, modify, save) inside
_locked(). The Postgres backend keeps that contract:

* Locking: _locked() opens one connection, starts a transaction and
  takes a transaction-level advisory lock, so a second writer (another
  visitor, another app process) blocks until the first commits. Every
  load/save inside the block reuses that connection and transaction,
  and the whole read-modify-write commits or rolls back together. This
  is the same guarantee fcntl.flock gave the file, now across machines.
  Transaction-level (not session-level) advisory locks are safe behind
  Neon's PgBouncer pooler.

* Types: rows are stored as the exact cell text df.to_csv() writes, one
  JSON object per row, and loading rebuilds that CSV text and parses it
  with the same pd.read_csv(dtype=...) call the file backend uses. So
  dtypes, NaN handling and the id/game_id string guarantees (see
  tracker.load_prediction_log) are identical by construction, not by
  a second hand-written type mapping.

* Saving writes only rows whose text or position changed (plus deletes
  rows that are no longer in the DataFrame), so a save stays cheap as
  the log grows. `pos` stores each row's position in the saved
  DataFrame, so the loaded order always matches what was saved.

psycopg is imported lazily, so the CSV path (and the tests) never need
it installed.
"""

import contextlib
import contextvars
import csv
import io
import json
import os

DATABASE_URL_KEY = "DATABASE_URL"
TABLE = "prediction_log"
# Arbitrary fixed 64-bit key for pg_advisory_xact_lock -- every writer
# uses the same one, so they serialize on it.
ADVISORY_LOCK_KEY = 7_311_205_517_224_117_001
CONNECT_TIMEOUT_SECONDS = 10

_current_conn = contextvars.ContextVar("prediction_log_conn", default=None)
_schema_ready_for = set()


class TrackerStorageError(RuntimeError):
    """The prediction log's database couldn't be reached or written.
    app.py shows a friendly message instead of a traceback."""


def database_url():
    """DATABASE_URL from the environment, else from Streamlit secrets.
    Returns None when neither is set (local dev, tests): CSV backend."""
    url = os.environ.get(DATABASE_URL_KEY, "").strip()
    if url:
        return url
    try:
        import streamlit as st
        url = str(st.secrets.get(DATABASE_URL_KEY, "")).strip()
    except Exception:
        # No secrets.toml at all raises here -- that just means "not set".
        url = ""
    return url or None


def using_database():
    return database_url() is not None


def _connect(url):
    try:
        import psycopg
    except ImportError as e:  # requirements.txt lists psycopg[binary]
        raise TrackerStorageError("psycopg is not installed") from e
    try:
        # prepare_threshold=None: no server-side prepared statements,
        # which a transaction-mode pooler (Neon's -pooler host) can't
        # carry between transactions.
        return psycopg.connect(
            url, connect_timeout=CONNECT_TIMEOUT_SECONDS, prepare_threshold=None,
        )
    except Exception as e:
        raise TrackerStorageError(f"could not connect to the prediction database: {e}") from e


def _ensure_schema(conn, url):
    """Create the table on first use. Only runs DDL when the table is
    missing, and then under the advisory lock: running CREATE ... IF NOT
    EXISTS on every connection takes table locks that can deadlock
    against a writer that already holds the advisory lock."""
    if url in _schema_ready_for:
        return
    if conn.execute("SELECT to_regclass(%s)", (TABLE,)).fetchone()[0] is None:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (ADVISORY_LOCK_KEY,))
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                seq bigint GENERATED ALWAYS AS IDENTITY,
                row_key text PRIMARY KEY,
                pos integer NOT NULL,
                row_data jsonb NOT NULL,
                updated_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(f"CREATE INDEX IF NOT EXISTS {TABLE}_pos_idx ON {TABLE} (pos)")
    _schema_ready_for.add(url)


@contextlib.contextmanager
def db_transaction(lock=True):
    """One connection + one transaction for a read-modify-write cycle.
    Nested calls reuse the outer transaction (and its lock)."""
    existing = _current_conn.get()
    if existing is not None:
        yield existing
        return
    url = database_url()
    conn = _connect(url)
    token = _current_conn.set(conn)
    try:
        try:
            with conn.transaction():
                if lock:
                    conn.execute("SELECT pg_advisory_xact_lock(%s)", (ADVISORY_LOCK_KEY,))
                _ensure_schema(conn, url)
                yield conn
        except TrackerStorageError:
            raise
        except Exception as e:
            if _is_db_error(e):
                raise TrackerStorageError(f"prediction database error: {e}") from e
            raise
    finally:
        _current_conn.reset(token)
        conn.close()


def _is_db_error(e):
    try:
        import psycopg
    except ImportError:
        return False
    return isinstance(e, psycopg.Error)


def _rows_to_csv_text(rows, columns):
    """rows: list of {column: cell text}. Header = `columns` first, then
    any extra keys in first-seen order (additive schema: a row saved by
    a newer version keeps its extra columns)."""
    header = list(columns)
    seen = set(header)
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                header.append(k)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=header, restval="", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def _df_to_cell_rows(df):
    """The exact cell text df.to_csv() would write, as dicts."""
    text = df.to_csv(index=False)
    return list(csv.DictReader(io.StringIO(text)))


def _row_key(cells, position, used):
    key = (cells.get("id") or "").strip() or f"_row_{position}"
    # A duplicated id must not collapse two rows into one.
    base, n = key, 1
    while key in used:
        n += 1
        key = f"{base}#{n}"
    used.add(key)
    return key


def load_csv_text(columns):
    """The whole log as CSV text (header only when empty)."""
    with db_transaction(lock=False) as conn:
        cur = conn.execute(f"SELECT row_data FROM {TABLE} ORDER BY pos, seq")
        rows = [r[0] if isinstance(r[0], dict) else json.loads(r[0]) for r in cur.fetchall()]
    return _rows_to_csv_text(rows, columns)


def save_df(df):
    """Make the table match `df`: upsert changed/new rows, delete gone ones."""
    from psycopg.types.json import Jsonb

    cell_rows = _df_to_cell_rows(df)
    used = set()
    wanted = [(_row_key(cells, i, used), cells) for i, cells in enumerate(cell_rows)]

    with db_transaction(lock=True) as conn:
        cur = conn.execute(f"SELECT row_key, pos, row_data FROM {TABLE}")
        existing = {
            k: (pos, v if isinstance(v, dict) else json.loads(v))
            for k, pos, v in cur.fetchall()
        }

        changed = [
            (k, pos, cells) for pos, (k, cells) in enumerate(wanted)
            if existing.get(k) != (pos, cells)
        ]
        with conn.cursor() as c:
            if changed:
                c.executemany(
                    f"""
                    INSERT INTO {TABLE} (row_key, pos, row_data) VALUES (%s, %s, %s)
                    ON CONFLICT (row_key) DO UPDATE
                        SET pos = EXCLUDED.pos, row_data = EXCLUDED.row_data,
                            updated_at = now()
                    """,
                    [(k, pos, Jsonb(cells)) for k, pos, cells in changed],
                )
            gone = [k for k in existing if k not in used]
            if gone:
                c.execute(f"DELETE FROM {TABLE} WHERE row_key = ANY(%s)", (gone,))
