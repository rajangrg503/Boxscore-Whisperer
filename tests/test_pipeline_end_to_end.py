"""The whole chain, and the one question none of the other tests ask:
did this stage produce ANYTHING?

WHY THIS FILE EXISTS
engine/disagreement.py read "n_prior". tools/capture_projections.py
writes "n_prior_games". distribution_for declines below five prior
games, so probability_over returned None on every leg and rank()
returned an empty list for EVERY real record -- the whole feature,
shipped and silent since PR #41.

Nothing failed. Its unit tests passed, because they built the record by
hand in this module's own spelling instead of through the writer. And
the symptom had a disguise ready: tools/tonights_gaps.py prints

    "nothing we see very differently tonight."

and says in a comment that this is a real answer and a common one. It
is. That is exactly why an empty list could have run all season without
anybody thinking twice.

That was the third of the same kind in a week:

    the odds parser      a guessed schema, not the real one
    early_season_sweep   unpaired groups with different composition
    the disagreement list a record shape nothing writes

THE RULE THIS FILE ENFORCES
1. Every fixture below the API boundary comes from the writer that
   really produces it -- capture_projections builds the projections,
   odds_snapshot builds the legs, score_forward_test builds the record.
   A dict typed by hand tests a module against itself.
2. Every stage is asserted NON-EMPTY, with a message saying what a zero
   would have meant. A pipeline that quietly produces nothing is the
   failure mode this codebase keeps shipping.

Only the two real boundaries are hand-built: an NBA game log and an
odds snapshot. Those come from somebody else's API, so there is no
writer of ours to ask. The snapshot fixture is shaped from the real
capture of 20 Sep 2026 and every key in it has been seen in the wild.
"""

import json
import os
import sys
from datetime import datetime, timezone

import pandas as pd
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import capture_projections as cp                                  # noqa: E402
import score_forward_test as sc                                   # noqa: E402
from engine import disagreement, odds_snapshot, pricing           # noqa: E402

SEASON = "2026-27"
GAME_DATE = "2026-10-21"
CAPTURED_AT = datetime(2026, 10, 22, 1, 30, tzinfo=timezone.utc)

# Six real players, so the name resolution in odds_snapshot has to do
# its actual job rather than being handed an id.
CAST = [
    ("LEBRON_JAMES_1_NBA", "LeBron James", "2544"),
    ("STEPHEN_CURRY_1_NBA", "Stephen Curry", "201939"),
    ("NIKOLA_JOKIC_1_NBA", "Nikola Jokic", "203999"),
    ("LUKA_DONCIC_1_NBA", "Luka Doncic", "1629029"),
    ("JAYSON_TATUM_1_NBA", "Jayson Tatum", "1628369"),
    ("ANTHONY_EDWARDS_1_NBA", "Anthony Edwards", "1630162"),
]

# statID -> (our column, the line the book posts, what he actually did).
# The actual is set above the line for every prop, so a stage that
# silently drops a leg shows up as a missing WIN rather than as noise.
MARKETS = {
    "points": ("PTS", "19.5", 30),
    "rebounds": ("REB", "5.5", 9),
    "assists": ("AST", "4.5", 8),
}


# ---- the two API boundaries, hand-built because nothing of ours writes them
def gamelog(scorer, n=40):
    """A season's log in the NBA's shape, newest first."""
    dates = pd.date_range("2026-01-01", periods=n)[::-1]
    rows = [{"GAME_DATE": day.strftime("%Y-%m-%d"), "MIN": 34,
             "PTS": scorer + (i % 5) - 2, "REB": 8 + (i % 3) - 1,
             "AST": 7 + (i % 3) - 1, "STL": 1, "BLK": 1, "FG3M": 2,
             "FG3A": 6, "TOV": 3, "OREB": 2}
            for i, day in enumerate(dates)]
    # The night being settled, in the format the box score really uses.
    rows.insert(0, {"GAME_DATE": "Oct 21, 2026", "MIN": 36,
                    "PTS": MARKETS["points"][2], "REB": MARKETS["rebounds"][2],
                    "AST": MARKETS["assists"][2], "STL": 2, "BLK": 1,
                    "FG3M": 3, "FG3A": 8, "TOV": 2, "OREB": 2})
    return {"cached_at": "x", "data": rows}


