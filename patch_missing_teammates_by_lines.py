"""
Robust replacement for get_teammate_availability_adjustment() that
locates the function by LINE NUMBER (via its `def` line and the next
top-level `def` after it) instead of matching exact text -- this
avoids the whitespace-mismatch problems that broke the last two
attempts.

This also restores the early-exit circuit breaker (MAX_CONSECUTIVE_
FAILURES / MAX_GAMES_TO_CHECK) which turned out to be missing from
this local copy -- it looks like it only ever landed on the deployed
cloud copy, not here. This patch folds that back in alongside the new
per-stat, contrast-check, and season-fallback logic, all at once.

Run this once, from the same folder as app.py.

Usage:
    python3 patch_missing_teammates_by_lines.py
"""

import re
from pathlib import Path

TARGET = Path("app.py")

NEW_FUNCTION_LINES = '''def get_teammate_availability_adjustment(player_id, missing_names, season):
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
    ), len(matching_games)
'''

OLD_CALL_SITE = '''        teammate_adj, teammate_note = get_teammate_availability_adjustment(
            player_id, missing_teammates, CURRENT_SEASON
        )'''

NEW_CALL_SITE = '''        if missing_teammates:
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


def main():
    text = TARGET.read_text()

    if '"matching_game_count"' in text or "matching_game_count)." in text:
        print("Already patched -- no changes needed.")
        return

    lines = text.splitlines(keepends=True)

    start_idx = None
    for i, line in enumerate(lines):
        if line.startswith("def get_teammate_availability_adjustment("):
            start_idx = i
            break

    if start_idx is None:
        print("Could not find get_teammate_availability_adjustment in app.py.")
        print("No changes made.")
        return

    end_idx = None
    for i in range(start_idx + 1, len(lines)):
        if re.match(r"^def \w", lines[i]):
            end_idx = i
            break

    if end_idx is None:
        print("Could not find the end of the function (next top-level def).")
        print("No changes made.")
        return

    new_lines = lines[:start_idx] + [NEW_FUNCTION_LINES] + lines[end_idx:]
    new_text = "".join(new_lines)

    missing = []
    if OLD_CALL_SITE not in new_text:
        missing.append("call site")
    if OLD_TOTAL_MULTIPLIER not in new_text:
        missing.append("total_multiplier")
    if OLD_PREDICTIONS_LOOP not in new_text:
        missing.append("predictions loop")

    if missing:
        print("Function replaced, but could not find expected text for: " + ", ".join(missing))
        print("No changes written -- aborting to avoid a half-applied patch.")
        return

    new_text = new_text.replace(OLD_CALL_SITE, NEW_CALL_SITE, 1)
    new_text = new_text.replace(OLD_TOTAL_MULTIPLIER, NEW_TOTAL_MULTIPLIER, 1)
    new_text = new_text.replace(OLD_PREDICTIONS_LOOP, NEW_PREDICTIONS_LOOP, 1)

    TARGET.write_text(new_text)
    print("Patched app.py successfully.")
    print("Missing teammates is now stat-specific, checks a 4-season window, requires")
    print("real with/without contrast, and has the early-exit circuit breaker restored.")


if __name__ == "__main__":
    main()
