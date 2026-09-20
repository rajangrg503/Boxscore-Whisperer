"""Tests for engine/disagreement.py -- the list, and what it may claim.

Two kinds of thing are pinned here. One is arithmetic: the ranking has
to be by probability rather than raw difference, or every points prop
tops every list. The other is restraint, and it matters more:

  * the market's number never leaves this module
  * a leg we broadly agree with is not a disagreement
  * the wording claims a difference of opinion, never a good bet --
    nothing has measured that yet, and the forward test is what will
"""

import json

import pytest

from engine import disagreement as dis


def projection(projected=25.0, low=17.0, high=42.0, n_prior=40):
    return {"projected": projected, "low": low, "high": high,
            "n_prior": n_prior, "calibrated": True}


def record(stats=None, player="2544", n_prior=40):
    return {"players": {player: {"n_prior": n_prior,
                                 "stats": stats or {"PTS": projection()}}}}


def leg(stat="PTS", line=25.5, player="2544", name="LeBron James"):
    return {"player_id": player, "stat": stat, "line": line, "name": name}


# ---- the licence line ----------------------------------------------------
def test_the_markets_number_never_leaves_this_module():
    """A public ranking carrying their numbers would be exactly the
    redistribution the terms forbid."""
    rows = dis.rank([leg(line=19.5)], record())
    assert rows, "expected a disagreement to rank"
    blob = json.dumps(rows)
    assert "19.5" not in blob
    assert "line" not in rows[0]
    assert "cutoff" not in rows[0]


# ---- what counts as a disagreement ---------------------------------------
def test_a_leg_we_broadly_agree_with_is_not_listed():
    """A leg we make 52% is one the market priced about right, and
    listing it buries the few that are interesting."""
    rows = dis.rank([leg(line=25.5)], record(stats={"PTS": projection(25.0)}))
    assert rows == []


def test_a_leg_we_see_very_differently_is_listed():
    rows = dis.rank([leg(line=15.5)], record(stats={"PTS": projection(25.0)}))
    assert len(rows) == 1
    assert rows[0]["side"] == "over"
    assert rows[0]["our_probability"] > 0.5


def test_the_side_follows_which_way_we_lean():
    rows = dis.rank([leg(line=35.5)], record(stats={"PTS": projection(25.0)}))
    assert rows[0]["side"] == "under"
    assert rows[0]["our_probability"] > 0.5      # our chance on OUR side


def test_a_whole_number_line_is_read_the_way_the_page_reads_it():
    """A typed 20 means "20 or more" everywhere else in this app, so
    the cutoff is 19.5 here too. Reading it differently would rank a
    leg on a claim the product does not make."""
    over = dis.rank([leg(line=20)], record(stats={"PTS": projection(25.0)}))
    half = dis.rank([leg(line=19.5)], record(stats={"PTS": projection(25.0)}))
    assert over[0]["our_probability"] == pytest.approx(half[0]["our_probability"])


# ---- ranking -------------------------------------------------------------
def test_ranking_is_by_probability_not_by_raw_difference():
    """Two points above a points line and two rebounds above a rebounds
    line are not comparable claims. Ranking on the raw gap would put
    every points prop at the top of every list."""
    legs = [leg(stat="PTS", line=27.5), leg(stat="REB", line=3.5)]
    rows = dis.rank(legs, record(stats={
        "PTS": projection(25.0, 17.0, 42.0),        # 2.5 below the line
        "REB": projection(8.0, 4.0, 13.0),          # 4.5 above a small line
    }))
    assert [row["stat"] for row in rows] == ["REB", "PTS"]


def test_the_biggest_disagreement_comes_first():
    legs = [leg(stat="PTS", line=22.5), leg(stat="AST", line=2.5)]
    rows = dis.rank(legs, record(stats={
        "PTS": projection(25.0, 17.0, 42.0),
        "AST": projection(8.0, 4.0, 13.0),
    }))
    assert rows == sorted(rows, key=lambda r: r["gap"], reverse=True)


def test_a_limit_takes_the_top_of_the_list():
    legs = [leg(stat="PTS", line=15.5), leg(stat="REB", line=2.5),
            leg(stat="AST", line=1.5)]
    rows = dis.rank(legs, record(stats={
        "PTS": projection(25.0, 17.0, 42.0),
        "REB": projection(8.0, 4.0, 13.0),
        "AST": projection(6.0, 3.0, 10.0),
    }), limit=2)
    assert len(rows) == 2