def snapshot():
    """An odds snapshot in the real v2 shape -- composite oddIDs, the
    line as a string in bookOverUnder, the feed's own player slugs, and
    the moneylines and quarter props that come with them."""
    odds = {}
    for slug, _name, _nba in CAST:
        for stat_id, (_col, line, _actual) in MARKETS.items():
            for side, price in (("over", "-115"), ("under", "-105")):
                odds[f"{stat_id}-{slug}-game-ou-{side}"] = {
                    "statID": stat_id, "playerID": slug, "statEntityID": slug,
                    "betTypeID": "ou", "periodID": "game", "sideID": side,
                    "bookOverUnder": line, "fairOverUnder": line,
                    "bookOdds": price, "bookOddsAvailable": True,
                    "marketName": stat_id,
                }
        # The noise a real capture carries alongside the props.
        odds[f"points-{slug}-1q-ou-over"] = {
            "statID": "points", "playerID": slug, "statEntityID": slug,
            "betTypeID": "ou", "periodID": "1q", "sideID": "over",
            "bookOverUnder": "7.5", "bookOdds": "-110"}
    odds["points-home-game-ml-home"] = {
        "statID": "points", "statEntityID": "home", "betTypeID": "ml",
        "periodID": "game", "sideID": "home", "bookOdds": "-140"}
    odds["points-home-game-sp-home"] = {
        "statID": "points", "statEntityID": "home", "betTypeID": "sp",
        "periodID": "game", "sideID": "home", "bookSpread": "-3.5",
        "bookOdds": "-110"}
    odds["points-all-game-ou-over"] = {
        "statID": "points", "statEntityID": "all", "betTypeID": "ou",
        "periodID": "game", "sideID": "over", "bookOverUnder": "228.5",
        "bookOdds": "-110"}
    return {"response": {"success": True, "data": [{
        "eventID": "e1", "leagueID": "NBA",
        "teams": {"home": {"teamID": "DETROIT_PISTONS_NBA",
                           "names": {"medium": "Pistons"}},
                  "away": {"teamID": "BOSTON_CELTICS_NBA",
                           "names": {"medium": "Celtics"}}},
        "players": {slug: {"playerID": slug, "name": name}
                    for slug, name, _nba in CAST},
        "odds": odds}]}}


# ---- the chain ------------------------------------------------------------
@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    """Runs the real chain once and hands back every stage's output."""
    monkeypatch.setattr(cp, "read_payload", lambda key: gamelog(24.0))
    monkeypatch.setattr(sc, "RESULT_DIR", str(tmp_path / "results"))

    blob = snapshot()
    ids, snapshot_report = odds_snapshot.nba_player_ids(blob)
    legs, _ = odds_snapshot.read(blob)
    projections, skipped = cp.build_record(ids, SEASON, CAPTURED_AT)

    path = tmp_path / "projections.json"
    path.write_text(json.dumps(projections))

    rows = disagreement.rank(legs, projections)
    record = sc.build_record(projections, str(path), legs, None, GAME_DATE,
                             CAPTURED_AT, read=lambda key: gamelog(24.0))
    return {"legs": legs, "ids": ids, "skipped": skipped, "rows": rows,
            "projections": projections, "record": record,
            "snapshot_report": snapshot_report, "context":
            odds_snapshot.game_context(blob)}


def test_the_snapshot_yields_the_players_it_names(pipeline):
    """Zero here is the guessed-schema failure: the feed's slug fed to
    gamelog_<id>_<season> finds nothing, so every player is skipped as
    having too little history -- indistinguishable from the off-season."""
    assert pipeline["ids"], "no player ids resolved out of the snapshot"
    assert len(pipeline["ids"]) == len(CAST)
    assert "unresolved_players" not in pipeline["snapshot_report"]
    assert "unknown_stats" not in pipeline["snapshot_report"]


def test_the_snapshot_yields_one_leg_per_player_and_stat(pipeline):
    legs = pipeline["legs"]
    assert legs, "no legs read from the snapshot"
    assert len(legs) == len(CAST) * len(MARKETS)
    assert all(leg["prices"] for leg in legs), "a leg came back unpriced"


def test_the_game_context_survives_the_same_read(pipeline):
    """The spread and the total are in the same file and were thrown
    away for a while. Nothing else asserts they come back together."""
    context = pipeline["context"]["e1"]
    assert context["spread"] == -3.5 and context["favourite"] == "home"
    assert context["total"] == 228.5


def test_every_player_in_the_snapshot_gets_a_projection(pipeline):
    assert pipeline["projections"]["players"], "projected nobody"
    assert not pipeline["skipped"], f"skipped {pipeline['skipped']}"
    assert set(pipeline["projections"]["players"]) == set(pipeline["ids"])


