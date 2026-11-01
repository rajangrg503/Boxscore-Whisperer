"""The Journal's Tonight tab: nothing is fetched until asked, a saved
slate renders, and the button reads who's out and projects every game."""
import datetime
import json
import os
import sys

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_journal_app import APP, journal_dir, _text  # noqa: E402,F401

import availability as av  # noqa: E402
import box_score as bs  # noqa: E402
import picks as pk  # noqa: E402

TODAY = datetime.datetime.now(pk.NEW_YORK).date()


def _line(name, pid, pts):
    return {"name": name, "pid": pid, "min": 34.0, "PTS": pts, "REB": 5.0, "AST": 4.0,
            "FG3M": 2.0, "STL": 1.0, "BLK": 0.5, "TOV": 2.0, "FGA": 15.0, "FTA": 4.0,
            "evidence": {}}


SLATE = {"games": [{
    "away": "BOS", "home": "ORL", "tipoff": "2026-11-01T20:40Z",
    "out": {"BOS": [], "ORL": ["Paolo Banchero", "Jalen Suggs"]},
    "day_to_day": {"BOS": [], "ORL": []},
    "players": {"BOS": [_line("Jayson Tatum", 1, 26.0)], "ORL": [_line("Franz Wagner", 2, 24.0)]},
    "score": {"BOS": 114.0, "ORL": 101.0},
    "reads": {"BOS": [{"name": "Jayson Tatum", "edge": "with", "verdict": "attacks ORL's wings"}],
              "ORL": []},
    "states": {}, "stale": {"BOS": {"last": "2026-10-30", "names": ["Jayson Tatum"]}}}],
    "league": {}}


def test_nothing_is_fetched_until_asked(journal_dir, monkeypatch):
    monkeypatch.setattr(av, "collect", lambda *a, **k: pytest.fail("fetched without asking"))
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert "Nothing projected for this date yet" in _text(at)


def test_a_saved_slate_renders(journal_dir):
    os.makedirs(journal_dir / "slate")
    (journal_dir / "slate" / f"{TODAY.isoformat()}.json").write_text(json.dumps(dict(
        SLATE, date=TODAY.isoformat(), made_at="2026-11-01T16:00:00+11:00")))
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    text = _text(at)
    assert "BOS 114" in text and "ORL 101" in text and "BOS by 13" in text
    assert "Paolo Banchero" in text
    assert "missing BOS" in text and "Jayson Tatum" in text
    assert "attacks ORL" in text and "wings" in text


def test_the_button_projects_and_saves(journal_dir, monkeypatch):
    monkeypatch.setattr(av, "collect", lambda day, index=None: [{"away": "BOS", "home": "ORL"}])
    monkeypatch.setattr(bs, "project_slate", lambda feed, only=None: json.loads(json.dumps(SLATE)))
    monkeypatch.setattr(bs, "stale_players", lambda team, pids, jd, before: (None, []))
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.button(key="tonight_go").click().run()
    assert not at.exception
    assert "BOS 114" in _text(at)
    assert (journal_dir / "slate" / f"{TODAY.isoformat()}.json").exists()
