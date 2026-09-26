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


def test_a_saved_matchup_says_whether_it_came_from_a_scenario():
    """The scenario box lives on the Full Matchup tab, so the batch save
    is where a reader's hypothesis can now reach the public track record
    -- append_prediction_to_log (Single Player) can no longer produce
    one. A row dict missing this key defaults to not-hypothetical, which
    is the silent direction."""
    text = source()
    calls = _calls_named(text, "append_predictions_batch")
    assert calls, "app.py no longer saves matchups at all?"
    assert '"hypothetical": (_matchup_from_scenario' in text, (
        "the Full Matchup save no longer marks scenario-driven rows. Every "
        "line of a box score built from a typed sentence would be scored "
        "into the layer accuracy figure shown to every visitor."
    )


def test_the_scenario_fills_the_pickers_before_they_are_built():
    """Streamlit forbids writing a widget's key AFTER the widget is
    instantiated -- it raises, loudly, but only on the run where somebody
    actually clicks Read my scenario, which is exactly the path a smoke
    test doesn't take. The whole design depends on this ordering, so the
    ordering is pinned rather than remembered.
    """
    text = source()
    marker = 'st.session_state[f"out_input_{team_a_id}"]'
    assert marker in text, (
        "the scenario no longer fills the out-pickers at all -- if that was "
        "deliberate, this test and its control need rewriting, not deleting")
    write = text.index(marker)
    build = text.index("pick_out_players(\n")
    assert write < build, (
        "the scenario writes the out-pickers' session_state after "
        "pick_out_players() builds them; Streamlit will raise on click"
    )


def test_that_ordering_check_is_looking_at_real_code():
    """The control. Both markers have to exist for the test above to mean
    anything -- if either string drifted, .index() would raise and the
    failure would at least be loud, but a reader of this file deserves to
    see both halves asserted."""
    text = source()
    assert text.count('st.session_state[f"out_input_{team_a_id}"]') == 1
    assert text.count('st.session_state[f"out_input_{team_b_id}"]') == 1
    assert text.count("pick_out_players(") >= 3  # def + two calls


def test_a_reader_supplied_minutes_row_is_marked_hypothetical():
    """A minutes override is an input the model did not measure. The
    layer track record compares each layer's direction against
    {stat}_base, and with an override that base is built on an assumed
    rotation -- scoring it would credit or blame a layer for a number
    the reader typed. hypothetical=False here would be silent."""
    text = source()
    assert "hypothetical=bool(r.get(\"minutes_override\"))" in text, (
        "the Single Player save no longer marks minutes-override rows; a "
        "projection built on assumed minutes would enter the public layer "
        "accuracy figure")


def test_the_override_reaches_the_baseline_and_not_something_else():
    """It has to arrive at the per-minute core. Wired to anything else
    -- stored, displayed, passed to a layer -- the control would appear
    to work and change no number, which is the failure mode a reader
    would never report because the page would look fine."""
    text = source()
    assert "get_season_baseline(\n                    player_id, player_full_name, minutes_override=minutes_override)" in text, \
        "minutes_override is no longer passed to get_season_baseline"
    assert "minutes_override = minutes_input if minutes_input > 0 else None" in text, \
        "0 must mean 'use his projected minutes', matching the line inputs"


def test_the_page_says_when_the_minutes_are_the_readers(
):
    """The default sentence explains where the minutes came from. Left
    up over a reader's own number it would be the app claiming its model
    produced something the reader typed in."""
    text = source()
    assert 'elif r.get("minutes_override"):' in text
    assert "yours, not " in text, "the override branch no longer says whose number it is"


def test_preseason_is_said_on_both_tabs():
    """3-16 October every projection assumes regular-season minutes. One
    wrong number is a mistake; a whole projected box score of them reads
    as authority, so the matchup tab needs it at least as much."""
    text = source()
    assert text.count("season_before_opener()") == 2, (
        f"expected the preseason check on both tabs, found "
        f"{text.count('season_before_opener()')}")


def test_the_calibration_claim_is_not_made_over_reader_set_minutes():
    """Three seasons of backtests measured the range built from the
    MODEL's minutes. spread_at_minutes() has not been backtested at all,
    so leaving that sentence up over an overridden projection would
    borrow a measured figure to vouch for an unmeasured one."""
    text = source()
    assert 'if not r.get("minutes_override") else' in text, (
        "the calibration sentence is no longer conditional on the override")
    assert "has not been\n            backtested" in text or \
           "not been backtested" in text, \
        "the override branch no longer says the scaling is unbacktested"


def test_the_calibration_claim_is_still_made_normally():
    """The control. A conditional that dropped the sentence in both
    branches would pass the test above while quietly deleting a true
    and hard-won claim from every projection on the site."""
    text = source()
    assert "in three seasons of backtests the real result " in text
    assert "landed inside an 80% range about 80% of the time" in text


