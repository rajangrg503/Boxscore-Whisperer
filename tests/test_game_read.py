"""game_read: the read is built from his own games and the matchup, never from a price."""
import datetime as dt
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import game_read as gr  # noqa: E402
import prop_context as pc  # noqa: E402

DAY = dt.date(2026, 11, 27)


def games(n, pts, minutes=30, opp="ZZZ", start=1):
    return [{"GAME_DATE": (dt.date(2026, 10, 20) + dt.timedelta(days=start + i)).strftime("%b %d, %Y"), "MATCHUP": f"AAA vs. {opp}",
             "MIN": minutes, "PTS": p, "REB": 6, "AST": 4, "FG3M": 2, "SEASON_ID": "22026"} for i, p in enumerate(pts[:n])]


@pytest.fixture
def his_games(monkeypatch):
    rows = games(12, [20, 22, 18, 24, 20, 26, 19, 21, 23, 20, 22, 25])
    rows += games(4, [30, 32, 28, 30], opp="BBB", start=20)             # he scores about 1.4x his rate v BBB
    monkeypatch.setattr(pc, "rows_for", lambda pid, seasons, before=None: sorted(rows, key=pc._date, reverse=True))
    return rows


def test_read_moves_the_expectation_by_history_and_names_why(his_games):
    plain = gr.player_read(1, "A One", "AAA", "CCC", DAY)
    v_bbb = gr.player_read(1, "A One", "AAA", "BBB", DAY)
    assert plain["stats"]["PTS"]["mult"] == 1.0 and plain["stats"]["PTS"]["why"] == []
    assert v_bbb["stats"]["PTS"]["mult"] > 1.05 and "v BBB" in v_bbb["stats"]["PTS"]["why"][0]
    assert v_bbb["stats"]["PTS"]["expected"] > plain["stats"]["PTS"]["expected"]
    assert v_bbb["stats"]["PTS"]["upside"] > plain["stats"]["PTS"]["upside"]                      # 30+ against 25+
    assert plain["stats"]["PTS"]["safe"] == 20 and plain["stats"]["PTS"]["safe_share"] >= 0.8     # "20+ points"


def test_more_minutes_and_what_the_opponent_allows_lift_the_line(his_games):
    base = gr.player_read(1, "A One", "AAA", "CCC", DAY)["stats"]["REB"]
    more = gr.player_read(1, "A One", "AAA", "CCC", DAY, minutes=36)["stats"]["REB"]
    assert more["expected"] > base["expected"]
    allows = {"REB": {"CCC": {"big": (1.30, 40)}}}
    soft = gr.player_read(1, "A One", "AAA", "CCC", DAY, allows=allows, kind=lambda pid: "big")["stats"]["REB"]
    assert soft["mult"] == pytest.approx(1.12) and "CCC allow bigs 1.30x" in soft["why"][0]     # capped


def test_too_few_games_gives_no_read(monkeypatch):
    monkeypatch.setattr(pc, "rows_for", lambda pid, seasons, before=None: games(3, [10, 12, 14]))
    assert gr.player_read(1, "A One", "AAA", "BBB", DAY) is None
