"""Tests for engine/odds_snapshot.py -- reading the real odds schema.

The first version of this parser was written against a guessed schema
and every part of the guess was wrong. It would have found zero legs
on every night of the season and reported that as a quiet night. So
the fixtures here are shaped like the real capture of 20 Sep 2026, and
what is pinned is mostly the three ways it went wrong:

  * the line is a STRING in bookOverUnder, not a number in "line"
  * playerID is the feed's slug, not an NBA player id
  * a third of the entries are moneylines, spreads and quarter props
"""

import json

import pytest

from engine import odds_snapshot as osnap


def odd(stat="points", player="LEBRON_JAMES_1_NBA", side="over",
        bet_type="ou", period="game", book="25.5", fair=None,
        price="-110", fair_price=None, available=None):
    body = {
        "statID": stat, "playerID": player, "statEntityID": player,
        "betTypeID": bet_type, "periodID": period, "sideID": side,
        "marketName": "whatever",
    }
    if price is not None:
        body["bookOdds"] = price
    if fair_price is not None:
        body["fairOdds"] = fair_price
    if available is not None:
        body["bookOddsAvailable"] = available
    if book is not None:
        body["bookOverUnder"] = book
    if fair is not None:
        body["fairOverUnder"] = fair
    return body


def snapshot(odds, players=None):
    players = players or {"LEBRON_JAMES_1_NBA": {
        "playerID": "LEBRON_JAMES_1_NBA", "name": "LeBron James",
        "firstName": "LeBron", "lastName": "James",
        "teamID": "LOS_ANGELES_LAKERS_NBA"}}
    return {"response": {"success": True, "data": [
        {"eventID": "e1", "leagueID": "NBA",
         "players": players,
         "odds": {f"k{i}": o for i, o in enumerate(odds)}}]}}


# ---- the three things the guess got wrong ---------------------------------
def test_the_line_is_read_out_of_a_string_field():
    """bookOverUnder: "25.5". The guessed parser looked for a numeric
    "overUnder" or "line" and would have found nothing, all season."""
    legs, _report = osnap.read(snapshot([odd(book="25.5")]))
    assert len(legs) == 1
    assert legs[0]["line"] == 25.5
    assert legs[0]["line_source"] == "book"


def test_the_feeds_player_slug_is_resolved_to_an_nba_id():
    """A slug finds no cached game log, so every player would have been
    skipped as having too little history -- which looks exactly like
    the off-season."""
    legs, _report = osnap.read(snapshot([odd()]))
    assert legs[0]["player_id"] == "2544"      # LeBron James
    assert legs[0]["name"] == "LeBron James"


@pytest.mark.parametrize("kwargs", [
    {"bet_type": "ml"},          # moneyline
    {"bet_type": "sp"},          # spread
    {"period": "1q"},            # first quarter
    {"period": "1h"},            # first half
    {"period": "reg"},           # regulation only -- settles without OT
])
def test_anything_that_is_not_a_game_long_over_under_is_left_alone(kwargs):
    """Scoring a first-quarter points line against a full box score
    would be wrong in a way no total would reveal."""
    legs, _report = osnap.read(snapshot([odd(**kwargs)]))
    assert legs == []


# ---- what it refuses to invent --------------------------------------------
def test_a_player_it_cannot_match_is_reported_not_guessed():
    players = {"NOT_A_REAL_PERSON_1_NBA": {"name": "Zzz Nobodyson"}}
    legs, report = osnap.read(
        snapshot([odd(player="NOT_A_REAL_PERSON_1_NBA")], players))
    assert legs == []
    assert report["unresolved_players"]["Zzz Nobodyson"] == 1


def test_an_unknown_stat_is_reported_as_a_parser_failure():
    legs, report = osnap.read(snapshot([odd(stat="somethingNew")]))
    assert legs == []
    assert report["unknown_stats"]["somethingNew"] == 1


def test_a_market_we_choose_not_to_score_is_not_a_parser_failure():
    """"We do not do combined totals" and "our parser broke" produce
    the same missing leg and want opposite responses."""
    legs, report = osnap.read(snapshot([
        odd(stat="points+rebounds+assists"), odd(stat="doubleDouble")]))
    assert legs == []
    assert "unknown_stats" not in report
    assert report["not_scored"]["points+rebounds+assists"] == 1
    assert report["not_scored"]["doubleDouble"] == 1


def test_a_missing_line_is_skipped_rather_than_zeroed():
    legs, _report = osnap.read(snapshot([odd(book=None)]))
    assert legs == []


# ---- one leg per claim ----------------------------------------------------
def test_over_and_under_are_one_leg_not_two():
    """They are two entries carrying the same number. Counting both
    would weight every prop twice in the published figure."""
    legs, _report = osnap.read(snapshot([
        odd(side="over"), odd(side="under")]))
    assert len(legs) == 1


