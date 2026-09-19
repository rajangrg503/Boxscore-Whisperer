"""Tests for engine/cache_archive.py -- the cache as one deploy artifact.

Three behaviours carry real weight here.

The archive must be byte-stable except where the data changed: a
rebuild from an unchanged folder has to produce the same entries, or
every daily refresh commits a fresh 69 MB and a morning where nothing
happened still looks like a change worth pushing.

Reads must never raise. The deployed app has no other copy of this
data, and a corrupt entry should look like a cache miss -- which
cached_or_live() already handles -- rather than an exception on the
page.

And compare() must tell a rewrite from a deletion, because it is what
the refresh job's safety check runs on now that git no longer tracks
the individual files.
"""

import datetime
import json
import zipfile

import pytest

from engine import cache_archive as ca


def cache(tmp_path, files):
    d = tmp_path / "data_cache"
    d.mkdir(exist_ok=True)
    for name, body in files.items():
        (d / name).write_text(body)
    return str(d)


# ---- packing --------------------------------------------------------------
def test_pack_round_trips_every_file(tmp_path):
    src = cache(tmp_path, {"a.json": '{"x":1}', "b.json": '{"y":2}'})
    archive = str(tmp_path / "c.zip")
    assert ca.pack(src, archive) == 2

    reader = ca.open_reader(archive)
    assert reader.read_json("a.json") == {"x": 1}
    assert reader.read_json("b.json") == {"y": 2}


def test_rebuilding_an_unchanged_cache_produces_the_same_entries(tmp_path):
    """The property the daily commit depends on: only the pack stamp
    may differ, and that one entry is excluded from every comparison."""
    src = cache(tmp_path, {f"f{i}.json": f'{{"n":{i}}}' for i in range(20)})
    first, second = str(tmp_path / "1.zip"), str(tmp_path / "2.zip")
    ca.pack(src, first)
    ca.pack(src, second)
    assert ca.manifest(first) == ca.manifest(second)
    assert ca.PACK_STAMP_NAME not in ca.manifest(first)


def test_entries_are_sorted_so_the_order_cannot_drift(tmp_path):
    src = cache(tmp_path, {"z.json": "1", "a.json": "2", "m.json": "3"})
    archive = str(tmp_path / "c.zip")
    ca.pack(src, archive)
    with zipfile.ZipFile(archive) as z:
        assert [n for n in z.namelist() if n != ca.PACK_STAMP_NAME] == [
            "a.json", "m.json", "z.json"]


def test_local_watchdog_state_is_not_archived(tmp_path):
    """It changes on every run and is already gitignored; archiving it
    would make an unchanged cache look changed every morning."""
    src = cache(tmp_path, {"a.json": "1", "_watchdog_status.json": "{}"})
    archive = str(tmp_path / "c.zip")
    assert ca.pack(src, archive) == 1
    assert list(ca.manifest(archive)) == ["a.json"]


def test_an_interrupted_pack_leaves_the_old_archive_intact(tmp_path, monkeypatch):
    """On the cloud the archive IS the cache. A truncated one is not a
    stale site, it is a broken one."""
    src = cache(tmp_path, {"a.json": "1"})
    archive = str(tmp_path / "c.zip")
    ca.pack(src, archive)
    good = open(archive, "rb").read()

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(ca.zipfile.ZipFile, "writestr", boom)
    with pytest.raises(RuntimeError):
        ca.pack(src, archive)
    assert open(archive, "rb").read() == good


def test_the_pack_stamp_records_when_it_was_built(tmp_path):
    when = datetime.datetime(2026, 9, 19, 8, 30, 0)
    src = cache(tmp_path, {"a.json": "1"})
    archive = str(tmp_path / "c.zip")
    ca.pack(src, archive, packed_at=when)
    assert ca.open_reader(archive).packed_at() == when


# ---- comparing ------------------------------------------------------------
def test_compare_tells_added_changed_and_removed_apart(tmp_path):
    """The refresh job's safety check runs on this now that git no
    longer tracks the files: "47 files vanished" and "47 files were
    rewritten" must not look the same."""
    src = cache(tmp_path, {"keep.json": "1", "edit.json": "2", "gone.json": "3"})
    archive = str(tmp_path / "c.zip")
    ca.pack(src, archive)

    (tmp_path / "data_cache" / "edit.json").write_text("222")
    (tmp_path / "data_cache" / "gone.json").unlink()
    (tmp_path / "data_cache" / "new.json").write_text("4")

    added, changed, removed = ca.compare(archive, src)
    assert added == ["new.json"]
    assert changed == ["edit.json"]
    assert removed == ["gone.json"]


