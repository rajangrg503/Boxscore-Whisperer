"""Tests for analytics/layer_accuracy.py. All point LOG_PATH at a temp
file (monkeypatched) so they never touch the real prediction_log.csv."""

import json

import pandas as pd
import pytest

from engine import tracker
from tests.conftest import write_raw_log
from analytics import layer_accuracy


@pytest.fixture
def temp_log(tmp_path, monkeypatch):
    log_path = tmp_path / "prediction_log.csv"
    monkeypatch.setattr(tracker, "LOG_PATH", str(log_path))
    monkeypatch.setattr(tracker, "LOCK_PATH", str(log_path) + ".lock")
    return log_path


def _make_row(id_, base, actual, multiplier, saved_at="2026-09-06T00:00:00",
              status="resolved", layer_name="opponent_defense", applied=True):
    row = {c: None for c in tracker.LOG_COLUMNS}
    row.update({
        "id": id_, "status": status, "saved_at": saved_at,
        "PTS_base": base, "PTS_actual": actual,
        "layers_json": json.dumps({
            layer_name: {"applied": applied, "data_quality": "real_current",
                         "sample_n": 0, "value": {"_all": multiplier}},
        }),
    })
    return row


def _write_rows(temp_log, rows):
    write_raw_log(pd.DataFrame(rows), temp_log)


def test_context_only_by_design_short_circuits_without_touching_log(temp_log):
    # No log file even exists at this path -- if this tried to read it,
    # load_prediction_log() would just return empty, so this alone
    # doesn't prove it short-circuited. The real proof is n=0 with the
    # specific reason, which only the short-circuit path produces.
    result = layer_accuracy.layer_hit_rate("defender_matchup", "PTS")
    assert result.hit_rate is None
    assert result.reason == "context_only_by_design"
    assert result.n == 0


def test_insufficient_data_returns_none_with_reason(temp_log):
    _write_rows(temp_log, [_make_row("r1", 21.3, 14, 0.997)])
    result = layer_accuracy.layer_hit_rate("opponent_defense", "PTS")
    assert result.hit_rate is None
    assert result.reason == "insufficient_data"
    assert result.n == 1


def test_enough_data_returns_real_percentage(temp_log):
    rows = [
        _make_row("hit1", 21.3, 14, 0.997),   # predicted down, actual down -> HIT
        _make_row("hit2", 20.0, 22.0, 1.05),  # predicted up, actual up -> HIT
        _make_row("hit3", 15.0, 12.0, 0.90),  # predicted down, actual down -> HIT
        _make_row("miss1", 10.0, 8.0, 1.10),  # predicted up, actual down -> MISS
        _make_row("miss2", 25.0, 27.0, 0.95), # predicted down, actual up -> MISS
    ]
    _write_rows(temp_log, rows)
    result = layer_accuracy.layer_hit_rate("opponent_defense", "PTS")
    assert result.n == 5
    assert result.hit_rate == 60.0
    assert result.reason is None


def test_skips_rows_where_layer_not_applied(temp_log):
    rows = [_make_row(f"r{i}", 20.0, 15.0, 0.9, applied=False) for i in range(10)]
    _write_rows(temp_log, rows)
    result = layer_accuracy.layer_hit_rate("opponent_defense", "PTS")
    assert result.n == 0
    assert result.reason == "insufficient_data"


def test_skips_rows_with_no_directional_assertion(temp_log):
    rows = [_make_row(f"r{i}", 20.0, 15.0, 1.0) for i in range(10)]  # multiplier == 1.0
    _write_rows(temp_log, rows)
    result = layer_accuracy.layer_hit_rate("opponent_defense", "PTS")
    assert result.n == 0


def test_skips_unresolved_rows(temp_log):
    rows = [_make_row(f"r{i}", 20.0, 15.0, 0.9, status="pending") for i in range(10)]
    _write_rows(temp_log, rows)
    result = layer_accuracy.layer_hit_rate("opponent_defense", "PTS")
    assert result.n == 0


def test_skips_rows_where_layer_absent_from_layers_json(temp_log):
    rows = [_make_row(f"r{i}", 20.0, 15.0, 0.9, layer_name="scheme") for i in range(10)]
    _write_rows(temp_log, rows)
    result = layer_accuracy.layer_hit_rate("opponent_defense", "PTS")
    assert result.n == 0


