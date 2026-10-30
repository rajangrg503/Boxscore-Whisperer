"""tools/daily_journal.py on a hand-built game, with the API, the cache
and the model all faked. What's pinned here is the bookkeeping the
written reason depends on: who won, which gaps count as edges and
which way they cut, who is listed as missing, what counts as a
projection miss, and that a reason with an invented number never
reaches the page."""

import datetime
import json
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import daily_journal as dj  # noqa: E402

DAY = datetime.date(2026, 10, 21)


def _player(pid, team, first, last, minutes, pts, reb, ast, *, fgm=0, fga=0,
            fg3m=0, fg3a=0, fta=0, oreb=0, tov=0, comment=""):
    return {
        "gameId": "0022600001", "teamTricode": team, "personId": pid,
        "firstName": first, "familyName": last, "comment": comment,
        "minutes": minutes, "points": pts, "reboundsTotal": reb, "assists": ast,
        "fieldGoalsMade": fgm, "fieldGoalsAttempted": fga,
        "threePointersMade": fg3m, "threePointersAttempted": fg3a,
        "freeThrowsAttempted": fta, "reboundsOffensive": oreb, "turnovers": tov,
    }


BOX = pd.DataFrame([
    # BOS: hot from three, careful with the ball
    _player(1, "BOS", "Al", "Star", "36:00", 38, 6, 5, fgm=13, fga=24, fg3m=8, fg3a=14, fta=6, oreb=1, tov=2),
    _player(2, "BOS", "Bo", "Wing", "30:00", 20, 4, 3, fgm=8, fga=16, fg3m=6, fg3a=10, fta=2, oreb=1, tov=2),
    _player(3, "BOS", "Cy", "Out", "", 0, 0, 0, comment="DNP - Injury/Illness"),
    # NYK: cold from three, more turnovers, more offensive boards
    _player(4, "NYK", "Di", "Guard", "35:00", 25, 3, 8, fgm=10, fga=22, fg3m=2, fg3a=9, fta=6, oreb=4, tov=6),
    _player(5, "NYK", "Ed", "Big", "33:00", 18, 14, 2, fgm=8, fga=12, fg3m=0, fg3a=1, fta=4, oreb=6, tov=4),
    _player(6, "NYK", "Fi", "Bench", "4:00", 0, 0, 0, fga=1),
])

PROJ = {
    1: {"PTS": (18.0, 24.0, 30.0), "REB": (3.0, 5.0, 8.0), "AST": (2.0, 4.0, 6.0)},   # 38 pts: big miss
    2: {"PTS": (10.0, 15.0, 21.0), "REB": (2.0, 4.0, 6.0), "AST": (1.0, 2.0, 4.0)},
    3: {"PTS": (12.0, 16.0, 22.0)},                                                    # notable, out
    4: {"PTS": (18.0, 24.0, 31.0), "REB": (2.0, 4.0, 6.0), "AST": (4.0, 6.0, 8.0)},
    5: {"PTS": (10.0, 14.0, 19.0), "REB": (6.0, 9.0, 12.0), "AST": (1.0, 2.0, 3.0)},   # 14 reb: 2 over
    6: {"PTS": (0.5, 2.0, 4.0)},                                                       # 0 pts: under MIN_MISS
}


# NYK led at the half; BOS won the third 25-8 and pulled away.
SUMMARY = {
    "line": pd.DataFrame([
        {"teamTricode": "BOS", "period1Score": 10, "period2Score": 12, "period3Score": 25, "period4Score": 11, "score": 58},
        {"teamTricode": "NYK", "period1Score": 14, "period2Score": 13, "period3Score": 8, "period4Score": 8, "score": 43},
    ]),
    "other": pd.DataFrame([
        {"teamTricode": "BOS", "pointsInThePaint": 20, "pointsFastBreak": 12, "pointsSecondChance": 2,
         "pointsFromTurnovers": 9, "benchPoints": 0, "biggestLead": 17, "biggestScoringRun": 15,
         "leadChanges": 5, "timesTied": 2},
        {"teamTricode": "NYK", "pointsInThePaint": 34, "pointsFastBreak": 2, "pointsSecondChance": 6,
         "pointsFromTurnovers": 4, "benchPoints": 0, "biggestLead": 9, "biggestScoringRun": 8,
         "leadChanges": 5, "timesTied": 2},
    ]),
}


