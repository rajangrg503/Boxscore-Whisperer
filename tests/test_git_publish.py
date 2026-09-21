"""Tests for tools/git_publish.sh -- the part that runs unattended.

Shell in a launchd job is where this project's worst failures have
lived: not crashes, but jobs that exit 0 having done nothing, at 08:30,
to nobody. The behaviours pinned here were all real incidents waiting
to happen -- the push race actually did happen, on the refresh job's
first complete run; the overlap is two launchd jobs in one repository
every morning from 09:00; and the wrong-branch publish was one night
away on 21 Sep 2026, with the repository left on a review branch after
a PR was merged.

These drive the real script against real git repositories, because the
bug would be in the shell, and a mock of git would have the bug too.
"""

import os
import subprocess
import textwrap

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HELPER = os.path.join(REPO_ROOT, "tools", "git_publish.sh")


def run(script, cwd, **env):
    """Run a bash snippet with the helper sourced."""
    body = f'set -uo pipefail\nsource "{HELPER}"\n{textwrap.dedent(script)}'
    environment = dict(os.environ, **env)
    return subprocess.run(["bash", "-c", body], cwd=cwd, env=environment,
                          capture_output=True, text=True)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A working repo with an upstream, as the jobs see it."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    # Both ends on main by name, not by whatever init.defaultBranch is
    # set to on the machine running this -- bw_publish now refuses to
    # publish from anywhere else, and that refusal is the point.
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"],
                   cwd=remote, check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(remote), str(work)], check=True)
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"],
                   cwd=work, check=True)
    for key, value in (("user.name", "Test"), ("user.email", "t@example.com"),
                       ("commit.gpgsign", "false")):
        subprocess.run(["git", "config", key, value], cwd=work, check=True)
    (work / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "add", "seed.txt"], cwd=work, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=work, check=True)
    subprocess.run(["git", "push", "-q", "-u", "origin", "HEAD"], cwd=work, check=True)
    (work / "results").mkdir()
    return work


def test_it_commits_and_pushes_what_changed(repo):
    (repo / "results" / "a.json").write_text("{}")
    result = run('bw_publish "Score today" results', repo)
    assert result.returncode == 0, result.stderr
    assert git(repo, "log", "-1", "--format=%s") == "Score today"
    assert git(repo, "rev-parse", "HEAD") == git(repo, "rev-parse", "@{upstream}")


def test_a_quiet_night_is_success_not_failure(repo):
    """Out of season nothing is captured. Nine months of a job
    reporting failure trains everybody to ignore it."""
    result = run('bw_publish "Nothing" results', repo)
    assert result.returncode == 0, result.stderr
    assert git(repo, "log", "-1", "--format=%s") == "seed"


def test_it_only_commits_the_paths_it_was_given(repo):
    """The refresh job publishes data_cache.zip while a capture may
    have left projections half-written. One job's work must not ride
    along in the other's commit."""
    (repo / "results" / "a.json").write_text("{}")
    (repo / "unrelated.txt").write_text("do not commit me\n")
    assert run('bw_publish "Score today" results', repo).returncode == 0
    files = git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert files == ["results/a.json"]


def test_it_rebases_and_retries_when_main_moved(repo, tmp_path):
    """The real incident: the refresh pulls at 08:30, pushes at 10:00,
    and a PR merged in the browser in between leaves the whole night's
    work in a local commit nobody can see."""
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(tmp_path / "remote.git"), str(other)],
                   check=True)
    for key, value in (("user.name", "Other"), ("user.email", "o@example.com")):
        subprocess.run(["git", "config", key, value], cwd=other, check=True)
    (other / "merged-pr.txt").write_text("from the browser\n")
    subprocess.run(["git", "add", "-A"], cwd=other, check=True)
    subprocess.run(["git", "commit", "-qm", "a PR merged meanwhile"], cwd=other, check=True)
    subprocess.run(["git", "push", "-q"], cwd=other, check=True)

    (repo / "results" / "a.json").write_text("{}")
    result = run('bw_publish "Score today" results', repo)

    assert result.returncode == 0, result.stderr + result.stdout
    assert "rebasing" in result.stdout
    assert git(repo, "rev-parse", "HEAD") == git(repo, "rev-parse", "@{upstream}")
    # Both pieces of work survived; neither clobbered the other.
    assert (repo / "merged-pr.txt").exists()
    assert (repo / "results" / "a.json").exists()


def test_two_jobs_at_once_do_not_race_on_the_index(repo):
    """From 09:00 every morning the capture job and the still-running
    refresh are both in this repository. Two concurrent commits race on
    .git/index, which surfaces as a lock error or, worse, as one job's
    staged changes landing in the other's commit."""
    (repo / "results" / "a.json").write_text("{}")
    (repo / "results" / "b.json").write_text("{}")
    result = run('''
        bw_publish "First" results/a.json &
        bw_publish "Second" results/b.json &
        wait
    ''', repo)
    assert result.returncode == 0, result.stderr + result.stdout
    subjects = git(repo, "log", "--format=%s", "-3").split("\n")
    assert "First" in subjects and "Second" in subjects
    assert git(repo, "status", "--porcelain") == ""


def test_a_lock_left_by_a_killed_job_does_not_block_forever(repo):
    """Otherwise one job killed mid-commit means silence every morning
    after it, which is this project's signature failure."""
    lock = repo / ".git" / "bw-publish.lock"
    lock.mkdir()
    old = 1_600_000_000            # well over 30 minutes ago
    os.utime(lock, (old, old))

    (repo / "results" / "a.json").write_text("{}")
    result = run('bw_publish "Score today" results',
                 repo, BW_LOCK_WAIT_SECONDS="15")
    assert result.returncode == 0, result.stderr + result.stdout
    assert "stale lock" in result.stdout
    assert git(repo, "log", "-1", "--format=%s") == "Score today"


