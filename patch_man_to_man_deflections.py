"""
Upgrade: 'Man-to-man (standard)' previously always returned the flat
manual SCHEME_ADJUSTMENTS value (1.00) since it has no Synergy
play-type equivalent. This left it unable to reflect real differences
in defensive disruption -- e.g. a team with a genuine ball-denying,
deflection-generating defender (the "Cason Wallace effect") looked
identical to a passive man-to-man team.

Fix: specifically for 'Man-to-man (standard)', pull real team
deflections-per-game (LeagueHustleStatsTeam) vs. league average, and
use that gap as a modest, clearly-labeled adjustment instead of the
flat manual number. 'Zone defense' and 'None / unsure' are left
untouched -- this is scoped tightly to the case that was actually
flagged, not a general overhaul of every scheme label.

Falls back to last season's hustle stats if the current season has no
games yet (preseason gap), and falls back further to the plain manual
estimate if hustle data is unavailable at all.

Uses line-number-based replacement (find the function's def line and
the next top-level def/@st.), same safe approach used earlier tonight.

Usage:
    python3 patch_man_to_man_deflections.py
Run from the same folder as app.py.
"""

import pathlib
import re

APP_PATH = pathlib.Path("app.py")

MARKER = "deflections/game vs. league avg"

NEW_FUNCTION = '''def get_synergy_scheme_adjustment(team_id, scheme_label, season):
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
    dimension from ball pressure/disruption)."""
    play_type = SCHEME_TO_SYNERGY_PLAYTYPE.get(scheme_label)
    manual_value = SCHEME_ADJUSTMENTS[scheme_label]

    if play_type is None:
        if scheme_label == "Man-to-man (standard)":
            from nba_api.stats.endpoints import leaguehustlestatsteam

            DEFLECTIONS_ADJUSTMENT_STRENGTH = 0.4  # kept modest -- this is a
                                                     # supplementary disruption
                                                     # signal, not a full defense
                                                     # rating replacement
            for try_season in [season, PREVIOUS_SEASON]:
                try:
                    hustle = leaguehustlestatsteam.LeagueHustleStatsTeam(
                        season=try_season, per_mode_time="PerGame", timeout=10
                    )
                    hustle_df = hustle.get_data_frames()[0]
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
                return real_adjustment, (
                    f"'Man-to-man (standard)' has no real Synergy play-type "
                    f"equivalent, but real disruption data is available{source_note}: "
                    f"{team_deflections:.1f} deflections/game vs. league avg "
                    f"{league_avg_deflections:.1f} -> \\U0001F9E9 Scheme Layer Applied "
                    f"\\u2014 x{real_adjustment:.3f}"
                )
            # hustle data unavailable in any season checked -- fall through
            # to the plain manual estimate below

        return manual_value, (
            f"'{scheme_label}' has no real Synergy play-type equivalent -- "
            f"using your manual estimate \\U0001F9E9 Scheme Layer Applied \\u2014 x{manual_value:.3f} (not data-backed)."
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
            return manual_value, (
                f"Synergy '{play_type}' data unavailable for this team -- "
                f"falling back to manual estimate \\U0001F9E9 Scheme Layer Applied \\u2014 x{manual_value:.3f}."
            )
        team_ppp = team_row["PPP"].values[0]
        league_avg_ppp = df["PPP"].mean()
        gap_pct = (team_ppp - league_avg_ppp) / league_avg_ppp
        real_adjustment = 1 + (gap_pct * 0.5)  # same damping as opponent DEF_RATING
        source_note = "" if source == "live" else f" (from a {source})"
        return real_adjustment, (
            f"REAL DATA{source_note}: {scheme_label} maps to Synergy '{play_type}' "
            f"defense -- team allows {team_ppp:.2f} PPP vs. league avg "
            f"{league_avg_ppp:.2f} PPP -> \\U0001F9E9 Scheme Layer Applied \\u2014 x{real_adjustment:.3f}."
        )
    except Exception as e:
        return manual_value, (
            f"Synergy data fetch failed ({{e}}) -- falling back to manual estimate "
            f"\\U0001F9E9 Scheme Layer Applied \\u2014 x{manual_value:.3f}."
        )
'''.replace("{{e}}", "{e}")


def main():
    if not APP_PATH.exists():
        print("ERROR: app.py not found in current directory.")
        return

    text = APP_PATH.read_text()

    if MARKER in text:
        print("Already patched -- 'deflections/game vs. league avg' found. No changes made.")
        return

    lines = text.split("\n")

    start_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith("def get_synergy_scheme_adjustment("):
            start_idx = i
            break

    if start_idx is None:
        print("ERROR: could not find 'def get_synergy_scheme_adjustment(' in app.py.")
        print("Paste: grep -n 'def get_synergy_scheme_adjustment' app.py")
        return

    end_idx = None
    for i in range(start_idx + 1, len(lines)):
        stripped = lines[i]
        if re.match(r'^(def |@st\.)', stripped):
            end_idx = i
            break

    if end_idx is None:
        print("ERROR: could not find the end of get_synergy_scheme_adjustment "
              "(no following top-level def/@st. line found). Stopping without changes.")
        return

    old_block = "\n".join(lines[start_idx:end_idx])
    print(f"Found function spanning lines {start_idx + 1} to {end_idx} (exclusive of end).")
    print(f"Old block is {len(old_block)} characters -- replacing with upgraded version.")

    new_lines = lines[:start_idx] + NEW_FUNCTION.rstrip("\n").split("\n") + [""] + lines[end_idx:]
    new_text = "\n".join(new_lines)

    APP_PATH.write_text(new_text)
    print()
    print("Patched successfully. 'Man-to-man (standard)' now uses real team")
    print("deflections-per-game vs. league average instead of a flat 1.00,")
    print("with fallback to last season's data and to the manual estimate")
    print("if hustle data is unavailable. All other schemes are unchanged.")
    print("Restart Streamlit to see it.")


if __name__ == "__main__":
    main()
