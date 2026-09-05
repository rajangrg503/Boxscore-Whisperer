"""Endpoint contracts -- the single source of truth for what a
"known-good" response from each nba_api endpoint looks like.

Used by data_watchdog/ (which validates a live sample query against
these before a batch script is allowed to touch data_cache/) so the
contract and the check can never silently drift apart from each other.

Each EndpointContract's sample_query is a single, cheap, KNOWN-GOOD
live call -- a real, long-retired-career player, a real closed game, a
real team -- not a guess. If nba_api changes response shape (renamed
columns, or -- as happened with BoxScoreTraditionalV2 this week --
silently stops returning any rows at all), the mismatch shows up here,
against a query that has no legitimate reason to ever come back empty.
"""

from dataclasses import dataclass
from typing import Callable, Optional, Set

import pandas as pd

# Real, stable identifiers used as "this can never legitimately be
# empty" known-good sample queries. Picked for long careers / full
# rosters so they stay valid for years without upkeep.
KNOWN_GOOD_GAME_ID = "0022500001"    # a real, completed 2025-26 game
KNOWN_GOOD_PLAYER_ID = 2544          # LeBron James
KNOWN_GOOD_TEAM_ID = 1610612747      # Los Angeles Lakers
KNOWN_GOOD_SEASON = "2025-26"


@dataclass
class EndpointContract:
    key: str
    required_columns: Set[str]
    min_rows: int
    sample_query: Callable[[], pd.DataFrame]
    description: str = ""


def _boxscore_traditional_v3_sample():
    from nba_api.stats.endpoints import boxscoretraditionalv3
    box = boxscoretraditionalv3.BoxScoreTraditionalV3(game_id=KNOWN_GOOD_GAME_ID, timeout=10)
    return box.get_data_frames()[0]


def _player_career_stats_sample():
    from nba_api.stats.endpoints import playercareerstats
    career = playercareerstats.PlayerCareerStats(player_id=KNOWN_GOOD_PLAYER_ID, timeout=10)
    return career.get_data_frames()[0]


def _league_season_matchups_sample():
    from nba_api.stats.endpoints import leagueseasonmatchups
    data = leagueseasonmatchups.LeagueSeasonMatchups(
        def_player_id_nullable=KNOWN_GOOD_PLAYER_ID, season=KNOWN_GOOD_SEASON, timeout=10
    )
    return data.get_data_frames()[0]


def _player_estimated_metrics_sample():
    from nba_api.stats.endpoints import playerestimatedmetrics
    metrics = playerestimatedmetrics.PlayerEstimatedMetrics(season=KNOWN_GOOD_SEASON, timeout=10)
    return metrics.get_data_frames()[0]


def _common_team_roster_sample():
    from nba_api.stats.endpoints import commonteamroster
    roster = commonteamroster.CommonTeamRoster(
        team_id=KNOWN_GOOD_TEAM_ID, season=KNOWN_GOOD_SEASON, timeout=10
    )
    return roster.get_data_frames()[0]


def _league_hustle_stats_team_sample():
    from nba_api.stats.endpoints import leaguehustlestatsteam
    hustle = leaguehustlestatsteam.LeagueHustleStatsTeam(
        season=KNOWN_GOOD_SEASON, per_mode_time="PerGame", timeout=10
    )
    return hustle.get_data_frames()[0]


def _league_dash_team_stats_advanced_sample():
    from nba_api.stats.endpoints import leaguedashteamstats
    stats = leaguedashteamstats.LeagueDashTeamStats(
        season=KNOWN_GOOD_SEASON, measure_type_detailed_defense="Advanced", timeout=10
    )
    return stats.get_data_frames()[0]


def _synergy_play_types_sample():
    from nba_api.stats.endpoints import synergyplaytypes
    data = synergyplaytypes.SynergyPlayTypes(
        league_id="00", per_mode_simple="PerGame", player_or_team_abbreviation="T",
        season_type_all_star="Regular Season", season=KNOWN_GOOD_SEASON,
        type_grouping_nullable="defensive", play_type_nullable="Isolation", timeout=10,
    )
    return data.get_data_frames()[0]


