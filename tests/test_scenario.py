"""Tests for engine/scenario.py -- the typed scenario parser.

The parser's job is to fill controls that already exist, and to REFUSE
rather than guess. So most of these pin refusals, and every refusal has
a control proving the parser still applies the thing it is supposed to
apply -- a parser that matched nothing would pass a refusal test while
being useless.

The roster fixtures are hand-built on purpose: they are the two rosters
the page passes in, not a record any writer of ours produces, and the
one that matters (Jalen Williams beside Jaylin Williams) is a real
Oklahoma City pairing chosen precisely because it is the trap.
"""

import pytest

from engine import scenario

# Real OKC and San Antonio names. Jalen and Jaylin Williams are both
# here because they are both really there.
THUNDER = [
    ("2544", "Shai Gilgeous-Alexander"),
    ("1628369", "Chet Holmgren"),
    ("1631114", "Jalen Williams"),
    ("1630793", "Jaylin Williams"),
    ("1629632", "Isaiah Hartenstein"),
    ("1627936", "Alex Caruso"),
]
SPURS = [
    ("1630170", "Victor Wembanyama"),
    ("1631110", "Devin Vassell"),
    ("203999", "Stephon Castle"),
]


def parse(text):
    return scenario.parse(text, teammates=THUNDER, opponents=SPURS)


# --------------------------------------------------------------------
# What it applies
# --------------------------------------------------------------------

def test_a_named_teammate_marked_out_fills_the_control():
    result = parse("Chet is injured")
    assert result["out_teammates"] == ["1628369"]
    assert result["out_opponents"] == []
    assert len(result["applied"]) == 1
    assert result["applied"][0]["control"] == "Missing teammates"
    assert result["applied"][0]["player"] == "Chet Holmgren"


def test_an_opponent_marked_out_fills_the_other_control():
    result = parse("Wembanyama is out")
    assert result["out_opponents"] == ["1630170"]
    assert result["out_teammates"] == []
    assert result["applied"][0]["control"] == "Missing opponent players"


@pytest.mark.parametrize("phrasing", [
    "Chet Holmgren is out",
    "Chet is injured",
    "Holmgren is sidelined",
    "Chet won't play tonight",
    "Chet is resting",
    "Chet is a DNP",
    "Chet is ruled out",
])
def test_the_ways_people_actually_write_it(phrasing):
    assert parse(phrasing)["out_teammates"] == ["1628369"]


def test_initials_resolve_when_they_are_unambiguous():
    """People type SGA far more often than the full name. Initials of a
    real roster name, never a hand-kept nickname list that would drift
    the first time somebody is traded."""
    assert parse("SGA is resting")["out_teammates"] == ["2544"]


def test_accents_do_not_have_to_be_typed():
    roster = [("1", "Nikola Đurišić")]
    result = scenario.parse("Durisic is out", teammates=roster)
    assert result["out_teammates"] == ["1"]


# --------------------------------------------------------------------
# What it refuses -- and the control for each
# --------------------------------------------------------------------

def test_an_ambiguous_surname_is_never_guessed():
    """The trap this file exists for. Both Williamses are Thunder, so
    picking one would be wrong half the time and silent either way."""
    result = parse("Williams is out")
    assert result["out_teammates"] == []
    assert len(result["unmatched"]) == 1
    reason = result["unmatched"][0]["reason"]
    assert "Jalen Williams" in reason and "Jaylin Williams" in reason


def test_the_full_name_resolves_the_pair_that_a_surname_cannot():
    """The control. A parser that refused both Williamses always would
    pass the test above while being useless on the roster that
    motivated it."""
    assert parse("Jalen Williams is out")["out_teammates"] == ["1631114"]
    assert parse("Jaylin Williams is out")["out_teammates"] == ["1630793"]


def test_a_clause_with_no_measured_layer_is_echoed_back():
    result = parse("Shai will be double teamed")
    assert result["out_teammates"] == []
    assert result["unmatched"] == [
        {"clause": "Shai will be double teamed",
         "reason": "no measured layer for this"}
    ]


def test_a_denial_does_not_mark_anyone_out():
    """"Chet is not out" contains "out". A single pass over the words
    would mark him out -- the opposite of what was typed."""
    result = parse("Chet is not out")
    assert result["out_teammates"] == []
    assert "denial" in result["unmatched"][0]["reason"]


def test_not_playing_still_means_out():
    """The control on the denial rule: it must not swallow the
    out-phrases that happen to contain a negation."""
    assert parse("Chet is not playing")["out_teammates"] == ["1628369"]
    assert parse("Chet won't play")["out_teammates"] == ["1628369"]


@pytest.mark.parametrize("phrasing", [
    "Chet is questionable",
    "Chet is doubtful",
    "Chet is a game-time decision",
    "Chet is on a minutes restriction",
])
def test_a_half_available_player_is_refused_not_rounded(phrasing):
    """The layer takes a player in or out. Rounding "questionable" to
    either one would be inventing an input the reader did not give."""
    result = parse(phrasing)
    assert result["out_teammates"] == []
    assert "half-available" in result["unmatched"][0]["reason"]


def test_a_player_from_neither_roster_is_not_silently_dropped():
    result = parse("LeBron James is out")
    assert result["out_teammates"] == [] and result["out_opponents"] == []
    assert "either roster" in result["unmatched"][0]["reason"]


def test_more_than_the_control_holds_is_reported_not_truncated():
    result = parse("Chet is out. Jalen Williams is out. Jaylin Williams is out. "
                   "Alex Caruso is out. Isaiah Hartenstein is out. SGA is out.")
    assert len(result["out_teammates"]) == scenario.MAX_PER_CONTROL
    assert any("more than" in u["reason"] for u in result["unmatched"])


# --------------------------------------------------------------------
# The whole sentence Karma actually typed
# --------------------------------------------------------------------

def test_karmas_scenario_end_to_end():
    """23 Sep, verbatim. One clause of it is measurable; the design
    depends on the other four being visible rather than swallowed."""
    result = parse(
        "Shai will be double teamed and the ball will be handled by Jalen "
        "Williams so SGA plays off ball. Chet is injured so Jaylin Williams "
        "plays a lot, and he is going to assist more."
    )

    assert result["out_teammates"] == ["1628369"], "Chet out is the one to apply"
    assert len(result["applied"]) == 1
    assert result["applied"][0]["player"] == "Chet Holmgren"

    ignored = " | ".join(u["clause"] for u in result["unmatched"])
    assert "double teamed" in ignored
    assert "off ball" in ignored
    assert "assist more" in ignored

    # Named players in an unmeasurable clause must not leak into a
    # control: "the ball will be handled by Jalen Williams" says
    # nothing about his availability.
    assert "1631114" not in result["out_teammates"]


def test_the_summary_says_how_much_was_used():
    result = parse("Chet is out and Shai gets double teamed")
    assert scenario.summary(result) == (
        "1 of 2 applied. The rest are listed so you know they were left out.")
    assert scenario.summary(parse("")) == "Nothing typed yet."


def test_nothing_typed_produces_nothing():
    result = parse("")
    assert result == {"out_teammates": [], "out_opponents": [], "arriving": None,
                      "applied": [], "unmatched": []}
