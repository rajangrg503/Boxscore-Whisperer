#!/usr/bin/env bash
# Commit and push some paths, safely, from an unattended job.
#
#     source tools/git_publish.sh
#     bw_publish "Nightly capture (2026-10-21)" projections line_records
#
# Sourced, not run. Two jobs need this and they need it to behave
# identically.
#
# WHY THIS IS NOT THREE LINES INLINE
# Two things went wrong in the first week of running any of this
# unattended, and both are the kind that leave the job looking healthy.
#
# 1. THE PUSH RACE. The refresh job pulls at the top and pushes ninety
#    minutes later. A PR merged in the browser in between -- which is
#    how this repo is normally merged -- leaves main ahead, the push is
#    rejected, and a full day's refresh sits in a local commit nobody
#    can see. That happened on its first complete run. The fix is to
#    rebase and push again, and it belongs everywhere that pushes, not
#    just in the one script where it was noticed.
#
# 2. THE OVERLAP. The refresh starts at 08:30 and runs for over an
#    hour. The line captures start at 09:00. So there is a window every
#    morning where two launchd jobs are both in this repository, and
#    two concurrent `git commit`s race on .git/index -- which surfaces
#    as "another git process seems to be running", or worse, as one
#    job's staged changes landing in the other's commit.
#
#    So every writer takes the same lock first. It is around the
#    git work only: the refresh spends its ninety minutes fetching,
#    which needs no lock, and holding one that long would mean the
#    captures simply never ran.
#
# 3. THE WRONG BRANCH. Every push below is a bare `git push`, which
#    pushes whatever happens to be checked out. On 21 Sep 2026 the
#    repository was left on a review branch after a PR. The next
#    refresh would have committed the day's data cache onto that
#    branch, pushed it there, returned 0, and left the live app --
#    which deploys from main -- serving a cache that quietly stopped
#    advancing. Nothing rejects a push to the wrong branch, so the
#    log would have read like a clean night for as long as it took
#    somebody to notice the dates.
#
#    So this refuses to publish from anywhere but main. A job that
#    does nothing and says so can be recovered from; one that does
#    the wrong thing and reports success cannot.

# Serialise every writer to this repository. mkdir is atomic on every
# filesystem this could run on, which `[ -e ] && touch` is not.
BW_LOCK_WAIT_SECONDS="${BW_LOCK_WAIT_SECONDS:-300}"

# The branch the live app deploys from. Overridable only so the tests
# can build a repository that is not called main.
BW_PUBLISH_BRANCH="${BW_PUBLISH_BRANCH:-main}"

bw_lock() {
    local lock="$1" waited=0
    while ! mkdir "$lock" 2>/dev/null; do
        # A job killed mid-commit would otherwise block every morning
        # after it, forever, and the symptom would be silence.
        if [ -d "$lock" ] && [ -n "$(find "$lock" -maxdepth 0 -mmin +30 2>/dev/null)" ]; then
            printf 'stale lock (over 30 min old) -- taking it\n'
            rm -rf "$lock" && continue
        fi
        if [ "$waited" -ge "$BW_LOCK_WAIT_SECONDS" ]; then
            return 1
        fi
        sleep 5
        waited=$((waited + 5))
    done
    printf '%s\n' "$$" > "$lock/pid" 2>/dev/null || true
    return 0
}

bw_unlock() {
    rm -rf "$1"
}

# bw_publish <message> <path>...
#
# Stages the given paths, commits if anything changed, and pushes --
# rebasing once onto whatever arrived while we were working. Returns
# 0 when there was nothing to commit, which is a normal quiet night and
# not a failure.
bw_publish() {
    local message="$1"; shift
    local repo lock branch rc=0
    repo="$(git rev-parse --show-toplevel)" || return 1
    lock="$repo/.git/bw-publish.lock"

    # Before the lock, because a refusal should not make the other job
    # wait for a lock this one is only going to hand straight back.
    # A detached HEAD reports itself as "HEAD" and is refused too: it
    # is not a branch, so a push from it goes nowhere useful either.
    branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
    if [ "$branch" != "$BW_PUBLISH_BRANCH" ]; then
        printf 'on branch %s, not %s -- refusing to publish. Nothing was committed.\n' \
            "${branch:-unknown}" "$BW_PUBLISH_BRANCH" >&2
        return 1
    fi

    if ! bw_lock "$lock"; then
        printf 'could not get the repo lock within %ss -- nothing committed\n' \
            "$BW_LOCK_WAIT_SECONDS" >&2
        return 1
    fi

    # No trap: bash only fires a RETURN trap under `set -T`, and a lock
    # released by a mechanism that silently does not fire is worse than
    # no lock at all. Every exit path below unlocks explicitly.
    _bw_publish_locked "$message" "$@"
    rc=$?
    bw_unlock "$lock"
    return $rc
}

_bw_publish_locked() {
    local message="$1"; shift
    local path existing=()

    # A path that is neither on disk nor known to git makes `git add`
    # fail outright -- "fatal: pathspec 'projections' did not match any
    # files" -- and takes the whole publish down with it. That is not
    # hypothetical: the capture job's first run died exactly there,
    # because projections/ does not exist until the first night that
    # has something to project, and the off-season has none.
    #
    # A missing path means nothing to publish from it, which is a quiet
    # night and not a failure. A DELETED path still counts, so this
    # asks git as well as the filesystem.
    for path in "$@"; do
        if [ -e "$path" ] || git ls-files --error-unmatch -- "$path" >/dev/null 2>&1; then
            existing+=("$path")
        fi
    done
    if [ ${#existing[@]} -eq 0 ]; then
        return 0
    fi
    set -- "${existing[@]}"

    git add -- "$@" || return 1

    if git diff --cached --quiet -- "$@"; then
        return 0                      # nothing changed; not an error
    fi

    git -c user.name="Boxscore Whisperer" \
        -c user.email="noreply@boxscorewhisperer.com" \
        commit -q -m "$message" -- "$@" || return 1

    if git push >/dev/null 2>&1; then
        return 0
    fi

    printf 'push rejected -- main moved; rebasing onto it\n'
    if ! git pull --rebase >/dev/null 2>&1; then
        printf 'rebase failed -- the commit is here, push it by hand\n' >&2
        return 1
    fi
    if ! git push >/dev/null 2>&1; then
        printf 'push failed after rebase -- the commit is here, push it by hand\n' >&2
        return 1
    fi
    return 0
}
