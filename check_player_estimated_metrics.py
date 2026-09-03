"""
Diagnostic only -- does not touch app.py.

Prints the real columns (and one sample row) returned by
PlayerEstimatedMetrics, so the next patch is built against confirmed
field names instead of guessed ones.

Usage:
    python3 check_player_estimated_metrics.py
Run from anywhere with nba_api installed (same environment as app.py).
"""

from nba_api.stats.endpoints import playerestimatedmetrics

CURRENT_SEASON = "2026-27"  # matches the constant already in app.py

def main():
    print(f"Requesting PlayerEstimatedMetrics for season={CURRENT_SEASON} ...")
    try:
        data = playerestimatedmetrics.PlayerEstimatedMetrics(
            season=CURRENT_SEASON, timeout=10
        )
        df = data.get_data_frames()[0]
    except Exception as e:
        print(f"FAILED with season={CURRENT_SEASON}: {type(e).__name__}: {e}")
        print("Retrying with last season (2025-26) in case 2026-27 has no games yet...")
        try:
            data = playerestimatedmetrics.PlayerEstimatedMetrics(
                season="2025-26", timeout=10
            )
            df = data.get_data_frames()[0]
        except Exception as e2:
            print(f"FAILED again: {type(e2).__name__}: {e2}")
            return

    print()
    print("Columns:")
    print(list(df.columns))
    print()
    print(f"Row count: {len(df)}")
    print()
    if not df.empty:
        print("Sample row (first player):")
        print(df.iloc[0])
        print()
        # Specifically check for PIE, since that's what we're after
        if "PIE" in df.columns:
            print("Confirmed: PIE column is present.")
        else:
            print("NOTE: no column literally named 'PIE' -- check the columns")
            print("printed above for whatever the actual impact-estimate field is called.")


if __name__ == "__main__":
    main()
