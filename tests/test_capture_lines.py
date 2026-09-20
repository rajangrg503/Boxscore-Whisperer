"""Tests for tools/capture_lines.py -- the nightly line snapshot.

This job has an unusual property: its failures are unrecoverable. A
parsing bug can be fixed and re-run over stored snapshots; a capture
bug means that night's closing line is gone from every free source and
no amount of later work brings it back.

So what is pinned here is mostly about not losing evidence and not
producing false evidence:

  * an unauthenticated run refuses rather than returning "no games",
    which would be indistinguishable from a quiet night
  * a genuinely quiet night exits 0 and writes nothing, so the
    off-season does not page anybody every hour
  * the response is stored verbatim, because the schema is not ours and
    anything this script "understands" is a guess it could get wrong
  * captured_at is OUR clock at request time -- the field an accuracy
    claim rests on
"""

import json
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import capture_lines as cl  # noqa: E402


WHEN = datetime(2026, 10, 21, 1, 30, 0, tzinfo=timezone.utc)


@pytest.fixture
def snapshots(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "SNAPSHOT_DIR", str(tmp_path / "line_snapshots"))
    monkeypatch.setattr(cl, "RECORD_DIR", str(tmp_path / "line_records"))
    monkeypatch.setattr(cl, "REPO_ROOT", str(tmp_path))
    return tmp_path / "line_snapshots"


# ---- refusing to produce false evidence ----------------------------------
def test_without_a_key_it_refuses_rather_than_reporting_a_quiet_night(monkeypatch, capsys):
    """An unauthenticated request returns nothing, which looks exactly
    like an off-season night. Exiting 2 makes the difference visible."""
    monkeypatch.delenv(cl.KEY_ENV, raising=False)
    assert cl.main([]) == 2
    assert cl.KEY_ENV in capsys.readouterr().err


def test_an_http_error_fails_loudly(monkeypatch, snapshots):
    """A bad key or a spent quota needs a person. A capture job that
    fails quietly is a season of missing evidence."""
    import urllib.error

    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)

    monkeypatch.setenv(cl.KEY_ENV, "x")
    monkeypatch.setattr(cl, "fetch_events", boom)
    assert cl.main([]) == 1
    assert not snapshots.exists()


def test_a_night_with_no_games_is_success_not_failure(monkeypatch, snapshots, capsys):
    """The season starts in October. Nine months of hourly failures
    would train everyone to ignore this job."""
    monkeypatch.setenv(cl.KEY_ENV, "x")
    monkeypatch.setattr(cl, "fetch_events", lambda *a, **k: ({"data": []}, []))
    assert cl.main([]) == 0
    assert "no events" in capsys.readouterr().out
    assert not snapshots.exists()


def test_a_dry_run_writes_nothing(monkeypatch, snapshots):
    monkeypatch.setenv(cl.KEY_ENV, "x")
    monkeypatch.setattr(cl, "fetch_events",
                        lambda *a, **k: ({"data": [{"eventID": "1"}]}, [{"eventID": "1"}]))
    assert cl.main(["--dry-run"]) == 0
    assert not snapshots.exists()


# ---- what gets written ----------------------------------------------------
def test_the_response_is_stored_verbatim(snapshots):
    """The schema belongs to somebody else and will change. Anything
    this script normalises now is a guess that could be wrong, and a
    wrong guess is unrecoverable -- unlike a parsing bug, which can be
    fixed and re-run over these files."""
    payload = {"data": [{"eventID": "abc", "odds": {"weird": ["shape", 1, None]}}],
               "nextCursor": "xyz"}
    path, _ = cl.write_snapshot(payload, payload["data"], WHEN)
    stored = json.load(open(path))
    assert stored["response"] == payload


def test_it_records_when_we_asked(snapshots):
    """The field an accuracy claim rests on: the line was captured
    before the game, and here is when."""
    path, _ = cl.write_snapshot({"data": []}, [], WHEN)
    assert json.load(open(path))["captured_at"] == "2026-10-21T01:30:00+00:00"


def test_the_event_count_is_recorded_so_summaries_need_no_parsing(snapshots):
    events = [{"eventID": str(i)} for i in range(7)]
    path, _ = cl.write_snapshot({"data": events}, events, WHEN)
    assert json.load(open(path))["event_count"] == 7


def test_snapshots_sort_by_filename_into_capture_order(snapshots):
    """Reading a season back in order should not require parsing every
    file to find out when it was taken."""
    early = cl.snapshot_path(datetime(2026, 10, 21, 1, 0, tzinfo=timezone.utc))
    late = cl.snapshot_path(datetime(2026, 10, 21, 3, 0, tzinfo=timezone.utc))
    assert os.path.dirname(early) == os.path.dirname(late)   # same night
    assert os.path.basename(early) < os.path.basename(late)


