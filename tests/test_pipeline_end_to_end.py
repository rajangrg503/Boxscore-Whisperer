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
import math
import os
import sys
from datetime import datetime, timezone

import pandas as pd
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import capture_projections as cp                                  # noqa: E402
import score_forward_test as sc                                   # noqa: E402
from engine import (disagreement, forward_record, odds_snapshot,  # noqa: E402
                    pricing, slip)

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


def test_the_live_record_page_reads_what_the_writer_wrote(pipeline, tmp_path):
    """The chain above stops at build_record(). The public page is the
    next link: engine/forward_record.summarise() reads the night files
    tools/score_forward_test.py writes, and the live record panel says
    whatever it returns.

    Nothing tested that seam. tests/test_forward_record.py builds its
    nights with a hand-written night() helper -- the exact shape that
    hid the n_prior bug for a whole feature: a reader and a writer
    disagreeing on a spelling, every test green because the fixture was
    written in the reader's spelling.

    So nothing here is spelled by hand. The comparison is key by key
    against the record the writer really produced.
    """
    folder = tmp_path / "results"
    folder.mkdir()
    (folder / "2026-10-21.json").write_text(json.dumps(pipeline["record"]))

    summary = forward_record.summarise(str(folder))
    assert summary["nights"] == 1, "the writer's own record did not count"
    assert summary["empty_nights"] == 0, (
        "a night the writer scored was read as having no evidence")

    written = pipeline["record"]["totals"]
    page = summary["totals"]
    assert page, "summarise produced no totals at all"

    # The counters the live record panel is built from. Named here
    # because they are the CONTRACT between the two modules -- the
    # n_prior lesson is do not build the DATA by hand, not do not write
    # down what the two sides have to agree on. A rename on either side
    # empties this set and fails here, loudly, instead of showing the
    # reader a zero.
    CORE = {"scored", "covered", "legs", "legs_correct",
            "strong_legs", "strong_correct"}
    shared = set(page) & set(written)
    assert CORE <= shared, (
        "the page and the writer no longer share the counters the live "
        f"record is built from. Missing: {sorted(CORE - shared)}. "
        f"The page reads {sorted(page)}; the writer wrote {sorted(written)}")

    for key in sorted(shared):
        assert page[key] == written[key], (
            f"the page's {key} is {page[key]!r}, the writer wrote "
            f"{written[key]!r}")

    # A key the writer never wrote is a counter that stayed at zero:
    # build_record() accumulates into a Counter, which does not
    # materialise a key it never incremented. Absence is zero, not a
    # mismatch -- void_players is normally absent, and should be.
    for key in sorted(set(page) - shared):
        assert page[key] == 0, (
            f"the page reports {key}={page[key]!r} from a record that "
            f"never carried that key")

    # ...and the shared counters are real, so the comparison above is
    # not 0 == 0 for every key -- which is what a mismatch looks like.
    assert page["legs"] > 0 and page["scored"] > 0

    money = summary["money"]["all"]
    assert money["legs"] == written["legs"]
    assert money["staked"] > 0 and money["profit"] != 0


def test_one_night_is_withheld_by_the_gate_not_by_a_missing_key(pipeline,
                                                                tmp_path):
    """The control on the test above.

    At one night every rate is None -- which looks identical to a page
    reading keys nobody writes. So repeat the SAME writer-made record
    until the gates are cleared. If the figures then appear, the silence
    at one night was the gate doing its job, not the page failing to
    find the numbers.

    The counts come from the gates and from the record, never from a
    literal here, so moving a gate cannot quietly turn this into a
    no-op.
    """
    record = pipeline["record"]
    totals = record["totals"]
    nights_needed = max(
        forward_record.MIN_NIGHTS_TO_STATE,
        math.ceil(forward_record.MIN_LEGS_TO_STATE / totals["legs"]),
        math.ceil(forward_record.MIN_CLAIMS_TO_STATE / totals["scored"]),
    )

    one = tmp_path / "one"
    one.mkdir()
    (one / "2026-10-21.json").write_text(json.dumps(record))
    held = forward_record.summarise(str(one))
    assert held["coverage"] is None
    assert held["against_line"] is None
    assert held["strong"] is None
    assert held["money"]["all"]["publishable"] is False

    many = tmp_path / "many"
    many.mkdir()
    for index in range(nights_needed):
        day = (f"2026-10-{21 + index:02d}" if index < 11
               else f"2026-11-{index - 10:02d}")
        (many / f"{day}.json").write_text(json.dumps(record))
    freed = forward_record.summarise(str(many))

    assert freed["nights"] == nights_needed
    for key in ("coverage", "against_line", "strong"):
        assert freed[key] is not None, (
            f"{key} is still withheld at {nights_needed} nights and "
            f"{freed['totals']['legs']} legs -- the page is not reading "
            f"the writer's numbers, and the gate was never the reason")
        assert 0.0 <= freed[key]["rate"] <= 1.0
    assert freed["money"]["all"]["publishable"] is True