@pytest.fixture(autouse=True)
def fake_world(monkeypatch):
    monkeypatch.setattr(dj, "list_games", lambda day: ({"0022600001": {"home": "NYK", "away": "BOS"}}, "test"))
    monkeypatch.setattr(dj, "load_boxscore", lambda gid: BOX.copy())
    monkeypatch.setattr(dj, "load_summary", lambda gid: {k: v.copy() for k, v in SUMMARY.items()})
    monkeypatch.setattr(dj, "pregame_projection", lambda pid, day: PROJ.get(pid))


def _game():
    return dj.analyse_game("0022600001", "NYK", "BOS", DAY, project=lambda pid, d: PROJ.get(pid))


def test_home_away_from_matchup_strings():
    games = dj._home_away([("1", "BOS @ NYK"), ("1", "NYK vs. BOS"), ("2", "junk")])
    assert games == {"1": {"home": "NYK", "away": "BOS"}}


def test_winner_edges_and_which_way_they_cut():
    g = _game()
    assert (g["winner"], g["loser"], g["margin"]) == ("BOS", "NYK", 15)
    edges = " | ".join(g["edges"])
    assert "BOS made 14 threes to NYK's 2" in edges
    assert "NYK turned it over 10 times to BOS's 4" in edges
    # NYK out-rebounded BOS on the offensive glass and still lost
    assert "NYK grabbed 10 offensive rebounds to BOS's 2 (BOS won despite it)" in edges
    assert g["edges"][0].startswith("BOS made 14 threes")  # biggest gap first


def test_absences_only_list_notable_players():
    g = _game()
    assert g["absent"] == [{"name": "Cy Out", "team": "BOS", "why": "DNP - Injury/Illness"}]


def test_projection_misses_need_a_real_gap():
    g = _game()
    misses = {(s["name"], s["label"]): s for s in g["surprises"]}
    assert ("Al Star", "points") in misses and misses[("Al Star", "points")]["direction"] == "above"
    assert ("Ed Big", "rebounds") in misses
    # 0 points against 0.5-4 is under MIN_MISS -- not worth a line
    assert not any(name == "Fi Bench" for name, _ in misses)
    assert g["surprises"][0]["name"] == "Al Star"  # biggest first


def test_facts_contain_every_number_a_reason_may_use():
    facts = dj.game_facts(_game())
    text = " ".join(facts)
    assert "Final: BOS 58, NYK 43, played at NYK. BOS won by 15." in text
    assert "Al Star had 38 points in 36 minutes, above the projected range of 18 to 30" in text
    ok = "BOS made 14 threes to NYK's 2 and won by 15; Al Star had 38 points."
    assert dj.whisper.check_note(ok, facts, dj.REASON_MAX_WORDS) is None
    assert "not in the facts" in dj.whisper.check_note(
        "BOS won by 15 on a 27-0 run.", facts, dj.REASON_MAX_WORDS)


def test_journal_day_writes_page_with_checked_reason(fake_world, tmp_path):
    def fake(facts, temperature):
        return "BOS trailed 27–22 at halftime and won by 15. BOS made 14 threes to NYK's 2, and Al Star scored 38 points."

    path = dj.journal_day(DAY, generate=fake, out_dir=str(tmp_path), log=lambda *a: None)
    page = open(path).read()
    assert page.startswith("# NBA journal — Wed 21 Oct 2026 (US date)")
    assert "## BOS 58 – 43 NYK  ·  at NYK" in page
    assert "**Why:** BOS trailed 27–22 at halftime and won by 15. BOS made 14 threes to NYK's 2, and Al Star scored 38 points.\n" in page
    assert "**Didn't play:** Cy Out (BOS) — DNP - Injury/Illness" in page
    assert "| BOS | 52.5% | 14/24 |" in page
    assert dj.INCOMPLETE_MARKER not in page


