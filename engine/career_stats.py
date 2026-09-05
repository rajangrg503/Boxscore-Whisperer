"""Pure helpers over a PlayerCareerStats regular-season-totals DataFrame.

No network calls, no nba_api dependency, no Streamlit dependency -- these
take a DataFrame you already have and do arithmetic on it, which is what
makes them directly unit-testable (see tests/test_missing_player_adjustment.py)
without hitting the live API or mocking cached_or_live.
"""

import pandas as pd


def resolve_season_mpg(career_df, season):
    """Return (mpg, note) for a player's minutes-per-game in `season`,
    given their PlayerCareerStats regular-season-totals DataFrame.

    mpg is None when there's no usable minutes signal (0 GP that season --
    injured/suspended/two-way all year is a real, common case, not a
    hypothetical). Callers must check for None rather than using the
    value blindly.

    Confirmed bugs this fixes:
    - 0 GP caused a silent 0/0 -> NaN (numpy doesn't raise on this), which
      poisoned every other missing player's contribution to a running sum
      in the caller. Now explicitly detected and reported instead.
    - A player traded mid-season has one row per team stint plus a
      combined "TOT" row for that season; taking .values[0] on the
      unfiltered season rows silently grabbed whichever stint nba_api
      happened to list first. Now prefers the TOT row when multiple rows
      match the season.
    """
    season_rows = career_df[career_df["SEASON_ID"] == season]
    if season_rows.empty:
        season_rows = career_df.tail(1)
    elif len(season_rows) > 1:
        tot_rows = season_rows[season_rows.get("TEAM_ABBREVIATION") == "TOT"]
        season_rows = tot_rows if not tot_rows.empty else season_rows.tail(1)

    gp = season_rows["GP"].values[0]
    if pd.isna(gp) or gp == 0:
        return None, f"0 games played in {season} (injured/inactive all season)"

    mpg = season_rows["MIN"].values[0] / gp
    return mpg, None
