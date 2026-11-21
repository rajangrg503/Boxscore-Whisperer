"""tools/prop_context.py: display-only H2H, ladder and Box Set helpers."""
import tools.prop_context as pc


def _row(date, opp, pts, reb=5, ast=3, mins=30, home=True):
    m = f"OKC vs. {opp}" if home else f"OKC @ {opp}"
    return {"GAME_DATE": date, "MATCHUP": m, "PTS": pts, "REB": reb, "AST": ast, "MIN": mins}


def test_norm_folds_dots_accents_and_suffixes():
    assert pc.norm("P.J. Washington") == pc.norm("PJ Washington") == "pj washington"
    assert pc.norm("Nikola Jokić") == "nikola jokic"
    assert pc.norm("Michael Porter Jr.") == "michael porter"


def test_h2h_counts_all_and_since_roster_change():
    rows = [_row("Nov 18, 2026", "ATL", 30), _row("Mar 01, 2026", "ATL", 10),
            _row("Jan 05, 2026", "MIA", 40), _row("Oct 30, 2025", "ATL", 25)]
    h = pc.h2h_from_rows(rows, "ATL", "PTS", 19.5, "over", since="2026-10-20")
    assert (h["hits"], h["n"]) == (2, 3)
    assert (h["since_hits"], h["since_n"]) == (1, 1)
    assert pc.h2h_text(h) == "H2H 2/3 (since 10-20: 1/1)"


def test_h2h_alias_and_combo_stat():
    rows = [_row("Nov 18, 2026", "NOP", 10, 10, 10)]
    h = pc.h2h_from_rows(rows, "NO", "PRA", 29.5)
    assert (h["hits"], h["n"]) == (1, 1)


def test_rates_and_conservative_ignore_small_h2h():
    rows = [_row(f"Nov {d:02d}, 2026", "ATL", p) for d, p in zip(range(20, 8, -1), [20] * 10 + [5, 5])]
    r = pc.rates(rows, "PTS", 9.5)
    assert r["last10"] == 1.0 and round(r["season"], 3) == round(10 / 12, 3)
    assert pc.conservative(r) == r["season"]
    assert pc.conservative(r, {"n": 3, "hits": 0}) == r["season"]      # n < 5: ignored
    assert pc.conservative(r, {"n": 5, "hits": 1}) == 0.2


def test_alt_rung_becomes_line_minus_half():
    assert pc.legs_for_row({"kind": "alt", "stat": "PTS", "threshold": 20, "price": 1.5}) == ("PTS", 19.5, "over", 1.5)
    assert pc.legs_for_row({"kind": "ou", "stat": "REB", "line": 7.5, "over": 1.8}) == ("REB", 7.5, "over", 1.8)
