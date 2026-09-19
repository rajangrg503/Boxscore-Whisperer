#!/usr/bin/env python3
"""Snapshot tonight's player prop lines, before the games are played.

    SGO_API_KEY=... python3 tools/capture_lines.py            # write a snapshot
    SGO_API_KEY=... python3 tools/capture_lines.py --dry-run  # fetch, print, write nothing
    python3 tools/capture_lines.py --summarise                # what has been captured so far

WHY THIS EXISTS
The accuracy research (claude/accuracy-research) turned up one thing
about this market that nobody disputes: no competitor publishes a
measured accuracy number. Not Props.Cash, not Outlier, not SaberSim.
They sell coverage and convenience. An honest, measured figure is the
gap worth taking -- and the only way to have one is to record what this
app projected BEFORE a game, next to what the market said, and settle
up afterwards.

That evidence cannot be reconstructed later. A closing line exists for
about ten minutes and is then gone from every free source. So this runs
on a schedule from the season's first night, and it starts before there
is anything to show for it.

CAPTURE RAW, PARSE LATER
This script parses almost nothing. It writes the API's response
verbatim, because the two kinds of mistake are not equally bad:

  a parsing bug     -> fix it and re-run over the stored snapshots
  a capture bug     -> that night's line is gone for good

So the schema is not interpreted here beyond what is needed to name the
file and count what arrived. Everything else is somebody's problem on a
day when it can still be fixed.

WHAT TRAVELS, AND WHAT STAYS PUT
An accuracy claim is worth exactly as much as its audit trail. A row in
a database saying "we projected 27.3 and the line was 26.5" is a claim
about the past that the claimant controls. A commit timestamped before
tip-off is a record nobody can quietly revise afterwards.

But this repository is public, and SportsGameOdds' terms forbid
redistributing their data through "downloadable files, bulk exports or
similar mechanisms". A public repo full of nightly odds dumps is all
three. So the raw responses stay in line_snapshots/, which .gitignore
keeps out of the repo entirely.

What travels instead is line_records/: for each capture, the time, the
number of events, and a SHA-256 of the exact bytes received. That is a
commitment, not a copy. Publishing the digest in October fixes the
content beyond revision, while the response itself is produced only on
request -- so an accuracy claim stays auditable without republishing a
single price. Nobody can work backwards from a hash to a story that
suits them in March.

WHERE IT RUNS
On the laptop, with the refresh job, not on a runner. The first design
used GitHub Actions to dodge the sleep problem, but a runner is
ephemeral: the raw response would have to be sent somewhere to survive,
and on a public repo both a commit and a build artifact are public.
Capturing where the raw file can simply stay put is simpler and cannot
leak.

Timing works out: NBA games tip between about 10:00 and 14:00
Australian eastern time, which is the middle of a waking day rather
than the middle of the night.

THE KEY
Read from SGO_API_KEY. It is never written to a file, never printed
and never committed; keep it somewhere outside the repo, such as a
chmod 600 file in the home directory that the job sources. If the
variable is missing the script says so and exits, rather than making
an unauthenticated request that would quietly return nothing and be
indistinguishable from a night with no games.
"""

import argparse
import hashlib
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE_URL = "https://api.sportsgameodds.com/v2"
LEAGUE = "NBA"
KEY_ENV = "SGO_API_KEY"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Raw responses. Gitignored -- see WHAT TRAVELS above.
SNAPSHOT_DIR = os.path.join(REPO_ROOT, "line_snapshots")

# The public half: one appended line per capture, carrying a digest of
# the raw bytes and nothing that belongs to anybody else.
RECORD_DIR = os.path.join(REPO_ROOT, "line_records")

# Only games about to start. Without this the endpoint returns every
# upcoming event that has odds, which on the first real dry run was 40
# -- the cap, so at least 40, and probably the rest of the schedule.
#
# That matters because the free tier bills one object per EVENT and
# allows 2,500 a month. Forty objects a call is 62 calls a month, about
# two a day, for a job that wants several captures a night as tip-off
# approaches. Asking for a window instead bills for the slate in front
# of us: about 7.5 games, so five captures a night is roughly 1,125 a
# month and fits comfortably.
#
# Twelve hours forward covers a full evening slate from before the
# first tip. An hour back catches a game that has just started, whose
# line is still the most recent one we can honestly call a close.
WINDOW_HOURS_AHEAD = 12
WINDOW_HOURS_BEHIND = 1

