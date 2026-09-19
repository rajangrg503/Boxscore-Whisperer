"""Tests for tools/probe_sources.py -- the probe that decides whether a
scheduled job can reach the NBA's data at all.

The probe's whole job is to report failures rather than raise them, and
to turn the results into one verdict. Both are tested offline; no probe
here touches the network."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import probe_sources as ps  # noqa: E402


def test_probe_reports_failures_instead_of_raising():
    def boom():
        raise ConnectionError("connection reset by peer")

    row = ps.probe("blows up", "example.com", boom)
    assert row["ok"] is False
    assert row["status"] is None
    assert "ConnectionError" in row["detail"]
    assert row["seconds"] >= 0


def test_probe_marks_non_200_as_failure():
    row = ps.probe("forbidden", "example.com", lambda: (403, "blocked"))
    assert row["ok"] is False and row["status"] == 403
    ok = ps.probe("fine", "example.com", lambda: (200, "12 teams"))
    assert ok["ok"] is True and ok["detail"] == "12 teams"


def _rows(**by_host):
    out = []
    for host, oks in by_host.items():
        for i, ok in enumerate(oks):
            out.append({"probe": f"{host}-{i}", "host": host, "ok": ok,
                        "status": 200 if ok else None, "seconds": 0.1, "detail": ""})
    return out


def test_verdict_all_good():
    v = ps.verdict(_rows(**{"stats.nba.com": [True, True, True]}))
    assert "can run unchanged" in v


def test_verdict_partial_is_rate_limiting_not_a_block():
    v = ps.verdict(_rows(**{"stats.nba.com": [True, False, False]}))
    assert "rate limiting" in v


def test_verdict_blocked_but_fallback_available():
    v = ps.verdict(_rows(**{"stats.nba.com": [False, False],
                            "raw.githubusercontent.com": [True]}))
    assert "fallback source answers" in v


def test_verdict_nothing_works():
    v = ps.verdict(_rows(**{"stats.nba.com": [False], "cdn.nba.com": [False],
                            "raw.githubusercontent.com": [False]}))
    assert "no useful network access" in v


def test_every_probe_is_registered_with_a_host_and_callable():
    assert len(ps.PROBES) >= 4
    for name, host, fn in ps.PROBES:
        assert name and "." in host and callable(fn)
    hosts = {host for _n, host, _f in ps.PROBES}
    assert {"stats.nba.com", "cdn.nba.com", "raw.githubusercontent.com"} <= hosts


def test_stats_headers_are_the_ones_nba_requires():
    # stats.nba.com rejects requests without these two
    assert ps.STATS_HEADERS["x-nba-stats-origin"] == "stats"
    assert ps.STATS_HEADERS["x-nba-stats-token"] == "true"
    assert ps.STATS_HEADERS["Referer"].startswith("https://stats.nba.com")
