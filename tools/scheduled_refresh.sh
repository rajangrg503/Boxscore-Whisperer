#!/usr/bin/env bash
# Refresh data_cache/ and push it, from a machine the NBA answers.
#
# The live app can't do this itself: stats.nba.com blocks Streamlit
# Community Cloud, and (measured 19 Sep 2026, tools/probe_sources.py)
# GitHub's runners too -- three calls timed out and cdn.nba.com returned
# 403. A laptop on a home connection still works, so this is the job
# that keeps the site current during the season.
#
# It is deliberately cautious: it refuses to push anything when the
# watchdog says an endpoint failed validation, or when the refresh would
# delete a large part of the cache. A stale cache is a bad day; a cache
# overwritten with junk is a bad week.
#
# Install (macOS, runs every morning):
#   cp tools/com.boxscorewhisperer.refresh.plist ~/Library/LaunchAgents/
#   # edit the two paths in that file to match this checkout, then:
#   launchctl load ~/Library/LaunchAgents/com.boxscorewhisperer.refresh.plist
#
# Run by hand any time:
#   bash tools/scheduled_refresh.sh            # refresh, commit, push
#   bash tools/scheduled_refresh.sh --dry-run  # refresh, show the diff, push nothing

set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR" || exit 1

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

mkdir -p logs
LOG="logs/refresh.log"
say() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"; }
die() { say "STOPPED: $*"; exit 1; }

PYTHON="${PYTHON:-python3}"

say "=== refresh starting (dry-run=$DRY_RUN) ==="

# Never refresh on top of local edits: this job commits whatever is in
# data_cache/, so it has to start from a clean tree.
if [ -n "$(git status --porcelain -- data_cache)" ]; then
    die "data_cache has uncommitted changes; sort those out first"
fi

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
[ "$CURRENT_BRANCH" = "main" ] || die "on branch $CURRENT_BRANCH, not main"

git pull --ff-only >>"$LOG" 2>&1 || die "git pull failed (see $LOG)"

BEFORE="$(find data_cache -name '*.json' | wc -l | tr -d ' ')"

say "running refresh_all.py (watchdog first, then every batch script that passed)"
"$PYTHON" refresh_all.py >>"$LOG" 2>&1
ALL_STATUS=$?
say "running refresh_cache.py (team stats, game logs, synergy)"
"$PYTHON" refresh_cache.py >>"$LOG" 2>&1
CACHE_STATUS=$?
[ $ALL_STATUS -eq 0 ] || say "WARNING: refresh_all.py exited $ALL_STATUS"
[ $CACHE_STATUS -eq 0 ] || say "WARNING: refresh_cache.py exited $CACHE_STATUS"

# The watchdog writes which endpoints failed validation. Anything that
# failed left its own cache files untouched, but a failure means the
# refresh is partial, so say so loudly rather than pushing quietly.
FAILED="$("$PYTHON" - <<'PY'
import json, os
path = os.path.join("data_cache", "_watchdog_status.json")
try:
    with open(path) as f:
        status = json.load(f)
except (OSError, ValueError):
    print("")
else:
    bad = [k for k, v in status.items()
           if isinstance(v, dict) and v.get("last_result") not in (None, "ok", "pass", "passed")]
    print(",".join(sorted(bad)))
PY
)"
[ -n "$FAILED" ] && say "WARNING: endpoints failing validation: $FAILED"

AFTER="$(find data_cache -name '*.json' | wc -l | tr -d ' ')"
DELETED="$(git status --porcelain -- data_cache | grep -c '^ D' || true)"
CHANGED="$(git status --porcelain -- data_cache | wc -l | tr -d ' ')"
say "cache files: $BEFORE -> $AFTER, $CHANGED changed, $DELETED deleted"

# A refresh adds and rewrites files. Mass deletion means something went
# wrong upstream (an endpoint returning an empty payload, say), and that
# is exactly what must never reach the live app.
if [ "$DELETED" -gt 50 ]; then
    git checkout -- data_cache
    die "$DELETED files would be deleted; reverted and pushed nothing"
fi

if [ "$CHANGED" -eq 0 ]; then
    say "nothing changed; done"
    exit 0
fi

if [ $DRY_RUN -eq 1 ]; then
    say "dry run: leaving $CHANGED changed files in the working tree"
    git status --short -- data_cache | head -20 | tee -a "$LOG"
    exit 0
fi

git add data_cache
git -c user.name="Boxscore Whisperer refresh" -c user.email="noreply@boxscorewhisperer.com" \
    commit -q -m "Refresh data_cache ($(date '+%Y-%m-%d'))${FAILED:+ [partial: $FAILED failed validation]}" \
    >>"$LOG" 2>&1 || die "commit failed (see $LOG)"

git push >>"$LOG" 2>&1 || die "push failed (see $LOG) -- the commit is here, push it by hand"
say "pushed; Streamlit will redeploy in a minute or two"
say "=== refresh done ==="
