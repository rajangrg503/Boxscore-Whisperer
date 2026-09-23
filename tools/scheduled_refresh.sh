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
# WHAT IT COMMITS
# data_cache/ itself is no longer tracked -- 148,684 loose files made
# Streamlit Cloud's checkout unreliable (engine/cache_archive.py has the
# reasoning). The refresh writes that folder exactly as before, then
# packs it into data_cache.zip and commits the archive. So the safety
# checks below can no longer ask git what changed: they ask
# tools/pack_cache.py, which compares the folder against the archive
# file by file and reports added / changed / removed separately.
#
# Install (macOS, runs every morning at 08:30):
#   bash tools/install_refresh_job.sh
#
# That script writes and loads the launchd plist using this checkout's
# real path, and refuses to install if the repo sits in ~/Documents,
# ~/Desktop or ~/Downloads -- macOS protects those, launchd's bash
# cannot read them, and the job then fails with exit 126 every morning
# without running a line. That is not hypothetical: it is what happened
# here for three days in September 2026.
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

# shellcheck source=tools/git_publish.sh
source "$REPO_DIR/tools/git_publish.sh"

say "=== refresh starting (dry-run=$DRY_RUN) ==="

# The swap to the archive is a one-time commit that has to be made by
# hand (it can only be built on a machine that has the folder, and it
# has to land in the same commit that untracks it). Until that has
# happened, committing an archive here would add 69 MB beside a cache
# that is still tracked file by file -- the worst of both.
if [ "$(git ls-files data_cache | head -1)" != "" ]; then
    die "data_cache is still tracked file by file; run tools/adopt_cache_archive.sh once first"
fi

# Never refresh on top of local edits: this job commits the archive it
# builds, so it has to start from a clean tree.
if [ -n "$(git status --porcelain -- data_cache.zip)" ]; then
    die "data_cache.zip has uncommitted changes; sort those out first"
fi

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
[ "$CURRENT_BRANCH" = "main" ] || die "on branch $CURRENT_BRANCH, not main"

git pull --ff-only >>"$LOG" 2>&1 || die "git pull failed (see $LOG)"

# A clone has the archive and no folder. Unpacking is the only way
# back, and refreshing on top of an empty folder would look like every
# file in the cache had just been deleted. After the pull, so a clone
# unpacks the archive it is about to be compared against.
if [ -z "$(find data_cache -name '*.json' -print -quit 2>/dev/null)" ]; then
    say "no data_cache/ here -- unpacking the archive first"
    "$PYTHON" tools/pack_cache.py --unpack >>"$LOG" 2>&1 \
        || die "could not unpack data_cache.zip (see $LOG)"
fi

BEFORE="$(find data_cache -name '*.json' | wc -l | tr -d ' ')"

say "running refresh_all.py (watchdog first, then every batch script that passed)"
"$PYTHON" refresh_all.py >>"$LOG" 2>&1
ALL_STATUS=$?
say "running refresh_cache.py (team stats, game logs, synergy)"
"$PYTHON" refresh_cache.py >>"$LOG" 2>&1
CACHE_STATUS=$?
# A non-zero exit from either fetcher means part of data_cache/ was not
# refreshed. That is still worth pushing -- what did fetch is real, and
# the alternative is the whole site going a day stale over one endpoint
# -- but the run must not then sign off as if it were clean. PARTIAL
# carries that to the last line and to the exit code.
PARTIAL=""
if [ $ALL_STATUS -ne 0 ]; then
    PARTIAL="refresh_all exited $ALL_STATUS"
    say "WARNING: refresh_all.py exited $ALL_STATUS -- at least one batch script did not complete"
fi
if [ $CACHE_STATUS -ne 0 ]; then
    PARTIAL="${PARTIAL:+$PARTIAL; }refresh_cache exited $CACHE_STATUS"
    say "WARNING: refresh_cache.py exited $CACHE_STATUS"
fi

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
    # data_watchdog/runner.check() writes "passed" (a bool), never
    # "last_result" -- reading the latter made this list permanently
    # empty, so the WARNING below and the [partial: ...] commit tag
    # could never fire. Absent/None still means "never checked", which
    # is not a failure.
    bad = [k for k, v in status.items()
           if isinstance(v, dict) and v.get("passed") is False]
    print(",".join(sorted(bad)))
PY
)"
[ -n "$FAILED" ] && say "WARNING: endpoints failing validation: $FAILED"

AFTER="$(find data_cache -name '*.json' | wc -l | tr -d ' ')"

# What the folder now holds versus what the committed archive holds.
# git can't answer this any more, so pack_cache.py does, and it reports
# rewrites and deletions separately -- which is the distinction the
# check below turns on.
DIFF="$("$PYTHON" tools/pack_cache.py --diff 2>>"$LOG" | head -1)"
ADDED="$(printf '%s' "$DIFF" | sed -n 's/.*added \([0-9]*\).*/\1/p')"
CHANGED="$(printf '%s' "$DIFF" | sed -n 's/.*changed \([0-9]*\).*/\1/p')"
DELETED="$(printf '%s' "$DIFF" | sed -n 's/.*removed \([0-9]*\).*/\1/p')"
: "${ADDED:=0}" "${CHANGED:=0}" "${DELETED:=0}"
say "cache files: $BEFORE -> $AFTER, $ADDED added, $CHANGED changed, $DELETED deleted"

