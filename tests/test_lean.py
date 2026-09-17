"""Tests for engine/lean.py -- the Single Player "strong lean"
(feature computation, thresholding, the rendered lines) and the
consistency of engine/lean_models.json with the feature code."""

import math
import re

import numpy as np
import pandas as pd
import pytest

from engine import lean


def _log(n_regular=12, extra_rows=True, shuffle=True):
    """Regular-season games spanning Nov/Dec (so a string sort of
    GAME_DATE would misorder them), plus a playoff and a preseason row
    that must be ignored. Game i (1-based): PTS=2i, FGA=i, MIN 30 for
    games 1-7 and 35 after, BLK=0, every other stat 1."""
    dates = pd.date_range("2025-11-25", periods=n_regular, freq="D")
    rows = []
    for i, d in enumerate(dates, start=1):
        rows.append({"GAME_DATE": d.strftime("%b %d, %Y"), "Game_ID": f"00225{i:05d}",
                     "MATCHUP": "DEN vs. OKC", "PTS": 2 * i, "FGA": i,
                     "MIN": 30 if i <= 7 else 35, "BLK": 0,
                     **{c: 1 for c in ["AST", "REB", "STL", "FG3M", "TOV", "FG3A", "OREB"]}})
    if extra_rows:
        base = {c: 99 for c in lean.STATS} | {"FGA": 99, "MIN": 48, "MATCHUP": "DEN @ OKC"}
        rows.append({**base, "GAME_DATE": "Apr 30, 2026", "Game_ID": "0042500101"})
        rows.append({**base, "GAME_DATE": "Oct 10, 2025", "Game_ID": "0012500001"})
    df = pd.DataFrame(rows)
    return df.sample(frac=1.0, random_state=3).reset_index(drop=True) if shuffle else df


def test_fewer_than_min_games_returns_none():
    assert lean.compute_features(_log(n_regular=9), 1.0) is None     # 9 regular + 2 others
    assert lean.compute_features(pd.DataFrame(), 1.0) is None
    assert lean.compute_features(None, 1.0) is None
    assert lean.compute_features(_log(n_regular=10), 1.0)["n_games"] == 10


def test_feature_values_on_synthetic_log():
    f = lean.compute_features(_log(), 1.04)
    assert f["n_games"] == 12
    pts = f["PTS"]
    assert pts["season_avg"] == pytest.approx(13.0)
    assert pts["form_l5"] == pytest.approx((20 - 13) / 13)          # games 8-12
    assert pts["form_l10"] == pytest.approx((15 - 13) / 13)         # games 3-12
    min_season = (7 * 30 + 5 * 35) / 12
    assert pts["min_l5"] == pytest.approx((35 - min_season) / min_season)
    assert pts["min_l10"] == pytest.approx((32.5 - min_season) / min_season)
    assert pts["volume_l10"] == pytest.approx((7.5 - 6.5) / 6.5)
    assert pts["log_games"] == pytest.approx(math.log(12))
    assert pts["defense"] == pytest.approx(0.04)
    assert pts["log_level"] == pytest.approx(math.log1p(13.0))
    # zero average: relative trend uses the REL_FLOOR denominator, no division by zero
    assert f["BLK"]["season_avg"] == 0.0
    assert f["BLK"]["form_l5"] == 0.0 and f["BLK"]["log_level"] == 0.0
    assert "volume_l10" not in f["AST"]
    assert set(f["FG3A"]) >= set(lean.feature_names("FG3A"))


def test_row_order_does_not_matter():
    a = lean.compute_features(_log(shuffle=True), 1.0)
    b = lean.compute_features(_log(shuffle=False), 1.0)
    assert a == b


def test_defense_multiplier_forms():
    assert lean.compute_features(_log(), None)["PTS"]["defense"] == 0.0
    f = lean.compute_features(_log(), {"PTS": 0.95})
    assert f["PTS"]["defense"] == pytest.approx(-0.05)
    assert f["AST"]["defense"] == 0.0


