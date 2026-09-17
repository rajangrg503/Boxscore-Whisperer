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
