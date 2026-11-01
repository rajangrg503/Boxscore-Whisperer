"""Team bets on a Sportsbet slip: read, labelled, graded, never dropped.

On 1 Nov 2026 a 7-leg multi (three same-game multis of a team result
plus a player prop, and a single prop) was saved as a "2-leg multi":
every Match Betting and Handicap Betting row was skipped without a word,
and the slip's combined chance was worked out on the two legs left.
"""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import picks as pk  # noqa: E402

# Tesseract's reading of the real screenshot, junk and all.
SLIP_1_NOV = """fs Multi @ 13.37
iS 7 Legs + Stake $2.63
Potential Winnings $35.17
ea ~Same Game Multi @ 2.40
2 Legs ©
Boston Celtics @ Orlando Magic
Tomorrow 2 Nov 07:40
) SS (| Boston Celtics
Match Betting
ae =—Payton Pritchard Over (22.5)
AY Payton Pritchard - Pts + Reb + Ast
pa ~Same Game Multi @ 1.80
2 Legs
Brooklyn Nets @ Indiana Pacers
Tomorrow 2 Nov 09:10
Indiana Pacers
Match Betting
Indiana Pacers (-3.5)
Handicap Betting
A ~Same Game Multi @ 1.80
2 Legs
Los Angeles Clippers @ Golden State Warriors
Tomorrow 2 Nov 12:40
Golden State Warriors
Match Betting
Golden State Warriors (-1.5)
Handicap Betting
Ay Collin Murray-Boyles @ 1.72
To Record 15+ Pts + Reb
Toronto Raptors @ Los Angeles Lakers
Tomorrow 2 Nov 13:10
Bet Placed On: Sun, 1 Nov 15:22:51 AEDT""".splitlines()

PLAYERS = {"payton pritchard": (1630202, "BOS", "Payton Pritchard"),
           "collin murray-boyles": (1642856, "TOR", "Collin Murray-Boyles")}


def parse():
    return pk.parse_sportsbet(SLIP_1_NOV, players=PLAYERS, today=datetime.date(2026, 11, 1),
                              us_today=datetime.date(2026, 11, 1))


def test_every_leg_is_read_and_the_count_matches_the_slip():
    slip = parse()
    assert slip["legs_on_slip"] == 7
    assert len(slip["legs"]) == 7
    assert not any("legs but" in p for p in slip["problems"])


def test_team_legs_carry_team_market_line_and_game():
    legs = [l for l in parse()["legs"] if pk.is_team(l)]
    got = [(l["team"], l["stat"], l["line"], l["game"]) for l in legs]
    assert got == [("BOS", "WIN", None, "BOS@ORL"),
                   ("IND", "WIN", None, "BKN@IND"), ("IND", "SPREAD", -3.5, "BKN@IND"),
                   ("GSW", "WIN", None, "LAC@GSW"), ("GSW", "SPREAD", -1.5, "LAC@GSW")]
    assert legs[0]["player"] == "Boston Celtics"          # not ") SS (| Boston Celtics"
    assert all(l["us_date"] == "2026-11-01" for l in legs)
    assert all(not l["needs_review"] for l in legs)


def test_player_legs_still_read():
    props = [(l["player"], l["stat"], l["line"]) for l in parse()["legs"] if not pk.is_team(l)]
    assert props == [("Payton Pritchard", "PRA", 22.5), ("Collin Murray-Boyles", "PR", 14.5)]


def test_labels():
    assert pk.leg_label({"stat": "WIN"}) == "to win"
    assert pk.leg_label({"stat": "SPREAD", "line": -3.5}) == "-3.5 line"
    assert pk.leg_label({"stat": "SPREAD", "line": 6.5}) == "+6.5 line"
    assert pk.leg_label({"stat": "TOTAL", "line": 218.5, "side": "under"}) == "under 218.5 total points"


def _night(tmp_path, monkeypatch):
    monkeypatch.setattr(pk.dj, "JOURNAL_DIR", str(tmp_path))
    (tmp_path / "2026-11-01.json").write_text(json.dumps({"games": [
        {"home": "IND", "away": "BKN", "winner": "IND", "loser": "BKN", "margin": 3,
         "tw": {"pts": 110}, "tl": {"pts": 107}},
        {"home": "GSW", "away": "LAC", "winner": "LAC", "loser": "GSW", "margin": 4,
         "tw": {"pts": 104}, "tl": {"pts": 100}}]}))


