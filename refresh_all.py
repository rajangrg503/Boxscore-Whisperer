"""
Single orchestrator for refreshing Boxscore Whisperer's data_cache/.

Runs data_watchdog against every known nba_api endpoint FIRST, prints a
pass/fail table, then only runs the batch_cache_*.py script for each
endpoint that passed. An endpoint that fails its watchdog check is
skipped entirely -- its existing data_cache/ files are left completely
untouched, and the failure is both printed here and logged to
logs/watchdog_alerts.jsonl.

Each batch script also calls require_valid() itself at the top of its
own main() -- that's intentional redundancy (cheap, since the status
was just computed here), not duplicated logic: it keeps every batch
script independently safe to run standalone, not just via this
orchestrator.

Three endpoints (league_dash_team_stats_advanced, synergy_play_types,
player_game_log) have no dedicated batch_cache_*.py script -- they're
refreshed by refresh_cache.py instead. This orchestrator still checks
them (for early visibility into a break) but doesn't invoke anything
for them; run refresh_cache.py separately for those.

Usage:
    python3 refresh_all.py
"""

from data_watchdog import runner

# endpoint_key -> batch script module name, or None if refreshed by
# refresh_cache.py instead of a dedicated batch script.
BATCH_SCRIPTS = {
    "boxscore_traditional_v3": "batch_cache_boxscores",
    "player_career_stats": "batch_cache_career_stats",
    "league_season_matchups": "batch_cache_all_matchups",
    "player_estimated_metrics": "batch_cache_player_estimated_metrics",
    "common_team_roster": "batch_cache_rosters",
    "league_hustle_stats_team": "batch_cache_hustle_stats",
    "league_dash_team_stats_advanced": None,
    "synergy_play_types": None,
    "player_game_log": None,
}


def main():
    print("Checking all endpoints against their known-good contracts...\n")
    results = runner.check_all()

    print(f"{'ENDPOINT':<35} {'STATUS':<8} REASON")
    for key, result in results.items():
        status = "PASS" if result.passed else "FAIL"
        print(f"{key:<35} {status:<8} {result.reason}")

    passed_keys = [key for key, result in results.items() if result.passed]
    failed_keys = [key for key, result in results.items() if not result.passed]

    if failed_keys:
        print(
            f"\n{len(failed_keys)} endpoint(s) failed validation -- skipping their batch "
            f"refresh entirely. Existing data_cache/ files for these are untouched:"
        )
        for key in failed_keys:
            script = BATCH_SCRIPTS.get(key)
            note = f" (would have run {script}.py)" if script else " (no dedicated batch script)"
            print(f"  - {key}: {results[key].reason}{note}")
        print("See logs/watchdog_alerts.jsonl for the full record.")

    runnable_passed = [k for k in passed_keys if BATCH_SCRIPTS.get(k)]
    print(f"\nRunning batch refresh for {len(runnable_passed)} passed endpoint(s) "
          f"with a dedicated batch script...\n")

    for key in runnable_passed:
        script_name = BATCH_SCRIPTS[key]
        print(f"=== Running {script_name}.py (endpoint: {key}) ===")
        module = __import__(script_name)
        module.main()
        print()

    no_script_passed = [k for k in passed_keys if BATCH_SCRIPTS.get(k) is None]
    if no_script_passed:
        print(
            f"Note: {', '.join(no_script_passed)} passed validation but have no dedicated "
            f"batch script -- run refresh_cache.py separately to actually refresh them."
        )

    print(
        "\nDone. Now commit and push the data_cache/ folder to GitHub so the deployed "
        "app picks it up."
    )


if __name__ == "__main__":
    main()
