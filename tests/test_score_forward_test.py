"""Tests for tools/score_forward_test.py -- the published number.

Everything the product is pitched on comes out of this file, so the
tests are about the ways a scorer flatters itself rather than about
the happy path:

  * a player who did not play must not score as a miss, or as a hit
  * a leg the parser did not understand must not vanish quietly
  * a whole-number line must be read the way the page reads it
  * the record must not carry the bookmaker's number
"""

import json
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import score_forward_test as sc  # noqa: E402

WHEN = datetime(2026, 10, 22, 1, 30, 0, tzinfo=timezone.utc)


def projections(players=None):
    return {
        "captured_at": WHEN.isoformat(timespec="seconds"),
        "season": "2026-27",
        "range_nominal": 0.8,
        "players": players or {"1": claim()},
    }


def claim(projected=25.0, low=17.0, high=42.0):
    return {"n_prior_games": 40,
            "stats": {"PTS": {"projected": projected, "low": low,
                              "high": high, "calibrated": True}}}


def log(date="Oct 21, 2026", pts=30):
    return {"cached_at": "x",
            "data": [{"GAME_DATE": date, "MIN": 34, "PTS": pts, "REB": 8,
                      "AST": 5, "STL": 1, "BLK": 1, "FG3M": 2, "FG3A": 5,
                      "TOV": 3, "OREB": 2}]}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "RESULT_DIR", str(tmp_path / "results"))
    monkeypatch.setattr(sc, "REPO_ROOT", str(tmp_path))
    return tmp_path


# ---- coverage -------------------------------------------------------------
def test_a_result_inside_the_range_is_scored_as_covered():
    players, totals = sc.score(projections(), [], "2026-10-21",
                               read=lambda key: log(pts=30))
    assert players["1"]["stats"]["PTS"]["covered"] is True
    assert totals["covered"] == 1 and totals["scored"] == 1


def test_a_result_outside_the_range_is_scored_as_a_miss():
    players, totals = sc.score(projections(), [], "2026-10-21",
                               read=lambda key: log(pts=55))
    assert players["1"]["stats"]["PTS"]["covered"] is False
    assert totals["covered"] == 0 and totals["scored"] == 1


def test_coverage_needs_no_snapshot_at_all():
    """The stronger claim is fully public and must not depend on an
    odds feed being up that night."""
    _players, totals = sc.score(projections(), [], "2026-10-21",
                                read=lambda key: log())
    assert totals["scored"] == 1
    assert "legs" not in totals


# ---- void -----------------------------------------------------------------
def test_a_player_who_did_not_play_is_void_not_a_miss():
    players, totals = sc.score(projections(), [], "2026-10-21",
                               read=lambda key: log(date="Oct 19, 2026"))
    assert players["1"] == {"status": "void"}
    assert totals["void_players"] == 1
    assert "scored" not in totals


def test_a_void_player_is_kept_out_of_the_percentage():
    both = {"played": claim(), "rested": claim()}
    reads = {"gamelog_played_2026-27": log(pts=30),
             "gamelog_rested_2026-27": log(date="Oct 19, 2026")}
    _players, totals = sc.score(projections(both), [], "2026-10-21",
                                read=reads.get)
    assert totals["scored"] == 1 and totals["covered"] == 1
    assert totals["void_players"] == 1


# ---- against the line -----------------------------------------------------
def test_we_are_right_when_our_lean_matches_the_result():
    legs = [{"player_id": "1", "stat": "PTS", "line": 19.5}]
    players, totals = sc.score(projections(), legs, "2026-10-21",
                               read=lambda key: log(pts=30))
    entry = players["1"]["stats"]["PTS"]
    assert entry["side_taken"] == "over" and entry["side_settled"] == "over"
    assert entry["side_correct"] is True
    assert totals["legs"] == 1 and totals["legs_correct"] == 1


def test_we_are_wrong_when_it_goes_the_other_way():
    legs = [{"player_id": "1", "stat": "PTS", "line": 19.5}]
    players, totals = sc.score(projections(), legs, "2026-10-21",
                               read=lambda key: log(pts=12))
    assert players["1"]["stats"]["PTS"]["side_correct"] is False
    assert totals["legs"] == 1 and totals["legs_correct"] == 0


