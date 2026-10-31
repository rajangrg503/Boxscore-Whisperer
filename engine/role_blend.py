#!/usr/bin/env python3
"""Blend two seasons by whether the player's ROLE carried over.

The projection engine used a hard switch: five games this season and
last season was dropped entirely; fewer than five and this season was
ignored instead. On the 31 Oct slip that produced, from the same six
legs on the same night:

    Camara   4 games -> last season ONLY, at 33 mpg, unscaled,
                        while he now plays 25        -> 4 or fewer reb, 45%
    Sabonis  5 games -> this season ONLY, five games,
                        minutes up 31%               -> 14+ reb, 92%

Neither number describes the player who will take the floor. A cliff at
five games is not a judgement about evidence, it is an accident of
counting.

What replaces it, the same arithmetic tools/rank_lines.py uses:

  * Last season is SCALED to this season's minutes before it is used at
    all. A player at 25 minutes is not last season's 33-minute player.
  * How much of it to trust is set by ROLE SIMILARITY -- how far the
    minutes AND the shots per minute have moved. Same minutes and same
    attempts is the same job; attempts up by half is not, however many
    games were played.
  * The weight similarity does NOT earn is given to "we don't know",
    not back to the small sample. Shrinking the prior alone hands the
    weight to this season's five games, which are least trustworthy
    exactly when the role changed -- the mistake made and corrected in
    rank_lines on 31 Oct.

The minutes scaling is linear and that is wrong in a known direction:
over 4,799 journal lines, production tracks minutes almost
proportionally downward (0.61 output per 0.55 minutes) but is damped
upward (1.23 per 1.64). So a player whose minutes have RISEN gets an
over-generous prior here; `minutes_up` flags those rows.
"""

PRIOR_W = 12.0          # last season is worth about twelve games, at most
UNKNOWN_CENTER = 0.5    # what "we don't know" says about a probability


def _ratio(a, b):
    """How close two positive numbers are, 1.0 being identical. Symmetric:
    doubling and halving are equally far apart."""
    if not a or not b:
        return 0.5
    return min(a / b, b / a)


def role_similarity(mpg_cur, mpg_prev, rate_cur, rate_prev):
    """0..1 -- same minutes and same shot volume per minute is 1.0."""
    return _ratio(mpg_cur, mpg_prev) * _ratio(rate_cur, rate_prev)


def weights(n_cur, similarity, prior_w=PRIOR_W):
    """(this season, last season, unknown) -- they sum to n_cur + prior_w."""
    return n_cur, prior_w * similarity, prior_w * (1.0 - similarity)


def blend_probability(n_cur, p_cur, p_prev, similarity, prior_w=PRIOR_W):
    """Two hit rates into one, with the unexplained share pulled to 0.5."""
    w_cur, w_prev, w_unknown = weights(n_cur, similarity, prior_w)
    total = w_cur + w_prev + w_unknown
    return (w_cur * p_cur + w_prev * p_prev + w_unknown * UNKNOWN_CENTER) / total


def blend_value(n_cur, v_cur, v_prev, similarity, prior_w=PRIOR_W):
    """Two projections into one. Unlike a probability there is no neutral
    value to fall back on, so the unknown share is split between the two
    estimates rather than invented -- the uncertainty it represents is
    carried by widen() instead."""
    w_cur, w_prev, w_unknown = weights(n_cur, similarity, prior_w)
    if v_cur is None:
        return v_prev
    if v_prev is None:
        return v_cur
    share = w_unknown / 2.0
    w_cur, w_prev = w_cur + share, w_prev + share
    return (w_cur * v_cur + w_prev * v_prev) / (w_cur + w_prev)


def widen(sd, similarity, apart=0.0):
    """Spread the projection to carry what the blend does not know.

    Two sources of doubt: a role that moved (1 - similarity), and two
    seasons that disagree about the answer (`apart`, the gap between
    them). Both widen the distribution rather than shifting it, because
    neither tells us which way the number is wrong.
    """
    sd = float(sd or 0.0)
    return (sd * (1.0 + 0.5 * (1.0 - similarity)) ** 0.5) + 0.35 * abs(apart)


def scale_to_minutes(value, mpg_cur, mpg_prev):
    """Last season's per-game number at this season's minutes."""
    if not value or not mpg_prev:
        return value
    return value * (mpg_cur / mpg_prev)
