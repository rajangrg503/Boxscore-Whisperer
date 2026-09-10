"""Tests for engine/game_log.py's resolve_season_gamelog() -- extracted
verbatim from app.py's get_season_baseline() so
engine/adjustments/teammates.py's get_out_redistribution_adjustment()
can share the exact same "which single season represents this player
right now" rule, instead of re-deriving (and risking drifting from) it
independently. These tests are the equivalence proof for that
extraction: the branching, thresholds, and exact source-label wording
below are the same ones that used to live inline in get_season_baseline
(see app.py's git history for the pre-extraction version) -- if any of
these assertions had to change to pass, that would mean the extraction
silently altered behavior, which is exactly what this file exists to
catch.

Each test uses a distinct fake player_id. resolve_season_gamelog() and
fetch_combined_game_log() are both @st.cache_data-decorated -- reusing
a player_id across tests with different monkeypatched fakes would risk
one test transparently getting another's cached return value instead
of exercising its own fake, which would make a passing test meaningless
rather than prove anything.
"""

import pandas as pd

from engine import game_log as game_log_module
from engine.game_log import resolve_season_gamelog
from engine.season import CURRENT_SEASON, PREVIOUS_SEASON


def _gamelog_df(n):
    return pd.DataFrame({"PTS": [10] * n, "Game_ID": [f"g{i}" for i in range(n)]})


def test_uses_current_season_when_enough_games(monkeypatch):
    calls = []

    def _fake_fetch(player_id, season):
        calls.append(season)
        if season == CURRENT_SEASON:
            return _gamelog_df(5)  # exactly the >=5 threshold
        raise AssertionError("PREVIOUS_SEASON should not be fetched when CURRENT_SEASON has enough games")

    monkeypatch.setattr(game_log_module, "fetch_combined_game_log", _fake_fetch)

    df, season, source = resolve_season_gamelog(900001)
    assert season == CURRENT_SEASON
    assert len(df) == 5
    assert source == f"{CURRENT_SEASON} season so far, incl. playoffs (5 games)"
    assert calls == [CURRENT_SEASON]  # PREVIOUS_SEASON genuinely never fetched, not just unused


def test_falls_back_to_previous_season_when_too_few_current_games(monkeypatch):
    def _fake_fetch(player_id, season):
        if season == CURRENT_SEASON:
            return _gamelog_df(4)  # one below the >=5 threshold
        return _gamelog_df(70)

    monkeypatch.setattr(game_log_module, "fetch_combined_game_log", _fake_fetch)

    df, season, source = resolve_season_gamelog(900002)
    assert season == PREVIOUS_SEASON
    assert len(df) == 70
    assert source == f"{PREVIOUS_SEASON} full season, incl. playoffs (70 games)"


def test_falls_back_to_previous_season_when_current_season_fetch_raises(monkeypatch):
    def _fake_fetch(player_id, season):
        if season == CURRENT_SEASON:
            raise ConnectionError("live fetch blocked, no cache")
        return _gamelog_df(60)

    monkeypatch.setattr(game_log_module, "fetch_combined_game_log", _fake_fetch)

    df, season, source = resolve_season_gamelog(900003)
    assert season == PREVIOUS_SEASON
    assert len(df) == 60


def test_zero_games_current_season_falls_back_too(monkeypatch):
    def _fake_fetch(player_id, season):
        if season == CURRENT_SEASON:
            return _gamelog_df(0)
        return _gamelog_df(50)

    monkeypatch.setattr(game_log_module, "fetch_combined_game_log", _fake_fetch)

    df, season, _source = resolve_season_gamelog(900004)
    assert season == PREVIOUS_SEASON
    assert len(df) == 50
