"""tools/journal_insights.py on hand-built lines."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import journal_insights as ji  # noqa: E402


def _line(pid, stat, actual, low=10, high=20, minutes=30, usual=30, name=None):
    return {"pid": pid, "name": name or f"P{pid}", "team": "BOS", "stat": stat, "low": low,
            "mid": (low + high) / 2, "high": high, "actual": actual, "min": minutes,
            "usual_min": usual}


def _night(date, games):
    return {"date": date, "games": [{"game_id": gid, "lines": lines} for gid, lines in games]}


def test_sides_and_causes():
    frame = ji.lines_frame([_night("2026-10-21", [("g1", [
        _line(1, "PTS", 15),                          # inside
        _line(2, "PTS", 30, minutes=40),              # above, 10 more minutes -> minutes
        _line(3, "PTS", 5, minutes=29),               # below, normal minutes -> per minute
        _line(4, "REB", 2, minutes=20),               # below, 10 fewer minutes -> minutes
        _line(5, "AST", 30, usual=None),              # above, minutes unknown -> no cause
    ])])])
    assert list(frame["side"]) == ["inside", "above", "below", "below", "above"]
    assert list(frame["cause"]) == [None, "minutes", "per_minute", "minutes", None]
    c = ji.causes(frame)
    assert (c["misses"], c["minutes"], c["per_minute"]) == (3, 2, 1)
    assert (c["more_minutes_above"], c["fewer_minutes_below"]) == (1, 1)


def test_by_stat_shares():
    frame = ji.lines_frame([_night("d", [("g", [_line(i, "PTS", 15) for i in range(8)]
                                              + [_line(8, "PTS", 30), _line(9, "PTS", 1)])])])
    row = ji.by_stat(frame).iloc[0]
    assert (row["stat"], row["lines"], row["inside"], row["above"], row["below"]) == ("Points", 10, 80.0, 10.0, 10.0)


def test_players_counted_per_game_not_per_line():
    # Three stats above in ONE game is one game, not three.
    one_game = [_line(1, s, 30, name="Hot Hand") for s in ("PTS", "REB", "AST")]
    nights = [_night("d1", [("g1", one_game)]),
              _night("d2", [("g2", [_line(1, "PTS", 15, name="Hot Hand")])]),
              _night("d3", [("g3", [_line(1, "PTS", 15, name="Hot Hand")])])]
    assert ji.players(ji.lines_frame(nights)).empty          # 1 of 3 games: not a pattern
    nights.append(_night("d4", [("g4", [_line(1, "PTS", 31, name="Hot Hand")])]))
    nights.append(_night("d5", [("g5", [_line(1, "PTS", 32, name="Hot Hand")])]))
    table = ji.players(ji.lines_frame(nights))
    row = table.iloc[0]
    assert (row["Player"], row["Games"], row["Games that way"], row["Pattern"]) == \
        ("Hot Hand", 5, 3, "Beats his range")


def test_headline_names_a_lean():
    frame = ji.lines_frame([_night("d", [("g", [_line(i, "PTS", 15) for i in range(8)]
                                             + [_line(8, "PTS", 1), _line(9, "PTS", 2)])])])
    assert "run high" in ji.headline(frame, 1)
    assert ji.lines_frame([_night("d", [("g", [])])]).empty
