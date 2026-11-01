"""Your own bet slips, read from screenshots and graded against what
happened -- private, on this Mac only, for the journal app's Picks tab.

    from picks import read_image_lines, parse_sportsbet, grade_slip

READING  read_image_lines() uses the Mac's built-in text recognition
         (Apple Vision, the same engine as Live Text). Needs
         `python3 -m pip install pyobjc-framework-Vision`.
PARSING  parse_sportsbet() turns the recognised lines of one Sportsbet
         slip into legs: player, stat, line, US game date. It anchors on
         the line under each player name ("16+ Points", "Cedric Coward -
         Pts + Reb + Ast"), which recognition reads cleanly, and matches
         names against the cached rosters, so stray characters around a
         name don't matter. Anything it can't place is left for the app's
         review step, never guessed silently.
GRADING  grade_slip() settles every leg from the cached game logs and
         puts the app's own pre-game projection beside it, built only
         from games before that date.

WHAT THIS IS NOT
It describes how picks went. It does not suggest picks, and nothing in
it ranks a bet as good or bad -- the live app removed betting-style
features for legal reasons, and this stays local for the same reason.
"""

import datetime
import difflib
import glob
import hashlib
import json
import os
import re
import sys
import unicodedata
from zoneinfo import ZoneInfo

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from engine import cache  # noqa: E402
from engine.actuals import parse_game_date  # noqa: E402
from engine.season import season_for_date  # noqa: E402
from engine.team_ids import TEAM_ID_BY_ABBR  # noqa: E402

import daily_journal as dj  # noqa: E402

PICKS_DIR = os.path.join(dj.JOURNAL_DIR, "picks")
SYDNEY = ZoneInfo("Australia/Sydney")
NEW_YORK = ZoneInfo("America/New_York")

# Stat wording on the slip -> the game-log columns it adds up.
STATS = {
    "PTS": (["PTS"], "points"), "REB": (["REB"], "rebounds"), "AST": (["AST"], "assists"),
    "PRA": (["PTS", "REB", "AST"], "pts + reb + ast"), "PR": (["PTS", "REB"], "pts + reb"),
    "PA": (["PTS", "AST"], "pts + ast"), "RA": (["REB", "AST"], "reb + ast"),
    "FG3M": (["FG3M"], "threes"), "STL": (["STL"], "steals"), "BLK": (["BLK"], "blocks"),
    # Team bets: graded from the night's final score, never projected.
    "WIN": ([], "to win"), "SPREAD": ([], "line"), "TOTAL": ([], "total points"),
}
TEAM_MARKETS = ("WIN", "SPREAD", "TOTAL")


def is_team(leg):
    return leg.get("stat") in TEAM_MARKETS
_STAT_WORDS = [  # longest first, matched on lower-case text with spaces removed
    ("pts+reb+ast", "PRA"), ("points+rebounds+assists", "PRA"),
    ("pts+reb", "PR"), ("pts+ast", "PA"), ("reb+ast", "RA"),
    ("points", "PTS"), ("rebounds", "REB"), ("assists", "AST"),
    ("threes", "FG3M"), ("3pointersmade", "FG3M"), ("threepointersmade", "FG3M"),
    ("steals", "STL"), ("blocks", "BLK"),
]
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

_THRESHOLD = re.compile(r"(?:to (?:score|record)\s+)?(\d{1,3})\s*\+\s*(.+)$", re.I)
_NAME_DASH_STAT = re.compile(r"^(.*?[A-Za-z].*?)\s+[-–]\s+(?:alt\s+)?(.+)$", re.I)
# Sportsbet writes the handicap signed: "Jalen Johnson Under (+22.5)".
# Without [+-]? here the match failed, the leg fell through to the "over"
# default below, and the line came back None -- every leg on a slip read
# as Over with no line. 31 Oct 2026.
_OVER = re.compile(r"\b(over|under)\s*\(?\s*([+-]?\d{1,3}(?:\.\d)?)\s*\)?", re.I)
# "(23.5) @ 1.96" with the Over/Under word misread: the line is still there.
_BARE_LINE = re.compile(r"\(\s*[+-]?(\d{1,3}\.\d)\s*\)")
_LEG_PRICE = re.compile(r"@\s*(\d{1,3}\.\d{2})\b")
# A placed slip dates a game by weekday ("Sunday, 1 Nov 10:10"); an
# unplaced one says "Tomorrow 1 Nov 06:10" or "Today 1 Nov 11:10".
# Requiring the weekday lost every date on a proposed slip, and then
# requiring one of the three words still lost them, because recognition
# does not reliably return that word from the small grey type the tip
# time is set in. What is always there is the shape: a day, a month
# name, a clock time. That is specific enough on its own -- the prefix
# is now optional and only the shape is required.
_GAME_DATE = re.compile(r"(?:(?:(?:mon|tues|wednes|thurs|fri|satur|sun)day|today|tomorrow),?\s+)?"
                        r"\b(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+"
                        r"(\d{1,2}):(\d{2})", re.I)
_PLACED = re.compile(r"bet\s+placed\s+on:?\s*\w{3},?\s+(\d{1,2})\s+([a-z]{3})\w*\s+"
                     r"(\d{1,2}):(\d{2})(?::(\d{2}))?", re.I)
