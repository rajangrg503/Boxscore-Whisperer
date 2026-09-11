"""Fast unit tests for engine/tracker.py. All tests point LOG_PATH at a
temp file (monkeypatched) so they never touch the real prediction_log.csv.
"""

import contextlib
import datetime

import pandas as pd
import pytest

from engine import tracker
from engine.adjustments.base import AdjustmentResult, ALL_STATS


@pytest.fixture
def temp_log(tmp_path, monkeypatch):
    log_path = tmp_path / "prediction_log.csv"
    monkeypatch.setattr(tracker, "LOG_PATH", str(log_path))
    monkeypatch.setattr(tracker, "LOCK_PATH", str(log_path) + ".lock")
    return log_path


def _sample_predictions():
    return {
        col: {"low": 20.0, "predicted": 25.0, "high": 30.0, "base": 24.0}
        for col, _ in tracker.STAT_COLUMNS
    }


def test_load_empty_log_returns_correct_columns(temp_log):
    df = tracker.load_prediction_log()
    assert df.empty
    assert list(df.columns) == tracker.LOG_COLUMNS


def test_append_then_load_roundtrip(temp_log):
    new_id = tracker.append_prediction_to_log(
        2544, "LeBron James", "Boston Celtics", "BOS",
        datetime.date(2026, 9, 10), _sample_predictions(),
    )
    df = tracker.load_prediction_log()
    assert len(df) == 1
    assert df.iloc[0]["id"] == new_id
    assert df.iloc[0]["PTS_base"] == 24.0
    assert df.iloc[0]["layers_json"] == "{}"
    # saved_by_email defaults to "" when not passed -- old call shapes
    # (and any future caller that forgets it) still work. Note: pandas'
    # CSV round-trip reads an empty string back as NaN, not "" -- same
    # class of quirk as the documented id-as-int64 flake. app.py's
    # filter already accounts for this via .fillna("") before comparing.
    assert pd.isna(df.iloc[0]["saved_by_email"]) or df.iloc[0]["saved_by_email"] == ""
    # source defaults to "single_player" when not passed -- every call
    # site before Full Matchup tracking existed still gets labeled
    # correctly, not left blank.
    assert df.iloc[0]["source"] == "single_player"


def test_id_column_survives_pandas_numeric_misparse(temp_log):
    """Regression test for the flaky-test issue documented in the
    project plan's Known Issues: ids are 8-char hex
    (uuid.uuid4().hex[:8]), and without an explicit dtype, pandas'
    read_csv() misreads some of them as numbers instead of strings --
    breaking any later `df["id"] == some_string` comparison. There are
    TWO independent triggers, confirmed empirically while fixing this
    (not assumed from the plan's original "all-digit" description
    alone): an all-digit id gets read as int64, AND a hex id that
    merely *contains* one "e" digit with only digits after it (e.g.
    "5e123456") gets misread as scientific-notation float -- pandas'
    C parser doesn't require the whole string to be digits, just that
    it matches a numeric grammar. A fix that only guarded against
    all-digit ids (e.g. regenerate-if-`.isdigit()`) would have missed
    the second case entirely, since "5e123456" is not all-digit.
    load_prediction_log()'s dtype={"id": str} fixes both uniformly by
    not depending on knowing every way pandas' inference could misfire."""
    old_columns = ["id", "saved_at", "player_id", "player_full_name", "opponent_full_name",
                   "opponent_abbr", "game_date", "status", "saved_by_email"]
    for col, _ in tracker.STAT_COLUMNS:
        old_columns += [f"{col}_low", f"{col}_mid", f"{col}_high", f"{col}_actual", f"{col}_hit", f"{col}_base"]
    old_columns += ["layers_json"]

    problem_ids = ["12345678", "5e123456"]  # all-digit, and scientific-notation-shaped
    rows = [{c: None for c in old_columns} for _ in problem_ids]
    for row, pid in zip(rows, problem_ids):
        row.update({"id": pid, "player_id": 1, "player_full_name": "X", "status": "pending"})
    pd.DataFrame(rows).to_csv(temp_log, index=False)

    df = tracker.load_prediction_log()
    # The exact dtype label pandas reports for dtype=str varies by
    # version ("object" vs a StringDtype) -- what actually matters is
    # that it's not numeric, and that values compare correctly below.
    assert not pd.api.types.is_numeric_dtype(df["id"])
    assert list(df["id"]) == problem_ids
    for pid in problem_ids:
        assert not df[df["id"] == pid].empty, f"{pid!r} did not survive as a comparable string"


