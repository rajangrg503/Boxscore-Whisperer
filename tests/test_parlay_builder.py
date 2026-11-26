"""parlay_builder: calibration, dependence, slip maths and karma's rules, on made-up legs."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import parlay_builder as pb  # noqa: E402

CAL = {"tables": {"alt_single": [[0.8, 0.74, 500], [0.5, 0.46, 500], [0.25, 0.22, 500]],
                  "alt_combo": [[0.5, 0.44, 500]], "ou_over": [[0.52, 0.48, 500]], "ou_under": [[0.52, 0.51, 500]]},
       "between": {"mate|other|PP": -0.2, "opp|top3|CC": 0.2}, "sgm_haircut": 0.96}
SAME = {("PTS", "REB"): 0.2, ("FG3M", "PTS"): 0.6}


def leg(player, stat="PTS", th=20, price=2.0, game="AAA@BBB", team="AAA", kind="alt", pid=None, crank=5, **kw):
    cls = "ou_over" if kind == "over" else "ou_under" if kind == "under" else "alt_single" if stat in pb.SINGLE else "alt_combo"
    d = dict(date="2026-11-27", game=game, player=player, pid=pid or abs(hash(player)) % 10 ** 6, stat=stat, th=th,
             price=price, kind=kind, cls=cls, team=team, crank=crank, min_l10=32.0, dtd=False)
    d.update(kw)
    return d


def test_fair_prob_takes_the_cut_out_and_is_monotone():
    p = [pb.fair_prob(x, "alt_single", CAL) for x in (1.25, 1.6, 2.0, 3.0, 4.0)]
    assert p == sorted(p, reverse=True)
    assert pb.fair_prob(2.0, "alt_single", CAL) == pytest.approx(0.46, abs=0.005)
    assert pb.fair_prob(2.0, "alt_combo", CAL) < pb.fair_prob(2.0, "alt_single", CAL)   # combos cost more
    assert 0 < pb.fair_prob(30.0, "alt_single", CAL) < 1 / 30                            # clamps past the table


def test_fit_tables_recovers_a_known_cut():
    legs = []
    for i in range(2000):
        legs.append({"cls": "alt_single", "price": 2.0, "hit": i % 100 < 45})     # 45% at an implied 50%
    t = pb.fit_tables(legs)["alt_single"]
    assert len(t) == 1 and t[0][0] == pytest.approx(0.5) and t[0][1] == pytest.approx(0.45, abs=0.005)


def test_independent_legs_multiply():
    a, b = leg("A One", game="AAA@BBB"), leg("B Two", game="CCC@DDD", team="CCC")
    e = pb.evaluate([a, b], cal=CAL, same=SAME, n=20000)
    assert e["chance"] == pytest.approx(0.46 * 0.46, abs=0.004)
    assert e["price"] == pytest.approx(4.0) and e["price_is_estimate"]
    assert e["roi"] == pytest.approx(0.46 * 0.46 * 4 - 1, abs=0.02)


def test_same_player_legs_land_together_more_and_are_priced_down():
    a, b = leg("A One", "PTS", 20, pid=1), leg("A One", "REB", 8, pid=1)
    e = pb.evaluate([a, b], cal=CAL, same=SAME, n=20000)
    assert e["chance"] > e["independent"] + 0.02
    assert e["price"] < e["product"] * 0.96          # the estimate cuts a same-player pair
    nested = pb.evaluate([leg("A One", "PTS", 20, 2.0, pid=1), leg("A One", "PTS", 25, 4.0, pid=1)], cal=CAL, same=SAME)
    assert nested["chance"] == pytest.approx(pb.fair_prob(4.0, "alt_single", CAL), abs=0.02)   # the harder rung decides


def test_teammates_scoring_legs_land_together_less_and_opposing_stars_more():
    mates = pb.evaluate([leg("A One", pid=1), leg("A Two", pid=2)], cal=CAL, same=SAME, n=20000)
    assert mates["chance"] < mates["independent"] - 0.01
    stars = pb.evaluate([leg("A One", "PRA", 30, pid=1, crank=1), leg("B One", "PRA", 30, pid=3, team="BBB", crank=1)],
                        cal=CAL, same=SAME, n=20000)
    assert stars["chance"] > stars["independent"] + 0.01


def test_an_under_flips_the_sign():
    assert pb.pair_r(leg("A One", "PTS", pid=1), leg("A One", "REB", pid=1, kind="under"), CAL, SAME) == pytest.approx(-0.2)
    assert pb.pair_r(leg("A One", pid=1), leg("Z Nine", pid=9, game="CCC@DDD"), CAL, SAME) == 0.0


def test_bonus_stake_and_bet_return():
    slip = [leg("A One", game="G1@H1", price=2.0), leg("B Two", game="G2@H2", team="G2", price=2.0),
            leg("C Three", game="G3@H3", team="G3", price=2.0)]
    cash = pb.evaluate(slip, cal=CAL, same=SAME, n=20000)
    bonus = pb.evaluate(slip, cal=CAL, same=SAME, stake="bonus", n=20000)
    assert bonus["return_per_dollar"] == pytest.approx(cash["chance"] * 7, abs=0.01)        # stake not returned
    back = pb.evaluate(slip, cal=CAL, same=SAME, bet_return=True, n=20000)
    assert back["return_per_dollar"] == pytest.approx(cash["return_per_dollar"] + cash["one_short"] * pb.BONUS_VALUE, abs=0.01)
    assert cash["one_short"] == pytest.approx(3 * 0.46 * 0.46 * 0.54, abs=0.01)


def test_rules_keep_out_dtd_threes_bench_and_luka_35():
    assert pb.eligible(leg("A One"), "sgm")
    assert not pb.eligible(leg("A One", dtd=True), "single")
    assert not pb.eligible(leg("A One", "FG3M", 3), "sgm") and pb.eligible(leg("A One", "FG3M", 3), "single")
    assert not pb.eligible(leg("A One", min_l10=24.0), "sgm") and pb.eligible(leg("A One", "REB", 8, min_l10=24.0), "sgm")
    assert not pb.eligible(leg("A One", min_l10=15.0), "single")
    assert not pb.eligible(leg("Luka Dončić", "PTS", 35), "single") and pb.eligible(leg("Luka Dončić", "PTS", 30), "single")
    assert not pb.eligible(leg("A One", kind="under"), "multi")


def test_builders_follow_the_shape_rules():
    legs = []
    for g, (t1, t2) in enumerate([("AAA", "BBB"), ("CCC", "DDD"), ("EEE", "FFF")]):
        for t in (t1, t2):
            for k in range(3):
                name = f"P{g}{t}{k}"
                legs.append(leg(name, "PTS", 20, 2.4, game=f"{t1}@{t2}", team=t, pid=100 * g + 10 * (t == t2) + k,
                                hr_season=0.5, n_season=12, hr_l8=0.5))
                legs.append(leg(name, "REB", 6, 1.45, game=f"{t1}@{t2}", team=t, pid=100 * g + 10 * (t == t2) + k,
                                hr_season=0.7, n_season=12, hr_l8=0.7))
    pb._annotate(legs, CAL)
    long3 = pb.build_longshot(legs, CAL, SAME, 3, top=3)
    assert long3 and all(len({x["game"] for x in slip}) == 3 for _, slip, _ in long3)
    sgm = pb.build_sgm(legs, CAL, SAME, "AAA@BBB", 4, top=3)
    assert sgm and all(len({x["pid"] for x in slip}) == 4 for _, slip, _ in sgm)
    assert all(max(sum(x["team"] == t for x in slip) for t in ("AAA", "BBB")) <= 2 for _, slip, _ in sgm)
    ladder = pb.build_ladder(legs, CAL, SAME, top=5)
    assert ladder and all(1.9 <= e["price"] <= 2.15 for _, _, e in ladder)
    big = pb.build_big(legs, CAL, SAME, target=100, top=1)
    assert big and big[0][2]["price"] >= 100 and len({x["team"] for x in big[0][1]}) == len(big[0][1])


def test_parse_legs_matches_saved_prices_and_accepts_overrides():
    legs = [leg("Davion Mitchell", "PTS", 15, 2.0, pid=7), leg("Davion Mitchell", "AST", 7.5, 1.74, pid=7, kind="over")]
    slip, problems = pb.parse_legs("Davion Mitchell PTS 15+; Mitchell AST o7.5 @1.80; Nobody PTS 10+", legs)
    assert [x["price"] for x in slip] == [2.0, 1.80] and slip[1]["kind"] == "over"
    assert len(problems) == 1 and "Nobody" in problems[0]


def test_unknown_teams_still_build_a_same_game_multi_and_a_big_ticket():
    legs = [leg(f"P{k}", "PTS", 20, 2.4, team=None, pid=k, hr_season=0.5, n_season=12, hr_l8=0.5) for k in range(6)]
    pb._annotate(legs, CAL)
    assert pb.build_sgm(legs, CAL, SAME, "AAA@BBB", 4, top=2)
    big = pb.build_big(legs, CAL, SAME, target=50, top=1)
    assert big and big[0][2]["price"] >= 50


def test_orthant_agrees_with_counting_simulated_nights_and_handles_tiny_chances():
    slip = [leg("A One", "PTS", 20, 2.0, pid=1), leg("A One", "REB", 8, 2.0, pid=1), leg("A Two", "PTS", 15, 1.6, pid=2),
            leg("B One", "PRA", 30, 2.4, pid=3, team="BBB", crank=1)]
    probs = [pb.fair_prob(x["price"], x["cls"], CAL) for x in slip]
    counted = pb.simulate(slip, probs, CAL, SAME, n=400000).all(1).mean()
    assert pb.evaluate(slip, cal=CAL, same=SAME, n=20000)["chance"] == pytest.approx(counted, rel=0.03)
    many = [leg(f"P{k}", "PTS", 20, 2.0, game=f"G{k}@H{k}", team=f"G{k}", pid=k) for k in range(30)]
    e = pb.evaluate(many, cal=CAL, same=SAME)
    assert e["chance"] == pytest.approx(0.46 ** 30, rel=1e-6)          # 30 independent legs: far too rare to count
    assert e["one_short"] == pytest.approx(30 * 0.46 ** 29 * 0.54, rel=1e-6)


def test_any_number_of_legs_and_play_mode():
    legs = []
    for g, (t1, t2) in enumerate([("AAA", "BBB"), ("CCC", "DDD")]):
        for t in (t1, t2):
            for k in range(6):
                pid = 100 * g + 10 * (t == t2) + k
                legs.append(leg(f"P{g}{t}{k}", "PTS", 20, 2.4, game=f"{t1}@{t2}", team=t, pid=pid,
                                hr_season=0.5, n_season=12, hr_l8=0.5, min_l10=30.0 if k < 3 else 22.0))
                legs.append(leg(f"P{g}{t}{k}", "PRA", 30, 1.5, game=f"{t1}@{t2}", team=t, pid=pid,
                                hr_season=0.6, n_season=12, hr_l8=0.6, min_l10=30.0 if k < 3 else 22.0))
    pb._annotate(legs, CAL)
    strict = pb.build_sgm(legs, CAL, SAME, "AAA@BBB", 10, top=1)
    assert len(strict[0][1]) == 6                                       # only six starters pass the rules
    play = pb.build_sgm(legs, CAL, SAME, "AAA@BBB", 10, top=1, loose=True)
    assert len(play[0][1]) == 10 and len({x["pid"] for x in play[0][1]}) == 10
    long4 = pb.build_longshot(legs, CAL, SAME, 4, top=1)                # two games, four legs: two a game, four teams
    assert len(long4[0][1]) == 4 and len({x["team"] for x in long4[0][1]}) == 4
    long9 = pb.build_longshot(legs, CAL, SAME, 9, top=1, loose=True)
    assert len(long9[0][1]) == 9 and len({x["pid"] for x in long9[0][1]}) == 9
    huge = pb.build_big(legs, CAL, SAME, target=5000, top=1)            # needs more legs than there are teams
    assert huge and huge[0][2]["price"] >= 5000


def test_alternatives_keep_the_slip_within_the_rules():
    legs = []
    for g, (t1, t2) in enumerate([("AAA", "BBB"), ("CCC", "DDD")]):
        for t in (t1, t2):
            for k in range(3):
                pid = 100 * g + 10 * (t == t2) + k
                for stat, th, price in (("PTS", 20, 2.4), ("REB", 8, 2.8), ("PRA", 30, 2.2)):
                    legs.append(leg(f"P{g}{t}{k}", stat, th, price, game=f"{t1}@{t2}", team=t, pid=pid,
                                    hr_season=0.5 + 0.01 * k, n_season=12, hr_l8=0.5))
    pb._annotate(legs, CAL)
    find = lambda name, stat: next(x for x in legs if x["player"] == name and x["stat"] == stat)  # noqa: E731
    slip = [find("P0AAA0", "PTS"), find("P1CCC0", "PTS")]
    alts = pb.alternatives(legs, slip, 0, lo=2.0, hi=3.6)
    assert alts and all(x["pid"] != slip[1]["pid"] and x["team"] != "CCC" for x in alts)       # not the other leg's team
    assert all(x["stat"] in pb.SINGLE for x in alts)                                           # no combos by the rules
    assert alts[0]["game"] == "AAA@BBB" and slip[0] not in alts                                # his game first
    assert any(x["pid"] == slip[0]["pid"] and x["stat"] == "REB" for x in alts)                # his other rung is offered
    assert any(x["stat"] == "PRA" for x in pb.alternatives(legs, slip, 0, lo=2.0, hi=3.6, loose=True))
    sgm = [find("P0AAA0", "PTS"), find("P0AAA1", "PTS"), find("P0BBB0", "PTS"), find("P0BBB1", "PTS")]
    same = pb.alternatives(legs, sgm, 0, purpose="sgm", lo=1.25, hi=3.6, same_game=True)
    assert same and all(x["game"] == "AAA@BBB" for x in same)
    assert all(x["team"] == "AAA" for x in same)                # BBB already has two of four: a third would break the cap


def test_reads_for_and_against_a_leg():
    star = leg("A One", "PTS", 20, crank=1, opp="BBB", h2h_ratio=1.21, h2h_ratio_n=7,
               out_names=["B Guard", "C Wing"], opp_out_names=["X Big", "Y Wing"], karma_bet_as="PRA/PA", karma_tags=["motor"])
    good, bad = pb.reads(star)
    assert any("1.21x" in g for g in good) and any("2 teammates out" in g for g in good)
    assert any("BBB missing 2" in g for g in good) and any("motor" in g for g in good)
    assert bad == ["karma: bet him as PRA/PA, not PTS"]
    assert pb.reads(dict(star, stat="PRA", cls="alt_combo"))[1] == []                      # the stat karma named
    role = leg("A Six", "PTS", 10, crank=6, opp="BBB", h2h_ratio=0.80, h2h_ratio_n=5, out_names=["B Guard"])
    good, bad = pb.reads(role)
    assert good == [] and any("only 0.80x" in b for b in bad)          # a role player gets no usage read; H2H veto
    assert pb.reads(leg("A One", crank=1, opp="BBB", h2h_ratio=1.3, h2h_ratio_n=3))[0] == []   # three games: too few
    under = leg("A One", "PTS", 20, kind="under", opp="BBB", h2h_ratio=1.21, h2h_ratio_n=7)
    assert pb.reads(under) == ([], ["v BBB: 1.21x his usual rate over 7 games"])               # a boost argues against the under


def test_reads_break_ties_between_equal_priced_legs():
    plain = leg("A One", "PTS", 20, 2.0, pid=1, hr_season=0.5, n_season=12, hr_l8=0.5)
    backed = leg("B Two", "PTS", 20, 2.0, pid=2, game="CCC@DDD", team="CCC", hr_season=0.5, n_season=12, hr_l8=0.5,
                 reads_for=["v DDD: 1.20x his usual rate over 6 games"])
    doubted = leg("C Three", "PTS", 20, 2.0, pid=3, game="EEE@FFF", team="EEE", hr_season=0.5, n_season=12, hr_l8=0.5,
                  reads_against=["v FFF: only 0.80x his usual rate over 5 games"])
    legs = pb._annotate([plain, backed, doubted], CAL)
    order = [slip[0]["player"] for _, slip, _ in pb.build_ladder(legs, CAL, SAME, top=3)]
    assert order == ["B Two", "A One", "C Three"]