def test_the_same_prop_from_several_books_is_one_leg():
    legs, _report = osnap.read(snapshot([
        odd(book="25.5"), odd(book="26.5"), odd(book="25.5")]))
    assert len(legs) == 1


def test_different_stats_for_one_player_are_different_legs():
    legs, _report = osnap.read(snapshot([
        odd(stat="points"), odd(stat="rebounds"), odd(stat="assists")]))
    assert sorted(leg["stat"] for leg in legs) == ["AST", "PTS", "REB"]


# ---- the book line, not the consensus -------------------------------------
def test_the_bookmakers_line_is_preferred_over_the_consensus():
    """"We beat the market" has to mean something somebody could
    actually have bet."""
    legs, _report = osnap.read(snapshot([odd(book="25.5", fair="24.5")]))
    assert legs[0]["line"] == 25.5 and legs[0]["line_source"] == "book"


def test_the_consensus_is_used_when_no_book_offered_one_and_says_so():
    legs, _report = osnap.read(snapshot([odd(book=None, fair="24.5")]))
    assert legs[0]["line"] == 24.5 and legs[0]["line_source"] == "fair"


# ---- names the feed writes without diacritics -----------------------------
def test_a_name_written_without_its_accents_still_resolves():
    """The feed writes plain ASCII and the NBA's table has the
    diacritics. That is most of a team's stars in this league."""
    players = {"NIKOLA_JOKIC_1_NBA": {"name": "Nikola Jokic"}}
    legs, report = osnap.read(
        snapshot([odd(player="NIKOLA_JOKIC_1_NBA")], players))
    assert "unresolved_players" not in report
    assert legs[0]["player_id"] == "203999"


# ---- the other caller -----------------------------------------------------
def test_player_ids_come_back_as_nba_ids_for_the_projection_capture():
    players = {"LEBRON_JAMES_1_NBA": {"name": "LeBron James"},
               "STEPHEN_CURRY_1_NBA": {"name": "Stephen Curry"}}
    ids, _report = osnap.nba_player_ids(snapshot(
        [odd(player="LEBRON_JAMES_1_NBA"), odd(player="STEPHEN_CURRY_1_NBA")],
        players))
    assert ids == sorted(["2544", "201939"])


def test_a_snapshot_with_nothing_in_it_is_empty_not_an_error():
    ids, report = osnap.nba_player_ids({"response": {"data": []}})
    assert ids == [] and report == {}


def test_it_reads_a_file_as_well_as_a_blob(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps(snapshot([odd()])))
    legs, _report = osnap.read(str(path))
    assert len(legs) == 1


def test_a_team_market_is_not_reported_as_an_unmatched_player():
    """Team totals are game-long over/unders too, and they name their
    entity "all", "home" or "away". Reporting those as players we
    failed to resolve would cry wolf on every capture -- which is how
    a real unresolved player ends up unnoticed."""
    legs, report = osnap.read(snapshot([
        odd(player="all"), odd(player="home"), odd(player="away")]))
    assert legs == []
    assert "unresolved_players" not in report
    assert sum(report["team_markets"].values()) == 3


# ---- game context: the spread, which the first parser threw away --------
def event(odds, home="DETROIT_PISTONS_NBA", away="BOSTON_CELTICS_NBA", eid="e1"):
    return {"response": {"data": [{
        "eventID": eid,
        "teams": {"home": {"teamID": home, "names": {"medium": "Pistons"}},
                  "away": {"teamID": away, "names": {"medium": "Celtics"}}},
        "players": {}, "odds": {f"k{i}": o for i, o in enumerate(odds)}}]}}


def spread(value="-5.5", side="home", period="game", fair=None):
    body = {"betTypeID": "sp", "statID": "points", "sideID": side,
            "statEntityID": side, "periodID": period, "bookOdds": "-110"}
    if value is not None:
        body["bookSpread"] = value
    if fair is not None:
        body["fairSpread"] = fair
    return body


def total(value="221.5", side="over"):
    return {"betTypeID": "ou", "statID": "points", "sideID": side,
            "statEntityID": "all", "periodID": "game",
            "bookOverUnder": value, "bookOdds": "-110"}


def test_the_spread_is_read_from_the_home_side():
    ctx = osnap.game_context(event([spread("-5.5")]))["e1"]
    assert ctx["spread"] == -5.5
    assert ctx["spread_source"] == "book"


def test_a_negative_spread_means_the_home_team_is_favoured():
    """The convention the books print, and the one least likely to be
    misread later."""
    assert osnap.game_context(event([spread("-5.5")]))["e1"]["favourite"] == "home"
    assert osnap.game_context(event([spread("+5.5")]))["e1"]["favourite"] == "away"