def test_game_id_column_survives_pandas_numeric_misparse(temp_log):
    """Same failure mode as test_id_column_survives_pandas_numeric_misparse,
    a second real column: real NBA Game_IDs are zero-padded strings
    (e.g. "0022400604") -- an all-digit Game_ID read back without an
    explicit dtype silently loses its leading zeros as int64. Caught
    live while verifying the post-game context capture columns, not
    theoretically."""
    row = {c: None for c in tracker.LOG_COLUMNS}
    row.update({"id": "abc12345", "player_id": 1, "player_full_name": "X",
                "status": "resolved", "game_id": "0022400604"})
    pd.DataFrame([row]).to_csv(temp_log, index=False)

    df = tracker.load_prediction_log()

    assert not pd.api.types.is_numeric_dtype(df["game_id"])
    assert df.iloc[0]["game_id"] == "0022400604"


def test_saved_by_email_is_normalized(temp_log):
    tracker.append_prediction_to_log(
        2544, "LeBron James", "Boston Celtics", "BOS",
        datetime.date(2026, 9, 10), _sample_predictions(),
        saved_by_email="  Someone@Example.com  ",
    )
    df = tracker.load_prediction_log()
    assert df.iloc[0]["saved_by_email"] == "someone@example.com"


def test_saved_by_email_distinguishes_rows_for_filtering(temp_log):
    # This is the property app.py's sidebar filter relies on: two
    # different emails' rows are distinguishable after a normal
    # load_prediction_log() call, with no extra filtering logic needed
    # inside the tracker module itself.
    tracker.append_prediction_to_log(
        2544, "LeBron James", "Boston Celtics", "BOS",
        datetime.date(2026, 9, 10), _sample_predictions(),
        saved_by_email="alice@example.com",
    )
    tracker.append_prediction_to_log(
        201939, "Stephen Curry", "Los Angeles Lakers", "LAL",
        datetime.date(2026, 9, 11), _sample_predictions(),
        saved_by_email="bob@example.com",
    )
    df = tracker.load_prediction_log()
    alice_rows = df[df["saved_by_email"] == "alice@example.com"]
    bob_rows = df[df["saved_by_email"] == "bob@example.com"]
    assert len(alice_rows) == 1
    assert len(bob_rows) == 1
    assert alice_rows.iloc[0]["player_full_name"] == "LeBron James"
    assert bob_rows.iloc[0]["player_full_name"] == "Stephen Curry"


def test_layers_json_captures_adjustment_results(temp_log):
    layer_results = {
        "opponent_defense": AdjustmentResult(
            layer="opponent_defense", value={ALL_STATS: 1.05}, note="n/a",
            data_quality="real_current", sample_n=0, applied=True,
        ),
        "missing_teammates": AdjustmentResult(
            layer="missing_teammates", value={"PTS": 0.9, "AST": 1.1}, note="n/a",
            data_quality="real_current", sample_n=8, applied=True,
        ),
    }
    tracker.append_prediction_to_log(
        2544, "LeBron James", "Boston Celtics", "BOS",
        datetime.date(2026, 9, 10), _sample_predictions(), layer_results=layer_results,
    )
    df = tracker.load_prediction_log()
    import json
    parsed = json.loads(df.iloc[0]["layers_json"])
    assert parsed["opponent_defense"]["applied"] is True
    assert parsed["opponent_defense"]["value"] == {"_all": 1.05}
    assert parsed["missing_teammates"]["sample_n"] == 8
    assert parsed["missing_teammates"]["value"] == {"PTS": 0.9, "AST": 1.1}
    # note/layer are deliberately excluded
    assert "note" not in parsed["opponent_defense"]
    assert "layer" not in parsed["opponent_defense"]