_PRICE = re.compile(r"multi\+?\s*@\s*(\d{1,4}\.\d{2})", re.I)
_LEGS = re.compile(r"\b(\d{1,2})[\s-]+(?:legs|pick parlay|leg parlay)\b", re.I)

# US sportsbook parlays (the "7 Pick Parlay" layout shared on X, 1 Nov
# 2026) stack each leg as three rows:
#     7+
#     Deni Avdija Assists
#     POR Trail Blazers @ UTA Jazz  Today 3:10 PM
# The threshold sits alone on its row, so _THRESHOLD never matched it and
# the slip read as having no legs. The price is American odds ("+4649";
# a boosted slip shows the old price struck through first), and the game
# is dated "Today"/"Tomorrow", or M/D, never with a month name.
_PARLAY = re.compile(r"\b(?:pick|leg)\s+parlay\b|parlay\s+boost|\bsgp\b", re.I)
_ALONE_THRESHOLD = re.compile(r"^(\d{1,3})\s*\+(?:\s+\S{1,3})?$")
_AMERICAN = re.compile(r"(?<![\d.])([+-]\d{3,5})(?![\d.])")
_DAY_WORD = re.compile(r"\b(today|tomorrow|tonight)\b|\b(\d{1,2})/(\d{1,2})\b", re.I)
# Team markets on a Sportsbet slip: the market name sits under the
# selection ("Boston Celtics" / "Match Betting", "Indiana Pacers (-3.5)" /
# "Handicap Betting", "Over (218.5)" / "Total Points"). Until 1 Nov 2026
# these rows were skipped without a word: a 7-leg multi was saved as its
# 2 player legs, and its "chance" was worked out on those two.
_TEAM_MARKET = re.compile(r"^(match betting|head to head|handicap betting|line betting|"
                          r"total (?:match )?points)$", re.I)
_HANDICAP = re.compile(r"\(\s*([+-]?\d{1,2}(?:\.\d)?)\s*\)")


def american_to_decimal(odds):
    """+4649 -> 47.49, -137 -> 1.73."""
    odds = int(odds)
    return round(1 + odds / 100 if odds > 0 else 1 + 100 / abs(odds), 2)


# ------------------------------------------------------------------ reading

class TextRecognitionUnavailable(RuntimeError):
    pass


def read_image_lines(path):
    """Text lines in one image, top to bottom, via Apple Vision -- or
    Tesseract, if that's installed and Vision isn't."""
    try:
        import Vision
        from Foundation import NSURL
    except ImportError as error:
        import shutil
        import subprocess
        if shutil.which("tesseract"):
            text = subprocess.run(["tesseract", path, "-", "--psm", "4"], capture_output=True,
                                  text=True, timeout=60).stdout
            return [line for line in text.splitlines() if line.strip()]
        raise TextRecognitionUnavailable(
            "Text recognition needs Apple's Vision bridge. In Terminal: "
            "python3 -m pip install pyobjc-framework-Vision") from error
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(
        NSURL.fileURLWithPath_(os.path.abspath(path)), None)
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(False)
    ok, error = handler.performRequests_error_([request], None)
    if not ok:
        raise RuntimeError(f"Text recognition failed: {error}")
    found = []
    for obs in request.results() or []:
        text = obs.topCandidates_(1)[0].string()
        box = obs.boundingBox()           # normalised, origin bottom-left
        top = 1 - (box.origin.y + box.size.height)
        found.append((round(top, 3), box.origin.x, str(text)))
    # Row by row (things on one row share a top within ~1%), left to right.
    found.sort(key=lambda t: (round(t[0] * 100), t[1]))
    return [t for _, _, t in found]


# ------------------------------------------------------------------ names

def _norm(name):
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z ]", " ", text)
    return re.sub(r"\s+", " ", text.replace(" jr", " ").replace(" sr", " ")).strip()


def known_players():
    """{normalised name: (player id, team, display name)} from the cached
    rosters -- the names a slip is matched against."""
    players = {}
    for abbr, team_id in TEAM_ID_BY_ABBR.items():
        payload = cache.read_payload(f"roster_{team_id}")
        for row in (payload or {}).get("data") or []:
            players[_norm(row["PLAYER"])] = (int(row["PLAYER_ID"]), abbr, row["PLAYER"])
    return players


def match_player(text, players):
    """The roster player a noisy line names, or None. Tries every run of
    1-4 words in the line, so junk before or after the name is ignored."""
    words = _norm(_LEG_PRICE.sub("", _OVER.split(text)[0])).split()
    best, best_score = None, 0.0
    names = list(players)
    for size in (4, 3, 2, 1):
        for i in range(len(words) - size + 1):
            chunk = " ".join(words[i:i + size])
            if len(chunk) < 5:
                continue
            if chunk in players:
                return players[chunk]
            close = difflib.get_close_matches(chunk, names, n=1, cutoff=0.86)
            if close:
                score = difflib.SequenceMatcher(None, chunk, close[0]).ratio()
                if score > best_score:
                    best, best_score = players[close[0]], score
    return best


_TEAM_NICKNAMES = None


