"""Tests for engine/confidence.py."""

from engine import confidence
from engine.adjustments.base import AdjustmentResult


def _result(layer, applied, data_quality, value=None):
    return AdjustmentResult(
        layer=layer, value=value or {"_all": 1.0}, note="",
        data_quality=data_quality, sample_n=0, applied=applied,
    )


# ---------- _label_for_score: exact integer boundaries ----------
# score_prediction()'s real weights are multiples of 5, so 69 and 39 can
# never actually occur; testing _label_for_score directly at these exact
# integers proves the >= comparison itself is correct.

def test_label_boundary_high_medium_69_is_medium():
    assert confidence._label_for_score(69) == "Medium"


def test_label_boundary_high_medium_70_is_high():
    assert confidence._label_for_score(70) == "High"


def test_label_boundary_medium_low_39_is_low():
    assert confidence._label_for_score(39) == "Low"


def test_label_boundary_medium_low_40_is_medium():
    assert confidence._label_for_score(40) == "Medium"


def test_label_zero_is_low():
    assert confidence._label_for_score(0) == "Low"


def test_label_max_possible_is_high():
    # 80: a full-season baseline with nothing taken off
    assert confidence._label_for_score(80) == "High"


def test_label_arbitrary_integers_across_all_three_bands():
    # Not just the boundaries -- arbitrary integers well inside each band,
    # unconstrained by score_prediction()'s actual weight granularity.
    assert confidence._label_for_score(1) == "Low"
    assert confidence._label_for_score(17) == "Low"
    assert confidence._label_for_score(41) == "Medium"
    assert confidence._label_for_score(55) == "Medium"
    assert confidence._label_for_score(71) == "High"
    assert confidence._label_for_score(100) == "High"


# ---------- score_prediction: baseline sample size ----------

def test_full_baseline_no_layers_is_high_no_reason():
    result = confidence.score_prediction({}, baseline_sample_n=20)
    assert result.score == 80
    assert result.label == "High"
    assert result.reasons == []


def test_mid_baseline_is_medium_with_reason():
    result = confidence.score_prediction({}, baseline_sample_n=5)
    assert result.score == 55
    assert result.label == "Medium"
    assert any("5 game(s)" in r for r in result.reasons)


def test_thin_baseline_is_low_with_reason():
    result = confidence.score_prediction({}, baseline_sample_n=2)
    assert result.score == 20
    assert result.label == "Low"
    assert any("2 game(s)" in r for r in result.reasons)


# ---------- score_prediction: layers only ever take points away ----------

def _r(layer, applied, data_quality, sample_n=0, value=None):
    res = _result(layer, applied, data_quality, value)
    res.sample_n = sample_n
    return res


def test_current_season_defense_costs_nothing():
    layer_results = {"opponent_defense": _r("opponent_defense", True, "real_current")}
    result = confidence.score_prediction(layer_results, baseline_sample_n=20)
    assert result.score == 80
    assert result.reasons == []


def test_last_season_defense_costs_5_with_reason():
    layer_results = {"opponent_defense": _r("opponent_defense", True, "real_fallback_season")}
    result = confidence.score_prediction(layer_results, baseline_sample_n=20)
    assert result.score == 75
    assert result.label == "High"
    assert any("last season" in r for r in result.reasons)


def test_missing_teammates_from_few_games_costs_15():
    layer_results = {"missing_teammates": _r("missing_teammates", True, "real_current", sample_n=5)}
    result = confidence.score_prediction(layer_results, baseline_sample_n=20)
    assert result.score == 65
    assert result.label == "Medium"
    assert any("only 5 game(s)" in r for r in result.reasons)


def test_missing_teammates_from_some_games_costs_5():
    layer_results = {"missing_teammates": _r("missing_teammates", True, "real_current", sample_n=12)}
    result = confidence.score_prediction(layer_results, baseline_sample_n=20)
    assert result.score == 75