def test_a_whole_number_line_is_read_as_that_number_or_more():
    """The page reads a typed 20 as "20 or more", so 20 exactly is a
    win for the over. Scoring it as a loss here would measure
    something the product does not claim."""
    legs = [{"player_id": "1", "stat": "PTS", "line": 20}]
    players, _totals = sc.score(projections({"1": claim(projected=25.0)}), legs,
                                "2026-10-21", read=lambda key: log(pts=20))
    entry = players["1"]["stats"]["PTS"]
    assert entry["side_taken"] == "over"
    assert entry["side_settled"] == "over"
    assert entry["side_correct"] is True


def test_a_projection_on_the_line_is_a_push_not_a_call():
    legs = [{"player_id": "1", "stat": "PTS", "line": 19.5}]
    _p, totals = sc.score(projections({"1": claim(projected=19.5)}), legs,
                          "2026-10-21", read=lambda key: log(pts=30))
    assert totals["legs_push"] == 1
    assert "legs" not in totals


def test_the_bookmakers_number_never_reaches_the_record(workspace):
    """The licence line. A season of nightly prop lines in a public
    repo is exactly the bulk export the terms forbid."""
    legs = [{"player_id": "1", "stat": "PTS", "line": 19.5}]
    path = str(workspace / "p.json")
    open(path, "w").write(json.dumps(projections()))
    body = sc.build_record(json.loads(open(path).read()), path, legs, None,
                           "2026-10-21", WHEN, read=lambda key: log(pts=30))
    entry = body["players"]["1"]["stats"]["PTS"]
    assert "line" not in entry and "cutoff" not in entry
    assert "19.5" not in json.dumps(body)


# ---- reading a snapshot ---------------------------------------------------
# The parser itself lives in engine/odds_snapshot.py and is tested in
# tests/test_odds_snapshot.py, against the real schema rather than the
# one this file used to guess at. What is checked here is only that the
# scorer reads its legs through that shared module -- if the scorer and
# the projection capture ever read a snapshot differently, the app
# projects one set of players and scores another.
def test_the_scorer_reads_snapshots_through_the_shared_parser():
    from engine.odds_snapshot import read as shared_read
    assert sc.legs_from_snapshot is shared_read


def test_a_spread_style_negative_line_is_skipped():
    legs = [{"player_id": "1", "stat": "PTS", "line": -4.5}]
    players, totals = sc.score(projections(), legs, "2026-10-21",
                               read=lambda key: log())
    assert "side_taken" not in players["1"]["stats"]["PTS"]
    assert "legs" not in totals


# ---- the game date --------------------------------------------------------
def test_the_game_date_is_the_us_date_not_the_capture_date():
    """Captures run from Australia. A capture stamped 01:30 UTC on the
    22nd is the evening of the 21st where the games were played."""
    assert sc.game_date_for(projections()) == "2026-10-21"


def test_an_explicit_date_wins():
    assert sc.game_date_for(projections(), "2026-10-19") == "2026-10-19"


# ---- the record -----------------------------------------------------------
def test_the_digest_matches_the_file_on_disk(workspace):
    import hashlib
    path, digest = sc.write_record({"totals": {}}, "2026-10-21")
    assert hashlib.sha256(open(path, "rb").read()).hexdigest() == digest


def test_the_record_names_what_it_was_built_from(workspace):
    """A scored night that cannot say which projection and which
    snapshot produced it is not evidence of anything."""
    proj = str(workspace / "p.json")
    open(proj, "w").write(json.dumps(projections()))
    snap = str(workspace / "s.json")
    open(snap, "w").write('{"response":[]}')
    body = sc.build_record(json.loads(open(proj).read()), proj, [], snap,
                           "2026-10-21", WHEN, read=lambda key: log())
    assert len(body["sources"]["projections_sha256"]) == 64
    assert len(body["sources"]["line_snapshot_sha256"]) == 64


def test_summarise_copes_with_nothing_scored(workspace, capsys):
    assert sc.summarise() == 0
    assert "nothing scored" in capsys.readouterr().out


def test_summarise_survives_a_damaged_record(workspace, capsys):
    sc.write_record({"totals": {"scored": 2, "covered": 1}}, "2026-10-21")
    os.makedirs(sc.RESULT_DIR, exist_ok=True)
    open(os.path.join(sc.RESULT_DIR, "broken.json"), "w").write("{not json")
    assert sc.summarise() == 0
    assert "50.0%" in capsys.readouterr().out


def test_nothing_to_score_is_an_error(workspace, capsys):
    assert sc.main([]) == 2
    assert "nothing to score" in capsys.readouterr().err
