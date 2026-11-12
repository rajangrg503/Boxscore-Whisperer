import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import advantage_finder as af  # noqa: E402


def _row(day, mins, pts, reb, ast, team="AAA", opp="BBB"):
    return {"_d": day, "MIN": mins, "PTS": pts, "REB": reb, "AST": ast, "_team": team, "_opp": opp}


def test_rate_uses_only_games_before_the_night():
    d1, d2, night = dt.date(2026, 11, 1), dt.date(2026, 11, 3), dt.date(2026, 11, 2)
    L = {1: [_row(d1, 30, 15, 6, 3), _row(d2, 30, 60, 6, 3)]}
    b = af.Book(L, {}, night)
    b.before[1].append(_row(d1, 30, 15, 6, 3))   # 60 minutes, so a rate exists
    assert abs(b.rate(1, "PTS") - 0.5) < 1e-9     # the 60-point night after is invisible


def test_summary_counts_ceiling_games():
    rows = [{"actual": 13, "proj": 10, "amin": 30, "min": 30},
            {"actual": 9, "proj": 10, "amin": 30, "min": 30}]
    s = af.summary(rows)
    assert "n=  2" in s and "ceiling (>=1.3x) 50.0%" in s


def test_context_is_neutral_without_history():
    af.TG = []
    assert af.context("BBB", "REB", dt.date(2026, 11, 9)) == 1.0


def test_role_overrides_win_over_archetype():
    for pid, role in af.ROLE_OVERRIDES.items():
        assert af.kind(pid) == role
    assert set(af.ROLE_OVERRIDES.values()) <= {"guard", "wing", "big"}