def test_the_disagreement_list_is_not_empty(pipeline):
    """THE REGRESSION. rank() returned [] on every real record from
    PR #41 until the key mismatch was found, and tools/tonights_gaps.py
    printed "nothing we see very differently tonight" -- which is a real
    answer on a real night, and was the perfect disguise."""
    rows = pipeline["rows"]
    assert rows, ("rank() found nothing in a record capture_projections "
                  "wrote -- this is the n_prior bug, or another like it")
    # The count the writer put on the player has to be the count the
    # ranking used. Zero here is the bug; a wrong number is the next one.
    expected = {player["n_prior_games"]
                for player in pipeline["projections"]["players"].values()}
    assert {row["n_prior"] for row in rows} == expected
    assert all(row["our_probability"] >= 0.5 for row in rows)


def test_the_scored_record_settles_what_it_was_given(pipeline):
    totals = pipeline["record"]["totals"]
    assert totals.get("scored"), "nothing was scored"
    assert totals.get("legs") == len(pipeline["legs"]), "legs went missing"
    # Every actual was set above every line, so every side we took is a
    # winner. A partial number here means a leg was dropped in between.
    assert totals["legs_correct"] == totals["legs"]
    assert totals.get("strong_legs"), "no leg counted as a strong one"
    assert not totals.get("void_players")


def test_the_money_comes_through_with_the_bar_it_had_to_clear(pipeline):
    money = pipeline["record"]["money"]
    assert money["all"].get("profit"), "no units, on a night of winners"
    assert money["all"]["legs"] == len(pipeline["legs"])
    assert money["all"]["roi"] > 0
    # -115 needs 53.5%. If this ever reads 52.4% the measured bar has
    # been replaced by the -110 assumption somewhere upstream.
    assert money["all"]["break_even"] == pytest.approx(115 / 215, abs=1e-4)


BOOKKEEPING_KEYS = {
    "projections_file", "projections_sha256",
    "line_snapshot_file", "line_snapshot_sha256",
}


def licence_blob(record):
    """The record's DATA, with `sources` left out.

    `sources.projections_file` is a path. On 22 Sep 2026 this test
    failed with "the price -105 reached the record" because pytest's
    temp directory had reached `pytest-105` and the fixture writes
    outside the repo, so relpath walked out to
    `.../pytest-105/.../projections.json`. No price was anywhere near
    it. A licence check that fires on the 105th run of the day, on a
    machine nobody changed, teaches everybody to ignore it -- and a
    licence check people ignore is worse than none.

    A price lands in a leg or a player's stats. Nothing puts one in a
    filename or a digest, so dropping `sources` costs the check
    nothing -- provided `sources` really is only bookkeeping, which
    the test below pins before relying on it.
    """
    return json.dumps({key: value for key, value in record.items()
                       if key != "sources"})


def test_the_published_record_still_carries_no_price_and_no_line(pipeline):
    """The licence line, asserted on the record the whole chain built
    rather than on one assembled for the purpose."""
    record = pipeline["record"]
    # Excluding `sources` is only safe while `sources` is bookkeeping
    # and nothing else. A new key here is a new place a price could
    # hide from this check, so it fails until somebody looks.
    assert set(record["sources"]) <= BOOKKEEPING_KEYS, (
        "a new key in sources -- the licence check below skips this "
        "whole object, so decide whether a price could reach it")

    blob = licence_blob(record)
    for price in ("-115", "-105"):
        assert price not in blob, f"the price {price} reached the record"
    for _stat, (_col, line, _actual) in MARKETS.items():
        assert f'"{line}"' not in blob
    assert "bookOverUnder" not in blob and "bookOdds" not in blob


def test_the_licence_check_can_still_fail(pipeline):
    """The control on the test above. Narrowing what it reads is how a
    guard quietly stops guarding -- this project has shipped that
    exact bug twice. So put a price where one would really land and
    confirm the same function still sees it."""
    record = json.loads(json.dumps(pipeline["record"]))  # deep copy
    player = next(iter(record["players"].values()))
    stat = next(iter(player["stats"].values()))
    stat["price"] = -115
    assert "-115" in licence_blob(record)


def test_every_stage_would_have_failed_loudly_on_an_empty_snapshot():
    """The negative control: if the chain is fed nothing, every stage
    reports nothing rather than inventing something to report."""
    empty = {"response": {"data": []}}
    ids, report = odds_snapshot.nba_player_ids(empty)
    legs, _ = odds_snapshot.read(empty)
    assert ids == [] and legs == [] and report == {}
    assert disagreement.rank(legs, {"players": {}}) == []
    assert pricing.tally([])["roi"] is None