def test_build_layer_lines_matches_old_hardcoded_output(temp_log):
    """Proves build_layer_lines() (the loop-based replacement for
    app.py's 6 hardcoded st.write() blocks) produces BYTE-FOR-BYTE
    identical text to those blocks, for the exact same inputs -- not
    just that it runs without error. old_expected below is copied
    verbatim from app.py's pre-refactor source (the f-string templates
    at the 6 call sites), not paraphrased or reconstructed from memory.

    Exercises all three LayerAccuracy branches at once: real-percentage
    (opponent_defense, scheme -- each with 5 resolved rows), insufficient
    data (missing_teammates, missing_opponents, new_teammate -- 0 rows),
    and context_only_by_design (defender_matchup, always)."""
    rows = (
        [_make_row(f"def_hit{i}", 20.0, 15.0, 0.9, layer_name="opponent_defense") for i in range(3)]
        + [_make_row(f"def_miss{i}", 20.0, 25.0, 0.9, layer_name="opponent_defense") for i in range(2)]
        + [_make_row(f"sch_hit{i}", 20.0, 25.0, 1.1, layer_name="scheme") for i in range(4)]
        + [_make_row("sch_miss0", 20.0, 15.0, 1.1, layer_name="scheme")]
    )
    _write_rows(temp_log, rows)

    notes_by_layer = {
        "opponent_defense": "DEF_NOTE_TEXT",
        "missing_teammates": "TEAMMATE_NOTE_TEXT",
        "missing_opponents": "OPP_MISSING_NOTE_TEXT",
        "new_teammate": "NEW_TEAMMATE_NOTE_TEXT",
        "defender_matchup": "DEFENDER_NOTE_TEXT",
        "scheme": "SCHEME_NOTE_TEXT",
    }

    # Verbatim from app.py's pre-refactor source (lines 1548-1559).
    old_expected = [
        f"**[2] Opponent defense:** {notes_by_layer['opponent_defense']} "
        f"_{layer_accuracy.format_layer_accuracy(layer_accuracy.layer_hit_rate('opponent_defense', 'PTS'))}_",
        f"**[3] Missing teammates:** {notes_by_layer['missing_teammates']} "
        f"_{layer_accuracy.format_layer_accuracy(layer_accuracy.layer_hit_rate('missing_teammates', 'PTS'))}_",
        f"**[4] Missing opponent players:** {notes_by_layer['missing_opponents']} "
        f"_{layer_accuracy.format_layer_accuracy(layer_accuracy.layer_hit_rate('missing_opponents', 'PTS'))}_",
        f"**[5] New teammate arriving:** {notes_by_layer['new_teammate']} "
        f"_{layer_accuracy.format_layer_accuracy(layer_accuracy.layer_hit_rate('new_teammate', 'PTS'))}_",
        f"**[6] Primary defender:** {notes_by_layer['defender_matchup']} "
        f"_{layer_accuracy.format_layer_accuracy(layer_accuracy.layer_hit_rate('defender_matchup', 'PTS'))}_",
        f"**[7] Scheme:** {notes_by_layer['scheme']} "
        f"_{layer_accuracy.format_layer_accuracy(layer_accuracy.layer_hit_rate('scheme', 'PTS'))}_",
    ]

    actual = layer_accuracy.build_layer_lines(notes_by_layer)

    assert actual == old_expected
    assert "60%" in actual[0]
    assert "80%" in actual[5]
    assert "not enough resolved predictions" in actual[1]
    assert "never becomes a multiplier on its own" in actual[4]
    assert "vs. specific player" in actual[4]  # the corrected copy points at the real mechanism


# ---------------------------------------------------------------------
# The public track record must not contain the reader's own what-ifs.
#
# This is the one line in the app that shows EVERY visitor a number
# computed from EVERY visitor's saved rows ("directionally correct N% of
# the time", app.py's "See how this estimate was built"). A scenario save
# is a made-up input, so scoring it would report a track record the model
# never earned.
# ---------------------------------------------------------------------

def _five_hits(prefix, **kw):
    return [_make_row(f"{prefix}{i}", 20.0, 15.0, 0.9, **kw) for i in range(5)]


def _five_misses(prefix, **kw):
    return [_make_row(f"{prefix}{i}", 20.0, 25.0, 0.9, **kw) for i in range(5)]


def test_hypothetical_rows_do_not_reach_the_public_track_record(temp_log):
    rows = _five_hits("real")
    for r in rows:
        r["hypothetical"] = False
    made_up = _five_misses("whatif")
    for r in made_up:
        r["hypothetical"] = True
    _write_rows(temp_log, rows + made_up)

    result = layer_accuracy.layer_hit_rate("opponent_defense", "PTS")
    assert result.n == 5, "only the five real rows should be scored"
    assert result.hit_rate == 100.0, "the five invented misses must not drag it down"


def test_real_rows_still_reach_it(temp_log):
    """The control. An over-eager filter that dropped everything would
    pass the test above while silently emptying the track record -- and
    "insufficient_data" reads on screen as a young app, not as a bug,
    so nothing would ever surface it."""
    rows = _five_hits("real")
    for r in rows:
        r["hypothetical"] = False
    _write_rows(temp_log, rows)

    result = layer_accuracy.layer_hit_rate("opponent_defense", "PTS")
    assert result.n == 5
    assert result.hit_rate == 100.0
    assert result.reason is None


