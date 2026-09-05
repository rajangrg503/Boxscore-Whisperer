"""Regression tests for resolve_season_mpg() -- the pure helper that
replaced the inlined season-row/GP/MPG logic in
get_opponent_missing_adjustment().

Confirmed bugs this pins down:
1. A 0-GP season (injured/suspended/two-way all year) used to silently
   divide 0/0 -> NaN via numpy (no exception raised), poisoning every
   other missing player's contribution to the same running sum.
2. A player traded mid-season has one row per team stint plus a
   combined "TOT" row for that season; taking the first matching row
   blindly could grab a single stint's partial totals instead of the
   season total.
"""

import pandas as pd

from engine.career_stats import resolve_season_mpg


def test_zero_gp_season_returns_none_not_nan():
    df = pd.DataFrame([
        {"SEASON_ID": "2025-26", "TEAM_ABBREVIATION": "LAL", "MIN": 0.0, "GP": 0.0},
    ])
    mpg, note = resolve_season_mpg(df, "2025-26")
    assert mpg is None
    assert "0 games played" in note


def test_traded_player_uses_tot_row_not_first_stint():
    df = pd.DataFrame([
        {"SEASON_ID": "2025-26", "TEAM_ABBREVIATION": "LAL", "MIN": 300.0, "GP": 20},
        {"SEASON_ID": "2025-26", "TEAM_ABBREVIATION": "BOS", "MIN": 200.0, "GP": 15},
        {"SEASON_ID": "2025-26", "TEAM_ABBREVIATION": "TOT", "MIN": 500.0, "GP": 35},
    ])
    mpg, note = resolve_season_mpg(df, "2025-26")
    assert mpg == 500.0 / 35  # TOT row, not the LAL-only 300/20
    assert note is None


def test_traded_player_without_tot_row_falls_back_to_last_stint():
    df = pd.DataFrame([
        {"SEASON_ID": "2025-26", "TEAM_ABBREVIATION": "LAL", "MIN": 300.0, "GP": 20},
        {"SEASON_ID": "2025-26", "TEAM_ABBREVIATION": "BOS", "MIN": 200.0, "GP": 15},
    ])
    mpg, note = resolve_season_mpg(df, "2025-26")
    assert mpg == 200.0 / 15  # no TOT row present -- falls back to most recent stint
    assert note is None


def test_normal_single_row_season():
    df = pd.DataFrame([
        {"SEASON_ID": "2024-25", "TEAM_ABBREVIATION": "DEN", "MIN": 2000.0, "GP": 70},
        {"SEASON_ID": "2025-26", "TEAM_ABBREVIATION": "DEN", "MIN": 600.0, "GP": 20},
    ])
    mpg, note = resolve_season_mpg(df, "2025-26")
    assert mpg == 600.0 / 20
    assert note is None


def test_missing_season_falls_back_to_most_recent_row():
    df = pd.DataFrame([
        {"SEASON_ID": "2023-24", "TEAM_ABBREVIATION": "MIA", "MIN": 1800.0, "GP": 60},
    ])
    mpg, note = resolve_season_mpg(df, "2025-26")  # season not present in df at all
    assert mpg == 1800.0 / 60
    assert note is None
