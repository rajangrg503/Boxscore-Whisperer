#!/usr/bin/env python3
"""Who is actually playing tonight. Run this BEFORE anything else.

    python3 tools/availability.py
    python3 tools/availability.py --date 2026-10-31
    python3 tools/availability.py --date 2026-10-31 --json --out out.json
    python3 tools/availability.py --check "Alperen Sengun" "Anthony Edwards"

WHY THIS EXISTS
On 30 Oct 2026 three of six picks on the card were justified by WHO ELSE
was on the floor, and in all three cases that teammate did not play:

    Maxey OVER    "Jaylen Brown played all five"     Brown  - out, knee
    Gobert UNDER  "Edwards played four of five"      Edwards- out, knee
    Durant UNDER  "Sengun has been the focal point"  Sengun - DNP, back

The teammate check had been run against the previous five games and
never against the game being bet. Two of the three won anyway, which is
worse than losing: the reasoning was void and the scoreboard hid it.

tools/matchup_holes.py has the same disease in a different organ -- it
counts Jalen Williams at 31.7 mpg toward Oklahoma City's rotation while
he is out for the season. So this is not an optional enrichment. It is a
gate, and it runs first.

WHERE IT RUNS
ESPN's site API, which is reachable from a normal home connection and
NOT from this project's automated hosts. Measured:

    cloud container   curl -> connection refused by the proxy (31 Oct)
    device_bash VM    curl -> rc 56, connection reset        (31 Oct)
    a browser on the Mac                             -> 200
    GitHub runners    not tested; stats.nba.com already fails there

So this is a LOCAL tool, exactly like batch_cache_rosters.py and
tools/refresh_current_season.py: run it on the Mac, write the JSON, and
let the automated pieces read the file. It does not silently return an
empty list when the host is unreachable -- it fails loudly, because an
availability gate that reports "nobody is injured" after a failed fetch
is more dangerous than no gate at all.

NAME MATCHING
ESPN names are folded through engine.players.fold_diacritics and matched
against the cached rosters, so a caller holding NBA PLAYER_IDs gets ids
back. An unmatched name is REPORTED, never dropped: a player this cannot
resolve is a player the gate cannot protect, and the caller has to know
that rather than receive a quietly shorter list.
"""
import argparse
import datetime
import json
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.cache import read_payload                          # noqa: E402
from engine.players import fold_diacritics                     # noqa: E402
from engine.team_ids import TEAM_ID_BY_ABBR                    # noqa: E402

SCOREBOARD = ("https://site.api.espn.com/apis/site/v2/sports/"
              "basketball/nba/scoreboard?dates={date}")
SUMMARY = ("https://site.api.espn.com/apis/site/v2/sports/"
           "basketball/nba/summary?event={event}")
ROSTER = ("https://site.api.espn.com/apis/site/v2/sports/"
          "basketball/nba/teams/{team}/roster")
TIMEOUT = 20

# ESPN abbreviations that differ from the NBA's. Same table as
# tools/build_crosswalk.py; kept local so neither tool depends on the
# other being present.
ESPN_TO_NBA = {"GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP",
               "UTAH": "UTA", "WSH": "WAS", "PHO": "PHX"}

# Statuses that void a pick outright, versus ones that only warn.
OUT_STATUSES = {"out", "injured", "suspension", "suspended"}
DOUBTFUL_STATUSES = {"doubtful"}


class Unreachable(RuntimeError):
    """ESPN did not answer. Never swallowed -- see the module docstring."""


def norm(name):
    folded = fold_diacritics(str(name)).lower()
    folded = "".join(c if (c.isalnum() or c.isspace()) else " " for c in folded)
    parts = [p for p in folded.split()
             if p and p not in {"jr", "sr", "ii", "iii", "iv", "v"}]
    return " ".join(parts)


def roster_index():
    """{folded name: (name, player_id, team abbr)} from the cached rosters."""
    index = {}
    for abbr, team_id in TEAM_ID_BY_ABBR.items():
        payload = read_payload(f"roster_{team_id}")
        if payload is None:
            continue
        for row in (payload.get("data") or []):
            index[norm(row["PLAYER"])] = (row["PLAYER"], int(row["PLAYER_ID"]), abbr)
    return index