def test_relative_trend_is_clipped():
    df = _log(n_regular=60, extra_rows=False)
    df["AST"] = 0
    df.loc[df["Game_ID"].str[-5:].astype(int) > 55, "AST"] = 10   # avg 0.83, last 5 avg 10
    assert lean.compute_features(df, 1.0)["AST"]["form_l5"] == lean.REL_CLIP


TOY = {"PTS": {"features": ["form_l5"], "mean": [0.0], "scale": [1.0], "coef": [2.0],
               "intercept": 0.0, "threshold": 0.1, "oos_accuracy": 0.63, "oos_coverage": 0.19, "n": 100}}


def test_lean_for_direction_and_threshold():
    up = lean.lean_for("PTS", {"PTS": {"form_l5": 1.0}}, models=TOY)
    assert up["direction"] == "above"
    assert up["probability"] == pytest.approx(1 / (1 + math.exp(-2)))
    assert up["historical_accuracy"] == 0.63 and up["coverage"] == 0.19
    down = lean.lean_for("PTS", {"PTS": {"form_l5": -1.0}}, models=TOY)
    assert down["direction"] == "below"
    assert down["probability"] == pytest.approx(up["probability"])  # P(called direction)
    # |p - 0.5| = 0.0498 < 0.1 -> no lean
    assert lean.lean_for("PTS", {"PTS": {"form_l5": 0.1}}, models=TOY) is None
    assert lean.lean_for("PTS", {"PTS": {"form_l5": 0.0}}, models=TOY) is None


def test_lean_for_missing_stat_or_features():
    assert lean.lean_for("BLK", {"BLK": {"form_l5": 3.0}}, models=TOY) is None
    assert lean.lean_for("PTS", None, models=TOY) is None
    assert lean.lean_for("PTS", {"AST": {"form_l5": 3.0}}, models=TOY) is None


def test_shipped_models_match_feature_code():
    for stat, m in lean.LEAN_MODELS.items():
        assert stat in lean.STATS
        assert m["features"] == lean.feature_names(stat)
        assert len(m["coef"]) == len(m["mean"]) == len(m["scale"]) == len(m["features"])
        assert 0 < m["threshold"] < 0.5
        assert m["oos_accuracy"] >= 0.60 and m["oos_coverage"] >= 0.05


def test_shipped_models_sign_of_recent_form():
    """More recent form (last 5 above the season average) must never
    push a shipped model toward BELOW."""
    f = lean.compute_features(_log(), 1.0)
    for stat, m in lean.LEAN_MODELS.items():
        lo = dict(f[stat], form_l5=-0.5)
        hi = dict(f[stat], form_l5=0.5)
        assert lean.probability_above(m, hi) > lean.probability_above(m, lo), stat


def test_strong_lean_lines_hidden_outside_current_season_or_thin_log():
    kind, lines = lean.strong_lean_lines(_log(), "2024-25", "2025-26", 1.0, models=TOY)
    assert kind == "not_yet" and len(lines) == 1 and "10 regular-season games" in lines[0]
    kind, _ = lean.strong_lean_lines(_log(n_regular=9), "2025-26", "2025-26", 1.0, models=TOY)
    assert kind == "not_yet"


def test_strong_lean_lines_render_and_none():
    # synthetic log: PTS form_l5 = 7/13 -> p = sigmoid(1.08) -> ABOVE
    kind, lines = lean.strong_lean_lines(_log(), "2025-26", "2025-26", 1.0, models=TOY)
    assert kind == "leans" and len(lines) == 1
    assert lines[0].startswith("Points: leans ABOVE his season average (13.0)")
    assert "right 63% of the time" in lines[0] and "about 1 in 5 games" in lines[0]
    for word in ["over", "under", "pick", "bet", "lock"]:
        assert not re.search(rf"\b{word}\b", lines[0], re.I)
    kind, lines = lean.strong_lean_lines(_log(), "2025-26", "2025-26", 1.0, models={})
    assert kind == "none" and lines == ["No strong lean for this game — most games don't have one."]


def test_models_file_missing_means_no_leans(tmp_path):
    assert lean._load_models(str(tmp_path / "nope.json")) == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert lean._load_models(str(bad)) == {}


