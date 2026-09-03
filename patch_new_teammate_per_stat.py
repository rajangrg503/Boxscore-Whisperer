"""
Makes the "New teammate arriving" adjustment STAT-SPECIFIC instead of
one blended ratio applied to everything.

THE PROBLEM: the original version computed a single ratio from PTS
alone (heavy-minutes games vs. light-minutes games) and applied that
SAME ratio to every stat -- points, assists, rebounds, blocks, all of
it. That hides real, different effects: a center's rebounding and a
point guard's assists can move in completely different directions (or
not at all) when a new ball-handler arrives, and squashing that into
one "scoring multiplier" erases the very thing this feature is
supposed to reveal.

THE FIX: compute a separate heavy-vs-light comparison for EVERY
tracked stat (PTS, AST, REB, STL, BLK, FG3M, TOV), using that stat's
own real data -- not points standing in for everything. Each stat then
gets its own real, data-backed adjustment.

Run this once, from the same folder as app.py. Requires
patch_new_teammate_feature.py AND patch_new_teammate_season_fallback.py
to already be applied.

Usage:
    python3 patch_new_teammate_per_stat.py
"""

from pathlib import Path

TARGET = Path("app.py")

OLD_FUNCTION = '''def get_new_teammate_impact_adjustment(player_id, new_teammate_name, season):
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
    )'''

NEW_FUNCTION = '''def get_new_teammate_impact_adjustment(player_id, new_teammate_name, season):
    """Measures how a specific teammate's HEAVY on-court presence has
    historically correlated with this player's production, using real
    shared games -- not a guess. Splits games where both players were
    on the same team into "teammate played heavy minutes" vs. "teammate
    played light/no minutes", and compares this player's stats between
    those two buckets.

    Returns (adjustments, note) where adjustments is a dict mapping
    each STAT_COLUMNS key to its OWN ratio -- a center's rebounding and
    a point guard's assists can move in different directions (or not
    at all) when a new ball-handler arrives, so this deliberately does
    NOT blend everything into one scoring-based number the way a
    naive version would. A neutral dict (all 1.0) is returned whenever
    there isn't enough real data to trust a comparison.

    Only works when the two players have actual shared game history on
    the same team -- a brand-new pairing that has never shared the
    floor has no data to measure an effect from yet, and this function
    says so plainly rather than guessing."""
    neutral = {col: 1.0 for col, _ in STAT_COLUMNS}

    if not new_teammate_name:
        return neutral, "No new teammate specified -- no adjustment."

    match = players.find_players_by_full_name(new_teammate_name)
    if not match:
        return neutral, f"No player found named '{new_teammate_name}' -- check spelling, skipping."
    teammate_id = match[0]["id"]

    try:
        player_df = fetch_combined_game_log(player_id, season)
        teammate_df = fetch_combined_game_log(teammate_id, season)
    except Exception:
        return neutral, ("Game log unavailable for this season (live fetch failed, not "
                          "yet cached) -- skipping this adjustment.")

    if player_df.empty or teammate_df.empty:
        return neutral, (f"No shared game history found between this player and "
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
        return neutral, (f"Only found {len(shared)} shared game(s) as teammates with "
                          f"{new_teammate_name} this season -- too few to trust, "
                          f"skipping this adjustment.")

    HEAVY_MINUTES_THRESHOLD = 25
    heavy = shared[shared["MIN_teammate"] >= HEAVY_MINUTES_THRESHOLD]
    light = shared[shared["MIN_teammate"] < HEAVY_MINUTES_THRESHOLD]

    if len(heavy) < 3 or len(light) < 3:
        return neutral, (f"Found {len(shared)} shared games with {new_teammate_name}, but "
                          f"not enough of a split between heavy-minute and light-minute "
                          f"games ({len(heavy)} vs {len(light)}) to trust a comparison -- "
                          f"skipping.")

    adjustments = {}
    per_stat_notes = []
    for col, _label in STAT_COLUMNS:
        avg_heavy = heavy[col].mean()
        avg_light = light[col].mean()
        ratio = avg_heavy / avg_light if avg_light else 1.0
        adjustments[col] = ratio
        per_stat_notes.append(
            f"{col} {avg_heavy:.1f} vs {avg_light:.1f} ({(ratio - 1) * 100:+.1f}%)"
        )

    summary = ", ".join(per_stat_notes)
    return adjustments, (
        f"Found {len(shared)} shared games with {new_teammate_name} -- comparing the "
        f"{len(heavy)} game(s) where {new_teammate_name} played "
        f"{HEAVY_MINUTES_THRESHOLD}+ minutes vs. the {len(light)} game(s) with lighter "
        f"minutes, stat-by-stat: {summary}."
    )'''

