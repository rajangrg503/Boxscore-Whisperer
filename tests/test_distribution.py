"""Tests for engine/distribution.py -- the predictive distribution that
backs the calibrated range and the "chance he clears this line" number,
plus the consistency of engine/stat_distribution.json with the code that
reads it."""

import json
import math

import pytest

from engine import distribution as dist
from engine.stat_columns import STAT_COLUMNS

NB_ENTRY = {"model": "nb_pool", "alpha": 0.25,
            "held_out": {"coverage_50": 0.5, "coverage_80": 0.8, "brier": 0.2}}
EMP_ENTRY = {
    "model": "empirical_z",
    # symmetric, roughly normal z grid: 1,001 percentiles from -3 to 3
    "z": [round(-3.0 + 6.0 * i / 1000.0, 4) for i in range(1001)],
    "held_out": {"coverage_50": 0.5, "coverage_80": 0.8, "brier": 0.2},
}
TOY = {"PTS": EMP_ENTRY, "BLK": NB_ENTRY}


def _d(stat="PTS", mu=20.0, sd=8.0, n=30):
    return dist.distribution_for(stat, mu, sd, n, distributions=TOY)


# ---- the negative binomial, against known values -------------------------
def test_nb_survival_matches_reference_values():
    # mean 12, r 4  ->  P(X > k) for k = 0, 5, 12, 30 (scipy.stats.nbinom.sf)
    r, mu = 4.0, 12.0
    for k, expected in [(0, 0.99609375), (5, 0.83427429), (12, 0.40498711), (30, 0.01674207)]:
        assert dist._nb_sf(k, mu, r) == pytest.approx(expected, abs=1e-7)
    assert dist._nb_sf(-1, mu, r) == 1.0


def test_betainc_edges_and_symmetry():
    assert dist.betainc(2.0, 3.0, 0.0) == 0.0
    assert dist.betainc(2.0, 3.0, 1.0) == 1.0
    # I_x(a,b) = 1 - I_(1-x)(b,a)
    assert dist.betainc(2.5, 4.5, 0.3) == pytest.approx(1 - dist.betainc(4.5, 2.5, 0.7), abs=1e-9)


def test_nb_variance_shape():
    """alpha controls over-dispersion: bigger alpha -> fatter tail."""
    tight = dist.StatDistribution("BLK", 1.0, 1.0, 40, {**NB_ENTRY, "alpha": 0.05})
    loose = dist.StatDistribution("BLK", 1.0, 1.0, 40, {**NB_ENTRY, "alpha": 2.0})
    assert loose.sf(3.5) > tight.sf(3.5)
    assert loose.quantile(0.95) > tight.quantile(0.95)


# ---- shared behaviour ----------------------------------------------------
@pytest.mark.parametrize("stat,mu,sd", [("PTS", 20.0, 8.0), ("BLK", 1.2, 1.0)])
def test_survival_is_monotone_and_bounded(stat, mu, sd):
    d = _d(stat, mu, sd)
    prev = 1.1
    for line in [0.5, 1.5, 3.5, 7.5, 15.5, 30.5, 60.5]:
        p = d.sf(line)
        assert 0.0 <= p <= 1.0
        assert p <= prev
        prev = p
    assert d.sf(-1) == pytest.approx(1.0, abs=1e-6)


@pytest.mark.parametrize("stat", ["PTS", "BLK"])
def test_interval_nests_and_matches_the_survival_curve(stat):
    d = _d(stat, 18.0 if stat == "PTS" else 1.5, 7.0 if stat == "PTS" else 1.2)
    lo50, hi50 = d.interval(0.5)
    lo80, hi80 = d.interval(0.8)
    assert lo80 <= lo50 <= hi50 <= hi80
    # the 80% interval's upper end should sit at the 90th percentile
    assert d.sf(hi80) == pytest.approx(0.10, abs=0.06)
    assert d.quantile(0.9) == pytest.approx(hi80, abs=1e-6)


def test_never_negative():
    d = _d("PTS", mu=1.0, sd=6.0)
    lo, _hi = d.interval(0.8)
    assert lo == 0.0
    assert d.quantile(0.01) >= 0.0


def test_empirical_uses_the_player_spread():
    narrow = _d("PTS", mu=20.0, sd=3.0)
    wide = _d("PTS", mu=20.0, sd=10.0)
    assert (wide.interval(0.8)[1] - wide.interval(0.8)[0]) > (
        narrow.interval(0.8)[1] - narrow.interval(0.8)[0])
    assert wide.sf(30.5) > narrow.sf(30.5)