def test_backward_compatible_with_old_schema_rows(temp_log):
    old_columns = ["id", "saved_at", "player_id", "player_full_name", "opponent_full_name",
                   "opponent_abbr", "game_date", "status"]
    for col, _ in tracker.STAT_COLUMNS:
        old_columns += [f"{col}_low", f"{col}_mid", f"{col}_high", f"{col}_actual", f"{col}_hit"]
    old_row = {c: None for c in old_columns}
    old_row.update({
        "id": "oldrow1", "saved_at": "2026-08-01T00:00:00", "player_id": 1,
        "player_full_name": "Old Player", "opponent_full_name": "X", "opponent_abbr": "X",
        "game_date": "2026-08-05", "status": "pending", "PTS_low": 10.0, "PTS_mid": 15.0,
        "PTS_high": 20.0,
    })
    pd.DataFrame([old_row]).to_csv(temp_log, index=False)

    loaded = tracker.load_prediction_log()
    assert len(loaded) == 1
    assert loaded.iloc[0]["PTS_mid"] == 15.0

    new_id = tracker.append_prediction_to_log(
        2, "New Player", "Y", "Y", datetime.date(2026, 9, 10), _sample_predictions(),
    )
    final = tracker.load_prediction_log()
    assert len(final) == 2
    old = final[final["id"] == "oldrow1"].iloc[0]
    new = final[final["id"] == new_id].iloc[0]
    assert old["PTS_mid"] == 15.0
    assert pd.isna(old["PTS_base"])  # old row never had this column -- NaN, not corrupted
    assert new["PTS_base"] == 24.0
    # A pre-existing row has no saved_by_email -- accepted consequence of
    # the privacy-scoping migration: it stays in the file (not deleted,
    # not corrupted) but can't match any real email a viewer types in,
    # so it becomes unfindable through app.py's filtered sidebar. Confirm
    # it doesn't accidentally match app.py's filter, which normalizes
    # missing values with .fillna("") before comparing to the entered email.
    assert pd.isna(old["saved_by_email"]) or old["saved_by_email"] == ""
    assert final["saved_by_email"].fillna("").iloc[0] != "somebody@example.com"
    # Same additive treatment for source -- a pre-existing row has no
    # source value (NaN), not a fabricated guess at which tool saved it.
    assert pd.isna(old["source"])
    assert new["source"] == "single_player"


def test_load_reindexes_missing_columns_without_keyerror(temp_log):
    # Reproduces a real bug hit during live verification: a
    # prediction_log.csv written before saved_by_email existed, read
    # back with NO append_prediction_to_log() call in between (the
    # sidebar's actual real-world flow -- a visitor just opens the app).
    # Before load_prediction_log() reindexed to LOG_COLUMNS, direct
    # column indexing (df["saved_by_email"]) raised KeyError here,
    # because pd.read_csv() only returns whatever headers are literally
    # in the file -- the "additive schema" guarantee only held for
    # callers using row.get(...), not whole-column indexing.
    old_columns = ["id", "saved_at", "player_id", "player_full_name", "opponent_full_name",
                   "opponent_abbr", "game_date", "status"]
    for col, _ in tracker.STAT_COLUMNS:
        old_columns += [f"{col}_low", f"{col}_mid", f"{col}_high", f"{col}_actual", f"{col}_hit"]
    old_row = {c: None for c in old_columns}
    old_row.update({"id": "oldrow1", "player_id": 1, "player_full_name": "Old Player",
                     "status": "pending"})
    pd.DataFrame([old_row]).to_csv(temp_log, index=False)

    df = tracker.load_prediction_log()
    assert list(df.columns) == tracker.LOG_COLUMNS
    assert pd.isna(df["saved_by_email"].iloc[0])
    assert pd.isna(df["PTS_base"].iloc[0])
    assert pd.isna(df["layers_json"].iloc[0])
    # The exact operation that crashed live: filtering by email on a
    # DataFrame loaded from a file that predates the column.
    filtered = df[df["saved_by_email"].fillna("") == "alice@example.com"]
    assert filtered.empty


def test_atomic_write_leaves_no_temp_file_behind(temp_log):
    tracker.append_prediction_to_log(
        2544, "LeBron James", "Boston Celtics", "BOS",
        datetime.date(2026, 9, 10), _sample_predictions(),
    )
    leftover_tmp_files = list(temp_log.parent.glob(f"{temp_log.name}.tmp.*"))
    assert leftover_tmp_files == []


