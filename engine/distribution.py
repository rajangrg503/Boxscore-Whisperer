"""The predictive distribution behind one projected stat: how wide the
range really is, and the chance the stat clears a line.

WHY THIS EXISTS
The projection is a mean. A mean answers "about how many", not "how
likely is 27.5 or more", and it can't honestly be turned into a range
by picking a multiple of the standard deviation and hoping. The app used
to show `prediction +/- 0.6 x the player's own game-to-game standard
deviation` as a "Likely range"; measured on the 29,910 point-in-time
backtest games that band held the real result only ~42% of the time
(PTS 42.4%, REB 42.6%, AST 42.3%, FG3A 45.8%), and engine/tracker.py
scores a saved prediction as a hit against that same band, so the
tracker's hit rate was capped near 40% no matter how good the
projection was.

This module replaces the guess with a fitted distribution per stat,
chosen and measured out of sample by calibration_sweep.py (leave one
season out, scored on log loss, Brier, and whether a 50%/80% interval
actually contains the result 50%/80% of the time). The fitted numbers
live in engine/stat_distribution.json; this module never fits anything.

TWO FAMILIES, per stat, whichever won on held-out log loss:
  * "empirical_z" -- the pooled distribution of z = (actual - mu) / s
    over the backtest, stored as 1,001 percentiles. Shape-free: it
    carries the real skew of a stat line instead of assuming symmetry.
  * "nb_pool" / "nb_shrunk" / "nb_std" -- negative binomial with mean
    mu. nb_pool sets the variance to mu + alpha*mu^2; nb_shrunk blends
    the player's own dispersion toward the pooled alpha by n/(n+K);
    nb_std sets it to a fitted multiple of the player's own spread.
    Better for the small counts (blocks, offensive rebounds) where a
    continuous distribution wastes probability on impossible values.

LIMITS
  * Calibration is measured against the SAME engine that produced mu.
    If the projection is biased for some group of players, so is this.
  * The fit population is build_backtest_population.py's: every player
    in the three cached seasons, from his 6th played game of a season
    on, decided as of each game date. The first games of a season are
    still outside it -- callers get no distribution below
    MIN_PRIOR_GAMES rather than a made-up one.
  * A probability here is "how often results like this landed above the
    line in three past seasons". It is not a bookmaker's price, and
    nothing in this repo has ever been tested against real betting
    lines.
"""

import json
import math
import os

MODELS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stat_distribution.json")


def _load(path=MODELS_PATH):
    try:
        with open(path) as f:
            payload = json.load(f)
        stats = dict(payload["stats"])
        return stats, dict(payload)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}, {}


STAT_DISTRIBUTIONS, DISTRIBUTION_META = _load()
MIN_PRIOR_GAMES = DISTRIBUTION_META.get("min_prior_games", 5)
SHRINK_K = DISTRIBUTION_META.get("shrink_k", 20.0)


