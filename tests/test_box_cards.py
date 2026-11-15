import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import box_cards as bc  # noqa: E402


def _p(name, mins, pts, reb=3, ast=2):
    return {"name": name, "min": mins, "PTS": pts, "REB": reb, "AST": ast}


SLATE = {"games": [
    {"away": "OKC", "home": "SAC", "tipoff": "2026-11-16T02:00Z",
     "players": {"OKC": [_p("Shai Gilgeous-Alexander", 32, 30), _p("Jared McCain", 27, 15), _p("Aday Mara", 23, 9)],
                 "SAC": [_p("Darius Acuff Jr.", 36, 23), _p("Zach LaVine", 28, 15)]},
     "out": {"OKC": ["Jalen Williams", "Jaylin Williams", "Chet Holmgren"], "SAC": ["Ben Simmons"]},
     "day_to_day": {"OKC": ["Ajay Mitchell (Day-To-Day)"], "SAC": []}},
    {"away": "DAL", "home": "BOS", "tipoff": "2026-11-15T23:00Z",
     "players": {"DAL": [_p("A", 36, 20), _p("B", 36, 20), _p("C", 36, 20), _p("D", 20, 10)], "BOS": [_p("E", 30, 25)]},
     "out": {"DAL": ["Dereck Lively II"], "BOS": []}, "day_to_day": {}},
    {"away": "IND", "home": "PHI", "tipoff": "2026-11-15T18:00Z",
     "players": {"IND": [_p("F", 30, 20)], "PHI": [_p("G", 30, 20)]}, "out": {}, "day_to_day": {}},
]}
SCORES = {("OKC", "SAC"): (111, 101), ("DAL", "BOS"): (106, 112), ("IND", "PHI"): (107, 110)}


def test_names():
    assert bc.last_name("Jaren Jackson Jr.") == "Jackson Jr."
    assert bc.last_name("Dereck Lively II") == "Lively II"
    assert bc.last_name("Kevin McCullar Jr. (Day-To-Day)") == "McCullar Jr."
    assert bc.short_name("Shai Gilgeous-Alexander") == "S. Gilgeous-Alexander"
    assert bc.short_name("Jared McCain") == "Jared McCain"
    assert bc.name_list(["Jalen Williams", "Jaylin Williams", "Chet Holmgren"]) == ["Jalen Williams", "Jaylin Williams", "Holmgren"]


def test_points_scale_to_the_team_score_and_times_are_aedt():
    games = bc.build("2026-11-15", slate=SLATE, scores=SCORES)
    okc = [g for g in games if g["away"] == "OKC"][0]
    assert abs(sum(r[2] for r in okc["teams"]["OKC"]["rows"]) - 111) <= 1
    assert okc["tip"] == "1:00pm" and okc["day"] == "Mon 16 Nov"
    assert okc["teams"]["OKC"]["questionable"] == ["Mitchell"]
    assert okc["teams"]["SAC"]["out"] == ["Simmons"]


def test_capped_minutes_are_flagged():
    games = bc.build("2026-11-15", slate=SLATE, scores=SCORES)
    assert [g for g in games if g["away"] == "DAL"][0]["teams"]["DAL"]["capped"] is True
    assert [g for g in games if g["away"] == "OKC"][0]["teams"]["OKC"]["capped"] is False


def test_page_has_one_section_per_two_games_and_a_summary_for_odd_counts():
    games = bc.build("2026-11-15", slate=SLATE, scores=SCORES)
    html = bc.page(games)
    assert html.count("<section") == 2
    assert "SLATE SUMMARY" in html and "Late calls:</b> Mitchell" in html
    assert "minutes capped by the model" in html


def test_post_text_fits_on_a_nine_game_slate():
    games = bc.build("2026-11-15", slate=SLATE, scores=SCORES) * 3
    assert len(bc.post_text(games)) <= 280
