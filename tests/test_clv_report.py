import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import clv_report as c  # noqa: E402


def _snap(ts, price, line=24.5):
    return (datetime.datetime.fromisoformat(ts), {"events": [{
        "game": "IND@PHI", "start": "2026-11-15T18:10:00+00:00",
        "rows": [{"stat": "PR", "player": "Tyrese Maxey", "kind": "ou", "line": line, "over": price, "under": 1.9}]}]})


LEG = {"player": "Tyrese Maxey", "stat": "PR", "line": 24.5, "side": "over", "game": "IND@PHI", "price": 1.92}


def test_uses_snapshot_before_bet_when_there_is_one():
    snaps = [_snap("2026-11-15T04:00:00+00:00", 2.0), _snap("2026-11-15T17:40:00+00:00", 1.8)]
    out = c.leg_clv(LEG, datetime.datetime.fromisoformat("2026-11-15T05:00:00+00:00"), snaps)
    assert out["open"] == 2.0 and out["close"] == 1.8 and out["clv"] == round(2.0 / 1.8 - 1, 3)


def test_falls_back_to_the_logged_price_when_no_snapshot_precedes_the_bet():
    snaps = [_snap("2026-11-15T17:40:00+00:00", 1.8)]
    out = c.leg_clv(LEG, datetime.datetime.fromisoformat("2026-11-15T05:00:00+00:00"), snaps)
    assert out["open"] == 1.92 and out["clv"] == round(1.92 / 1.8 - 1, 3)
    assert "line_move" not in out


def test_no_closing_snapshot_means_no_clv():
    assert c.leg_clv(LEG, datetime.datetime.fromisoformat("2026-11-15T05:00:00+00:00"), []) is None