# ---- graded leans / clearest read (engine/lean_tiers.json) ----------------
TOY2 = {
    "PTS": dict(TOY["PTS"]),
    "AST": {"features": ["form_l5"], "mean": [0.0], "scale": [1.0], "coef": [4.0],
            "intercept": 0.0, "threshold": 0.1, "oos_accuracy": 0.61, "oos_coverage": 0.1, "n": 50},
}
TIERS = {
    "PTS": {"shown_coverage": 0.12, "tiers": [
        {"label": "Borderline", "min_margin": 0.0, "accuracy": 0.57, "ci": [0.55, 0.59], "n": 10, "shown": False},
        {"label": "Solid", "min_margin": 0.02, "accuracy": 0.63, "ci": [0.6, 0.66], "n": 20, "shown": True},
        {"label": "Very strong", "min_margin": 0.10, "accuracy": 0.75, "ci": [0.7, 0.8], "n": 30, "shown": True},
    ]},
    "AST": {"shown_coverage": 0.05, "tiers": [
        {"label": "Borderline", "min_margin": 0.0, "accuracy": 0.58, "ci": [0.5, 0.6], "n": 5, "shown": False},
        {"label": "Strong", "min_margin": 0.05, "accuracy": 0.66, "ci": [0.6, 0.7], "n": 7, "shown": True},
    ]},
}


def _p_margin(coef, x, threshold=0.1):
    return abs(1 / (1 + math.exp(-coef * x)) - 0.5) - threshold


def test_tier_for_picks_highest_reached_tier():
    assert lean.tier_for("PTS", 0.0, TIERS)["label"] == "Borderline"
    assert lean.tier_for("PTS", 0.05, TIERS)["label"] == "Solid"
    assert lean.tier_for("PTS", 0.3, TIERS)["label"] == "Very strong"
    assert lean.tier_for("REB", 0.3, TIERS) is None
    assert lean.tier_for("PTS", 0.3, {}) is None


def test_lean_for_uses_tier_accuracy_and_hides_weak_tiers():
    # form_l5 = 0.3 -> |p-0.5| = 0.146, margin 0.046 -> Solid
    solid = lean.lean_for("PTS", {"PTS": {"form_l5": 0.3}}, models=TOY2, tiers=TIERS)
    assert solid["tier"] == "Solid" and solid["historical_accuracy"] == 0.63
    assert solid["historical_calls"] == 20 and solid["coverage"] == 0.12
    assert solid["margin"] == pytest.approx(_p_margin(2.0, 0.3))
    # form_l5 = 0.22 -> margin ~0.009 -> Borderline, hidden
    assert lean.lean_for("PTS", {"PTS": {"form_l5": 0.22}}, models=TOY2, tiers=TIERS) is None
    # untiered (explicit models, no tiers) keeps the original rule and numbers
    plain = lean.lean_for("PTS", {"PTS": {"form_l5": 0.22}}, models=TOY2)
    assert plain["tier"] is None and plain["historical_accuracy"] == 0.63 and plain["coverage"] == 0.19


def test_leans_for_game_strongest_first_and_clearest_read():
    feats = {"PTS": {"form_l5": 1.0, "season_avg": 20.0}, "AST": {"form_l5": 0.2, "season_avg": 5.0}}
    got = lean.leans_for_game(feats, models=TOY2, tiers=TIERS)
    assert [g["stat"] for g in got] == ["PTS", "AST"]
    assert got[0]["margin"] > got[1]["margin"]
    assert got[1]["tier"] == "Strong"
    assert lean.leans_for_game(None, models=TOY2, tiers=TIERS) == []

    read = lean.clearest_read(_log(), "2025-26", "2025-26", 1.0, models=TOY2, tiers=TIERS)
    assert read["lean"]["stat"] in {"PTS", "AST"}
    assert read["season_avg"] == pytest.approx(13.0 if read["lean"]["stat"] == "PTS" else 1.0)
    assert lean.clearest_read(_log(), "2024-25", "2025-26", 1.0, models=TOY2, tiers=TIERS) is None
    assert lean.clearest_read(_log(n_regular=9), "2025-26", "2025-26", 1.0, models=TOY2, tiers=TIERS) is None
    assert lean.clearest_read(_log(), "2025-26", "2025-26", 1.0, models={}, tiers=TIERS) is None