def _team_in(text):
    """The team abbreviation named in a bit of slip text ('Portland Trail
    Blazers', 'LA Clippers'), by nickname, or None."""
    global _TEAM_NICKNAMES
    if _TEAM_NICKNAMES is None:
        try:
            from nba_api.stats.static import teams
            _TEAM_NICKNAMES = sorted(((t["nickname"].lower(), t["abbreviation"])
                                      for t in teams.get_teams()), key=lambda x: -len(x[0]))
        except ImportError:
            _TEAM_NICKNAMES = []
    low = text.lower()
    return next((abbr for nick, abbr in _TEAM_NICKNAMES if nick in low), None)


def _team_name(abbr):
    """'BOS' -> 'Boston Celtics', or None."""
    try:
        from nba_api.stats.static import teams
        hit = teams.find_team_by_abbreviation(abbr) if abbr else None
        return hit["full_name"] if hit else None
    except ImportError:
        return None


def _game_from(line):
    """'Memphis Grizzlies @ Portland Trail Blazers' -> 'MEM@POR', or None."""
    if "@" not in line:
        return None
    away, _, home = line.partition("@")
    a, h = _team_in(away), _team_in(home)
    return f"{a}@{h}" if a and h and a != h else None


def _stat_from(text):
    squashed = re.sub(r"\s+", "", text.lower()).replace("alt", "")
    for words, stat in _STAT_WORDS:
        if words in squashed:
            return stat
    return None


# ------------------------------------------------------------------ parsing


def parser_fingerprint():
    """A short digest of THIS file's source.

    journal_app caches a screenshot's parse under a key made from the
    image bytes alone, so once a slip had been read its answer was frozen
    for the life of the browser session. On 31 Oct 2026 the signed-line
    fix landed and the app kept showing the old read -- every leg "Over",
    no line -- because the image had not changed and the cached answer
    was never recomputed.

    Keying on the parser as well means any edit here retires every stored
    read automatically, so it cannot be forgotten the next time this file
    changes.
    """
    try:
        with open(os.path.abspath(__file__), "rb") as handle:
            return hashlib.sha1(handle.read()).hexdigest()[:8]
    except OSError:
        return "nofile"


def _us_date(day, month, hour, minute, year):
    start = datetime.datetime(year, month, day, hour, minute, tzinfo=SYDNEY)
    return start.astimezone(NEW_YORK).date()


def _year_for(day, month, today):
    """Slips show no year: the most recent such date not far in the future."""
    year = today.year
    if datetime.date(year, month, day) > today + datetime.timedelta(days=30):
        year -= 1
    return year


def _title_above(lines, i, need_over=False):
    """The leg's title row above line i. Recognition sometimes puts a
    scrap of an icon (';', '%', 'iy') between title and subtitle, so look
    up to three rows back, skipping rows with too few letters."""
    for j in range(i - 1, max(i - 4, -1), -1):
        text = lines[j]
        if len(re.findall(r"[A-Za-z]", text)) < 4:
            continue
        if need_over and not (_OVER.search(text) or _BARE_LINE.search(text)):
            continue
        return text
    return lines[i - 1] if i else ""