def test_missing_teammates_from_many_games_costs_nothing():
    layer_results = {"missing_teammates": _r("missing_teammates", True, "real_current", sample_n=25)}
    result = confidence.score_prediction(layer_results, baseline_sample_n=20)
    assert result.score == 80
    assert result.reasons == []


def test_scheme_costs_15_even_with_real_data():
    layer_results = {"scheme": _r("scheme", True, "real_current")}
    result = confidence.score_prediction(layer_results, baseline_sample_n=20)
    assert result.score == 65
    assert any("scheme" in r.lower() for r in result.reasons)


def test_new_teammate_costs_10():
    layer_results = {"new_teammate": _r("new_teammate", True, "real_current")}
    result = confidence.score_prediction(layer_results, baseline_sample_n=20)
    assert result.score == 70
    assert any("new teammate" in r.lower() for r in result.reasons)


def test_unknown_weak_layer_costs_5_not_a_crash():
    layer_results = {"missing_opponents": _r("missing_opponents", True, "some_future_value")}
    result = confidence.score_prediction(layer_results, baseline_sample_n=20)
    assert result.score == 80
    layer_results = {"missing_opponents": _r("missing_opponents", True, "manual_estimate")}
    result = confidence.score_prediction(layer_results, baseline_sample_n=20)
    assert result.score == 75


def test_not_applied_layer_is_neutral_no_score_no_reason():
    layer_results = {
        "scheme": _r("scheme", False, "unavailable"),
        "missing_opponents": _r("missing_opponents", False, "real_current", sample_n=1),
    }
    result = confidence.score_prediction(layer_results, baseline_sample_n=20)
    assert result.score == 80
    assert result.reasons == []


def test_score_never_goes_below_zero():
    layer_results = {
        "scheme": _r("scheme", True, "real_current"),
        "new_teammate": _r("new_teammate", True, "real_current"),
        "missing_teammates": _r("missing_teammates", True, "real_current", sample_n=3),
    }
    result = confidence.score_prediction(layer_results, baseline_sample_n=1)
    assert result.score == 0
    assert result.label == "Low"


# ---------- defender_matchup / NEVER_APPLIED_BY_DESIGN exclusion ----------

def test_defender_matchup_excluded_even_if_hypothetically_applied():
    layer_results = {"defender_matchup": _r("defender_matchup", True, "manual_estimate")}
    result = confidence.score_prediction(layer_results, baseline_sample_n=20)
    assert result.score == 80
    assert result.reasons == []


# ---------- The bug this rewrite fixes ----------

def test_stacking_thin_adjustments_lowers_confidence():
    """Live 16 Sep: plain Jokic vs OKC showed Medium, Jokic with every
    advanced option showed High. More untested adjustments must never
    raise confidence."""
    plain = {"opponent_defense": _r("opponent_defense", True, "real_fallback_season")}
    stacked = dict(plain)
    stacked.update({
        "missing_teammates": _r("missing_teammates", True, "real_current", sample_n=5),
        "missing_opponents": _r("missing_opponents", False, "real_current", sample_n=1),
        "new_teammate": _r("new_teammate", False, "unavailable"),
        "scheme": _r("scheme", True, "real_current"),
        "defender_matchup": _r("defender_matchup", False, "real_current"),
    })
    plain_result = confidence.score_prediction(plain, baseline_sample_n=71)
    stacked_result = confidence.score_prediction(stacked, baseline_sample_n=71)
    assert plain_result.label == "High"
    assert stacked_result.label == "Medium"
    assert stacked_result.score < plain_result.score
    assert len(stacked_result.reasons) == 3


# ---------- Integration: the real saved LeBron/Celtics prediction ----------
# From prediction_log.csv row cba21625 (season baseline, 70 games). It
# applied a real-data scheme adjustment; scheme can't be backtested, so
# it now costs 15 points: 80 - 15 = 65, Medium (was 70, High).

