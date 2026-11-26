#!/usr/bin/env bash
# Put tonight's parlay builder on the phone.
#
#     bash tools/publish_builder.sh
#
# 1. Saves Sportsbet's current player lines (journal/lines/, as the Journal's button does), reads ESPN's
#    injury list, refits the margins and logs the builder's suggestions for the coming nights.
# 2. Writes data/parlay_feed.json.gz: the priced legs for the coming nights, each player's record at the
#    line, and the measured margins.
# 3. Commits that one file and pushes, so the public app's unlisted /Parlay_builder page shows it a
#    minute or two later.
#
# Run it on the Mac (Sportsbet only answers a home connection). karma chose on 26 Nov 2026 to publish
# these lines so the builder works away from the Mac; journal/ itself, including his bets, stays local.

set -uo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$REPO_DIR" || exit 1
# shellcheck source=tools/git_publish.sh
source "$REPO_DIR/tools/git_publish.sh"

python3 tools/line_snapshot.py 2>/dev/null || echo "Couldn't read Sportsbet just now: publishing the lines already saved."
python3 tools/parlay_builder.py injuries 2>/dev/null      # who is out / day-to-day, from ESPN
python3 tools/parlay_builder.py calibrate 2>/dev/null | head -1   # refit the margins with any newly finished nights
python3 tools/parlay_builder.py log 2>/dev/null           # write down tonight's suggestions, to grade later
python3 tools/parlay_builder.py publish 2>/dev/null || { echo "STOPPED: could not write the feed."; exit 1; }
bw_publish "Parlay builder feed ($(date '+%Y-%m-%d %H:%M'))" data/parlay_feed.json.gz
rc=$?
if [ $rc -eq 0 ]; then
    echo "Published. Open <your app address>/Parlay_builder on the phone in a minute or two."
else
    echo "The feed was written but not pushed (see above)."
fi
exit $rc