def _batch_row(player_id, player_full_name, **overrides):
    row = {
        "player_id": player_id, "player_full_name": player_full_name,
        "opponent_full_name": "Boston Celtics", "opponent_abbr": "BOS",
        "game_date": datetime.date(2026, 10, 20), "predictions": _sample_predictions(),
    }
    row.update(overrides)
    return row


def test_append_predictions_batch_saves_all_rows_with_shared_email_and_source(temp_log):
    rows_input = [
        _batch_row(1, "Player One"),
        _batch_row(2, "Player Two"),
        _batch_row(3, "Player Three"),
    ]
    ids = tracker.append_predictions_batch(rows_input, saved_by_email="alice@example.com")

    assert len(ids) == 3
    assert len(set(ids)) == 3  # distinct ids, not the same id reused

    df = tracker.load_prediction_log()
    assert len(df) == 3
    assert (df["saved_by_email"] == "alice@example.com").all()
    assert (df["source"] == "full_matchup").all()  # the default for this function
    assert set(df["player_full_name"]) == {"Player One", "Player Two", "Player Three"}
    assert (df["game_date"] == "2026-10-20").all()


def test_append_predictions_batch_uses_one_lock_for_the_whole_batch(temp_log, monkeypatch):
    # The actual property being sold here: NOT append_prediction_to_log()
    # called once per player. A loop would still be correct (each call
    # is independently lock-protected) but would acquire the lock N
    # times, leaving N-1 windows where a concurrent reader could see a
    # partial roster. Counting real _locked() invocations is the only
    # way to prove "one atomic batch" rather than just "produces the
    # right rows" -- a loop-based implementation would pass a
    # rows-are-correct-only test just as easily.
    lock_acquisitions = []
    real_locked = tracker._locked

    @contextlib.contextmanager
    def _counting_locked():
        lock_acquisitions.append(1)
        with real_locked():
            yield

    monkeypatch.setattr(tracker, "_locked", _counting_locked)

    rows_input = [_batch_row(1, "Player One"), _batch_row(2, "Player Two"), _batch_row(3, "Player Three")]
    tracker.append_predictions_batch(rows_input, saved_by_email="alice@example.com")

    assert len(lock_acquisitions) == 1


def test_append_predictions_batch_empty_list_is_a_no_op(temp_log):
    ids = tracker.append_predictions_batch([], saved_by_email="alice@example.com")
    assert ids == []
    # Confirms the file was never touched at all, not written as an
    # empty-but-valid CSV -- load_prediction_log() falls back to the
    # same empty-columns DataFrame whether the file never existed or
    # genuinely has zero rows, so checking the file's existence is the
    # only way to prove "no-op", not just "looks empty afterward".
    import os
    assert not os.path.exists(temp_log)


def test_append_predictions_batch_respects_explicit_source_override(temp_log):
    # "full_matchup" is the default (see the shared-fields test above);
    # this proves the parameter is actually used, not hardcoded, by
    # passing something else.
    tracker.append_predictions_batch([_batch_row(1, "Player One")], source="some_other_source")
    df = tracker.load_prediction_log()
    assert df.iloc[0]["source"] == "some_other_source"


# --- Post-game context capture (_capture_post_game_context and helpers) ---


def test_season_shooting_and_minutes_sums_before_dividing(monkeypatch):
    # The whole point of sum(makes)/sum(attempts): a naive mean() of
    # per-game percentages would be wrong here. Game 1 is 10/10 (100%),
    # game 2 is 0/10 (0%) -- mean-of-percentages gives 50%, but the real
    # combined percentage is 10/20 = 50% too by coincidence, so use
    # unequal attempt counts to actually distinguish the two approaches:
    # game 1 is 1/1 (100%), game 2 is 1/19 (~5.3%) -- mean-of-percentages
    # gives ~52.6%, but sum/sum gives 2/20 = 10%.
    fake_log = pd.DataFrame([
        {"MIN": 30, "FGA": 1, "FGM": 1, "FG3A": 1, "FG3M": 1},
        {"MIN": 20, "FGA": 19, "FGM": 1, "FG3A": 9, "FG3M": 0},
    ])
    monkeypatch.setattr(tracker, "fetch_combined_game_log", lambda pid, season: fake_log)

    result = tracker._season_shooting_and_minutes(201939, "2024-25")

    assert result["min_avg"] == 25.0
    assert result["fg_pct"] == pytest.approx(2 / 20)
    assert result["fg3_pct"] == pytest.approx(1 / 10)


