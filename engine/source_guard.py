"""Is the code running the same code that is on disk?

WHAT WENT WRONG, 20 SEPTEMBER 2026
The live app threw

    TypeError: score_prediction() got an unexpected keyword argument
               'baseline_is_prior_season'

with `app.py` calling it three ways and `engine/confidence.py` accepting
two. Both files were correct and consistent on main; a scan of every
commit found no moment when they disagreed.

The skew was in memory, not in git. Streamlit Cloud's "Updated app!"
pulls the new code and re-runs the script WITHOUT restarting Python.
`app.py` is re-executed, so it moves forward. Anything already in
sys.modules does not: every module under engine/ kept the code it had
when the container booted. That container had been up 22 hours while
seven pull requests landed underneath it.

Rebooting fixed it. Nothing in the repository could have.

WHY A GUARD RATHER THAN A NOTE IN THE README
The crash was the lucky case. It happened only because a SIGNATURE
changed, which Python refuses to paper over.

A change that keeps the signature makes no noise at all:

  * a threshold moves in confidence.py and the badge keeps saying High
  * MIN_NIGHTS_TO_STATE changes in forward_record.py and the live
    record panel keeps quoting the old gate
  * a fix lands in pricing.py and the break-even stays wrong

The app would serve yesterday's numbers, indefinitely, looking
perfectly healthy. For a product whose entire claim is that its numbers
can be checked, that is the worst failure available -- and it is
exactly the shape of every other bug found on 20 September: not a
crash, a quiet wrong answer.

WHAT THIS DOES
Hashes every watched source file the first time it is asked, while
those files are certainly the ones in memory. On every rerun it hashes
them again. A file whose contents have changed is a module whose code
on disk is no longer the code that is running.

The app then refuses to render rather than mixing two versions.

WHY REFUSING IS THE RIGHT ANSWER HERE, AND ITS COST
Refusing means the app is down until somebody reboots it -- a rerun
will not reload the modules, so it will not clear on its own. That is a
real cost and it is the correct trade for this product: showing a
figure computed half from one version and half from another is worse
than showing none, because nobody can tell it happened.

It should also be rare. Only a change to a watched .py file can trigger
it, and in season the daily pushes are data_cache.zip and results/*.json
-- neither is watched. It fires when code is deployed, which is exactly
when somebody is looking.

WHAT IS NOT WATCHED, AND WHY
app.py itself. Streamlit re-executes it on every rerun, so it is always
the version on disk; it is the thing the others fall behind.
"""

import hashlib
import os
import sys

# Import roots whose modules are ours and change together with app.py.
# Third-party packages are left alone: they only change when the
# dependency install runs, which restarts the process anyway.
WATCHED_ROOTS = ("engine", "analytics")

# path -> the hash it had when we first saw it. Written once per path
# and never overwritten: the first reading is the one that matches what
# was imported into memory.
_SNAPSHOT = {}


def _digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _watched_files():
    """Every loaded module of ours that has a readable .py on disk."""
    for name, module in list(sys.modules.items()):
        if name.split(".")[0] not in WATCHED_ROOTS:
            continue
        path = getattr(module, "__file__", None)
        if not path or not path.endswith(".py"):
            continue
        yield name, path


def check():
    """Modules whose source has changed since it was imported.

    Returns a sorted list of module names, empty when everything in
    memory matches its file. Safe to call on every rerun.

    A module seen for the first time is recorded, not reported: it was
    imported from whatever is on disk now, so it is current by
    definition. That covers imports that happen lazily, after the first
    call, including after a pull.

    Never raises. A guard that can take the app down by itself is worse
    than the problem it guards against, so an unreadable or vanished
    file is skipped rather than treated as skew.
    """
    stale = []
    for name, path in _watched_files():
        try:
            digest = _digest(path)
        except OSError:
            continue
        known = _SNAPSHOT.get(path)
        if known is None:
            _SNAPSHOT[path] = digest
        elif known != digest:
            stale.append(name)
    return sorted(stale)


def message(stale):
    """What to tell a reader, and what to tell whoever runs this.

    Two audiences in one box on purpose: the visitor needs to know the
    page is not broken so much as mid-deploy, and the operator needs to
    know that refreshing will not fix it.
    """
    if not stale:
        return ""
    return (
        "**This app is part-way through a deploy.**\n\n"
        "The page is running newer code than some of the modules it "
        "depends on, so any number it showed you now could be computed "
        "half from one version and half from another. It is better to "
        "show you nothing than to show you that.\n\n"
        f"Out of date in memory: `{'`, `'.join(stale)}`\n\n"
        "This clears with a reboot of the app, not a refresh of the "
        "page — Streamlit re-runs the script on a deploy but does not "
        "re-import modules it already has."
    )
