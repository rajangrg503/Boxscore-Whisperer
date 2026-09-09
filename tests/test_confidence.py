"""Tests for engine/confidence.py."""

from engine import confidence
from engine.adjustments.base import AdjustmentResult


def _result(layer, applied, data_quality, value=None):
    return AdjustmentResult(
        layer=layer, value=value or {"_all": 1.0}, note="",
        data_quality=data_quality, sample_n=0, applied=applied,
    )


# ---------- _label_for_score: exact integer boundaries ----------
# score_prediction()'s real weights (baseline in {0,20,40}, each layer
# in {0,5,15}) mean every ACHIEVABLE score is a multiple of 5 -- 69 and
# 39 can never actually occur from real inputs. Testing _label_for_score
# directly at these exact integers proves the >= comparison itself is
# correct, not just that one real example happened to land on a
# reachable multiple of 5.

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
    # 40 (baseline) + 5 scorable layers x 15 (real_current) = 115
    assert confidence._label_for_score(115) == "High"


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

def test_baseline_high_no_layers_scores_40_medium_no_reason():
    result = confidence.score_prediction({}, baseline_sample_n=20)
    assert result.score == 40
    assert result.label == "Medium"
    assert result.reasons == []


def test_baseline_low_band_scores_20_with_reason():
    result = confidence.score_prediction({}, baseline_sample_n=5)
    assert result.score == 20
    assert any("5 game(s)" in r for r in result.reasons)


def test_baseline_thin_scores_0_with_reason():
    result = confidence.score_prediction({}, baseline_sample_n=2)
    assert result.score == 0
    assert result.label == "Low"
    assert any("2 game(s)" in r for r in result.reasons)


# ---------- score_prediction: per-layer data_quality weights ----------

def test_applied_real_current_layer_adds_15_no_reason():
    layer_results = {"opponent_defense": _result("opponent_defense", True, "real_current")}
    result = confidence.score_prediction(layer_results, baseline_sample_n=20)
    assert result.score == 40 + 15
    assert result.reasons == []


def test_applied_fallback_layer_adds_5_with_reason():
    layer_results = {"opponent_defense": _result("opponent_defense", True, "real_fallback_season")}
    result = confidence.score_prediction(layer_results, baseline_sample_n=20)
    assert result.score == 40 + 5
    assert any("opponent defense" in r.lower() and "real fallback season" in r for r in result.reasons)


def test_applied_thin_sample_layer_adds_5_with_reason():
    layer_results = {"scheme": _result("scheme", True, "real_thin_sample")}
    result = confidence.score_prediction(layer_results, baseline_sample_n=20)
    assert result.score == 40 + 5


def test_applied_unavailable_data_quality_adds_0_with_reason():
    # Unusual (applied=True with data_quality="unavailable" doesn't happen
    # in the real layer modules today), but the weight table must still
    # handle it safely rather than assume it can't occur.
    layer_results = {"scheme": _result("scheme", True, "unavailable")}
    result = confidence.score_prediction(layer_results, baseline_sample_n=20)
    assert result.score == 40 + 0
    assert any("scheme" in r.lower() for r in result.reasons)


def test_unrecognized_data_quality_defaults_to_0_not_a_crash():
    layer_results = {"scheme": _result("scheme", True, "some_future_value")}
    result = confidence.score_prediction(layer_results, baseline_sample_n=20)
    assert result.score == 40 + 0


def test_not_applied_layer_is_neutral_no_score_no_reason():
    layer_results = {"opponent_defense": _result("opponent_defense", False, "unavailable")}
    result = confidence.score_prediction(layer_results, baseline_sample_n=20)
    assert result.score == 40
    assert result.reasons == []


def test_layer_missing_from_dict_entirely_is_neutral():
    result = confidence.score_prediction({}, baseline_sample_n=20)
    assert result.score == 40
    assert result.reasons == []


# ---------- defender_matchup / NEVER_APPLIED_BY_DESIGN exclusion ----------

def test_defender_matchup_excluded_even_when_not_applied():
    layer_results = {"defender_matchup": _result("defender_matchup", False, "unavailable")}
    result = confidence.score_prediction(layer_results, baseline_sample_n=20)
    assert result.score == 40
    assert result.reasons == []


def test_defender_matchup_excluded_even_if_hypothetically_applied():
    # Proves the exclusion happens BEFORE the applied/data_quality check,
    # not merely as a side effect of defender_matchup always being
    # applied=False in practice. Even a synthetic, real-current,
    # applied=True defender_matchup result must contribute nothing.
    layer_results = {"defender_matchup": _result("defender_matchup", True, "real_current")}
    result = confidence.score_prediction(layer_results, baseline_sample_n=20)
    assert result.score == 40
    assert result.reasons == []


# ---------- Multiple layers combine correctly ----------

def test_multiple_applied_layers_sum_correctly():
    layer_results = {
        "opponent_defense": _result("opponent_defense", True, "real_current"),
        "scheme": _result("scheme", True, "real_current"),
        "missing_teammates": _result("missing_teammates", False, "unavailable"),
        "missing_opponents": _result("missing_opponents", False, "unavailable"),
        "new_teammate": _result("new_teammate", False, "unavailable"),
        "defender_matchup": _result("defender_matchup", False, "unavailable"),
    }
    result = confidence.score_prediction(layer_results, baseline_sample_n=70)
    assert result.score == 40 + 15 + 15
    assert result.label == "High"
    assert result.reasons == []


# ---------- Integration: tonight's real saved LeBron/Celtics prediction ----------
# Hardcoded from the real prediction_log.csv row (id cba21625) saved during
# live browser verification, not synthetic -- same pattern as
# tests/test_player_resolution.py's real recorded ids fixture. baseline_sample_n
# is season_n=70 (confirmed separately against the same row: no head-to-head
# baseline selected, no extra sources, so 70 + 0 + 0 = 70).

def test_real_lebron_celtics_prediction_scores_70_high():
    layer_results = {
        "opponent_defense": _result("opponent_defense", True, "real_current", {"_all": 0.98678094131319}),
        "missing_opponents": _result("missing_opponents", False, "unavailable", {"_all": 1.0}),
        "scheme": _result("scheme", True, "real_current", {"_all": 1.0101421800947867}),
        "missing_teammates": _result("missing_teammates", False, "unavailable",
                                      {"PTS": 1.0, "AST": 1.0, "REB": 1.0, "STL": 1.0,
                                       "BLK": 1.0, "FG3M": 1.0, "TOV": 1.0}),
        "new_teammate": _result("new_teammate", False, "unavailable",
                                 {"PTS": 1.0, "AST": 1.0, "REB": 1.0, "STL": 1.0,
                                  "BLK": 1.0, "FG3M": 1.0, "TOV": 1.0}),
        "defender_matchup": _result("defender_matchup", False, "unavailable", {"_all": 1.0}),
    }
    result = confidence.score_prediction(layer_results, baseline_sample_n=70)
    assert result.score == 70
    assert result.label == "High"
    assert result.reasons == []
