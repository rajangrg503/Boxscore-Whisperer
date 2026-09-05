"""Proves validate_schema() would have caught the BoxScoreTraditionalV2
deprecation automatically -- no live API call needed, since the whole
point is that the failure mode is reproducible from a fixture: real
old-style columns, zero rows (exactly what V2 actually returned this
week for a real, completed game).
"""

import pandas as pd

from data_watchdog.validators import validate_schema
from engine.schemas import ENDPOINT_CONTRACTS


def test_dead_v2_style_response_fails_on_missing_columns():
    contract = ENDPOINT_CONTRACTS["boxscore_traditional_v3"]
    # Real V2 column names (GAME_ID, PLAYER_ID, PLAYER_NAME, ...), zero
    # rows -- exactly what BoxScoreTraditionalV2 actually returned for a
    # real, completed 2025-26 game this week.
    dead_response = pd.DataFrame(columns=["GAME_ID", "TEAM_ID", "PLAYER_ID", "PLAYER_NAME"])
    result = validate_schema(dead_response, contract)
    assert result.passed is False
    assert "missing required columns" in result.reason


def test_correct_columns_but_too_few_rows_fails():
    contract = ENDPOINT_CONTRACTS["boxscore_traditional_v3"]
    # Right columns this time, but only 2 rows -- contract requires >= 10
    # for a real game's boxscore, so this should still be flagged.
    thin_response = pd.DataFrame([
        {"gameId": "1", "personId": 1, "firstName": "A", "familyName": "B", "points": 10},
        {"gameId": "1", "personId": 2, "firstName": "C", "familyName": "D", "points": 5},
    ])
    result = validate_schema(thin_response, contract)
    assert result.passed is False
    assert "row" in result.reason


def test_valid_response_passes():
    contract = ENDPOINT_CONTRACTS["boxscore_traditional_v3"]
    good_response = pd.DataFrame([
        {"gameId": "1", "personId": i, "firstName": "A", "familyName": "B", "points": 10}
        for i in range(15)
    ])
    result = validate_schema(good_response, contract)
    assert result.passed is True
    assert result.row_count == 15


def test_none_response_fails_gracefully():
    contract = ENDPOINT_CONTRACTS["player_career_stats"]
    result = validate_schema(None, contract)
    assert result.passed is False