OLD_CALL_SITE = '''        new_teammate_adj, new_teammate_note = get_new_teammate_impact_adjustment(
            player_id, new_teammate_input, CURRENT_SEASON
        )
        if new_teammate_input and new_teammate_adj == 1.0 and "No shared game history" in new_teammate_note:
            # Current season likely too thin (early in the year) -- try
            # last season before concluding there's genuinely no shared
            # history between these two players.
            prev_adj, prev_note = get_new_teammate_impact_adjustment(
                player_id, new_teammate_input, PREVIOUS_SEASON
            )
            if prev_adj != 1.0 or "No shared game history" not in prev_note:
                new_teammate_adj, new_teammate_note = prev_adj, prev_note'''

NEW_CALL_SITE = '''        new_teammate_adj_by_stat, new_teammate_note = get_new_teammate_impact_adjustment(
            player_id, new_teammate_input, CURRENT_SEASON
        )
        if new_teammate_input and "No shared game history" in new_teammate_note:
            # Current season likely too thin (early in the year) -- try
            # last season before concluding there's genuinely no shared
            # history between these two players.
            prev_by_stat, prev_note = get_new_teammate_impact_adjustment(
                player_id, new_teammate_input, PREVIOUS_SEASON
            )
            if "No shared game history" not in prev_note:
                new_teammate_adj_by_stat, new_teammate_note = prev_by_stat, prev_note'''

OLD_TOTAL_MULTIPLIER = '''        total_multiplier = def_adjustment * teammate_adj * opp_missing_adj * scheme_adj * new_teammate_adj'''
NEW_TOTAL_MULTIPLIER = '''        total_multiplier = def_adjustment * teammate_adj * opp_missing_adj * scheme_adj'''

OLD_PREDICTIONS_LOOP = '''        predictions = {}
        for col, _label in STAT_COLUMNS:
            base_mean, base_std = baseline_stats[col]
            predicted = base_mean * total_multiplier
            spread = base_std if pd.notna(base_std) else predicted * 0.2'''
NEW_PREDICTIONS_LOOP = '''        predictions = {}
        for col, _label in STAT_COLUMNS:
            base_mean, base_std = baseline_stats[col]
            stat_multiplier = total_multiplier * new_teammate_adj_by_stat.get(col, 1.0)
            predicted = base_mean * stat_multiplier
            spread = base_std if pd.notna(base_std) else predicted * 0.2'''

EDITS = [
    ("function (per-stat rewrite)", OLD_FUNCTION, NEW_FUNCTION),
    ("call site (season fallback, per-stat)", OLD_CALL_SITE, NEW_CALL_SITE),
    ("total_multiplier (remove global new-teammate factor)", OLD_TOTAL_MULTIPLIER, NEW_TOTAL_MULTIPLIER),
    ("predictions loop (apply per-stat factor)", OLD_PREDICTIONS_LOOP, NEW_PREDICTIONS_LOOP),
]


def main():
    text = TARGET.read_text()

    if "new_teammate_adj_by_stat" in text:
        print("Already patched -- no changes needed.")
        return

    missing = [label for label, old, new in EDITS if old not in text]
    if missing:
        print("Could not find expected text for: " + ", ".join(missing))
        print("Make sure patch_new_teammate_feature.py and")
        print("patch_new_teammate_season_fallback.py were both run first.")
        print("No changes made.")
        return

    for label, old, new in EDITS:
        text = text.replace(old, new, 1)

    TARGET.write_text(text)
    print("Patched app.py successfully.")
    print("The new-teammate adjustment is now stat-specific: rebounds move based on")
    print("real rebounding data, assists on real assist data, etc. -- not one blended")
    print("scoring number applied to everything.")


if __name__ == "__main__":
    main()