def test_several_captures_a_night_do_not_overwrite_each_other(snapshots):
    """Five runs a night is the point -- one of them is nearest to tip."""
    a, _ = cl.write_snapshot({"data": []}, [], datetime(2026, 10, 21, 1, 0, tzinfo=timezone.utc))
    b, _ = cl.write_snapshot({"data": []}, [], datetime(2026, 10, 21, 2, 0, tzinfo=timezone.utc))
    assert a != b
    assert os.path.exists(a) and os.path.exists(b)


# ---- reading what is there ------------------------------------------------
def test_summarise_copes_with_nothing_captured_yet(snapshots, capsys):
    assert cl.summarise() == 0
    assert "no snapshots" in capsys.readouterr().out


def test_summarise_counts_without_choking_on_a_damaged_file(snapshots, capsys):
    """One unreadable file must not hide the rest of the season."""
    events = [{"eventID": "1"}, {"eventID": "2"}]
    cl.write_snapshot({"data": events}, events, WHEN)
    bad = os.path.join(os.path.dirname(cl.snapshot_path(WHEN)), "broken.json")
    open(bad, "w").write("{not json")

    assert cl.summarise() == 0
    out = capsys.readouterr().out
    assert "2026-10-21" in out
    assert "2 event" in out


# ---- the quota seatbelt ---------------------------------------------------
def test_there_is_a_cap_on_events_per_run():
    """The free tier bills per event and the season's evidence is worth
    more than any one night: a scheduling mistake must not spend the
    month's allowance in an afternoon."""
    assert 0 < cl.MAX_EVENTS_PER_RUN <= 100


def test_the_request_asks_only_for_games_with_odds(monkeypatch):
    """Events without odds cost quota and carry nothing worth storing."""
    seen = {}

    def fake_get(path, params, api_key):
        seen.update(path=path, params=params, key=api_key)
        return {"data": []}

    monkeypatch.setattr(cl, "_get", fake_get)
    cl.fetch_events("key-123")
    assert seen["path"] == "events"
    assert seen["params"]["leagueID"] == "NBA"
    assert seen["params"]["oddsAvailable"] == "true"
    assert seen["params"]["limit"] == cl.MAX_EVENTS_PER_RUN
    assert seen["key"] == "key-123"


def test_it_asks_only_for_games_about_to_start(monkeypatch):
    """The first real dry run returned 40 events -- the cap, in
    September, with no NBA being played. The endpoint hands back the
    whole schedule unless asked otherwise, and at one billed object per
    event that is the month's allowance in two days."""
    seen = {}
    monkeypatch.setattr(cl, "_get",
                        lambda path, params, key: seen.update(params) or {"data": []})
    now = datetime(2026, 10, 21, 23, 0, 0, tzinfo=timezone.utc)
    cl.fetch_events("k", now=now)

    assert seen["startsAfter"] == "2026-10-21T22:00:00Z"    # 1h back
    assert seen["startsBefore"] == "2026-10-22T11:00:00Z"   # 12h forward


def test_the_window_can_be_widened_for_one_call(monkeypatch):
    """Out of season nothing tips within twelve hours, so the nightly
    settings return nothing at all -- which leaves no way to look at
    the feed's shape until opening night, the worst possible time to
    be finding out what its responses look like."""
    seen = {}
    monkeypatch.setattr(cl, "_get",
                        lambda path, params, key: seen.update(params) or {"data": []})
    now = datetime(2026, 9, 20, 1, 0, 0, tzinfo=timezone.utc)
    cl.fetch_events("k", now=now, ahead_hours=24 * 40)

    assert seen["startsAfter"] == "2026-09-20T00:00:00Z"    # still 1h back
    assert seen["startsBefore"] == "2026-10-30T01:00:00Z"   # 40 days forward


def test_a_widened_window_still_cannot_outspend_the_cap(monkeypatch):
    """The seatbelt is the point: a wide window must cost no more than
    a narrow one."""
    seen = {}
    monkeypatch.setattr(cl, "_get",
                        lambda path, params, key: seen.update(params) or {"data": []})
    cl.fetch_events("k", ahead_hours=24 * 365)
    assert seen["limit"] == cl.MAX_EVENTS_PER_RUN


def test_the_window_reaches_back_far_enough_to_catch_a_tipped_game(monkeypatch):
    """A game that has just started still has the most recent line we
    can honestly call a close."""
    assert cl.WINDOW_HOURS_BEHIND >= 1
    assert cl.WINDOW_HOURS_AHEAD >= 8      # a full evening slate


