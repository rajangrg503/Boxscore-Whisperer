"""Tests for engine/forward_record.py -- mostly about what it refuses.

This module exists to stand between a thin sample and a reader. Its
failure mode is not a crash; it is a true number, computed correctly,
published three weeks into a season where it means nothing. So these
tests are almost all about the page declining to speak:

  * no rate at all below the gate, however good the rate would look
  * an interval on every rate above it
  * "undecided" when the interval spans the bar, not a weak yes
  * caveats that do not disappear when the figures turn good
  * the market's numbers still absent from everything produced here
"""

import json

import pytest

from engine import forward_record as fr


def night(game_date="2026-10-21", scored=100, covered=80, legs=40,
          legs_correct=24, strong_legs=10, strong_correct=7,
          void=5, push=0, money=None):
    body = {
        "game_date": game_date,
        "totals": {"scored": scored, "covered": covered,
                   "void_players": void, "legs": legs,
                   "legs_correct": legs_correct, "legs_push": push,
                   "strong_legs": strong_legs,
                   "strong_correct": strong_correct},
    }
    if money is not None:
        body["money"] = money
    return body


def published(legs=40, profit=4.0, break_even=0.533):
    """A night's money block as the scorer writes it."""
    return {"legs": legs, "staked": float(legs), "profit": profit,
            "roi": 100.0 * profit / legs, "break_even": break_even}


def pot(legs=500, profit=40.0, break_even=0.533, publishable=True):
    """A SEASON pot as summarise() returns it, gate flag included."""
    return {"legs": legs, "staked": float(legs), "profit": profit,
            "roi": 100.0 * profit / legs, "break_even": break_even,
            "publishable": publishable}


def season(nights=fr.MIN_NIGHTS_TO_STATE, **kwargs):
    """Enough nights to clear the gate, so a test about something else
    is not silently testing the gate instead."""
    return [night(game_date=f"2026-11-{1 + i:02d}", **kwargs)
            for i in range(nights)]


def withheld(legs=3):
    return {"legs": legs, "withheld": "fewer than 10 priced legs"}


def workspace(tmp_path, nights, name="results"):
    folder = tmp_path / name
    folder.mkdir(exist_ok=True)
    for index, body in enumerate(nights):
        (folder / f"2026-10-{21 + index:02d}.json").write_text(json.dumps(body))
    return str(folder)


# ---- the gate ------------------------------------------------------------
def test_an_empty_season_says_so_rather_than_showing_zero(tmp_path):
    summary = fr.summarise(str(tmp_path / "nothing"))
    assert summary["nights"] == 0
    assert summary["coverage"] is None and summary["against_line"] is None
    assert "Nothing scored yet" in fr.headline(summary)


def test_a_thin_sample_publishes_no_rate_at_all(tmp_path):
    # Four nights, and every single leg a winner. The most tempting
    # possible number, and the page must not print it.
    nights = [night(legs=40, legs_correct=40, scored=100, covered=100)
              for _ in range(4)]
    summary = fr.summarise(workspace(tmp_path, nights))
    assert summary["totals"]["legs_correct"] == 160
    assert summary["against_line"] is None, "published a rate on 160 legs"
    assert summary["coverage"] is None, "published coverage on 400 claims"


def test_the_thin_state_still_says_what_it_has(tmp_path):
    summary = fr.summarise(workspace(tmp_path, [night(), night()]))
    text = fr.headline(summary)
    assert "2 nights" in text
    assert "80" in text and "Too early" in text


def test_the_leg_gate_opens_at_its_own_number(tmp_path):
    per_night = fr.MIN_LEGS_TO_STATE // fr.MIN_NIGHTS_TO_STATE
    short = season(legs=per_night, legs_correct=per_night // 2,
                   scored=0, covered=0)
    short[-1]["totals"]["legs"] -= 1          # one leg under the floor
    summary = fr.summarise(workspace(tmp_path, short))
    assert summary["against_line"] is None

    full = season(legs=per_night, legs_correct=per_night // 2,
                  scored=0, covered=0)
    summary = fr.summarise(workspace(tmp_path, full, name="full"))
    assert summary["against_line"] is not None
    assert summary["against_line"]["total"] == fr.MIN_LEGS_TO_STATE


# ---- the gate that actually binds ----------------------------------------
# The first version of this module gated on counts alone. A render of
# four simulated opening nights put "+25.60 units, +11.9%" and an 82.7%
# coverage figure on the page: four nights cleared a 1,000-claim gate,
# because one slate produces about three hundred projected stat lines.
#
# A night's player-games are not independent draws. One blowout, one
# slate where every favourite covered, and three hundred claims move
# together. The effective sample size of this page is NIGHTS.
def test_a_handful_of_nights_publishes_nothing_however_many_claims(tmp_path):
    nights = [night(scored=312, covered=258, legs=54, legs_correct=33,
                    money={"all": published(legs=54, profit=6.4),
                           "strong": published(legs=14, profit=3.0)})
              for _ in range(4)]
    summary = fr.summarise(workspace(tmp_path, nights))
    assert summary["totals"]["scored"] > fr.MIN_CLAIMS_TO_STATE
    assert summary["coverage"] is None, "published coverage on four nights"
    assert summary["against_line"] is None
    for key in ("all", "strong"):
        assert fr.money_line(summary["money"][key], key) is None, (
            f"published a units figure for {key} on four nights")