def test_strong_lean_lines_graded_wording():
    feats_log = _log()
    kind, lines = lean.strong_lean_lines(feats_log, "2025-26", "2025-26", 1.0, models=TOY, tiers=TIERS)
    assert kind == "leans" and len(lines) == 1
    # PTS form_l5 = 7/13 -> p = sigmoid(1.08), margin ~0.146 -> Very strong
    assert "Very strong lean — calls like this were right 75% of the time" in lines[0]
    assert "30 calls" in lines[0]
    for word in ["over", "under", "pick", "bet", "lock", "odds", "wager"]:
        assert not re.search(rf"\b{word}\b", lines[0], re.I)


def test_shipped_tiers_are_consistent():
    assert set(lean.LEAN_TIERS) == set(lean.LEAN_MODELS)
    for stat, entry in lean.LEAN_TIERS.items():
        tiers = entry["tiers"]
        edges = [t["min_margin"] for t in tiers]
        assert edges[0] == 0.0 and edges == sorted(edges) and len(set(edges)) == len(edges)
        for t in tiers:
            assert t["shown"] == (t["accuracy"] >= 0.60)
            assert t["n"] >= 300
            assert t["ci"][0] <= t["accuracy"] <= t["ci"][1]
        assert any(t["shown"] for t in tiers), stat
        assert 0 < entry["shown_coverage"] < 1
    s = lean.LEAN_TIER_SUMMARY
    assert s["shown_accuracy"] >= 0.60 and s["clearest_read_accuracy"] >= 0.60
    assert s["hidden_accuracy"] < s["shown_accuracy"]
    assert 0 < s["games_with_a_read_share"] < 1


def test_tiers_file_missing_or_bad(tmp_path):
    assert lean._load_tiers(str(tmp_path / "nope.json")) == ({}, {})
    bad = tmp_path / "bad.json"
    bad.write_text("[]")
    assert lean._load_tiers(str(bad)) == ({}, {})


def test_graded_leans_need_regular_minutes_and_a_real_average():
    feats = {"mpg": 30.0, "PTS": {"form_l5": 1.0, "season_avg": 12.0}}
    assert lean.lean_for("PTS", feats, models=TOY2, tiers=TIERS) is not None
    low_min = dict(feats, mpg=lean.MIN_MPG - 0.1)
    assert lean.lean_for("PTS", low_min, models=TOY2, tiers=TIERS) is None
    tiny = {"mpg": 30.0, "PTS": {"form_l5": 1.0, "season_avg": 0.4}}
    assert lean.lean_for("PTS", tiny, models=TOY2, tiers=TIERS) is None
    # untiered keeps the original behaviour
    assert lean.lean_for("PTS", tiny, models=TOY2) is not None
    # compute_features reports minutes per game
    assert lean.compute_features(_log(), 1.0)["mpg"] == pytest.approx((7 * 30 + 5 * 35) / 12)


def test_clearest_read_prefers_the_better_graded_lean():
    tiers = {
        "PTS": {"shown_coverage": 0.1, "tiers": [
            {"label": "Solid", "min_margin": 0.0, "accuracy": 0.62, "ci": [0.6, 0.64], "n": 400, "shown": True}]},
        "AST": {"shown_coverage": 0.1, "tiers": [
            {"label": "Strong", "min_margin": 0.0, "accuracy": 0.70, "ci": [0.65, 0.75], "n": 400, "shown": True}]},
    }
    # PTS has the far larger margin, AST the better grade -> AST first
    feats = {"mpg": 30.0, "PTS": {"form_l5": 3.0, "season_avg": 20.0}, "AST": {"form_l5": 0.2, "season_avg": 5.0}}
    got = lean.leans_for_game(feats, models=TOY2, tiers=tiers)
    assert [g["stat"] for g in got] == ["AST", "PTS"]
    assert got[1]["margin"] > got[0]["margin"]
