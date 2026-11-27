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
    assert v_bbb["stats"]["PTS"]["upside"] >= plain["stats"]["PTS"]["upside"]                     # never a lower rung
    assert plain["stats"]["PTS"]["safe"] == 20 and plain["stats"]["PTS"]["safe_share"] >= 0.8     # "20+ points"


def test_more_minutes_and_what_the_opponent_allows_lift_the_line(his_games):
    base = gr.player_read(1, "A One", "AAA", "CCC", DAY)["stats"]["REB"]
    more = gr.player_read(1, "A One", "AAA", "CCC", DAY, minutes=36)["stats"]["REB"]
    assert more["expected"] > base["expected"]
    allows = {"REB": {"CCC": {"big": (1.30, 40)}}}
    soft = gr.player_read(1, "A One", "AAA", "CCC", DAY, allows=allows, kind=lambda pid: "big")["stats"]["REB"]
    assert soft["mult"] == pytest.approx(1.15) and "CCC allow bigs 1.30x" in soft["why"][0]     # half of what they allow


def test_the_two_opponent_views_are_averaged_not_stacked_and_thin_history_counts_for_little(his_games):
    alone = gr.player_read(1, "A One", "AAA", "BBB", DAY)["stats"]["PTS"]
    assert alone["mult"] == pytest.approx(1 + (1.26 - 1) * 4 / 12, abs=0.01)                   # 4 games: a third of the way
    allows = {"PTS": {"BBB": {"guard": (1.30, 40)}}}
    both = gr.player_read(1, "A One", "AAA", "BBB", DAY, allows=allows, kind=lambda pid: "guard")["stats"]["PTS"]
    assert len(both["why"]) == 2
    assert both["mult"] == pytest.approx(1 + ((alone["mult"] - 1) + 0.15) / 2, abs=0.005)     # the mean of the two
    assert both["mult"] < alone["mult"] * 1.15                                                # never the product
    threes = gr.player_read(1, "A One", "AAA", "BBB", DAY, allows={"FG3M": {"BBB": {"guard": (1.9, 40)}}},
                            kind=lambda pid: "guard")["stats"]["FG3M"]
    assert threes["mult"] == pytest.approx(1.12)                                              # threes capped lower


def pts_minutes(_):
    return gr.player_read(1, "A One", "AAA", "BBB", DAY)["usual_minutes"]


def test_each_pick_carries_what_he_actually_did(his_games):
    pts = gr.player_read(1, "A One", "AAA", "BBB", DAY)["stats"]["PTS"]
    assert pts["safe_record"] == {"season": "14 of 16", "v_opp": "4 of 4"}                     # 20+ points, untouched by the read
    more = gr.player_read(1, "A One", "AAA", "BBB", DAY, minutes=pts_minutes(his_games) + 6)["stats"]["PTS"]
    assert " min" in more["safe_record"]["v_opp"] or " min" in more["upside_record"]["v_opp"]   # a bigger role tonight is flagged


def test_too_few_games_gives_no_read(monkeypatch):
    monkeypatch.setattr(pc, "rows_for", lambda pid, seasons, before=None: games(3, [10, 12, 14]))
    assert gr.player_read(1, "A One", "AAA", "BBB", DAY) is None


def test_a_game_with_no_sportsbet_lines_is_still_read_from_the_rosters(his_games, monkeypatch):
    monkeypatch.setattr(gr.pb, "night_games", lambda date: ["AAA@BBB"])
    monkeypatch.setattr(gr.pb, "load_injuries", lambda date: {"teams": {"AAA": {"out": [], "dtd": [{"pid": 1, "name": "A One"}]}}})
    monkeypatch.setattr(gr, "_roster", lambda team: [("A One", 1)] if team == "AAA" else [])
    monkeypatch.setattr(gr, "opponent_allows", lambda season: (None, None))
    monkeypatch.setattr(gr, "game_shape", lambda away, home: [])
    night = gr.read_night(DAY.isoformat(), legs=[])                       # no lines saved at all
    assert list(night) == ["AAA@BBB"]
    assert [p["player"] for p in night["AAA@BBB"]["players"]] == ["A One"] and night["AAA@BBB"]["players"][0]["dtd"]
    assert night["AAA@BBB"]["picks"] and all(k["price"] is None for k in night["AAA@BBB"]["picks"])
    monkeypatch.setattr(gr.pb, "load_injuries", lambda date: {"teams": {"AAA": {"out": [{"pid": 1, "name": "A One"}], "dtd": []}}})
    assert gr.read_night(DAY.isoformat(), legs=[]) == {}                  # a player listed out is not read


