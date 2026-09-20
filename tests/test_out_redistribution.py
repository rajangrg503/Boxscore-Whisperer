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
    row = {"Game_ID": game_id, "PTS": 20, "AST": 5, "REB": 6, "STL": 1, "BLK": 0, "FG3M": 2, "TOV": 3, "FG3A": 5, "OREB": 2}
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
    raw = avg_without / avg_overall
    n = 4
    expected = 1 + (raw - 1) * n / (n + teammates_module.OUT_RATIO_SHRINK_K)
    assert result.value["PTS"] == expected
    assert 1.0 < result.value["PTS"] < raw  # correctly signed, and shrunk toward 1


def test_shrink_ratio_behaviour():
    shrink = teammates_module.shrink_ratio
    k = teammates_module.OUT_RATIO_SHRINK_K
    assert shrink(1.5, 0) == 1.0              # no games -> no effect
    assert shrink(1.0, 10) == 1.0             # no difference stays no difference
    assert shrink(0.0, 5) == 1 - 5 / (5 + k)  # a zero ratio no longer zeroes the stat
    assert abs(shrink(1.5, 10_000) - 1.5) < 0.01  # huge samples keep their ratio


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


def test_pooled_prior_from_out_player_averages(monkeypatch):
    # Out player averages 30 PTS / 6 AST / 9 FG3A: prior = 1 + beta * avg / league team avg.
    out_rows = [{"Game_ID": f"g{i}", "PTS": 30, "AST": 6, "FG3A": 9, "REB": 5} for i in range(4)]

    monkeypatch.setattr(teammates_module, "fetch_combined_game_log",
                        lambda pid, season: pd.DataFrame(out_rows))

    player_df = pd.DataFrame([
        _row("g0", PTS=20), _row("g1", PTS=20), _row("g2", PTS=20), _row("g3", PTS=20),
        _row("g4", PTS=28), _row("g5", PTS=28), _row("g6", PTS=28), _row("g7", PTS=28),
    ])
    result = get_out_redistribution_adjustment(1, 999, "2026-27", player_df)

    beta, league, k = teammates_module.OUT_PRIOR_BETA, teammates_module.LEAGUE_TEAM_PER_GAME, teammates_module.OUT_RATIO_SHRINK_K
    prior_pts = 1 + beta["PTS"] * 30 / league["PTS"]
    raw = 28.0 / player_df["PTS"].mean()
    assert result.value["PTS"] == prior_pts + (raw - prior_pts) * 4 / (4 + k)
    prior_ast = 1 + beta["AST"] * 6 / league["AST"]
    # AST identical with/without -> raw 1.0, pulled most of the way to the prior
    assert abs(result.value["AST"] - (prior_ast + (1.0 - prior_ast) * 4 / (4 + k))) < 1e-12
    assert 1.0 < result.value["AST"] < prior_ast
    assert result.value["REB"] == 1.0                     # no beta for REB -> prior 1, raw 1


def test_thin_sample_falls_back_to_pooled_prior(monkeypatch):
    out_rows = [{"Game_ID": f"g{i}", "PTS": 30, "AST": 6, "FG3A": 9} for i in range(7)]
    monkeypatch.setattr(teammates_module, "fetch_combined_game_log",
                        lambda pid, season: pd.DataFrame(out_rows))
    player_df = pd.DataFrame([_row(f"g{i}") for i in range(8)])  # only g7 is "without"

    result = get_out_redistribution_adjustment(1, 999, "2026-27", player_df)

    assert result.applied is True
    assert result.data_quality == "real_thin_sample"
    assert result.sample_n == 1
    assert result.value["PTS"] == 1 + teammates_module.OUT_PRIOR_BETA["PTS"] * 30 / teammates_module.LEAGUE_TEAM_PER_GAME["PTS"]
    assert result.value["BLK"] == 1.0
    assert "league-wide pickup" in result.note


# ---- when the out player's log cannot be fetched at all -------------------
# THE CRASH, 21 Sep 2026. This was the only fetch in teammates.py that
# was not wrapped. A ConnectionError from it escaped the adjustment
# layer entirely -- up through predict_player_vs_opponent,
# build_team_projection and render_team_projection to the top of
# app.py -- and Full Matchup showed a traceback instead of a
# projection.
#
# Not a rare corner. On Streamlit Cloud stats.nba.com is blocked, so
# the first lookup of any session sets _live_nba_api_blocked and every
# later fetch becomes cache-or-raise. Anyone signed, traded or called
# up since the last cache refresh has no cached log, and marking them
# out took the page down rather than saying so.
def _raising_fetch(exc):
    def _fetch(player_id, season):
        raise exc
    return _fetch


def test_an_unfetchable_out_player_skips_the_layer_rather_than_raising(
        monkeypatch):
    monkeypatch.setattr(
        teammates_module, "fetch_combined_game_log",
        _raising_fetch(ConnectionError(
            "Live NBA data fetch skipped -- already confirmed unreachable "
            "this session.")))
    player_df = pd.DataFrame([_row(f"g{i}") for i in range(20)])

    result = get_out_redistribution_adjustment(1, 999, "2026-27", player_df)

    assert result.applied is False
    assert result.data_quality == "unavailable"
    assert all(v == 1.0 for v in result.value.values()), "invented an adjustment"
    assert "not yet cached" in result.note


def test_it_survives_any_failure_not_just_a_connection_error(monkeypatch):
    """The live path can fail in more ways than one -- a timeout, a
    malformed payload, a cache read that throws. None of them should
    be the difference between a page and a traceback."""
    for exc in (ConnectionError("blocked"), TimeoutError("slow"),
                ValueError("malformed"), KeyError("Game_ID")):
        monkeypatch.setattr(teammates_module, "fetch_combined_game_log",
                            _raising_fetch(exc))
        player_df = pd.DataFrame([_row(f"g{i}") for i in range(20)])
        result = get_out_redistribution_adjustment(1, 999, "2026-27", player_df)
        assert result.applied is False, exc


def test_the_note_says_which_player_could_not_be_read(monkeypatch):
    """A reader who marked somebody out needs to know the answer is
    missing for that reason, not that the marking did nothing."""
    monkeypatch.setattr(teammates_module, "fetch_combined_game_log",
                        _raising_fetch(ConnectionError("blocked")))
    player_df = pd.DataFrame([_row(f"g{i}") for i in range(20)])
    note = get_out_redistribution_adjustment(1, 999, "2026-27", player_df).note
    assert "marked-out player" in note and "2026-27" in note


def test_every_fetch_in_this_module_is_guarded():
    """The fix is one try/except; the lesson is that it was the only
    one missing. Pins that, so a fourth fetch added later cannot
    quietly reintroduce the same crash."""
    import inspect
    source = inspect.getsource(teammates_module)
    lines = source.splitlines()
    for index, line in enumerate(lines):
        if "fetch_combined_game_log(" not in line or "import" in line:
            continue
        if "def " in line:
            continue
        window = "\n".join(lines[max(0, index - 6):index])
        assert "try:" in window, (
            f"unguarded fetch_combined_game_log at line {index + 1}: "
            f"{line.strip()}")
