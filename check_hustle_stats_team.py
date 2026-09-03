"""
Diagnostic only -- does not touch app.py.

Prints the real columns (and OKC's row, as a sanity check) returned by
LeagueHustleStatsTeam, so we can confirm the exact field name for team
deflections before building the Man-to-man scheme upgrade against it.

Usage:
    python3 check_hustle_stats_team.py
Run from anywhere with nba_api installed (same environment as app.py).
"""

from nba_api.stats.endpoints import leaguehustlestatsteam

CURRENT_SEASON = "2026-27"
OKC_TEAM_ID = 1610612760

def try_season(season):
    print(f"Requesting LeagueHustleStatsTeam for season={season} ...")
    try:
        data = leaguehustlestatsteam.LeagueHustleStatsTeam(
            season=season, per_mode_time="PerGame", timeout=10
        )
        df = data.get_data_frames()[0]
    except Exception as e:
        print(f"FAILED with season={season}: {type(e).__name__}: {e}")
        return None
    return df


def main():
    df = try_season(CURRENT_SEASON)
    if df is None or df.empty:
        print(f"No data for {CURRENT_SEASON} (likely preseason gap) -- trying 2025-26 ...")
        df = try_season("2025-26")

    if df is None or df.empty:
        print("Still no data. Stopping.")
        return

    print()
    print("Columns:")
    print(list(df.columns))
    print()
    print(f"Row count: {len(df)}")
    print()

    if "TEAM_ID" in df.columns:
        okc_row = df[df["TEAM_ID"] == OKC_TEAM_ID]
        if not okc_row.empty:
            print("OKC row (sanity check):")
            print(okc_row.iloc[0])
        else:
            print("Could not find OKC's row by TEAM_ID -- check the TEAM_ID column values.")


if __name__ == "__main__":
    main()
