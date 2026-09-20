"""Tests for tools/capture_projections.py -- our half of the forward test.

The line capture records what the market said. This records what we
said. Neither is worth anything alone, and both have the same property:
the claim is fixed at capture time and cannot be improved afterwards.

What matters here:
  * the recorded number is the one the page shows, via the shared seam
    rather than a second implementation that could drift
  * a player we cannot project is skipped, not guessed -- a forward test
    must never score a projection it never made
  * the digest fixes the record, so a projection published in October
    cannot be quietly corrected in March
"""

import hashlib
import json
import os
import sys
from datetime import datetime, timezone

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import capture_projections as cp  # noqa: E402

WHEN = datetime(2026, 10, 21, 1, 30, 0, tzinfo=timezone.utc)


def gamelog(n=40, pts=25.0):
    """A plausible season, newest first, with the columns the engine needs."""
    dates = pd.date_range("2026-01-01", periods=n)[::-1]
    return {"cached_at": "x", "data": [
        {"GAME_DATE": d.strftime("%Y-%m-%d"), "MIN": 34,
         "PTS": pts + (i % 7) - 3, "REB": 5 + (i % 3), "AST": 6 + (i % 4),
         "STL": 1, "BLK": 1, "FG3M": 2, "FG3A": 6, "TOV": 3, "OREB": 1}
        for i, d in enumerate(dates)]}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "PROJECTION_DIR", str(tmp_path / "projections"))
    monkeypatch.setattr(cp, "REPO_ROOT", str(tmp_path))
    return tmp_path


# ---- projecting -----------------------------------------------------------
def test_a_projection_carries_the_number_and_its_interval(monkeypatch):
    monkeypatch.setattr(cp, "read_payload", lambda key: gamelog())
    result = cp.projection_for("1", "2026-27")
    assert result["n_prior_games"] == 40
    pts = result["stats"]["PTS"]
    assert pts["low"] < pts["projected"] < pts["high"]
    assert pts["calibrated"] is True


def test_the_interval_is_the_one_the_page_shows(monkeypatch):
    """Recorded through the same distribution the app renders, so a
    recorded claim cannot mean something different from the page."""
    from engine.distribution import distribution_for
    monkeypatch.setattr(cp, "read_payload", lambda key: gamelog())
    result = cp.projection_for("1", "2026-27")
    pts = result["stats"]["PTS"]

    from engine.baseline_stats import stats_from_gamelog
    stats, n = stats_from_gamelog(pd.DataFrame(gamelog()["data"]))
    mean, spread = stats["PTS"]
    low, high = distribution_for("PTS", mean, spread, n).interval(cp.RANGE_NOMINAL)

    assert pts["projected"] == pytest.approx(round(float(mean), 3))
    assert pts["low"] == pytest.approx(round(float(low), 3))
    assert pts["high"] == pytest.approx(round(float(high), 3))


def test_every_tracked_stat_is_projected(monkeypatch):
    """The slips carry points, rebounds, assists, threes and more. A
    stat missing from the record is a leg we cannot score later."""
    from engine.stat_columns import STAT_COLUMNS
    monkeypatch.setattr(cp, "read_payload", lambda key: gamelog())
    stats = cp.projection_for("1", "2026-27")["stats"]
    for col, _label in STAT_COLUMNS:
        assert col in stats, f"{col} would be unscoreable"


# ---- refusing to invent ---------------------------------------------------
def test_a_player_with_no_cached_log_is_skipped_not_guessed(monkeypatch):
    monkeypatch.setattr(cp, "read_payload", lambda key: None)
    assert cp.projection_for("nobody", "2026-27") is None


def test_a_player_with_too_little_history_is_skipped(monkeypatch):
    """The app itself declines to lean below this; a projection on three
    games is not a claim worth scoring."""
    monkeypatch.setattr(cp, "read_payload",
                        lambda key: gamelog(n=cp.MIN_PRIOR_GAMES - 1))
    assert cp.projection_for("1", "2026-27") is None


def test_skipped_players_are_reported_not_silently_dropped(monkeypatch):
    monkeypatch.setattr(cp, "read_payload",
                        lambda key: gamelog() if key.endswith("_good_2026-27") else None)
    body, skipped = cp.build_record(["good", "bad"], "2026-27", WHEN)
    assert list(body["players"]) == ["good"]
    assert skipped == ["bad"]


def test_a_night_with_nothing_projectable_writes_nothing(monkeypatch, workspace, capsys):
    """Out of season the current log is empty. That is a quiet night,
    not a failure."""
    monkeypatch.setattr(cp, "read_payload", lambda key: None)
    assert cp.main(["--players", "1", "2", "--season", "2026-27"]) == 0
    assert "nothing to project" in capsys.readouterr().out
    assert not (workspace / "projections").exists()


def test_no_players_at_all_is_an_error(workspace, capsys):
    assert cp.main([]) == 2
    assert "no players given" in capsys.readouterr().err


# ---- the record -----------------------------------------------------------
def test_the_digest_matches_the_file_on_disk(workspace):
    body = {"captured_at": WHEN.isoformat(), "players": {"1": {"n_prior_games": 40}}}
    path, digest = cp.write_record(body, WHEN)
    assert hashlib.sha256(open(path, "rb").read()).hexdigest() == digest


def test_editing_a_recorded_projection_breaks_its_digest(workspace):
    """The property the claim rests on: a projection published in
    October cannot be quietly improved in March."""
    body = {"captured_at": WHEN.isoformat(), "players": {"1": {"projected": 20.0}}}
    path, digest = cp.write_record(body, WHEN)
    open(path, "w").write('{"players":{"1":{"projected":25.0}}}')
    assert hashlib.sha256(open(path, "rb").read()).hexdigest() != digest


def test_the_record_says_which_interval_it_used(workspace):
    """An 80% range and a 50% range are different claims; a record that
    does not say which is unscoreable."""
    body, _ = cp.build_record([], "2026-27", WHEN)
    assert body["range_nominal"] == cp.RANGE_NOMINAL
    assert body["season"] == "2026-27"
    assert body["captured_at"] == "2026-10-21T01:30:00+00:00"


# ---- reading player ids out of a line snapshot ---------------------------
def test_player_ids_are_found_wherever_they_sit(tmp_path):
    """The odds schema is not documented well enough to assume a shape,
    so this walks for anything that names a player."""
    snapshot = tmp_path / "s.json"
    snapshot.write_text(json.dumps({"response": {"data": [
        {"eventID": "a", "odds": [{"playerID": "1628983", "line": 25.5}]},
        {"eventID": "b", "players": {"x": {"statEntityID": "203999"}}},
    ]}}))
    assert cp.player_ids_from_snapshot(str(snapshot)) == ["1628983", "203999"]


def test_a_snapshot_with_no_players_yields_none(tmp_path):
    snapshot = tmp_path / "s.json"
    snapshot.write_text(json.dumps({"response": {"data": [{"eventID": "a"}]}}))
    assert cp.player_ids_from_snapshot(str(snapshot)) == []


# ---- summarising ----------------------------------------------------------
def test_summarise_copes_with_nothing_recorded(workspace, capsys):
    assert cp.summarise() == 0
    assert "no projections" in capsys.readouterr().out


def test_summarise_survives_a_damaged_record(workspace, capsys):
    cp.write_record({"players": {"1": {}, "2": {}}}, WHEN)
    os.makedirs(cp.PROJECTION_DIR, exist_ok=True)
    open(os.path.join(cp.PROJECTION_DIR, "broken.json"), "w").write("{not json")
    assert cp.summarise() == 0
    assert "2 player" in capsys.readouterr().out
