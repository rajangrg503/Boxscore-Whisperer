"""Tests for the df= injection mechanism added to
engine/adjustments/teammates.py's get_teammate_availability_adjustment()
for the backtesting project. Doesn't re-verify the pre-existing
statistical logic (matching-games detection, heavy/light split, etc.)
-- only that df=None still fetches exactly as before, and df=<a
dataframe> genuinely bypasses the fetch instead of being silently
ignored."""

import pandas as pd

from engine.adjustments import teammates as teammates_module
from engine.adjustments.teammates import get_teammate_availability_adjustment


def test_default_df_none_still_fetches_as_before(monkeypatch):
    called = {}

    def _fake_fetch(player_id, season):
        called["yes"] = True
        return pd.DataFrame({"Game_ID": []})

    monkeypatch.setattr(teammates_module, "fetch_combined_game_log", _fake_fetch)

    get_teammate_availability_adjustment(2544, ["Some Player"], "2023-24")
    assert called.get("yes") is True


def test_injected_df_bypasses_fetch_entirely(monkeypatch):
    def _fetch_should_not_be_called(*args, **kwargs):
        raise AssertionError("fetch_combined_game_log must not be called when df is provided")

    monkeypatch.setattr(teammates_module, "fetch_combined_game_log", _fetch_should_not_be_called)

    empty_df = pd.DataFrame({"Game_ID": []})
    result = get_teammate_availability_adjustment(2544, ["Some Player"], "2023-24", df=empty_df)

    # Reaching this line at all (without the monkeypatched fetch
    # raising) proves the injected df was used. The empty df also
    # produces the honest "not enough games" neutral result, same as
    # any other insufficient-sample case.
    assert result.applied is False
    assert result.sample_n == 0
