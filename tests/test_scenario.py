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
    assert result == {"out_teammates": [], "out_opponents": [],
                      "minutes_teammates": {}, "minutes_opponents": {},
                      "arriving": None, "applied": [], "unmatched": []}


# --------------------------------------------------------------------
# merge(): what the reader clicked, plus what they typed
# --------------------------------------------------------------------

NAMES = dict(THUNDER + SPURS)


def merged(text, manual_teammates=(), manual_opponents=()):
    return scenario.merge(
        parse(text), manual_teammates=manual_teammates,
        manual_opponents=manual_opponents, name_of=NAMES.get,
    )


def test_a_typed_name_joins_the_ones_already_picked():
    teammates, opponents, notes = merged("Chet is out", manual_teammates=["Alex Caruso"])
    assert teammates == ["Alex Caruso", "Chet Holmgren"]
    assert opponents == [] and notes == []


def test_the_same_player_clicked_and_typed_takes_one_slot():
    teammates, _opponents, notes = merged(
        "Chet is out", manual_teammates=["Chet Holmgren"])
    assert teammates == ["Chet Holmgren"]
    assert notes == []


def test_clicks_win_the_cap_and_the_overflow_is_reported():
    """The reason merge() exists. Five clicks plus a typed sixth: the
    clicks are unambiguous and the sentence was parsed, so the clicks
    keep the slots -- but a player the reader believes is out who is
    quietly not in the model is the exact wrong number this app is
    supposed to not produce, so it is said out loud."""
    clicked = ["Alex Caruso", "Isaiah Hartenstein", "Jalen Williams",
               "Jaylin Williams", "Shai Gilgeous-Alexander"]
    teammates, _opponents, notes = merged("Chet is out", manual_teammates=clicked)
    assert teammates == clicked, "the five clicked names are untouched"
    assert len(notes) == 1
    assert notes[0]["clause"] == "Chet Holmgren"
    assert "already holds 5" in notes[0]["reason"]


def test_under_the_cap_the_typed_name_is_added():
    """The control on the test above: four clicks leave room, so the
    typed name goes in and nothing is reported. A merge that always
    overflowed would pass the cap test while never applying anything."""
    clicked = ["Alex Caruso", "Isaiah Hartenstein", "Jalen Williams",
               "Jaylin Williams"]
    teammates, _opponents, notes = merged("Chet is out", manual_teammates=clicked)
    assert teammates == clicked + ["Chet Holmgren"]
    assert notes == []


def test_an_unresolvable_id_is_reported_not_passed_on_as_an_id():
    """Downstream every consumer expects a name string. Letting an id
    through would read as a player called "1628369"."""
    teammates, _opponents, notes = scenario.merge(
        {"out_teammates": ["9999999"], "out_opponents": []},
        name_of=lambda pid: None,
    )
    assert teammates == []
    assert len(notes) == 1 and "could not be matched" in notes[0]["reason"]


def test_the_two_controls_stay_separate():
    teammates, opponents, notes = merged("Chet is out and Wembanyama is out")
    assert teammates == ["Chet Holmgren"]
    assert opponents == ["Victor Wembanyama"]
    assert notes == []


def test_nothing_typed_leaves_the_clicks_exactly_as_they_were():
    clicked = ["Alex Caruso", "Chet Holmgren"]
    teammates, opponents, notes = merged("", manual_teammates=clicked)
    assert teammates == clicked
    assert opponents == [] and notes == []


def test_a_blocked_summary_does_not_claim_nothing_was_measurable():
    """When the page could not load a roster, nothing was CHECKED. Saying
    "none of this maps to a layer the model measures" would be a
    different claim and a false one, so a caller can supply its own line.
    """
    blocked = parse("")
    blocked.update({
        "unmatched": [{"clause": "Chet is out", "reason": "no roster available"}],
        "blocked": "Couldn't check any names: no roster loaded.",
    })
    assert scenario.summary(blocked) == "Couldn't check any names: no roster loaded."


def test_an_ordinary_summary_still_counts():
    """The control: without a blocked line the count is unchanged, so the
    early return cannot swallow the normal path."""
    assert scenario.summary(parse("Chet is out and Shai gets double teamed")) == (
        "1 of 2 applied. The rest are listed so you know they were left out.")
    assert scenario.summary(parse("Shai gets double teamed")) == (
        "None of the 1 thing(s) you described maps to a layer the model measures.")


def test_the_player_being_projected_is_told_apart_from_a_stranger():
    """The page removes the subject from the teammate roster -- he cannot
    be his own missing teammate -- so without `subject` this comes back
    as "no player from either roster named here". That is true of the
    lists handed in and misleading about the world."""
    result = scenario.parse(
        "SGA is out", teammates=[p for p in THUNDER if p[0] != "2544"],
        opponents=SPURS, subject=("2544", "Shai Gilgeous-Alexander"),
    )
    assert result["out_teammates"] == [] and result["out_opponents"] == []
    reason = result["unmatched"][0]["reason"]
    assert "being projected" in reason
    assert "Shai Gilgeous-Alexander" in reason


