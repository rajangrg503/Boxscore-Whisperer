#!/usr/bin/env python3
"""Save Sportsbet's NBA player lines, so a pick can be checked against
where the market closed.

    python3 tools/line_snapshot.py                 # every listed game, now
    python3 tools/line_snapshot.py --closing 45    # only games tipping in the next 45 min

WHY
Karma, 1 Nov 2026: "usually you get best odds at this time ... and lines".
The test of that, and of picking skill generally, is closing-line value:
the line you took against the line just before tip. Ajay Mitchell's PRA
went 25.5 -> 26.5 between two slips on 31 Oct -- the market moved to the
read. Win rate needs months of results to mean anything; whether you
beat the close shows up in weeks.

WHAT IS SAVED
journal/lines/<US date>/<HHMMSS>.json, one file per snapshot, with every
player over/under market (line and both prices) and every "To Record N+"
market (price per player) in the points, rebounds, assists, threes and
combo groupings. journal/ data is gitignored: bookmaker lines never go
into the repository or the public app.

WHERE IT RUNS
On the Mac (the Journal's button, or tools/install_lines_job.sh's
launchd job every 20 minutes). Sportsbet answers a home connection; the
automated hosts can't reach it.
"""
import argparse
import datetime
import json
import os
import re
import sys
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import daily_journal as dj  # noqa: E402
import picks as pk  # noqa: E402

BASE = "https://www.sportsbet.com.au/apigw/sportsbook-sports/Sportsbook/Sports"
NBA_COMPETITION = 6927
# Market groupings on an NBA event page (found 1 Nov 2026): points,
# rebounds, assists, reb+ast, threes, pts+reb+ast, pts+reb, pts+ast.
GROUPINGS = (567, 568, 569, 570, 710, 782, 783, 784)
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
           "Accept": "application/json"}
TIMEOUT = 20
NEW_YORK = ZoneInfo("America/New_York")
SYDNEY = ZoneInfo("Australia/Sydney")
LINES_DIR = os.path.join(dj.JOURNAL_DIR, "lines")
_ALT = re.compile(r"^to record (\d{1,3})\+\s*(.+)$", re.I)


class Unreachable(RuntimeError):
    pass