def test_events_are_found_whatever_the_envelope(monkeypatch):
    """Three plausible response shapes, because the schema is not
    documented well enough to bet a season on one reading of it."""
    for payload, expected in (
        ({"data": [1, 2]}, 2),
        ({"events": [1, 2, 3]}, 3),
        ([1, 2, 3, 4], 4),
        ({"unexpected": "shape"}, 0),
    ):
        monkeypatch.setattr(cl, "_get", lambda *a, **k: payload)
        _, events = cl.fetch_events("k")
        assert len(events) == expected


# ---- the public half: a commitment, not a copy ---------------------------
def test_the_record_carries_a_digest_and_no_odds(snapshots, tmp_path):
    """This repo is public and the provider's terms forbid republishing
    their data. The record must fix the capture beyond later revision
    without reproducing a single price."""
    payload = {"data": [{"eventID": "a", "odds": {"points": 26.5, "price": -115}}]}
    path, digest = cl.write_snapshot(payload, payload["data"], WHEN)
    record_path = cl.append_record(WHEN, 1, digest, path)

    row = json.loads(open(record_path).read().strip())
    assert row["sha256"] == digest
    assert row["event_count"] == 1
    assert row["captured_at"] == "2026-10-21T01:30:00+00:00"

    blob = open(record_path).read()
    for leaked in ("26.5", "-115", "odds", "price"):
        assert leaked not in blob, f"{leaked!r} must not travel in the public record"


def test_the_digest_matches_the_file_on_disk(snapshots):
    """Verifiable with sha256sum alone -- no Python, no knowledge of
    this script, no trust in it."""
    import hashlib
    payload = {"data": [{"eventID": "a"}]}
    path, digest = cl.write_snapshot(payload, payload["data"], WHEN)
    assert hashlib.sha256(open(path, "rb").read()).hexdigest() == digest


def test_a_changed_snapshot_no_longer_matches_its_record(snapshots):
    """The property the whole design rests on: editing the raw file
    afterwards breaks the digest published at capture time."""
    path, digest = cl.write_snapshot({"data": [{"eventID": "a"}]}, [{"eventID": "a"}], WHEN)
    import hashlib
    open(path, "w").write('{"captured_at":"2026-10-21T01:30:00+00:00","tampered":true}')
    assert hashlib.sha256(open(path, "rb").read()).hexdigest() != digest


def test_records_append_within_a_month_rather_than_overwrite(snapshots):
    a = cl.append_record(WHEN, 1, "aa", "x.json")
    b = cl.append_record(datetime(2026, 10, 22, 1, 0, tzinfo=timezone.utc), 2, "bb", "y.json")
    assert a == b                                  # same month, same file
    assert len(open(a).read().strip().splitlines()) == 2


def test_a_full_run_writes_both_halves(monkeypatch, snapshots, tmp_path):
    events = [{"eventID": "1"}]
    monkeypatch.setenv(cl.KEY_ENV, "x")
    monkeypatch.setattr(cl, "fetch_events", lambda *a, **k: ({"data": events}, events))
    assert cl.main([]) == 0
    assert snapshots.exists()                      # raw, gitignored
    assert (tmp_path / "line_records").exists()    # record, committed


# ---- talking to the right server -----------------------------------------
def test_the_context_always_verifies(monkeypatch):
    """The fallback exists so an unattended job does not depend on a
    manual step run months earlier. It must never become a way to stop
    checking who we are talking to."""
    import ssl
    for ca_count in (0, 150):
        monkeypatch.setattr(
            ssl.SSLContext, "cert_store_stats",
            lambda self, n=ca_count: {"x509_ca": n, "x509": n, "crl": 0})
        context = cl._ssl_context()
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True


def test_an_empty_system_store_falls_back_to_certifi(monkeypatch):
    """macOS' python.org build ships no usable store until somebody
    runs Install Certificates.command; certifi carries the same bundle
    that installer links."""
    import ssl
    monkeypatch.setattr(ssl.SSLContext, "cert_store_stats",
                        lambda self: {"x509_ca": 0, "x509": 0, "crl": 0})
    called = {}
    real = ssl.create_default_context

    def spy(*a, **k):
        called.update(k)
        return real()

    monkeypatch.setattr(ssl, "create_default_context", spy)
    cl._ssl_context()
    assert "cafile" in called, "should have reached for certifi's bundle"


def test_with_no_certificates_anywhere_it_fails_rather_than_skipping_checks(monkeypatch):
    """A capture that silently stopped verifying would be worse than a
    missing night."""
    import builtins
    import ssl
    monkeypatch.setattr(ssl.SSLContext, "cert_store_stats",
                        lambda self: {"x509_ca": 0, "x509": 0, "crl": 0})
    real_import = builtins.__import__

    def no_certifi(name, *a, **k):
        if name == "certifi":
            raise ImportError("no certifi")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_certifi)
    context = cl._ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