def fetch(url):
    try:
        response = requests.get(url, timeout=TIMEOUT,
                                headers={"User-Agent": "boxscore-whisperer"})
        response.raise_for_status()
        return response.json()
    except Exception as exc:                      # noqa: BLE001 -- reported, not hidden
        raise Unreachable(f"{type(exc).__name__}: {str(exc)[:120]}\n{url}") from exc


def slate(date):
    """[(event_id, away_abbr, home_abbr, tipoff)] for a YYYYMMDD date."""
    payload = fetch(SCOREBOARD.format(date=date))
    games = []
    for event in payload.get("events", []):
        comp = (event.get("competitions") or [{}])[0]
        sides = {c.get("homeAway"): c for c in comp.get("competitors", [])}
        away = (sides.get("away", {}).get("team") or {}).get("abbreviation")
        home = (sides.get("home", {}).get("team") or {}).get("abbreviation")
        games.append({
            "event": event.get("id"),
            "away": ESPN_TO_NBA.get(away, away),
            "home": ESPN_TO_NBA.get(home, home),
            "espn_ids": {ESPN_TO_NBA.get(away, away): (sides.get("away", {}).get("team") or {}).get("id"),
                         ESPN_TO_NBA.get(home, home): (sides.get("home", {}).get("team") or {}).get("id")},
            "tipoff": event.get("date"),
            "status": ((comp.get("status") or {}).get("type") or {}).get("detail"),
        })
    return games


def injuries_for(event_id, index):
    """[{team, name, player_id, status, detail, matched}] for one game."""
    payload = fetch(SUMMARY.format(event=event_id))
    rows = []
    for block in payload.get("injuries", []):
        abbr = (block.get("team") or {}).get("abbreviation")
        abbr = ESPN_TO_NBA.get(abbr, abbr)
        for entry in block.get("injuries", []):
            name = ((entry.get("athlete") or {}).get("displayName") or "").strip()
            hit = index.get(norm(name))
            rows.append({
                "team": abbr,
                "name": name,
                "nba_name": hit[0] if hit else None,
                "player_id": hit[1] if hit else None,
                "matched": hit is not None,
                "status": (entry.get("status") or "").strip(),
                "detail": ((entry.get("details") or {}).get("type") or "").strip(),
                "return_date": (entry.get("details") or {}).get("returnDate"),
                "updated": entry.get("date"),
            })
    return rows


def season_of(date):
    """'2026-27' for any YYYYMMDD from October 2026 to September 2027."""
    y, m = int(str(date)[:4]), int(str(date)[4:6])
    start = y if m >= 10 else y - 1
    return f"{start}-{(start + 1) % 100:02d}"


def last_played(player_id, season):
    """The latest GAME_DATE in the cached log, as a date, or None."""
    rows = (read_payload(f"gamelog_{player_id}_{season}") or {}).get("data") or []
    dates = []
    for r in rows:
        try:
            dates.append(datetime.datetime.strptime(r.get("GAME_DATE", ""), "%b %d, %Y").date())
        except ValueError:
            continue
    return max(dates) if dates else None


def roster_injuries(team_id, abbr, index, season):
    """Injuries ESPN keeps on the TEAM ROSTER but leaves off the game.

    On 2 Nov 2026 the game summary listed neither Haliburton (out since
    28 Oct) nor Butler (out all season); the team rosters had both, and
    the Tonight tab projected Butler at 31 minutes. A roster entry can
    also be stale, so one counts only when the cache shows no game for
    the player since the date ESPN put on it."""
    if not team_id:
        return []
    payload = fetch(ROSTER.format(team=team_id))
    rows = []
    for athlete in payload.get("athletes", []):
        injuries = athlete.get("injuries") or []
        if not injuries:
            continue
        name = (athlete.get("displayName") or "").strip()
        status = (injuries[0].get("status") or "").strip()
        hit = index.get(norm(name))
        since = None
        try:
            since = datetime.datetime.fromisoformat(
                str(injuries[0].get("date")).replace("Z", "+00:00")).date()
        except ValueError:
            pass
        if hit and since:
            last = last_played(hit[1], season)
            if last and last >= since:
                continue                      # played since: the entry is stale
        rows.append({
            "team": abbr, "name": name,
            "nba_name": hit[0] if hit else None,
            "player_id": hit[1] if hit else None,
            "matched": hit is not None, "status": status,
            "detail": "team roster", "source": "roster",
        })
    return rows