def test_the_scenario_applies_without_pressing_a_button():
    """The button was a trap. A reader types a sentence, presses the big
    green Predict button because it says Predict, and the sentence is
    ignored with no sign it was -- which reads as "the feature doesn't
    work", not "you missed a step". Observed on the live site."""
    text = source()
    assert '_typed != _already_read' in text, (
        "the scenario no longer applies on a text change; it is back to "
        "requiring the button, and pressing Predict will ignore the box")
    assert 'st.session_state["matchup_scenario_source"] = _typed' in text, (
        "nothing records what was last read, so it would re-apply on every "
        "rerun and stamp over the reader's own edits to the pickers")


def test_the_button_still_exists_for_re_applying():
    """The control. Deleting the button would pass the test above while
    removing the only way to re-apply a scenario after the pickers have
    been edited by hand -- at which point the text is unchanged, so
    nothing fires on its own."""
    assert 'st.button("Read my scenario"' in source()


def test_a_failed_roster_does_not_render_an_empty_picker():
    """st.multiselect drops any session_state value not in `options`,
    silently. With an empty roster that un-marks everyone the reader
    marked out and the projection returns at full strength looking
    normal. Not rendering the widget leaves session_state intact."""
    text = source()
    assert "if not roster:" in text, "the empty-roster branch is gone"
    i, j = text.index("if not roster:"), text.index("out_ids = st.multiselect(")
    assert i < j, "the guard must come before the widget is built"
    assert "return [], []" in text[i:j], (
        "the guard no longer returns early, so the widget is still built "
        "with empty options")


def test_the_picker_is_still_built_when_the_roster_loads():
    """The control. A guard that returned early always would pass the
    test above and remove the who's-out feature entirely."""
    text = source()
    assert "options=[pid for pid, _pname in roster]" in text
    assert "format_func=lambda pid: player_search_label(roster_id_to_name[pid])" in text
def test_the_passing_panel_never_touches_the_projection():
    """It is descriptive. The data does not say WHY a teammate got open,
    so turning it into an adjustment would smuggle a causal claim into a
    descriptive dataset. The guard is that `passing` is never used to
    build a number -- only to print one."""
    text = source()
    import re as _re
    uses = _re.findall(r"passing\.(\w+)", text)
    allowed = {"_fetch_passes", "feeds", "shooting_note", "recent_cutoff", "RECENT_DAYS"}
    assert set(uses) <= allowed, f"unexpected use of engine.passing: {set(uses) - allowed}"
    # and it must not reach the layer machinery
    assert "layer_results[\"passing\"]" not in text
    assert "passing" not in text[text.index("notes_by_layer = {"):
                                 text.index("notes_by_layer = {") + 400]


def test_the_passing_panel_is_opt_in():
    """Two extra nba.com calls per projection, on an endpoint that was
    timing out today. Firing them on every prediction would make the
    page slower for everyone to answer a question most readers are not
    asking."""
    text = source()
    i = text.index('section_heading("Who he passes to"')
    j = text.index("passing._fetch_passes")
    between = text[i:j]
    assert 'st.checkbox(' in between, (
        "the passing panel fetches without being asked; that is two more "
        "calls on every projection")
    assert "extra data fetches" in between, "the label no longer says what it costs"


def test_the_panel_says_it_is_not_an_explanation():
    """A reader who sees 'Shai -> McCain, 14 threes' will reach for a
    cause. The panel has to say it does not have one, next to the list
    rather than somewhere above it."""
    text = source()
    assert "it does not say why anyone " in text
    assert "does not change the projection above" in text


# ---------------------------------------------------------------------
# FOURTH: per-player minutes on Full Matchup. Every one of these pins a
# seam that type-checks and runs while being quietly wrong -- a control
# that changes no number, a row that does not say whose assumption it
# is, a total that disagrees with the row above it.
# ---------------------------------------------------------------------

def test_the_matchup_override_reaches_the_per_minute_core():
    """It has to arrive at get_season_baseline. Wired anywhere else --
    stored, displayed, handed to a layer -- the control would appear to
    work and change nothing, which is the failure a reader never
    reports because the page looks fine."""
    text = source()
    # Spelled out in full, and this matters: the Single Player tab has
    # its own get_season_baseline(..., minutes_override=minutes_override)
    # call, so the short substring was satisfied by the OTHER tab and
    # stayed green when this one was deleted outright. Mutation-tested
    # after that was found.
    assert ("get_season_baseline(\n"
            "            player_id, player_name, minutes_override=minutes_override)"
            ) in text, (
        "predict_player_vs_opponent no longer passes the override to "
        "get_season_baseline")
    assert "minutes_override=his_minutes," in text, (
        "build_team_projection no longer passes each player's minutes to "
        "predict_player_vs_opponent")


