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


def test_a_teammate_of_another_archetype_is_not_measured(monkeypatch):
    """An absent guard does not change what a wing does, so his absence
    is dropped with a note rather than measured -- Avdija's shot volume
    per 36 was identical with and without Sharpe over 71 games. The
    engine must say so, not return a neutral 1.0 that reads as 'checked,
    no difference'."""
    from engine import archetype
    monkeypatch.setattr(archetype, "of_player", lambda pid: "wing")
    monkeypatch.setattr(archetype, "of_name",
                        lambda n: {"Guard Man": "guard", "Wing Man": "wing"}.get(n))
    df = pd.DataFrame({"Game_ID": [f"g{i}" for i in range(6)],
                       "PTS": [20] * 6, "REB": [5] * 6, "AST": [3] * 6,
                       "FG3M": [2] * 6, "STL": [1] * 6, "BLK": [0] * 6})
    out = get_teammate_availability_adjustment(1, ["Guard Man"], "2026-27", df=df)
    assert out.applied is False
    assert "Guard Man (guard) not counted" in out.note
    assert "does not take shots from a guard" in out.note
    assert all(v == 1.0 for v in out.value.values())


def test_a_same_archetype_teammate_still_goes_through_to_the_measurement(monkeypatch):
    from engine import archetype
    monkeypatch.setattr(archetype, "of_player", lambda pid: "wing")
    monkeypatch.setattr(archetype, "of_name", lambda n: "wing")
    # No box scores reachable in a test, so the measurement itself cannot
    # run; what matters is that the gate let the name through rather than
    # answering "no same-position teammate is missing".
    monkeypatch.setattr(teammates_module, "cached_or_live",
                        lambda key, fn: (_ for _ in ()).throw(RuntimeError("offline")))
    df = pd.DataFrame({"Game_ID": [f"g{i}" for i in range(6)],
                       "PTS": [20] * 6, "REB": [5] * 6, "AST": [3] * 6,
                       "FG3M": [2] * 6, "STL": [1] * 6, "BLK": [0] * 6})
    out = get_teammate_availability_adjustment(1, ["Wing Man"], "2026-27", df=df)
    assert "No same-position teammate" not in out.note
