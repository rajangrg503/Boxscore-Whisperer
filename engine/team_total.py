"""Expected team total for Full Matchup.

WHY THIS EXISTS
Each row of the Full Matchup table is a player's line IF HE PLAYS (his
per-game average, adjusted). Summing those rows over a whole roster
treats every rostered player as certain to play his usual minutes, so
the old "Team total" row added up to roughly 320 player-minutes a game
instead of 240, and ran about 35 points high.

minutes_model_sweep.py (point in time, 6,120 team-games over three
seasons, roster proxy from cached box scores) compared:
  * sum of the rows (the old total):          PTS bias +35.4, MAE 35.6
  * weighted by how often each player plays:  PTS bias  +1.2, MAE 11.0
  * ... and scaled so the minutes add to 240: PTS bias  -0.7, MAE 10.0
With the user marking injured players out, the old total's MAE was
17.9 and the scaled version's 10.6. The scaled version, using the
availability the live app can compute (below), is what this module does.

HOW
For each projected player i:
  p_i   = availability(his regular-season games, his team's games),
          shrunk toward AVAILABILITY_PRIOR by AVAILABILITY_PSEUDO_GAMES.
  mpg_i = his average minutes in the games behind his line.
  line_i = his per-game average x the opponent-defense multiplier (the
          out-redistribution pickup is left out: the minutes scaling
          already hands an absent player's minutes to everyone else,
          which is what the backtest measured).
expected total = sum(p_i * line_i) * 240 / sum(p_i * mpg_i)

The rows themselves are unchanged; the total is labelled as expected and
isn't their sum.
"""

TEAM_MINUTES = 240.0
AVAILABILITY_PRIOR = 0.7
AVAILABILITY_PSEUDO_GAMES = 5.0
REGULAR_SEASON_GAMES = 82


def availability(player_games, team_games):
    """Share of his team's games a player can be expected to play.
    player_games is capped at team_games (a traded player can have
    played more games than his new team)."""
    team_games = max(int(team_games or 0), 0)
    player_games = min(max(int(player_games or 0), 0), team_games)
    return (player_games + AVAILABILITY_PSEUDO_GAMES * AVAILABILITY_PRIOR) / (
        team_games + AVAILABILITY_PSEUDO_GAMES
    )


def minutes_profile(gamelog_df):
    """(mpg over every game in the log, number of regular-season games)
    for the gamelog behind a player's line. Regular-season games are
    the ones whose Game_ID starts with 0022 (the log also holds playoff
    games). Returns (None, 0) when minutes aren't available."""
    if gamelog_df is None or gamelog_df.empty or "MIN" not in gamelog_df.columns:
        return None, 0
    minutes = gamelog_df["MIN"].map(_to_minutes)
    minutes = minutes[minutes.notna()]
    if minutes.empty:
        return None, 0
    if "Game_ID" in gamelog_df.columns:
        regular = int(gamelog_df["Game_ID"].astype(str).str.startswith("0022").sum())
    else:
        regular = len(gamelog_df)
    return float(minutes.mean()), regular


def _to_minutes(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value)
    if ":" in text:
        mm, _, ss = text.partition(":")
        try:
            return float(mm) + float(ss or 0) / 60
        except ValueError:
            return None
    try:
        return float(text)
    except ValueError:
        return None


def expected_team_total(entries, stat_cols):
    """entries: list of dicts {"line": {col: value}, "mpg": float,
    "availability": float}. Returns ({col: total}, expected_minutes)
    or (None, 0.0) when there is nothing to scale."""
    usable = [e for e in entries if e.get("mpg") and e["mpg"] > 0 and e.get("availability", 0) > 0]
    expected_minutes = sum(e["availability"] * e["mpg"] for e in usable)
    if expected_minutes <= 0:
        return None, 0.0
    scale = TEAM_MINUTES / expected_minutes
    totals = {
        col: scale * sum(e["availability"] * e["line"][col] for e in usable)
        for col in stat_cols
    }
    return totals, expected_minutes
