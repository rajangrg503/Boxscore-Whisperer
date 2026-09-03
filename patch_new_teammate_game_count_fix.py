"""
Fixes a real bug in the season-lookback loop: it stopped searching
after the FIRST season checked whenever that season reported "Only
found 0 shared game(s)..." -- because the loop's stop condition only
recognized the phrase "No shared game history", not this other zero-
games message from a different branch of the function. String-matching
free text to make control-flow decisions is fragile, and this is a
concrete example of it going wrong.

THE FIX: have get_new_teammate_impact_adjustment() return an actual
integer game count as a third value, and have the loop use THAT to
decide whether to keep checking earlier seasons -- no more guessing
from message text.

Run this once, from the same folder as app.py. Requires
patch_new_teammate_wider_lookback.py to already be applied.

Usage:
    python3 patch_new_teammate_game_count_fix.py
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

NEW_FUNCTION = '''def get_new_teammate_impact_adjustment(player_id, new_teammate_name, season):
    """Measures how a specific teammate's HEAVY on-court presence has
    historically correlated with this player's production, using real
    shared games -- not a guess. Splits games where both players were
    on the same team into "teammate played heavy minutes" vs. "teammate
    played light/no minutes", and compares this player's stats between
    those two buckets.

    Returns (adjustments, note, shared_game_count). adjustments is a
    dict mapping each STAT_COLUMNS key to its OWN ratio -- a center's
    rebounding and a point guard's assists can move in different
    directions (or not at all) when a new ball-handler arrives, so
    this deliberately does NOT blend everything into one scoring-based
    number. shared_game_count is a real integer (not a parsed message)
    so callers can decide whether to check an earlier season without
    relying on fragile text-matching.

    A neutral dict (all 1.0) and a count of 0 are returned whenever
    there isn't enough real data to trust a comparison.

    Only works when the two players have actual shared game history on
    the same team -- a brand-new pairing that has never shared the
    floor has no data to measure an effect from yet, and this function
    says so plainly rather than guessing."""
    neutral = {col: 1.0 for col, _ in STAT_COLUMNS}

    if not new_teammate_name:
        return neutral, "No new teammate specified -- no adjustment.", 0

    match = players.find_players_by_full_name(new_teammate_name)
    if not match:
        return neutral, f"No player found named '{new_teammate_name}' -- check spelling, skipping.", 0
    teammate_id = match[0]["id"]

    try:
        player_df = fetch_combined_game_log(player_id, season)
        teammate_df = fetch_combined_game_log(teammate_id, season)
    except Exception:
        return neutral, ("Game log unavailable for this season (live fetch failed, not "
                          "yet cached) -- skipping this adjustment."), 0

    if player_df.empty or teammate_df.empty:
        return neutral, (f"No shared game history found between this player and "
                          f"{new_teammate_name} yet -- likely a brand-new pairing. This "
                          f"adjustment needs real games played together to measure an "
                          f"effect, so it's skipped rather than guessed at."), 0

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
                          f"skipping this adjustment."), len(shared)

    HEAVY_MINUTES_THRESHOLD = 25
    heavy = shared[shared["MIN_teammate"] >= HEAVY_MINUTES_THRESHOLD]
    light = shared[shared["MIN_teammate"] < HEAVY_MINUTES_THRESHOLD]

    if len(heavy) < 3 or len(light) < 3:
        return neutral, (f"Found {len(shared)} shared games with {new_teammate_name}, but "
                          f"not enough of a split between heavy-minute and light-minute "
                          f"games ({len(heavy)} vs {len(light)}) to trust a comparison -- "
                          f"skipping."), len(shared)

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
    ), len(shared)'''

OLD_CALL_SITE = '''        if new_teammate_input:
            # Check the full multi-season window (same one used for
            # opponent head-to-head elsewhere) rather than stopping
            # after just one fallback season -- a real pairing can sit
            # further back if one of the two players has since been
            # traded away.
            new_teammate_adj_by_stat, new_teammate_note = None, None
            for _try_season in HEAD_TO_HEAD_SEASONS:
                new_teammate_adj_by_stat, new_teammate_note = get_new_teammate_impact_adjustment(
                    player_id, new_teammate_input, _try_season
                )
                if "No shared game history" not in new_teammate_note:
                    break
        else:
            new_teammate_adj_by_stat, new_teammate_note = get_new_teammate_impact_adjustment(
                player_id, new_teammate_input, CURRENT_SEASON
            )'''

NEW_CALL_SITE = '''        if new_teammate_input:
            # Check the full multi-season window (same one used for
            # opponent head-to-head elsewhere) rather than stopping
            # after just one fallback season -- a real pairing can sit
            # further back if one of the two players has since been
            # traded away. Uses the real shared-game COUNT to decide
            # whether to keep looking, not fragile text-matching.
            new_teammate_adj_by_stat, new_teammate_note = None, None
            for _try_season in HEAD_TO_HEAD_SEASONS:
                new_teammate_adj_by_stat, new_teammate_note, _shared_count = get_new_teammate_impact_adjustment(
                    player_id, new_teammate_input, _try_season
                )
                if _shared_count > 0:
                    break
        else:
            new_teammate_adj_by_stat, new_teammate_note, _ = get_new_teammate_impact_adjustment(
                player_id, new_teammate_input, CURRENT_SEASON
            )'''


def main():
    text = TARGET.read_text()

    if "shared_game_count" in text:
        print("Already patched -- no changes needed.")
        return

    missing = []
    if OLD_FUNCTION not in text:
        missing.append("function body")
    if OLD_CALL_SITE not in text:
        missing.append("call site")

    if missing:
        print("Could not find expected text for: " + ", ".join(missing))
        print("Make sure patch_new_teammate_wider_lookback.py was run first.")
        print("No changes made.")
        return

    text = text.replace(OLD_FUNCTION, NEW_FUNCTION, 1)
    text = text.replace(OLD_CALL_SITE, NEW_CALL_SITE, 1)
    TARGET.write_text(text)
    print("Patched app.py successfully.")
    print("The season-lookback loop now uses a real shared-game count instead of")
    print("guessing from message text -- it will correctly keep checking earlier")
    print("seasons when a season shows 0 shared games, not just when it shows the")
    print("'brand-new pairing' message specifically.")


if __name__ == "__main__":
    main()