def _get(path):
    try:
        r = requests.get(f"{BASE}/{path}", headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as exc:                          # noqa: BLE001 -- reported, not hidden
        raise Unreachable(f"{type(exc).__name__}: {str(exc)[:120]}") from exc


def _game(name):
    """'Boston Celtics At Orlando Magic' -> 'BOS@ORL'."""
    parts = re.split(r"\s+at\s+", name, maxsplit=1, flags=re.I)
    if len(parts) != 2:
        return None
    a, h = pk._team_in(parts[0]), pk._team_in(parts[1])
    return f"{a}@{h}" if a and h else None


def events():
    """[{id, name, game, start (UTC ISO), us_date}] for listed NBA games."""
    payload = _get(f"Competitions/{NBA_COMPETITION}?displayType=default&eventFilter=matches")
    out = []
    for e in payload.get("events") or []:
        start = datetime.datetime.fromtimestamp(int(e["startTime"]), datetime.timezone.utc)
        out.append({"id": e["id"], "name": e["name"], "game": _game(e["name"]),
                    "start": start.isoformat(),
                    "us_date": start.astimezone(NEW_YORK).date().isoformat()})
    return out


def rows_for(event):
    """Every player line on one event, as flat rows."""
    rows = []
    for g in GROUPINGS:
        try:
            markets = _get(f"Events/{event['id']}/MarketGroupings/{g}/Markets")
        except Unreachable:
            continue                                   # a grouping can be missing
        for m in markets or []:
            name = m.get("name") or ""
            if re.search(r"\balt\b|qtr|quarter|half", name, re.I):
                continue
            alt = _ALT.match(name)
            if alt:
                stat = pk._stat_from(alt.group(2))
                if not stat:
                    continue
                for s in m.get("selections") or []:
                    rows.append({"game": event["game"], "player": s.get("name"), "stat": stat,
                                 "kind": "alt", "threshold": int(alt.group(1)),
                                 "price": (s.get("price") or {}).get("winPrice")})
                continue
            player, _, what = name.partition(" - ")
            stat = pk._stat_from(what) if what else None
            sels = m.get("selections") or []
            over = next((s for s in sels if s.get("name", "").endswith(" Over")), None)
            under = next((s for s in sels if s.get("name", "").endswith(" Under")), None)
            if not stat or not over or not under:
                continue
            try:
                line = abs(float(over.get("unformattedHandicap") or over.get("displayHandicap")))
            except (TypeError, ValueError):
                continue
            rows.append({"game": event["game"], "player": player.strip(), "stat": stat,
                         "kind": "ou", "line": line,
                         "over": (over.get("price") or {}).get("winPrice"),
                         "under": (under.get("price") or {}).get("winPrice")})
    return rows


def snapshot(closing_within=None, now=None, out_dir=None):
    """Save one snapshot per US date of the games taken. Returns
    {us_date: path}. closing_within=N only takes games tipping in the next
    N minutes -- the closing line -- and never a game already started."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    out_dir = out_dir or LINES_DIR
    taken = {}
    for e in events():
        start = datetime.datetime.fromisoformat(e["start"])
        if start <= now:
            continue                                   # live lines aren't closing lines
        if closing_within is not None and start - now > datetime.timedelta(minutes=closing_within):
            continue
        taken.setdefault(e["us_date"], []).append(dict(e, rows=rows_for(e)))
    paths = {}
    stamp = now.astimezone(SYDNEY)
    for day, evs in taken.items():
        folder = os.path.join(out_dir, day)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, stamp.strftime("%H%M%S") + ".json")
        with open(path, "w") as f:
            json.dump({"taken_at": stamp.isoformat(timespec="seconds"),
                       "closing": closing_within is not None, "events": evs}, f)
        paths[day] = path
    return paths


# ------------------------------------------------------------------ reading back

def _snapshots(us_date, out_dir=None):
    folder = os.path.join(out_dir or LINES_DIR, us_date)
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith(".json"))
    except OSError:
        return []
    out = []
    for n in names:
        try:
            with open(os.path.join(folder, n)) as f:
                out.append(json.load(f))
        except (OSError, ValueError):
            continue
    return out


def closing_line(leg, out_dir=None):
    """The last pre-tip over/under line for a leg's player and stat, plus the
    last price of its own "N+" market when it has one: {line, over, under,
    alt_price, taken_at} or None."""
    if not leg.get("us_date") or pk.is_team(leg):
        return None
    want = pk._norm(leg.get("player") or "")
    best = None
    for snap in _snapshots(leg["us_date"], out_dir):
        for e in snap.get("events") or []:
            if leg.get("game") and e.get("game") and e["game"] != leg["game"]:
                continue
            if datetime.datetime.fromisoformat(snap["taken_at"]) >= datetime.datetime.fromisoformat(e["start"]):
                continue
            found = {}
            for r in e.get("rows") or []:
                if r["stat"] != leg["stat"] or pk._norm(r.get("player") or "") != want:
                    continue
                if r["kind"] == "ou":
                    found.update(line=r["line"], over=r["over"], under=r["under"])
                elif leg.get("line") is not None and r["threshold"] == int(leg["line"] + 0.5):
                    found["alt_price"] = r["price"]
            if found and (best is None or snap["taken_at"] >= best["taken_at"]):
                best = dict(found, taken_at=snap["taken_at"])
    return best


def clv(leg, close):
    """How the close moved against the line you took, in your favour when
    positive: points of line for over/under, and the price you got over
    the closing price for an N+ leg priced on its own."""
    if not close or close.get("line") is None or leg.get("line") is None:
        return None
    under = leg.get("side") == "under"
    out = {"close": close["line"],
           "line": round((leg["line"] - close["line"]) if under else (close["line"] - leg["line"]), 1)}
    if leg.get("price") and close.get("alt_price"):
        out["price"] = round(leg["price"] / close["alt_price"] - 1, 3)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--closing", type=int, default=None, metavar="MIN",
                    help="only games tipping within this many minutes")
    args = ap.parse_args(argv)
    try:
        paths = snapshot(args.closing)
    except Unreachable as exc:
        print(f"Sportsbet unreachable: {exc}")
        return 1
    if not paths:
        print("no games in the window" if args.closing else "no upcoming games listed")
    for day, path in paths.items():
        print(f"{day}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
