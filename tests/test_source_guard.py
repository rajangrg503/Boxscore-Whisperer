"""Tests for engine/source_guard.py -- the deploy-skew guard.

The bug it exists for: Streamlit Cloud pulls new code and re-runs
app.py without restarting Python, so modules already in sys.modules
keep the code they had at boot. On 20 Sep 2026 the live app ran an
app.py from the evening against an engine/confidence.py from 22 hours
earlier, and threw a TypeError from two files that were never
inconsistent in git.

The TypeError was the lucky case. A change that keeps its signature
makes no noise and serves stale numbers, which is why this guard
watches the FILES rather than waiting for a call to fail.

What is pinned here:
  * a module whose source changed underneath it is reported
  * one seen for the first time is recorded, not reported
  * the guard never raises, whatever the filesystem does
  * app.py is not watched -- it is the thing others fall behind
"""

import sys
import types

import pytest

from engine import source_guard


@pytest.fixture(autouse=True)
def clean_snapshot(monkeypatch):
    """A fresh snapshot per test. The real one is process-global on
    purpose -- it has to outlive a rerun -- so it must be isolated
    here or the tests leak into each other."""
    monkeypatch.setattr(source_guard, "_SNAPSHOT", {})


def fake_module(tmp_path, name, text="X = 1\n", root="engine"):
    """A module of ours, on disk and in sys.modules, as the real thing
    looks to the guard."""
    path = tmp_path / f"{name}.py"
    path.write_text(text)
    full = f"{root}.{name}"
    module = types.ModuleType(full)
    module.__file__ = str(path)
    sys.modules[full] = module
    return full, path


@pytest.fixture
def cleanup_modules():
    added = []
    yield added
    for name in added:
        sys.modules.pop(name, None)


# ---- the skew it exists to catch -----------------------------------------
def test_a_module_whose_source_changed_underneath_it_is_reported(
        tmp_path, cleanup_modules):
    name, path = fake_module(tmp_path, "widget")
    cleanup_modules.append(name)

    assert source_guard.check() == [], "flagged on the first look"

    # what a git pull does: the file moves on, the module does not
    path.write_text("X = 2\n")
    assert source_guard.check() == [name]


def test_it_keeps_reporting_until_the_process_restarts(
        tmp_path, cleanup_modules):
    """A rerun does not reload the module, so the guard must not
    quietly forgive the skew on the second look -- that would hand the
    reader stale numbers one refresh later."""
    name, path = fake_module(tmp_path, "sticky")
    cleanup_modules.append(name)
    source_guard.check()
    path.write_text("X = 99\n")

    assert source_guard.check() == [name]
    assert source_guard.check() == [name]
    assert source_guard.check() == [name]


def test_an_unchanged_module_is_never_reported(tmp_path, cleanup_modules):
    name, path = fake_module(tmp_path, "steady")
    cleanup_modules.append(name)
    for _ in range(5):
        assert source_guard.check() == []
    # rewriting identical bytes is not a change
    path.write_text("X = 1\n")
    assert source_guard.check() == []


def test_the_content_decides_not_the_timestamp(tmp_path, cleanup_modules):
    """A pull rewrites mtimes on files it did not change. Watching
    timestamps would take the app down on every deploy."""
    name, path = fake_module(tmp_path, "touched")
    cleanup_modules.append(name)
    source_guard.check()
    import os
    os.utime(path, (0, 0))
    assert source_guard.check() == []


# ---- what it must not do -------------------------------------------------
def test_a_module_first_seen_later_is_recorded_not_reported(
        tmp_path, cleanup_modules):
    """Imports can happen lazily, including after a pull. Such a module
    was loaded from whatever is on disk now, so it is current by
    definition -- reporting it would be a false alarm."""
    first, _ = fake_module(tmp_path, "early")
    cleanup_modules.append(first)
    source_guard.check()

    late, _ = fake_module(tmp_path, "late")
    cleanup_modules.append(late)
    assert source_guard.check() == []


def test_a_vanished_file_is_skipped_rather_than_called_skew(
        tmp_path, cleanup_modules):
    """A guard that can take the app down by itself is worse than the
    problem it guards against."""
    name, path = fake_module(tmp_path, "gone")
    cleanup_modules.append(name)
    source_guard.check()
    path.unlink()
    assert source_guard.check() == []


def test_third_party_modules_are_left_alone(tmp_path, cleanup_modules):
    name, path = fake_module(tmp_path, "vendor", root="somelibrary")
    cleanup_modules.append(name)
    source_guard.check()
    path.write_text("X = 2\n")
    assert source_guard.check() == []


def test_a_module_with_no_file_is_ignored(cleanup_modules):
    module = types.ModuleType("engine.virtual")
    sys.modules["engine.virtual"] = module          # no __file__ at all
    cleanup_modules.append("engine.virtual")
    assert source_guard.check() == []


def test_app_py_is_not_watched():
    """Streamlit re-executes app.py on every rerun, so it is always the
    version on disk. It is what the others fall behind, not a victim."""
    assert "app" not in source_guard.WATCHED_ROOTS
    assert all(not root.endswith(".py") for root in source_guard.WATCHED_ROOTS)


# ---- the real modules ----------------------------------------------------
def test_the_engine_as_it_actually_sits_is_clean():
    """Run against the real sys.modules: a checkout that has not
    changed under us must report nothing."""
    assert source_guard.check() == []
    assert source_guard.check() == []


def test_it_is_watching_something_real():
    """A guard whose watch list matches nothing would pass every test
    above and protect nothing."""
    watched = list(source_guard._watched_files())
    assert watched, "the guard is not watching any loaded module"
    assert any(name.startswith("engine.") for name, _ in watched)


# ---- what it says --------------------------------------------------------
def test_the_message_names_the_modules_and_says_a_refresh_will_not_help():
    text = source_guard.message(["engine.confidence", "engine.pricing"])
    assert "engine.confidence" in text and "engine.pricing" in text
    assert "reboot" in text.lower()
    assert "refresh" in text.lower()


def test_there_is_no_message_when_there_is_no_skew():
    assert source_guard.message([]) == ""