# A seatbelt, not a target: whatever the window returns, never bill
# more than this in one call. The season's evidence is worth more than
# any single night's, and a scheduling mistake must not spend the
# month's allowance in an afternoon.
MAX_EVENTS_PER_RUN = 40

TIMEOUT_SECONDS = 30


def _ssl_context():
    """A context that can actually verify the odds API's certificate.

    The python.org framework build for macOS -- which is the one the
    refresh job uses -- does not read the system keychain. It ships a
    CA bundle that a separate "Install Certificates.command" has to
    link into place, and until somebody runs it every stdlib HTTPS call
    fails with:

        [SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer
        certificate

    The refresh scripts never hit this because `requests` carries its
    own certificates. This one uses only the standard library on
    purpose, so it has to ask.

    A one-time manual step is a poor thing for an unattended nightly
    job to depend on -- it will be months before anybody notices it was
    never done, and those months are the season. So if the default
    context has no certificates, fall back to certifi's bundle, which
    is what that installer links anyway.

    Verification is never disabled. If neither source has certificates
    the call fails, which is the correct outcome: a capture that
    silently stopped checking who it was talking to would be worse than
    a missing night.
    """
    context = ssl.create_default_context()
    if context.cert_store_stats().get("x509_ca", 0) > 0:
        return context
    try:
        import certifi
    except ImportError:
        return context        # let it fail loudly, verification intact
    return ssl.create_default_context(cafile=certifi.where())


def _get(path, params, api_key):
    """One GET, returning parsed JSON. Raises on anything unexpected."""
    url = f"{BASE_URL}/{path.lstrip('/')}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={
        "X-Api-Key": api_key,
        "Accept": "application/json",
        # Named so that if this ever misbehaves, whoever is looking at
        # the logs at the other end can tell what it is.
        "User-Agent": "boxscore-whisperer-line-capture/1.0",
    })
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS,
                                context=_ssl_context()) as response:
        return json.loads(response.read().decode("utf-8"))


def _iso(when):
    """The wire format: UTC, seconds, trailing Z."""
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_events(api_key, league=LEAGUE, limit=MAX_EVENTS_PER_RUN, now=None):
    """Games starting around now, with odds, as the API returned them.

    Deliberately not normalised. See CAPTURE RAW, PARSE LATER above.

    The window is the whole reason this is affordable: see the note on
    WINDOW_HOURS_AHEAD. Without it the endpoint hands back the schedule
    and every call bills for all of it.
    """
    now = now or datetime.now(timezone.utc)
    payload = _get("events", {
        "leagueID": league,
        "oddsAvailable": "true",
        "startsAfter": _iso(now - timedelta(hours=WINDOW_HOURS_BEHIND)),
        "startsBefore": _iso(now + timedelta(hours=WINDOW_HOURS_AHEAD)),
        "limit": limit,
    }, api_key)
    if isinstance(payload, dict):
        events = payload.get("data") or payload.get("events") or []
    elif isinstance(payload, list):
        events = payload
    else:
        events = []
    return payload, events


def snapshot_path(captured_at):
    """One file per capture, named so the order is the filename order."""
    day = captured_at.strftime("%Y-%m-%d")
    stamp = captured_at.strftime("%Y-%m-%dT%H%M%SZ")
    return os.path.join(SNAPSHOT_DIR, day, f"{stamp}.json")


def write_snapshot(payload, events, captured_at):
    """Write the raw response locally. Returns (path, sha256).

    The digest is taken over the bytes actually written, not over the
    parsed object, so verifying it later needs nothing but the file and
    sha256sum -- no Python, no knowledge of this script, no trust in it.
    """
    path = snapshot_path(captured_at)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    document = {
        # Ours, not the API's: when WE asked. An accuracy claim rests on
        # this being before tip-off, and on it being fixed by a digest
        # published at the time rather than a field we could edit later.
        "captured_at": captured_at.isoformat(timespec="seconds"),
        "league": LEAGUE,
        "source": "sportsgameodds/v2/events",
        "event_count": len(events),
        "response": payload,
    }
    body = json.dumps(document, separators=(",", ":"), sort_keys=True).encode("utf-8")
    with open(path, "wb") as handle:
        handle.write(body)
    return path, hashlib.sha256(body).hexdigest()


