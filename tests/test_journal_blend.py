"""The Journal's pregame projection follows the app's two-season rule.

Until 1 Nov 2026 daily_journal.pregame_projection kept its own copy of
the old five-game switch, so the Misses tab judged Camara (four games at
25 minutes) against last season's 33-minute log after the app had
already moved on.
"""
import datetime
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import daily_journal as dj  # noqa: E402

NIGHT = datetime.date(2026, 10, 31)


def _log(n, minutes, rebounds, start, newest_first=True):
    days = [start + datetime.timedelta(days=2 * i) for i in range(n)]
    from engine.stat_columns import STAT_COLUMNS
    base = {col: 1.0 for col, _ in STAT_COLUMNS}
    rows = [dict(base, GAME_DATE=d.strftime("%b %d, %Y"), MIN=minutes, PTS=minutes * 0.4,
                 REB=rebounds, AST=2, FGA=minutes * 0.3, _date=d) for d in days]
    frame = pd.DataFrame(rows)
    return frame.iloc[::-1].reset_index(drop=True) if newest_first else frame


def _patch(monkeypatch, cur, prev):
    monkeypatch.setattr(dj, "_gamelog",
                        lambda pid, season: cur.copy() if season == "2026-27" else prev.copy())


def test_four_games_this_season_are_not_thrown_away(monkeypatch):
    _patch(monkeypatch, _log(4, 25, 2, datetime.date(2026, 10, 21)),
           _log(80, 33, 5, datetime.date(2025, 10, 22)))
    out = dj.pregame_projection(1, NIGHT)
    assert out is not None
    assert out["_usual_min"] == 25.0               # this season's minutes, not last season's 33
    assert out["REB"][1] < 4.0                     # 2 a game now, blended with 5 last season


def test_games_on_or_after_the_night_never_count(monkeypatch):
    late = _log(3, 40, 20, NIGHT)                  # the night itself and after
    early = _log(4, 25, 2, datetime.date(2026, 10, 21))
    _patch(monkeypatch, pd.concat([late, early], ignore_index=True),
           _log(80, 33, 5, datetime.date(2025, 10, 22)))
    assert dj.pregame_projection(1, NIGHT)["_usual_min"] == 25.0


def test_a_rookie_with_four_games_is_still_refused(monkeypatch):
    _patch(monkeypatch, _log(4, 25, 2, datetime.date(2026, 10, 21)), pd.DataFrame())
    assert dj.pregame_projection(1, NIGHT) is None