def test_the_units_figure_is_gated_like_every_rate(tmp_path):
    # The most screenshotable number this page can produce, and the
    # least meaningful one early. It was ungated in the first version.
    thin = [night(money={"all": published(legs=200, profit=30.0),
                         "strong": published(legs=60, profit=12.0)})
            for _ in range(5)]
    summary = fr.summarise(workspace(tmp_path, thin))
    assert summary["money"]["all"]["profit"] > 0
    assert summary["money"]["all"]["publishable"] is False
    assert fr.money_line(summary["money"]["all"], "x") is None


def test_a_pot_that_never_went_through_the_gate_is_not_waved_past():
    assert fr.money_line(published(legs=900, profit=90.0), "x") is None


def test_the_headline_explains_the_nights_gate_rather_than_a_count(tmp_path):
    summary = fr.summarise(workspace(tmp_path, [night(), night()]))
    text = fr.headline(summary)
    assert str(fr.MIN_NIGHTS_TO_STATE) in text
    assert "rise and fall together" in text


# ---- what a published rate carries ---------------------------------------
def test_every_published_rate_carries_an_interval(tmp_path):
    nights = season(legs=100, legs_correct=55, scored=300, covered=240)
    summary = fr.summarise(workspace(tmp_path, nights))
    for key in ("coverage", "against_line"):
        rate = summary[key]
        assert rate is not None
        assert rate["lo"] < rate["rate"] < rate["hi"]


def test_the_interval_stays_inside_zero_and_one_at_the_edges():
    # The normal approximation gives a negative lower bound here. A
    # published rate of "-2% to 4%" is worse than no rate.
    lo, hi = fr.wilson(0, 500)
    assert lo == 0.0 and 0.0 < hi < 0.05
    lo, hi = fr.wilson(500, 500)
    assert hi == pytest.approx(1.0) and 0.95 < lo < 1.0