def test_season_shooting_and_minutes_returns_none_on_empty_or_error(monkeypatch):
    monkeypatch.setattr(tracker, "fetch_combined_game_log", lambda pid, season: pd.DataFrame())
    assert tracker._season_shooting_and_minutes(201939, "2024-25") is None

    def _raise(pid, season):
        raise RuntimeError("blocked")
    monkeypatch.setattr(tracker, "fetch_combined_game_log", _raise)
    assert tracker._season_shooting_and_minutes(201939, "2024-25") is None


def test_key_teammates_for_applies_70_percent_threshold(monkeypatch):
    roster_df = pd.DataFrame([
        {"PLAYER_ID": 1, "PLAYER": "Regular Starter"},
        {"PLAYER_ID": 2, "PLAYER": "Occasional Bench Player"},
    ])
    monkeypatch.setattr(tracker, "_load_df_cache", lambda key: (roster_df, None) if key == "roster_100" else (None, None))
    league_df = pd.DataFrame([{"TEAM_ID": 100, "GP": 82}])
    monkeypatch.setattr(tracker, "get_league_advanced_team_stats", lambda season: league_df)

    def _fake_gamelog(pid, season):
        # Player 1 played 60/82 (~73%, clears 70%); player 2 played 20/82 (~24%, doesn't).
        n_games = 60 if pid == 1 else 20
        return pd.DataFrame([{"GAME_DATE": "2025-01-01"}] * n_games)
    monkeypatch.setattr(tracker, "fetch_combined_game_log", _fake_gamelog)

    key_teammates = tracker._key_teammates_for(100, "2024-25")

    assert key_teammates == [(1, "Regular Starter")]


def test_key_teammates_for_empty_when_roster_or_team_stats_missing(monkeypatch):
    monkeypatch.setattr(tracker, "_load_df_cache", lambda key: (None, None))
    assert tracker._key_teammates_for(100, "2024-25") == []

    roster_df = pd.DataFrame([{"PLAYER_ID": 1, "PLAYER": "X"}])
    monkeypatch.setattr(tracker, "_load_df_cache", lambda key: (roster_df, None))
    monkeypatch.setattr(tracker, "get_league_advanced_team_stats", lambda season: pd.DataFrame([{"TEAM_ID": 999, "GP": 82}]))
    assert tracker._key_teammates_for(100, "2024-25") == []


def test_capture_post_game_context_populates_all_fields_with_real_shaped_data(monkeypatch):
    row = {"player_id": 201939, "opponent_abbr": "BOS", "game_date": "2025-01-20"}
    actual = pd.Series({
        "MIN": 27, "FG_PCT": 0.375, "FG3_PCT": 0.333,
        "MATCHUP": "GSW vs. BOS", "Game_ID": "0022400604",
    })

    monkeypatch.setattr(tracker, "_season_shooting_and_minutes",
                         lambda pid, season: {"min_avg": 32.5, "fg_pct": 0.47, "fg3_pct": 0.41})

    team_box = pd.DataFrame([
        {"teamTricode": "BOS", "defensiveRating": 89.5},
        {"teamTricode": "GSW", "defensiveRating": 130.2},
    ])
    player_box = pd.DataFrame([
        {"teamTricode": "GSW", "personId": 201939},
        {"teamTricode": "GSW", "personId": 1234},  # a played teammate, not out
    ])
    monkeypatch.setattr(tracker, "_load_or_fetch_advanced_box_score", lambda game_id: (player_box, team_box))

    monkeypatch.setattr(tracker, "TEAM_ID_BY_ABBR", {"BOS": 1610612738, "GSW": 1610612744})
    league_df = pd.DataFrame([{"TEAM_ID": 1610612738, "DEF_RATING": 112.0}])
    monkeypatch.setattr(tracker, "get_league_advanced_team_stats", lambda season: league_df)

    monkeypatch.setattr(tracker, "_key_teammates_for",
                         lambda team_id, season: [(1234, "Played Teammate"), (5678, "Out Teammate")])

    context = tracker._capture_post_game_context(row, actual, "0022400604")

    assert context["game_id"] == "0022400604"
    assert context["min_actual"] == 27.0
    assert context["fg_pct_actual"] == 0.375
    assert context["fg3_pct_actual"] == 0.333
    assert context["min_season_avg"] == 32.5
    assert context["fg_pct_season_avg"] == 0.47
    assert context["fg3_pct_season_avg"] == 0.41
    assert context["opp_def_rating_actual"] == 89.5
    assert context["opp_def_rating_season_avg"] == 112.0
    assert context["key_teammate_out"] is True
    assert context["key_teammate_out_names"] == "Out Teammate"


