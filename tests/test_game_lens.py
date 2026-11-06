import importlib.util, os
spec = importlib.util.spec_from_file_location("game_lens", os.path.join(os.path.dirname(__file__), "..", "tools", "game_lens.py"))
gl = importlib.util.module_from_spec(spec); spec.loader.exec_module(gl)


def _side(pts, tov, oreb, fta, fg, fb=10):
    return {"pts": pts, "tov": tov, "oreb": oreb, "fta": fta, "fg_pct": fg, "paint": 40, "fastbreak": fb,
            "second": 10, "off_tov": 15, "fg3a": 35}


def test_possession_edge_and_leeway():
    games = [("2026-11-01", {"winner": "AAA", "loser": "BBB", "tw": _side(110, 10, 15, 30, 44.0), "tl": _side(100, 18, 8, 20, 47.0)}),
             ("2026-11-02", {"winner": "AAA", "loser": "BBB", "tw": _side(105, 12, 12, 25, 46.0), "tl": _side(99, 16, 9, 22, 45.0)})]
    p = gl.profiles(games)
    # AAA forces 17 and commits 11 (+6), boards +5, FTA +6.5*0.44
    assert abs(p["AAA"]["poss"] - (6 + 5 + 0.44 * 6.5)) < 1e-9
    assert abs(p["AAA"]["poss"] + p["BBB"]["poss"]) < 1e-9
    assert (p["AAA"]["cold_n"], p["AAA"]["cold_w"]) == (1, 1)   # out-shot once, still won
    assert (p["BBB"]["cold_n"], p["BBB"]["cold_w"]) == (1, 0)
    text = gl.lens("AAA", "BBB", p, top=2)
    assert "Possession battle leans AAA" in text
    assert "no recorded games" in gl.lens("AAA", "ZZZ", p)
