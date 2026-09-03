"""
Upgrade: get_opponent_missing_adjustment() previously weighted every
missing opponent player by minutes-per-game ONLY -- a 30-minute bench
player counted the same as a 30-minute All-Star. This patch weights
each missing player's minutes by their real E_NET_RATING (NBA's own
estimated per-100-possession plus-minus, from PlayerEstimatedMetrics),
so a genuine impact player missing matters more than a replacement-level
one missing the same amount of court time.

Falls back to last season's estimated metrics if the current season has
no games yet (preseason gap). Falls back to minutes-only weighting for
any individual player not found in the estimated-metrics table, rather
than dropping them.

Uses line-number-based replacement (find the function's def line and
the next top-level def) rather than exact text matching, since that's
what reliably worked earlier tonight for get_teammate_availability_adjustment.

Usage:
    python3 patch_missing_opponent_net_rating.py
Run from the same folder as app.py.
"""

import pathlib
import re

APP_PATH = pathlib.Path("app.py")

MARKER = "quality-weighted using"

NEW_FUNCTION = '''def get_opponent_missing_adjustment(missing_opponents, season):
    if not missing_opponents:
        return 1.0, "No missing opponent players specified -- no adjustment."

    # Pull league-wide estimated net ratings once (not per player) so a
    # missing player's real two-way impact -- not just their minutes --
    # informs how much their absence should matter. Falls back to last
    # season if the current one has no games yet (e.g. preseason).
    from nba_api.stats.endpoints import playerestimatedmetrics

    net_rating_by_id = {}
    metrics_season_used = None
    for try_season in [season, PREVIOUS_SEASON]:
        try:
            metrics = playerestimatedmetrics.PlayerEstimatedMetrics(
                season=try_season, timeout=10
            )
            metrics_df = metrics.get_data_frames()[0]
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
    for name in missing_opponents:
        try:
            match = players.find_players_by_full_name(name)
            if not match:
                continue
            pid = match[0]["id"]
            career = playercareerstats.PlayerCareerStats(player_id=pid, timeout=5)
            df = career.get_data_frames()[0]
            season_row = df[df["SEASON_ID"] == season]
            if season_row.empty:
                season_row = df.tail(1)
            mpg = season_row["MIN"].values[0] / season_row["GP"].values[0]

            net_rating = net_rating_by_id.get(pid)
            if net_rating is not None:
                quality_multiplier = max(MIN_QUALITY_MULTIPLIER, 1 + (net_rating / QUALITY_SCALE))
                weighted_mpg = mpg * quality_multiplier
                found_players.append((name, round(mpg, 1), round(net_rating, 1)))
            else:
                weighted_mpg = mpg  # no estimated-metrics data found for this
                                     # player -- fall back to raw MPG rather
                                     # than dropping them entirely
                found_players.append((name, round(mpg, 1), None))

            total_weighted_mpg += weighted_mpg
            time.sleep(0.5)
        except Exception:
            continue

    if not found_players:
        return 1.0, f"Could not find stats for {missing_opponents} -- skipping adjustment."

    minutes_fraction = total_weighted_mpg / 240
    adjustment = 1 + (minutes_fraction * 0.35)
    detail_parts = []
    for n, m, nr in found_players:
        if nr is not None:
            detail_parts.append(f"{n} ({m} MPG, {nr:+.1f} net rtg)")
        else:
            detail_parts.append(f"{n} ({m} MPG, net rtg unavailable)")
    detail = ", ".join(detail_parts)
    if metrics_season_used:
        metrics_note = f" [quality-weighted using {metrics_season_used} estimated net ratings]"
    else:
        metrics_note = " [net rating data unavailable -- weighted by MPG only]"
    return adjustment, (f"Missing: {detail}{metrics_note} -> "
                         f"\\U0001F691 Opponent Missing Players Layer Applied \\u2014 x{adjustment:.3f}")
'''


def main():
    if not APP_PATH.exists():
        print("ERROR: app.py not found in current directory.")
        return

    text = APP_PATH.read_text()

    if MARKER in text:
        print("Already patched -- 'quality-weighted using' found. No changes made.")
        return

    lines = text.split("\n")

    start_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith("def get_opponent_missing_adjustment("):
            start_idx = i
            break

    if start_idx is None:
        print("ERROR: could not find 'def get_opponent_missing_adjustment(' in app.py.")
        print("Paste: grep -n 'def get_opponent_missing_adjustment' app.py")
        return

    end_idx = None
    for i in range(start_idx + 1, len(lines)):
        stripped = lines[i]
        # next top-level def or decorator (no leading whitespace) ends the function
        if re.match(r'^(def |@st\.)', stripped):
            end_idx = i
            break

    if end_idx is None:
        print("ERROR: could not find the end of get_opponent_missing_adjustment "
              "(no following top-level def/@st. line found). Stopping without changes.")
        return

    old_block = "\n".join(lines[start_idx:end_idx])
    print(f"Found function spanning lines {start_idx + 1} to {end_idx} (exclusive of end).")
    print(f"Old block is {len(old_block)} characters -- replacing with upgraded version.")

    new_lines = lines[:start_idx] + NEW_FUNCTION.rstrip("\n").split("\n") + [""] + lines[end_idx:]
    new_text = "\n".join(new_lines)

    APP_PATH.write_text(new_text)
    print()
    print("Patched successfully. get_opponent_missing_adjustment() now weights each")
    print("missing player by E_NET_RATING alongside minutes, with graceful fallback")
    print("to last season's metrics (preseason gap) and to minutes-only weighting")
    print("for any player not found in the estimated-metrics table.")
    print("Restart Streamlit to see it.")


if __name__ == "__main__":
    main()
