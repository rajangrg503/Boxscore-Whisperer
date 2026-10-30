"""tools/whisper.py with the model mocked out -- no Ollama needed.

What matters here is not the prose (a mock can't test that) but the
guard rails around it: the facts only contain what the engine logged,
a note with an invented number or betting language never reaches the
log, hypotheticals are skipped, and saving never clobbers a cell that
was filled in between the read and the write."""

import json
import os
import sys

import pandas as pd
import pytest

from engine import tracker

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import whisper  # noqa: E402


@pytest.fixture
def temp_log(tmp_path, monkeypatch):
    log_path = tmp_path / "prediction_log.csv"
    monkeypatch.setattr(tracker, "LOG_PATH", str(log_path))
    monkeypatch.setattr(tracker, "LOCK_PATH", str(log_path) + ".lock")
    return log_path


def _row(id_, status="pending", hypothetical=False, **extra):
    row = {c: None for c in tracker.LOG_COLUMNS}
    row.update({
        "id": id_, "status": status, "player_full_name": "Test Player",
        "opponent_full_name": "Boston Celtics", "opponent_abbr": "BOS",
        "game_date": "2026-11-01", "hypothetical": hypothetical,
        "PTS_low": 18.0, "PTS_mid": 24.5, "PTS_high": 31.0, "PTS_base": 26.0,
        "REB_low": 4.0, "REB_mid": 6.0, "REB_high": 9.0, "REB_base": 6.0,
        "AST_low": 3.0, "AST_mid": 5.0, "AST_high": 7.0, "AST_base": 5.0,
        "layers_json": json.dumps({
            "opponent_defense": {"applied": True, "data_quality": "real_current",
                                  "sample_n": 20, "value": {"_all": 0.94}},
            "scheme": {"applied": False, "data_quality": "unavailable",
                       "sample_n": 0, "value": {}},
        }),
    })
    row.update(extra)
    return row


def _save(rows):
    tracker.save_prediction_log(pd.DataFrame(rows, columns=tracker.LOG_COLUMNS))


def test_pregame_facts_only_use_logged_numbers():
    facts = whisper.build_pregame_facts(_row("a1"))
    text = " ".join(facts)
    assert "24.5" in text and "18 to 31" in text and "baseline of 26" in text
    assert "Opponent defense moved it down" in text
    assert "Scheme" not in text  # not applied, so not mentioned


def test_recap_facts_report_inside_and_outside():
    row = _row("r1", status="resolved", PTS_actual=33, PTS_hit=False,
               REB_actual=7, REB_hit=True, min_actual=38, min_season_avg=34.2)
    text = " ".join(whisper.build_recap_facts(row))
    assert "Actual points: 33, outside" in text
    assert "Actual rebounds: 7, inside" in text
    assert "38 this game vs 34.2" in text


def test_check_note_rejects_invented_numbers_and_betting_words():
    facts = whisper.build_pregame_facts(_row("a1"))
    assert whisper.check_note("Projected around 24.5 points against a tough defense.", facts) is None
    assert "not in the facts" in whisper.check_note("He has scored 40 in three straight.", facts)
    assert "banned" in whisper.check_note("Easy over on 24.5 points.", facts)
    assert whisper.check_note("", facts) == "empty"
    # 10 must not slip through just because 1 appears somewhere.
    assert "not in the facts" in whisper.check_note("Expect 10 rebounds.", ["1 game"])


def test_bad_first_draft_is_retried_then_skipped():
    facts = whisper.build_pregame_facts(_row("a1"))
    drafts = iter(["Lock it in, 40 points.", "Projected near 24.5 points."])
    note, reason = whisper.write_note(facts, lambda f, t: next(drafts))
    assert note == "Projected near 24.5 points." and reason is None

    note, reason = whisper.write_note(facts, lambda f, t: "Hammer the over.")
    assert note is None and "banned" in reason


def test_end_to_end_fills_missing_notes_and_skips_hypotheticals(temp_log):
    _save([
        _row("p1"),
        _row("p2", hypothetical=True),
        _row("p3", whisper="Already written."),
        _row("r1", status="resolved", PTS_actual=22, PTS_hit=True),
    ])
    calls = []

    def fake(facts, temperature):
        calls.append(facts)
        return "A calm, factual note."

    assert whisper.main([], generate=fake) == 0
    df = tracker.load_prediction_log().set_index("id")
    assert df.at["p1", "whisper"] == "A calm, factual note."
    assert pd.isna(df.at["p2", "whisper"])            # what-if: left alone
    assert df.at["p3", "whisper"] == "Already written."  # not overwritten
    assert df.at["r1", "whisper_recap"] == "A calm, factual note."
    assert len(calls) == 2


def test_save_does_not_clobber_a_note_written_meanwhile(temp_log):
    _save([_row("p1")])
    tracker_df = tracker.load_prediction_log()
    tracker_df["whisper"] = tracker_df["whisper"].astype(object)
    tracker_df.loc[tracker_df["id"] == "p1", "whisper"] = "Written by another run."
    tracker.save_prediction_log(tracker_df)

    written = whisper.save_notes({("p1", "whisper"): "Late arrival."})
    assert written == 0
    assert tracker.load_prediction_log().iloc[0]["whisper"] == "Written by another run."


def test_dry_run_saves_nothing(temp_log):
    _save([_row("p1")])
    whisper.main(["--dry-run"], generate=lambda f, t: "Projected near 24.5 points.")
    assert pd.isna(tracker.load_prediction_log().iloc[0]["whisper"])


def test_sentences_ignore_jr_initials_and_decimals():
    assert whisper.sentences("A won by 3. Darius Acuff Jr. had 34. NOP shot 58.8% here! J. Smith scored 5.") == [
        "A won by 3.", "Darius Acuff Jr. had 34.", "NOP shot 58.8% here!", "J. Smith scored 5."]


def test_trimmed_versions_drop_a_cut_off_fragment():
    assert whisper.trimmed_versions("One. Two. Three and") == ["One. Two.", "One."]


def test_long_draft_is_trimmed_not_thrown_away():
    facts = ["MEM won by 21.", "MEM's bench scored 56 points to POR's 32."]
    draft = "MEM won by 21. MEM's bench scored 56 points to POR's 32. " + "Filler words here. " * 12
    note, reason = whisper.write_note(facts, lambda f, t: draft, max_words=20)
    assert reason is None
    # The longest version that fits: front sentences kept, the rest cut.
    assert note.startswith("MEM won by 21. MEM's bench scored 56 points to POR's 32.")
    assert len(note.split()) <= 20 and len(note.split()) + 3 > 20


def test_bad_last_sentence_is_cut_but_bad_first_sentence_is_not_saved():
    facts = ["MEM won by 21."]
    note, _ = whisper.write_note(facts, lambda f, t: "MEM won by 21. Easy over next time.")
    assert note == "MEM won by 21."
    note, reason = whisper.write_note(facts, lambda f, t: "MEM won by 40. Fine.")
    assert note is None and "not in the facts" in reason
