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
}
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
_OVER = re.compile(r"\bover\s*\(?\s*(\d{1,3}(?:\.\d)?)\s*\)?", re.I)
_LEG_PRICE = re.compile(r"@\s*(\d{1,3}\.\d{2})\b")
_GAME_DATE = re.compile(r"(?:mon|tues|wednes|thurs|fri|satur|sun)day,?\s+(\d{1,2})\s+([a-z]{3})\w*\s+"
                        r"(\d{1,2}):(\d{2})", re.I)
_PLACED = re.compile(r"bet\s+placed\s+on:?\s*\w{3},?\s+(\d{1,2})\s+([a-z]{3})\w*\s+"
                     r"(\d{1,2}):(\d{2})(?::(\d{2}))?", re.I)
_PRICE = re.compile(r"multi\+?\s*@\s*(\d{1,4}\.\d{2})", re.I)
_LEGS = re.compile(r"\b(\d{1,2})\s+legs\b", re.I)


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


def _stat_from(text):
    squashed = re.sub(r"\s+", "", text.lower()).replace("alt", "")
    for words, stat in _STAT_WORDS:
        if words in squashed:
            return stat
    return None


# ------------------------------------------------------------------ parsing

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
        if need_over and not _OVER.search(text):
            continue
        return text
    return lines[i - 1] if i else ""


def parse_sportsbet(lines, players=None, today=None):
    """One Sportsbet slip's recognised text -> {price, legs, placed_at,
    status_on_slip, problems}. Each leg: player, pid, team, stat, line,
    us_date, plus `needs_review` when something couldn't be read."""
    players = players if players is not None else known_players()
    today = today or datetime.datetime.now(SYDNEY).date()
    lines = [re.sub(r"\s+", " ", str(l)).strip() for l in lines if str(l).strip()]
    blob = " ".join(lines)
    slip = {"source": "sportsbet", "legs": [], "problems": []}

    price = _PRICE.search(blob)
    slip["price"] = float(price.group(1)) if price else None
    legs_count = _LEGS.search(blob)
    slip["legs_on_slip"] = int(legs_count.group(1)) if legs_count else None
    letters = re.sub(r"[^A-Z]", "", blob.upper())
    slip["status_on_slip"] = ("lost" if "NORETURN" in letters else
                              "won" if any(w in letters for w in ("WINNER", "PAIDOUT", "COLLECT"))
                              else None)
    placed = _PLACED.search(blob)
    if placed:
        d, mon = int(placed.group(1)), _MONTHS.get(placed.group(2).lower()[:3])
        year = _year_for(d, mon, today)
        slip["placed_at"] = datetime.datetime(
            year, mon, d, int(placed.group(3)), int(placed.group(4)), int(placed.group(5) or 0),
            tzinfo=SYDNEY).isoformat()
    else:
        slip["placed_at"] = None
        slip["problems"].append("Couldn't read when the bet was placed.")

    # Where each game date sits in the text, as (line index, US date).
    dates = []
    for i, line in enumerate(lines):
        m = _GAME_DATE.search(line)
        if m:
            d, mon = int(m.group(1)), _MONTHS.get(m.group(2).lower()[:3])
            if mon:
                dates.append((i, _us_date(d, mon, int(m.group(3)), int(m.group(4)),
                                          _year_for(d, mon, today))))

    for i, line in enumerate(lines):
        if _PLACED.search(line) or _GAME_DATE.search(line):
            continue
        leg = None
        dash = _NAME_DASH_STAT.match(line)
        threshold = _THRESHOLD.search(line)
        if dash and _stat_from(dash.group(2)) and not threshold:
            # "Cedric Coward - Alt Pts + Reb": the line is on the row above.
            above = _title_above(lines, i, need_over=True)
            over = _OVER.search(above)
            leg = {"stat": _stat_from(dash.group(2)),
                   "line": float(over.group(1)) if over else None,
                   "who": dash.group(1), "title": above}
        elif threshold and _stat_from(threshold.group(2)) and not _PRICE.search(line):
            # "16+ Points" / "To Record 8+ Rebounds": the name is the row above.
            above = _title_above(lines, i)
            leg = {"stat": _stat_from(threshold.group(2)),
                   "line": int(threshold.group(1)) - 0.5,
                   "who": above, "title": above}
        if not leg:
            continue
        found = match_player(leg["who"], players) or match_player(leg["title"], players)
        leg_price = _LEG_PRICE.search(leg["title"])
        # A leg priced on its own (a single inside a multi) is followed by
        # its game; legs of a same-game multi come after theirs.
        if leg_price:
            after = [d for j, d in dates if j > i]
            when = after[0] if after else (dates[-1][1] if dates else None)
        else:
            before = [d for j, d in dates if j < i]
            when = before[-1] if before else (dates[0][1] if dates else None)
        entry = {
            "player": found[2] if found else re.sub(r"[^A-Za-z.' -]", "", leg["who"]).strip(),
            "pid": found[0] if found else None, "team": found[1] if found else None,
            "stat": leg["stat"], "line": leg["line"],
            "us_date": when.isoformat() if when else None,
            "price": float(leg_price.group(1)) if leg_price else None,
        }
        entry["needs_review"] = [msg for ok, msg in (
            (entry["pid"], "player not recognised"), (entry["line"] is not None, "line not read"),
            (entry["us_date"], "game date not read")) if not ok]
        slip["legs"].append(entry)

    if slip["legs_on_slip"] and slip["legs_on_slip"] != len(slip["legs"]):
        # Same-game multis inside a multi count as one leg each on the slip.
        nested = len(re.findall(r"same game multi @", blob, re.I))
        if not nested or len(slip["legs"]) < slip["legs_on_slip"]:
            slip["problems"].append(
                f"The slip says {slip['legs_on_slip']} legs but {len(slip['legs'])} were read. "
                "Check for a missing leg.")
    if not slip["legs"]:
        slip["problems"].append("No legs were recognised on this screenshot.")
    return slip