# --------------------------------------------------------------------
# The nightly slip: the claim posted before tip-off, and the accounting
# of it the next morning. engine/slip.py explains why the commitment is
# a file rather than a formatter.
# --------------------------------------------------------------------

SLIP_CLAIM_KEYS = {"player_id", "name", "stat", "projected", "low", "high",
                   "calibrated"}


def test_a_slip_is_built_from_projections_the_writer_really_made(pipeline):
    """Not a hand-typed projections dict -- the one
    tools/capture_projections.py produced earlier in this chain."""
    names = {pid: f"Player {pid}" for pid
             in pipeline["projections"]["players"]}
    card = slip.commit(pipeline["projections"], "2026-10-21", names=names)

    assert card["claims"], "the slip named nothing from a real capture"
    assert card["range_nominal"] == pipeline["projections"]["range_nominal"]
    for claim in card["claims"]:
        assert set(claim) == SLIP_CLAIM_KEYS, (
            f"claim carries {sorted(set(claim) - SLIP_CLAIM_KEYS)} -- a "
            f"slip is published, so every field on it is published too")
        assert claim["low"] <= claim["projected"] <= claim["high"]


def test_the_slip_carries_nothing_of_the_market(pipeline):
    """The licence line, on the thing that actually gets posted.

    A slip has no line and no price by construction: the claim is a
    projection and a range, both ours. This asserts the rendered text
    as well as the structure, because the rendered text is what leaves
    the building.
    """
    names = {pid: f"Player {pid}" for pid
             in pipeline["projections"]["players"]}
    card = slip.commit(pipeline["projections"], "2026-10-21", names=names)
    text = slip.render_card(card)

    for leg in pipeline["legs"]:
        assert str(leg["line"]) not in text, (
            f"the book's line {leg['line']} reached a published slip")
    for price in ("-115", "-105"):
        assert price not in text


def test_the_slip_licence_check_can_still_fail(pipeline):
    """The control. The assertion above passes trivially if the
    renderer prints nothing useful, so put a line where one would land
    and confirm the same render surfaces it."""
    names = {pid: f"Player {pid}" for pid
             in pipeline["projections"]["players"]}
    card = slip.commit(pipeline["projections"], "2026-10-21", names=names)
    line = pipeline["legs"][0]["line"]
    card["claims"][0]["name"] = f"Someone ({line})"

    assert str(line) in slip.render_card(card)


def test_the_morning_settles_exactly_what_the_night_committed(pipeline):
    """The cherry-picking guard.

    If the morning post chose what to report, it would select for the
    claims that landed, every morning, without anybody deciding to.
    """
    names = {pid: f"Player {pid}" for pid
             in pipeline["projections"]["players"]}
    card = slip.commit(pipeline["projections"], "2026-10-21", names=names)
    settled = slip.settle(card, pipeline["record"])

    committed = [(c["player_id"], c["stat"]) for c in card["claims"]]
    reported = [(c["player_id"], c["stat"]) for c in settled["claims"]]
    assert reported == committed, (
        "the morning post reported a different set of claims than the "
        "card committed to")

    counts = slip.tally(settled)
    assert counts["claims"] == len(committed)
    assert counts["settled"] > 0, (
        "nothing settled against a record the writer really built -- "
        "the slip and the record disagree about a shape")
    assert counts["covered"] == sum(1 for c in settled["claims"]
                                    if c.get("covered"))


def test_a_claim_with_no_result_is_reported_not_dropped(pipeline):
    """The control on the guard above. A claim that cannot be settled
    is the one a quiet failure would swallow, so make one and watch it
    come back marked."""
    names = {pid: f"Player {pid}" for pid
             in pipeline["projections"]["players"]}
    card = slip.commit(pipeline["projections"], "2026-10-21", names=names)
    thinned = json.loads(json.dumps(pipeline["record"]))
    dropped = card["claims"][0]
    thinned["players"].pop(dropped["player_id"], None)

    settled = slip.settle(card, thinned)
    assert len(settled["claims"]) == len(card["claims"])

    missing = [c for c in settled["claims"]
               if c["player_id"] == dropped["player_id"]]
    assert missing, "the unsettled claim vanished from the morning post"
    assert all(c["covered"] is None and c["actual"] is None
               for c in missing)
    assert slip.tally(settled)["unsettled"] == len(missing)
    assert "did not play" in slip.render_result(settled)