# ---- negative binomial (no scipy; the app ships pandas and numpy only) ----
def _betacf(a, b, x, itmax=300, eps=1e-12):
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > 1e-30 else 1e-30)
    h = d
    for m in range(1, itmax + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 / ((1.0 + aa * d) if abs(1.0 + aa * d) > 1e-30 else 1e-30)
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 / ((1.0 + aa * d) if abs(1.0 + aa * d) > 1e-30 else 1e-30)
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def betainc(a, b, x):
    """Regularised incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_front = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                 + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(log_front) * _betacf(a, b, x) / a
    return 1.0 - math.exp(log_front) * _betacf(b, a, 1.0 - x) / b


def _nb_sf(k, mu, r):
    """P(X > k) for a negative binomial with this mean and shape."""
    if k < 0:
        return 1.0
    p = r / (r + mu)
    return min(max(betainc(math.floor(k) + 1.0, r, 1.0 - p), 0.0), 1.0)


def _nb_r(mu, variance):
    variance = max(variance, mu * 1.000001 + 1e-9)
    return mu * mu / (variance - mu)


# ---- the distribution object ---------------------------------------------
class StatDistribution:
    """One stat's predictive distribution for one game.

    sf(line)          P(actual > line)
    interval(p)       the middle p of the distribution, as (low, high)
    """

    def __init__(self, stat, mu, sd, n_prior, entry):
        self.stat = stat
        self.mu = max(float(mu), 0.0)
        self.sd = float(sd)
        self.n_prior = float(n_prior)
        self.entry = entry
        self.model = entry["model"]
        if self.model == "empirical_z":
            self.z = entry["z"]
        elif self.model == "nb_std":
            # variance is a fitted multiple of the player's own spread
            variance = float(entry["c"]) * max(self.sd, 1e-6) ** 2
            self.r = _nb_r(max(self.mu, 1e-6), variance)
        else:
            alpha = float(entry["alpha"])
            if self.model == "nb_shrunk":
                own = max((self.sd ** 2 - self.mu) / max(self.mu ** 2, 1e-9), 0.0)
                w = self.n_prior / (self.n_prior + SHRINK_K)
                alpha = max(w * own + (1 - w) * alpha, 1e-6)
            self.r = _nb_r(max(self.mu, 1e-6), self.mu + alpha * self.mu ** 2)

    # -- probability ------------------------------------------------------
    def sf(self, line):
        """P(the player finishes strictly above `line`). Lines are half
        points in practice, so ties don't arise; a whole-number line is
        treated as "more than that number", not "that number or more"."""
        line = float(line)
        if line < 0:
            return 1.0          # stats are non-negative; no line sits below zero
        if self.model == "empirical_z":
            z = (line - self.mu) / max(self.sd, 1e-6)
            return 1.0 - _empirical_cdf(self.z, z)
        return _nb_sf(line, max(self.mu, 1e-6), self.r)

    def chance_over(self, line):
        """sf() as a percentage, rounded the way the app shows it."""
        return 100.0 * self.sf(line)

    # -- interval ---------------------------------------------------------
    def quantile(self, q):
        q = min(max(float(q), 1e-6), 1 - 1e-6)
        if self.model == "empirical_z":
            return max(self.mu + _empirical_quantile(self.z, q) * max(self.sd, 1e-6), 0.0)
        return max(_nb_quantile(q, max(self.mu, 1e-6), self.r), 0.0)

    def interval(self, nominal=0.8):
        lo_q = (1.0 - nominal) / 2.0
        return self.quantile(lo_q), self.quantile(1.0 - lo_q)

    # -- what the backtest said about this stat's calibration -------------
    def measured_coverage(self, nominal=0.8):
        held = self.entry.get("held_out", {})
        return held.get(f"coverage_{int(round(nominal * 100))}")


def _empirical_cdf(z_percentiles, z):
    """Share of the fitted z distribution at or below z, interpolating
    between stored percentiles."""
    n = len(z_percentiles)
    if z <= z_percentiles[0]:
        return 0.0
    if z >= z_percentiles[-1]:
        return 1.0
    lo, hi = 0, n - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if z_percentiles[mid] <= z:
            lo = mid
        else:
            hi = mid
    span = z_percentiles[hi] - z_percentiles[lo]
    frac = 0.0 if span <= 0 else (z - z_percentiles[lo]) / span
    return (lo + frac) / (n - 1)


def _empirical_quantile(z_percentiles, q):
    n = len(z_percentiles)
    pos = q * (n - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, n - 1)
    return z_percentiles[lo] + (pos - lo) * (z_percentiles[hi] - z_percentiles[lo])


def _nb_quantile(q, mu, r, hi=300):
    """Continuity-corrected quantile: the discrete CDF is read at k+0.5
    and interpolated, so a "50% range" on a stat that only takes the
    values 0, 1, 2 doesn't silently become an 80% range."""
    prev = 0.0
    for k in range(hi):
        cdf = 1.0 - _nb_sf(k, mu, r)
        if cdf >= q:
            span = max(cdf - prev, 1e-12)
            return max(k - 0.5 + (q - prev) / span, 0.0)
        prev = cdf
    return float(hi)


def distribution_for(stat, predicted, spread, n_prior, distributions=None):
    """The fitted distribution for one projected stat, or None when the
    stat has no fitted model, the spread is missing, or the player has
    fewer than MIN_PRIOR_GAMES games behind the projection (the fit
    population starts there, and guessing outside it is what this module
    exists to stop)."""
    entry = (STAT_DISTRIBUTIONS if distributions is None else distributions).get(stat)
    if entry is None or predicted is None or spread is None:
        return None
    try:
        spread = float(spread)
        predicted = float(predicted)
    except (TypeError, ValueError):
        return None
    if not (spread > 0) or predicted != predicted or n_prior is None or n_prior < MIN_PRIOR_GAMES:
        return None
    return StatDistribution(stat, predicted, spread, n_prior, entry)
