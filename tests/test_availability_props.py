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