def test_the_gate_is_wide_enough_to_separate_a_coin_flip_from_an_edge():
    # The reason MIN_LEGS_TO_STATE is what it is: at the gate, a 50%
    # record's interval must be tight enough that a real edge would sit
    # outside it.
    lo, hi = fr.wilson(fr.MIN_LEGS_TO_STATE // 2, fr.MIN_LEGS_TO_STATE)
    assert (hi - lo) / 2 < 0.05


# ---- the verdict, which is usually "we cannot tell" -----------------------
def test_an_interval_spanning_the_bar_is_undecided_not_a_win():
    rate = fr._rate(215, 400, 0)          # 53.75%, just over break-even
    assert fr.verdict_for(rate, fr.TYPICAL_BREAK_EVEN) == "undecided"
    assert "still spans" in fr.line_for("x", rate, fr.TYPICAL_BREAK_EVEN)


def test_a_record_clear_of_the_bar_says_so():
    rate = fr._rate(1300, 2000, 0)        # 65%
    assert fr.verdict_for(rate, fr.TYPICAL_BREAK_EVEN) == "above"
    assert "clear of" in fr.line_for("x", rate, fr.TYPICAL_BREAK_EVEN)


def test_a_losing_record_is_not_softened():
    rate = fr._rate(800, 2000, 0)         # 40%
    assert fr.verdict_for(rate, fr.TYPICAL_BREAK_EVEN) == "below"
    assert "short of" in fr.line_for("x", rate, fr.TYPICAL_BREAK_EVEN)


def test_the_bar_is_the_measured_break_even_not_a_coin_flip():
    # 52% beats a coin flip and loses money. If this ever compares
    # against 50% the page will call a losing model a winning one.
    # 10,000 legs, because at 2,000 a 52% record is not even separable
    # from a coin flip -- which is its own argument for the gate.
    rate = fr._rate(5200, 10000, 0)       # 52%, CI roughly 51.0-53.0
    assert fr.verdict_for(rate, 0.5) == "above"
    assert fr.verdict_for(rate, fr.TYPICAL_BREAK_EVEN) == "below"


# ---- the money -----------------------------------------------------------
def test_units_add_up_across_nights_with_their_weighted_bar(tmp_path):
    nights = [night(money={"all": published(legs=40, profit=4.0,
                                            break_even=0.53),
                           "strong": published(legs=10, profit=3.0,
                                               break_even=0.55)})
              for _ in range(3)]
    summary = fr.summarise(workspace(tmp_path, nights))
    pot = summary["money"]["all"]
    assert pot["legs"] == 120 and pot["profit"] == pytest.approx(12.0)
    assert pot["roi"] == pytest.approx(10.0)
    assert pot["break_even"] == pytest.approx(0.53)


def test_a_withheld_night_is_counted_and_named_not_averaged_over(tmp_path):
    nights = [night(money={"all": published(), "strong": withheld(3)}),
              night(money={"all": published(), "strong": withheld(2)})]
    summary = fr.summarise(workspace(tmp_path, nights))
    strong = summary["money"]["strong"]
    assert strong["legs"] == 0 and strong["withheld_legs"] == 5
    assert fr.money_line(strong, "x") is None
    assert any("5 priced leg(s)" in line for line in fr.caveats(summary))


def test_a_money_line_reports_its_break_even_beside_the_profit():
    line = fr.money_line(pot(legs=100, profit=8.0, break_even=0.533),
                         "on the listed legs")
    assert "+8.00 units" in line and "+8.0%" in line and "53.3%" in line


# ---- the caveats ---------------------------------------------------------
def test_the_caveats_survive_a_good_season(tmp_path):
    """A caveat that disappears when the figures look good is advertising."""
    nights = season(legs=100, legs_correct=90, scored=300, covered=290)
    text = " ".join(fr.caveats(fr.summarise(workspace(tmp_path, nights))))
    assert "baseline" in text and "void" in text.lower()
    assert "never published here" in text


def test_an_unreadable_night_is_reported_not_swallowed(tmp_path):
    folder = tmp_path / "results"
    folder.mkdir()
    (folder / "2026-10-21.json").write_text(json.dumps(night()))
    (folder / "2026-10-22.json").write_text("{ this is not json")
    summary = fr.summarise(str(folder))
    assert summary["nights"] == 1 and summary["unreadable"] == 1
    assert any("could not be read" in line for line in fr.caveats(summary))


def test_pushes_and_voids_are_named_rather_than_folded_in(tmp_path):
    nights = [night(void=12, push=3)]
    summary = fr.summarise(workspace(tmp_path, nights))
    text = " ".join(fr.caveats(summary))
    assert "12 player-game(s) voided" in text
    assert "3 leg(s) landed exactly on the line" in text


# ---- the licence ---------------------------------------------------------
def test_nothing_this_module_produces_carries_a_line_or_a_price(tmp_path):
    nights = season(legs=100, legs_correct=55, scored=300, covered=240,
                    money={"all": published(), "strong": published(10, 2.0)})
    summary = fr.summarise(workspace(tmp_path, nights))
    produced = " ".join(filter(None, [
        fr.headline(summary),
        fr.line_for("coverage", summary["coverage"]),
        fr.line_for("line", summary["against_line"], fr.TYPICAL_BREAK_EVEN),
        fr.money_line(summary["money"]["all"], "all"),
        *fr.caveats(summary)]))
    # A price or a line would show up as one of these shapes. The
    # aggregate break-even is allowed -- see engine/pricing.py.
    for forbidden in ("bookOdds", "bookOverUnder", "-110", "+110", "19.5"):
        assert forbidden not in produced


# ---- what is withheld, said out loud --------------------------------------
# Silence and refusal look identical on a page. A reader who sees two
# figures and not the third cannot tell whether it is being withheld or
# was never computed, and the second reading makes a product look like
# it is hiding something.
def test_a_withheld_figure_is_named_rather_than_omitted_silently(tmp_path):
    nights = season(legs=60, legs_correct=34, scored=300, covered=240,
                    strong_legs=12, strong_correct=7,
                    money={"all": published(legs=60, profit=5.0),
                           "strong": published(legs=12, profit=1.5)})
    summary = fr.summarise(workspace(tmp_path, nights))
    assert summary["against_line"] is not None     # this one shows
    assert summary["strong"] is None               # this one does not
    notes = fr.pending(summary)
    assert any("listed" in note and str(fr.MIN_LEGS_TO_STATE) in note
               for note in notes), notes
    assert any("units figure for the listed legs is withheld" in note
               for note in notes), notes


def test_nothing_is_listed_as_pending_before_the_nights_gate(tmp_path):
    # The headline already says it in full; repeating it under an empty
    # figure list would read as an error state.
    summary = fr.summarise(workspace(tmp_path, [night(), night()]))
    assert fr.pending(summary) == []


def test_a_fully_published_record_has_nothing_pending(tmp_path):
    nights = season(legs=60, legs_correct=34, scored=300, covered=240,
                    strong_legs=40, strong_correct=24,
                    money={"all": published(legs=60, profit=5.0),
                           "strong": published(legs=40, profit=4.0)})
    summary = fr.summarise(workspace(tmp_path, nights))
    assert summary["strong"] is not None
    assert fr.pending(summary) == []
