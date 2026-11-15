"""Tests for tools/pbp_patterns.py (pattern buckets from NBA.com play-by-play)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import pbp_patterns as pp  # noqa: E402


def shot(team, kind="2pt", sub="Jump Shot", desc="", dist=15, made=True, quals=(), clock="PT10M00.00S",
         period=1, assist=None, name="A. Shooter"):
    a = {"isFieldGoal": 1, "teamTricode": team, "actionType": kind, "subType": sub, "descriptor": desc,
         "shotDistance": dist, "shotResult": "Made" if made else "Missed", "qualifiers": list(quals),
         "clock": clock, "period": period, "playerNameI": name}
    if assist:
        a["assistPersonId"] = 1
        a["assistPlayerNameInitial"] = assist
    return a


@pytest.mark.parametrize("action,want", [
    (shot("A", sub="Layup", desc="putback", dist=1), "putback"),
    (shot("A", sub="Layup", desc="tip", dist=1), "putback"),
    (shot("A", sub="Layup", desc="driving", dist=2, quals=["fastbreak"]), "transition"),
    (shot("A", sub="DUNK", desc="alley-oop", dist=1), "cut"),
    (shot("A", sub="Layup", desc="cutting", dist=2), "cut"),
    (shot("A", sub="Layup", desc="driving", dist=2), "drive"),
    (shot("A", sub="Jump Shot", desc="driving floating", dist=7), "drive"),
    (shot("A", sub="Layup", desc="running", dist=3), "rim_other"),
    (shot("A", sub="Hook", desc="", dist=4), "rim_other"),
    (shot("A", sub="Jump Shot", desc="pullup", dist=16), "pullup2"),
    (shot("A", sub="Jump Shot", desc="step back", dist=18), "pullup2"),
    (shot("A", sub="Jump Shot", desc="turnaround fadeaway", dist=12), "mid_other"),
    (shot("A", kind="3pt", desc="pullup", dist=27), "three_dribble"),
    (shot("A", kind="3pt", desc="", dist=24), "three_set"),
    (shot("A", kind="3pt", desc="", dist=27), "three_deep"),
])
def test_bucket(action, want):
    assert pp.bucket(action) == want


def test_every_bucket_has_a_label():
    assert set(pp.BUCKETS) == set(pp.LABEL)


def test_clock_seconds():
    assert pp.clock_seconds({"period": 1, "clock": "PT12M00.00S"}) == 0
    assert pp.clock_seconds({"period": 2, "clock": "PT06M00.00S"}) == 720 + 360
    assert pp.clock_seconds({"period": 5, "clock": "PT05M00.00S"}) == 2880


def test_streaks_counts_three_makes_inside_three_minutes_without_overlap():
    assert pp.streaks([0, 60, 120]) == 1
    assert pp.streaks([0, 60, 200]) == 0
    assert pp.streaks([0, 30, 60, 90, 120, 150]) == 2
    assert pp.streaks([0, 30, 60, 90]) == 1
    assert pp.streaks([]) == 0


def test_aggregate_counts_offence_defence_assists_and_streaks():
    acts = [
        shot("AAA", kind="3pt", dist=27, clock="PT06M11.00S", assist="T. Passer", name="J. Big"),
        shot("AAA", kind="3pt", dist=27, clock="PT05M30.00S", made=False, name="J. Big"),
        shot("AAA", kind="3pt", dist=27, clock="PT05M01.00S", assist="Q. Guard", name="J. Big"),
        shot("AAA", kind="3pt", dist=27, clock="PT04M00.00S", assist="T. Passer", name="J. Big"),
        shot("BBB", sub="Layup", desc="driving", dist=2, clock="PT03M00.00S"),
        {"isFieldGoal": 0, "teamTricode": "BBB", "actionType": "rebound"},
    ]
    data = pp.aggregate([("AAA", "BBB", acts)], "test")
    i = pp.BUCKETS.index("three_deep")
    assert data["teams"]["AAA"]["o"][i] == [4, 3, 9, 3]
    assert data["teams"]["BBB"]["d"][i] == [4, 3, 9]
    assert data["teams"]["AAA"]["so"][i] == 1
    assert data["teams"]["BBB"]["sd"][i] == 1
    assert data["teams"]["BBB"]["o"][pp.BUCKETS.index("drive")] == [1, 1, 2, 0]
    assert data["teams"]["AAA"]["pairs"][0] == ["Passer>Big|3", 2]
    assert data["teams"]["AAA"]["g"] == 1 and data["games"] == 1


def _team(o_fga, d_pts_per_shot, streak_allowed=0, d_fga=None):
    """A team that takes `o_fga[bucket]` shots, allows `d_fga[bucket]` shots (default 100)
    and gives up `d_pts_per_shot[bucket]` on them."""
    d_fga = d_fga or {}
    o = [[o_fga.get(b, 10), 5, 10, 3] for b in pp.BUCKETS]
    d = [[d_fga.get(b, 100), 50, int(d_fga.get(b, 100) * d_pts_per_shot.get(b, 1.0))] for b in pp.BUCKETS]
    sd = [streak_allowed if b == "cut" else 0 for b in pp.BUCKETS]
    return {"g": 10, "o": o, "d": d, "so": [0] * len(pp.BUCKETS), "sd": sd, "pairs": []}


def _league():
    teams = {"T%02d" % i: _team({}, {"cut": 1.0 + i / 100, "drive": 1.0 + i / 100}, d_fga={"cut": 100 + i})
             for i in range(27)}
    teams["NOCUT"] = _team({"cut": 1, "drive": 1}, {})                     # hardly ever cuts or drives
    teams["CUT"] = _team({"cut": 60, "drive": 60}, {})                     # leans on cuts and drives
    # lets teams take the most cuts, gives up the most per shot on cuts and drives, in streaks
    teams["SOFT"] = _team({}, {"cut": 2.0, "drive": 2.0}, streak_allowed=30, d_fga={"cut": 300})
    # takes cuts away and gives up the least per shot on drives
    teams["WALL"] = _team({}, {"cut": 0.2, "drive": 0.2}, d_fga={"cut": 20})
    return {"season": "t", "games": 150, "buckets": list(pp.BUCKETS), "teams": teams}


def test_matchup_flags_open_shut_feeds_dead_end_and_no_answer():
    data = _league()
    rows = {r["bucket"]: r for r in pp.matchup(data, "CUT", "SOFT")}
    assert rows["cut"]["o_share_rk"] == 1
    assert rows["cut"]["flag"] == "OPEN + NO ANSWER"          # cuts: volume flag only
    assert "FEEDS" in rows["drive"]["flag"]                   # drives keep the points-per-shot flag
    rows = {r["bucket"]: r for r in pp.matchup(data, "CUT", "WALL")}
    assert rows["cut"]["flag"] == "SHUT"
    assert "DEAD END" in rows["drive"]["flag"]
    # a team that does not lean on a shot type gets no flag, whatever the defence allows
    rows = {r["bucket"]: r for r in pp.matchup(data, "NOCUT", "SOFT")}
    assert rows["cut"]["flag"] == "" and rows["drive"]["flag"] == ""


def test_points_per_shot_flags_only_on_drives_and_mid_range():
    assert set(pp.PPS_BUCKETS) == {"drive", "mid_other"}
    data = _league()
    for de in ("SOFT", "WALL"):
        for r in pp.matchup(data, "CUT", de):
            if r["bucket"] not in pp.PPS_BUCKETS:
                assert "FEEDS" not in r["flag"] and "DEAD END" not in r["flag"]


def test_import_checks_the_sum_and_writes_a_file(tmp_path):
    data = _league()
    raw = json.dumps(data, separators=(",", ":"))
    p = tmp_path / "raw.txt"
    p.write_text("\n".join(raw[i:i + 900] for i in range(0, len(raw), 900)), encoding="utf-8")
    with pytest.raises(ValueError):
        pp.import_raw(str(p), "2026-11-14", expect_sum=1, out_dir=str(tmp_path))
    out = pp.import_raw(str(p), "2026-11-14", expect_sum=pp.checksum(raw), out_dir=str(tmp_path))
    saved = pp.load(out)
    assert saved["asof"] == "2026-11-14" and len(saved["teams"]) == 31
    assert "CUT offence against WALL defence" in pp.card(saved, "CUT", "WALL")


def test_validate_rejects_a_different_bucket_list():
    data = _league()
    data["buckets"] = data["buckets"][:-1]
    with pytest.raises(ValueError):
        pp.validate(data)