def parse_sportsbet(lines, players=None, today=None, us_today=None):
    """One slip's recognised text -> {price, legs, placed_at,
    status_on_slip, problems}. Reads Sportsbet slips and the US "N Pick
    Parlay" layout. Each leg: player, pid, team, stat, line, us_date, plus
    `needs_review` when something couldn't be read."""
    players = players if players is not None else known_players()
    today = today or datetime.datetime.now(SYDNEY).date()
    us_today = us_today or datetime.datetime.now(NEW_YORK).date()
    lines = [re.sub(r"\s+", " ", str(l)).strip() for l in lines if str(l).strip()]
    blob = " ".join(lines)
    parlay = bool(_PARLAY.search(blob)) and not _PRICE.search(blob)
    slip = {"source": "parlay" if parlay else "sportsbet", "legs": [], "problems": []}

    price = _PRICE.search(blob)
    slip["price"] = float(price.group(1)) if price else None
    if parlay:
        # The header's last American price, before the first leg: a boost
        # shows the old price struck through, then the one that pays.
        first_leg = next((i for i, l in enumerate(lines) if _ALONE_THRESHOLD.match(l)), len(lines))
        odds = [m.group(1) for l in lines[:first_leg] for m in _AMERICAN.finditer(l)]
        slip["price"] = american_to_decimal(odds[-1]) if odds else None
    legs_count = _LEGS.search(blob)
    slip["legs_on_slip"] = int(legs_count.group(1)) if legs_count else None
    letters = re.sub(r"[^A-Z]", "", blob.upper())
    slip["status_on_slip"] = ("lost" if "NORETURN" in letters else
                              "won" if any(w in letters for w in ("WINNER", "PAIDOUT", "COLLECT"))
                              else None)
    slip["proposed"] = False
    placed = _PLACED.search(blob)
    if placed:
        d, mon = int(placed.group(1)), _MONTHS.get(placed.group(2).lower()[:3])
        year = _year_for(d, mon, today)
        slip["placed_at"] = datetime.datetime(
            year, mon, d, int(placed.group(3)), int(placed.group(4)), int(placed.group(5) or 0),
            tzinfo=SYDNEY).isoformat()
    else:
        slip["placed_at"] = None
        slip["proposed"] = bool(re.search(r"proposed\s+bet", blob, re.I))
        if not slip["proposed"] and not parlay:   # a US parlay shows no placed time
            slip["problems"].append("Couldn't read when the bet was placed.")

    # Where each game date sits in the text, as (line index, US date),
    # and the game named just above it.
    dates, games = [], {}
    for i, line in enumerate(lines):
        m = _GAME_DATE.search(line)
        if m:
            d, mon = int(m.group(1)), _MONTHS.get(m.group(2).lower()[:3])
            if mon:
                when = _us_date(d, mon, int(m.group(3)), int(m.group(4)), _year_for(d, mon, today))
                dates.append((i, when))
                game = next((g for g in (_game_from(lines[j]) for j in range(i - 1, max(i - 3, -1), -1))
                             if g), None)
                games[i] = game

    for i, line in enumerate(lines):
        if _PLACED.search(line) or _GAME_DATE.search(line):
            continue
        leg = None
        market = _TEAM_MARKET.match(line)
        if market:
            title = _title_above(lines, i)
            kind = market.group(1).lower()
            if kind.startswith("total"):
                over = _OVER.search(title)
                leg = {"stat": "TOTAL", "line": abs(float(over.group(2))) if over else None,
                       "side": over.group(1).lower() if over else "over",
                       "side_unsure": not over, "who": title, "title": title, "team_leg": True}
            else:
                hcp = _HANDICAP.search(title) if "handicap" in kind or "line" in kind else None
                leg = {"stat": "SPREAD" if hcp else "WIN",
                       "line": float(hcp.group(1)) if hcp else None,
                       "side": "over", "side_unsure": False,
                       "who": _HANDICAP.sub("", title).strip(), "title": title, "team_leg": True}
        dash = None if leg else _NAME_DASH_STAT.match(line)
        threshold = None if leg else _THRESHOLD.search(line)
        if leg:
            pass
        elif dash and _stat_from(dash.group(2)) and not threshold:
            # "Cedric Coward - Alt Pts + Reb": the line is on the row above.
            above = _title_above(lines, i, need_over=True)
            over = _OVER.search(above)
            bare = None if over else _BARE_LINE.search(above)
            leg = {"stat": _stat_from(dash.group(2)),
                   "line": abs(float(over.group(2))) if over else (float(bare.group(1)) if bare else None),
                   "side": over.group(1).lower() if over else "over",
                   "side_unsure": not over, "market": "ou",
                   "who": dash.group(1), "title": above}
        elif threshold and _stat_from(threshold.group(2)) and not _PRICE.search(line):
            # "16+ Points" / "To Record 8+ Rebounds": the name is the row above.
            above = _title_above(lines, i)
            leg = {"stat": _stat_from(threshold.group(2)),
                   "line": int(threshold.group(1)) - 0.5, "side": "over", "side_unsure": False,
                   "market": "alt", "who": above, "title": above}
        elif parlay and _ALONE_THRESHOLD.match(line):
            # "7+" alone, then "Deni Avdija Assists", then the game.
            for j in range(i + 1, min(i + 3, len(lines))):
                if _ALONE_THRESHOLD.match(lines[j]):
                    break
                if _stat_from(lines[j]):
                    leg = {"stat": _stat_from(lines[j]),
                           "line": int(_ALONE_THRESHOLD.match(line).group(1)) - 0.5,
                           "side": "over", "side_unsure": False,
                           "who": lines[j], "title": lines[j], "below": j}
                    break
        if not leg:
            continue
        found = (None if leg.get("team_leg") else
                 match_player(leg["who"], players) or match_player(leg["title"], players))
        leg_price = _LEG_PRICE.search(leg["title"])
        # A leg priced on its own (a single inside a multi) is followed by
        # its game; legs of a same-game multi come after theirs.
        if leg_price:
            after = [(j, d) for j, d in dates if j > i]
            pick = after[0] if after else (dates[-1] if dates else None)
        else:
            before = [(j, d) for j, d in dates if j < i]
            pick = before[-1] if before else (dates[0] if dates else None)
        when = pick[1] if pick else None
        game = games.get(pick[0]) if pick else None
        if "below" in leg:
            # The game and its day sit on the row(s) under the name.
            when, game = None, None
            for j in range(leg["below"] + 1, min(leg["below"] + 3, len(lines))):
                game = game or _game_from(lines[j])
                day = _DAY_WORD.search(lines[j])
                if day and when is None:
                    if day.group(1):
                        when = us_today + datetime.timedelta(
                            days=1 if day.group(1).lower() == "tomorrow" else 0)
                        slip["dated_today"] = True
                    else:
                        try:
                            when = datetime.date(us_today.year, int(day.group(2)), int(day.group(3)))
                        except ValueError:
                            when = None
        if leg.get("team_leg"):
            team = _team_in(leg["who"]) if leg["stat"] != "TOTAL" else None
            entry = {
                "player": (game or "Game") if leg["stat"] == "TOTAL"
                else _team_name(team) or re.sub(r"[^A-Za-z.' -]", "", leg["who"]).strip(),
                "pid": None, "team": team,
                "stat": leg["stat"], "line": leg["line"], "side": leg["side"],
                "us_date": when.isoformat() if when else None,
                "price": float(leg_price.group(1)) if leg_price else None,
                "game": game,
            }
            entry["needs_review"] = [msg for ok, msg in (
                (team or leg["stat"] == "TOTAL", "team not recognised"),
                (entry["line"] is not None or leg["stat"] == "WIN", "line not read"),
                (game or leg["stat"] != "TOTAL", "game not read"),
                (entry["us_date"], "game date not read")) if not ok]
            slip["legs"].append(entry)
            continue
        entry = {
            "player": found[2] if found else re.sub(r"[^A-Za-z.' -]", "", leg["who"]).strip(),
            "pid": found[0] if found else None, "team": found[1] if found else None,
            "stat": leg["stat"], "line": leg["line"], "side": leg["side"],
            "us_date": when.isoformat() if when else None,
            "price": float(leg_price.group(1)) if leg_price else None,
            "game": game,
        }
        if leg.get("market"):
            entry["market"] = leg["market"]   # "ou" or "alt" ("To Record N+"): see line_snapshot.clv
        entry["needs_review"] = [msg for ok, msg in (
            (entry["pid"], "player not recognised"), (entry["line"] is not None, "line not read"),
            (not (leg["side_unsure"] and entry["line"] is not None), "check over or under"),
            (entry["us_date"], "game date not read")) if not ok]
        slip["legs"].append(entry)

    if slip["legs_on_slip"] and slip["legs_on_slip"] != len(slip["legs"]):
        # Same-game multis inside a multi count as one leg each on the slip.
        nested = len(re.findall(r"same game multi @", blob, re.I))
        if not nested or len(slip["legs"]) < slip["legs_on_slip"]:
            slip["problems"].append(
                f"The slip says {slip['legs_on_slip']} legs but {len(slip['legs'])} were read. "
                "Check for a missing leg.")
    if slip.pop("dated_today", False):
        slip["problems"].append(
            f"This slip says \"Today\" with no date, so its legs are dated the US day it is now "
            f"({us_today.strftime('%a %-d %b')}). Change the US date if the games were another day.")
    if not slip["legs"]:
        slip["problems"].append("No legs were recognised on this screenshot.")
    return slip


