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


# ---- scoring a night without being told which one -------------------------
# The refresh job calls --catch-up once a morning. What matters is not
# that it scores, but that it refuses to score too early: a night
# settled before the cache has the box scores records every player as
# void, and a void is permanent once written and published.
import datetime as _dt  # noqa: E402


@pytest.fixture
def catchup(tmp_path, monkeypatch):
    for name, sub in (("PROJECTION_DIR", "projections"),
                      ("SNAPSHOT_DIR", "line_snapshots"),
                      ("RESULT_DIR", "results")):
        monkeypatch.setattr(sc, name, str(tmp_path / sub))
    monkeypatch.setattr(sc, "REPO_ROOT", str(tmp_path))
    (tmp_path / "projections").mkdir()
    (tmp_path / "line_snapshots").mkdir()
    return tmp_path


def write_projection(root, captured_at, stem=None):
    body = dict(projections())
    body["captured_at"] = captured_at.isoformat(timespec="seconds")
    path = root / "projections" / f"{stem or captured_at.strftime('%Y-%m-%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(body))
    return str(path)


def write_snapshot(root, captured_at, day="2026-10-21"):
    folder = root / "line_snapshots" / day
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{captured_at.strftime('%Y-%m-%dT%H%M%SZ')}.json"
    path.write_text(json.dumps({
        "captured_at": captured_at.isoformat(timespec="seconds"),
        "response": {"data": []}}))
    return str(path)


def test_a_night_is_not_scored_before_the_cache_could_know(catchup):
    """The box scores arrive when the morning refresh fetches them.
    Scoring an hour after tip-off would mark everyone void, and that
    record is what gets published."""
    now = WHEN + _dt.timedelta(hours=2)
    write_projection(catchup, WHEN)
    assert sc.unscored(now=now) == []


def test_a_night_that_has_settled_is_scored(catchup):
    now = WHEN + _dt.timedelta(hours=sc.SETTLE_AFTER_HOURS + 1)
    write_projection(catchup, WHEN)
    pending = sc.unscored(now=now)
    assert [game_date for _p, _b, game_date in pending] == ["2026-10-21"]


def test_a_night_already_scored_is_left_alone(catchup):
    """Otherwise every morning rewrites every night it can still see,
    and a published figure that changes under you is not a record."""
    now = WHEN + _dt.timedelta(days=2)
    write_projection(catchup, WHEN)
    sc.write_record({"totals": {}}, "2026-10-21")
    assert sc.unscored(now=now) == []


def test_the_snapshot_chosen_is_the_last_of_that_evening(catchup):
    """The capture job samples across the slate precisely because the
    latest line before a tip is the only one worth calling a close."""
    write_snapshot(catchup, WHEN + _dt.timedelta(hours=1))
    last = write_snapshot(catchup, WHEN + _dt.timedelta(hours=4))
    assert sc.snapshot_for(WHEN) == last


def test_a_snapshot_from_another_night_is_not_used(catchup):
    write_snapshot(catchup, WHEN + _dt.timedelta(hours=30), day="2026-10-22")
    assert sc.snapshot_for(WHEN) is None


def test_catch_up_scores_coverage_with_no_snapshot_at_all(catchup, monkeypatch, capsys):
    """A night with no snapshot is a night with no market claim, not a
    night with no evidence."""
    now = WHEN + _dt.timedelta(days=1)
    write_projection(catchup, WHEN)
    monkeypatch.setattr(sc, "read_payload", lambda key: log(pts=30))
    assert sc.score_unscored(now=now) == 0
    out = capsys.readouterr().out
    assert "no line snapshot" in out
    assert (catchup / "results" / "2026-10-21.json").exists()


def test_catch_up_says_so_when_there_is_nothing_to_do(catchup, capsys):
    assert sc.score_unscored() == 0
    assert "nothing new to score" in capsys.readouterr().out


# ---- the money ------------------------------------------------------------
# A hit rate is not a profit claim: 55% at -140 loses and 48% at +130
# wins. These pin the ways the money figure flatters itself, which is
# the only direction anybody would ever ship by accident.
def priced_leg(over="-110", under="-110", line=19.5, player="1", stat="PTS"):
    prices = {}
    if over is not None:
        prices["over"] = over
    if under is not None:
        prices["under"] = under
    return {"player_id": player, "stat": stat, "line": line, "prices": prices}


def many(n, **kwargs):
    """n identical priced legs, spread over n players, so a night can
    clear the disclosure threshold without repeating a player."""
    return [priced_leg(player=str(i), **kwargs) for i in range(n)]


def many_projections(n, projected=25.0):
    return projections({str(i): claim(projected=projected) for i in range(n)})


def test_the_price_of_the_side_we_took_is_the_one_that_pays():
    # Over at -300 and under at +240 is the same leg and two very
    # different bets. We took the over and won it.
    legs = [priced_leg(over="-300", under="+240")]
    players, _totals = sc.score(projections(), legs, "2026-10-21",
                                read=lambda key: log(pts=30))
    pot = sc.money(players, legs)
    assert pot["all"]["legs"] == 1
    assert round(pot["all"]["profit"], 4) == round(100 / 300, 4)