def append_record(captured_at, event_count, digest, snapshot_file):
    """The public half: a commitment to the capture, not a copy of it.

    Carries no odds, no prices, no player lines -- nothing that belongs
    to the data provider. Just when we asked, how much came back, and a
    digest that fixes the content beyond later revision.
    """
    os.makedirs(RECORD_DIR, exist_ok=True)
    path = os.path.join(RECORD_DIR, f"{captured_at.strftime('%Y-%m')}.jsonl")
    row = {
        "captured_at": captured_at.isoformat(timespec="seconds"),
        "league": LEAGUE,
        "event_count": event_count,
        "snapshot": os.path.basename(snapshot_file),
        "sha256": digest,
    }
    with open(path, "a") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    return path


def summarise():
    """What is on disk, without parsing any of it."""
    if not os.path.isdir(SNAPSHOT_DIR):
        print("no snapshots yet")
        return 0
    days = sorted(d for d in os.listdir(SNAPSHOT_DIR)
                  if os.path.isdir(os.path.join(SNAPSHOT_DIR, d)))
    total_files = 0
    total_events = 0
    for day in days:
        files = sorted(os.listdir(os.path.join(SNAPSHOT_DIR, day)))
        events = 0
        for name in files:
            try:
                with open(os.path.join(SNAPSHOT_DIR, day, name)) as handle:
                    events += json.load(handle).get("event_count", 0)
            except (OSError, ValueError):
                pass
        total_files += len(files)
        total_events += events
        print(f"  {day}  {len(files):2d} snapshot(s)  {events:3d} event(s)")
    print(f"\n{len(days)} day(s), {total_files} snapshot(s), {total_events} event-captures")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch and report, write nothing")
    parser.add_argument("--summarise", action="store_true",
                        help="report what has already been captured, fetch nothing")
    args = parser.parse_args(argv)

    if args.summarise:
        return summarise()

    api_key = os.environ.get(KEY_ENV, "").strip()
    if not api_key:
        print(f"{KEY_ENV} is not set.\n\n"
              "  Locally:  SGO_API_KEY=... python3 tools/capture_lines.py\n"
              "  In CI:    add it as a repository secret named SGO_API_KEY\n\n"
              "Refusing to make an unauthenticated request, which would "
              "return nothing and look like a quiet night.", file=sys.stderr)
        return 2

    captured_at = datetime.now(timezone.utc)
    try:
        payload, events = fetch_events(api_key)
    except urllib.error.HTTPError as e:
        # 401 is a bad key, 429 is the quota. Both need a person, and
        # both must be loud: a capture job that fails quietly is a
        # season of missing evidence.
        print(f"HTTP {e.code} from the odds API: {e.reason}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"could not reach the odds API: {e}", file=sys.stderr)
        return 1

    if not events:
        # A real outcome, not an error: out of season, or a night with
        # no games. Exits 0 so a quiet night does not page anybody.
        print(f"{captured_at.isoformat(timespec='seconds')}  no events with odds "
              f"(off-season or no games scheduled) -- nothing written")
        return 0

    if args.dry_run:
        print(f"{captured_at.isoformat(timespec='seconds')}  "
              f"{len(events)} event(s) with odds -- dry run, nothing written")
        return 0

    path, digest = write_snapshot(payload, events, captured_at)
    record = append_record(captured_at, len(events), digest, path)
    size_kb = os.path.getsize(path) / 1024
    print(f"{captured_at.isoformat(timespec='seconds')}  "
          f"{len(events)} event(s), {size_kb:.0f} KB\n"
          f"  raw    {os.path.relpath(path, REPO_ROOT)}  (local only, gitignored)\n"
          f"  record {os.path.relpath(record, REPO_ROOT)}  sha256 {digest[:16]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
