"""Missing-opponent-players adjustment -- moved from app.py.

Weights each missing opponent's absence by their real minutes-per-game
(via engine.career_stats.resolve_season_mpg, which already fixes the
0-GP/NaN and mid-season-trade/TOT-row bugs) and, when available, their
real estimated net rating -- a poor player missing matters less than
an elite one, not just by raw minutes.
"""

import time

from nba_api.stats.endpoints import playercareerstats, playerestimatedmetrics

from engine.cache import cached_or_live
from engine.career_stats import resolve_season_mpg
from engine.players import get_player_id
from engine.season import PREVIOUS_SEASON
from engine.adjustments.base import AdjustmentResult, ALL_STATS

LAYER = "missing_opponents"


def get_opponent_missing_adjustment(missing_opponents, season) -> AdjustmentResult:
    if not missing_opponents:
        return AdjustmentResult(
            layer=LAYER, value={ALL_STATS: 1.0},
            note="No missing opponent players specified -- no adjustment.",
            data_quality="unavailable", sample_n=0, applied=False,
        )

    # Pull league-wide estimated net ratings once (not per player) so a
    # missing player's real two-way impact -- not just their minutes --
    # informs how much their absence should matter. Falls back to last
    # season if the current one has no games yet (e.g. preseason).
    net_rating_by_id = {}
    metrics_season_used = None
    for try_season in [season, PREVIOUS_SEASON]:
        def _fetch_metrics():
            metrics = playerestimatedmetrics.PlayerEstimatedMetrics(
                season=try_season, timeout=10
            )
            return metrics.get_data_frames()[0]

        try:
            metrics_df, _source = cached_or_live(
                f"player_estimated_metrics_{try_season}", _fetch_metrics
            )
        except Exception:
            continue
        if not metrics_df.empty:
            net_rating_by_id = dict(zip(metrics_df["PLAYER_ID"], metrics_df["E_NET_RATING"]))
            metrics_season_used = try_season
            break

    QUALITY_SCALE = 10.0  # a player at +10 E_NET_RATING roughly doubles
                           # their raw-MPG weight; -10 roughly zeroes it out.
    MIN_QUALITY_MULTIPLIER = 0.2  # floor, so a very poor E_NET_RATING never
                                   # flips a player's contribution negative

    total_weighted_mpg = 0.0
    found_players = []
    skipped_zero_gp = []
    for name in missing_opponents:
        try:
            pid, resolved_name, ambiguity_note = get_player_id(name)
            if pid is None:
                continue

            def _fetch_career():
                career = playercareerstats.PlayerCareerStats(player_id=pid, timeout=5)
                return career.get_data_frames()[0]

            df, _source = cached_or_live(f"career_stats_{pid}", _fetch_career)
            mpg, skip_reason = resolve_season_mpg(df, season)
            if mpg is None:
                skipped_zero_gp.append((name, skip_reason))
                continue

            net_rating = net_rating_by_id.get(pid)
            if net_rating is not None:
                quality_multiplier = max(MIN_QUALITY_MULTIPLIER, 1 + (net_rating / QUALITY_SCALE))
                weighted_mpg = mpg * quality_multiplier
                found_players.append((name, round(mpg, 1), round(net_rating, 1), ambiguity_note))
            else:
                weighted_mpg = mpg  # no estimated-metrics data found for this
                                     # player -- fall back to raw MPG rather
                                     # than dropping them entirely
                found_players.append((name, round(mpg, 1), None, ambiguity_note))

            total_weighted_mpg += weighted_mpg
            time.sleep(0.5)
        except Exception:
            continue

    # sample_n is len(found_players) -- the single source of truth for
    # "how many real players actually factored into this adjustment".
    # Nothing below computes a second, separate count for display.
    sample_n = len(found_players)

    if not found_players:
        if skipped_zero_gp:
            skipped_detail = ", ".join(f"{n} ({reason})" for n, reason in skipped_zero_gp)
            note = (f"Found {skipped_detail}, but excluded -- no real minutes to weight "
                    f"this adjustment by, skipping.")
        else:
            note = f"Could not find stats for {missing_opponents} -- skipping adjustment."
        return AdjustmentResult(
            layer=LAYER, value={ALL_STATS: 1.0}, note=note,
            data_quality="unavailable", sample_n=sample_n, applied=False,
        )

    minutes_fraction = total_weighted_mpg / 240
    adjustment = 1 + (minutes_fraction * 0.35)
    detail_parts = []
    ambiguity_notes = []
    for n, m, nr, amb in found_players:
        if nr is not None:
            detail_parts.append(f"{n} ({m} MPG, {nr:+.1f} net rtg)")
        else:
            detail_parts.append(f"{n} ({m} MPG, net rtg unavailable)")
        if amb:
            ambiguity_notes.append(amb)
    detail = ", ".join(detail_parts)
    if metrics_season_used:
        metrics_note = f" [quality-weighted using {metrics_season_used} estimated net ratings]"
    else:
        metrics_note = " [net rating data unavailable -- weighted by MPG only]"
    skip_note = (
        f" (excluded: {', '.join(f'{n} ({reason})' for n, reason in skipped_zero_gp)})"
        if skipped_zero_gp else ""
    )
    ambiguity_prefix = (" ".join(ambiguity_notes) + " ") if ambiguity_notes else ""
    note = (f"{ambiguity_prefix}Missing: {detail}{metrics_note}{skip_note} -> "
            f"\U0001F691 Opponent Missing Players Layer Applied — x{adjustment:.3f}")

    if metrics_season_used == season:
        data_quality = "real_current"
    elif metrics_season_used is not None:
        data_quality = "real_fallback_season"
    else:
        # Real MPG data was used either way -- this reflects the absence
        # of the *quality* (net rating) signal specifically, not a thin
        # sample. Closest existing tier: treated as real_thin_sample
        # rather than manual_estimate, since it's still measured minutes,
        # just without the two-way-impact weighting. Revisit in Phase 5
        # if this classification needs its own tier.
        data_quality = "real_thin_sample"

    return AdjustmentResult(
        layer=LAYER, value={ALL_STATS: adjustment}, note=note,
        data_quality=data_quality, sample_n=sample_n, applied=True,
    )
