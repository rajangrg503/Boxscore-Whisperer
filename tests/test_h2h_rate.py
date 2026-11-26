import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import h2h_rate as hr  # noqa: E402


def _row(date, matchup, mins, pts, reb=5, ast=3, sid="22026"):
    return {"GAME_DATE": date, "MATCHUP": matchup, "MIN": mins, "PTS": pts, "REB": reb, "AST": ast, "SEASON_ID": sid}


def _write(tmp, pid, season, rows):
    with open(os.path.join(tmp, f"gamelog_{pid}_{season}.json"), "w") as f:
        json.dump({"data": rows}, f)


def test_boost_when_he_scores_more_per_minute(tmp_path):
    # norm: 20 pts in 40 min = 0.5/min ... v OKC 6 games of 30 in 40 -> ratio ~1.3
    rows = [_row(f"Jan {i:02d}, 2027", "MIN vs. LAL", 40, 16) for i in range(1, 21)]
    rows += [_row(f"Feb {i:02d}, 2027", "MIN @ OKC", 40, 24) for i in range(1, 7)]
    _write(tmp_path, 1, "2026-27", rows)
    r = hr.h2h(1, "OKC", "PTS", cache=str(tmp_path), seasons=("2026-27",))
    assert r["n"] == 6 and r["mean"] > 1.10 and "BOOST" in r["flag"]
    assert hr.boosted(20.0, r) == round(20.0 * min(r["mean"], hr.CAP), 1)


def test_veto_and_short_games_ignored(tmp_path):
    rows = [_row(f"Jan {i:02d}, 2027", "MIN vs. LAL", 30, 15) for i in range(1, 21)]
    rows += [_row(f"Feb {i:02d}, 2027", "MIN @ OKC", 30, 9) for i in range(1, 5)]
    rows += [_row("Mar 01, 2027", "MIN @ OKC", 8, 0)]          # under 20 min -> ignored
    _write(tmp_path, 2, "2026-27", rows)
    r = hr.h2h(2, "OKC", "PTS", cache=str(tmp_path), seasons=("2026-27",))
    assert r["n"] == 4 and r["flag"] == "VETO"


def test_no_games_no_flag(tmp_path):
    _write(tmp_path, 3, "2026-27", [_row("Jan 01, 2027", "MIN vs. LAL", 30, 15)])
    r = hr.h2h(3, "OKC", "PTS", cache=str(tmp_path), seasons=("2026-27",))
    assert r["n"] == 0 and r["flag"] is None and hr.boosted(12.0, r) == 12.0


def test_pra_value():
    assert hr._val({"PTS": 10, "REB": 5, "AST": 3}, "PRA") == 18
