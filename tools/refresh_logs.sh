#!/usr/bin/env bash
# Pull last night's player game logs into data_cache/ -- local only.
#
# Run by the launchd job tools/install_logs_job.sh installs, twice a day:
# after the US slate finishes (17:45 Sydney) and again before the next
# one tips (07:15), as a catch-up when the Mac slept through the first.
#
# It refetches only players whose team played in the last two US days
# and whose cached log is missing that game (tools/refresh_current_season.py
# --recent 2): a few minutes, not the hour-long full refresh that timed out
# half-way on 1 Nov 2026 and left the Journal waiting on legs that had
# already landed.
#
# It does NOT commit or push. data_cache.zip is 70 MB, and pushing it
# twice a day would bloat the repository; the daily publish job
# (tools/scheduled_refresh.sh) still packs and pushes what this fetched.
# This exists so the Journal, the Tonight tab and the slip checks on this
# Mac are current the same evening.

set -uo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR" || exit 1
mkdir -p logs
LOG="logs/logs_refresh.log"
PYTHON="${PYTHON:-python3}"
say() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"; }

say "=== recent game logs: starting ==="
"$PYTHON" -u tools/refresh_current_season.py --recent 2 >>"$LOG" 2>&1
STATUS=$?
if [ $STATUS -ne 0 ]; then
    say "=== recent game logs: finished with failures (exit $STATUS) -- the next run retries ==="
else
    say "=== recent game logs: done ==="
fi
exit $STATUS