def test_rejected_reason_leaves_the_facts_in_place(fake_world, tmp_path):
    path = dj.journal_day(DAY, generate=lambda f, t: "Lock of the night, they won by 30.",
                          out_dir=str(tmp_path), log=lambda *a: None)
    page = open(path).read()
    assert "Lock of the night" not in page and "by 30" not in page
    assert "**Why:** BOS trailed 27–22 at halftime and won by 15." in page
    assert "_(auto)_" in page


def test_finished_night_is_not_redone_but_incomplete_one_is(fake_world, tmp_path, monkeypatch):
    calls = []
    log = calls.append
    dj.journal_day(DAY, out_dir=str(tmp_path), log=log)
    dj.journal_day(DAY, out_dir=str(tmp_path), log=log)
    assert "already written" in calls[-1]

    monkeypatch.setattr(dj, "load_boxscore", lambda gid: None)
    other = DAY + datetime.timedelta(days=1)
    path = dj.journal_day(other, out_dir=str(tmp_path), log=log)
    assert dj.INCOMPLETE_MARKER in open(path).read()
    monkeypatch.setattr(dj, "load_boxscore", lambda gid: BOX.copy())
    dj.journal_day(other, out_dir=str(tmp_path), log=log)
    assert dj.INCOMPLETE_MARKER not in open(path).read()


def test_us_yesterday_uses_eastern_time():
    from zoneinfo import ZoneInfo
    # Saturday 6:36am in Sydney is Friday afternoon in New York
    sydney = datetime.datetime(2026, 10, 31, 6, 36, tzinfo=ZoneInfo("Australia/Sydney"))
    eastern = sydney.astimezone(ZoneInfo("America/New_York"))
    assert dj.us_yesterday(eastern) == datetime.date(2026, 10, 29)


def test_team_codes_come_from_the_box_score_not_the_game_list():
    g = dj.analyse_game("0022600001", "LAL", "GSW", DAY, project=lambda pid, d: PROJ.get(pid))
    assert (g["winner"], g["tw"]["pts"], g["tl"]["pts"]) == ("BOS", 58, 43)
    assert g["home"] is None
    assert "played at" not in dj.game_facts(g)[0]


def test_game_flow_facts_tell_the_story():
    facts = " | ".join(dj.flow_facts(_game()))
    assert "NYK led 27–22 at halftime; BOS came back to win." in facts
    assert "BOS won the third quarter 25–8." in facts
    assert "BOS's biggest lead was 17." in facts
    assert "The lead changed hands 5 times." in facts
    assert "BOS had a 15-point run." in facts
    assert "overtime" not in facts


def test_summary_stats_become_edges_both_ways(monkeypatch):
    assert len(_game()["edges"]) == dj.MAX_EDGES   # capped, biggest first
    monkeypatch.setattr(dj, "MAX_EDGES", 10)
    edges = " | ".join(_game()["edges"])
    assert "BOS scored 12 fast-break points to NYK's 2" in edges
    assert "NYK scored 34 points in the paint to BOS's 20 (BOS won despite it)" in edges


def test_fallback_reason_uses_only_the_facts():
    g = _game()
    text = dj.fallback_reason(g)
    assert text.startswith("BOS trailed 27–22 at halftime and won by 15.")
    assert "Al Star led BOS with 38 points." in text
    assert dj.whisper.check_note(text, dj.game_facts(g), dj.REASON_MAX_WORDS) is None


def test_overtime_is_noticed():
    ot = {k: v.copy() for k, v in SUMMARY.items()}
    ot["line"].loc[0, "score"] = 64   # 6 more than the four quarters
    ot["line"].loc[1, "score"] = 49
    flow = dj.game_flow(ot, "BOS", "NYK")
    assert flow["overtime"] and (flow["otw"], flow["otl"]) == (6, 6)


