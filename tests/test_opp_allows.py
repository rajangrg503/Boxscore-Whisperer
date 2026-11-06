import importlib.util, os
spec = importlib.util.spec_from_file_location("opp_allows", os.path.join(os.path.dirname(__file__), "..", "tools", "opp_allows.py"))
oa = importlib.util.module_from_spec(spec); spec.loader.exec_module(oa)


def test_ratio_against_own_norm(monkeypatch):
    monkeypatch.setattr(oa, "kind", lambda pid, cache=None: "guard")
    # one player: 10 reb in 30 min three times (norm 1/3 per min), then 15 in 30 vs XYZ
    rows = [{"MIN": 30, "REB": 10, "MATCHUP": "AAA vs. BBB"}] * 3 + [{"MIN": 30, "REB": 15, "MATCHUP": "AAA @ XYZ"}]
    res = oa.allows("XYZ", "REB", L={1: rows})
    norm = 45 / 120
    assert res["guard"]["n"] == 1
    assert abs(res["guard"]["ratio"] - 15 / (norm * 30)) < 1e-9
    assert oa.allows("NOPE", "REB", L={1: rows}) == {}