def test_compare_against_a_missing_archive_calls_everything_added(tmp_path):
    src = cache(tmp_path, {"a.json": "1", "b.json": "2"})
    added, changed, removed = ca.compare(str(tmp_path / "nope.zip"), src)
    assert added == ["a.json", "b.json"]
    assert (changed, removed) == ([], [])


def test_an_unchanged_cache_compares_clean(tmp_path):
    """A quiet morning must push nothing, so the pack stamp -- which
    differs on every rebuild -- cannot register as a change."""
    src = cache(tmp_path, {"a.json": "1"})
    archive = str(tmp_path / "c.zip")
    ca.pack(src, archive)
    assert ca.compare(archive, src) == ([], [], [])


# ---- reading --------------------------------------------------------------
def test_reads_come_back_as_written(tmp_path):
    src = cache(tmp_path, {"g.json": json.dumps({"cached_at": "x", "data": [1, 2]})})
    archive = str(tmp_path / "c.zip")
    ca.pack(src, archive)
    assert ca.open_reader(archive).read_json("g.json")["data"] == [1, 2]


def test_a_missing_entry_reads_as_none_not_an_error(tmp_path):
    src = cache(tmp_path, {"a.json": "1"})
    archive = str(tmp_path / "c.zip")
    ca.pack(src, archive)
    reader = ca.open_reader(archive)
    assert reader.read_bytes("absent.json") is None
    assert reader.read_json("absent.json") is None
    assert "absent.json" not in reader


def test_an_entry_that_is_not_json_reads_as_none(tmp_path):
    """A cache miss is a normal outcome here; a page-level exception
    is not."""
    src = cache(tmp_path, {"bad.json": "{not json"})
    archive = str(tmp_path / "c.zip")
    ca.pack(src, archive)
    assert ca.open_reader(archive).read_json("bad.json") is None


def test_opening_something_that_is_not_an_archive_returns_none(tmp_path):
    (tmp_path / "junk.zip").write_text("not a zip")
    assert ca.open_reader(str(tmp_path / "junk.zip")) is None
    assert ca.open_reader(str(tmp_path / "absent.zip")) is None


def test_names_lists_the_cache_but_not_the_stamp(tmp_path):
    src = cache(tmp_path, {"a.json": "1", "b.json": "2"})
    archive = str(tmp_path / "c.zip")
    ca.pack(src, archive)
    assert ca.open_reader(archive).names() == ["a.json", "b.json"]


# ---- unpacking (the escape hatch) ----------------------------------------
def test_unpack_restores_the_folder(tmp_path):
    """The only way back to a loose cache now that git no longer
    carries one."""
    src = cache(tmp_path, {"a.json": "1", "b.json": "2"})
    archive = str(tmp_path / "c.zip")
    ca.pack(src, archive)
    assert ca.unpack(archive, str(tmp_path / "out")) == 2
    assert (tmp_path / "out" / "a.json").read_text() == "1"
    assert not (tmp_path / "out" / ca.PACK_STAMP_NAME).exists()


def test_entries_naming_a_path_are_never_written(tmp_path):
    """Cache keys are flat by construction, so anything with a
    separator in it did not come from here."""
    archive = str(tmp_path / "c.zip")
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("good.json", "1")
        z.writestr("../escape.json", "2")
    assert ca.unpack(archive, str(tmp_path / "out")) == 1
    assert [p.name for p in (tmp_path / "out").iterdir()] == ["good.json"]
    assert not (tmp_path / "escape.json").exists()


# ---- which form is in use -------------------------------------------------
def test_a_folder_of_json_is_recognised_as_the_live_cache(tmp_path):
    assert ca.has_loose_cache(cache(tmp_path, {"a.json": "1"}))


def test_a_missing_or_empty_folder_is_not(tmp_path):
    assert not ca.has_loose_cache(str(tmp_path / "absent"))
    assert not ca.has_loose_cache(cache(tmp_path, {}))


def test_watchdog_state_alone_does_not_count_as_a_cache(tmp_path):
    """Otherwise a machine with nothing but leftover diagnostic state
    would read the empty folder instead of the archive."""
    assert not ca.has_loose_cache(cache(tmp_path, {"_watchdog_status.json": "{}"}))
