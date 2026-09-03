"""
Overhauls get_teammate_availability_adjustment() ("Missing teammates")
to match the same standards already built for "New teammate arriving":

  1. STAT-SPECIFIC: returns a per-stat dict instead of one PTS-derived
     ratio applied to everything -- a center's rebounds and a guard's
     assists should move based on their OWN real data.

  2. MULTI-SEASON FALLBACK: checks the same 4-season HEAD_TO_HEAD_SEASONS
     window instead of only the current season.

  3. REAL BUG FIX: if a teammate has left the team ENTIRELY (e.g. traded
     away), every game in the current season trivially "misses" them --
     there's no genuine with/without contrast to compare, so the old
     code would silently return a near-meaningless ratio (comparing the
     season to itself) instead of an honest skip. This patch requires a
     real sample of games BOTH with and without the teammate present
     before trusting a comparison, and falls back to an earlier season
     (e.g. real in-season injury absences) when the current season has
     no contrast to offer.

Run this once, from the same folder as app.py. Requires
patch_new_teammate_game_count_fix.py to already be applied (uses
HEAD_TO_HEAD_SEASONS and the same season-fallback pattern).

Usage:
    python3 patch_missing_teammates_overhaul.py
"""

from pathlib import Path

TARGET = Path("app.py")

OLD_FUNCTION = '''def get_teammate_availability_adjustment(player_id, missing_names, season):
    if not missing_names:
        return 1.0, "No missing teammates specified -- no adjustment."

    try:
        df = fetch_combined_game_log(player_id, season)
    except Exception:
        return 1.0, (f"No game log available for {season} (live fetch failed, not yet "
                      f"cached) -- skipping this adjustment.")

    matching_games = []
    consecutive_failures = 0
    games_checked = 0
    MAX_CONSECUTIVE_FAILURES = 3  # after this many in a row, assume the
                                   # live host is blocked for this whole
                                   # request and stop paying the timeout
                                   # cost on every remaining game
    MAX_GAMES_TO_CHECK = 20        # bound the worst case even when the
                                    # live host IS reachable

    for _, row in df.iterrows():
        if games_checked >= MAX_GAMES_TO_CHECK:
            break
        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            break
        game_id = row["Game_ID"]
        games_checked += 1
        try:
            box = boxscoretraditionalv2.BoxScoreTraditionalV2(game_id=game_id, timeout=5)
            box_df = box.get_data_frames()[0]
            players_in_game = set(box_df["PLAYER_NAME"])
        except Exception:
            consecutive_failures += 1
            continue
        consecutive_failures = 0  # reset streak on any success
        time.sleep(0.5)
        if all(name not in players_in_game for name in missing_names):
            matching_games.append(row)

    if len(matching_games) < 3:
        return 1.0, (f"Only found {len(matching_games)} past games missing "
                      f"{missing_names} -- too few to trust, skipping this adjustment.")

    matched_df = pd.DataFrame(matching_games)
    avg_with_missing = matched_df["PTS"].mean()
    avg_overall = df["PTS"].mean()
    ratio = avg_with_missing / avg_overall if avg_overall else 1.0
    return ratio, (f"Found {len(matching_games)} games missing {missing_names}: "
                    f"averaged {avg_with_missing:.1f} pts vs. {avg_overall:.1f} pts overall "
                    f"({(ratio - 1) * 100:+.1f}%)")'''

NEW_FUNCTION = '''def get_teammate_availability_adjustment(player_id, missing_names, season):
    """Measures how this player's production differs in real games
    where a specific teammate did NOT play vs. games where they did,
    within the same season -- a genuine natural experiment, not a
    guess.

    Returns (adjustments, note, matching_game_count). adjustments is a
    dict mapping each STAT_COLUMNS key to its OWN ratio -- a center's
    rebounds and a guard's assists can move differently, so this does
    NOT blend everything into one scoring-based number.

    Requires a real sample of games BOTH missing the teammate AND with
    them present. If a teammate has left the team entirely, every game
    this season trivially "misses" them -- that's not a genuine
    comparison, so it's detected and skipped rather than silently
    returning a near-meaningless ratio. matching_game_count is 0
    whenever there's no usable comparison, so callers can fall back to
    an earlier season without relying on fragile text-matching."""
    neutral = {col: 1.0 for col, _ in STAT_COLUMNS}

    if not missing_names:
        return neutral, "No missing teammates specified -- no adjustment.", 0

    try:
        df = fetch_combined_game_log(player_id, season)
    except Exception:
        return neutral, (f"No game log available for {season} (live fetch failed, not yet "
                          f"cached) -- skipping this adjustment."), 0

    matching_games = []
    consecutive_failures = 0
    games_checked = 0
    MAX_CONSECUTIVE_FAILURES = 3  # after this many in a row, assume the
                                   # live host is blocked for this whole
                                   # request and stop paying the timeout
                                   # cost on every remaining game
    MAX_GAMES_TO_CHECK = 20        # bound the worst case even when the
                                    # live host IS reachable

    for _, row in df.iterrows():
        if games_checked >= MAX_GAMES_TO_CHECK:
            break
        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            break
        game_id = row["Game_ID"]
        games_checked += 1
        try:
            box = boxscoretraditionalv2.BoxScoreTraditionalV2(game_id=game_id, timeout=5)
            box_df = box.get_data_frames()[0]
            players_in_game = set(box_df["PLAYER_NAME"])
        except Exception:
            consecutive_failures += 1
            continue
        consecutive_failures = 0  # reset streak on any success
        time.sleep(0.5)
        if all(name not in players_in_game for name in missing_names):
            matching_games.append(row)

    present_count = games_checked - len(matching_games)

    if len(matching_games) < 3 or present_count < 3:
        return neutral, (f"Found {len(matching_games)} game(s) missing {missing_names} "
                          f"out of {games_checked} checked (and {present_count} with them "
                          f"present) -- not enough real contrast in both directions to "
                          f"trust a comparison, skipping this adjustment."), 0

    matched_df = pd.DataFrame(matching_games)
    adjustments = {}
    per_stat_notes = []
    for col, _label in STAT_COLUMNS:
        avg_with_missing = matched_df[col].mean()
        avg_overall = df[col].mean()
        ratio = avg_with_missing / avg_overall if avg_overall else 1.0
        adjustments[col] = ratio
        per_stat_notes.append(
            f"{col} {avg_with_missing:.1f} vs {avg_overall:.1f} overall ({(ratio - 1) * 100:+.1f}%)"
        )

    summary = ", ".join(per_stat_notes)
    return adjustments, (
        f"Found {len(matching_games)} games missing {missing_names} (vs. {present_count} "
        f"with them present), stat-by-stat: {summary}."
    ), len(matching_games)'''

