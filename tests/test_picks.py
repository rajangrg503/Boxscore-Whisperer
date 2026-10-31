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


UNDERS = """Multi @ 42.56
6 Legs
Bet Return - 1 leg fails, get a Bonus Bet
TAY Alpha Guard oe (23.5) @ 1.96
Alpha Guard - Points
New York Knicks @ Philadelphia 76ers
Saturday, 31 Oct 10:40
) Bravo Wing Under (9.5) @ 1.96
Bravo Wing - Points
Los Angeles Clippers @ Minnesota Timberwolves
Saturday, 31 Oct 11:10
Bet Placed On: Fri, 30 Oct 19:48:23 AEDT""".splitlines()


def test_under_legs_and_a_misread_over():
    slip = pk.parse_sportsbet(UNDERS, PLAYERS, TODAY)
    a, b = slip["legs"]
    assert (a["side"], a["line"], a["game"]) == ("over", 23.5, "NYK@PHI")
    assert "check over or under" in a["needs_review"]          # "Over" read as "oe"
    assert (b["side"], b["line"], b["game"], b["needs_review"]) == ("under", 9.5, "LAC@MIN", [])
    assert pk.leg_label(b) == "9 or fewer points"


def test_under_grades_the_other_way(fake_logs):
    assert pk.grade_leg(dict(_leg("PTS", 15.5), side="under"))["result"] == "won"   # 14 < 15.5
    assert pk.grade_leg(dict(_leg("PTS", 13.5), side="under"))["result"] == "lost"


def test_record_splits_by_who_picked(fake_logs):
    mine = pk.grade_slip({"id": "a", "price": 2.0, "legs": [_leg("PTS", 10.5)]})
    theirs = pk.grade_slip({"id": "b", "price": 2.0, "picked_by": "boxscore whisperer",
                            "legs": [_leg("PTS", 15.5)]})
    assert pk.record([mine, theirs])["by_picker"] == {"you": (1, 1), "boxscore whisperer": (0, 1)}


# A PROPOSED (unplaced) slip, 31 Oct 2026. Three things in this layout
# broke the parser at once and every leg came back "Over", no line, no
# date:
#   1. Sportsbet writes the handicap signed -- "Under (+22.5)". Neither
#      _OVER nor _BARE_LINE allowed a sign, so the side fell through to
#      its "over" default and the line read as None.
#   2. An unplaced slip dates a game "Tomorrow 1 Nov 06:10", with no
#      weekday, and _GAME_DATE required one.
#   3. A proposed bet has no "Bet Placed On" row, which was reported as
#      a reading failure rather than as the ordinary thing it is.
PROPOSED = """PROPOSED BET
Multi @ 42.98
3 Legs
Same Game Multi @ 3.75
2 Legs
Memphis Grizzlies @ Portland Trail Blazers
Tomorrow 1 Nov 10:10
Alpha Guard Under (+22.5)
Alpha Guard - Points
Bravo Wing Over (+12.5)
Bravo Wing - Points
Charlie Big Over (+13.5) @ 1.72
Charlie Big - Rebounds
Sacramento Kings @ Oklahoma City Thunder
Tomorrow 1 Nov 11:10
THINK. IS THIS A BET YOU REALLY WANT TO
PLACE?""".splitlines()


def test_proposed_slip_reads_signed_lines_and_a_tomorrow_date():
    slip = pk.parse_sportsbet(PROPOSED, PLAYERS, TODAY)
    assert slip["proposed"] is True
    assert slip["problems"] == []                      # nothing to place yet is not a failure
    assert slip["placed_at"] is None
    assert slip["price"] == 42.98

    a, b, c = slip["legs"]
    assert (a["player"], a["side"], a["line"]) == ("Alpha Guard", "under", 22.5)
    assert (b["player"], b["side"], b["line"]) == ("Bravo Wing", "over", 12.5)
    assert (c["player"], c["side"], c["line"], c["price"]) == ("Charlie Big", "over", 13.5, 1.72)
    for leg in slip["legs"]:
        assert leg["needs_review"] == []
        assert leg["us_date"] == "2026-10-31"          # 1 Nov AEDT tip is 31 Oct in the US


def test_a_placed_slip_still_wants_its_placed_row():
    without = [l for l in PROPOSED if "PROPOSED" not in l]
    slip = pk.parse_sportsbet(without, PLAYERS, TODAY)
    assert slip["proposed"] is False
    assert "Couldn't read when the bet was placed." in slip["problems"]


