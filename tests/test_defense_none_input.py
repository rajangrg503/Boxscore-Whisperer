"""Direct test for get_defense_adjustment()'s None-input branch --
previously only exercised implicitly through
tests/test_backtest_driver.py's first-month case. The backtest's
correctness depends on this branch genuinely being neutral and
error-free, so it deserves its own isolated test, not just an
assertion on a downstream result."""

from engine.adjustments.defense import get_defense_adjustment


def test_none_team_def_rating_returns_genuinely_neutral_result():
    result = get_defense_adjustment(None, None, "some custom note")

    assert result.value == {"_all": 1.0}
    assert result.multiplier_for("PTS") == 1.0
    assert result.applied is False
    assert result.data_quality == "unavailable"
    assert result.sample_n == 0


def test_none_branch_discards_the_passed_in_note():
    # Documented, not a bug fix -- the None-input branch hardcodes its
    # own note and never references def_source_note. A caller passing
    # a custom explanation (e.g. the backtest's "season's first
    # calendar month" message) should not assume it appears anywhere.
    result = get_defense_adjustment(None, None, "this custom text is discarded")
    assert "this custom text is discarded" not in result.note
    assert result.note == "Opponent defensive rating unavailable -- no adjustment."
