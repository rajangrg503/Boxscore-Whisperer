"""Singles placed together at the same price are separate bets (1 Nov 2026)."""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import picks  # noqa: E402

PLACED = "2026-11-01T19:57:45+11:00"


def _single(player, stat, line):
    return {"source": "sportsbet", "price": 1.80, "legs_on_slip": 1, "placed_at": PLACED,
            "legs": [{"player": player, "pid": None, "team": None, "stat": stat,
                      "line": line, "side": "under", "us_date": "2026-11-01", "price": 1.80}]}


def test_singles_at_same_time_and_price_are_kept_apart(tmp_path):
    a = picks.save_slip(_single("Darius Garland", "PR", 26.5), picks_dir=str(tmp_path))
    b = picks.save_slip(_single("Collin Murray-Boyles", "PR", 15.5), picks_dir=str(tmp_path))
    assert a != b
    assert len(picks.load_slips(str(tmp_path))) == 2


def test_reparse_of_the_same_bet_still_replaces_it(tmp_path):
    first = _single("Darius Garland", "PR", 26.5)
    first["stake"] = 2.0
    a = picks.save_slip(first, picks_dir=str(tmp_path))
    again = _single("Darius Garland", "PR", 26.5)
    again["legs"][0]["line"] = 26.5
    again["legs"].append({"player": "Collin Murray-Boyles", "pid": None, "team": None,
                          "stat": "PR", "line": 15.5, "side": "under",
                          "us_date": "2026-11-01", "price": None})
    b = picks.save_slip(again, picks_dir=str(tmp_path))
    assert a == b
    slips = picks.load_slips(str(tmp_path))
    assert len(slips) == 1 and slips[0]["stake"] == 2.0
