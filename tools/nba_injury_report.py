#!/usr/bin/env python3
"""The NBA's official injury report (the league's own PDF), parsed to rows.

    python3 tools/nba_injury_report.py                    # latest report
    python3 tools/nba_injury_report.py --team OKC MIA
    python3 tools/nba_injury_report.py --file report.pdf --json

WHY (22 Nov 2026)
ESPN's injury list is our gate (tools/availability.py), but it lags and
sometimes disagrees with the league's report (Missi: ESPN out, NBA report
probable, 15 Nov). The league publishes the report every 15 minutes at
    https://ak-static.cms.nba.com/referee/injury/Injury-Report_<YYYY-MM-DD>_<hh>_<mm><AM|PM>.pdf
(Eastern time; linked from https://www.nba.com/players/injury-report).

WHERE IT RUNS
On the Mac. The cloud container and the device VM are blocked from
ak-static.cms.nba.com (22 Nov: proxy 403 / connection failed). Chrome can
read it, which is how the parser below was checked against the real
12:45 AM ET 22 Nov report (88 rows, 6 pages).

HOW IT PARSES (no PDF library needed)
The PDF is GemBox-generated: each table cell is drawn as
    q  x y w h re  W* n  BT ... (word) Tj ... ET  Q
so the content streams are inflated with zlib, every cell becomes
(x, y, text), the header cells give each column's x, rows are cells within
5 points of each other on the same page, and Game Date / Time / Matchup /
Team are carried down from the row above (they print once per group).
"NOT YET SUBMITTED" rows are kept with player=None so a caller can see
which teams haven't filed.
"""
import argparse
import datetime
import json
import re
import sys
import zlib

BASE = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_{}.pdf"
HEADERS = ("Game Date", "Game Time", "Matchup", "Team", "Player Name", "Current Status", "Reason")
KEYS = dict(zip(HEADERS, ("date", "time", "matchup", "team", "player", "status", "reason")))
CARRY = ("date", "time", "matchup", "team")

_STREAM = re.compile(rb"/Length (\d+)[^>]*>>\s*stream\r?\n")
_CELL = re.compile(r"q\s+([\d.]+) ([\d.]+) ([\d.]+) ([\d.]+) re\s+W\* n\s+(?:BT([\s\S]*?)ET\s+)?Q")
_WORD = re.compile(r"\(((?:\\.|[^\\)])*)\)\s*Tj")

TEAM_ABBR = {
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN", "Charlotte Hornets": "CHA",
    "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE", "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN",
    "Detroit Pistons": "DET", "Golden State Warriors": "GSW", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "LA Clippers": "LAC", "Los Angeles Clippers": "LAC", "Los Angeles Lakers": "LAL", "Memphis Grizzlies": "MEM",
    "Miami Heat": "MIA", "Milwaukee Bucks": "MIL", "Minnesota Timberwolves": "MIN", "New Orleans Pelicans": "NOP",
    "New York Knicks": "NYK", "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL", "Philadelphia 76ers": "PHI",
    "Phoenix Suns": "PHX", "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC", "San Antonio Spurs": "SAS",
    "Toronto Raptors": "TOR", "Utah Jazz": "UTA", "Washington Wizards": "WAS",
}


def report_url(when):
    """URL of the report stamped at `when` (Eastern time, any minute; rounded down to 15)."""
    when = when.replace(minute=when.minute - when.minute % 15, second=0, microsecond=0)
    hour12 = when.hour % 12 or 12
    return BASE.format(f"{when:%Y-%m-%d}_{hour12:02d}_{when:%M}{'AM' if when.hour < 12 else 'PM'}")


def candidate_urls(now_et, hours=24):
    """Newest first: every 15-minute stamp back `hours` hours."""
    t = now_et.replace(minute=now_et.minute - now_et.minute % 15, second=0, microsecond=0)
    return [report_url(t - datetime.timedelta(minutes=15 * i)) for i in range(hours * 4 + 1)]


def page_streams(pdf):
    """Inflated content streams that draw text, in file order (one per page)."""
    out = []
    for m in _STREAM.finditer(pdf):
        start = m.end()
        raw = pdf[start:start + int(m.group(1))]
        try:
            text = zlib.decompress(raw).decode("latin-1")
        except zlib.error:
            continue
        if "Tj" in text:
            out.append(text)
    return out