def test_empirical_quantile_interpolates_between_percentiles():
    z = EMP_ENTRY["z"]
    assert dist._empirical_quantile(z, 0.5) == pytest.approx(0.0, abs=1e-6)
    assert dist._empirical_quantile(z, 0.0) == z[0]
    assert dist._empirical_quantile(z, 1.0) == z[-1]
    assert dist._empirical_cdf(z, -3.5) == 0.0
    assert dist._empirical_cdf(z, 3.5) == 1.0
    assert dist._empirical_cdf(z, 0.0) == pytest.approx(0.5, abs=1e-3)


def test_shrunk_model_blends_toward_the_players_own_spread():
    entry = {**NB_ENTRY, "model": "nb_shrunk"}
    few = dist.StatDistribution("BLK", 1.0, 3.0, 1, entry)     # barely any games: pooled
    many = dist.StatDistribution("BLK", 1.0, 3.0, 400, entry)  # his own, much wider
    assert many.sf(4.5) > few.sf(4.5)


def test_chance_over_and_measured_coverage_pass_through():
    d = _d()
    assert d.chance_over(20.5) == pytest.approx(100 * d.sf(20.5))
    assert d.measured_coverage(0.8) == 0.8
    assert d.measured_coverage(0.5) == 0.5


# ---- the guard rails -----------------------------------------------------
def test_no_distribution_outside_the_fitted_population():
    assert dist.distribution_for("PTS", 20, 8, dist.MIN_PRIOR_GAMES - 1, distributions=TOY) is None
    assert dist.distribution_for("PTS", 20, 0, 30, distributions=TOY) is None
    assert dist.distribution_for("PTS", 20, None, 30, distributions=TOY) is None
    assert dist.distribution_for("PTS", None, 8, 30, distributions=TOY) is None
    assert dist.distribution_for("PTS", 20, float("nan"), 30, distributions=TOY) is None
    assert dist.distribution_for("PTS", float("nan"), 8, 30, distributions=TOY) is None
    assert dist.distribution_for("AST", 5, 2, 30, distributions=TOY) is None   # no model
    assert dist.distribution_for("PTS", 20, 8, None, distributions=TOY) is None


def test_missing_or_bad_model_file(tmp_path):
    assert dist._load(str(tmp_path / "nope.json")) == ({}, {})
    bad = tmp_path / "bad.json"
    bad.write_text("[]")
    assert dist._load(str(bad)) == ({}, {})
    bad.write_text('{"no_stats": 1}')
    assert dist._load(str(bad)) == ({}, {})


# ---- the shipped file ----------------------------------------------------
def test_shipped_distributions_cover_every_stat_and_are_sane():
    shipped = dist.STAT_DISTRIBUTIONS
    assert set(shipped) == {col for col, _ in STAT_COLUMNS}
    for col, entry in shipped.items():
        assert entry["model"] in {"empirical_z", "nb_pool", "nb_shrunk", "nb_std", "normal_k"}
        if entry["model"] == "empirical_z":
            z = entry["z"]
            assert len(z) == 1001
            assert z == sorted(z)
            assert z[0] < 0 < z[-1]
        else:
            assert entry["alpha"] > 0
        held = entry["held_out"]
        # measured on held-out seasons: the range must hold the result at
        # least as often as it claims, and not absurdly more
        assert 0.47 <= held["coverage_50"] <= 0.72, col
        assert 0.78 <= held["coverage_80"] <= 0.93, col
        assert held["brier"] < 0.25, col            # better than saying 50% every time
        old = held["baseline_shipped_0.6"]
        assert held["coverage_80"] > old["coverage_80"] + 0.2, col
        assert held["log_loss"] < old["log_loss"], col
        assert held["ece"] <= old["ece"], col


def test_shipped_file_is_usable_end_to_end():
    for col, _label in STAT_COLUMNS:
        d = dist.distribution_for(col, 6.0, 3.0, 25)
        assert d is not None
        lo, hi = d.interval(0.8)
        assert 0 <= lo <= 6.0 <= hi
        assert 0.0 <= d.sf(5.5) <= 1.0
        assert d.sf(5.5) >= d.sf(6.5)


def test_meta_matches_module_constants():
    with open(dist.MODELS_PATH) as f:
        payload = json.load(f)
    assert payload["min_prior_games"] == dist.MIN_PRIOR_GAMES
    assert payload["shrink_k"] == dist.SHRINK_K
    assert "leave-one-season-out" in payload["rule"]
    assert not math.isnan(dist.SHRINK_K)
