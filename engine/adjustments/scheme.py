"""Defensive-scheme adjustment -- moved from app.py.

SCHEME_ADJUSTMENTS and SCHEME_TO_SYNERGY_PLAYTYPE moved alongside the
function since they're scheme-adjustment domain data, not generic
app-wide constants. app.py imports SCHEME_ADJUSTMENTS back from here
for its dropdown (the only other place it's used).
"""

from nba_api.stats.endpoints import synergyplaytypes, leaguehustlestatsteam

from engine.cache import cached_or_live
from engine.season import PREVIOUS_SEASON
from engine.adjustments.base import AdjustmentResult, ALL_STATS

LAYER = "scheme"

SCHEME_ADJUSTMENTS = {
    "Drop coverage": 1.03,             # big sags back in the paint on PnR -- favors pull-up scorers
    "Switch everything": 0.97,         # limits easy paint/rim looks, creates size mismatches
    "Aggressive double-team / blitz": 0.90,   # meaningfully suppresses usage on the ball-handler
    "Zone defense": 1.05,              # often favors driving/playmaking scorers, weaker vs. shooters
    "Man-to-man (standard)": 1.00,     # neutral baseline
    "Nail help": 0.96,                 # help from the foul-line area collapses driving lanes
    "Weak-side / tagging": 0.97,       # help from the far side discourages drives, allows more kick-outs
    "Bigs roaming (free safety)": 0.95,  # a big leaves his man to protect the rim, suppresses paint scoring
    "Ice / blue (PnR sideline)": 0.97,   # forces ball-handler away from a sideline screen
    "Hard hedge (no full blitz)": 0.95,  # big shows hard on PnR without fully trapping
    "Deny / face-guard": 0.88,         # denies the ball entirely to a specific shooter off screens
    "Full-court press": 0.93,          # pressures possessions, forces tempo/turnovers
    "Pack the paint": 1.02,            # packs the paint against non-shooters, can favor perimeter scorers
    "None / unsure": 1.00,
}

# Where a real Synergy play-type roughly overlaps with one of the
# manual scheme labels above, we use REAL team defensive data instead
# of the guessed multiplier. Not every scheme has a genuine Synergy
# equivalent (e.g. "Zone defense" and "Man-to-man" aren't tracked as
# distinct play types), so those fall back to the manual estimate.
SCHEME_TO_SYNERGY_PLAYTYPE = {
    "Drop coverage": "PRBallHandler",
    "Switch everything": "PRBallHandler",
    "Aggressive double-team / blitz": "PRBallHandler",
    "Ice / blue (PnR sideline)": "PRBallHandler",
    "Hard hedge (no full blitz)": "PRBallHandler",
    "Nail help": "Isolation",
    "Weak-side / tagging": "Isolation",
    "Bigs roaming (free safety)": "PRRollman",
    "Deny / face-guard": "OffScreen",
    "Full-court press": "Transition",
    "Pack the paint": "Postup",
}


