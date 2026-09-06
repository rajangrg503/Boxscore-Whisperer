"""Primary-defender matchup adjustment -- moved from app.py.

By design this layer NEVER changes the predicted number (value is
always {ALL_STATS: 1.0}, applied is always False) -- it surfaces real
player-vs-player tracking data as context only, since matchup sample
sizes are usually too small to trust as a hard multiplier. That's the
same behavior as before the move, not a new decision made here.

See AdjustmentResult's docstring (base.py, rule 3) -- this is the
concrete example of a layer where applied=False does NOT mean "no
data found". Check data_quality/sample_n below to tell "no defender
specified" apart from "real matchup data found but not applied".

_safe_float moved alongside it since it's used only by this function.
Dropped the @st.cache_data decorator it had in app.py -- caching a
tiny, pure string/float coercion helper via Streamlit's decorator was
pure overhead (hashing the input to build a cache key costs more than
just doing the conversion), not a deliberate design choice worth
preserving.
"""

import pandas as pd

from nba_api.stats.endpoints import leagueseasonmatchups

from engine.cache import cached_or_live
from engine.players import get_player_id
from engine.season import PREVIOUS_SEASON
from engine.adjustments.base import AdjustmentResult, ALL_STATS

LAYER = "defender_matchup"

# applied is ALWAYS False for this layer, by design (see module docstring
# and AdjustmentResult rule 3) -- never inferred from log data, which
# could never reliably tell "structurally never applies" apart from
# "just hasn't had enough resolved predictions yet." Consumed directly
# by analytics/layer_accuracy.py.
CONTEXT_ONLY_BY_DESIGN = True


def _safe_float(value):
    """The matchups endpoint sometimes returns numeric fields as
    strings (occasionally MM:SS format for minutes). Coerce to a
    float where possible, otherwise return None so callers can skip
    that detail cleanly instead of crashing."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        value = value.strip()
        if ":" in value:  # "MM:SS" format
            try:
                mins, secs = value.split(":")
                return float(mins) + float(secs) / 60
            except ValueError:
                return None
        try:
            return float(value)
        except ValueError:
            return None
    return None


def get_defender_matchup_adjustment(player_id, player_full_name, defender_name, season) -> AdjustmentResult:
    neutral_value = {ALL_STATS: 1.0}

    if not defender_name:
        return AdjustmentResult(
            layer=LAYER, value=neutral_value,
            note="No primary defender specified -- no adjustment.",
            data_quality="unavailable", sample_n=0, applied=False,
        )

    defender_id, _defender_full_name, ambiguity_note = get_player_id(defender_name)
    if defender_id is None:
        return AdjustmentResult(
            layer=LAYER, value=neutral_value,
            note=f"No player found named '{defender_name}' -- check spelling, skipping.",
            data_quality="unavailable", sample_n=0, applied=False,
        )

    for try_season in [season, PREVIOUS_SEASON]:
        def _fetch():
            data = leagueseasonmatchups.LeagueSeasonMatchups(
                off_player_id_nullable=player_id,
                def_player_id_nullable=defender_id,
                season=try_season,
                timeout=5,
            )
            return data.get_data_frames()[0]

        try:
            df, _source = cached_or_live(
                f"matchup_{player_id}_{defender_id}_{try_season}", _fetch
            )
        except Exception:
            continue

        if df.empty:
            continue

        row = df.iloc[0]
        details = []
        matchup_min = _safe_float(row.get("MATCHUP_MIN"))
        partial_poss = _safe_float(row.get("PARTIAL_POSS"))
        player_pts = _safe_float(row.get("PLAYER_PTS"))
        fg_pct = _safe_float(row.get("MATCHUP_FG_PCT"))

        if matchup_min is not None:
            details.append(f"{matchup_min:.1f} matchup minutes")
        if partial_poss is not None:
            details.append(f"{partial_poss:.1f} possessions")
        if player_pts is not None:
            details.append(f"{player_pts:.0f} points scored in those minutes")
        if fg_pct is not None:
            details.append(f"{fg_pct*100:.0f}% FG in the matchup")

        if not details:
            continue

        sample_flag = ""
        if matchup_min is not None and matchup_min < 10:
            sample_flag = " -- small sample, treat as context not a hard signal."

        ambiguity_prefix = f"{ambiguity_note} " if ambiguity_note else ""
        note = (
            f"{ambiguity_prefix}"
            f"REAL matchup data ({try_season}): {defender_name} has guarded "
            f"{player_full_name} for {', '.join(details)}.{sample_flag} Shown as "
            f"context -- not folded into the number above since sample sizes here "
            f"are usually too small to trust as a hard multiplier."
        )
        # sample_n uses matchup MINUTES, not a game count, since that's the
        # real underlying sample-size metric this layer actually has --
        # honest to what backs it rather than forcing a games-count that
        # doesn't exist for this data source.
        sample_n = int(round(matchup_min)) if matchup_min is not None else 0
        data_quality = (
            "real_thin_sample" if sample_flag
            else ("real_current" if try_season == season else "real_fallback_season")
        )
        return AdjustmentResult(
            layer=LAYER, value=neutral_value, note=note,
            data_quality=data_quality, sample_n=sample_n, applied=False,
        )

    return AdjustmentResult(
        layer=LAYER, value=neutral_value,
        note=(
            f"No recorded head-to-head matchup minutes found between this player and "
            f"{defender_name} in {season} or {PREVIOUS_SEASON} -- skipping."
        ),
        data_quality="unavailable", sample_n=0, applied=False,
    )