def test_real_lebron_celtics_prediction_scores_65_medium():
    layer_results = {
        "opponent_defense": _r("opponent_defense", True, "real_current", value={"_all": 0.98678094131319}),
        "missing_opponents": _r("missing_opponents", False, "unavailable", value={"_all": 1.0}),
        "scheme": _r("scheme", True, "real_current", value={"_all": 1.0101421800947867}),
        "missing_teammates": _r("missing_teammates", False, "unavailable"),
        "new_teammate": _r("new_teammate", False, "unavailable"),
        "defender_matchup": _r("defender_matchup", False, "unavailable", value={"_all": 1.0}),
    }
    result = confidence.score_prediction(layer_results, baseline_sample_n=70)
    assert result.score == 65
    assert result.label == "Medium"
    assert result.reasons == ["Scheme: an untested estimate"]


# ---- a baseline from a season that has finished --------------------------
# Until a player has five games this season, the baseline is last
# season's whole log. The rubric was counting those seventy games
# exactly as it would count seventy from this season.
#
# It is a DEDUCTION, not a cap on the label. The bands at the top of
# engine/confidence.py are the documented meaning of High and Medium;
# a rule overriding them from outside would make the published label
# stop meaning what the rubric says.
def test_a_prior_season_baseline_costs_points():
    from engine.confidence import score_prediction, PRIOR_SEASON_PENALTY
    plain = score_prediction({}, baseline_sample_n=70)
    early = score_prediction({}, baseline_sample_n=70,
                             baseline_is_prior_season=True)
    assert early.score == plain.score - PRIOR_SEASON_PENALTY


def test_a_plain_early_season_projection_still_says_high():
    """Measured, not assumed. early_season_sweep.py scored 3,801 real
    first-five-games projections against the same players' later
    in-season ones: paired within player-season, last season's
    baseline was a median 3.0% worse, CI [0.998, 1.059], worse for
    only 53.2% of 742 players.

    An effect that small does not cost a projection its label. An
    earlier version of this deducted 15 -- the file's charge for an
    untested estimate -- and moved every opening-fortnight projection
    to Medium on the strength of a guess."""
    from engine.confidence import score_prediction
    result = score_prediction({}, baseline_sample_n=70,
                              baseline_is_prior_season=True)
    assert result.label == "High"


def test_but_it_still_says_where_the_number_came_from():
    """The disclosure is the honest part and does not depend on the
    penalty being large."""
    from engine.confidence import score_prediction
    result = score_prediction({}, baseline_sample_n=70,
                              baseline_is_prior_season=True)
    assert any("last season" in reason.lower() for reason in result.reasons)


def test_the_penalty_matches_the_one_for_last_seasons_defence_ratings():
    """Five, anchored on the closest comparable already in this file,
    rather than picked."""
    from engine.confidence import PRIOR_SEASON_PENALTY, DEFENSE_FALLBACK_PENALTY
    assert PRIOR_SEASON_PENALTY == DEFENSE_FALLBACK_PENALTY


def test_the_label_always_follows_the_score_nothing_overrides_it():
    """The invariant the deduction exists to preserve. The bands are
    the documented meaning of High and Medium; if a projection scores
    High after the deduction it says High. An earlier version capped
    the label from outside the rubric, which made the published word
    stop meaning what the file says it means."""
    from engine.confidence import score_prediction, _label_for_score
    for n in (1, 4, 5, 19, 20, 70):
        for early in (False, True):
            result = score_prediction({}, baseline_sample_n=n,
                                      baseline_is_prior_season=early)
            assert result.label == _label_for_score(result.score)


def test_the_reason_is_named_not_just_the_score_lowered():
    """A badge that drops with no explanation is worse than one that
    does not drop at all."""
    from engine.confidence import score_prediction
    result = score_prediction({}, baseline_sample_n=70,
                              baseline_is_prior_season=True)
    assert any("last season" in reason.lower() for reason in result.reasons)


def test_the_score_never_goes_below_zero():
    from engine.confidence import score_prediction
    result = score_prediction({}, baseline_sample_n=1,
                              baseline_is_prior_season=True)
    assert result.score >= 0
    assert result.label == "Low"