# ---- refusing to rank what it cannot ------------------------------------
def test_a_player_we_never_projected_is_skipped():
    """Every leg in this list has to have a claim behind it."""
    assert dis.rank([leg(player="99999")], record()) == []


def test_a_stat_we_never_projected_is_skipped():
    assert dis.rank([leg(stat="BLK")], record(stats={"PTS": projection()})) == []


def test_a_line_that_is_not_a_line_is_skipped():
    assert dis.rank([leg(line=-4.5)], record()) == []
    assert dis.rank([leg(line=0)], record()) == []


def test_no_legs_and_no_projections_are_both_empty_not_errors():
    assert dis.rank([], record()) == []
    assert dis.rank([leg()], {}) == []
    assert dis.rank(None, None) == []


# ---- the probability itself ---------------------------------------------
def test_the_probability_is_the_one_the_player_card_shows():
    """Ranked here and typed into the card, the same leg must not give
    two different numbers."""
    from engine.distribution import distribution_for
    from engine.line_input import interpret
    proj = projection(25.0, 17.0, 42.0)
    spread = (42.0 - 17.0) / 2.56
    expected = distribution_for("PTS", 25.0, spread, 40).sf(interpret(19.5).cutoff)
    assert dis.probability_over("PTS", proj, interpret(19.5).cutoff) == pytest.approx(expected)


def test_a_projection_with_no_width_cannot_be_ranked():
    assert dis.probability_over("PTS", {"projected": 25.0}, 19.5) is None


# ---- the wording ---------------------------------------------------------
def test_the_sentence_claims_a_difference_of_opinion_not_a_good_bet():
    """Nothing has measured whether these disagreements are worth
    money. The forward test is what will."""
    rows = dis.rank([leg(line=15.5)], record(stats={"PTS": projection(25.0)}))
    line = dis.sentence(rows[0])
    assert "we make him" in line
    for forbidden in ("best bet", "value", "edge", "lock", "recommend"):
        assert forbidden not in line.lower()


def test_the_sentence_names_the_player_and_the_stat():
    rows = dis.rank([leg(line=15.5)], record(stats={"PTS": projection(25.0)}))
    line = dis.sentence(rows[0])
    assert "LeBron James" in line and "Points" in line


def test_no_entry_means_no_sentence():
    assert dis.sentence(None) is None


# ---- the two files agreeing about the same record -------------------------
# This module was written reading "n_prior"; tools/capture_projections.py
# writes "n_prior_games". Nothing failed. distribution_for declines
# below five prior games, so probability_over returned None on every
# leg and rank() returned an EMPTY LIST for every night of the season
# -- while these tests passed, because they built the record by hand in
# this module's spelling instead of through the writer.
#
# So the fixture below is built by tools/capture_projections.py itself.
# If the two files ever disagree about a key again, this is what fails.
import os                                                      # noqa: E402
import sys                                                     # noqa: E402
from datetime import datetime, timezone                        # noqa: E402

import pandas as pd                                            # noqa: E402

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import capture_projections as cp                               # noqa: E402


def written_record(monkeypatch, n=40, projected=25.0):
    """A projections record as the capture tool actually writes one."""
    dates = pd.date_range("2026-01-01", periods=n)[::-1]
    payload = {"cached_at": "x", "data": [
        {"GAME_DATE": day.strftime("%Y-%m-%d"), "MIN": 34,
         "PTS": projected + (i % 7) - 3, "REB": 5 + (i % 3),
         "AST": 6 + (i % 4), "STL": 1, "BLK": 1, "FG3M": 2, "FG3A": 6,
         "TOV": 3, "OREB": 1} for i, day in enumerate(dates)]}
    monkeypatch.setattr(cp, "read_payload", lambda key: payload)
    body, _skipped = cp.build_record(
        ["2544"], "2026-27", datetime(2026, 10, 21, tzinfo=timezone.utc))
    return body


def test_a_record_written_by_the_capture_tool_produces_a_list(monkeypatch):
    record = written_record(monkeypatch)
    legs = [{"player_id": "2544", "stat": "PTS", "line": 15.5,
             "name": "LeBron James"}]
    rows = dis.rank(legs, record)
    assert rows, "rank() found nothing in a record the capture tool wrote"
    assert rows[0]["n_prior"] == 40
    assert rows[0]["side"] == "over"


