"""The Picks tab shows where each leg's line closed."""
import json
import os
import sys

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_journal_app import APP, journal_dir, _text  # noqa: E402,F401

import picks as pk  # noqa: E402


def test_a_leg_shows_its_closing_line(journal_dir):
    pk.save_slip({"source": "sportsbet", "price": 1.9, "placed_at": "2026-11-01T15:00:00+11:00",
                  "legs": [{"player": "Payton Pritchard", "pid": None, "team": "BOS", "stat": "PRA",
                            "line": 22.5, "side": "over", "us_date": "2099-01-01",
                            "game": "BOS@ORL", "price": None}]},
                 picks_dir=str(journal_dir / "picks"))
    os.makedirs(journal_dir / "lines" / "2099-01-01")
    (journal_dir / "lines" / "2099-01-01" / "070000.json").write_text(json.dumps({
        "taken_at": "2099-01-02T07:00:00+11:00", "closing": True,
        "events": [{"id": 1, "game": "BOS@ORL", "start": "2099-01-01T20:30:00+00:00",
                    "us_date": "2099-01-01",
                    "rows": [{"game": "BOS@ORL", "player": "Payton Pritchard", "stat": "PRA",
                              "kind": "ou", "line": 23.5, "over": 1.9, "under": 1.9}]}]}))
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    text = _text(at)
    assert "closed 23.5 (+1)" in text
    assert "Against the closing line" in text