def test_the_card_is_chosen_before_the_games_not_after(pipeline):
    """Selection must not depend on anything the night produced."""
    names = {pid: f"Player {pid}" for pid
             in pipeline["projections"]["players"]}
    first = slip.commit(pipeline["projections"], "2026-10-21", names=names)
    again = slip.commit(pipeline["projections"], "2026-10-21", names=names)
    assert first == again, "the card is not deterministic"

    # Assert the property, not a second copy of the sort. Reproducing
    # the ordering here would only pin that two expressions of the same
    # line agree -- and the first version of this test did exactly
    # that, then failed on a tie-break it had spelled backwards.
    chosen = slip.select(pipeline["projections"])
    assert chosen, "nothing was selected from a real capture"

    points = {pid: p["stats"]["PTS"]["projected"]
              for pid, p in pipeline["projections"]["players"].items()
              if (p.get("stats") or {}).get("PTS")}
    assert set(chosen) <= set(points), (
        "a player with no points projection cannot be ranked by one")
    left_out = set(points) - set(chosen)
    if left_out:
        assert min(points[pid] for pid in chosen) >= \
            max(points[pid] for pid in left_out), (
                "somebody left off the card outprojects somebody on it")


# ---------------------------------------------------------------------
# The public record and the visitor tracker are separate stores, and the
# separation is load-bearing rather than incidental.
#
# results/ is written only by tools/score_forward_test.py, scoring
# projections/ captures against real box scores. prediction_log.csv is
# what visitors save from the browser, and since the scenario box exists
# it can contain deliberate what-ifs. If the two ever met, a reader's
# hypothesis could reach the headline accuracy figure.
#
# Nothing enforces that today except the absence of an import, which is
# exactly the kind of guarantee that holds until somebody needs a helper
# and reaches for the nearest module. So it is pinned here, with a
# control proving the check can still fail -- an import checker that
# quietly matched nothing would pass this forever.
# ---------------------------------------------------------------------

import ast as _ast

TRACKER_MODULES = {"engine.tracker", "engine.log_store", "analytics.layer_accuracy"}


def _module_path(module):
    """Repo-relative file for a dotted module name, or None if it isn't
    one of ours (stdlib, third party)."""
    candidate = os.path.join(REPO_ROOT, *module.split(".")) + ".py"
    return candidate if os.path.exists(candidate) else None


def _imports_of(path):
    """Every dotted module name `path` imports, at any depth in the file."""
    found = set()
    for node in _ast.walk(_ast.parse(open(path).read())):
        if isinstance(node, _ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, _ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            # `from engine import tracker` names the module in the alias,
            # not in node.module -- miss this and the checker is blind to
            # the single most likely way the import would actually appear.
            found.update(f"{node.module}.{a.name}" for a in node.names)
    return found


def _reaches(start_module, targets):
    """The first target `start_module` can reach through repo-local
    imports, following them transitively, or None. Returns the chain so a
    failure says which hop introduced it."""
    seen, stack = set(), [(start_module, [start_module])]
    while stack:
        module, chain = stack.pop()
        if module in seen:
            continue
        seen.add(module)
        path = _module_path(module)
        if path is None:
            continue
        for imported in sorted(_imports_of(path)):
            if imported in targets:
                return chain + [imported]
            stack.append((imported, chain + [imported]))
    return None


def test_the_public_record_cannot_reach_the_visitor_tracker():
    for module in ("engine.forward_record", "tools.score_forward_test"):
        chain = _reaches(module, TRACKER_MODULES)
        assert chain is None, (
            f"{module} can now reach the visitor prediction log: "
            f"{' -> '.join(chain)}. The public accuracy figure would be "
            f"open to whatever a reader typed into the scenario box."
        )


def test_the_import_check_can_still_fail():
    """The control. app.py legitimately imports engine.tracker, so the
    checker must find it there. Without this, a typo in TRACKER_MODULES
    or a walk that silently visits nothing would leave the test above
    passing while checking for nothing at all."""
    chain = _reaches("app", TRACKER_MODULES)
    assert chain is not None, "the checker found no tracker import in app.py"
    assert chain[0] == "app"
    assert chain[-1] in TRACKER_MODULES


def test_the_check_follows_imports_more_than_one_hop(tmp_path, monkeypatch):
    """The other control, and the one the repo cannot supply itself.

    Nothing in this codebase currently reaches engine.tracker in two hops
    -- app.py and analytics/layer_accuracy.py both import it directly. So
    the walk's whole reason for existing (catching a tracker import that
    arrives through an innocent-looking helper) is exercised by no real
    module, and a checker that only ever looked one level deep would pass
    both tests above.

    A synthetic package proves the hop, so the guarantee is about the
    mechanism rather than about today's import graph.
    """
    pkg = tmp_path / "pretend"
    pkg.mkdir()
    (pkg / "entry.py").write_text("from pretend import middle\n")
    (pkg / "middle.py").write_text("from engine import tracker\n")
    monkeypatch.setattr(
        sys.modules[__name__], "REPO_ROOT", str(tmp_path), raising=True)

    chain = _reaches("pretend.entry", {"engine.tracker"})
    assert chain == ["pretend.entry", "pretend.middle", "engine.tracker"], chain

    # And it stops rather than looping when the graph has a cycle.
    (pkg / "middle.py").write_text("from pretend import entry\n")
    assert _reaches("pretend.entry", {"engine.tracker"}) is None
