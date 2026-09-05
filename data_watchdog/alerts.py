"""Structured, append-only alert log for validation failures.

Plain JSON-lines to a local file -- no external infra (Slack/email/etc)
assumed. Deliberately simple and dependency-free so it's easy to wire
into a real notification channel later without redesigning anything.
"""

import datetime
import json
import os

from engine.cache import CACHE_DIR

LOG_DIR = os.path.join(os.path.dirname(CACHE_DIR), "logs")
ALERTS_PATH = os.path.join(LOG_DIR, "watchdog_alerts.jsonl")


def log_failure(endpoint_key: str, reason: str) -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    entry = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "endpoint": endpoint_key,
        "passed": False,
        "reason": reason,
        "severity": "critical",
    }
    with open(ALERTS_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")
