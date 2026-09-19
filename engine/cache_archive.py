"""One file instead of 148,684 -- the data cache as a deploy artifact.

WHY THIS EXISTS
stats.nba.com blocks Streamlit Community Cloud, so the deployed app
reads everything from data_cache/ instead of calling the NBA. That
folder grew to 148,684 JSON files, and the repository tracked every one
of them: 148,942 files in total, 258 of which were the actual program.

Streamlit Cloud re-clones the repository on every reboot. At that size
the checkout stopped being atomic in practice -- twice now it has
finished with a new app.py sitting next to a stale engine/, which is
not a slow deploy but a WRONG one:

    19 Sep 2026  ImportError: cannot import name 'against_opponent'
                 from 'engine.hit_rates'        (site down, 3 reboots)
    18 Sep 2026  the page printed the projected-minutes explanation
                 underneath numbers that were still flat averages

Both were the same fault, and neither was visible in the code. A repo
whose deploys are unreliable in proportion to its file count is a bug
in the repo, so the cache stops being 148,684 tracked files and becomes
one archive that is either there or not.

WHAT THIS CHANGES, AND WHAT IT DOESN'T
Locally nothing changes: data_cache/ stays a folder of loose JSON that
the refresh scripts read and write exactly as before. It is simply no
longer tracked by git. What gets committed is data_cache.zip, built
from that folder by tools/pack_cache.py at the end of the daily
refresh.

On the cloud there is no folder, and engine/cache.py reads entries
straight out of the archive instead.

WHY READ IT IN PLACE RATHER THAN UNPACK IT
The first version of this unpacked the archive into a temp directory at
import, so nothing downstream had to change. Measured on this cache,
that took between 7 and 31 seconds depending on how busy the disk was
-- which is writing 148,684 files at boot to avoid checking out 148,684
files at deploy. The same cost in a new place, and the same variance.

Reading in place costs one pass over the zip's own directory:

    index the archive        0.60s, once per process
    read one cache entry     about 20 microseconds

so the cache is available a tenth of a second after import and nothing
is written to disk at all.

WHY A ZIP, AND WHY DETERMINISTIC
Measured on the real 681 MB cache (19 Sep 2026):

    one .zip               69 MB, packs in 8s, random access
    one .tar.gz            24 MB -- smaller, but gzip has no seams, so
                           git cannot delta two days' copies and the
                           history would grow 24 MB per refresh, and
                           nothing can be read without unpacking it all
    one plain .tar        331 MB -- git deltas it to nothing, but
                           GitHub refuses any file over 100 MB

A zip compresses each entry separately, so an unchanged file's bytes
are unchanged in tomorrow's archive and git deltas the two: a refresh
touching 1,401 files added 5 MB to a test repository, not 69 MB.

That only holds if the archive is byte-stable, which is why entries are
written in sorted order with a fixed timestamp and mode. Otherwise
every rebuild would differ everywhere and the daily commit would carry
a fresh 69 MB -- and worse, a refresh that changed nothing would still
look like a change worth pushing.
"""

import datetime
import json
import os
import threading
import zipfile

# Zip cannot store a timestamp before 1980. Any fixed value works; this
# one is the format's own floor, and being obviously synthetic says
# "this field is not data" to anyone who looks.
FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
FIXED_MODE = 0o644
COMPRESS_LEVEL = 6

ARCHIVE_NAME = "data_cache.zip"
CACHE_DIR_NAME = "data_cache"

# Written into the archive by pack(), and the only entry that is not a
# copy of a file in data_cache/. It carries the moment the archive was
# built, which on the cloud is the closest honest answer to "when was
# this data refreshed" -- file modification times there record when git
# checked the repo out, not when the NBA was last asked anything.
# Excluded from compare(), or a rebuilt archive would always look
# changed by exactly one file and the refresh job could never tell a
# quiet morning from a real one.
PACK_STAMP_NAME = "_packed_at.json"

# Files in data_cache/ that are local diagnostic state rather than
# cached NBA data. .gitignore already keeps this one out of the repo;
# keeping it out of the archive too means a local watchdog run does not
# make the archive look changed.
EXCLUDED_NAMES = frozenset({"_watchdog_status.json", PACK_STAMP_NAME})


def _entry_names(cache_dir):
    """The cache files to archive, sorted, excluding local-only state."""
    try:
        names = os.listdir(cache_dir)
    except OSError:
        return []
    return sorted(
        n for n in names
        if n not in EXCLUDED_NAMES
        and not n.startswith(".")
        and os.path.isfile(os.path.join(cache_dir, n))
    )


def pack(cache_dir, archive_path, packed_at=None):
    """Write cache_dir into archive_path. Returns the number of entries.

    Writes to a temporary file and renames, so an interrupted pack
    cannot leave a half-written archive where a whole one used to be --
    the archive IS the cache on the cloud, and a truncated one would
    take the site down rather than merely make it stale.
    """
    names = _entry_names(cache_dir)
    stamp = (packed_at or datetime.datetime.now()).isoformat(timespec="seconds")
    tmp_path = archive_path + ".tmp"
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=COMPRESS_LEVEL) as archive:
        _write(archive, PACK_STAMP_NAME, json.dumps({"packed_at": stamp}).encode())
        for name in names:
            with open(os.path.join(cache_dir, name), "rb") as handle:
                _write(archive, name, handle.read())
    os.replace(tmp_path, archive_path)
    return len(names)


