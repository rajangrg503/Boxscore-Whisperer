"""Tests for engine/adjustments/teammates.py's
get_out_redistribution_adjustment() -- the Full Matchup "mark a player
as out" feature's redistribution layer. See that function's docstring
for why it's a separate function from get_teammate_availability_adjustment
rather than a reuse of it (Game_ID set membership against the out
player's own game log, not a per-game box-score fetch).

fetch_combined_game_log is monkeypatched (not a real network/cache
call) -- these tests only exercise the pure comparison logic.
"""

import pandas as pd

from engine.adjustments import teammates as teammates_module
from engine.adjustments.teammates import get_out_redistribution_adjustment


def _row(game_id, **stats):
    row = {"Game_ID": game_id, "PTS": 20, "AST": 5, "REB": 6, "STL": 1, "BLK": 0, "FG3M": 2, "TOV": 3, "FG3A": 5}
    row.update(stats)
    return row


def test_normal_redistribution_computes_real_ratio(monkeypatch):
    # Out player played in games g0-g3 (4 games); the queried player's
    # own log has 8 games total, 4 overlapping (out player present) and
    # 4 not (out player absent) -- clears the >=3-and->=3 threshold.
    out_game_ids = [f"g{i}" for i in range(4)]

    def _fake_fetch(player_id, season):
        assert player_id == 999  # the out player's id, not the queried player's
        return pd.DataFrame({"Game_ID": out_game_ids})

    monkeypatch.setattr(teammates_module, "fetch_combined_game_log", _fake_fetch)

    # PTS is higher in the games WITHOUT the out player (real
    # redistribution signal), identical elsewhere.
    player_df = pd.DataFrame([
        _row("g0", PTS=20), _row("g1", PTS=20), _row("g2", PTS=20), _row("g3", PTS=20),
        _row("g4", PTS=28), _row("g5", PTS=28), _row("g6", PTS=28), _row("g7", PTS=28),
    ])

    result = get_out_redistribution_adjustment(1, 999, "2026-27", player_df)

    assert result.applied is True
    assert result.data_quality == "real_current"
    assert result.sample_n == 4  # 4 real games without the out player
    avg_without = 28.0
    avg_overall = player_df["PTS"].mean()
    assert result.value["PTS"] == avg_without / avg_overall
    assert result.value["PTS"] > 1.0  # production goes UP without this player, correctly signed


def test_insufficient_data_both_directions_excluded(monkeypatch):
    # Only 2 games without the out player -- below the >=3 threshold.
    out_game_ids = [f"g{i}" for i in range(6)]

    def _fake_fetch(player_id, season):
        return pd.DataFrame({"Game_ID": out_game_ids})

    monkeypatch.setattr(teammates_module, "fetch_combined_game_log", _fake_fetch)

    player_df = pd.DataFrame([_row(f"g{i}") for i in range(8)])  # g0-g7; only g6,g7 are "without"

    result = get_out_redistribution_adjustment(1, 999, "2026-27", player_df)

    assert result.applied is False
    assert result.data_quality == "unavailable"
    assert result.sample_n == 2  # honest real count, not zero and not fabricated
    assert result.value == {col: 1.0 for col, _ in teammates_module.STAT_COLUMNS}  # neutral, no fabricated adjustment
    assert "2" in result.note  # the real count appears in the note, not a different number


def test_insufficient_data_out_player_never_absent(monkeypatch):
    # Out player played in every single one of the queried player's
    # games -- zero real "without" games to compare, same honest
    # exclusion as any other thin-sample case, not a crash or a 0/0.
    def _fake_fetch(player_id, season):
        return pd.DataFrame({"Game_ID": [f"g{i}" for i in range(8)]})

    monkeypatch.setattr(teammates_module, "fetch_combined_game_log", _fake_fetch)

    player_df = pd.DataFrame([_row(f"g{i}") for i in range(8)])

    result = get_out_redistribution_adjustment(1, 999, "2026-27", player_df)

    assert result.applied is False
    assert result.sample_n == 0


def test_season_scoping_uses_the_passed_season_not_an_independent_lookup(monkeypatch):
    # The out player's own preferred season might differ from the
    # queried player's -- get_out_redistribution_adjustment must fetch
    # the out player's log for the EXACT season it's given, not
    # independently re-resolve one via resolve_season_gamelog.
    seen_seasons = []

    def _fake_fetch(player_id, season):
        seen_seasons.append(season)
        return pd.DataFrame({"Game_ID": ["g0", "g1", "g2"]})

    monkeypatch.setattr(teammates_module, "fetch_combined_game_log", _fake_fetch)

    player_df = pd.DataFrame([_row(f"g{i}") for i in range(6)])  # g0-g5

    get_out_redistribution_adjustment(1, 999, "2024-25", player_df)

    # Only the caller-supplied season was ever fetched for the out
    # player -- proves no independent season resolution happened here.
    assert seen_seasons == ["2024-25"]