def test_expected_margin_is_the_size_regardless_of_side():
    for value in ("-9.5", "+9.5"):
        ctx = osnap.game_context(event([spread(value)]))["e1"]
        assert ctx["expected_margin"] == 9.5


def test_a_pick_em_has_no_favourite():
    """Saying "home" at 0.0 would put every coin-flip game in the
    favourite bucket, which is exactly the bucket the blowout work
    cares about keeping clean."""
    ctx = osnap.game_context(event([spread("0")]))["e1"]
    assert ctx["favourite"] is None
    assert ctx["expected_margin"] == 0.0


def test_the_game_total_is_read_too():
    ctx = osnap.game_context(event([total("221.5")]))["e1"]
    assert ctx["total"] == 221.5 and ctx["total_source"] == "book"


def test_a_player_over_under_is_not_mistaken_for_the_game_total():
    """Player props and the game total are both betTypeID "ou". Only
    the one whose entity is the whole game is the total."""
    player_ou = {"betTypeID": "ou", "statID": "points", "sideID": "over",
                 "statEntityID": "LEBRON_JAMES_1_NBA", "periodID": "game",
                 "bookOverUnder": "25.5"}
    assert osnap.game_context(event([player_ou]))["e1"]["total"] is None


def test_a_quarter_spread_is_not_the_game_spread():
    assert osnap.game_context(event([spread("-2.5", period="1q")]))["e1"]["spread"] is None


def test_the_consensus_spread_is_used_when_no_book_offered_one():
    ctx = osnap.game_context(event([spread(None, fair="-4.5")]))["e1"]
    assert ctx["spread"] == -4.5 and ctx["spread_source"] == "fair"


def test_the_teams_come_back_with_it():
    ctx = osnap.game_context(event([spread()]))["e1"]
    assert ctx["home_team"] == "DETROIT_PISTONS_NBA"
    assert ctx["away_name"] == "Celtics"


def test_an_event_with_no_spread_is_still_listed():
    """A game we have no market view on is a game we know nothing about,
    not a game that vanishes."""
    ctx = osnap.game_context(event([]))["e1"]
    assert ctx["spread"] is None and ctx["expected_margin"] is None


# ---- prices, for engine/pricing.py ---------------------------------------
# A hit rate does not say whether following us made money, so a settled
# leg has to be weighted by what it paid. These pin the two ways that
# goes quietly wrong: paying out at a de-vigged price nobody offered,
# and settling the side we did not take.
def test_both_sides_carry_their_own_price():
    legs, _report = osnap.read(snapshot([
        odd(side="over", price="-130"),
        odd(side="under", price="+108"),
    ]))
    assert len(legs) == 1
    assert legs[0]["prices"] == {"over": "-130", "under": "+108"}


def test_the_two_sides_are_not_interchangeable():
    # Over at -300 and under at +240 is the same leg and two very
    # different bets. Settling the wrong one reports a profit nobody
    # could have collected.
    legs, _report = osnap.read(snapshot([
        odd(side="over", price="-300"), odd(side="under", price="+240")]))
    assert legs[0]["prices"]["over"] != legs[0]["prices"]["under"]


def test_a_de_vigged_price_is_never_used_as_a_real_one():
    # fairOdds is a defensible fallback for the LINE and an indefensible
    # one for money: it has the bookmaker's margin removed, so settling
    # at it pays about 4.5% a bet that was never on offer -- roughly
    # the size of the edge we would be claiming.
    legs, _report = osnap.read(snapshot([
        odd(side="over", price=None, fair_price="+104")]))
    assert legs[0]["prices"] == {}


def test_a_price_the_book_is_not_standing_behind_is_dropped():
    legs, _report = osnap.read(snapshot([
        odd(side="over", price="-110", available=False),
        odd(side="under", price="-110", available=True),
    ]))
    assert "over" not in legs[0]["prices"]
    assert legs[0]["prices"]["under"] == "-110"


def test_a_leg_with_no_price_is_still_a_leg():
    # Coverage and the against-the-line record do not need a price, and
    # must not be hostage to one being missing.
    legs, _report = osnap.read(snapshot([odd(price=None)]))
    assert len(legs) == 1
    assert legs[0]["line"] == 25.5
    assert legs[0]["prices"] == {}


def test_the_first_price_seen_wins_like_the_line_does():
    # A feed carries the same prop from several books. One leg per
    # player and stat, one price per side, or a popular prop is
    # weighted several times in the published figure.
    legs, _report = osnap.read(snapshot([
        odd(side="over", price="-110"), odd(side="over", price="+120")]))
    assert legs[0]["prices"]["over"] == "-110"