def test_a_fresh_lock_is_waited_on_not_stolen(repo):
    """The opposite failure: taking a lock another job is using is how
    two commits race in the first place."""
    lock = repo / ".git" / "bw-publish.lock"
    lock.mkdir()
    (repo / "results" / "a.json").write_text("{}")
    result = run('bw_publish "Score today" results',
                 repo, BW_LOCK_WAIT_SECONDS="5")
    assert result.returncode == 1
    assert "could not get the repo lock" in result.stderr
    assert git(repo, "log", "-1", "--format=%s") == "seed"


def test_a_path_that_does_not_exist_yet_is_not_a_failure(repo):
    """The capture job's first real run died here: projections/ does
    not exist until the first night with something to project, and
    `git add` on a missing pathspec is fatal -- so the whole publish
    went down, including the line_records/ entry next to it that DID
    have something in it."""
    (repo / "results" / "a.json").write_text("{}")
    result = run('bw_publish "Capture today" projections results', repo)
    assert result.returncode == 0, result.stderr + result.stdout
    assert git(repo, "log", "-1", "--format=%s") == "Capture today"
    assert git(repo, "show", "--name-only", "--format=", "HEAD").split() \
        == ["results/a.json"]


def test_nothing_existing_at_all_is_a_quiet_night(repo):
    result = run('bw_publish "Capture today" projections line_records', repo)
    assert result.returncode == 0, result.stderr + result.stdout
    assert git(repo, "log", "-1", "--format=%s") == "seed"


def test_a_deleted_file_still_publishes(repo):
    """Gone from disk but known to git is a real change, not a missing
    path -- the filesystem check alone would silently skip it."""
    (repo / "results" / "a.json").write_text("{}")
    assert run('bw_publish "Add" results', repo).returncode == 0
    (repo / "results" / "a.json").unlink()
    result = run('bw_publish "Remove" results', repo)
    assert result.returncode == 0, result.stderr + result.stdout
    assert git(repo, "log", "-1", "--format=%s") == "Remove"


def test_it_refuses_to_publish_from_the_wrong_branch(repo):
    """21 Sep 2026: the repository was left on a review branch after a
    PR. Every push in the helper is a bare `git push`, so the next
    refresh would have committed the night's data cache to that
    branch, pushed it, returned 0, and left the live app -- which
    deploys from main -- serving a cache that quietly stopped
    advancing. Nothing rejects a push to the wrong branch."""
    subprocess.run(["git", "checkout", "-qb", "claude/some-review"],
                   cwd=repo, check=True)
    (repo / "results" / "a.json").write_text("{}")
    result = run('bw_publish "Score today" results', repo)

    assert result.returncode == 1
    # The branch it found, by name: "wrong branch" alone sends whoever
    # reads launchd.err looking for which one.
    assert "claude/some-review" in result.stderr
    assert "main" in result.stderr
    assert git(repo, "log", "-1", "--format=%s") == "seed"
    # Nothing staged either -- a refusal that leaves the index dirty
    # hands the next job a commit it did not make.
    assert git(repo, "diff", "--cached", "--name-only") == ""


def test_a_detached_head_is_refused_too(repo):
    """Not a branch, so a push from it goes nowhere useful. git spells
    it "HEAD", which is not main, so the same check catches it."""
    subprocess.run(["git", "checkout", "-q", "--detach"], cwd=repo, check=True)
    (repo / "results" / "a.json").write_text("{}")
    result = run('bw_publish "Score today" results', repo)

    assert result.returncode == 1
    assert "refusing to publish" in result.stderr
    assert git(repo, "log", "-1", "--format=%s") == "seed"


def test_the_wrong_branch_is_caught_before_the_lock(repo):
    """Two things at once: a job that is going to refuse should not
    spend the lock wait first, and it must not report the other job's
    lock as the reason. The whole value of this check is that the log
    says what actually happened."""
    (repo / ".git" / "bw-publish.lock").mkdir()
    subprocess.run(["git", "checkout", "-qb", "claude/some-review"],
                   cwd=repo, check=True)
    (repo / "results" / "a.json").write_text("{}")
    result = run('bw_publish "Score today" results',
                 repo, BW_LOCK_WAIT_SECONDS="5")

    assert result.returncode == 1
    assert "not main" in result.stderr
    assert "could not get the repo lock" not in result.stderr


def test_main_still_publishes(repo):
    """The guard's cost, pinned: the normal night must be untouched."""
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    (repo / "results" / "a.json").write_text("{}")
    result = run('bw_publish "Score today" results', repo)
    assert result.returncode == 0, result.stderr
    assert git(repo, "rev-parse", "HEAD") == git(repo, "rev-parse", "@{upstream}")


def test_the_branch_name_is_not_hardcoded_past_the_setting(repo):
    """The tests override it; nothing else should need to. If this
    fails, the check grew a second copy of the name somewhere."""
    subprocess.run(["git", "checkout", "-qb", "deploy"], cwd=repo, check=True)
    # A real deploy branch, with an upstream. Without one the push
    # fails for a reason that has nothing to do with this check, and
    # the test passes or fails on the wrong thing.
    subprocess.run(["git", "push", "-q", "-u", "origin", "deploy"],
                   cwd=repo, check=True)
    (repo / "results" / "a.json").write_text("{}")
    result = run('bw_publish "Score today" results', repo,
                 BW_PUBLISH_BRANCH="deploy")
    assert result.returncode == 0, result.stderr + result.stdout
    assert git(repo, "log", "-1", "--format=%s") == "Score today"