def slip_id(slip):
    key = json.dumps([slip.get("placed_at"), slip.get("price"),
                      [(l["player"], l["stat"], l["line"]) + ((l["side"],) if l.get("side") == "under" else ())
                       for l in slip["legs"]]])
    return hashlib.sha1(key.encode()).hexdigest()[:10]


# ------------------------------------------------------------------ grading

def _log(pid, season):
    payload = cache.read_payload(f"gamelog_{pid}_{season}")
    frame = pd.DataFrame((payload or {}).get("data") or [])
    if not frame.empty and "GAME_DATE" in frame.columns:
        frame["_d"] = frame["GAME_DATE"].map(parse_game_date)
    return frame


def projection(pid, stat, day):
    """(low, mid, high, rough) from games before `day`, or None. Points,
    rebounds and assists use the app's calibrated projection; combos use
    the player's own spread of past games (rough: the engine doesn't
    calibrate combos)."""
    if stat in ("PTS", "REB", "AST"):
        pr = dj.pregame_projection(pid, day)
        if pr and stat in pr:
            low, mid, high = pr[stat]
            return low, mid, high, False
        return None
    season = season_for_date(day)
    start = int(season[:4])
    frame = _log(pid, season)
    if not frame.empty:
        frame = frame[frame["_d"].map(lambda d: d is not None and d < day)]
    if len(frame) < dj.MIN_PRIOR_GAMES:
        frame = _log(pid, f"{start - 1}-{str(start)[-2:]}")
    cols = STATS[stat][0]
    if len(frame) < dj.MIN_PRIOR_GAMES or not all(c in frame for c in cols):
        return None
    series = sum(frame[c].astype(float) for c in cols).tail(30)
    return (round(float(np.percentile(series, 10)), 1), round(float(series.median()), 1),
            round(float(np.percentile(series, 90)), 1), True)


def _night_game(us_date, team=None, game=None):
    """The written night's record of one game: (winner, loser, margin,
    total) or None when the night or that game isn't in yet."""
    path = os.path.join(dj.JOURNAL_DIR, f"{us_date}.json")
    try:
        with open(path) as f:
            night = json.load(f)
    except (OSError, ValueError):
        return None
    sides = set(game.split("@")) if game and "@" in game else None
    for g in night.get("games") or []:
        pair = {g.get("home"), g.get("away")}
        if (sides and pair == sides) or (team and team in pair):
            if not g.get("winner") or g.get("margin") is None:
                return None
            total = None
            if isinstance(g.get("tw"), dict) and isinstance(g.get("tl"), dict):
                total = (g["tw"].get("pts") or 0) + (g["tl"].get("pts") or 0)
            return g["winner"], g.get("loser"), int(g["margin"]), total
    return None


