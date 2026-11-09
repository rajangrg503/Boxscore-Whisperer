import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import defender_matchups as dm  # noqa: E402

LINE = "1629130|1610612747|4200.6,919,137,44,311,689,107|1629656~Grimes~4~46.6~11~2~0~3~5~2;0~rest~12~31.8~11~2~0~3~8~4"


def test_parse_line_keeps_totals_and_rows():
    e = dm.parse_line(LINE, "2025-26", "2026-11-09")
    assert e["pid"] == 1629130 and e["opp_tid"] == 1610612747
    assert e["totals"]["poss"] == 4200.6 and e["totals"]["pts"] == 919
    assert [r[1] for r in e["vs"]] == ["Grimes", "rest"]
    assert e["vs"][0][3] == 46.6 and e["vs"][0][12] == 2.0


def test_summarise_shrinks_toward_one():
    s = dm.summarise(dm.parse_line(LINE, "2025-26"))
    raw = (22 / 78.4) / (919 / 4200.6)
    assert s["poss_vs"] == 78.4
    assert 1.0 < s["pts_factor"] < raw          # pulled toward 1 by the prior
    assert s["defenders"][0]["name"] == "Grimes"


def test_flags():
    base = {"too_few": False, "pts_factor": 1.0}
    assert dm.flag(dict(base, pts_factor=0.80)) == "struggles"
    assert dm.flag(dict(base, pts_factor=1.20)) == "feasts"
    assert dm.flag(base) == ""
    assert dm.flag(dict(base, too_few=True)) == "thin"
    assert dm.flag(None) == "no data"


def test_team_id_aliases():
    assert dm.team_id("WSH") == dm.team_id("WAS") == 1610612764
    assert dm.team_id(1610612765) == 1610612765