def slip_id(slip):
    key = json.dumps([slip.get("placed_at"), slip.get("price"),
                      [(l["player"], l["stat"], l["line"]) for l in slip["legs"]]])
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


def grade_leg(leg):
    out = dict(leg)
    out.update({"result": "pending", "actual": None, "min": None, "proj": None})
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
        out["result"] = "won" if out["actual"] > leg["line"] else "lost"
    elif day < datetime.datetime.now(NEW_YORK).date() - datetime.timedelta(days=2):
        # The cache has days after this and no game: he didn't play.
        latest = frame["_d"].max() if not frame.empty else None
        if latest is not None and latest > day:
            out["result"] = "void"
    proj = projection(leg["pid"], leg["stat"], day)
    if proj:
        low, mid, high, rough = proj
        out["proj"] = {"low": low, "mid": mid, "high": high, "rough": rough,
                       "agrees": mid > leg["line"]}
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

def save_slip(slip, image_bytes=None, image_name=None, picks_dir=None):
    picks_dir = picks_dir or PICKS_DIR
    os.makedirs(picks_dir, exist_ok=True)
    slip = {k: v for k, v in slip.items() if k != "problems"}
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
        kind = STATS[l["stat"]][1] if l["stat"] in ("PTS", "REB", "AST") else "combos"
        won, total = kinds.get(kind, (0, 0))
        kinds[kind] = (won + (l["result"] == "won"), total + 1)
    out["by_kind"] = kinds
    seen = {}
    for s in settled:
        for l in s["legs"]:
            if l["result"] == "lost":
                seen.setdefault((l["player"], l["stat"], l["us_date"]), set()).add(s["id"])
    out["shared_losses"] = [(k, len(v)) for k, v in seen.items() if len(v) > 1]
    return out


def leg_label(leg):
    """'16+ points', '23+ pts + reb + ast' -- every leg as the number to reach."""
    if leg.get("line") is None:
        return STATS.get(leg.get("stat"), ([], "?"))[1]
    return f"{int(leg['line'] + 0.5)}+ {STATS[leg['stat']][1]}"
