"""tools/pages/1_Parlay_builder.py through Streamlit's test runner, with made-up legs: the page opens,
builds slips, and checks a slip sent over from a builder."""
import os
import sys

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
sys.path.insert(0, TOOLS)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import parlay_builder as pb  # noqa: E402
import test_parlay_builder as fake  # noqa: E402

PAGE = os.path.join(TOOLS, "pages", "1_Parlay_builder.py")


def made_up_legs(date, with_actuals=False):
    legs = []
    for g, (t1, t2) in enumerate([("AAA", "BBB"), ("CCC", "DDD"), ("EEE", "FFF")]):
        for t in (t1, t2):
            for k in range(3):
                pid = 100 * g + 10 * (t == t2) + k
                for stat, th, price in (("PTS", 20, 2.0), ("REB", 6, 2.6), ("AST", 4, 1.45)):
                    legs.append(fake.leg(f"P{g}{t}{k}", stat, th, price, game=f"{t1}@{t2}", team=t, pid=pid,
                                         hr_season=0.5, n_season=12, hr_l8=0.5, date=date))
    return legs


@pytest.fixture
def page(monkeypatch, tmp_path):
    import streamlit as st
    st.cache_data.clear()
    monkeypatch.setattr(pb, "ROOT", str(tmp_path))
    monkeypatch.setattr(pb, "line_nights", lambda: ["2026-11-27"])
    monkeypatch.setattr(pb, "load_cal", lambda: dict(fake.CAL, fitted_on=["a", "b"]))
    monkeypatch.setattr(pb, "load_same", lambda: fake.SAME)
    monkeypatch.setattr(pb, "load_legs", made_up_legs)
    monkeypatch.setattr(pb, "has_slate", lambda date: False)
    return AppTest.from_file(PAGE, default_timeout=60)


def test_page_opens_and_builds_every_tab(page):
    at = page.run()
    assert not at.exception
    assert at.title[0].value == "Parlay builder"
    assert [t.label for t in at.tabs] == ["Check a slip", "Ladder", "Longshot", "Same-game multi", "Big ticket", "Bonus bet"]
    assert len(at.dataframe) >= 5                       # built slips are on the page
    assert any("expected return" in m.value for m in at.markdown)
    assert any("bonus bet comes back as" in m.value for m in at.markdown)
    assert any("injuries are NOT checked" in w.value for w in at.warning)      # no projected slate for the night


def test_add_a_leg_and_check_it(page):
    at = page.run()
    next(b for b in at.button if b.label == "Add leg").click().run()
    assert not at.exception
    assert len(at.session_state.pb_slip) == 1
    assert any("true chance" in m.value and "pb-card" in m.value for m in at.markdown)


def test_send_a_built_slip_to_the_checker(page):
    at = page.run()
    next(b for b in at.button if b.label == "Send to Check a slip").click().run()
    assert not at.exception and len(at.session_state.pb_slip) >= 1


def test_no_calibration_asks_to_measure_first(page, monkeypatch):
    monkeypatch.setattr(pb, "load_cal", lambda: {})
    at = page.run()
    assert not at.exception
    assert any("haven't been measured" in w.value for w in at.warning)


# ---- the phone page: the same builder, fed from data/parlay_feed.json.gz

PHONE_PAGE = os.path.join(os.path.dirname(TOOLS), "pages", "Parlay_builder.py")


@pytest.fixture
def phone(monkeypatch):
    import streamlit as st
    st.cache_data.clear()
    monkeypatch.setenv("BW_BUILDER_FEED", "1")           # the wrapper sets it; this restores it afterwards
    monkeypatch.setattr(pb, "load_same", lambda: fake.SAME)
    monkeypatch.setattr(pb, "load_legs", lambda *a, **k: pytest.fail("the phone page must not read journal/"))
    feed = {"made_at": "2026-11-27T07:05+11:00", "cal": dict(fake.CAL, fitted_on=["a", "b"]),
            "nights": {"2026-11-27": {"has_slate": True, "legs": made_up_legs("2026-11-27")}}}
    monkeypatch.setattr(pb, "load_feed", lambda *a, **k: feed)
    return AppTest.from_file(PHONE_PAGE, default_timeout=60)


def test_phone_page_builds_from_the_feed_without_the_mac(phone):
    at = phone.run()
    assert not at.exception
    assert at.title[0].value == "Parlay builder"
    assert len(at.dataframe) >= 5
    assert not any(b.label == "Save Sportsbet's lines now" for b in at.button)
    assert any("lines published 07:05 on 27/11" in c.value for c in at.caption)
    assert not at.warning                                                 # the night was projected


def test_phone_page_with_nothing_published_says_how(phone, monkeypatch):
    monkeypatch.setattr(pb, "load_feed", lambda *a, **k: {})
    at = phone.run()
    assert not at.exception
    assert any("publish_builder.sh" in i.value for i in at.info)


def test_feed_round_trips(tmp_path, monkeypatch):
    legs = pb._annotate(made_up_legs("2026-11-27"), fake.CAL)
    monkeypatch.setattr(pb, "load_legs", lambda date, with_actuals=False: legs)
    monkeypatch.setattr(pb, "has_slate", lambda date: True)
    monkeypatch.setattr(pb, "load_cal", lambda: fake.CAL)
    path = str(tmp_path / "feed.json.gz")
    pb.build_feed(dates=["2026-11-27"], path=path)
    feed = pb.load_feed(path)
    night = feed["nights"]["2026-11-27"]
    assert night["has_slate"] and len(night["legs"]) == len(legs)
    assert "ev" not in night["legs"][0] and night["legs"][0]["price"] == legs[0]["price"]
    assert feed["cal"]["tables"] == fake.CAL["tables"]


def test_swap_a_leg_reprices_the_slip_and_can_be_undone(page):
    at = page.run()
    before = at.dataframe[0].value["Leg"].tolist()
    box = next(s for s in at.selectbox if s.key == "pb_swapleg_lad0")
    box.select(0).run()
    to = next(s for s in at.selectbox if s.key == "pb_swapto_lad0_0")
    assert len(to.options) > 1
    to.select_index(1).run()
    assert not at.exception
    after = at.dataframe[0].value["Leg"].tolist()
    assert after != before
    assert any("changed from the built slip" in e.label for e in at.expander)
    next(b for b in at.button if b.key == "pb_undo_lad0").click().run()
    assert at.dataframe[0].value["Leg"].tolist() == before
