"""Wording the page makes claims with, checked against the source.

app.py is a Streamlit script -- importing it runs it -- so these read
it as text. That is a blunt instrument, and it is used for exactly two
things that a normal unit test cannot reach.

FIRST: the page must not call the baseline a season average. It stopped
being one when it became a per-minute rate times projected minutes
(engine/minutes.py). Nine input labels and a dropdown option said
"season average" for a while after that was true, which is the quiet
kind of wrong -- nothing breaks, the reader is just told the number is
something it isn't.

SECOND: BASELINE_FULL_SEASON is compared with != in four separate
branches to decide whether a head-to-head baseline is in play. Spelled
as a literal in four places, a rename that missed one would leave a
comparison that never matches, silently putting every prediction on the
head-to-head path. No test would fail. So the literal is allowed to
appear once -- where it is defined.
"""

import os
import re

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def source():
    with open(APP) as f:
        return f.read()


def line_input_labels(text):
    """The first argument of every st.number_input in the file."""
    return re.findall(r'st\.number_input\(\s*\n?\s*"([^"]+)"', text)


def test_no_line_input_claims_to_default_to_a_season_average():
    """The number a blank line falls back to is predictions[col]["base"]
    -- minutes-aware, and taken before the opponent multiplier. It is
    neither a season average nor the figure on the statline card."""
    offenders = [lab for lab in line_input_labels(source())
                 if "season" in lab.lower()]
    assert offenders == [], f"line inputs still claiming a season average: {offenders}"


def test_every_stat_still_has_a_line_input():
    """Dropping the parenthetical must not drop a field: the hit-rate
    loop reads line_inputs[col] for each of the nine tracked stats, and
    a missing one is a KeyError on the rendered page."""
    labels = [lab for lab in line_input_labels(source()) if lab.endswith("line")]
    assert len(labels) >= 9, f"expected nine stat lines, found {labels}"
    assert len(set(labels)) == len(labels), f"duplicate line labels: {labels}"


def test_the_baseline_option_is_spelled_in_exactly_one_place():
    """Four branches compare against it; none of them may hard-code it."""
    text = source()
    assert "BASELINE_FULL_SEASON = " in text
    value = re.search(r'BASELINE_FULL_SEASON = "([^"]+)"', text).group(1)
    assert text.count(f'"{value}"') == 1, (
        f'"{value}" is written out more than once -- compare against '
        "BASELINE_FULL_SEASON instead, or a rename will leave a branch behind")
    assert re.findall(r'baseline_source_input != BASELINE_FULL_SEASON', text), (
        "nothing compares against the constant any more")


def test_the_baseline_option_does_not_call_itself_an_average():
    text = source()
    value = re.search(r'BASELINE_FULL_SEASON = "([^"]+)"', text).group(1)
    assert "average" not in value.lower(), (
        f'the default baseline option is named "{value}", but the baseline '
        "is a per-minute rate times projected minutes, not an average")


# ---------------------------------------------------------------------
# THIRD: the scenario box's two wirings, which no unit test can reach
# because they live in a Streamlit script.
#
# Both failures are silent and both are in the bad direction. If the save
# call loses hypothetical=, a reader's what-if enters the public accuracy
# record and the number quietly stops meaning what the page says it
# means. If parse() is handed the league list instead of the two rosters,
# "Williams is out" becomes ambiguous a dozen ways and the parser refuses
# every name -- the box would appear to work and apply nothing.
# ---------------------------------------------------------------------

import ast


def _calls_named(text, name):
    """Every ast.Call in app.py whose callee ends in `name`."""
    found = []
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        attr = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if attr == name:
            found.append(node)
    return found


def _kwargs(call):
    return {kw.arg for kw in call.keywords if kw.arg}


def test_a_saved_prediction_says_whether_it_came_from_a_scenario():
    calls = _calls_named(source(), "append_prediction_to_log")
    assert calls, "app.py no longer saves single predictions at all?"
    for call in calls:
        assert "hypothetical" in _kwargs(call), (
            "append_prediction_to_log() in app.py does not pass hypothetical=. "
            "A scenario-adjusted projection would be scored into the public "
            "layer accuracy figure shown to every visitor."
        )


def test_the_source_check_can_still_find_arguments():
    """The control. If _kwargs or _calls_named quietly matched nothing,
    the test above would pass forever. These two keywords have been on
    that call since long before the scenario box."""
    calls = _calls_named(source(), "append_prediction_to_log")
    names = _kwargs(calls[0])
    assert "layer_results" in names and "saved_by_email" in names, names


def test_the_parser_is_given_the_two_rosters_and_not_the_league():
    calls = _calls_named(source(), "parse")
    scenario_calls = [
        c for c in calls
        if isinstance(c.func, ast.Attribute)
        and isinstance(c.func.value, ast.Name)
        and c.func.value.id == "scenario"
    ]
    assert len(scenario_calls) == 1, f"expected one scenario.parse() call, got {len(scenario_calls)}"
    call = scenario_calls[0]
    assert {"teammates", "opponents"} <= _kwargs(call), _kwargs(call)

    # player_ids is the whole league (every active player). Passing it
    # here is the specific mistake this pins: it type-checks, it runs,
    # and it makes the feature silently useless.
    passed = {
        kw.arg: kw.value for kw in call.keywords
        if kw.arg in ("teammates", "opponents")
    }
    for arg, value in passed.items():
        assert not (isinstance(value, ast.Name) and value.id == "player_ids"), (
            f"scenario.parse() is being handed the league list as {arg}=")