def test_no_summary_still_gives_a_why_and_retries_later(monkeypatch, tmp_path):
    monkeypatch.setattr(dj, "load_summary", lambda gid: None)
    path = dj.journal_day(DAY, out_dir=str(tmp_path), log=lambda *a: None)
    page = open(path).read()
    assert ("**Why:** BOS won by 15; BOS made 14 threes to NYK's 2. "
            "NYK turned it over 10 times to BOS's 4. Al Star led BOS with 38 points. _(auto)_") in page
    assert dj.INCOMPLETE_MARKER in page and "Quarter scores for 1 game(s)" in page
    assert "| Q1 |" not in page


def test_page_shows_quarters_and_model_reason_untagged(tmp_path):
    path = dj.journal_day(DAY, generate=lambda f, t: "BOS trailed 27–22 at halftime and won by 15, then won the third quarter 25–8.",
                          out_dir=str(tmp_path), log=lambda *a: None)
    page = open(path).read()
    assert "**Why:** BOS trailed 27–22 at halftime and won by 15, then won the third quarter 25–8.\n" in page
    assert "| BOS | 10 | 12 | 25 | 11 | **58** |" in page
    assert "| Team | FG% | 3PT | FTA | OREB | AST | TOV | Paint | Fast break | 2nd chance | Bench |" in page


def test_fallback_skips_edges_that_favoured_the_loser():
    g = _game()
    g["edges"] = ["NYK grabbed 10 offensive rebounds to BOS's 2 (BOS won despite it)",
                  "BOS made 14 threes to NYK's 2"]
    text = dj.fallback_reason(g)
    assert "offensive rebounds" not in text and "BOS made 14 threes to NYK's 2." in text


def _with_flow(**changes):
    g = _game()
    flow = dict(g["flow"])
    flow.update(changes.pop("flow", {}))
    g["flow"] = flow
    for team, values in changes.items():
        g[team] = {**g[team], **values}
    return g


def test_main_story_priority():
    # A 10+ point comeback beats everything.
    assert dj.main_story(_with_flow(tl={"biggest_lead": 15})) == "BOS came back from 15 down to win by 15."
    # Then a halftime deficit (the fixture: NYK led 27-22, biggest lead 9).
    assert dj.main_story(_game()) == "BOS trailed 27–22 at halftime and won by 15."
    # Then a big quarter: "pulled away" only late in a game close at the half.
    late = _with_flow(flow={"half": (40, 38), "decisive": ("fourth", 33, 20)})
    assert dj.main_story(late) == "BOS pulled away in the fourth quarter, winning it 33–20, and won by 15."
    early = _with_flow(flow={"half": (60, 38), "decisive": ("first", 35, 20)})
    assert dj.main_story(early) == "BOS won the first quarter 35–20 and won by 15."
    # Then the biggest edge that helped the winner.
    small = _with_flow(flow={"half": (40, 38), "decisive": ("second", 30, 22)})
    assert dj.main_story(small) == "BOS won by 15; BOS made 14 threes to NYK's 2."


def test_reason_check_rules():
    g = _game()
    check = dj.reason_check(g, dj.game_facts(g))
    good = "BOS trailed 27–22 at halftime and won by 15. BOS made 14 threes to NYK's 2."
    assert check(good) is None
    assert "left out the main story" in check("BOS made 14 threes to NYK's 2 and won by 15.")
    assert "overclaimed" in check(good + " The threes were decisive.")
    assert "team name 'Celtics'" in check(good + " The Celtics shot well.")
    assert "repeated Al Star" in check(good + " Al Star scored 38. Al Star also had 5 assists.")


def test_fallback_names_tied_top_scorers():
    g = _game()
    g["top"] = [{**g["top"][0], "pts": 20}] + g["top"][1:]   # Al Star now ties Bo Wing on 20
    assert "Al Star and Bo Wing led BOS with 20 points each." in dj.fallback_reason(g)


def test_lead_changed_hands_once():
    assert "The lead changed hands once." in dj.flow_facts(_with_flow(tw={"lead_changes": 1}))


def test_prompt_puts_main_story_first():
    assert dj.game_facts(_game())[0] == "Main story: BOS trailed 27–22 at halftime and won by 15."


