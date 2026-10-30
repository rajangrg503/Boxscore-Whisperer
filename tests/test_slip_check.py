"""tools/slip_check.py and the money side of tools/picks.py, on invented
game logs (no real slips in a public repo)."""

import datetime
import math
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import picks as pk  # noqa: E402
import slip_check as sc  # noqa: E402

NIGHT = "2026-10-30"


def _games(points, minutes, start=datetime.date(2026, 10, 1)):
    rows = []
    for i, (p, m) in enumerate(zip(points, minutes)):
        d = start + datetime.timedelta(days=2 * i)
        rows.append({"GAME_DATE": d.strftime("%b %d, %Y"), "PTS": p, "REB": 6, "AST": 3,
                     "MIN": m, "MATCHUP": "MEM vs. POR", "FG3M": 1, "STL": 1, "BLK": 0,
                     "TOV": 2, "FG3A": 3, "OREB": 1})
    return pd.DataFrame(rows)


LOGS = {
    1: _games([20, 22, 18, 25, 19, 21, 23, 17, 24, 20, 30], [32] * 11),        # steady scorer
    2: _games([4, 12, 3, 15, 2, 14, 5, 16, 3, 11], [12, 30, 10, 34, 9, 31, 14, 35, 11, 29]),
    3: _games([10, 12], [20, 22]),                                               # two games: a rookie
}


@pytest.fixture(autouse=True)
def fake_logs(monkeypatch):
    def log(pid, season):
        if season != "2026-27" or pid not in LOGS:
            return pd.DataFrame()
        f = LOGS[pid].copy()
        f["_d"] = f["GAME_DATE"].map(pk.parse_game_date)
        return f
    monkeypatch.setattr(pk, "_log", log)
    monkeypatch.setattr(sc, "defense_multiplier", lambda opp, day: (1.0, None))


def _leg(pid, line, stat="PTS", player=None):
    return {"player": player or f"P{pid}", "pid": pid, "team": "MEM", "stat": stat, "line": line,
            "us_date": NIGHT, "game": "MEM@POR", "price": None}


def test_steady_scorer_against_a_low_line():
    c = sc.check_leg(_leg(1, 15.5))
    assert c["recent"]["over"] == 10 and c["recent"]["of"] == 10
    assert c["minutes"]["steady"]
    assert c["chance"] > 0.8 and c["proj"]["mid"] > 15.5
    assert not c["flags"]


def test_unsteady_minutes_and_rarely_cleared_are_flagged():
    c = sc.check_leg(_leg(2, 13.5))
    text = " ".join(c["flags"])
    assert "Minutes have swung a lot lately (9 to 35" in text
    assert "Cleared this line in only 3 of his last 10" in text


def test_rookie_gets_recent_form_but_no_projection():
    c = sc.check_leg(_leg(3, 9.5))
    assert c["chance"] is None and c["recent"]["of"] == 2
    assert any("Too few games" in f for f in c["flags"])


def test_combo_is_rough_and_uses_the_sum():
    c = sc.check_leg(_leg(1, 25.5, stat="PRA"))
    assert c["rough"] and c["proj"]["mid"] > 25.5


def test_slip_level_warnings_and_upper_limit():
    slip = {"id": "a", "price": 10.0, "legs": [_leg(1, 15.5), _leg(1, 4.5, stat="REB"),
                                               _leg(3, 9.5)]}
    other = {"id": "b", "legs": [_leg(1, 15.5)]}
    r = sc.check_slip(slip, [other])
    flags = [f for l in r["legs"] for f in l["check"]["flags"]]
    assert sum("2 legs on P1" in f for f in flags) == 2
    assert any("Also in 1 other open slip" in f for f in flags)
    assert r["unchecked"] == 1 and r["implied"] == 0.1 and r["same_game"]
    checked = [l["check"]["chance"] for l in r["legs"] if l["check"]["chance"] is not None]
    assert r["combined"] == pytest.approx(math.prod(checked), abs=1e-4)


