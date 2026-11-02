"""Roster injuries the game summary leaves off (2 Nov 2026)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import availability as av  # noqa: E402

INDEX = {"tyrese haliburton": ("Tyrese Haliburton", 1630169, "IND"),
         "andrew nembhard": ("Andrew Nembhard", 1629614, "IND"),
         "ivica zubac": ("Ivica Zubac", 1627826, "IND")}
SCOREBOARD = {"events": [{"id": "9", "date": "2026-11-01T22:00Z", "competitions": [{
    "competitors": [{"homeAway": "away", "team": {"abbreviation": "BKN", "id": "17"}},
                    {"homeAway": "home", "team": {"abbreviation": "IND", "id": "11"}}],
    "status": {"type": {"detail": "Scheduled"}}}]}]}
SUMMARY = {"injuries": [{"team": {"abbreviation": "IND"}, "injuries": [
    {"athlete": {"displayName": "Ivica Zubac"}, "status": "Out", "details": {"type": "Ankle"}}]}]}
ROSTER_IND = {"athletes": [
    {"displayName": "Tyrese Haliburton", "injuries": [{"status": "Out", "date": "2026-10-28T21:30Z"}]},
    {"displayName": "Andrew Nembhard", "injuries": [{"status": "Out", "date": "2026-10-20T21:30Z"}]},
    {"displayName": "Ivica Zubac", "injuries": [{"status": "Out", "date": "2026-10-30T21:30Z"}]},
    {"displayName": "Pascal Siakam", "injuries": []}]}


def fake_fetch(url):
    if "scoreboard" in url:
        return SCOREBOARD
    if "summary" in url:
        return SUMMARY
    if "/teams/11/" in url:
        return ROSTER_IND
    return {"athletes": []}


def fake_last(pid, season):
    import datetime
    return {1630169: datetime.date(2026, 10, 27),       # before his entry: still out
            1629614: datetime.date(2026, 10, 30)}.get(pid)  # played since: stale entry


def test_roster_injuries_fill_what_the_summary_misses(monkeypatch):
    monkeypatch.setattr(av, "fetch", fake_fetch)
    monkeypatch.setattr(av, "last_played", fake_last)
    games = av.collect("20261101", index=INDEX)
    out = sorted(r["name"] for r in games[0]["out"])
    assert out == ["Ivica Zubac", "Tyrese Haliburton"]          # Nembhard's entry is stale
    hali = next(r for r in games[0]["out"] if r["name"] == "Tyrese Haliburton")
    assert hali["source"] == "roster" and hali["player_id"] == 1630169
    assert sum(1 for r in games[0]["injuries"] if r["name"] == "Ivica Zubac") == 1


def test_season_of():
    assert av.season_of("20261101") == "2026-27"
    assert av.season_of("20270315") == "2026-27"
    assert av.season_of("20270115") == "2026-27"
