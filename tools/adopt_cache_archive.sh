#!/usr/bin/env bash
# One-time swap: stop tracking 148,684 cache files, start tracking one
# archive. Run this once, on the machine that has data_cache/, after
# the code that reads the archive is already on main.
#
#     bash tools/adopt_cache_archive.sh
#
# WHY IT IS A SCRIPT AND NOT A COMMIT
# The archive is built from YOUR data_cache/, so it can only be made on
# the machine that has one. And it has to land in the SAME commit that
# untracks the folder -- a commit that removed the files without adding
# the archive would deploy an app with no cache at all, and the NBA
# does not answer Streamlit Cloud.
#
# WHAT IT DOES, IN ORDER
#   1. checks you are on main with a clean tree
#   2. builds data_cache.zip from data_cache/        (~10 seconds)
#   3. removes the 148,684 files from the index      (~1-5 MINUTES,
#      and it looks frozen the whole time; it is not)
#   4. commits both halves as one change, and stops
#
# It does NOT push. Read the commit, then push it yourself.
#
# Nothing in data_cache/ is deleted from disk at any point: git rm
# --cached only touches the index. After this, that folder is local
# working state, and tools/pack_cache.py is what turns it into the
# thing the deployed app reads.

set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR" || exit 1

PYTHON="${PYTHON:-python3}"
say() { printf '  %s\n' "$*"; }
die() { printf 'STOPPED: %s\n' "$*" >&2; exit 1; }

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
[ "$BRANCH" = "main" ] || die "on branch $BRANCH, not main"

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    die "you have uncommitted changes; commit or set them aside first"
fi

[ -f tools/pack_cache.py ] || die "tools/pack_cache.py is missing -- pull main first"

COUNT="$(git ls-files data_cache | wc -l | tr -d ' ')"
if [ "$COUNT" -eq 0 ]; then
    say "data_cache is already out of the index -- nothing to do"
    exit 0
fi

say "tracked files right now: $(git ls-files | wc -l | tr -d ' ') ($COUNT of them cache)"

say "building data_cache.zip ..."
"$PYTHON" tools/pack_cache.py || die "pack failed"
[ -f data_cache.zip ] || die "pack produced no archive"

say "removing the cache from the index -- this takes a few minutes and prints nothing"
git rm -r -q --cached data_cache >/dev/null || die "git rm --cached failed"

git add .gitignore data_cache.zip
git commit -q -m "Track the data cache as one archive, not 148,684 files

Streamlit Cloud re-clones this repo on every reboot, and at 148,942
tracked files the checkout stopped being atomic in practice: twice it
finished with a new app.py beside a stale engine/, which took the site
down once and printed an explanation of a model that hadn't run the
other time.

data_cache/ is now local working state and data_cache.zip is what
travels. The app reads entries straight out of it -- see
engine/cache_archive.py for the measurements behind that choice." \
    || die "commit failed"

say ""
say "done. tracked files now: $(git ls-files | wc -l | tr -d ' ')"
say "nothing has been pushed. Look it over, then: git push"