def _night_actuals(us_date, pid):
    """What a player did on a night, from the written journal night (ESPN's
    box score): ({"PTS": .., "REB": .., "AST": ..}, minutes, played) or None.

    The fallback for grading when the stats.nba.com cache hasn't caught up
    -- on 1 Nov 2026 the morning refresh timed out on half its players, so
    a multi that had landed showed "waiting on 3 legs" while the night
    itself was already written with every number in it."""
    path = os.path.join(dj.JOURNAL_DIR, f"{us_date}.json")
    try:
        with open(path) as f:
            night = json.load(f)
    except (OSError, ValueError):
        return None
    stats, minutes, played = {}, None, None
    for g in night.get("games") or []:
        for line in g.get("lines") or []:
            if line.get("pid") == pid and line.get("actual") is not None:
                stats[line["stat"]] = float(line["actual"])
                minutes = line.get("min") if minutes is None else minutes
        for m in g.get("minutes") or []:
            if m.get("pid") == pid:
                played = m.get("played")
                minutes = m.get("min") if minutes is None else minutes
    if not stats and played is None:
        return None
    return stats, minutes, played


def grade_team_leg(leg, out):
    """A team bet from the night's final score: to win, a line, or the
    game total. Pending until the night is written with that game in it."""
    found = _night_game(leg["us_date"], leg.get("team"), leg.get("game"))
    if not found:
        return out
    winner, _loser, margin, total = found
    if leg["stat"] == "TOTAL":
        if total is None or leg.get("line") is None:
            return out
        out["actual"] = float(total)
        under = leg.get("side") == "under"
        out["result"] = "won" if (total < leg["line"] if under else total > leg["line"]) else "lost"
        return out
    signed = margin if winner == leg.get("team") else -margin
    out["actual"] = float(signed)
    if leg["stat"] == "WIN":
        out["result"] = "won" if signed > 0 else "lost"
    else:
        cover = signed + float(leg["line"])
        out["result"] = "won" if cover > 0 else ("void" if cover == 0 else "lost")
    return out


def grade_leg(leg):
    out = dict(leg)
    out.update({"result": "pending", "actual": None, "min": None, "proj": None})
    if is_team(leg):
        if not leg.get("us_date") or not (leg.get("team") or leg.get("game")) or (
                leg["stat"] != "WIN" and leg.get("line") is None):
            out["result"] = "unreadable"
            return out
        return grade_team_leg(leg, out)
    if not leg.get("pid") or leg.get("line") is None or not leg.get("us_date"):
        out["result"] = "unreadable"
        return out
    day = datetime.date.fromisoformat(leg["us_date"])
    cols = STATS[leg["stat"]][0]
    frame = _log(leg["pid"], season_for_date(day))
    played = frame[frame["_d"] == day] if not frame.empty else frame
    if len(played):
        row = played.iloc[0]
        out["actual"] = float(sum(float(row[c]) for c in cols))
        out["min"] = float(row.get("MIN") or 0)
        under = leg.get("side") == "under"
        out["result"] = "won" if (out["actual"] < leg["line"] if under else out["actual"] > leg["line"]) else "lost"
    elif (night := _night_actuals(leg["us_date"], leg["pid"])) and (
            all(c in night[0] for c in cols) or night[2] is False):
        stats, minutes, played = night
        if played is False:
            out["result"] = "void"
        else:
            out["actual"] = float(sum(stats[c] for c in cols))
            out["min"] = float(minutes or 0)
            under = leg.get("side") == "under"
            out["result"] = "won" if (out["actual"] < leg["line"] if under
                                      else out["actual"] > leg["line"]) else "lost"
    elif day < datetime.datetime.now(NEW_YORK).date() - datetime.timedelta(days=2):
        # The cache has days after this and no game: he didn't play.
        latest = frame["_d"].max() if not frame.empty else None
        if latest is not None and latest > day:
            out["result"] = "void"
    proj = projection(leg["pid"], leg["stat"], day)
    if proj:
        low, mid, high, rough = proj
        out["proj"] = {"low": low, "mid": mid, "high": high, "rough": rough,
                       "agrees": (mid < leg["line"]) if leg.get("side") == "under" else (mid > leg["line"])}
    return out


def grade_slip(slip):
    out = dict(slip)
    out["legs"] = [grade_leg(l) for l in slip["legs"]]
    results = [l["result"] for l in out["legs"]]
    live = [r for r in results if r != "void"]
    out["result"] = ("lost" if "lost" in live else
                     "won" if live and all(r == "won" for r in live) else "pending")
    out["legs_won"] = results.count("won")
    out["legs_lost"] = results.count("lost")
    return out


# ------------------------------------------------------------------ storage

def _leg_key(leg):
    return (_norm(leg.get("player") or ""), leg.get("stat"), leg.get("line"), leg.get("side"))


def carry_reasons(old, new_legs):
    """Your reasons from an older copy of a slip, re-attached to the same
    legs on the new copy. Reasons are stored one per leg IN ORDER, so when
    a re-read slip gains legs the old answers must follow their leg, not
    their position: on 1 Nov 2026 a 2-leg read became 7 legs and
    Pritchard's reason landed on "Boston Celtics to win" and Murray-
    Boyles' on Pritchard."""
    reasons = dict(old.get("reasons") or {})
    by_key = {_leg_key(l): r for l, r in zip(old.get("legs") or [], reasons.get("legs") or [])}
    reasons["legs"] = [by_key.get(_leg_key(l), {}) for l in new_legs]
    return reasons