def _player_game_log_sample():
    from nba_api.stats.endpoints import playergamelog
    log = playergamelog.PlayerGameLog(
        player_id=KNOWN_GOOD_PLAYER_ID, season=KNOWN_GOOD_SEASON,
        season_type_all_star="Regular Season", timeout=10,
    )
    return log.get_data_frames()[0]


ENDPOINT_CONTRACTS = {
    "boxscore_traditional_v3": EndpointContract(
        key="boxscore_traditional_v3",
        required_columns={"gameId", "personId", "firstName", "familyName", "points"},
        min_rows=10,
        sample_query=_boxscore_traditional_v3_sample,
        description=(
            "Per-game player boxscore (BoxScoreTraditionalV3) -- used by "
            "batch_cache_boxscores.py and get_teammate_availability_adjustment()."
        ),
    ),
    "player_career_stats": EndpointContract(
        key="player_career_stats",
        required_columns={"SEASON_ID", "TEAM_ABBREVIATION", "GP", "MIN"},
        min_rows=1,
        sample_query=_player_career_stats_sample,
        description=(
            "Per-player career totals (PlayerCareerStats) -- used by "
            "batch_cache_career_stats.py and get_opponent_missing_adjustment()."
        ),
    ),
    "league_season_matchups": EndpointContract(
        key="league_season_matchups",
        required_columns={"OFF_PLAYER_ID"},
        min_rows=1,
        sample_query=_league_season_matchups_sample,
        description=(
            "Per-defender matchup data (LeagueSeasonMatchups) -- used by "
            "batch_cache_all_matchups.py and get_defender_matchup_adjustment()."
        ),
    ),
    "player_estimated_metrics": EndpointContract(
        key="player_estimated_metrics",
        required_columns={"PLAYER_ID", "E_NET_RATING"},
        min_rows=50,
        sample_query=_player_estimated_metrics_sample,
        description=(
            "League-wide estimated net ratings (PlayerEstimatedMetrics) -- used by "
            "batch_cache_player_estimated_metrics.py and get_opponent_missing_adjustment()."
        ),
    ),
    "common_team_roster": EndpointContract(
        key="common_team_roster",
        required_columns={"PLAYER_ID", "PLAYER"},
        min_rows=5,
        sample_query=_common_team_roster_sample,
        description=(
            "Current team roster (CommonTeamRoster) -- used by batch_cache_rosters.py "
            "and get_team_roster()."
        ),
    ),
    "league_hustle_stats_team": EndpointContract(
        key="league_hustle_stats_team",
        required_columns={"TEAM_ID", "DEFLECTIONS"},
        min_rows=20,
        sample_query=_league_hustle_stats_team_sample,
        description=(
            "League-wide hustle stats (LeagueHustleStatsTeam) -- used by "
            "batch_cache_hustle_stats.py and get_synergy_scheme_adjustment()."
        ),
    ),
    "league_dash_team_stats_advanced": EndpointContract(
        key="league_dash_team_stats_advanced",
        required_columns={"TEAM_ID", "DEF_RATING", "PACE", "GP"},
        min_rows=20,
        sample_query=_league_dash_team_stats_advanced_sample,
        description=(
            "League-wide advanced team stats (LeagueDashTeamStats) -- used by "
            "get_league_advanced_team_stats() / refresh_cache.py's refresh_team_stats(). "
            "No dedicated batch script -- refreshed directly by refresh_cache.py."
        ),
    ),
    "synergy_play_types": EndpointContract(
        key="synergy_play_types",
        required_columns={"TEAM_ID", "PPP"},
        min_rows=10,
        sample_query=_synergy_play_types_sample,
        description=(
            "Team defensive play-type efficiency (SynergyPlayTypes) -- used by "
            "get_synergy_scheme_adjustment() / refresh_cache.py's "
            "refresh_synergy_scheme_data(). No dedicated batch script."
        ),
    ),
    "player_game_log": EndpointContract(
        key="player_game_log",
        required_columns={"Game_ID", "MATCHUP", "GAME_DATE", "PTS"},
        min_rows=1,
        sample_query=_player_game_log_sample,
        description=(
            "Per-player game log (PlayerGameLog) -- used by fetch_combined_game_log() / "
            "refresh_cache.py's refresh_player_game_logs(). No dedicated batch script."
        ),
    ),
}
