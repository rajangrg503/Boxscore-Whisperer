"""Sportsbet props override a stale ESPN 'out' (3 Nov 2026: Curry, Draymond)."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import availability as av  # noqa: E402


def _game(rows):
    injuries = [dict(r, severity=av.severity(r["status"])) for r in rows]
    return {"away": "PHX", "home": "GSW", "injuries": injuries,
            "out": [r for r in injuries if r["severity"] == "out"]}


def test_player_with_props_is_not_out():
    game = _game([{"team": "GSW", "name": "Stephen Curry", "status": "Out"},
                  {"team": "GSW", "name": "Draymond Green", "status": "Out"},
                  {"team": "GSW", "name": "Jimmy Butler III", "status": "Out"}])
    props = {"PHX@GSW": {av.norm("Stephen Curry"), av.norm("Draymond Green"),
                         av.norm("Devin Booker")}}
    av.reconcile_with_props([game], props)
    assert [r["name"] for r in game["out"]] == ["Jimmy Butler III"]
    curry = game["injuries"][0]
    assert curry["severity"] == "questionable" and curry["props_up"]


def test_no_props_for_game_changes_nothing():
    game = _game([{"team": "GSW", "name": "Stephen Curry", "status": "Out"}])
    av.reconcile_with_props([game], {})
    assert [r["name"] for r in game["out"]] == ["Stephen Curry"]


def test_other_games_props_do_not_leak():
    game = _game([{"team": "GSW", "name": "Stephen Curry", "status": "Out"}])
    av.reconcile_with_props([game], {"OKC@CLE": {av.norm("Stephen Curry")}})
    assert len(game["out"]) == 1


def test_props_by_game_reads_latest_snapshot(tmp_path):
    day = tmp_path / "2026-11-02"
    day.mkdir()
    old = {"events": [{"game": "PHX@GSW", "rows": [{"player": "Devin Booker"}]}]}
    new = {"events": [{"game": "PHX@GSW", "rows": [{"player": "Stephen Curry"},
                                                    {"player": "Draymond Green"}]}]}
    (day / "100000.json").write_text(json.dumps(old))
    (day / "175309.json").write_text(json.dumps(new))
    got = av.props_by_game("2026-11-02", str(tmp_path))
    assert got == {"PHX@GSW": {av.norm("Stephen Curry"), av.norm("Draymond Green")}}


def test_real_snapshot_had_curry():
    """The 2 Nov 17:53 snapshot: the fix would have caught it (skips if absent)."""
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "journal", "lines")
    if not os.path.exists(os.path.join(root, "2026-11-02", "175309.json")):
        return
    props = av.props_by_game("2026-11-02", root)
    assert av.norm("Stephen Curry") in props.get("PHX@GSW", set())


def test_morning_snapshot_beats_last_nights_by_save_time(tmp_path):
    """5 Nov: '093737' (morning) sorted before '204116' (the night before)."""
    day = tmp_path / "2026-11-04"
    day.mkdir()
    evening = day / "204116.json"
    evening.write_text(json.dumps({"events": [{"game": "MIL@BOS", "rows": [{"player": "Josh Hart"}]}]}))
    morning = day / "093737.json"
    morning.write_text(json.dumps({"events": [{"game": "MIL@BOS", "rows": [{"player": "Derrick White"}]}]}))
    os.utime(evening, (1_000_000, 1_000_000))
    os.utime(morning, (2_000_000, 2_000_000))
    assert av.props_by_game("2026-11-04", str(tmp_path)) == {"MIL@BOS": {av.norm("Derrick White")}}


def test_same_day_ruling_stands_over_props():
    """5 Nov: Tatum ruled out (rest) that morning; Sportsbet's props were still up."""
    game = {"away": "MIL", "home": "BOS", "tipoff": "2026-11-05T00:30Z",
            "injuries": [{"team": "BOS", "name": "Jayson Tatum", "status": "Out",
                          "severity": "out", "updated": "2026-11-04T17:48Z"},
                         {"team": "BOS", "name": "Payton Pritchard", "status": "Out",
                          "severity": "out", "updated": "2026-11-03T21:12Z"}]}
    av.reconcile_with_props([game], {"MIL@BOS": {av.norm("Jayson Tatum"), av.norm("Payton Pritchard")}})
    assert [r["name"] for r in game["out"]] == ["Jayson Tatum"]


def test_stale_snapshot_cannot_overrule_out(tmp_path):
    """10 Nov: the 9:42 rebuild read the 9:20 snapshot, which still priced Reaves."""
    day = tmp_path / "2026-11-09"
    day.mkdir()
    snap = day / "092022.json"
    snap.write_text(json.dumps({"events": [{"game": "LAL@ATL", "rows": [{"player": "Austin Reaves"}]}]}))
    os.utime(snap, (1_000_000, 1_000_000))
    now = 1_000_000 + 22 * 60
    assert av.props_by_game("2026-11-09", str(tmp_path), max_age_min=15, now=now) == {}
    assert av.props_by_game("2026-11-09", str(tmp_path), max_age_min=30, now=now) == {
        "LAL@ATL": {av.norm("Austin Reaves")}}
