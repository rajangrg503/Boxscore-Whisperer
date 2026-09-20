#!/usr/bin/env bash
# Install the nightly line-and-projection capture as a launchd job.
# Run it once:
#
#     bash tools/install_capture_job.sh
#
# It writes the plist using this checkout's real path and loads it.
# Nothing to hand-edit.
#
# WHEN IT RUNS, AND WHY THOSE TIMES
# NBA games tip between about 10:00 and 14:00 Australian eastern time,
# which is the one genuinely convenient thing about following this
# league from here: the slate lands in the middle of a waking day.
#
# Five runs across it. The reason for more than one is that the last
# line before a tip is the only one worth calling a close, and there is
# no way to know in advance which run will be the last before a given
# game -- so the job samples across the window and the scorer uses
# whichever snapshot was nearest.
#
# The cost is the constraint. The free tier bills one object per event
# and allows 2,500 a month; a typical slate is about 7.5 games, so five
# runs a night is roughly 1,125 a month. Adding runs is not free and
# the allowance is not per-night -- an afternoon of enthusiasm in
# November is a week of missing evidence in March.
#
# 09:00 is deliberately before the first tip: that run is the one that
# also captures our projections, and a projection recorded after a game
# has started is not evidence of anything.
#
# WHY NOT A TIMER EVERY 30 MINUTES
# Because nothing is being watched. A capture is worth taking when the
# lines are moving toward a tip, and worth nothing at 04:00. Fixed
# times spend the allowance where the evidence is.

set -uo pipefail

LABEL="com.boxscorewhisperer.capture"
# Hour:Minute, local time. Keep in step with the quota note above.
TIMES=("9 0" "10 30" "11 30" "12 30" "13 30")

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
KEY_FILE="${SGO_KEY_FILE:-$HOME/.sgo_key}"

say() { printf '  %s\n' "$*"; }
die() { printf '\nSTOPPED: %s\n' "$*" >&2; exit 1; }

echo
say "repo:  $REPO_DIR"
say "plist: $PLIST"
echo

# ---- the same TCC check the refresh installer makes ------------------
# Matched on the path, not by trying a read: the shell running this has
# your terminal's permissions and would pass. The process that cannot
# read the folder is the one launchd spawns, which is not this one.
case "$REPO_DIR/" in
    "$HOME/Documents/"*|"$HOME/Desktop/"*|"$HOME/Downloads/"*)
        protected="${REPO_DIR#$HOME/}"
        protected="${protected%%/*}"
        die "this checkout is inside ~/$protected, which macOS protects.

  A launchd job cannot read it: the job would install, report exit code
  126, and never run a line. Move the repo somewhere unprotected first
  (see tools/install_refresh_job.sh, which explains the whole story)."
        ;;
esac
say "not in a macOS-protected folder -- launchd will be able to read it"

[ -f "$REPO_DIR/tools/nightly_capture.sh" ] || die "tools/nightly_capture.sh is missing"

# ---- the key ---------------------------------------------------------
# Checked here so a missing key is a message now rather than a silent
# non-capture every evening. Never read into the plist: the job reads
# the file itself at run time.
[ -f "$KEY_FILE" ] || die "no odds API key at $KEY_FILE.

  Put it there once, without it passing through your shell history:

      cat > $KEY_FILE
      <paste the key, Enter, then Ctrl-D>
      chmod 600 $KEY_FILE

  The job reads that file at run time. The key is never written into
  the plist, into the repo, or into a command line."
[ -s "$KEY_FILE" ] || die "$KEY_FILE is empty"
say "key: $KEY_FILE (present)"

KEY_PERMS="$(stat -f '%Lp' "$KEY_FILE" 2>/dev/null || stat -c '%a' "$KEY_FILE" 2>/dev/null || echo "")"
case "$KEY_PERMS" in
    600|400) ;;
    "") say "could not check the key's permissions" ;;
    *) say "NOTE: $KEY_FILE is mode $KEY_PERMS -- chmod 600 it" ;;
esac

# ---- which python ----------------------------------------------------
# launchd hands a job PATH=/usr/bin:/bin:/usr/sbin:/sbin and nothing
# else, so `python3` there is macOS's own, with no third-party
# packages. The refresh job died on exactly that. Resolve it here,
# where this script runs in your shell, and write it into the plist.
PYTHON_BIN="$(command -v python3 2>/dev/null || true)"
[ -n "$PYTHON_BIN" ] || die "no python3 on PATH"
PYTHON_DIR="$(cd "$(dirname "$PYTHON_BIN")" && pwd -P)"
PYTHON_BIN="$PYTHON_DIR/$(basename "$PYTHON_BIN")"

# streamlit is in this list for an unobvious reason: engine/cache.py
# imports it at module scope, and tools/capture_projections.py reads
# the cache through that module. So a batch job with no UI anywhere in
# it still cannot start without streamlit installed. Better to say so
# now than at 09:00 to nobody.
MISSING=""
for mod in pandas nba_api certifi streamlit; do
    "$PYTHON_BIN" -c "import $mod" >/dev/null 2>&1 || MISSING="$MISSING $mod"
done
if [ -n "$MISSING" ]; then
    die "$PYTHON_BIN cannot import:$MISSING

      $PYTHON_BIN -m pip install$MISSING

  Checked now rather than at 09:00, because a job that fails then fails
  to nobody."
fi
say "python: $PYTHON_BIN (pandas, nba_api, certifi, streamlit all import)"

mkdir -p "$HOME/Library/LaunchAgents" "$REPO_DIR/logs" || die "could not create directories"

INTERVALS=""
for entry in "${TIMES[@]}"; do
    set -- $entry
    INTERVALS="$INTERVALS
        <dict>
            <key>Hour</key><integer>$1</integer>
            <key>Minute</key><integer>$2</integer>
        </dict>"
done

cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!--
  Generated by tools/install_capture_job.sh -- do not hand-edit.
  Re-run that script after moving or renaming the checkout.
-->
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$REPO_DIR/tools/nightly_capture.sh</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$REPO_DIR</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHON</key>
        <string>$PYTHON_BIN</string>
        <key>PATH</key>
        <string>$PYTHON_DIR:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>

    <!-- Several times across the slate. See the header of the
         installer for why these times and not more of them. -->
    <key>StartCalendarInterval</key>
    <array>$INTERVALS
    </array>

    <key>RunAtLoad</key>
    <false/>

    <key>StandardOutPath</key>
    <string>$REPO_DIR/logs/capture-launchd.out</string>
    <key>StandardErrorPath</key>
    <string>$REPO_DIR/logs/capture-launchd.err</string>
</dict>
</plist>
PLISTEOF

say "wrote the plist"

launchctl unload "$PLIST" >/dev/null 2>&1   # ignore "not loaded"
launchctl load "$PLIST" 2>/dev/null || die "launchctl load failed"
say "loaded"

echo
STATUS="$(launchctl list | grep "$LABEL" || true)"
[ -n "$STATUS" ] || die "the job is not in launchctl list -- it did not load"
say "launchctl says: $STATUS"

echo
printf '  It will run at'
for entry in "${TIMES[@]}"; do
    set -- $entry
    printf ' %02d:%02d' "$1" "$2"
done
printf ' daily, local time.\n'
echo
say "To prove it works without waiting:"
say "    launchctl kickstart -k gui/\$UID/$LABEL"
say "    tail -f $REPO_DIR/logs/capture.log"
echo
say "Out of season it will report 'no events with odds' and write"
say "nothing, which is the correct answer and costs one object."
echo
say "If an evening passes with nothing new in logs/capture.log,"
say "check logs/capture-launchd.err."
echo
