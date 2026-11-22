"""Fixture built from the real 22 Nov 2026 12:45 AM ET report's content stream
(GemBox cells: `q x y w h re W* n BT ... Tj ET Q`)."""
import datetime
import importlib.util
import os
import zlib

spec = importlib.util.spec_from_file_location(
    "nba_injury_report", os.path.join(os.path.dirname(__file__), "..", "tools", "nba_injury_report.py"))
nir = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nir)

COLX = [19.1488, 115.5871, 195.9523, 260.2445, 420.975, 581.7055, 662.0708]


def cell(x, y, words):
    body = ""
    if words:
        body = "BT\n0 g\n" + f"{x + 5} {y + 15} Td ({words[0]}) Tj\n" + "".join(
            f"20 0 Td ({w}) Tj\n" for w in words[1:]) + "ET\n"
    return f"q\n{x} {y} 80 22.0381 re\nW* n\n{body}Q\n"


def row(y, texts, jitter=0.0):
    return "".join(cell(x, y + (jitter if i == 4 else 0), t.split() if t else [])
                   for i, (x, t) in enumerate(zip(COLX, texts)))


def header(y=100.4381):
    return "".join(cell(x, y, h.split()) for x, h in zip(COLX, nir.HEADERS))


def pdf(*pages):
    out = b"%PDF-1.4\n"
    for i, p in enumerate(pages):
        z = zlib.compress(p.encode("latin-1"))
        out += f"{i + 4} 0 obj\n<</Length {len(z)}/Filter/FlateDecode>>\nstream\n".encode() + z + b"\nendstream\nendobj\n"
    return out


PAGE1 = (
    "BT\n/F0 -22 Tf\n288 62 Td (Injury) Tj\nET\n" + header()
    + row(121.734, ["11/22/2026", "03:30 \\(ET\\)", "DET@ATL", "Detroit Pistons", "Cunningham, Cade", "Probable",
                    "Injury/Illness - Right Knee Soreness"])
    + row(143.7721, ["", "", "", "", "Duren, Jalen", "Out", "Injury/Illness - Right Hamstring Strain"])
    + row(166.0, ["", "", "", "Atlanta Hawks", "Jackson Jr., Jaren", "Questionable", "Rest"], jitter=1.0)
    + row(407.0094, ["", "07:00 \\(ET\\)", "DAL@UTA", "Dallas Mavericks", "", "", "NOT YET SUBMITTED"])
)
PAGE2 = header() + row(121.734, ["", "", "OKC@MIA", "Oklahoma City Thunder", "Gilgeous- Alexander, Shai",
                                 "Questionable", "Injury/Illness - Left Ankle Sprain"])


def test_parse_real_layout():
    rows = nir.parse_pdf(pdf(PAGE1, PAGE2))
    assert len(rows) == 5
    a = rows[0]
    assert (a["date"], a["time"], a["matchup"], a["abbr"]) == ("11/22/2026", "03:30 (ET)", "DET@ATL", "DET")
    assert a["name"] == "Cade Cunningham" and a["status"] == "Probable"
    assert a["reason"] == "Injury/Illness - Right Knee Soreness"
    # carried down: date/time/matchup/team print once per group
    assert rows[1]["name"] == "Jalen Duren" and rows[1]["abbr"] == "DET" and rows[1]["time"] == "03:30 (ET)"
    # team changes mid-game; a 1-point rect offset stays in the same row; Jr. handled
    assert rows[2]["abbr"] == "ATL" and rows[2]["name"] == "Jaren Jackson Jr." and rows[2]["status"] == "Questionable"
    # not yet submitted keeps the team, no player
    assert rows[3]["player"] is None and rows[3]["status"] == "NOT YET SUBMITTED" and rows[3]["abbr"] == "DAL"
    # page 2: header repeated, date carried across pages, wrapped hyphenated surname rejoined
    assert rows[4]["date"] == "11/22/2026" and rows[4]["abbr"] == "OKC"
    assert rows[4]["name"] == "Shai Gilgeous-Alexander"


def test_report_url_and_candidates():
    t = datetime.datetime(2026, 11, 22, 0, 52)
    assert nir.report_url(t).endswith("Injury-Report_2026-11-22_12_45AM.pdf")
    assert nir.report_url(datetime.datetime(2026, 11, 21, 17, 0)).endswith("2026-11-21_05_00PM.pdf")
    assert nir.report_url(datetime.datetime(2026, 11, 21, 12, 14)).endswith("2026-11-21_12_00PM.pdf")
    c = nir.candidate_urls(t, hours=1)
    assert len(c) == 5 and c[0].endswith("12_45AM.pdf") and c[-1].endswith("2026-11-21_11_45PM.pdf")


def test_bad_stream_is_skipped():
    blob = pdf(PAGE1) + b"9 0 obj\n<</Length 5>>\nstream\nnotzl\nendstream\n"
    assert len(nir.parse_pdf(blob)) == 4


def test_merge_official_into_espn_slate():
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from tools import availability as av
    games = [{"away": "OKC", "home": "MIA", "injuries": [
        {"team": "OKC", "name": "Shai Gilgeous-Alexander", "status": "Day-To-Day", "detail": "Ankle",
         "player_id": 1, "matched": True, "severity": "questionable"},
        {"team": "MIA", "name": "Giannis Antetokounmpo", "status": "Questionable", "detail": "Knee",
         "player_id": 2, "matched": True, "severity": "questionable"}]}]
    official = [
        {"abbr": "OKC", "name": "Shai Gilgeous-Alexander", "player": "Gilgeous-Alexander, Shai", "status": "Out"},
        {"abbr": "OKC", "name": "Jaylin Williams", "player": "Williams, Jaylin", "status": "Out", "reason": "Rest"},
        {"abbr": "MIA", "name": None, "player": None, "status": "NOT YET SUBMITTED"},
    ]
    av.merge_official(games, official)
    g = games[0]
    shai = g["injuries"][0]
    assert shai["official"] == "Out"
    assert g["injuries"][1]["official"] is None               # Giannis not on the NBA report
    extra = [r for r in g["injuries"] if r.get("source") == "nba_report"]
    assert [r["name"] for r in extra] == ["Jaylin Williams"] and extra[0]["severity"] == "out"
    assert "Jaylin Williams" in [r["name"] for r in g["out"]]  # NBA-only scratch still gates
    assert g["not_submitted"] == ["MIA"]
