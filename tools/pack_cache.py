#!/usr/bin/env python3
"""Build data_cache.zip from data_cache/ -- the thing that gets committed.

    python3 tools/pack_cache.py            # rebuild the archive, print what moved
    python3 tools/pack_cache.py --check    # exit 1 if the archive is out of date
    python3 tools/pack_cache.py --diff     # say what differs, change nothing
    python3 tools/pack_cache.py --unpack [dir]   # archive -> loose folder

data_cache/ is no longer tracked by git (see engine/cache_archive.py for
why). The daily refresh writes loose JSON as it always has, then runs
this, and the archive is what travels to the deployed app.

--check is for anyone about to commit by hand: it answers "is the
archive in the tree the one this cache would produce", which is the
only way a stale archive gets caught before it is pushed.

--unpack is the way back. data_cache/ is no longer in git, so a fresh
clone has the archive and no folder; this writes the folder.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.cache_archive import (  # noqa: E402
    ARCHIVE_NAME, CACHE_DIR_NAME, compare, open_reader, pack, unpack,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(REPO_ROOT, CACHE_DIR_NAME)
ARCHIVE_PATH = os.path.join(REPO_ROOT, ARCHIVE_NAME)


def _summarise(added, changed, removed):
    """One line, and the first few names when something was removed.

    Removals are the interesting case by a distance: a refresh that adds
    and rewrites is normal, and a refresh that deletes usually means an
    endpoint answered with nothing.
    """
    print(f"added {len(added)}, changed {len(changed)}, removed {len(removed)}")
    for name in removed[:10]:
        print(f"  removed: {name}")
    if len(removed) > 10:
        print(f"  ... and {len(removed) - 10} more")


def _is_unstamped(archive_path):
    """True for an archive built before the pack stamp existed.

    The stamp is excluded from compare() so a rebuild never registers
    as a change, which also means an archive missing it would compare
    clean forever and never gain one. The deployed app reads that stamp
    to date the data, so an unstamped archive is out of date by
    definition even when every entry in it matches.
    """
    if not os.path.exists(archive_path):
        return False
    reader = open_reader(archive_path)
    return reader is None or reader.packed_at() is None


def main(argv):
    mode = argv[1] if len(argv) > 1 else "pack"
    if mode == "--unpack":
        target = argv[2] if len(argv) > 2 else CACHE_DIR
        count = unpack(ARCHIVE_PATH, target)
        print(f"wrote {count} files into {target}")
        return 0
    if mode not in ("pack", "--check", "--diff"):
        print(__doc__)
        return 2

    if not os.path.isdir(CACHE_DIR):
        print(f"no {CACHE_DIR_NAME}/ here -- nothing to pack")
        return 1

    added, changed, removed = compare(ARCHIVE_PATH, CACHE_DIR)
    unstamped = _is_unstamped(ARCHIVE_PATH)
    in_step = not (added or changed or removed or unstamped)
    if unstamped:
        print(f"{ARCHIVE_NAME} predates the pack stamp -- rebuilding")

    if mode == "--check":
        if in_step:
            print(f"{ARCHIVE_NAME} is up to date")
            return 0
        _summarise(added, changed, removed)
        print(f"{ARCHIVE_NAME} is STALE -- run: python3 tools/pack_cache.py")
        return 1

    if mode == "--diff":
        _summarise(added, changed, removed)
        return 0

    if in_step:
        print(f"{ARCHIVE_NAME} already matches {CACHE_DIR_NAME}/ -- left alone")
        return 0

    _summarise(added, changed, removed)
    count = pack(CACHE_DIR, ARCHIVE_PATH)
    size_mb = os.path.getsize(ARCHIVE_PATH) / (1024 * 1024)
    print(f"wrote {ARCHIVE_NAME}: {count} entries, {size_mb:.0f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