def test_team_legs_grade_from_the_written_night(tmp_path, monkeypatch):
    _night(tmp_path, monkeypatch)
    leg = lambda **k: dict({"us_date": "2026-11-01", "pid": None}, **k)   # noqa: E731
    g = pk.grade_leg(leg(stat="WIN", team="IND", game="BKN@IND", line=None))
    assert (g["result"], g["actual"]) == ("won", 3.0)
    assert pk.grade_leg(leg(stat="SPREAD", team="IND", game="BKN@IND", line=-3.5))["result"] == "lost"
    assert pk.grade_leg(leg(stat="SPREAD", team="GSW", game="LAC@GSW", line=6.5))["result"] == "won"
    assert pk.grade_leg(leg(stat="WIN", team="GSW", game="LAC@GSW", line=None))["result"] == "lost"
    t = pk.grade_leg(leg(stat="TOTAL", team=None, game="BKN@IND", line=218.5, side="under"))
    assert (t["result"], t["actual"]) == ("won", 217.0)


def test_a_game_not_in_the_night_yet_is_pending(tmp_path, monkeypatch):
    _night(tmp_path, monkeypatch)
    g = pk.grade_leg({"stat": "WIN", "team": "BOS", "game": "BOS@ORL", "line": None,
                      "us_date": "2026-11-01", "pid": None})
    assert g["result"] == "pending"


def test_reuploading_the_same_bet_replaces_the_short_copy(tmp_path):
    old = {"price": 13.37, "placed_at": "2026-11-01T15:22:51+11:00", "picked_by": "you",
           "stake": 2.63, "legs": parse()["legs"][1:2]}
    first = pk.save_slip(dict(old), picks_dir=str(tmp_path))
    full = dict(parse(), picked_by=None)
    second = pk.save_slip(full, picks_dir=str(tmp_path))
    assert first == second
    saved = pk.load_slips(str(tmp_path))
    assert len(saved) == 1 and len(saved[0]["legs"]) == 7 and saved[0]["stake"] == 2.63


def test_slip_check_leaves_team_legs_out_of_the_chance():
    import slip_check as sc
    c = sc.check_leg({"stat": "WIN", "team": "IND", "us_date": "2026-11-01", "pid": None})
    assert c["chance"] is None and c.get("team")


def test_reasons_follow_their_leg_when_a_slip_gains_legs(tmp_path):
    full = parse()["legs"]
    short = [l for l in full if not pk.is_team(l)]           # Pritchard, Murray-Boyles
    pk.save_slip({"price": 13.37, "placed_at": "2026-11-01T15:22:51+11:00", "legs": short,
                  "reasons": {"slip": {"idea": "even games"},
                              "legs": [{"sure": "Strong", "why": "x factor"},
                                       {"sure": "Strong", "why": "Poeltl"}]}},
                 picks_dir=str(tmp_path))
    pk.save_slip(dict(parse()), picks_dir=str(tmp_path))
    saved = pk.load_slips(str(tmp_path))[0]
    by_player = {(l["player"], l["stat"]): r for l, r in zip(saved["legs"], saved["reasons"]["legs"])}
    assert by_player[("Payton Pritchard", "PRA")]["why"] == "x factor"
    assert by_player[("Collin Murray-Boyles", "PR")]["why"] == "Poeltl"
    assert by_player[("Boston Celtics", "WIN")] == {}
    assert saved["reasons"]["slip"] == {"idea": "even games"}


def test_a_player_leg_grades_from_the_written_night_when_the_cache_is_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(pk.dj, "JOURNAL_DIR", str(tmp_path))
    (tmp_path / "2026-10-31.json").write_text(json.dumps({"games": [{
        "home": "SAS", "away": "MIN", "winner": "SAS", "margin": 10,
        "lines": [{"pid": 999000001, "stat": s, "actual": a, "min": 20.1}
                  for s, a in (("PTS", 10), ("REB", 8), ("AST", 4))],
        "minutes": [{"pid": 999000001, "min": 20.1, "played": True},
                    {"pid": 999000002, "min": 0, "played": False}]}]}))
    leg = {"pid": 999000001, "stat": "PTS", "line": 19.5, "side": "under", "us_date": "2026-10-31"}
    g = pk.grade_leg(leg)
    assert (g["result"], g["actual"], g["min"]) == ("won", 10.0, 20.1)
    assert pk.grade_leg(dict(leg, stat="PRA", line=30.5, side="over"))["result"] == "lost"
    assert pk.grade_leg(dict(leg, pid=999000002))["result"] == "void"
