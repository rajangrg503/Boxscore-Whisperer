"""missing_opponents_sweep.py found the opponent-missing multiplier made
predictions worse at every strength above 0, so OPPONENT_MISSING_STRENGTH
is 0: the layer still reports who is missing, but is neutral and not
applied."""

import pandas as pd

from engine.adjustments import missing_players as mp


def test_missing_opponent_is_reported_but_not_applied(monkeypatch):
    def fake_cached_or_live(key, fetch):
        if key.startswith("player_estimated_metrics_"):
            return pd.DataFrame({"PLAYER_ID": [1628983], "E_NET_RATING": [16.9]}), "cache"
        return pd.DataFrame([{"SEASON_ID": "2025-26", "TEAM_ABBREVIATION": "OKC",
                              "MIN": 2324.0, "GP": 70.0}]), "cache"

    monkeypatch.setattr(mp, "cached_or_live", fake_cached_or_live)
    monkeypatch.setattr(mp, "get_player_id", lambda name: (1628983, name, None))
    monkeypatch.setattr(mp.time, "sleep", lambda s: None)

    result = mp.get_opponent_missing_adjustment(["Shai Gilgeous-Alexander"], "2025-26")

    assert mp.OPPONENT_MISSING_STRENGTH == 0
    assert result.applied is False
    assert result.multiplier_for("PTS") == 1.0
    assert result.sample_n == 1
    assert "Shai Gilgeous-Alexander" in result.note
    assert "context only" in result.note
