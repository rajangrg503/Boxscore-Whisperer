"""The Single Player form defaults the scheme dropdown to NO_SCHEME. It used
to default to the first key ("Drop coverage"), so every prediction silently
carried a scheme multiplier nobody chose. NO_SCHEME must be a neutral,
unapplied layer so it adds nothing to the number, the summary copy, or the
confidence caveats."""

from engine.adjustments.scheme import NO_SCHEME, SCHEME_ADJUSTMENTS, get_synergy_scheme_adjustment


def test_no_scheme_option_exists():
    assert NO_SCHEME in SCHEME_ADJUSTMENTS


def test_no_scheme_selected_is_neutral_and_not_applied():
    result = get_synergy_scheme_adjustment(1610612760, NO_SCHEME, "2025-26")
    assert result.applied is False
    assert result.multiplier_for("PTS") == 1.0
    assert result.sample_n == 0
