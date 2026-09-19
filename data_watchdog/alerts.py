"""Structured, append-only alert log for validation failures.

Plain JSON-lines to a local file -- no external infra (Slack/email/etc)
assumed. Deliberately simple and dependency-free so it's easy to wire
into a real notification channel later without redesigning anything.
"""

import datetime
import json
import os

from engine.cache import REPO_ROOT

# Anchored to the repo, not to CACHE_DIR: since the cache can now
# resolve to an unpacked archive under /tmp (engine/cache.py), deriving
# the log directory from its parent would quietly scatter alert logs
# into the temp directory on any machine reading the archive form.
LOG_DIR = os.path.join(REPO_ROOT, "logs")
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