def test_a_genuine_stranger_still_gets_the_plain_message():
    """The control. A subject check that fired for everybody would make
    the message above useless and hide real typos."""
    result = scenario.parse(
        "LeBron James is out", teammates=THUNDER, opponents=SPURS,
        subject=("2544", "Shai Gilgeous-Alexander"),
    )
    assert "either roster" in result["unmatched"][0]["reason"]


def test_omitting_the_subject_changes_nothing_else():
    """It is optional, so every existing caller keeps working."""
    result = scenario.parse("Chet is out", teammates=THUNDER, opponents=SPURS)
    assert result["out_teammates"] == ["1628369"]


# --------------------------------------------------------------------
# The shape of the sentence, which is a separate problem from the
# shape of the name.
#
# Both rules below were written after watching the live site refuse a
# sentence it could mostly read. Every one of them is paired with a
# control, because the obvious way to make a run-on work -- resolve the
# clause to whichever name looks best -- would pass the applying tests
# and destroy the refusal this whole file is built on.
# --------------------------------------------------------------------

def test_a_run_on_still_applies_the_half_it_measures():
    """25 Sep, verbatim, off the phone. No punctuation between "out"
    and the next subject, so the old splitter handed the matcher one
    clause with two names in it, called it ambiguous, and applied
    nothing at all -- including "chet is out", which is the layer this
    app measures best. Reported as "I tried it but I dont think it
    works", which is the correct reading of what it did."""
    result = parse("chet is out sga will be double teamed. jalen williams "
                   "will only play 20 minutes due to minute restrictions")

    assert result["out_teammates"] == ["1628369"]
    assert result["applied"][0]["clause"] == "chet is out"

    # The minutes clause applies too, since #73 gave this tab a minutes
    # control. Before that it was refused with "no measured layer for
    # this", which was true when it was written and stopped being true
    # the day the control shipped.
    assert result["minutes_teammates"] == {"1631114": 20}

    ignored = [u["clause"] for u in result["unmatched"]]
    assert ignored == ["sga will be double teamed"], (
        "only the clause with no layer behind it should be refused now")


def test_a_run_on_does_not_resolve_an_ambiguous_name():
    """THE control for the split. Cutting a clause in two must not be
    a back door into guessing: the second half here names a surname two
    Thunder players share, and it has to be refused exactly as it would
    be on its own."""
    result = parse("chet is out williams is out")

    assert result["out_teammates"] == ["1628369"], "Chet still applies"
    assert len(result["unmatched"]) == 1
    reason = result["unmatched"][0]["reason"]
    assert "Jalen Williams" in reason and "Jaylin Williams" in reason


def test_the_cut_keeps_the_reader_s_own_words():
    """The panel quotes the clause back. Cutting the folded text would
    echo a lower-cased, punctuation-stripped version of a sentence the
    reader can see on screen, which reads as the app having misheard
    them."""
    result = parse("Chet is OUT, SGA will be double teamed")
    assert result["applied"][0]["clause"] == "Chet is OUT"
    assert result["unmatched"][0]["clause"] == "SGA will be double teamed"


def test_a_state_phrase_before_any_name_does_not_cut():
    """The control for the cut's precondition. "missing Chet Caruso"
    names two players and asserts nothing about either one yet, so it
    is as ambiguous as it looks -- a cut there would invent a boundary
    the sentence does not have."""
    assert parse("missing Chet")["out_teammates"] == ["1628369"]
    result = parse("missing Chet Caruso")
    assert result["out_teammates"] == []
    assert "could mean" in result["unmatched"][0]["reason"]


def test_a_list_of_names_sharing_one_verb_marks_all_of_them_out():
    """The quieter half of the bug, and the worse one. "and" is a
    clause break, so this used to split into "chet" -- a name with no
    verb, dropped as unmeasurable -- and "jalen williams are out",
    which applied. One applied, one silently gone, and a projected
    lineup that was not the one described."""
    result = parse("Chet and Jalen Williams are out")
    assert sorted(result["out_teammates"]) == sorted(["1628369", "1631114"])
    assert len(result["applied"]) == 2


def test_a_longer_list_carries_down_the_whole_chain():
    result = parse("Chet, Jalen Williams, and Alex Caruso are out")
    assert sorted(result["out_teammates"]) == sorted(
        ["1628369", "1631114", "1627936"])


def test_a_claim_never_inherits_the_next_clause_s_state():
    """THE control for the carry, and the reason it is gated on the
    clause being nothing but a name. "SGA plays more" is an assertion
    of its own; inheriting "out" from the clause after it would mark
    out the one player the reader just said would play MORE -- silent,
    and in the direction that flatters the projection."""
    result = parse("SGA plays more, Chet is out")
    assert result["out_teammates"] == ["1628369"]
    assert "2544" not in result["out_teammates"]
    assert any("plays more" in u["clause"] for u in result["unmatched"])