OLD_CALL_SITE = '''        teammate_adj, teammate_note = get_teammate_availability_adjustment(
            player_id, missing_teammates, CURRENT_SEASON
        )'''

NEW_CALL_SITE = '''        if missing_teammates:
            # Check the full multi-season window -- a teammate who's
            # since been traded away has no in-season contrast in the
            # CURRENT season (every game trivially "misses" them), so
            # a real comparison often lives in an earlier season
            # instead (e.g. real in-season injury absences).
            teammate_adj_by_stat, teammate_note = None, None
            for _try_season in HEAD_TO_HEAD_SEASONS:
                teammate_adj_by_stat, teammate_note, _teammate_count = get_teammate_availability_adjustment(
                    player_id, missing_teammates, _try_season
                )
                if _teammate_count > 0:
                    break
        else:
            teammate_adj_by_stat, teammate_note, _ = get_teammate_availability_adjustment(
                player_id, missing_teammates, CURRENT_SEASON
            )'''

OLD_TOTAL_MULTIPLIER = '''        total_multiplier = def_adjustment * teammate_adj * opp_missing_adj * scheme_adj'''
NEW_TOTAL_MULTIPLIER = '''        total_multiplier = def_adjustment * opp_missing_adj * scheme_adj'''

OLD_PREDICTIONS_LOOP = '''        predictions = {}
        for col, _label in STAT_COLUMNS:
            base_mean, base_std = baseline_stats[col]
            stat_multiplier = total_multiplier * new_teammate_adj_by_stat.get(col, 1.0)
            predicted = base_mean * stat_multiplier
            spread = base_std if pd.notna(base_std) else predicted * 0.2'''
NEW_PREDICTIONS_LOOP = '''        predictions = {}
        for col, _label in STAT_COLUMNS:
            base_mean, base_std = baseline_stats[col]
            stat_multiplier = (
                total_multiplier
                * teammate_adj_by_stat.get(col, 1.0)
                * new_teammate_adj_by_stat.get(col, 1.0)
            )
            predicted = base_mean * stat_multiplier
            spread = base_std if pd.notna(base_std) else predicted * 0.2'''

EDITS = [
    ("function (per-stat + contrast check)", OLD_FUNCTION, NEW_FUNCTION),
    ("call site (season fallback)", OLD_CALL_SITE, NEW_CALL_SITE),
    ("total_multiplier (remove global teammate factor)", OLD_TOTAL_MULTIPLIER, NEW_TOTAL_MULTIPLIER),
    ("predictions loop (apply both per-stat factors)", OLD_PREDICTIONS_LOOP, NEW_PREDICTIONS_LOOP),
]


def main():
    text = TARGET.read_text()

    if "teammate_adj_by_stat" in text:
        print("Already patched -- no changes needed.")
        return

    missing = [label for label, old, new in EDITS if old not in text]
    if missing:
        print("Could not find expected text for: " + ", ".join(missing))
        print("Make sure patch_new_teammate_game_count_fix.py was run first.")
        print("No changes made.")
        return

    for label, old, new in EDITS:
        text = text.replace(old, new, 1)

    TARGET.write_text(text)
    print("Patched app.py successfully.")
    print("Missing teammates is now stat-specific, checks a 4-season window, and")
    print("requires real with/without contrast before trusting a comparison --")
    print("instead of silently comparing a season to itself when a teammate has")
    print("left the team entirely.")


if __name__ == "__main__":
    main()