def _write(archive, name, data):
    info = zipfile.ZipInfo(name, date_time=FIXED_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = FIXED_MODE << 16
    archive.writestr(info, data)


def manifest(archive_path):
    """{name: crc} for an archive, or {} when it isn't there.

    CRCs come free in the zip's own directory, so comparing two
    archives costs a directory read rather than a decompression.
    """
    if not os.path.exists(archive_path):
        return {}
    with zipfile.ZipFile(archive_path) as archive:
        return {info.filename: info.CRC for info in archive.infolist()
                if info.filename not in EXCLUDED_NAMES}


def compare(archive_path, cache_dir):
    """How the folder differs from the archive: (added, changed, removed).

    This is what the refresh job's safety checks run on now that git no
    longer tracks the individual files. It reads the folder rather than
    trusting a count, because "47 files vanished" and "47 files were
    rewritten" have to be told apart before anything is pushed.
    """
    import zlib

    old = manifest(archive_path)
    added, changed = [], []
    present = set()
    for name in _entry_names(cache_dir):
        present.add(name)
        with open(os.path.join(cache_dir, name), "rb") as handle:
            crc = zlib.crc32(handle.read()) & 0xFFFFFFFF
        if name not in old:
            added.append(name)
        elif old[name] != crc:
            changed.append(name)
    removed = sorted(set(old) - present)
    return added, changed, removed


def unpack(archive_path, target_dir):
    """Extract the whole archive into target_dir. Returns the count.

    Not used by the app -- it reads entries in place. This is the
    escape hatch for a machine that has the archive and wants the
    folder back (tools/pack_cache.py --unpack), which is the only way
    to restore a cache that is no longer in git.
    """
    os.makedirs(target_dir, exist_ok=True)
    written = 0
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            name = info.filename
            if name in EXCLUDED_NAMES or not _is_flat(name):
                continue
            with archive.open(info) as src, open(os.path.join(target_dir, name), "wb") as dst:
                dst.write(src.read())
            written += 1
    return written


def _is_flat(name):
    """Cache keys are flat by construction (engine/cache.py replaces
    every separator), so an entry naming a path did not come from here
    and is not written anywhere."""
    if not name or name.startswith("..") or os.path.isabs(name):
        return False
    if "/" in name or "\\" in name:
        return False
    return True


def has_loose_cache(cache_dir):
    """True when cache_dir holds real cached data.

    The test is "any .json at all" rather than a count, because the one
    case that must resolve to the loose folder is a developer's machine,
    where it is always full, and the one case that must not is the
    cloud, where the folder does not exist.
    """
    try:
        with os.scandir(cache_dir) as entries:
            for entry in entries:
                if entry.name.endswith(".json") and entry.name not in EXCLUDED_NAMES:
                    return True
    except OSError:
        return False
    return False


class ArchiveReader:
    """Random access to the packed cache, without unpacking it.

    Holds one open zip and a name -> ZipInfo index built once. Reads are
    guarded by a lock because Streamlit runs script reruns on threads
    and a ZipFile shares one file position; the lock is cheap next to
    the tens of microseconds a read costs.

    Every method answers "not here" rather than raising. A cache miss
    has always been a normal outcome in this app -- cached_or_live()
    treats it as one -- and a corrupt archive should degrade to the
    same thing rather than take the page down.
    """

    def __init__(self, archive_path):
        self.path = archive_path
        self._lock = threading.Lock()
        self._zip = zipfile.ZipFile(archive_path)
        self._names = {info.filename for info in self._zip.infolist()}

    def __contains__(self, name):
        return name in self._names

    def names(self):
        """Every cache file in the archive, excluding its own stamp."""
        return sorted(n for n in self._names if n not in EXCLUDED_NAMES)

    def read_bytes(self, name):
        if name not in self._names:
            return None
        try:
            with self._lock:
                return self._zip.read(name)
        except Exception:
            return None

    def read_json(self, name):
        raw = self.read_bytes(name)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return None

    def packed_at(self):
        """When this archive was built, as a datetime, or None.

        On the cloud this is what "how old is the data" actually means:
        the refresh job packs the archive the moment it finishes, and
        everything else about the deployed files -- their modification
        times above all -- records the deploy instead.
        """
        payload = self.read_json(PACK_STAMP_NAME) or {}
        try:
            return datetime.datetime.fromisoformat(payload.get("packed_at"))
        except (TypeError, ValueError):
            return None


def open_reader(archive_path):
    """An ArchiveReader, or None if there is nothing readable there."""
    if not os.path.exists(archive_path):
        return None
    try:
        return ArchiveReader(archive_path)
    except Exception:
        return None