def test_a_state_is_not_carried_across_a_full_stop():
    """A comma lists; a full stop starts again. "Chet plays." is not a
    list item waiting for a verb, whatever follows it."""
    result = parse("Chet plays. Jalen Williams is out")
    assert result["out_teammates"] == ["1631114"]


def test_a_bare_ambiguous_name_in_a_list_is_still_refused():
    """The carry hands a state to a name; it does not decide who the
    name is."""
    result = parse("Williams and Chet are out")
    assert result["out_teammates"] == ["1628369"]
    assert any("Jalen Williams" in u["reason"] and "Jaylin Williams" in u["reason"]
               for u in result["unmatched"])


# --------------------------------------------------------------------
# Minutes, which are the one number a reader can assert without the
# model inventing anything: the baseline is a per-minute rate times
# projected minutes, so replacing the minutes replaces exactly one
# input and leaves his real rates alone.
#
# The hazard here is the opposite of the ambiguous-name one. There the
# risk was acting on a guess; here it is refusing an answer the reader
# actually gave, because the phrase they gave it in ("minutes
# restriction") is on a list of phrases that mean "we don't know".
# --------------------------------------------------------------------

def test_minutes_fill_the_control():
    result = parse("Chet plays 24 minutes")
    assert result["minutes_teammates"] == {"1628369": 24}
    assert result["minutes_opponents"] == {}
    assert result["applied"][0]["control"] == "Minutes for a player"
    assert "24 minutes" in result["applied"][0]["player"]


def test_an_opponent_gets_his_own_control():
    result = parse("Wembanyama plays 30 mins")
    assert result["minutes_opponents"] == {"1630170": 30}
    assert result["minutes_teammates"] == {}


def test_a_number_beats_the_phrase_that_says_we_do_not_know():
    """"minutes restriction" is on the undecided list, and undecided is
    checked before almost everything because "questionable" must never
    be rounded to in or out. But a reader who writes "only 20 minutes
    due to a minutes restriction" HAS given the number -- refusing that
    as half-available is the app ignoring the answer while quoting the
    question back."""
    result = parse("Jalen Williams will only play 20 minutes due to a "
                   "minutes restriction")
    assert result["minutes_teammates"] == {"1631114": 20}
    assert result["unmatched"] == []


def test_the_phrase_without_a_number_is_still_undecided():
    """The control. The reordering must not swallow the undecided list:
    "on a minutes restriction" with no number still says nothing the
    model can use, and guessing a number for him would be inventing the
    one input this control exists to take from the reader."""
    result = parse("Chet is on a minutes restriction")
    assert result["minutes_teammates"] == {}
    assert "half-available" in result["unmatched"][0]["reason"]


def test_out_outranks_a_number_in_the_same_clause():
    """"out for 20 minutes" is a contradiction, and the safe reading of
    a contradiction is the one that removes a player rather than the one
    that invents a rotation for him."""
    result = parse("Chet is out for 20 minutes")
    assert result["out_teammates"] == ["1628369"]
    assert result["minutes_teammates"] == {}


@pytest.mark.parametrize("typed,number", [
    ("Chet plays 2 minutes", "2"),
    ("Chet plays 60 minutes", "60"),
])
def test_a_number_that_is_not_a_rotation_says_so(typed, number):
    """A per-minute rate multiplied by a nonsense number is a nonsense
    line delivered with a straight face. And the reason has to name the
    range: "no measured layer for this" would be false here -- the layer
    exists and the number was the problem, which is a thing the reader
    can fix."""
    result = parse(typed)
    assert result["minutes_teammates"] == {}
    reason = result["unmatched"][0]["reason"]
    assert number in reason and "4-48" in reason


def test_an_ambiguous_name_is_not_resolved_by_a_number():
    """The control this whole file exists for, applied to the new
    branch. A minutes clause must refuse an ambiguous name exactly as
    an out clause does -- setting minutes for the wrong Williams is the
    same silent wrongness as marking the wrong one out."""
    result = parse("Williams plays 20 minutes")
    assert result["minutes_teammates"] == {}
    reason = result["unmatched"][0]["reason"]
    assert "Jalen Williams" in reason and "Jaylin Williams" in reason


def test_more_players_than_the_control_holds_is_reported():
    result = parse("Chet plays 20 minutes. Jalen Williams plays 21 minutes. "
                   "Jaylin Williams plays 22 minutes. Alex Caruso plays 23 minutes. "
                   "Isaiah Hartenstein plays 24 minutes. SGA plays 25 minutes.")
    assert len(result["minutes_teammates"]) == scenario.MAX_PER_CONTROL
    assert any("more than" in u["reason"] for u in result["unmatched"])


def test_out_and_minutes_can_both_come_from_one_sentence():
    result = parse("Chet is out, Jalen Williams plays 20 minutes")
    assert result["out_teammates"] == ["1628369"]
    assert result["minutes_teammates"] == {"1631114": 20}
    assert len(result["applied"]) == 2
