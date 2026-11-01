"""refresh_current_season --recent: refetch only the players whose team
played and whose cached log is missing that game."""
import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import availability as av  # noqa: E402
import refresh_current_season as rcs  # noqa: E402

TODAY = datetime.date(2026, 11, 1)


def test_only_players_missing_their_teams_last_game(monkeypatch):
    slates = {"20261031": [{"away": "ATL", "home": "NOP", "status": "Final"},
                           {"away": "OKC", "home": "HOU", "status": "8:30 PM EDT"}],
              "20261030": [{"away": "BOS", "home": "CHI", "status": "Final/OT"}]}
    monkeypatch.setattr(av, "slate", lambda date: slates.get(date, []))
    logs = {
        "gamelog_1_2026-27": [{"GAME_DATE": "Oct 31, 2026"}],      # ATL, has it
        "gamelog_2_2026-27": [{"GAME_DATE": "Oct 29, 2026"}],      # NOP, missing 31st
        "gamelog_4_2026-27": [{"GAME_DATE": "Oct 28, 2026"}],      # BOS, missing 30th
    }
    monkeypatch.setattr(rcs, "read_payload", lambda key: {"data": logs.get(key, [])})
    players = {1: ("Jalen Johnson", "ATL"), 2: ("Saddiq Bey", "NOP"),
               3: ("Ajay Mitchell", "OKC"), 4: ("Payton Pritchard", "BOS"),
               5: ("Nobody", "MIA")}
    todo, played = rcs.recent_todo(2, players, "2026-27", today=TODAY)
    assert sorted(name for _, name, _ in todo) == ["Payton Pritchard", "Saddiq Bey"]
    assert "OKC" not in played                       # not final yet: nothing to fetch
    assert played["BOS"] == datetime.date(2026, 10, 30)