def get_synergy_scheme_adjustment(team_id, scheme_label, season) -> AdjustmentResult:
    """For scheme labels with a real Synergy play-type equivalent,
    pull actual team defensive efficiency (points per possession) for
    that play type and use it instead of the guessed multiplier.
    Falls back to the manual estimate if no mapping exists or the
    data can't be fetched.

    Special case: 'Man-to-man (standard)' has no Synergy play-type
    equivalent, but real disruption data (deflections/game vs. league
    average) is available and genuinely reflects man-to-man defensive
    quality without double-counting the separate Opponent Defense
    Layer (which measures points-allowed efficiency, a different
    dimension from ball pressure/disruption).

    Note on data_quality vs applied: every path here returns
    applied=True (the value is always folded into the prediction) --
    the manual-estimate paths are just as "applied" as the real-data
    ones, they're distinguished by data_quality instead. See
    AdjustmentResult's docstring, rule 3."""
    play_type = SCHEME_TO_SYNERGY_PLAYTYPE.get(scheme_label)
    manual_value = SCHEME_ADJUSTMENTS[scheme_label]

    if play_type is None:
        if scheme_label == "Man-to-man (standard)":
            DEFLECTIONS_ADJUSTMENT_STRENGTH = 0.4  # kept modest -- this is a
                                                     # supplementary disruption
                                                     # signal, not a full defense
                                                     # rating replacement
            for try_season in [season, PREVIOUS_SEASON]:
                try:
                    def _fetch_hustle():
                        hustle = leaguehustlestatsteam.LeagueHustleStatsTeam(
                            season=try_season, per_mode_time="PerGame", timeout=10
                        )
                        return hustle.get_data_frames()[0]
                    hustle_df, _hustle_source = cached_or_live(
                        f"hustle_team_stats_{try_season}", _fetch_hustle
                    )
                except Exception:
                    continue
                if hustle_df.empty or "DEFLECTIONS" not in hustle_df.columns:
                    continue
                team_row = hustle_df[hustle_df["TEAM_ID"] == team_id]
                if team_row.empty:
                    continue
                team_deflections = team_row["DEFLECTIONS"].values[0]
                league_avg_deflections = hustle_df["DEFLECTIONS"].mean()
                gap_pct = (team_deflections - league_avg_deflections) / league_avg_deflections
                real_adjustment = 1 - (gap_pct * DEFLECTIONS_ADJUSTMENT_STRENGTH)
                if try_season == season:
                    source_note = ""
                else:
                    source_note = f" (from {try_season}, {season} not available yet)"
                note = (
                    f"'Man-to-man (standard)' has no real Synergy play-type "
                    f"equivalent, but real disruption data is available{source_note}: "
                    f"{team_deflections:.1f} deflections/game vs. league avg "
                    f"{league_avg_deflections:.1f} -> \U0001F9E9 Scheme Layer Applied "
                    f"— x{real_adjustment:.3f}"
                )
                return AdjustmentResult(
                    layer=LAYER, value={ALL_STATS: real_adjustment}, note=note,
                    data_quality=("real_current" if try_season == season else "real_fallback_season"),
                    sample_n=len(hustle_df), applied=True,
                )
            # hustle data unavailable in any season checked -- fall through
            # to the plain manual estimate below

        note = (
            f"'{scheme_label}' has no real Synergy play-type equivalent -- "
            f"using your manual estimate \U0001F9E9 Scheme Layer Applied — x{manual_value:.3f} (not data-backed)."
        )
        return AdjustmentResult(
            layer=LAYER, value={ALL_STATS: manual_value}, note=note,
            data_quality="manual_estimate", sample_n=0, applied=True,
        )

    def _fetch():
        data = synergyplaytypes.SynergyPlayTypes(
            league_id="00",
            per_mode_simple="PerGame",
            player_or_team_abbreviation="T",
            season_type_all_star="Regular Season",
            season=season,
            type_grouping_nullable="defensive",
            play_type_nullable=play_type,
            timeout=5,
        )
        return data.get_data_frames()[0]
    try:
        df, source = cached_or_live(f"synergy_{play_type}_{season}", _fetch)
        team_row = df[df["TEAM_ID"] == team_id]
        if team_row.empty or len(df) < 5:
            note = (
                f"Synergy '{play_type}' data unavailable for this team -- "
                f"falling back to manual estimate \U0001F9E9 Scheme Layer Applied — x{manual_value:.3f}."
            )
            return AdjustmentResult(
                layer=LAYER, value={ALL_STATS: manual_value}, note=note,
                data_quality="manual_estimate", sample_n=0, applied=True,
            )
        team_ppp = team_row["PPP"].values[0]
        league_avg_ppp = df["PPP"].mean()
        gap_pct = (team_ppp - league_avg_ppp) / league_avg_ppp
        real_adjustment = 1 + (gap_pct * 0.5)  # same damping as opponent DEF_RATING
        source_note = "" if source == "live" else f" (from a {source})"
        note = (
            f"REAL DATA{source_note}: {scheme_label} maps to Synergy '{play_type}' "
            f"defense -- team allows {team_ppp:.2f} PPP vs. league avg "
            f"{league_avg_ppp:.2f} PPP -> \U0001F9E9 Scheme Layer Applied — x{real_adjustment:.3f}."
        )
        return AdjustmentResult(
            layer=LAYER, value={ALL_STATS: real_adjustment}, note=note,
            data_quality="real_current", sample_n=len(df), applied=True,
        )
    except Exception as e:
        note = (
            f"Synergy data fetch failed ({e}) -- falling back to manual estimate "
            f"\U0001F9E9 Scheme Layer Applied — x{manual_value:.3f}."
        )
        return AdjustmentResult(
            layer=LAYER, value={ALL_STATS: manual_value}, note=note,
            data_quality="manual_estimate", sample_n=0, applied=True,
        )
