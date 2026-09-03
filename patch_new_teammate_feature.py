"""
Adds a new "New teammate arriving" adjustment to Boxscore Whisperer.

THE IDEA: when a new, ball-dominant teammate joins a team, it can
genuinely change another player's role -- shot volume, assist chances,
usage. This feature measures that using real shared game history: it
finds every game where the target player and the new teammate were
actually on the same roster, splits those into "new teammate played
heavy minutes" vs. "light minutes", and compares the target player's
stats between the two groups.

HONEST LIMITATION (by design, not a bug): if the two players have
never actually shared a team, there's no shared-game data to measure
an effect from yet -- the function says so plainly and skips the
adjustment rather than guessing. Once they've played some real games
together, this starts finding signal automatically.

This patch touches SIX places in app.py:
  1. A new get_new_teammate_impact_adjustment() function
  2. A new UI field ("New teammate arriving") in Advanced options
  3. The function call + inclusion in total_multiplier
  4. Saving new_teammate_note into st.session_state["results"]
  5. Restoring new_teammate_note on reruns
  6. A new numbered step in "See how this estimate was built"
     (renumbers Primary defender/Scheme/etc. by one)

Run this once, from the same folder as app.py. All six edits are
applied together, or none are -- a partial application would leave
app.py referencing an undefined variable and crash.

Usage:
    python3 patch_new_teammate_feature.py
"""

from pathlib import Path

TARGET = Path("app.py")

NEW_FUNCTION = '''

def get_new_teammate_impact_adjustment(player_id, new_teammate_name, season):
    """Measures how a specific teammate's HEAVY on-court presence has
    historically correlated with this player's production, using real
    shared games -- not a guess. Splits games where both players were
    on the same team into "teammate played heavy minutes" vs. "teammate
    played light/no minutes", and compares this player's stats between
    those two buckets.

    Only works when the two players have actual shared game history on
    the same team -- a brand-new pairing that has never shared the
    floor has no data to measure an effect from yet, and this function
    says so plainly rather than guessing."""
    if not new_teammate_name:
        return 1.0, "No new teammate specified -- no adjustment."

    match = players.find_players_by_full_name(new_teammate_name)
    if not match:
        return 1.0, f"No player found named '{new_teammate_name}' -- check spelling, skipping."
    teammate_id = match[0]["id"]

    try:
        player_df = fetch_combined_game_log(player_id, season)
        teammate_df = fetch_combined_game_log(teammate_id, season)
    except Exception:
        return 1.0, ("Game log unavailable for this season (live fetch failed, not "
                      "yet cached) -- skipping this adjustment.")

    if player_df.empty or teammate_df.empty:
        return 1.0, (f"No shared game history found between this player and "
                      f"{new_teammate_name} yet -- likely a brand-new pairing. This "
                      f"adjustment needs real games played together to measure an "
                      f"effect, so it's skipped rather than guessed at.")

    # Only trust games where they were on the SAME team, not games where
    # they happened to face each other as opponents (that's a different
    # question, already covered by the head-to-head features).
    player_df = player_df.copy()
    teammate_df = teammate_df.copy()
    player_df["_team_abbr"] = player_df["MATCHUP"].str.split().str[0]
    teammate_df["_team_abbr"] = teammate_df["MATCHUP"].str.split().str[0]

    shared = player_df.merge(
        teammate_df[["Game_ID", "MIN", "_team_abbr"]],
        on="Game_ID", suffixes=("", "_teammate"),
    )
    shared = shared[shared["_team_abbr"] == shared["_team_abbr_teammate"]]

    if len(shared) < 5:
        return 1.0, (f"Only found {len(shared)} shared game(s) as teammates with "
                      f"{new_teammate_name} this season -- too few to trust, "
                      f"skipping this adjustment.")

    HEAVY_MINUTES_THRESHOLD = 25
    heavy = shared[shared["MIN_teammate"] >= HEAVY_MINUTES_THRESHOLD]
    light = shared[shared["MIN_teammate"] < HEAVY_MINUTES_THRESHOLD]

    if len(heavy) < 3 or len(light) < 3:
        return 1.0, (f"Found {len(shared)} shared games with {new_teammate_name}, but "
                      f"not enough of a split between heavy-minute and light-minute "
                      f"games ({len(heavy)} vs {len(light)}) to trust a comparison -- "
                      f"skipping.")

    avg_heavy = heavy["PTS"].mean()
    avg_light = light["PTS"].mean()
    ratio = avg_heavy / avg_light if avg_light else 1.0
    return ratio, (
        f"Found {len(shared)} shared games with {new_teammate_name}: in the "
        f"{len(heavy)} game(s) where {new_teammate_name} played "
        f"{HEAVY_MINUTES_THRESHOLD}+ minutes, this player averaged {avg_heavy:.1f} "
        f"pts vs. {avg_light:.1f} pts in the {len(light)} game(s) with lighter "
        f"{new_teammate_name} minutes ({(ratio - 1) * 100:+.1f}%)."
    )

'''

