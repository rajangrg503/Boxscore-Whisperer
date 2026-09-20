#!/usr/bin/env bash
# One night's evidence: the market's lines, and our projections for the
# players who have one.
#
#     bash tools/nightly_capture.sh            # capture, commit, push
#     bash tools/nightly_capture.sh --dry-run  # fetch and report, write nothing
#
# Installed by tools/install_capture_job.sh to run several times across
# the slate. See that script for the times and why.
#
# WHAT IT DOES, AND WHY IN THIS ORDER
# 1. Capture the lines. Every run. The last one before a tip is the
#    closest thing to a closing line we can honestly record, and a line
#    that existed for ten minutes cannot be reconstructed afterwards.
# 2. Capture our projections, ONCE a night, from the first snapshot
#    that had anything in it.
#
# The projections do not change between runs -- they come from the
# cache, which the morning refresh already updated -- so capturing them
# five times would just be five identical files. What matters is that
# they are captured BEFORE the games, which the first run of the
# evening guarantees.
#
# WHY THE PROJECTION CAPTURE READS THE SNAPSHOT
# It projects the players the market has priced, not a list we keep.
# That keeps the two halves of the forward test aligned by
# construction: every leg we could score has a projection behind it,
# and we do not spend time projecting players nobody offered a bet on.
#
# THE KEY
# Read from ~/.sgo_key at run time, never written into the job or into
# this file. If it is missing this exits non-zero and says so, rather
# than making an unauthenticated request that would return nothing and
# look exactly like a night with no games.

set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$REPO_DIR" || exit 1

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

mkdir -p logs
LOG="logs/capture.log"
say() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"; }
die() { say "STOPPED: $*"; exit 1; }

PYTHON="${PYTHON:-python3}"
KEY_FILE="${SGO_KEY_FILE:-$HOME/.sgo_key}"

# shellcheck source=tools/git_publish.sh
source "$REPO_DIR/tools/git_publish.sh"

[ -f "$KEY_FILE" ] || die "no key at $KEY_FILE -- refusing to make an \
unauthenticated request, which would look like a night with no games"

SGO_API_KEY="$(cat "$KEY_FILE")"
export SGO_API_KEY
[ -n "$SGO_API_KEY" ] || die "$KEY_FILE is empty"

TODAY="$(date '+%Y-%m-%d')"
say "=== capture starting (dry-run=$DRY_RUN) ==="

# ---- the lines -------------------------------------------------------
if [ $DRY_RUN -eq 1 ]; then
    "$PYTHON" tools/capture_lines.py --dry-run 2>&1 | tee -a "$LOG"
else
    "$PYTHON" tools/capture_lines.py 2>&1 | tee -a "$LOG"
fi
LINE_STATUS=${PIPESTATUS[0]}
[ $LINE_STATUS -eq 0 ] || die "capture_lines.py exited $LINE_STATUS"

# ---- our projections, once a night -----------------------------------
# The newest snapshot written in the last six hours. Deliberately not
# "today's folder": snapshots are filed under the UTC date, this job
# runs on Australian local time, and an evening slate straddles
# midnight UTC -- so a local-date lookup would find nothing for half
# the night and the wrong night for the other half. Recency is the
# thing actually being asked about, so ask about recency.
SNAPSHOT="$(find line_snapshots -name '*.json' -mmin -360 2>/dev/null | sort | tail -1)"

# Same reason this marker is keyed to the LOCAL date rather than the
# file names: "once this evening" is a local idea. logs/ is gitignored,
# so it stays on this machine where it belongs.
PROJECTED_MARKER="logs/.projected-$TODAY"

if [ -z "$SNAPSHOT" ]; then
    say "no recent snapshot -- nothing to project"
    say "=== capture done ==="
    exit 0
fi

if [ -f "$PROJECTED_MARKER" ]; then
    say "projections already captured for $TODAY -- lines only this run"
else
    say "projecting the players in $(basename "$SNAPSHOT")"
    if [ $DRY_RUN -eq 1 ]; then
        "$PYTHON" tools/capture_projections.py --from-snapshot "$SNAPSHOT" \
            --dry-run 2>&1 | tee -a "$LOG"
    else
        "$PYTHON" tools/capture_projections.py --from-snapshot "$SNAPSHOT" \
            2>&1 | tee -a "$LOG"
        PROJ_STATUS=${PIPESTATUS[0]}
        # Marked only on success, so a failed projection run is retried
        # by the next capture rather than skipped for the evening.
        [ $PROJ_STATUS -eq 0 ] && touch "$PROJECTED_MARKER"
    fi
    PROJ_STATUS=${PROJ_STATUS:-0}
    [ $PROJ_STATUS -eq 0 ] || say "WARNING: capture_projections.py exited $PROJ_STATUS"
fi

if [ $DRY_RUN -eq 1 ]; then
    say "dry run -- nothing committed"
    exit 0
fi

# ---- publish ---------------------------------------------------------
# line_records/ is the public commitment to tonight's snapshot: when we
# asked, how many events came back, and a digest. projections/ is the
# claim itself. Neither carries a price. The raw snapshot stays out --
# .gitignore keeps line_snapshots/ local, which is the licence line.
bw_publish "Capture $TODAY" projections line_records 2>&1 | tee -a "$LOG"
PUBLISH_STATUS=${PIPESTATUS[0]}
[ $PUBLISH_STATUS -eq 0 ] || die "could not publish tonight's capture"

say "=== capture done ==="
