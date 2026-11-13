"""Line snapshots and closing-line value, with Sportsbet faked."""
import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import line_snapshot as ls  # noqa: E402

START = datetime.datetime(2026, 11, 1, 20, 30, tzinfo=datetime.timezone.utc)   # 07:30 Sydney


def _fake(monkeypatch, line, alt_price):
    comp = {"events": [{"id": 1, "name": "Boston Celtics At Orlando Magic",
                        "startTime": int(START.timestamp())}]}
    markets = {
        783: [{"name": "Collin Murray-Boyles - Pts + Reb", "selections": [
                  {"name": "Collin Murray-Boyles Over", "unformattedHandicap": str(line),
                   "price": {"winPrice": 1.85}},
                  {"name": "Collin Murray-Boyles Under", "unformattedHandicap": str(line),
                   "price": {"winPrice": 1.9}}]},
              {"name": "To Record 15+ Pts + Reb", "selections": [
                  {"name": "Collin Murray-Boyles", "price": {"winPrice": alt_price}}]},
              {"name": "Collin Murray-Boyles - Alt Pts + Reb", "selections": []}],
        782: [{"name": "Payton Pritchard - Pts + Reb + Ast", "selections": [
                  {"name": "Payton Pritchard Over", "unformattedHandicap": "22.5",
                   "price": {"winPrice": 1.87}},
                  {"name": "Payton Pritchard Under", "unformattedHandicap": "22.5",
                   "price": {"winPrice": 1.89}}]}],
    }

    def get(path):
        if path.startswith("Competitions/"):
            return comp
        g = int(path.split("MarketGroupings/")[1].split("/")[0])
        return markets.get(g, [])
    monkeypatch.setattr(ls, "_get", get)


def test_snapshot_reads_over_unders_and_thresholds(tmp_path, monkeypatch):
    _fake(monkeypatch, 13.5, 1.72)
    paths = ls.snapshot(now=START - datetime.timedelta(hours=5), out_dir=str(tmp_path))
    assert list(paths) == ["2026-11-01"]
    snap = ls._snapshots("2026-11-01", str(tmp_path))[0]
    rows = snap["events"][0]["rows"]
    assert snap["events"][0]["game"] == "BOS@ORL"
    assert {"player": "Collin Murray-Boyles", "stat": "PR", "kind": "ou", "line": 13.5,
            "over": 1.85, "under": 1.9, "game": "BOS@ORL"} in rows
    assert any(r["kind"] == "alt" and r["threshold"] == 15 and r["price"] == 1.72 for r in rows)


def test_closing_window_and_started_games(tmp_path, monkeypatch):
    _fake(monkeypatch, 13.5, 1.72)
    assert ls.snapshot(closing_within=45, now=START - datetime.timedelta(hours=2),
                       out_dir=str(tmp_path)) == {}
    assert ls.snapshot(closing_within=45, now=START + datetime.timedelta(minutes=5),
                       out_dir=str(tmp_path)) == {}
    assert ls.snapshot(closing_within=45, now=START - datetime.timedelta(minutes=30),
                       out_dir=str(tmp_path))


def test_clv_uses_the_last_pre_tip_snapshot(tmp_path, monkeypatch):
    _fake(monkeypatch, 13.5, 1.72)
    ls.snapshot(now=START - datetime.timedelta(hours=5), out_dir=str(tmp_path))
    _fake(monkeypatch, 14.5, 1.55)                      # the market moved toward the over
    ls.snapshot(now=START - datetime.timedelta(minutes=20), out_dir=str(tmp_path))
    leg = {"player": "Collin Murray-Boyles", "stat": "PR", "line": 14.5, "side": "over",
           "us_date": "2026-11-01", "game": "BOS@ORL", "price": 1.72, "market": "alt"}
    close = ls.closing_line(leg, str(tmp_path))
    assert close["line"] == 14.5 and close["alt_price"] == 1.55
    v = ls.clv(leg, close)
    assert v == {"price": round(1.72 / 1.55 - 1, 3), "close_price": 1.55}   # 11% better odds
    assert ls.clv(dict(leg, price=None), close) is None     # an N+ leg in a multi: nothing honest
    over = dict(leg, line=13.5, price=None, market="ou")
    assert ls.clv(over, close)["line"] == 1.0
    under = dict(over, side="under")
    assert ls.clv(under, close)["line"] == -1.0


def test_points_and_threes_thresholds_are_saved(tmp_path, monkeypatch):
    """Sportsbet names these "To Score 20+ Points" and "2+ Made Threes", not
    "To Record ...". They were silently dropped until 13 Nov 2026."""
    comp = {"events": [{"id": 1, "name": "Boston Celtics At Orlando Magic",
                        "startTime": int(START.timestamp())}]}
    markets = {
        567: [{"name": "To Score 20+ Points", "selections": [
                  {"name": "Jayson Tatum", "price": {"winPrice": 1.28}}]},
              {"name": "1st Quarter - To Score 6+ Points", "selections": [
                  {"name": "Jayson Tatum", "price": {"winPrice": 1.31}}]}],
        710: [{"name": "3+ Made Threes", "selections": [
                  {"name": "Jayson Tatum", "price": {"winPrice": 2.0}}]},
              {"name": "1st Quarter - To Record 2+ Made Threes", "selections": [
                  {"name": "Jayson Tatum", "price": {"winPrice": 4.0}}]}],
        568: [{"name": "To Record 8+ Rebounds", "selections": [
                  {"name": "Jayson Tatum", "price": {"winPrice": 1.5}}]}],
    }

    def get(path):
        if path.startswith("Competitions/"):
            return comp
        g = int(path.split("MarketGroupings/")[1].split("/")[0])
        return markets.get(g, [])
    monkeypatch.setattr(ls, "_get", get)
    ls.snapshot(now=START - datetime.timedelta(hours=5), out_dir=str(tmp_path))
    rows = ls._snapshots("2026-11-01", str(tmp_path))[0]["events"][0]["rows"]
    alts = {(r["stat"], r["threshold"], r["price"]) for r in rows if r["kind"] == "alt"}
    assert alts == {("PTS", 20, 1.28), ("FG3M", 3, 2.0), ("REB", 8, 1.5)}