def test_a_loser_costs_one_unit_whatever_the_price_was():
    legs = [priced_leg(over="+900")]
    players, _totals = sc.score(projections(), legs, "2026-10-21",
                                read=lambda key: log(pts=5))
    assert sc.money(players, legs)["all"]["profit"] == -1.0


def test_a_push_is_not_a_bet_that_lost():
    legs = [priced_leg()]
    players, _t = sc.score(projections({"1": claim(projected=19.5)}), legs,
                           "2026-10-21", read=lambda key: log(pts=30))
    assert sc.money(players, legs)["all"]["legs"] == 0


def test_an_unpriced_leg_still_counts_for_accuracy_and_not_for_money():
    legs = [{"player_id": "1", "stat": "PTS", "line": 19.5}]   # no prices
    players, totals = sc.score(projections(), legs, "2026-10-21",
                               read=lambda key: log(pts=30))
    assert totals["legs"] == 1 and totals["legs_correct"] == 1
    assert "priced_legs" not in totals
    assert players["1"]["stats"]["PTS"]["priced"] is False
    assert sc.money(players, legs)["all"]["legs"] == 0


def test_the_strong_legs_are_counted_apart_from_every_leg():
    # 25.0 projected against a 19.5 line is a real disagreement; the
    # same projection against its own number is not one, and belongs in
    # "all" only. Nobody's plan is to bet every prop in the feed.
    legs = [priced_leg(player="1", line=19.5),
            priced_leg(player="2", line=24.5)]
    body = projections({"1": claim(projected=25.0), "2": claim(projected=25.0)})
    players, totals = sc.score(body, legs, "2026-10-21",
                               read=lambda key: log(pts=30))
    assert players["1"]["stats"]["PTS"]["strong"] is True
    assert players["2"]["stats"]["PTS"]["strong"] is False
    assert totals["legs"] == 2 and totals["strong_legs"] == 1


def test_a_thin_night_withholds_its_units_rather_than_publishing_a_price():
    # One winning leg's profit IS its price. This is a licence rule.
    legs = [priced_leg()]
    players, _t = sc.score(projections(), legs, "2026-10-21",
                           read=lambda key: log(pts=30))
    block = sc.publishable_money(sc.money(players, legs))
    assert block["all"]["legs"] == 1
    assert "profit" not in block["all"]
    assert "withheld" in block["all"]


def test_a_full_night_publishes_its_units():
    n = 12
    legs = many(n)
    players, _t = sc.score(many_projections(n), legs, "2026-10-21",
                           read=lambda key: log(pts=30))
    block = sc.publishable_money(sc.money(players, legs))
    assert block["all"]["legs"] == n
    assert block["all"]["profit"] > 0
    assert "withheld" not in block["all"]


def test_no_price_ever_reaches_the_written_record(workspace):
    """The licence line again, for the money. A per-leg profit is the
    price in plain sight: 0.909 units won says -110 out loud."""
    n = 12
    legs = many(n, over="-137", under="+113")
    path = str(workspace / "p.json")
    open(path, "w").write(json.dumps(many_projections(n)))
    body = sc.build_record(json.loads(open(path).read()), path, legs, None,
                           "2026-10-21", WHEN, read=lambda key: log(pts=30))
    blob = json.dumps(body)
    assert "-137" not in blob and "113" not in blob
    for player in body["players"].values():
        for entry in player["stats"].values():
            assert set(entry) & {"price", "prices", "units", "profit"} == set()
            if "priced" in entry:
                assert entry["priced"] in (True, False)


def test_the_published_money_is_an_aggregate_and_nothing_else():
    n = 12
    legs = many(n)
    players, _t = sc.score(many_projections(n), legs, "2026-10-21",
                           read=lambda key: log(pts=30))
    block = sc.publishable_money(sc.money(players, legs))
    assert set(block["all"]) == {"legs", "staked", "profit", "roi",
                                 "break_even"}


def test_the_bar_the_legs_had_to_clear_travels_with_the_result():
    # A strike rate published against an invented break-even is the
    # dishonesty this whole apparatus exists to avoid. The real feed
    # is not -110: measured, its typical prop bar is 53.3%.
    n = 12
    legs = many(n, over="-130", under="+108")
    players, _t = sc.score(many_projections(n), legs, "2026-10-21",
                           read=lambda key: log(pts=30))
    tallied = sc.money(players, legs)["all"]
    assert tallied["break_even"] == pytest.approx(130 / 230, abs=1e-4)


def test_the_sentence_quotes_the_measured_bar_not_the_assumed_one():
    n = 12
    legs = many(n, over="-130", under="+108")
    players, _t = sc.score(many_projections(n), legs, "2026-10-21",
                           read=lambda key: log(pts=30))
    from engine import pricing
    line = pricing.summary_sentence(sc.money(players, legs)["all"],
                                    hit_rate=0.6)
    assert "56.5%" in line and "prices taken" in line
    assert "-110" not in line
