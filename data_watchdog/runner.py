"""Runs each endpoint's known-good sample query, validates the
response against its contract, and records the result -- both as a
structured alert (logs/watchdog_alerts.jsonl) on failure, and as a
status file (data_cache/_watchdog_status.json) that distinguishes
"stale because nobody's refreshed this in a while" from "stale because
the last refresh attempt actively FAILED validation".

This module makes real, live nba_api calls. It's meant to be run
locally (same constraint as every batch_cache_*.py script) -- nba_api
is blocked on Streamlit Cloud, so there's nothing for a check to
validate there anyway.
"""

import datetime
import json
import os

from engine.cache import CACHE_DIR
from engine.schemas import ENDPOINT_CONTRACTS
from data_watchdog.validators import ValidationResult, validate_schema
from data_watchdog.alerts import log_failure

STATUS_PATH = os.path.join(CACHE_DIR, "_watchdog_status.json")


def _load_status():
    if not os.path.exists(STATUS_PATH):
        return {}
    try:
        with open(STATUS_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_status(status):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(STATUS_PATH, "w") as f:
        json.dump(status, f, indent=2)


def check(endpoint_key: str) -> ValidationResult:
    """Runs one endpoint's sample query + validation. Always re-checks
    live (never trusts a stale status file) since the whole point is to
    catch a live regression. Returns a ValidationResult. Updates the
    on-disk status file as a side effect -- on failure, last_checked and
    passed/reason update but last_successful_refresh is left untouched,
    so a caller can tell "stale, not yet refreshed" apart from "stale,
    and the last refresh attempt actively failed"."""
    contract = ENDPOINT_CONTRACTS[endpoint_key]
    now = datetime.datetime.now().isoformat(timespec="seconds")
    status = _load_status()
    entry = status.get(endpoint_key, {})

    try:
        df = contract.sample_query()
        result = validate_schema(df, contract)
    except Exception as e:
        result = ValidationResult(passed=False, reason=f"sample query raised: {e}")

    entry["last_checked"] = now
    entry["passed"] = result.passed
    entry["reason"] = result.reason
    if result.passed:
        entry["last_successful_refresh"] = now
    status[endpoint_key] = entry
    _save_status(status)

    if not result.passed:
        log_failure(endpoint_key, result.reason)

    return result


def check_all() -> dict:
    """Runs check() for every known endpoint contract. Returns a dict
    of {endpoint_key: ValidationResult}."""
    return {key: check(key) for key in ENDPOINT_CONTRACTS}
