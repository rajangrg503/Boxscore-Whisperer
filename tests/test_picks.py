"""tools/picks.py: reading Sportsbet slips and grading legs.

The slips below are invented (the repository is public; real slips stay
in journal/, which is gitignored) but they copy each Sportsbet layout
exactly, including the junk text recognition puts around names and the
stray rows it inserts between a leg's title and its stat line."""

import datetime
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import picks as pk  # noqa: E402

TODAY = datetime.date(2026, 10, 31)
PLAYERS = {pk._norm(n): (pid, team, n) for pid, team, n in [
    (1, "MEM", "Alpha Guard"), (2, "MEM", "Bravo Wing"), (3, "POR", "Charlie Big"),
    (4, "CLE", "Delta Forward"), (5, "SAC", "Echo Shooter Jr."), (6, "OKC", "Isaiah Foxtrot"),
]}

SAME_GAME = """NO)RETURN
4 Same Game Multi @ 15.75
meu 3 Legs
Memphis Grizzlies @ Portland Trail Blazers
Friday, 30 Oct 13:13
Alpha Guard
16+ Points
Bravo Wing
To Record 8+ Rebounds
Charlie Big Over 18.5
Charlie Big - Alt Pts + Reb
Bet Placed On: Fri, 30 Oct 12:55:40 AEDT
THINK. IS THIS A BET YOU REALLY WANT TO
PLACE?
Set a deposit limit""".splitlines()

MULTI_OF_SINGLES = """NO)RETURN
[S| Multi @ 23.27
= Echo Shooter Jr. Over (23.5) @ 1.96
iy ;
Echo Shooter Jr. - Pts + Reb + Ast g@
Sacramento Kings @ New Orleans Pelicans
Tuesday, 27 Oct 10:12
ws Delta Forward @ 1.47
To Score 20+ Points
Cleveland Cavaliers @ Atlanta Hawks
Wednesday, 28 Oct 11:11
Bet Placed On: Tue, 27 Oct 04:41:39 AEDT""".splitlines()

NESTED = """Same Game Multi+ @ 37.19 34.09
4 Legs
Same Game Multi @ 3.59
2 Legs
Oklahoma City Thunder @ Dallas Mavericks
Thursday, 29 Oct 12:48
an Alpha Guard ;
AY To Score 25+ Points
ge _='saiah Foxtrot
AY To Record 12+ Rebounds
Bet Placed On: Thu, 29 Oct 04:39:47 AEDT""".splitlines()


def _legs(slip):
    return [(l["player"], l["stat"], l["line"], l["us_date"]) for l in slip["legs"]]


def test_same_game_multi_all_three_leg_styles():
    slip = pk.parse_sportsbet(SAME_GAME, PLAYERS, TODAY)
    assert _legs(slip) == [
        ("Alpha Guard", "PTS", 15.5, "2026-10-29"),
        ("Bravo Wing", "REB", 7.5, "2026-10-29"),
        ("Charlie Big", "PR", 18.5, "2026-10-29"),
    ]
    assert slip["price"] == 15.75 and slip["status_on_slip"] == "lost"
    assert slip["placed_at"] == "2026-10-30T12:55:40+11:00"
    assert not slip["problems"]


def test_singles_in_a_multi_take_the_game_after_them_and_skip_junk_rows():
    slip = pk.parse_sportsbet(MULTI_OF_SINGLES, PLAYERS, TODAY)
    assert _legs(slip) == [
        ("Echo Shooter Jr.", "PRA", 23.5, "2026-10-26"),
        ("Delta Forward", "PTS", 19.5, "2026-10-27"),
    ]
    assert [l["price"] for l in slip["legs"]] == [1.96, 1.47]
    assert slip["price"] == 23.27


def test_nested_multi_and_a_mangled_name_still_match():
    slip = pk.parse_sportsbet(NESTED, PLAYERS, TODAY)
    assert _legs(slip) == [
        ("Alpha Guard", "PTS", 24.5, "2026-10-28"),
        ("Isaiah Foxtrot", "REB", 11.5, "2026-10-28"),
    ]
    assert slip["price"] == 37.19          # the whole slip, not the boosted-from or inner prices


def test_unknown_player_is_flagged_for_review_not_guessed():
    lines = SAME_GAME[:5] + ["Nobody Weknow", "16+ Points"] + SAME_GAME[-4:]
    leg = pk.parse_sportsbet(lines, PLAYERS, TODAY)["legs"][0]
    assert leg["pid"] is None and "player not recognised" in leg["needs_review"]


def test_year_rolls_back_for_a_slip_from_last_season():
    assert pk._year_for(20, 12, datetime.date(2027, 1, 5)) == 2026


def test_leg_labels():
    assert pk.leg_label({"stat": "PTS", "line": 15.5}) == "16+ points"
    assert pk.leg_label({"stat": "PRA", "line": 23.5}) == "24+ pts + reb + ast"


# ---- grading -----------------------------------------------------------

LOG = pd.DataFrame([
    {"GAME_DATE": "Oct 29, 2026", "PTS": 14, "REB": 9, "AST": 3, "MIN": 30},
    {"GAME_DATE": "Oct 27, 2026", "PTS": 22, "REB": 5, "AST": 1, "MIN": 31},
])


@pytest.fixture
def fake_logs(monkeypatch):
    def log(pid, season):
        frame = LOG.copy()
        frame["_d"] = frame["GAME_DATE"].map(pk.parse_game_date)
        return frame
    monkeypatch.setattr(pk, "_log", log)
    monkeypatch.setattr(pk, "projection", lambda pid, stat, day: (10.0, 18.0, 26.0, False))


def _leg(stat, line, date="2026-10-29"):
    return {"player": "Alpha Guard", "pid": 1, "team": "MEM", "stat": stat, "line": line,
            "us_date": date, "price": None}


def test_legs_grade_from_the_game_log(fake_logs):
    assert pk.grade_leg(_leg("PTS", 15.5))["result"] == "lost"      # 14
    assert pk.grade_leg(_leg("PR", 22.5))["result"] == "won"        # 14 + 9
    graded = pk.grade_leg(_leg("PTS", 15.5))
    assert graded["actual"] == 14 and graded["min"] == 30
    assert graded["proj"]["agrees"] is True                         # mid 18 > 15.5


def test_a_slip_needs_every_leg(fake_logs):
    slip = {"id": "x", "price": 4.0, "legs": [_leg("PR", 22.5), _leg("PTS", 15.5)]}
    g = pk.grade_slip(slip)
    assert (g["result"], g["legs_won"], g["legs_lost"]) == ("lost", 1, 1)
    rec = pk.record([g])
    assert (rec["lost_by_one"], rec["legs"], rec["legs_won"], rec["implied_wins"]) == (1, 2, 1, 0.2)


def test_game_not_in_the_logs_yet_is_pending(fake_logs):
    assert pk.grade_leg(_leg("PTS", 15.5, date="2026-10-30"))["result"] == "pending"


def test_save_load_remove(tmp_path, fake_logs):
    slip = pk.parse_sportsbet(SAME_GAME, PLAYERS, TODAY)
    sid = pk.save_slip(slip, b"png-bytes", "slip.png", picks_dir=str(tmp_path))
    assert pk.save_slip(slip, picks_dir=str(tmp_path)) == sid          # same slip, same id
    loaded = pk.load_slips(str(tmp_path))
    assert len(loaded) == 1 and (tmp_path / loaded[0]["image"]).read_bytes() == b"png-bytes"
    pk.remove_slip(sid, str(tmp_path))
    assert pk.load_slips(str(tmp_path)) == [] and not list((tmp_path / "images").iterdir())