def props_by_game(us_date, lines_dir, max_age_min=None, now=None):
    """{game "AWY@HOM": set of folded player names} with a Sportsbet prop
    in the latest snapshot saved for that US date, or {} if none.

    With `max_age_min`, a latest snapshot saved longer ago than that
    returns {} too: an old snapshot can't say a player is still priced.
    10 Nov 2026: Reaves was ruled out that morning; the slate was rebuilt
    at 9:42 while the 9:41 lines pull was still saving, so it read the
    9:20 snapshot (Reaves priced) and projected him for 36 minutes. A
    props override needs a fresh positive signal."""
    import glob as _glob
    day_dir = os.path.join(lines_dir, str(us_date))
    # By save time, not filename: names are HHMMSS, so a morning snapshot
    # ("093737") sorted before the previous evening's ("204116") and the
    # 5 Nov slate read yesterday's props -- Tatum and Hart, ruled out that
    # morning, were projected as playing because their props had been up
    # the night before.
    snaps = sorted(_glob.glob(os.path.join(day_dir, "*.json")),
                   key=lambda p: (os.path.getmtime(p), p))
    if not snaps:
        return {}
    if max_age_min is not None:
        import time as _time
        age = ((now if now is not None else _time.time()) - os.path.getmtime(snaps[-1])) / 60
        if age > max_age_min:
            return {}
    try:
        payload = json.load(open(snaps[-1]))
    except (OSError, ValueError):
        return {}
    out = {}
    for event in payload.get("events") or []:
        names = {norm(r.get("player")) for r in event.get("rows") or [] if r.get("player")}
        if names:
            out[event.get("game")] = names
    return out


def _us_day(iso):
    """The New York calendar date of an ESPN timestamp, or None."""
    if not iso:
        return None
    try:
        from zoneinfo import ZoneInfo
        when = datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return when.astimezone(ZoneInfo("America/New_York")).date()
    except (ValueError, TypeError):
        return None


def reconcile_with_props(games, props):
    """A player ESPN lists as out but the bookmaker has props up for is
    expected to play: books pull a player's props when he is ruled out.

    3 Nov 2026: ESPN still carried Curry and Draymond as out the morning
    after they rested the front end of a back-to-back; Sportsbet had
    Curry at 26.5 points. He played 33 minutes (31/6/7) and the
    Podziemski over built on his absence lost. Such a player moves from
    "out" to day-to-day with the reason attached. Only a positive signal
    is used: a book with no prop for someone says nothing, since books
    only list the main players. Mutates and returns `games`.

    5 Nov 2026, the other side: Tatum was ruled out (rest) that morning
    and Sportsbet still had his props up 90 minutes before tip, so the
    slate projected him as playing. An entry ESPN updated on the game's
    own US date is fresh news and stands; the props override is only for
    entries carried over from an earlier day, like Curry's."""
    for game in games:
        names = props.get(f"{game.get('away')}@{game.get('home')}")
        if not names:
            continue
        game_day = _us_day(game.get("tipoff"))
        for row in game.get("injuries") or []:
            if game_day and _us_day(row.get("updated")) == game_day:
                continue
            if row.get("severity") == "out" and norm(row.get("name")) in names:
                row["severity"] = "questionable"
                row["props_up"] = True
                row["status"] = f"{row.get('status')}, but Sportsbet has props up"
        game["out"] = [r for r in game.get("injuries") or [] if r.get("severity") == "out"]
    return games


def severity(status):
    low = (status or "").strip().lower()
    if low in OUT_STATUSES:
        return "out"
    if low in DOUBTFUL_STATUSES:
        return "doubtful"
    return "questionable" if low else "unknown"