def _join(words):
    s = ""
    for w in words:
        glue = not s or (s.endswith("-") and len(s.split(" ")[-1]) > 1)  # "Gilgeous-" wrapped, not a lone "-"
        s += w if glue else " " + w
    return s


def cells(stream):
    out = []
    for m in _CELL.finditer(stream):
        words = [re.sub(r"\\(.)", r"\1", w) for w in _WORD.findall(m.group(5) or "")]
        out.append((float(m.group(1)), float(m.group(2)), _join(words)))
    return out


def parse_streams(streams):
    rows, header_x, carry = [], None, {}
    for stream in streams:
        cs = cells(stream)
        hx = {t: x for x, _, t in cs if t in HEADERS}
        if len(hx) == len(HEADERS):
            header_x = hx
        if not header_x:
            continue
        cs = [c for c in cs if c[2] not in HEADERS]
        cs.sort(key=lambda c: c[1])
        page_rows, last_y = [], None
        for x, y, text in cs:
            if last_y is None or y - last_y > 5:
                page_rows.append({})
                last_y = y
            col = min(header_x, key=lambda h: abs(header_x[h] - x))
            if abs(header_x[col] - x) < 9 and text:
                page_rows[-1][KEYS[col]] = text
        for r in page_rows:
            for k in CARRY:
                if r.get(k):
                    carry[k] = r[k]
            if r.get("player") or r.get("reason") == "NOT YET SUBMITTED":
                row = {**carry, **r}
                row.setdefault("player", None)
                row.setdefault("status", "NOT YET SUBMITTED" if row["player"] is None else "")
                row["abbr"] = TEAM_ABBR.get(row.get("team", ""), row.get("team", ""))
                row["name"] = display_name(row["player"]) if row["player"] else None
                rows.append(row)
    return rows


def display_name(last_first):
    """'Jackson Jr., Jaren' -> 'Jaren Jackson Jr.'; 'Gilgeous-Alexander, Shai' -> 'Shai Gilgeous-Alexander'."""
    if "," not in last_first:
        return last_first
    last, first = last_first.split(",", 1)
    return f"{first.strip()} {last.strip()}"


def parse_pdf(pdf_bytes):
    return parse_streams(page_streams(pdf_bytes))


def fetch_latest(now_et=None, session=None, hours=24):
    """(url, rows) for the newest report found. Raises RuntimeError if none answers."""
    import requests
    s = session or requests.Session()
    if now_et is None:
        try:
            from zoneinfo import ZoneInfo
            now_et = datetime.datetime.now(ZoneInfo("America/New_York")).replace(tzinfo=None)
        except Exception:  # pragma: no cover
            now_et = datetime.datetime.utcnow() - datetime.timedelta(hours=5)
    errors = 0
    for url in candidate_urls(now_et, hours):
        try:
            r = s.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        except Exception:
            errors += 1
            if errors >= 3:
                raise RuntimeError("ak-static.cms.nba.com unreachable from this host (run it on the Mac)")
            continue
        if r.status_code == 200 and r.content[:4] == b"%PDF":
            return url, parse_pdf(r.content)
    raise RuntimeError(f"no official injury report found in the last {hours} hours")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", help="parse a saved PDF instead of fetching")
    ap.add_argument("--team", nargs="*", help="only these team abbreviations")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.file:
        url, rows = a.file, parse_pdf(open(a.file, "rb").read())
    else:
        try:
            url, rows = fetch_latest()
        except RuntimeError as exc:
            print(f"UNREACHABLE: {exc}", file=sys.stderr)
            return 2
    if a.team:
        want = {t.upper() for t in a.team}
        rows = [r for r in rows if r["abbr"] in want]
    if a.json:
        print(json.dumps({"source": url, "rows": rows}, separators=(",", ":")))
        return 0
    print(f"source: {url}  ({len(rows)} rows)")
    last = None
    for r in rows:
        if r["matchup"] != last:
            print(f"\n=== {r['matchup']}  {r['time']} ===")
            last = r["matchup"]
        print(f"   {r['abbr']:4s} {(r['name'] or '-'):28s} {r['status']:18s} {r.get('reason', '')[:50]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
