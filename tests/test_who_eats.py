"""The who-eats step in tools/box_score.py (2 Nov 2026)."""
import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import box_score as B  # noqa: E402


def test_position_groups():
    assert B.pos_group("C") == "C"
    assert B.pos_group("F-C") == "C"
    assert B.pos_group("C-F") == "C"
    assert B.pos_group("F") == "F"
    assert B.pos_group("F-G") == "F"
    assert B.pos_group("G-F") == "G"
    assert B.pos_group("") is None


def test_their_centre_out_lifts_our_centre_and_guards():
    c = B.lift_for("C", ["C"])
    g = B.lift_for("G", ["C"])
    assert c["REB"] > 1.10 and c["PTS"] > 1.10
    assert g["AST"] > 1.08 and g["REB"] < 1.05


def test_nothing_out_means_no_lift():
    assert B.lift_for("F", []) == {"PTS": 1.0, "REB": 1.0, "AST": 1.0}
    assert B.lift_for(None, ["C"]) == {"PTS": 1.0, "REB": 1.0, "AST": 1.0}


def test_lifts_multiply_but_are_capped():
    lift = B.lift_for("F", ["C", "F", "G"])
    assert all(v <= B.MAX_LIFT for v in lift.values())
    assert lift["AST"] > B.lift_for("F", ["G"])["AST"]


def test_us_date_from_tip_time_and_from_feed():
    assert B.us_date_of({"tipoff": "2026-11-02T00:30Z"}) == datetime.date(2026, 11, 1)
    assert B.us_date_of({}, {"date": "20261101"}) == datetime.date(2026, 11, 1)
    assert B.us_date_of({}, {}) is None


def test_back_to_back_reads_the_cache(monkeypatch):
    monkeypatch.setattr(B, "roster", lambda abbr: [{"PLAYER_ID": 1}])
    monkeypatch.setattr(B, "log", lambda pid, season: [
        {"GAME_DATE": "Nov 01, 2026", "MATCHUP": "BOS @ ORL", "MIN": 30}])
    assert B.played_day_before("BOS", datetime.date(2026, 11, 2))
    assert not B.played_day_before("BOS", datetime.date(2026, 11, 3))
    assert not B.played_day_before("ORL", datetime.date(2026, 11, 2))
    assert not B.played_day_before("BOS", None)


def test_key_players_prefers_this_season(monkeypatch):
    roster = [{"PLAYER_ID": 1, "POSITION": "C"}, {"PLAYER_ID": 2, "POSITION": "C"},
              {"PLAYER_ID": 3, "POSITION": "G"}]
    logs = {
        (1, B.CUR): [{"MATCHUP": "IND vs. BKN", "MIN": 20}] * 3,
        (2, B.CUR): [{"MATCHUP": "IND vs. BKN", "MIN": 30}] * 2,      # too few games
        (2, B.PREV): [{"MATCHUP": "IND vs. BKN", "MIN": 34}] * 60,
        (3, B.PREV): [{"MATCHUP": "IND vs. BKN", "MIN": 33}] * 50,    # out all season
    }
    monkeypatch.setattr(B, "roster", lambda abbr: roster)
    monkeypatch.setattr(B, "log", lambda pid, season: logs.get((pid, season), []))
    keys = B.key_players("IND")
    assert keys["C"] == 1          # this season's man, not last season's
    assert keys["G"] == 3          # nobody this season, so last season's



def test_tonight_tab_words():
    g = {"away": "BKN", "home": "IND",
         "context": {"BKN": {"their_key_out": ["C"], "back_to_back": False},
                     "IND": {"their_key_out": [], "back_to_back": True}}}
    notes = B.who_eats_notes(g)
    assert len(notes) == 2
    assert notes[0].startswith("IND's main centre is out, so BKN's bigs")
    assert notes[1].startswith("IND played last night")
    assert B.who_eats_notes({"away": "A", "home": "B"}) == []