def test_the_prior_game_count_survives_the_trip_from_the_writer(monkeypatch):
    record = written_record(monkeypatch)
    assert dis.prior_games(record["players"]["2544"]) == 40


def test_either_spelling_of_the_prior_count_is_read():
    assert dis.prior_games({"n_prior_games": 12}) == 12
    assert dis.prior_games({"n_prior": 12}) == 12
    assert dis.prior_games({}) == 0
    assert dis.prior_games(None) == 0
    assert dis.prior_games({"n_prior_games": "x"}) == 0


# ---- the market's side, taken from the prices ----------------------------
# This module ranked by distance from 50%, on the stated assumption that
# a line is where a book balances its action. A dress rehearsal against
# the one real capture showed the assumption was not merely imprecise,
# it selected for the wrong legs:
#
#     strong legs, median break-even   60.2%
#     every other side, median         51.1%
#
# Six of the eight biggest "disagreements" were priced PAST us. The
# ranking was finding props where the book had already moved the price
# to say what we were about to say.
def priced_leg(over, under, line=19.5, player="2544", stat="PTS",
               name="LeBron James"):
    return {"player_id": player, "stat": stat, "line": line, "name": name,
            "prices": {"over": over, "under": under}}


def test_an_evenly_priced_prop_behaves_exactly_as_before():
    # -110 both ways de-vigs to 50%, so the old assumption was right
    # here and the change must not move this case at all.
    assert dis.market_probability(priced_leg("-110", "-110")) == (0.5, True)


def test_a_juiced_prop_takes_its_probability_from_the_price():
    market, priced = dis.market_probability(priced_leg("-300", "+240"))
    assert priced is True
    assert 0.70 < market < 0.74, market


def test_an_unpriced_leg_falls_back_and_says_so():
    market, priced = dis.market_probability(
        {"player_id": "2544", "stat": "PTS", "line": 19.5})
    assert (market, priced) == (0.5, False)


def test_a_leg_the_market_has_priced_past_us_is_not_a_disagreement():
    """THE REGRESSION. We make him 59%; the price needs 68%. Under the
    old rule that was a nine-point 'disagreement' and went top of the
    list. It is the opposite of one -- the market is more confident
    than us, in our own direction."""
    projection = {"projected": 25.0, "low": 17.0, "high": 42.0,
                  "n_prior_games": 40}
    # -650/+450 puts the market around 85% on the over; our number is
    # nowhere near that, so the only honest answer is "no disagreement".
    leg = priced_leg("-650", "+450", line=15.5)
    assert dis.for_leg(leg, projection) is None

    # The identical leg, evenly priced, IS a disagreement.
    assert dis.for_leg(priced_leg("-110", "-110", line=15.5),
                       projection) is not None


def test_the_side_follows_the_price_not_the_line():
    # Our number sits above the line, so the old rule always said
    # "over". Against a market that is higher still, the disagreement
    # is on the under.
    projection = {"projected": 25.0, "low": 17.0, "high": 42.0,
                  "n_prior_games": 40}
    entry = dis.for_leg(priced_leg("-900", "+600", line=18.5), projection)
    assert entry is not None and entry["side"] == "under"


def test_every_row_says_whether_the_market_was_priced_or_assumed():
    projection = {"projected": 25.0, "low": 17.0, "high": 42.0,
                  "n_prior_games": 40}
    priced = dis.for_leg(priced_leg("-110", "-110"), projection)
    assumed = dis.for_leg({"player_id": "2544", "stat": "PTS",
                           "line": 19.5, "name": "x"}, projection)
    assert priced["market_priced"] is True
    assert assumed["market_priced"] is False


def test_the_sentence_still_carries_no_price_and_no_line():
    projection = {"projected": 25.0, "low": 17.0, "high": 42.0,
                  "n_prior_games": 40}
    entry = dis.for_leg(priced_leg("-137", "+113"), projection)
    text = dis.sentence(entry)
    for forbidden in ("-137", "113", "19.5"):
        assert forbidden not in text


def test_the_gap_is_not_published_by_the_sentence():
    """our_probability minus gap is the market's implied probability,
    which is the price in different clothes."""
    projection = {"projected": 25.0, "low": 17.0, "high": 42.0,
                  "n_prior_games": 40}
    entry = dis.for_leg(priced_leg("-300", "+240", line=12.5), projection)
    if entry:
        assert f"{entry['gap']:.0%}" not in dis.sentence(entry)