def test_what_ifs_are_skipped_over_not_counted_against_the_window(temp_log):
    """Filtering has to happen BEFORE the window is taken, not after.

    Fifty what-ifs saved today and five real rows saved earlier: filter
    after .head(50) and the sample is empty while the app still says
    "over the last 50 resolved predictions". The number would quietly
    stop meaning anything, which is the failure mode this codebase keeps
    finding. Filtering first reaches past them.
    """
    made_up = [
        dict(_make_row(f"whatif{i}", 20.0, 25.0, 0.9,
                       saved_at=f"2026-09-20T00:{i:02d}:00"), hypothetical=True)
        for i in range(50)
    ]
    real = [
        dict(_make_row(f"real{i}", 20.0, 15.0, 0.9,
                       saved_at=f"2026-09-01T00:{i:02d}:00"), hypothetical=False)
        for i in range(5)
    ]
    _write_rows(temp_log, made_up + real)

    result = layer_accuracy.layer_hit_rate("opponent_defense", "PTS")
    assert result.n == 5
    assert result.hit_rate == 100.0


def test_a_log_that_predates_the_column_is_unaffected(temp_log):
    """Rows written before the column existed read back as NaN, and NaN
    is not a what-if. Without this, shipping the column would have
    blanked the whole existing track record on deploy."""
    rows = _five_hits("legacy")
    for r in rows:
        del r["hypothetical"]
    _write_rows(temp_log, rows)

    result = layer_accuracy.layer_hit_rate("opponent_defense", "PTS")
    assert result.n == 5
    assert result.hit_rate == 100.0


def test_the_mark_survives_the_real_write_path(temp_log):
    """The tests above hand-build frames, which proves the filter but not
    that anything ever sets the flag. This one goes through
    append_prediction_to_log and back out, so a scenario save really is
    excluded end to end -- including the CSV round trip that turns the
    bool into the text "True"."""
    predictions = {
        col: {"low": 15.0, "predicted": 20.0, "high": 25.0, "base": 22.0}
        for col, _ in tracker.STAT_COLUMNS
    }
    for i in range(5):
        tracker.append_prediction_to_log(
            i, f"What If {i}", "Boston Celtics", "BOS",
            __import__("datetime").date(2026, 10, 20), predictions,
            hypothetical=True,
        )
    df = tracker.load_prediction_log()
    df["status"] = "resolved"
    for col in ("PTS_actual",):
        df[col] = 15.0
    df["layers_json"] = json.dumps({
        "opponent_defense": {"applied": True, "data_quality": "real_current",
                             "sample_n": 0, "value": {"_all": 0.9}},
    })
    write_raw_log(df, temp_log)

    result = layer_accuracy.layer_hit_rate("opponent_defense", "PTS")
    assert result.n == 0, "five saved what-ifs must contribute nothing"
    assert result.reason == "insufficient_data"


# ---------------------------------------------------------------------
# The empty log -- the state none of the tests above exercised, and the
# state the app is actually in on a fresh install and was in on the day
# the filter shipped. Every test in this file writes rows first, so the
# ordinary path went untested while the interesting ones were covered.
# ---------------------------------------------------------------------

def test_an_empty_log_still_has_its_columns(temp_log):
    """The regression. .map() on an empty Series cannot infer a dtype
    and returns object; pandas reads df[<object Series>] as COLUMN
    selection, so the filter silently returned a frame with zero columns
    and the next df["status"] raised KeyError -- taking down "See how
    this estimate was built" for every visitor."""
    recent = layer_accuracy._recent_resolved(50)
    assert len(recent) == 0
    for column in ("status", "saved_at", "layers_json", "PTS_base"):
        assert column in recent.columns, (
            f"{column} was dropped: the mask was read as a column indexer")


@pytest.mark.parametrize("rows, label", [
    ([], "no rows at all"),
    ([("pending", False)], "nothing resolved yet"),
    ([("resolved", True)], "resolved, but every one a what-if"),
])
def test_every_way_the_sample_comes_out_empty(temp_log, rows, label):
    """All three reach the same zero-row frame by different routes, and
    each one used to lose its columns."""
    built = []
    for i, (status, made_up) in enumerate(rows):
        row = _make_row(f"r{i}", 20.0, 15.0, 0.9, status=status)
        row["hypothetical"] = made_up
        built.append(row)
    if built:
        _write_rows(temp_log, built)

    recent = layer_accuracy._recent_resolved(50)
    assert len(recent) == 0, label
    assert "status" in recent.columns, label
    # And the thing the KeyError actually broke: the caller must survive.
    result = layer_accuracy.layer_hit_rate("opponent_defense", "PTS", df=recent)
    assert result.hit_rate is None
    assert result.reason == "insufficient_data"


def test_the_whole_panel_renders_against_an_empty_log(temp_log):
    """The end of the chain, which is what the visitor sees. app.py calls
    build_layer_lines() unconditionally, so this is the exact call that
    raised in the browser."""
    notes_by_layer = {key: "NOTE" for key, _label in
                      __import__("engine.adjustments.registry", fromlist=["x"]).LAYER_DISPLAY}
    lines = layer_accuracy.build_layer_lines(notes_by_layer)
    assert len(lines) == len(notes_by_layer)
    assert all(isinstance(line, str) and line for line in lines)