def test_long_model_line_keeps_its_main_story(tmp_path):
    long_line = ("BOS trailed 27–22 at halftime and won by 15. BOS made 14 threes to NYK's 2. "
                 + "Al Star scored 38 points. " + "NYK turned it over 10 times to BOS's 4. " * 6)
    path = dj.journal_day(DAY, generate=lambda f, t: long_line, out_dir=str(tmp_path), log=lambda *a: None)
    page = open(path).read()
    why = next(line for line in page.splitlines() if line.startswith("**Why:**"))
    assert why.startswith("**Why:** BOS trailed 27–22 at halftime and won by 15.")
    assert "_(auto)_" not in why
    assert len(why.split()) - 1 <= dj.REASON_MAX_WORDS


# ---- every line, kept Llama lines, Llama not running ------------------

def test_every_projection_line_is_recorded():
    g = _game()
    assert g["checked"] == len(g["lines"])
    star = next(l for l in g["lines"] if l["name"] == "Al Star" and l["stat"] == "PTS")
    assert (star["low"], star["high"], star["actual"], star["min"]) == (18.0, 30.0, 38.0, 36.0)


def test_rewrite_without_llama_keeps_its_lines(tmp_path):
    line = "BOS trailed 27–22 at halftime and won by 15. BOS made 14 threes to NYK's 2."
    dj.journal_day(DAY, generate=lambda f, t: line, out_dir=str(tmp_path), log=lambda *a: None)
    dj.journal_day(DAY, generate=None, out_dir=str(tmp_path), force=True, log=lambda *a: None)
    night = json.loads((tmp_path / f"{DAY}.json").read_text())
    assert night["games"][0]["why"] == {**night["games"][0]["why"], "text": line, "by": "model"}


def test_kept_line_is_dropped_when_the_story_changes(tmp_path, monkeypatch):
    line = "BOS trailed 27–22 at halftime and won by 15."
    dj.journal_day(DAY, generate=lambda f, t: line, out_dir=str(tmp_path), log=lambda *a: None)
    monkeypatch.setattr(dj, "main_story", lambda g: "BOS won by 15.")
    dj.journal_day(DAY, generate=None, out_dir=str(tmp_path), force=True, log=lambda *a: None)
    night = json.loads((tmp_path / f"{DAY}.json").read_text())
    assert night["games"][0]["why"]["by"] == "auto"


def test_llama_not_running_writes_plain_and_retries_next_time(tmp_path, monkeypatch):
    def no_llama(*a, **k):
        raise SystemExit("Can't reach Ollama.")
    monkeypatch.setattr(dj.whisper, "make_generator", no_llama)
    dj.main(["--date", DAY.isoformat(), "--llama-if-available", "--out-dir", str(tmp_path)])
    night = json.loads((tmp_path / f"{DAY}.json").read_text())
    assert night["llama_missing"] and not night["complete"]
    assert dj.INCOMPLETE_MARKER in (tmp_path / f"{DAY}.md").read_text()

    # Next morning Llama is up: the unfinished night is written again without --force.
    monkeypatch.setattr(dj.whisper, "make_generator",
                        lambda *a, **k: lambda f, t: "BOS trailed 27–22 at halftime and won by 15.")
    dj.main(["--date", DAY.isoformat(), "--llama-if-available", "--out-dir", str(tmp_path)])
    night = json.loads((tmp_path / f"{DAY}.json").read_text())
    assert night["complete"] and night["games"][0]["why"]["by"] == "model"


def test_without_the_flag_a_missing_llama_still_stops(tmp_path, monkeypatch):
    monkeypatch.setattr(dj.whisper, "make_generator",
                        lambda *a, **k: (_ for _ in ()).throw(SystemExit("Can't reach Ollama.")))
    with pytest.raises(SystemExit):
        dj.main(["--date", DAY.isoformat(), "--out-dir", str(tmp_path)])


def test_quarter_scores_are_cached_with_the_journal_not_in_data_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(dj, "SUMMARY_CACHE_DIR", str(tmp_path / "_cache"))
    dj._summary_write("boxsummary_line_1", SUMMARY["line"])
    assert (tmp_path / "_cache" / "boxsummary_line_1.json").exists()
    assert dj._summary_read("boxsummary_line_1")["data"][0]["teamTricode"] == "BOS"