# The tip time sits in small grey type and recognition does not reliably
# return the word in front of it. Requiring "Tomorrow"/"Today"/a weekday
# lost the date, and with it the leg's game and every check that needs
# one. The shape -- day, month name, clock time -- is what is always
# there, and is specific enough on its own.
@pytest.mark.parametrize("when", [
    "Tomorrow 1 Nov 10:10",      # unplaced slip, as Sportsbet writes it
    "Today 1 Nov 11:10",         # same slip, later in the day
    "Sunday, 1 Nov 10:10",       # a placed slip
    "1 Nov 10:10",               # the word dropped entirely
    "Tomorow 1 Nov 06:10",       # the word misread
])
def test_a_game_date_survives_whatever_precedes_it(when):
    lines = ["PROPOSED BET", "Multi @ 42.98", "1 Legs",
             "Memphis Grizzlies @ Portland Trail Blazers", when,
             "Alpha Guard Under (+22.5)", "Alpha Guard - Points"]
    leg = pk.parse_sportsbet(lines, PLAYERS, TODAY)["legs"][0]
    assert (leg["side"], leg["line"]) == ("under", 22.5)
    assert leg["us_date"] == "2026-10-31"      # a 1 Nov AEDT tip is 31 Oct in the US
    assert leg["needs_review"] == []


def test_reasons_save_trimmed_and_survive_a_resave(tmp_path, fake_logs):
    slip = {"price": 3.0, "legs": [_leg("PTS", 15.5), _leg("REB", 8.5)]}
    sid = pk.save_slip(dict(slip, legs=[dict(l) for l in slip["legs"]]), picks_dir=str(tmp_path))
    pk.save_reasons(sid, str(tmp_path), slip={"idea": "  pace game  ", "after": ""},
                    legs=[{"sure": "Strong", "why": "starting now", "miss": " "},
                          {"sure": None, "why": "", "miss": ""}])
    saved = pk.load_slips(str(tmp_path))[0]
    assert saved["reasons"]["slip"] == {"idea": "pace game"}
    assert saved["reasons"]["legs"] == [{"sure": "Strong", "why": "starting now"}, {}]
    assert pk.has_reasons(saved)
    # Uploading the same screenshot again keeps what you wrote.
    pk.save_slip(dict(slip, legs=[dict(l) for l in slip["legs"]]), picks_dir=str(tmp_path))
    assert pk.load_slips(str(tmp_path))[0]["reasons"]["slip"] == {"idea": "pace game"}


def test_empty_reasons_count_as_none():
    assert not pk.has_reasons({"legs": []})
    assert not pk.has_reasons({"reasons": {"slip": {}, "legs": [{}, {}]}})


def test_record_splits_by_how_sure(fake_logs):
    g = pk.grade_slip({"id": "a", "price": 2.0, "legs": [_leg("PTS", 10.5), _leg("PTS", 15.5),
                                                          _leg("PR", 22.5)],
                       "reasons": {"legs": [{"sure": "Strong"}, {"sure": "Strong"}, {}]}})
    assert pk.record([g])["by_sure"] == {"Strong": (1, 2)}


# A US "7 Pick Parlay" shared on X, 1 Nov 2026, as Vision reads it: each
# threshold alone on its row, the name and stat under it, then the game
# dated "Today". American odds, the boosted price after the struck one.
US_PARLAY = """Bet Placed
7 Pick Parlay
CASH OUT +3098 +4649
+50% PARLAY BOOST
7+
Alpha Guard Assists
MEM Grizzlies @ POR Trail Blazers • Today 3:10 PM
5+
Bravo Wing Rebounds
MEM Grizzlies @ POR Trail Blazers • Today 3:10 PM
3+
Alpha Guard Three Pointers Made
MEM Grizzlies @ POR Trail Blazers • Today 3:10 PM""".splitlines()


def test_us_parlay_layout():
    slip = pk.parse_sportsbet(US_PARLAY, PLAYERS, TODAY, us_today=datetime.date(2026, 10, 31))
    assert slip["source"] == "parlay" and slip["price"] == 47.49
    assert [(l["player"], l["stat"], l["line"], l["us_date"], l["game"]) for l in slip["legs"]] == [
        ("Alpha Guard", "AST", 6.5, "2026-10-31", "MEM@POR"),
        ("Bravo Wing", "REB", 4.5, "2026-10-31", "MEM@POR"),
        ("Alpha Guard", "FG3M", 2.5, "2026-10-31", "MEM@POR"),
    ]
    assert all(not l["needs_review"] for l in slip["legs"])
    assert slip["legs_on_slip"] == 7          # this cut-down copy has 3, so it's flagged
    assert any("says 7 legs but 3" in p for p in slip["problems"])
    assert any('says "Today"' in p for p in slip["problems"])
    assert not any("placed" in p for p in slip["problems"])


def test_american_odds():
    assert pk.american_to_decimal("+4649") == 47.49
    assert pk.american_to_decimal("-137") == 1.73