def test_the_team_total_is_weighted_at_the_minutes_the_row_used():
    """expected_team_total fits every player into the team's 240
    minutes by his mpg. With the row already built at the reader's
    twenty and the fit still using his logged thirty-four, the row and
    the total would describe different players -- and only the total
    would look wrong, so nobody would know which one to believe."""
    text = source()
    assert "minutes_override=his_minutes)" in text, (
        "total_entry is no longer told about the override")
    # The CONDITION as well as the assignment. Pinning only the
    # assignment left this green when the branch around it was turned
    # off -- the line was still in the file and no longer ran, which is
    # the dead-guard shape this repo keeps finding in its own tests.
    assert "if minutes_override is not None:" in text, (
        "the override no longer reaches total_entry's minutes at all")
    assert "mpg = float(minutes_override)" in text, (
        "total_entry no longer weights him at the minutes his line was built "
        "from")
    gap = text.index("mpg = float(minutes_override)") - text.index(
        "if minutes_override is not None:")
    assert 0 < gap < 700, (
        "the assignment is no longer inside the branch that guards it")


def test_a_row_built_on_the_readers_minutes_says_so():
    """Nine columns of a projected box score all look equally like the
    model's work. One of them being the reader's own assumption is
    obvious while you type it and invisible an hour later -- or to
    whoever you sent the screenshot to."""
    text = source()
    assert 'f"{pname} · {his_minutes:g} min"' in text, (
        "the table no longer marks which lines were built on minutes the "
        "reader supplied")


def test_the_calibration_is_not_claimed_over_matchup_overrides():
    """Same rule as the Single Player tab: three seasons of backtests
    measured the range built from the MODEL's minutes, and
    spread_at_minutes has not been backtested at all."""
    text = source()
    assert "has not been backtested, so the 80% " in text, (
        "the matchup table no longer withdraws the calibration claim over "
        "reader-supplied minutes")


def test_an_assumed_rotation_does_not_enter_the_public_record():
    """A minutes override is an input the model did not measure, so the
    row cannot be scored into the layer accuracy figure every visitor
    sees. Unlike the out-list it changes one player's line and nobody
    else's, so it is marked per row rather than over the whole box
    score -- and hypothetical=False here would be silent."""
    text = source()
    assert 't["player_id"] in _assumed_minutes' in text, (
        "rows built on reader-supplied minutes are no longer marked "
        "hypothetical")
    assert "_assumed_minutes = set(team_a_minutes) | set(team_b_minutes)" in text


def test_the_scenario_fills_the_minutes_controls_before_they_are_built():
    """Streamlit forbids writing a widget's key AFTER the widget is
    instantiated. Same ordering as the out-pickers, same reason it is
    pinned rather than remembered: it raises only on the run where
    somebody actually types a sentence with minutes in it."""
    text = source()
    marker = 'st.session_state[f"min_players_{_side}"] = list(_said)'
    assert marker in text, "the scenario no longer fills the minutes controls"
    # The CALL, not the def. pick_minutes is defined hundreds of lines
    # above the scenario block and runs hundreds of lines below it --
    # comparing against the def would pin the opposite of the rule and
    # pass for the wrong reason.
    assert text.index(marker) < text.index("team_a_minutes = pick_minutes("), (
        "the scenario writes the minutes controls after pick_minutes builds "
        "them; Streamlit will raise on the run that reads a sentence")
    assert 'st.session_state[f"min_value_{_side}_{_who}"] = int(_long)' in text, (
        "only the multiselect is filled, so the reader would see a player "
        "with a minutes box reading zero -- which means 'use the model's' "
        "and is not what the sentence said")


def test_a_failed_roster_does_not_empty_the_minutes_picker():
    """st.multiselect drops any session_state value not in `options`.
    The out-picker learned this the hard way in #70; the minutes picker
    is built from the same roster fetch and would lose a reader's typed
    minutes to one bad minute on nba.com."""
    text = source()
    start = text.index("def pick_minutes(")
    body = text[start:start + 2000]
    assert "if not roster:" in body and "return {}" in body, (
        "pick_minutes no longer refuses to build the widget on an empty "
        "roster")
    assert body.index("if not roster:") < body.index("st.multiselect(")


def test_the_preseason_note_no_longer_says_there_is_nothing_to_do():
    """It said 'There is no minutes control on this tab yet'. Leaving
    that up beside the control would send readers to the other tab to
    do something they can now do here."""
    text = source()
    assert "no minutes control on this tab" not in text, (
        "the preseason banner still tells readers this tab has no minutes "
        "control")
    assert "set it above and his " in text  # wraps in the source
