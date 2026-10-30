"""tools/journal_app.py, driven through Streamlit's own test runner with
the API, the cache and the model faked (same fake game as
test_daily_journal.py). Checks the app opens empty, writes a night from
the sidebar button, and shows the game card it wrote."""

import datetime
import os
import sys

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
sys.path.insert(0, TOOLS)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import daily_journal as dj  # noqa: E402
import test_daily_journal as fake  # noqa: E402

APP = os.path.join(TOOLS, "journal_app.py")
DAY = fake.DAY


@pytest.fixture
def journal_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BW_JOURNAL_DIR", str(tmp_path))
    monkeypatch.setattr(dj, "list_games", lambda day: ({"0022600001": {"home": "NYK", "away": "BOS"}}, "test"))
    monkeypatch.setattr(dj, "load_boxscore", lambda gid: fake.BOX.copy())
    monkeypatch.setattr(dj, "load_summary", lambda gid: {k: v.copy() for k, v in fake.SUMMARY.items()})
    monkeypatch.setattr(dj, "pregame_projection", lambda pid, day: fake.PROJ.get(pid))
    monkeypatch.setattr(dj, "us_yesterday", lambda now=None: DAY)
    # Hide Llama, so the tests behave the same with or without Ollama installed.
    monkeypatch.setitem(sys.modules, "ollama", None)
    return tmp_path


def _text(at):
    return " ".join(m.value for m in at.markdown)


def test_empty_journal_invites_writing(journal_dir):
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert "No nights written yet" in _text(at)
    assert at.button[0].label == "Write this night"


def test_write_button_writes_and_shows_the_night(journal_dir):
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert at.toggle[0].disabled and at.toggle[0].value is False   # Llama hidden
    at.button[0].click().run()
    assert not at.exception
    assert (journal_dir / f"{DAY.isoformat()}.json").exists()
    assert (journal_dir / f"{DAY.isoformat()}.md").exists()
    text = _text(at)
    assert "Wednesday 21 October" in text
    assert "BOS trailed 27⁠–⁠22 at halftime and won by 15." in text
    assert "Plain summary" in text
    assert at.button[0].label == "Rewrite this night"


def test_legacy_night_without_data_still_opens(journal_dir):
    (journal_dir / "2026-10-20.md").write_text("# NBA journal\n\n## BOS 100 – 90 NYK\n")
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert "Written before the journal app" in _text(at)


def test_misses_tab_after_a_night_is_written(journal_dir):
    dj.journal_day(DAY, None, out_dir=str(journal_dir), log=lambda *a: None)
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    text = _text(at)
    assert "Where projections miss" in text
    assert "Across 1 night and" in text
    assert "Only 1 night so far" in text


def test_misses_tab_offers_to_rebuild_old_nights(journal_dir):
    (journal_dir / "2026-10-20.md").write_text("# NBA journal\n")
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert any(b.label == "Rebuild 1 older night" for b in at.button)