EDITS = [
    (
        "new function definition",
        '''    matched_df = pd.DataFrame(matching_games)
    avg_with_missing = matched_df["PTS"].mean()
    avg_overall = df["PTS"].mean()
    ratio = avg_with_missing / avg_overall if avg_overall else 1.0
    return ratio, (f"Found {len(matching_games)} games missing {missing_names}: "
                    f"averaged {avg_with_missing:.1f} pts vs. {avg_overall:.1f} pts overall "
                    f"({(ratio - 1) * 100:+.1f}%)")


def get_opponent_missing_adjustment(missing_opponents, season):''',
        '''    matched_df = pd.DataFrame(matching_games)
    avg_with_missing = matched_df["PTS"].mean()
    avg_overall = df["PTS"].mean()
    ratio = avg_with_missing / avg_overall if avg_overall else 1.0
    return ratio, (f"Found {len(matching_games)} games missing {missing_names}: "
                    f"averaged {avg_with_missing:.1f} pts vs. {avg_overall:.1f} pts overall "
                    f"({(ratio - 1) * 100:+.1f}%)")
''' + NEW_FUNCTION + '''
def get_opponent_missing_adjustment(missing_opponents, season):''',
    ),
    (
        "UI field",
        '''        with adv1:
            missing_teammates = st.multiselect(
                "Missing teammates", options=player_names, default=[],
                placeholder="Search and select players...",
            )
            defender_input = st.selectbox(
                "Primary defender assigned", options=player_names, index=None,
                placeholder="Search a defender...",
            )''',
        '''        with adv1:
            missing_teammates = st.multiselect(
                "Missing teammates", options=player_names, default=[],
                placeholder="Search and select players...",
            )
            new_teammate_input = st.selectbox(
                "New teammate arriving (optional)", options=player_names, index=None,
                placeholder="Search a player who just joined...",
            )
            st.caption(
                "Uses real shared games to compare this player's stats when this "
                "teammate played heavy minutes vs. light minutes. Needs actual "
                "shared game history to work -- a pairing that hasn't shared the "
                "floor yet will be flagged, not guessed at."
            )
            defender_input = st.selectbox(
                "Primary defender assigned", options=player_names, index=None,
                placeholder="Search a defender...",
            )''',
    ),
    (
        "function call + total_multiplier",
        '''        teammate_adj, teammate_note = get_teammate_availability_adjustment(
            player_id, missing_teammates, CURRENT_SEASON
        )
        opp_missing_adj, opp_missing_note = get_opponent_missing_adjustment(
            missing_opponents, PREVIOUS_SEASON
        )''',
        '''        teammate_adj, teammate_note = get_teammate_availability_adjustment(
            player_id, missing_teammates, CURRENT_SEASON
        )
        new_teammate_adj, new_teammate_note = get_new_teammate_impact_adjustment(
            player_id, new_teammate_input, CURRENT_SEASON
        )
        opp_missing_adj, opp_missing_note = get_opponent_missing_adjustment(
            missing_opponents, PREVIOUS_SEASON
        )''',
    ),
    (
        "total_multiplier inclusion",
        '''        total_multiplier = def_adjustment * teammate_adj * opp_missing_adj * scheme_adj''',
        '''        total_multiplier = def_adjustment * teammate_adj * opp_missing_adj * scheme_adj * new_teammate_adj''',
    ),
    (
        "session_state save",
        '''        "teammate_note": teammate_note,
        "opp_missing_note": opp_missing_note,''',
        '''        "teammate_note": teammate_note,
        "new_teammate_note": new_teammate_note,
        "opp_missing_note": opp_missing_note,''',
    ),
    (
        "session_state restore",
        '''    teammate_note = r["teammate_note"]
    opp_missing_note = r["opp_missing_note"]''',
        '''    teammate_note = r["teammate_note"]
    new_teammate_note = r["new_teammate_note"]
    opp_missing_note = r["opp_missing_note"]''',
    ),
    (
        "breakdown step renumbering",
        '''        st.write(f"**[3] Missing teammates:** {teammate_note}")
        st.write(f"**[4] Missing opponent players:** {opp_missing_note}")
        st.write(f"**[5] Primary defender:** {defender_note}")
        st.write(f"**[6] Scheme:** {scheme_note}")
        if scheme_executor_input:
            st.write(f"**[7] Scheme executed by (reference only):** {scheme_executor_input} "
                     f"-- not used in the calculation, no data exists to attribute schemes to individual players.")
        if post_change_thin_sample:
            st.write(
                f"**[8] Roster-change uncertainty:** the likely range above was widened "
                f"x{THIN_SAMPLE_SPREAD_MULTIPLIER} since the post-change sample is small -- "
                f"treat this prediction as a rougher estimate than usual until more games "
                f"have been played with the new roster."
            )''',
        '''        st.write(f"**[3] Missing teammates:** {teammate_note}")
        st.write(f"**[4] Missing opponent players:** {opp_missing_note}")
        st.write(f"**[5] New teammate arriving:** {new_teammate_note}")
        st.write(f"**[6] Primary defender:** {defender_note}")
        st.write(f"**[7] Scheme:** {scheme_note}")
        if scheme_executor_input:
            st.write(f"**[8] Scheme executed by (reference only):** {scheme_executor_input} "
                     f"-- not used in the calculation, no data exists to attribute schemes to individual players.")
        if post_change_thin_sample:
            st.write(
                f"**[9] Roster-change uncertainty:** the likely range above was widened "
                f"x{THIN_SAMPLE_SPREAD_MULTIPLIER} since the post-change sample is small -- "
                f"treat this prediction as a rougher estimate than usual until more games "
                f"have been played with the new roster."
            )''',
    ),
]


def main():
    text = TARGET.read_text()

    if "get_new_teammate_impact_adjustment" in text:
        print("Already patched -- no changes needed.")
        return

    missing = [label for label, old, new in EDITS if old not in text]
    if missing:
        print("Could not find expected text for: " + ", ".join(missing))
        print("No changes made -- app.py may differ from what this patch expects.")
        return

    for label, old, new in EDITS:
        text = text.replace(old, new, 1)

    TARGET.write_text(text)
    print("Patched app.py successfully.")
    print("New feature added: 'New teammate arriving' (Advanced options).")
    print("It compares real shared games (heavy vs. light minutes) where available,")
    print("and clearly flags pairings with no shared history yet instead of guessing.")


if __name__ == "__main__":
    main()
