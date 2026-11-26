"""A leg whose stat the box score doesn't carry must not crash grading (26 Nov 2026: slip f3m20nov,
stat PTS_FIRST3MIN, raised KeyError and took the Journal's Picks tab down)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import picks as pk  # noqa: E402

LEG = {"player": "Luka Doncic", "pid": 1629029, "team": "LAL", "stat": "PTS_FIRST3MIN", "line": 2.5,
       "side": "over", "us_date": "2026-11-20", "price": 2.3, "game": "SAC@LAL"}


def test_first_three_minutes_leg_is_unreadable_not_a_crash():
    g = pk.grade_slip({"id": "x", "legs": [LEG]})
    assert g["legs"][0]["result"] == "unreadable" and g["result"] == "pending"


def test_a_result_written_on_the_leg_by_hand_is_kept():
    g = pk.grade_slip({"id": "x", "legs": [dict(LEG, result="won", actual=5)]})
    assert g["legs"][0]["result"] == "won" and g["legs"][0]["actual"] == 5 and g["result"] == "won"


def test_an_unknown_stat_is_unreadable_too():
    assert pk.grade_leg(dict(LEG, stat="SOMETHING_NEW"))["result"] == "unreadable"