# A refresh adds and rewrites files. Mass deletion means something went
# wrong upstream (an endpoint returning an empty payload, say), and that
# is exactly what must never reach the live app. Nothing is committed
# yet at this point, so stopping here leaves the archive untouched --
# the live app keeps reading yesterday's, which is the safe side.
if [ "$DELETED" -gt 50 ]; then
    die "$DELETED files are missing from data_cache/; packed and pushed nothing"
fi

if [ "$ADDED" -eq 0 ] && [ "$CHANGED" -eq 0 ] && [ "$DELETED" -eq 0 ]; then
    # "Nothing changed" means two completely different things depending
    # on whether anything was actually fetched. On a quiet day it is the
    # correct, healthy answer. After a fetcher crashed it means the
    # cache was never touched -- and saying "nothing changed; done" then
    # is the same quiet lie as a stale cache reporting itself fresh.
    #
    # Seen for real on the first launchd run after the TCC fix: both
    # fetchers died on ModuleNotFoundError, and the run still signed off
    # with "nothing changed; done" and exit 0. A week of mornings like
    # that reads as healthy in the log and in launchctl list.
    if [ $ALL_STATUS -ne 0 ] || [ $CACHE_STATUS -ne 0 ]; then
        die "nothing changed because nothing was fetched -- refresh_all exited \
$ALL_STATUS, refresh_cache exited $CACHE_STATUS. The cache was NOT refreshed. \
Exiting non-zero so 'launchctl list | grep boxscore' shows it."
    fi
    say "nothing changed; done"
    exit 0
fi

if [ $DRY_RUN -eq 1 ]; then
    say "dry run: $ADDED added, $CHANGED changed, $DELETED deleted -- archive left alone"
    "$PYTHON" tools/pack_cache.py --diff | head -12 | tee -a "$LOG"
    exit 0
fi

say "packing data_cache/ into data_cache.zip"
"$PYTHON" tools/pack_cache.py >>"$LOG" 2>&1 || die "pack failed (see $LOG)"

# The commit-and-push lives in tools/git_publish.sh now, shared with
# the nightly capture job. Two reasons, both learned the hard way:
# the push race this used to handle alone (main moves while a
# ninety-minute refresh runs), and the overlap -- the capture job
# starts at 09:00, while this is still going, and two git commits in
# one repository race on the index. bw_publish serialises them.
bw_publish "Refresh data_cache ($(date '+%Y-%m-%d'))${FAILED:+ [partial: $FAILED failed validation]}" \
    data_cache.zip >>"$LOG" 2>&1 \
    || die "could not publish the refreshed cache (see $LOG)"
say "pushed; Streamlit will redeploy in a minute or two"

# ---- settle up -------------------------------------------------------
# Scoring belongs here rather than in its own job, and the reason is
# ordering rather than tidiness. A night can only be scored once the
# cache has the box scores, and the moment the cache has them is the
# moment this script finishes fetching. A separate job at a fixed time
# would either run before this one finished -- recording every player
# as void, permanently -- or sit idle waiting for it.
say "scoring any night that has settled"
"$PYTHON" tools/score_forward_test.py --catch-up >>"$LOG" 2>&1
SCORE_STATUS=$?
if [ $SCORE_STATUS -ne 0 ]; then
    # Never fatal. A scoring bug must not stop tomorrow's cache
    # refresh; the projections and the snapshots are already on disk
    # and can be scored again once it is fixed.
    say "WARNING: scoring exited $SCORE_STATUS (see $LOG) -- the cache is fine"
else
    bw_publish "Score $(date '+%Y-%m-%d')" results >>"$LOG" 2>&1 \
        || say "WARNING: could not publish results (see $LOG)"

    # The morning post, written into the log for copying out. No --date:
    # the tool picks the newest committed card that has a result, so
    # this job never has to work out what "yesterday" means from
    # Australia -- which is the same timezone trap that would have made
    # every card unsettleable.
    #
    # Printed, never posted. Posting is a human action and stays one.
    say "last night's card:"
    "$PYTHON" tools/nightly_slip.py --settle >>"$LOG" 2>&1 \
        || say "nothing to settle yet"
fi

if [ -n "$PARTIAL" ]; then
    # Deliberately not "=== refresh done ===" and deliberately non-zero:
    # that line is what a morning glance greps for, and
    # "launchctl list | grep boxscore" shows the status. A partial
    # refresh that reports itself as done is the same quiet lie as a
    # stale cache reporting itself fresh.
    say "=== refresh done, PARTIAL ($PARTIAL) ==="
    say "Part of data_cache/ is still yesterday's; what did fetch has been pushed. \
Re-run tools/scheduled_refresh.sh to retry the rest."
    exit 1
fi

say "=== refresh done ==="
