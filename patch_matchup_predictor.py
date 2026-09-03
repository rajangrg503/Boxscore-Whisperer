"""
Patch: adds the "Predict a full matchup" feature to app.py.

Appends new code to the END of the file only — no text-matching against
existing lines, so this can't collide with anything already patched.
Idempotent: safe to run twice, will skip if already applied.

Usage:
    python3 patch_matchup_predictor.py
Run from the same folder as app.py (your Documents folder).
"""

import pathlib

APP_PATH = pathlib.Path("app.py")

MARKER = "# ============================================================\n# Predict a full matchup\n"

NEW_CODE = '''

# ============================================================
# Predict a full matchup
# ============================================================
st.divider()
st.subheader("Predict a full matchup")
st.caption(
    "Projects a full box score for both teams using each player's live "
    "current roster spot (so departed players drop off and new arrivals "
    "show up automatically) and the same season-baseline + "
    "opponent-defense engine as the single-player tool above. This does "
    "not model rotations or minutes -- every player is projected at "
    "their own adjusted season-average rate, not a coach's actual "
    "rotation plan. Per-player nuance (missing/new teammates, primary "
    "defender, scheme) stays in the single-player tool for now."
)


def get_team_roster(team_id):
    """Pull current live roster for a team via commonteamroster, with the
    same timeout=5 treatment as every other live call in this app.
    Returns a list of (player_id, player_name) tuples, or [] on failure."""
    from nba_api.stats.endpoints import commonteamroster
    try:
        roster = commonteamroster.CommonTeamRoster(team_id=team_id, timeout=5)
        df = roster.get_data_frames()[0]
        return list(zip(df["PLAYER_ID"], df["PLAYER"]))
    except Exception:
        return []


def predict_player_vs_opponent(player_id, player_name, opponent_id):
    """MVP matchup-predictor engine: season baseline + opponent-defense
    adjustment only. Deliberately excludes missing/new-teammate, primary
    defender, and scheme adjustments -- that nuance stays in the
    single-player tool, per the approved v1 scope.

    Returns None if there isn't enough real data for this player (e.g. a
    true rookie with no NBA history) so the caller can flag it rather
    than silently guessing.
    """
    try:
        season_stats, season_source = get_season_baseline(player_id, player_name)
    except Exception:
        return None
    if not season_stats:
        return None

    team_def_rating, league_avg_def, def_source_note = get_opponent_defense_with_fallback(opponent_id)

    DEF_ADJUSTMENT_STRENGTH = 0.5
    if team_def_rating is not None:
        def_gap_pct = (team_def_rating - league_avg_def) / league_avg_def
        def_adjustment = 1 + (def_gap_pct * DEF_ADJUSTMENT_STRENGTH)
    else:
        def_adjustment = 1.0

    predictions = {}
    for col, _label in STAT_COLUMNS:
        base_mean, _base_std = season_stats[col]
        predictions[col] = {"predicted": base_mean * def_adjustment}

    return predictions, season_source, def_source_note


with st.form("matchup_form"):
    mcol1, mcol2 = st.columns(2)
    with mcol1:
        default_a = team_names.index("Oklahoma City Thunder") if "Oklahoma City Thunder" in team_names else None
        team_a_input = st.selectbox(
            "Team A", options=team_names, index=default_a,
            placeholder="Search a team..."
        )
    with mcol2:
        default_b = team_names.index("San Antonio Spurs") if "San Antonio Spurs" in team_names else None
        team_b_input = st.selectbox(
            "Team B", options=team_names, index=default_b,
            placeholder="Search a team..."
        )
    matchup_submitted = st.form_submit_button("Predict matchup")

if matchup_submitted:
    if not team_a_input or not team_b_input:
        st.error("Please select both teams.")
        st.stop()
    if team_a_input == team_b_input:
        st.error("Please select two different teams.")
        st.stop()

    with st.spinner("Pulling rosters and calculating..."):
        team_a_id, team_a_full, team_a_abbr = get_team_id(team_a_input)
        team_b_id, team_b_full, team_b_abbr = get_team_id(team_b_input)

        def build_team_projection(team_id, opponent_id):
            roster = get_team_roster(team_id)
            rows = []
            skipped = []
            for pid, pname in roster:
                result = predict_player_vs_opponent(pid, pname, opponent_id)
                if result is None:
                    skipped.append(pname)
                    continue
                predictions, _season_source, _def_source_note = result
                row = {"Player": pname}
                for col, label in STAT_COLUMNS:
                    row[label] = round(predictions[col]["predicted"], 1)
                rows.append(row)
            return rows, skipped

        team_a_rows, team_a_skipped = build_team_projection(team_a_id, team_b_id)
        team_b_rows, team_b_skipped = build_team_projection(team_b_id, team_a_id)

    st.markdown(f"**{team_a_full}** projected box score")
    if team_a_rows:
        st.dataframe(pd.DataFrame(team_a_rows), width="stretch", hide_index=True)
    else:
        st.info("No players with enough data to project.")
    if team_a_skipped:
        st.caption(f"Not enough data to project: {', '.join(team_a_skipped)}")

    st.markdown(f"**{team_b_full}** projected box score")
    if team_b_rows:
        st.dataframe(pd.DataFrame(team_b_rows), width="stretch", hide_index=True)
    else:
        st.info("No players with enough data to project.")
    if team_b_skipped:
        st.caption(f"Not enough data to project: {', '.join(team_b_skipped)}")
'''


def main():
    if not APP_PATH.exists():
        print("ERROR: app.py not found in current directory. Run this from your Documents folder.")
        return

    text = APP_PATH.read_text()

    if MARKER in text:
        print("Already patched -- 'Predict a full matchup' section found. No changes made.")
        return

    with APP_PATH.open("a") as f:
        f.write(NEW_CODE)

    print("Patched successfully. Appended 'Predict a full matchup' section to app.py.")
    print("New functions added: get_team_roster(), predict_player_vs_opponent()")
    print("Restart your local Streamlit app to see it.")


if __name__ == "__main__":
    main()