def test_defense_formula_matches_the_engine(monkeypatch):
    monkeypatch.undo()
    frame = pd.DataFrame({"TEAM_ID": [1610612763, 1610612757], "DEF_RATING": [120.0, 100.0],
                          "GP": [10, 10]})
    monkeypatch.setattr(sc.cache, "read_payload",
                        lambda key: {"data": frame.to_dict("records")} if "2026-27" in key else None)
    sc._defense_cache.clear()
    mult, note = sc.defense_multiplier("MEM", datetime.date(2026, 10, 30))
    assert mult == pytest.approx(1 + 0.5 * (120 - 110) / 110)
    assert "softer" in note
    sc._defense_cache.clear()


def test_calibration_groups_legs_by_the_chance_they_were_given():
    slips = [{"legs": [dict(_leg(1, 15.5), result="won"), dict(_leg(2, 13.5), result="lost")]}]
    cal = sc.calibration(slips)
    assert cal["legs"] == 2 and sum(b["legs"] for b in cal["buckets"]) == 2
    assert {f["label"] for f in cal["flags"]} == {"Unsteady minutes", "Rarely cleared lately"}


# ---- money -------------------------------------------------------------

def _slip(result, stake, price=5.0, paid=None, placed="2026-10-27T10:00:00+11:00"):
    return {"result": result, "stake": stake, "price": price, "paid": paid, "placed_at": placed}


def test_returned_uses_paid_when_given():
    assert pk.returned(_slip("won", 10)) == 50.0
    assert pk.returned(_slip("lost", 10)) == 0.0
    assert pk.returned(_slip("won", 10, paid=62.5)) == 62.5      # boosted
    assert pk.returned(_slip("pending", 10)) is None
    assert pk.returned(_slip("lost", None)) is None


def test_money_by_week():
    m = pk.money([_slip("lost", 10), _slip("won", 10, placed="2026-10-28T10:00:00+11:00"),
                  _slip("pending", 5, placed="2026-11-03T10:00:00+11:00"), _slip("lost", None)])
    assert (m["slips"], m["settled_staked"], m["returned"], m["net"], m["open_staked"]) == \
        (3, 20.0, 50.0, 30.0, 5.0)
    assert [w["week"].isoformat() for w in m["weeks"]] == ["2026-11-02", "2026-10-26"]


def test_update_slip_keeps_everything_else(tmp_path):
    slip = {"source": "sportsbet", "price": 4.0, "placed_at": "2026-10-27T10:00:00+11:00",
            "legs": [_leg(1, 15.5)]}
    sid = pk.save_slip(slip, b"img", "s.png", picks_dir=str(tmp_path))
    pk.update_slip(sid, str(tmp_path), stake=12.5)
    saved = pk.load_slips(str(tmp_path))[0]
    assert saved["stake"] == 12.5 and saved["image"] and saved["legs"][0]["line"] == 15.5


def test_parser_keeps_each_legs_game():
    lines = ["Same Game Multi @ 5.25", "2 Legs", "Golden State Warriors @ Minnesota Timberwolves",
             "Thursday, 29 Oct 11:11", "Alpha Guard", "10+ Rebounds",
             "Bet Placed On: Thu, 29 Oct 05:31:59 AEDT"]
    players = {pk._norm("Alpha Guard"): (1, "MIN", "Alpha Guard")}
    leg = pk.parse_sportsbet(lines, players, datetime.date(2026, 10, 31))["legs"][0]
    assert leg["game"] == "GSW@MIN"
    assert sc.opponent_for(leg) == "GSW"


def test_under_legs_flip_the_chance_and_recent_count():
    over = sc.check_leg(_leg(1, 15.5))
    under = sc.check_leg(dict(_leg(1, 15.5), side="under"))
    assert under["chance"] == pytest.approx(1 - over["chance"], abs=1e-3)
    assert under["recent"]["over"] == 0 and under["recent"]["under"]
    assert any("Stayed under this line in only 0" in f for f in under["flags"])


def test_thin_current_season_is_flagged():
    c = sc.check_leg(_leg(1, 15.5))          # 11 games this season: not thin
    assert "thin" not in c
    LOGS[9] = _games([20] * 6, [30] * 6)
    c = sc.check_leg(_leg(9, 15.5))
    assert c["thin"] == 6 and any("only 6 games this season" in f for f in c["flags"])
    assert sc.check_slip({"price": 2.0, "legs": [_leg(9, 15.5)]})["thin"] == 1
