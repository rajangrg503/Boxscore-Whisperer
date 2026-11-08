import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import team_defense as td  # noqa: E402


def _g(gid, team, opp, day, pts, opp_pts, home):
    return {"gid": gid, "team": team, "opp": opp, "date": day, "home": home, "pts": pts,
            "fg3a": 30, "fg3m": 11, "fga": 88, "fgm": 40, "poss": 100.0, "opp_pts": opp_pts}


def test_good_defence_rates_lower_and_projects_fewer_points():
    d = datetime.date(2026, 11, 1)
    games = []
    for i in range(4):
        # A holds everyone to 100; B allows 120
        games += [_g(f"a{i}", "A", "C", d, 110, 100, True), _g(f"a{i}", "C", "A", d, 100, 110, False),
                  _g(f"b{i}", "B", "C", d, 110, 120, True), _g(f"b{i}", "C", "B", d, 120, 110, False)]
    R = td.ratings(games, before=datetime.date(2026, 11, 2), halflife=100)
    assert R["def"]["A"] < R["def"]["B"]
    c_vs_a, _ = td.project(R, "C", "A")
    c_vs_b, _ = td.project(R, "C", "B")
    assert c_vs_a < c_vs_b


def test_before_excludes_later_games():
    d1, d2 = datetime.date(2026, 11, 1), datetime.date(2026, 11, 5)
    games = [_g("x", "A", "B", d1, 100, 90, True), _g("x", "B", "A", d1, 90, 100, False),
             _g("y", "A", "B", d2, 80, 130, True), _g("y", "B", "A", d2, 130, 80, False)]
    R = td.ratings(games, before=datetime.date(2026, 11, 3))
    assert R["n"]["A"] == 1
