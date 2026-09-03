#!/usr/bin/env python3
"""
patch_roster_cache_fallback.py

Fixes a real functional break on Streamlit Cloud: get_team_roster() was
the one live nba_api call in the whole app that did NOT go through
cached_or_live() (the file's own comment at line 45 says "every real
nba_api call below routes through cached_or_live()" -- this function
was the exception). On Cloud, live nba_api calls are blocked, so this
function always returned [] there, making the "Predict a full matchup"
tab show "No players with enough data to project" for every team.

Fix: refactor get_team_roster to fetch a DataFrame (same shape
cached_or_live() and fetch_combined_game_log already use) and route it
through cached_or_live(), so it falls back to a local data_cache/ file
on Cloud instead of unconditionally returning [].

This patch alone is NOT sufficient -- the cache file has to actually
exist. Run batch_cache_rosters.py locally afterward (same pattern as
batch_cache_players.py) to populate data_cache/roster_<team_id>.json for
all 30 teams, then commit + push data_cache/ so Cloud can read it.

Idempotent: checks for the patch marker first; if already applied, skips.
Verifies exact-text occurrence count before touching the file.
"""

import sys

TARGET_FILE = "app.py"
MARKER = "# patch_roster_cache_fallback"

OLD_BLOCK = '''def get_team_roster(team_id):
    """Pull current live roster for a team via commonteamroster, with the
    same timeout=5 treatment as every other live call in this app.
    Returns a list of (player_id, player_name) tuples, or [] on failure."""
    from nba_api.stats.endpoints import commonteamroster
    for attempt_timeout in (5, 10):
        try:
            roster = commonteamroster.CommonTeamRoster(
                team_id=team_id, season=CURRENT_SEASON, timeout=attempt_timeout
            )
            df = roster.get_data_frames()[0]
            return list(zip(df["PLAYER_ID"], df["PLAYER"]))
        except Exception as e:
            print(
                f"[get_team_roster] attempt (timeout={attempt_timeout}) failed "
                f"for team_id={team_id}: {type(e).__name__}: {e}"
            )
    return []'''

NEW_BLOCK = '''def get_team_roster(team_id):
    """Pull current live roster for a team via commonteamroster, with the
    same timeout=5 treatment as every other live call in this app.

    Routes through cached_or_live() like every other real nba_api call in
    this file (see the comment near the top), so on Cloud -- where live
    nba_api calls are blocked -- this falls back to a local data_cache/
    copy instead of silently returning an empty roster. Requires
    batch_cache_rosters.py to have been run locally and data_cache/
    committed, same as every other cached endpoint.

    Returns a list of (player_id, player_name) tuples, or [] if neither a
    live fetch nor a cached copy is available.
    """  # patch_roster_cache_fallback
    from nba_api.stats.endpoints import commonteamroster

    def _fetch():
        last_error = None
        for attempt_timeout in (5, 10):
            try:
                roster = commonteamroster.CommonTeamRoster(
                    team_id=team_id, season=CURRENT_SEASON, timeout=attempt_timeout
                )
                return roster.get_data_frames()[0]
            except Exception as e:
                last_error = e
                print(
                    f"[get_team_roster] attempt (timeout={attempt_timeout}) failed "
                    f"for team_id={team_id}: {type(e).__name__}: {e}"
                )
        raise last_error if last_error else RuntimeError("get_team_roster: no attempts made")

    try:
        df, _source = cached_or_live(f"roster_{team_id}", _fetch)
    except Exception as e:
        print(f"[get_team_roster] no live or cached roster for team_id={team_id}: {e}")
        return []

    if df is None or df.empty:
        return []
    return list(zip(df["PLAYER_ID"], df["PLAYER"]))'''


def main():
    with open(TARGET_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    if MARKER in content:
        print("Already patched (marker found) -- skipping, no changes made.")
        return

    count = content.count(OLD_BLOCK)
    print(f"found: {count} (expected: 1)")

    if count != 1:
        print("ABORTING -- occurrence count mismatch. No changes made.")
        print("Re-run `sed -n \"2369,2387p\" app.py` and confirm exact live text before retrying.")
        sys.exit(1)

    content = content.replace(OLD_BLOCK, NEW_BLOCK)

    with open(TARGET_FILE, "w", encoding="utf-8") as f:
        f.write(content)

    print("Patched: get_team_roster now routes through cached_or_live().")
    print("Restart Streamlit locally to confirm nothing broke (it should still work")
    print("live, same as before, since your Mac can reach nba_api fine).")
    print("")
    print("NEXT STEP (required before this fixes anything on Cloud):")
    print("  Run batch_cache_rosters.py to populate data_cache/roster_<id>.json")
    print("  for all 30 teams, then commit + push data_cache/.")


if __name__ == "__main__":
    main()
