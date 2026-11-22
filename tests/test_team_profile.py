import datetime
import importlib.util
import os

spec = importlib.util.spec_from_file_location(
    "team_profile", os.path.join(os.path.dirname(__file__), "..", "tools", "team_profile.py"))
tp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tp)


def row(gid, matchup, date, **k):
    r = {"Game_ID": gid, "MATCHUP": matchup, "GAME_DATE": date, "MIN": 30}
    for c in tp.COLS:
        r[c] = k.get(c, 0)
    return r


def two_games():
    return [
        # game 1: BOS @ SAC
        row("g1", "BOS @ SAC", "Nov 21, 2026", FGA=80, FGM=35, FG3A=59, FG3M=13, FTA=11, OREB=15, DREB=30, TOV=10, PTS=96),
        row("g1", "SAC vs. BOS", "Nov 21, 2026", FGA=85, FGM=38, FG3A=26, FG3M=7, FTA=20, OREB=10, DREB=35, TOV=14, PTS=97),
        # game 2: two players on the same team are summed; a 0-minute row is ignored
        row("g2", "BOS vs. NYK", "Nov 23, 2026", FGA=40, FG3A=20, OREB=5, DREB=15),
        row("g2", "BOS vs. NYK", "Nov 23, 2026", FGA=40, FG3A=20, OREB=5, DREB=15),
        dict(row("g2", "BOS vs. NYK", "Nov 23, 2026", FGA=99), MIN=0),
        row("g2", "NY vs. BOS", "Nov 23, 2026", FGA=80, FG3A=30, OREB=10, DREB=30),
    ]


def test_team_box_and_rates():
    games = tp.team_boxes(two_games())
    p = tp.profile(games, before=datetime.date(2026, 11, 22))
    assert p["BOS"]["n"] == 1 and p["SAC"]["n"] == 1
    assert p["BOS"]["fg3a_g"] == 59
    assert abs(p["BOS"]["fg3a_rate"] - 59 / 80) < 1e-9
    # OREB% = own OREB / (own OREB + opponent DREB)
    assert abs(p["BOS"]["oreb_pct"] - 15 / (15 + 35)) < 1e-9
    assert abs(p["SAC"]["opp_oreb_pct"] - 15 / (15 + 35)) < 1e-9
    assert abs(p["SAC"]["opp_fg3a_g"] - 59) < 1e-9


def test_summing_alias_and_zero_minutes():
    games = tp.team_boxes(two_games())
    p = tp.profile(games)
    assert "NYK" in p and "NY" not in p           # alias folded
    assert p["BOS"]["n"] == 2
    assert p["BOS"]["fg3a_g"] == (59 + 40) / 2      # 0-minute row (FGA 99) ignored


def test_last_n_and_ranks():
    games = tp.team_boxes(two_games())
    p = tp.profile(games, last=1)
    assert p["BOS"]["fg3a_g"] == 40                 # only the latest game
    rk = tp.ranks(tp.profile(games))
    assert rk["fg3a_g"]["BOS"] == 1                 # most threes
    assert rk["opp_efg"]["BOS"] in (1, 2, 3)        # lower-is-better metric still ranked


def test_main_runs_on_empty_cache(monkeypatch, capsys):
    monkeypatch.setattr(tp, "load_games", lambda season="2026-27": [])
    assert tp.main([]) == 1
