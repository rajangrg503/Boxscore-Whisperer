"""Regression test for the confirmed 'Brandon Williams' bug.

nba_api's players.find_players_by_full_name() does an unanchored regex
search across every player in league history -- active or retired --
and the old code took match[0] blindly. Verified empirically (outside
this test suite, against the real nba_api database) that "Brandon
Williams" resolves to TWO real people: id 1585 (retired, is_active=False)
and id 1630314 (active, is_active=True). match[0] returned the retired
one. This test pins that exact scenario down with a fixture so it can
never regress silently.
"""

from engine import players as players_module


def test_ambiguous_name_resolves_to_active_player(monkeypatch):
    monkeypatch.setattr(
        players_module.players,
        "find_players_by_full_name",
        lambda name: [
            {"id": 1585, "full_name": "Brandon Williams", "is_active": False},
            {"id": 1630314, "full_name": "Brandon Williams", "is_active": True},
        ],
    )
    pid, full_name, note = players_module.get_player_id("Brandon Williams")
    assert pid == 1630314
    assert full_name == "Brandon Williams"
    assert note is not None  # ambiguity must be surfaced, not silently hidden


def test_unambiguous_name_has_no_note(monkeypatch):
    monkeypatch.setattr(
        players_module.players,
        "find_players_by_full_name",
        lambda name: [{"id": 2544, "full_name": "LeBron James", "is_active": True}],
    )
    pid, full_name, note = players_module.get_player_id("LeBron James")
    assert pid == 2544
    assert note is None


def test_no_match_returns_all_none(monkeypatch):
    monkeypatch.setattr(
        players_module.players, "find_players_by_full_name", lambda name: []
    )
    pid, full_name, note = players_module.get_player_id("Nobody Real")
    assert (pid, full_name, note) == (None, None, None)