def collect(date, index=None):
    """The whole slate's availability. Raises Unreachable rather than
    returning an empty, reassuring answer."""
    index = index if index is not None else roster_index()
    games = slate(date)
    season = season_of(date)
    for game in games:
        rows = injuries_for(game["event"], index)
        listed = {norm(r["name"]) for r in rows}
        for abbr, team_id in (game.get("espn_ids") or {}).items():
            for row in roster_injuries(team_id, abbr, index, season):
                if norm(row["name"]) not in listed:
                    rows.append(row)
        for row in rows:
            row["severity"] = severity(row["status"])
        game["injuries"] = rows
        game["out"] = [r for r in rows if r["severity"] == "out"]
    return games


def mpg(player_id, season):
    """Minutes per game this season -- how much an absence actually costs."""
    payload = read_payload(f"gamelog_{player_id}_{season}")
    rows = (payload or {}).get("data") or []
    if not rows:
        return 0.0
    total = 0.0
    for row in rows:
        try:
            total += float(row.get("MIN") or 0)
        except (TypeError, ValueError):
            continue
    return round(total / len(rows), 1)


def main(argv=None):
    from engine.season import CURRENT_SEASON

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", default=None,
                        help="YYYYMMDD or YYYY-MM-DD (default: today, US date)")
    parser.add_argument("--season", default=CURRENT_SEASON)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", default=None, help="write the JSON here")
    parser.add_argument("--check", nargs="+", default=None,
                        help="names to look up; exits 1 if any is out")
    parser.add_argument("--min-mpg", type=float, default=15.0,
                        help="flag an absence above this as rotation-changing")
    args = parser.parse_args(argv)

    date = (args.date or datetime.date.today().strftime("%Y%m%d")).replace("-", "")

    index = roster_index()
    try:
        games = collect(date, index)
    except Unreachable as exc:
        print(f"UNREACHABLE: ESPN did not answer, so availability is UNKNOWN.\n{exc}\n"
              f"This is a gate: treat every pick as ungated rather than as clear.",
              file=sys.stderr)
        return 2

    for game in games:
        for row in game["injuries"]:
            row["mpg"] = mpg(row["player_id"], args.season) if row["player_id"] else None

    if args.check:
        wanted = {norm(n): n for n in args.check}
        found = {}
        for game in games:
            for row in game["injuries"]:
                key = norm(row["name"])
                if key in wanted:
                    found[key] = row
        bad = False
        for key, original in wanted.items():
            row = found.get(key)
            if row is None:
                print(f"  {original:26s} not on tonight's injury report")
            else:
                mark = "OUT" if row["severity"] == "out" else row["status"].upper()
                print(f"  {original:26s} {mark:14s} {row['detail']} "
                      f"({row['team']}, {row['mpg']} mpg)")
                if row["severity"] in ("out", "doubtful"):
                    bad = True
        return 1 if bad else 0

    body = {"date": date, "games": games}
    if args.out:
        with open(args.out, "w") as handle:
            json.dump(body, handle, separators=(",", ":"))
        print(f"{len(games)} game(s) -> {args.out}")
    if args.json:
        print(json.dumps(body, separators=(",", ":")))
        return 0

    if not games:
        print(f"no games listed for {date}")
        return 0

    unmatched = 0
    for game in games:
        print(f"\n=== {game['away']} @ {game['home']} ===")
        if not game["injuries"]:
            print("   no injury report published")
            continue
        for row in sorted(game["injuries"], key=lambda r: -(r["mpg"] or 0)):
            flag = ""
            if row["severity"] == "out" and (row["mpg"] or 0) >= args.min_mpg:
                flag = "   <-- rotation player, gate anything that names him"
            if not row["matched"]:
                flag += "   [UNMATCHED to a cached roster]"
                unmatched += 1
            mins = f"{row['mpg']:.1f} mpg" if row["mpg"] is not None else "   -   "
            print(f"   {row['team']:4s} {row['name']:26s} {row['status']:12s} "
                  f"{row['detail'][:18]:18s} {mins}{flag}")

    if unmatched:
        print(f"\n{unmatched} name(s) could not be matched to a cached roster. "
              f"The gate cannot protect a pick that names one of them; refresh "
              f"the rosters (python3 batch_cache_rosters.py) and re-run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