def test_player_types_follow_the_listed_position(monkeypatch):
    bodies = {1: ("F", 80, 200), 2: ("F", 77, 205), 3: ("G", 79, 199), 4: ("G-F", 79, 225), 5: ("C", 83, 250),
              6: ("F", 82, 205), 7: ("F", 81, 240), 8: ("G-F", 78, 195)}
    monkeypatch.setattr(gr, "_bodies", lambda: bodies)
    assert [gr.player_type(p) for p in range(1, 9)] == ["wing", "wing", "guard", "wing", "big", "wing", "big", "guard"]


def test_picks_are_ranked_by_what_the_read_adds_and_need_volume(his_games, monkeypatch):
    monkeypatch.setattr(gr.pb, "night_games", lambda date: ["AAA@BBB"])
    monkeypatch.setattr(gr.pb, "load_injuries", lambda date: {})
    monkeypatch.setattr(gr, "_roster", lambda team: [("A One", 1)] if team == "AAA" else [])
    monkeypatch.setattr(gr, "opponent_allows", lambda season: (None, None))
    monkeypatch.setattr(gr, "game_shape", lambda away, home: [])
    picks = gr.read_night(DAY.isoformat(), legs=[])["AAA@BBB"]["picks"]
    assert picks and all("lift" in k for k in picks)
    assert [k["lift"] for k in picks] == sorted((k["lift"] for k in picks), reverse=True)
    assert all(k["lift"] >= gr.MIN_LIFT for k in picks if k["kind"] == "upside")
    read = gr.player_read(1, "A One", "AAA", "BBB", DAY)
    assert all(read["stats"][k["stat"]]["expected"] >= gr.VOLUME[k["stat"]] for k in picks)


def test_every_leg_gets_the_reads_chance_and_read_v_price():
    night = {"AAA@BBB": {"players": [{"pid": 1, "stats": {"AST": {"chance": {"1": 1.0, "2": 0.9, "3": 0.7, "4": 0.5, "5": 0.3, "6": 0.1, "7": 0.0}}}}]}}
    legs = [dict(pid=1, stat="AST", kind="alt", th=4, price=2.0), dict(pid=1, stat="AST", kind="over", th=5.5, price=4.0),
            dict(pid=1, stat="AST", kind="under", th=3.5, price=2.5), dict(pid=1, stat="AST", kind="alt", th=2, price=1.05),
            dict(pid=1, stat="AST", kind="alt", th=9, price=20.0), dict(pid=2, stat="AST", kind="alt", th=4, price=2.0)]
    gr.annotate_legs(legs, night)
    assert legs[0]["read"] == 0.48 and legs[0]["read_edge"] == -0.02              # 50% read, 2 points off, v 50%
    assert legs[1]["read"] == 0.08 and legs[1]["read_edge"] == round(0.08 - 0.25, 3)  # o5.5 = 6+
    assert legs[2]["read"] == 0.5                                                 # u3.5 = under 4: 1 - 0.5
    assert legs[3]["read"] == 0.82                                                # floor-like: 8 points off
    assert legs[4]["read"] == 0.01                                                # past his best game
    assert legs[5]["read"] is None and legs[5]["read_edge"] is None               # not read


def test_threes_list_shows_makes_v_them_and_the_best_priced_leg():
    log = [{"date": "2026-11-01", "team": "AAA", "min": 32, "made": 5, "att": 9},
           {"date": "2025-03-01", "team": "ZZZ", "min": 30, "made": 3, "att": 7}]
    night = {"AAA@BBB": {"players": [
        {"pid": 1, "player": "A One", "team": "AAA", "opp": "BBB", "minutes": 32,
         "stats": {"FG3M": {"expected": 2.5, "season_raw": 2.2, "season_att": 6.1, "v_log": log}}},
        {"pid": 2, "player": "B Two", "team": "AAA", "opp": "BBB", "minutes": 30,
         "stats": {"FG3M": {"expected": 0.6, "season_raw": 0.5, "season_att": 2.0, "v_log": log}}}]}}   # too few threes
    legs = [dict(pid=1, stat="FG3M", kind="alt", th=3, price=2.1, read_edge=0.04, read=0.52, hr_season=0.5),
            dict(pid=1, stat="FG3M", kind="alt", th=6, price=15.0, read_edge=0.08, read=0.15, hr_season=0.1),  # too long
            dict(pid=1, stat="FG3M", kind="alt", th=2, price=1.4, read_edge=-0.02, read=0.69, hr_season=0.8)]
    rows = gr.threes_table(night, legs)
    assert [r["player"] for r in rows] == ["A One"]
    r = rows[0]
    assert r["v_makes"] == [5, 3] and r["v_avg"] == 4 and r["v_other_team"] == 1 and r["season_avg"] == 2.2
    assert r["leg"]["th"] == 3 and r["leg"]["price"] == 2.1