def save_slip(slip, image_bytes=None, image_name=None, picks_dir=None):
    picks_dir = picks_dir or PICKS_DIR
    os.makedirs(picks_dir, exist_ok=True)
    slip = {k: v for k, v in slip.items() if k != "problems"}
    if not slip.get("id") and slip.get("placed_at"):
        # The same bet uploaded again after the parser learned more (1 Nov
        # 2026: a 7-leg multi first saved as 2 legs) replaces the old copy
        # instead of sitting beside it. Same placed time, same price.
        for old in load_slips(picks_dir):
            if (old.get("placed_at") == slip["placed_at"]
                    and old.get("price") == slip.get("price") and old.get("id")):
                slip["id"] = old["id"]
                for keep in ("stake", "picked_by", "out"):
                    if old.get(keep) and not slip.get(keep):
                        slip[keep] = old[keep]
                if old.get("reasons") and not slip.get("reasons"):
                    slip["reasons"] = carry_reasons(old, slip["legs"])
                break
    slip["id"] = slip.get("id") or slip_id(slip)
    slip["saved_at"] = datetime.datetime.now(SYDNEY).isoformat(timespec="seconds")
    path = os.path.join(picks_dir, f"{slip['id']}.json")
    if not image_bytes and os.path.exists(path):
        try:
            with open(path) as f:
                old_image = json.load(f).get("image")
            if old_image:
                slip["image"] = old_image      # re-saved without a screenshot: keep the old one
        except (OSError, ValueError):
            pass
    if "reasons" not in slip and os.path.exists(path):
        try:
            with open(path) as f:
                old_reasons = json.load(f).get("reasons")
            if old_reasons:
                slip["reasons"] = old_reasons  # re-saved from a screenshot: keep what you wrote
        except (OSError, ValueError):
            pass
    if image_bytes:
        ext = os.path.splitext(image_name or "slip.png")[1].lower() or ".png"
        os.makedirs(os.path.join(picks_dir, "images"), exist_ok=True)
        slip["image"] = f"images/{slip['id']}{ext}"
        with open(os.path.join(picks_dir, slip["image"]), "wb") as f:
            f.write(image_bytes)
    for leg in slip["legs"]:
        leg.pop("needs_review", None)
    with open(os.path.join(picks_dir, f"{slip['id']}.json"), "w") as f:
        json.dump(slip, f, indent=1)
    return slip["id"]


def load_slips(picks_dir=None):
    picks_dir = picks_dir or PICKS_DIR
    slips = []
    for path in glob.glob(os.path.join(picks_dir, "*.json")):
        try:
            with open(path) as f:
                slips.append(json.load(f))
        except (OSError, ValueError):
            continue
    return sorted(slips, key=lambda s: s.get("placed_at") or "", reverse=True)


def remove_slip(sid, picks_dir=None):
    picks_dir = picks_dir or PICKS_DIR
    path = os.path.join(picks_dir, f"{sid}.json")
    try:
        with open(path) as f:
            image = json.load(f).get("image")
    except (OSError, ValueError):
        image = None
    for target in (path, os.path.join(picks_dir, image) if image else None):
        if target and os.path.exists(target):
            os.remove(target)


# ------------------------------------------------------------------ record

def record(graded):
    """The numbers for the top of the Picks tab, from graded slips."""
    settled = [s for s in graded if s["result"] in ("won", "lost")]
    legs = [l for s in graded for l in s["legs"] if l["result"] in ("won", "lost")]
    out = {
        "slips": len(graded), "settled": len(settled),
        "slips_won": sum(s["result"] == "won" for s in settled),
        "lost_by_one": sum(s["result"] == "lost" and s["legs_lost"] == 1 for s in settled),
        "legs": len(legs), "legs_won": sum(l["result"] == "won" for l in legs),
        "implied_wins": round(sum(1 / s["price"] for s in settled if s.get("price")), 1),
        "priced": sum(1 for s in settled if s.get("price")),
    }
    with_proj = [l for l in legs if l.get("proj")]
    out["agree"] = [l for l in with_proj if l["proj"]["agrees"]]
    out["disagree"] = [l for l in with_proj if not l["proj"]["agrees"]]
    out["no_proj"] = [l for l in legs if not l.get("proj")]
    kinds = {}
    for l in legs:
        kind = ("team bets" if is_team(l) else
                STATS[l["stat"]][1] if l["stat"] in ("PTS", "REB", "AST") else "combos")
        won, total = kinds.get(kind, (0, 0))
        kinds[kind] = (won + (l["result"] == "won"), total + 1)
    out["by_kind"] = kinds
    seen = {}
    for s in settled:
        for l in s["legs"]:
            if l["result"] == "lost":
                seen.setdefault((l["player"], l["stat"], l["us_date"]), set()).add(s["id"])  # either side
    out["shared_losses"] = [(k, len(v)) for k, v in seen.items() if len(v) > 1]
    pickers = {}
    for s in graded:
        who = s.get("picked_by") or "you"
        for l in s["legs"]:
            if l["result"] in ("won", "lost"):
                won, total = pickers.get(who, (0, 0))
                pickers[who] = (won + (l["result"] == "won"), total + 1)
    out["by_picker"] = pickers
    out["by_sure"] = by_sure(graded)
    return out


