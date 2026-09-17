"""Season rollover: CURRENT/PREVIOUS derived from the date, the shared
"use this season once it has enough games" rule, the missing-opponent
layer's per-player fallback, and no rate-limit sleeps on cached reads."""

import datetime

import pandas as pd
import pytest

from engine import season as season_mod
from engine.adjustments import defense, missing_players, teammates
from engine.career_stats import resolve_season_mpg
from engine.stat_columns import STAT_COLUMNS


# ------------------------------------------------------------ season.py

def test_current_season_follows_the_calendar():
    assert season_mod.CURRENT_SEASON == season_mod.season_for_date(datetime.date.today())
    assert season_mod.PREVIOUS_SEASON == season_mod.season_offset(season_mod.CURRENT_SEASON, 1)


@pytest.mark.parametrize("day, expected", [
    (datetime.date(2026, 6, 30), "2025-26"),   # finals month: still last season
    (datetime.date(2026, 7, 1), "2026-27"),    # off-season: next season is "current"
    (datetime.date(2026, 10, 20), "2026-27"),
    (datetime.date(2027, 1, 15), "2026-27"),
    (datetime.date(2099, 12, 31), "2099-00"),  # century wrap in the label
])
def test_season_for_date_boundaries(day, expected):
    assert season_mod.season_for_date(day) == expected


def test_recent_seasons_and_offsets():
    assert season_mod.recent_seasons(4, current="2026-27") == [
        "2026-27", "2025-26", "2024-25", "2023-24"]
    assert season_mod.season_offset("2000-01", 1) == "1999-00"


# ------------------------------------------------------------ team_stats_season

@pytest.fixture
def league_stats(monkeypatch):
    frames = {}

    def fake(season):
        if season not in frames:
            raise RuntimeError("blocked and not cached")
        return frames[season]

    monkeypatch.setattr(defense, "get_league_advanced_team_stats", fake)
    clear = getattr(defense.team_stats_season, "clear", None)
    if clear:
        clear()
    yield frames
    if clear:
        clear()


def test_previous_season_until_current_has_enough_games(league_stats):
    assert defense.team_stats_season() == season_mod.PREVIOUS_SEASON          # no data at all
    league_stats[season_mod.CURRENT_SEASON] = pd.DataFrame()
    assert defense.team_stats_season() == season_mod.PREVIOUS_SEASON          # empty
    league_stats[season_mod.CURRENT_SEASON] = pd.DataFrame({"TEAM_ID": [1, 2], "GP": [3, 4]})
    assert defense.team_stats_season() == season_mod.PREVIOUS_SEASON          # 4 games


def test_current_season_once_a_team_has_five_games(league_stats):
    league_stats[season_mod.CURRENT_SEASON] = pd.DataFrame({"TEAM_ID": [1, 2], "GP": [4, 5]})
    assert defense.team_stats_season() == season_mod.CURRENT_SEASON


# ------------------------------------------------------------ resolve_season_mpg

def test_min_games_turns_a_tiny_sample_into_no_signal():
    df = pd.DataFrame([{"SEASON_ID": "2026-27", "TEAM_ABBREVIATION": "BOS", "MIN": 70.0, "GP": 2}])
    assert resolve_season_mpg(df, "2026-27") == (35.0, None)            # default unchanged
    mpg, note = resolve_season_mpg(df, "2026-27", min_games=5)
    assert mpg is None and "only 2 game(s)" in note


# ------------------------------------------------------------ missing opponents

def _career(rows):
    return pd.DataFrame([
        {"SEASON_ID": s, "TEAM_ABBREVIATION": "BOS", "MIN": m, "GP": g} for s, m, g in rows
    ])


@pytest.fixture
def opponent_layer(monkeypatch):
    """Fakes for get_opponent_missing_adjustment: career stats per name,
    a cached-or-live source label, and a sleep counter."""
    state = {"careers": {}, "source": "cached copy", "sleeps": 0}
    ids = {"Star": 1, "Rookie": 2}

    monkeypatch.setattr(missing_players, "get_player_id", lambda n: (ids[n], n, None))

    def fake_cached_or_live(key, fetch_fn):
        if key.startswith("player_estimated_metrics_"):
            return pd.DataFrame(), state["source"]
        pid = int(key.rsplit("_", 1)[1])
        name = {v: k for k, v in ids.items()}[pid]
        return state["careers"][name], state["source"]

    def fake_sleep(_seconds):
        state["sleeps"] += 1

    monkeypatch.setattr(missing_players, "cached_or_live", fake_cached_or_live)
    monkeypatch.setattr(missing_players.time, "sleep", fake_sleep)
    return state


def test_early_season_uses_last_seasons_minutes_and_says_so(opponent_layer):
    cur, prev = season_mod.CURRENT_SEASON, season_mod.PREVIOUS_SEASON
    opponent_layer["careers"]["Star"] = _career([(prev, 2800.0, 80), (cur, 70.0, 2)])
    result = missing_players.get_opponent_missing_adjustment(["Star"], cur)
    assert result.sample_n == 1
    assert f"Star (35.0 MPG in {prev}" in result.note


def test_current_season_minutes_once_he_has_enough_games(opponent_layer):
    cur, prev = season_mod.CURRENT_SEASON, season_mod.PREVIOUS_SEASON
    opponent_layer["careers"]["Star"] = _career([(prev, 2800.0, 80), (cur, 180.0, 6)])
    result = missing_players.get_opponent_missing_adjustment(["Star"], cur)
    assert "Star (30.0 MPG," in result.note


def test_previous_season_call_is_unchanged(opponent_layer):
    prev = season_mod.PREVIOUS_SEASON
    opponent_layer["careers"]["Rookie"] = _career([(prev, 20.0, 2)])
    result = missing_players.get_opponent_missing_adjustment(["Rookie"], prev)
    assert "Rookie (10.0 MPG," in result.note        # 2 games still counts, as before


def test_no_sleep_for_cached_reads_one_per_live_fetch(opponent_layer):
    prev = season_mod.PREVIOUS_SEASON
    for name in ("Star", "Rookie"):
        opponent_layer["careers"][name] = _career([(prev, 1000.0, 40)])
    missing_players.get_opponent_missing_adjustment(["Star", "Rookie"], prev)
    assert opponent_layer["sleeps"] == 0
    opponent_layer["source"] = "live"
    missing_players.get_opponent_missing_adjustment(["Star", "Rookie"], prev)
    assert opponent_layer["sleeps"] == 2


# ------------------------------------------------------------ missing teammates

def test_teammate_layer_does_not_sleep_on_cached_box_scores(monkeypatch):
    sleeps = []
    monkeypatch.setattr(teammates.time, "sleep", lambda s: sleeps.append(s))
    games = pd.DataFrame({
        "Game_ID": [f"g{i}" for i in range(8)],
        **{col: [10.0] * 8 for col, _ in STAT_COLUMNS},
    })

    def box(key, _fetch):
        i = int(key.rsplit("g", 1)[1])
        names = ["Guard", "Other"] if i % 2 else ["Other"]  # teammate present in odd games
        first, family = zip(*(n.split(" ", 1) if " " in n else (n, "X") for n in names))
        return pd.DataFrame({"firstName": first, "familyName": family}), "cached copy"

    monkeypatch.setattr(teammates, "cached_or_live", box)
    teammates.get_teammate_availability_adjustment(1, ["Guard X"], "2025-26", df=games)
    assert sleeps == []