def test_capture_post_game_context_never_raises_and_defaults_to_none(monkeypatch):
    # game_date can't be parsed -> season can't be derived -> every
    # field past the free MIN/FG_PCT/FG3_PCT extraction stays None,
    # and the function returns cleanly rather than raising.
    row = {"player_id": 201939, "opponent_abbr": "BOS", "game_date": "not-a-date"}
    actual = pd.Series({"MIN": 27, "FG_PCT": 0.375, "FG3_PCT": 0.333,
                         "MATCHUP": "GSW vs. BOS", "Game_ID": "0022400604"})

    context = tracker._capture_post_game_context(row, actual, "0022400604")

    assert context["min_actual"] == 27.0
    assert context["min_season_avg"] is None
    assert context["opp_def_rating_actual"] is None
    assert context["key_teammate_out"] is None


def test_capture_post_game_context_no_key_teammate_out_when_all_played(monkeypatch):
    row = {"player_id": 201939, "opponent_abbr": "BOS", "game_date": "2025-01-20"}
    actual = pd.Series({"MIN": 27, "FG_PCT": 0.375, "FG3_PCT": 0.333,
                         "MATCHUP": "GSW vs. BOS", "Game_ID": "0022400604"})

    monkeypatch.setattr(tracker, "_season_shooting_and_minutes", lambda pid, season: None)
    team_box = pd.DataFrame([{"teamTricode": "BOS", "defensiveRating": 89.5}])
    player_box = pd.DataFrame([
        {"teamTricode": "GSW", "personId": 201939},
        {"teamTricode": "GSW", "personId": 1234},
    ])
    monkeypatch.setattr(tracker, "_load_or_fetch_advanced_box_score", lambda game_id: (player_box, team_box))
    monkeypatch.setattr(tracker, "TEAM_ID_BY_ABBR", {"BOS": 1610612738, "GSW": 1610612744})
    monkeypatch.setattr(tracker, "get_league_advanced_team_stats", lambda season: pd.DataFrame())
    monkeypatch.setattr(tracker, "_key_teammates_for", lambda team_id, season: [(1234, "Played Teammate")])

    context = tracker._capture_post_game_context(row, actual, "0022400604")

    assert context["key_teammate_out"] is False
    assert context["key_teammate_out_names"] is None


def test_try_resolve_prediction_capture_failure_never_blocks_resolution(monkeypatch, temp_log):
    # A capture-time exception must not prevent the core hit/miss
    # resolution it rides alongside -- the try/except around the call
    # inside try_resolve_prediction() is the thing under test here.
    monkeypatch.setattr(tracker, "_capture_post_game_context",
                         lambda row, actual, game_id: (_ for _ in ()).throw(RuntimeError("boom")))

    row = {"player_id": 201939, "opponent_abbr": "BOS",
           "game_date": "2025-01-20", "status": "pending",
           "PTS_low": 10.0, "PTS_high": 25.0}

    match_df = pd.DataFrame([{
        "GAME_DATE": pd.Timestamp("2025-01-20"), "PTS": 18, "Game_ID": "0022400604",
        "MATCHUP": "GSW vs. BOS",
    }])

    def _fake_h2h(player_id, opponent_abbr):
        return match_df

    resolved = tracker.try_resolve_prediction(row, _fake_h2h)

    assert resolved["status"] == "resolved"
    assert resolved["PTS_actual"] == 18
    assert resolved["PTS_hit"] is True
    # capture columns stay absent/unset since the capture itself blew up
    assert "game_id" not in resolved or resolved.get("game_id") is None