def leg_label(leg):
    """'16+ points', '9 or fewer points' -- every leg as the number to reach
    (Over) or stay at or under (Under)."""
    if leg.get("stat") == "WIN":
        return "to win"
    if leg.get("stat") == "SPREAD" and leg.get("line") is not None:
        return f"{leg['line']:+g} line"
    if leg.get("stat") == "TOTAL" and leg.get("line") is not None:
        return f"{'under' if leg.get('side') == 'under' else 'over'} {leg['line']:g} total points"
    if leg.get("line") is None:
        return STATS.get(leg.get("stat"), ([], "?"))[1]
    if leg.get("side") == "under":
        return f"{int(leg['line'] - 0.5)} or fewer {STATS[leg['stat']][1]}"
    return f"{int(leg['line'] + 0.5)}+ {STATS[leg['stat']][1]}"


# ------------------------------------------------------------------ your reasons

# How sure you were of a leg when you picked it, strongest first.
SURE = ["Strong", "Lean", "Filler", "Not sure"]


def _clean(answers):
    """Drop blank answers and trim the rest."""
    out = {}
    for k, v in (answers or {}).items():
        if isinstance(v, str):
            v = v.strip()
        if v not in (None, ""):
            out[k] = v
    return out


def save_reasons(sid, picks_dir=None, slip=None, legs=None):
    """Keep your own reasons on a saved slip. `slip` holds the slip-wide
    answers ("idea" before the game, "after" once it's settled); `legs` is
    one {"sure", "why", "miss"} per leg, in the slip's order. Blank answers
    are dropped, so saving an empty form leaves nothing behind."""
    reasons = {"slip": _clean(slip), "legs": [_clean(l) for l in (legs or [])]}
    reasons["at"] = datetime.datetime.now(SYDNEY).isoformat(timespec="seconds")
    return update_slip(sid, picks_dir, reasons=reasons)


def has_reasons(slip):
    r = slip.get("reasons") or {}
    return bool(r.get("slip")) or any(r.get("legs") or [])


def by_sure(graded):
    """Settled legs grouped by how sure you said you were: {label: (won, total)}."""
    out = {}
    for s in graded:
        marks = (s.get("reasons") or {}).get("legs") or []
        for l, m in zip(s["legs"], marks):
            sure = (m or {}).get("sure")
            if sure and l["result"] in ("won", "lost"):
                won, total = out.get(sure, (0, 0))
                out[sure] = (won + (l["result"] == "won"), total + 1)
    return out


# ------------------------------------------------------------------ money

def update_slip(sid, picks_dir=None, **fields):
    """Change saved fields (stake, paid, out) on one slip."""
    picks_dir = picks_dir or PICKS_DIR
    path = os.path.join(picks_dir, f"{sid}.json")
    with open(path) as f:
        slip = json.load(f)
    slip.update(fields)
    with open(path, "w") as f:
        json.dump(slip, f, indent=1)
    return slip


def returned(slip):
    """What a graded slip paid back: what you entered if you did (boosts,
    cash-outs), otherwise stake x price for a winner and 0 for a loser.
    None while it's still pending or when no stake is recorded."""
    if slip.get("paid") is not None:
        return float(slip["paid"])
    if not slip.get("stake"):
        return None
    if slip.get("result") == "won" and slip.get("price"):
        return round(float(slip["stake"]) * float(slip["price"]), 2)
    if slip.get("result") == "lost":
        return 0.0
    return None


def money(graded):
    """Staked and returned, overall and week by week (weeks start Monday,
    Sydney time), across slips that have a stake."""
    rows = []
    for s in graded:
        if not s.get("stake") or not s.get("placed_at"):
            continue
        placed = datetime.datetime.fromisoformat(s["placed_at"]).astimezone(SYDNEY).date()
        back = returned(s)
        rows.append({"week": placed - datetime.timedelta(days=placed.weekday()),
                     "stake": float(s["stake"]), "back": back, "settled": back is not None})
    if not rows:
        return None
    frame = pd.DataFrame(rows)
    settled = frame[frame["settled"]]
    weeks = []
    for week, part in frame.groupby("week"):
        done = part[part["settled"]]
        weeks.append({"week": week, "slips": len(part), "staked": part["stake"].sum(),
                      "returned": done["back"].sum(), "net": done["back"].sum() - done["stake"].sum(),
                      "open": int((~part["settled"]).sum())})
    return {
        "slips": len(frame), "staked": float(frame["stake"].sum()),
        "settled_staked": float(settled["stake"].sum()), "returned": float(settled["back"].sum()),
        "net": float(settled["back"].sum() - settled["stake"].sum()),
        "open_staked": float(frame[~frame["settled"]]["stake"].sum()),
        "weeks": sorted(weeks, key=lambda w: w["week"], reverse=True),
    }
