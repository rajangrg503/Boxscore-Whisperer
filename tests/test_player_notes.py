import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import player_notes as pn  # noqa: E402


def test_scheierman_is_motor():
    assert "motor" in pn.tags("Baylor Scheierman")


def test_under_blocked_only_with_starter_out():
    notes = {"A": {"tags": ["motor"]}, "B": {"tags": []}}
    assert pn.blocks_under("A", ["Jayson Tatum"], notes)
    assert not pn.blocks_under("A", [], notes)
    assert not pn.blocks_under("B", ["Jayson Tatum"], notes)
