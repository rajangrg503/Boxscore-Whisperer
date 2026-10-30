#!/usr/bin/env python3
"""A one-page record of every NBA game on a night: the score, the box-score
edges that decided it, who stood out, who was missing, where the app's
projections were wrong -- and a short plain-English reason, written by a
local Llama 3.2 model from those facts only.

    python3 tools/daily_journal.py                    # last night (US date)
    python3 tools/daily_journal.py --date 2026-10-28
    python3 tools/daily_journal.py --days 7           # the last week
    python3 tools/daily_journal.py --no-model         # skip the reason lines

Writes journal/<US date>.md, one file per night. Run it on your Mac,
after the morning refresh, the same way as the other tools/ scripts:
the game list and any missing box score come from nba_api, which
stats.nba.com blocks from cloud hosts. Already-cached box scores are
read straight from the cache, so a night that is cached works offline.

THE DATE IS THE US DATE
Box scores and game logs are filed under the date the game was played
in the US. From Sydney, "last night" in the morning is still the US
evening, so the default is US Eastern yesterday: the most recent night
whose games are certainly final.

WHAT "THE REASON" IS, AND IS NOT
It is what the box score shows separated the teams -- shooting,
turnovers, free throws, offensive rebounds, standout and missing
players. It is not WHY those happened. There is no play-by-play,
injury report or coaching data here, and a 3B model asked "why did
Boston win" would happily supply some. So, as in tools/whisper.py,
Python reduces each game to a list of facts, the model only phrases
them, and a note with any number not in the facts (or betting language)
is rejected. With no model, or a rejected note, the facts are still in
the file -- the reason line is the only thing that goes missing.

THE PROJECTIONS ARE POINT-IN-TIME
Each player's projection is rebuilt from games strictly before the
date, through the same seam the backtest uses, so a game cannot leak
into its own projection. It is the baseline the page shows before any
adjustment (no matchup, missing-teammate or scheme layers) -- the same
number tools/capture_projections.py records. One night of these is
noise; engine/forward_record.py is where accuracy is actually claimed.
"""

import argparse
import datetime
import glob
import os
import sys
from zoneinfo import ZoneInfo

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import pandas as pd  # noqa: E402

from engine import cache  # noqa: E402
from engine.actuals import parse_game_date  # noqa: E402
from engine.baseline_stats import stats_from_gamelog  # noqa: E402
from engine.distribution import distribution_for  # noqa: E402
from engine.season import season_for_date  # noqa: E402

import whisper  # noqa: E402  (shared checker + Ollama setup)

JOURNAL_DIR = os.path.join(REPO_ROOT, "journal")
MIN_PRIOR_GAMES = 5          # same floor as tools/slate_projections.py
NOTABLE_PROJECTED_PTS = 10   # "a rotation player worth mentioning if out"
SURPRISES_PER_GAME = 3
REASON_MAX_WORDS = 60
INCOMPLETE_MARKER = "<!-- incomplete -->"

HEADLINE = [("PTS", "points", "pts"), ("REB", "rebounds", "reb"), ("AST", "assists", "ast")]
BOX_TO_STAT = {"PTS": "points", "REB": "reboundsTotal", "AST": "assists"}
# How far outside the range a result has to land to count as a miss
# worth reading about. Without it, a bench player's 0 rebounds against
# a 0.7-5.2 range crowds out a 38-point night against 10-29.
MIN_MISS = {"PTS": 4.0, "REB": 2.0, "AST": 2.0}

# (key, sentence, minimum gap worth mentioning, higher is better for the team?)
# The sentence takes {lead} {a} {trail} {b}: the team with more, then the other.
EDGES = [
    ("fg3m", "{lead} made {a} threes to {trail}'s {b}", 4, True),
    ("fg_pct", "{lead} shot {a}% from the field to {trail}'s {b}%", 5.0, True),
    ("tov", "{lead} turned it over {a} times to {trail}'s {b}", 4, False),
    ("fta", "{lead} took {a} free throws to {trail}'s {b}", 8, True),
    ("oreb", "{lead} grabbed {a} offensive rebounds to {trail}'s {b}", 5, True),
    ("ast", "{lead} had {a} assists to {trail}'s {b}", 8, True),
    # From the box score summary (load_summary) -- skipped when it isn't available.
    ("paint", "{lead} scored {a} points in the paint to {trail}'s {b}", 12, True),
    ("fastbreak", "{lead} scored {a} fast-break points to {trail}'s {b}", 8, True),
    ("second", "{lead} scored {a} second-chance points to {trail}'s {b}", 8, True),
    ("off_tov", "{lead} scored {a} points off turnovers to {trail}'s {b}", 8, True),
    ("bench", "{lead}'s bench scored {a} points to {trail}'s {b}", 15, True),
]
MAX_EDGES = 4

# BoxScoreSummaryV3 OtherStats column -> our key
SUMMARY_STATS = {
    "pointsInThePaint": "paint", "pointsFastBreak": "fastbreak",
    "pointsSecondChance": "second", "pointsFromTurnovers": "off_tov",
    "benchPoints": "bench", "biggestLead": "biggest_lead",
    "biggestScoringRun": "run", "leadChanges": "lead_changes", "timesTied": "times_tied",
}
QUARTERS = ["period1Score", "period2Score", "period3Score", "period4Score"]
ORDINAL = ["first", "second", "third", "fourth"]
# A quarter won by this much, or a run this long, is part of the story.
DECISIVE_QUARTER = 6
NOTABLE_RUN = 12

REASON_PROMPT = f"""You write the "why" line in an NBA game journal.
Rules:
- Use ONLY the facts given. Never add injuries, momentum, coaching, effort or any cause that is not listed.
- Copy numbers exactly as written in the facts. Do not calculate new ones.
- 2 or 3 sentences, at most {REASON_MAX_WORDS} words, plain calm English.
- Lead with the edge that mattered most, then a standout or missing player if one is listed.
- Describe, never advise. Never use these words: {", ".join(whisper.BANNED_WORDS)}.
- Output the sentences only. No title, no bullet points."""


# ------------------------------------------------------------- the slate

def us_yesterday(now=None):
    now = now or datetime.datetime.now(ZoneInfo("America/New_York"))
    return now.date() - datetime.timedelta(days=1)


def _home_away(rows):
    """{game_id: {"home": abbr, "away": abbr}} from (game_id, MATCHUP) pairs.
    "NYK @ CHI" is NYK away at CHI; "CHI vs. NYK" is CHI at home."""
    games = {}
    for game_id, matchup in rows:
        parts = str(matchup).split()
        if len(parts) != 3:
            continue
        team, sep, other = parts
        home, away = (other, team) if sep == "@" else (team, other)
        games[str(game_id)] = {"home": home, "away": away}
    return games


def games_live(day):
    """Every game on that US date, from nba_api. None if unreachable."""
    try:
        from nba_api.stats.endpoints import leaguegamelog
    except ImportError:
        return None
    stamp = day.strftime("%m/%d/%Y")
    rows = []
    try:
        for season_type in ("Regular Season", "Playoffs"):
            df = leaguegamelog.LeagueGameLog(
                season=season_for_date(day), season_type_all_star=season_type,
                player_or_team_abbreviation="T",
                date_from_nullable=stamp, date_to_nullable=stamp, timeout=15,
            ).get_data_frames()[0]
            rows += list(zip(df["GAME_ID"], df["MATCHUP"]))
    except Exception:
        return None
    return _home_away(rows)


def _cached_gamelog_names(season):
    names = {os.path.basename(p) for p in glob.glob(os.path.join(cache.CACHE_DIR, f"gamelog_*_{season}.json"))}
    if cache.ARCHIVE is not None and cache.CACHE_DIR == cache.DEFAULT_CACHE_DIR:
        names |= {n for n in cache.ARCHIVE.names() if n.startswith("gamelog_") and n.endswith(f"_{season}.json")}
    return sorted(names)


def games_from_cache(day):
    """The same, rebuilt from cached player game logs -- for when the
    API can't be reached. Only as complete as the last refresh."""
    rows = []
    for name in _cached_gamelog_names(season_for_date(day)):
        payload = cache.read_payload(name[: -len(".json")])
        for row in (payload or {}).get("data") or []:
            if parse_game_date(row.get("GAME_DATE")) == day:
                rows.append((row.get("Game_ID"), row.get("MATCHUP")))
    return _home_away(rows)


def list_games(day):
    games = games_live(day)
    if games:
        return games, "nba_api"
    return games_from_cache(day), "cache"


def load_boxscore(game_id):
    """Player rows for one game, cache first (a final box score never
    changes), then live. None if neither has it."""
    key = f"boxscore_{game_id}"
    payload = cache.read_payload(key)
    rows = (payload or {}).get("data") or []
    if rows and "personId" in rows[0]:
        return pd.DataFrame(rows)
    try:
        from nba_api.stats.endpoints import boxscoretraditionalv3
        df = boxscoretraditionalv3.BoxScoreTraditionalV3(game_id=game_id, timeout=15).get_data_frames()[0]
    except Exception:
        return None
    if df.empty:
        return None
    cache._save_df_cache(key, df)
    return df


def load_summary(game_id):
    """{"line": DataFrame, "other": DataFrame} from BoxScoreSummaryV3 --
    quarter scores and the team stats the traditional box score lacks
    (paint, fast-break, second-chance, bench points, biggest lead, lead
    changes). Cache first; live only caches a game that is final.
    None if neither has it: the journal just leaves those facts out."""
    line_key, other_key = f"boxsummary_line_{game_id}", f"boxsummary_other_{game_id}"
    line, other = cache.read_payload(line_key), cache.read_payload(other_key)
    if line and other and line.get("data") and other.get("data"):
        return {"line": pd.DataFrame(line["data"]), "other": pd.DataFrame(other["data"])}
    try:
        from nba_api.stats.endpoints import boxscoresummaryv3
        summary = boxscoresummaryv3.BoxScoreSummaryV3(game_id=game_id, timeout=15)
        status = summary.game_summary.get_data_frame()
        line_df = summary.line_score.get_data_frame()
        other_df = summary.other_stats.get_data_frame()
    except Exception:
        return None
    if line_df.empty or other_df.empty:
        return None
    if not status.empty and int(status.iloc[0].get("gameStatus", 0) or 0) == 3:
        cache._save_df_cache(line_key, line_df)
        cache._save_df_cache(other_key, other_df)
    return {"line": line_df, "other": other_df}


def game_flow(summary, winner, loser):
    """Quarter-by-quarter and lead facts, or None without a summary."""
    if summary is None:
        return None
    line = summary["line"].set_index("teamTricode") if "teamTricode" in summary["line"] else None
    if line is None or winner not in line.index or loser not in line.index:
        return None

    def quarters(team):
        row = line.loc[team]
        qs = [int(row.get(q, 0) or 0) for q in QUARTERS]
        ot = int(row.get("score", sum(qs)) or 0) - sum(qs)
        return qs, max(ot, 0)

    (qw, otw), (ql, otl) = quarters(winner), quarters(loser)
    flow = {"qw": qw, "ql": ql, "otw": otw, "otl": otl, "overtime": bool(otw or otl),
            "half": (qw[0] + qw[1], ql[0] + ql[1])}
    gaps = [w - l for w, l in zip(qw, ql)]
    best = max(range(4), key=lambda i: gaps[i])
    flow["decisive"] = (ORDINAL[best], qw[best], ql[best]) if gaps[best] >= DECISIVE_QUARTER else None
    return flow


# ------------------------------------------------------------- projections

def _gamelog(player_id, season):
    payload = cache.read_payload(f"gamelog_{player_id}_{season}")
    frame = pd.DataFrame((payload or {}).get("data") or [])
    if frame.empty or "GAME_DATE" not in frame.columns:
        return frame
    frame["_date"] = frame["GAME_DATE"].map(parse_game_date)
    return frame


def pregame_projection(player_id, day):
    """{stat: (low, mid, high)} from games strictly before `day`, or None.

    Mirrors the app's season choice: this season once a player has five
    games in it, otherwise last season's whole log."""
    season = season_for_date(day)
    start = int(season[:4])
    previous = f"{start - 1}-{str(start)[-2:]}"
    frame = _gamelog(player_id, season)
    if not frame.empty:
        frame = frame[frame["_date"].map(lambda d: d is not None and d < day)]
    if len(frame) < MIN_PRIOR_GAMES:
        frame = _gamelog(player_id, previous)
    if len(frame) < MIN_PRIOR_GAMES:
        return None
    stats, n_games = stats_from_gamelog(frame.drop(columns=["_date"], errors="ignore"))
    out = {}
    for col, _label, _short in HEADLINE:
        mean, spread = stats.get(col, (None, None))
        dist = distribution_for(col, mean, spread, n_games)
        if dist is None:
            continue
        low, high = dist.interval(0.8)
        out[col] = (round(low, 1), round(dist.quantile(0.5), 1), round(high, 1))
    return out or None


# ------------------------------------------------------------- one game

def _minutes(value):
    text = str(value or "").strip()
    if not text:
        return 0.0
    if ":" in text:
        m, _, s = text.partition(":")
        try:
            return int(m) + int(float(s)) / 60
        except ValueError:
            return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _n(v):
    """'12' for 12.0, '12.5' for 12.5 -- the one way numbers are written."""
    f = float(v)
    return str(int(f)) if f == int(f) else f"{f:.1f}"


def _count(v, label):
    """'1 rebound', '0 rebounds', '12.5 points'."""
    return f"{_n(v)} {label[:-1] if float(v) == 1 else label}"


def team_totals(box, abbr):
    t = box[box["teamTricode"] == abbr]
    fga = t["fieldGoalsAttempted"].sum()
    return {
        "pts": int(t["points"].sum()),
        "fg_pct": round(100 * t["fieldGoalsMade"].sum() / fga, 1) if fga else 0.0,
        "fg3m": int(t["threePointersMade"].sum()),
        "fg3a": int(t["threePointersAttempted"].sum()),
        "fta": int(t["freeThrowsAttempted"].sum()),
        "oreb": int(t["reboundsOffensive"].sum()),
        "ast": int(t["assists"].sum()),
        "tov": int(t["turnovers"].sum()),
    }


def edges(winner, loser, tw, tl):
    """Box-score gaps big enough to mention, biggest first (relative to
    each one's threshold), as sentences that favour the winner or not."""
    found = []
    for key, sentence, threshold, higher_better in EDGES:
        if tw.get(key) is None or tl.get(key) is None:
            continue
        gap = tw[key] - tl[key]
        if abs(gap) < threshold:
            continue
        helped_winner = (gap > 0) == higher_better
        lead, trail = (winner, loser) if gap > 0 else (loser, winner)
        lead_v, trail_v = (tw[key], tl[key]) if gap > 0 else (tl[key], tw[key])
        text = sentence.format(lead=lead, a=_n(lead_v), trail=trail, b=_n(trail_v))
        if not helped_winner:
            text += f" ({winner} won despite it)"
        found.append((abs(gap) / threshold, text))
    return [text for _, text in sorted(found, reverse=True)][:MAX_EDGES]


def analyse_game(game_id, home, away, day, project=None):
    project = project or pregame_projection
    box = load_boxscore(game_id)
    if box is None or box.empty:
        return {"game_id": game_id, "home": home, "away": away, "missing": True}

    box = box.copy()
    box["min"] = box["minutes"].map(_minutes)
    # The box score is the authority on who played. The game list only
    # supplies home/away, and a team-code mismatch between the two must
    # not turn into a 0-0 game.
    teams = list(dict.fromkeys(box["teamTricode"]))
    if len(teams) != 2:
        return {"game_id": game_id, "home": home, "away": away, "missing": True}
    if home not in teams:
        home = None
    (winner, tw), (loser, tl) = sorted(((t, team_totals(box, t)) for t in teams),
                                       key=lambda x: -x[1]["pts"])
    summary = load_summary(game_id)
    if summary is not None and "teamTricode" in summary["other"]:
        other = summary["other"].set_index("teamTricode")
        for team, totals in ((winner, tw), (loser, tl)):
            if team in other.index:
                for col, key in SUMMARY_STATS.items():
                    value = other.loc[team].get(col)
                    try:
                        totals[key] = int(float(value))
                    except (TypeError, ValueError):
                        pass  # missing or NaN: that fact is simply left out
    flow = game_flow(summary, winner, loser)

    stars, absent, surprises, checked, inside = [], [], [], 0, 0
    for _, p in box.iterrows():
        name = f"{p['firstName']} {p['familyName']}".strip()
        proj = project(int(p["personId"]), day)
        if p["min"] <= 0:
            comment = str(p.get("comment") or "").strip()
            if proj and proj.get("PTS", (0, 0, 0))[1] >= NOTABLE_PROJECTED_PTS and comment:
                absent.append({"name": name, "team": p["teamTricode"], "why": comment})
            continue
        if proj:
            for col, label, _short in HEADLINE:
                if col not in proj:
                    continue
                low, mid, high = proj[col]
                actual = float(p[BOX_TO_STAT[col]])
                checked += 1
                if low <= actual <= high:
                    inside += 1
                    continue
                miss = (actual - high) if actual > high else (low - actual)
                if miss < MIN_MISS[col]:
                    continue
                surprises.append({
                    "name": name, "team": p["teamTricode"], "label": label,
                    "min": round(p["min"]),
                    "actual": actual, "low": low, "high": high,
                    "direction": "above" if actual > high else "below",
                    "size": miss / max(high - low, 1.0),
                })
        stars.append({"name": name, "team": p["teamTricode"], "pts": int(p["points"]),
                      "reb": int(p["reboundsTotal"]), "ast": int(p["assists"]),
                      "min": round(p["min"])})

    top = []
    for team in (winner, loser):
        top += sorted((s for s in stars if s["team"] == team), key=lambda s: -s["pts"])[:2]
    surprises.sort(key=lambda s: -s["size"])

    return {
        "game_id": game_id, "home": home, "away": away, "missing": False,
        "winner": winner, "loser": loser, "tw": tw, "tl": tl,
        "margin": tw["pts"] - tl["pts"], "flow": flow,
        "edges": edges(winner, loser, tw, tl),
        "top": top, "absent": absent,
        "surprises": surprises[:SURPRISES_PER_GAME],
        "checked": checked, "inside": inside,
    }


def game_facts(g):
    venue = f", played at {g['home']}" if g["home"] else ""
    facts = [f"Final: {g['winner']} {g['tw']['pts']}, {g['loser']} {g['tl']['pts']}"
             f"{venue}. {g['winner']} won by {g['margin']}."]
    facts += flow_facts(g)
    facts += g["edges"] or ["No team stat gap stood out; the box score was close across the board."]
    for s in g["top"]:
        facts.append(f"{s['name']} ({s['team']}): {s['pts']} points, {s['reb']} rebounds, {s['ast']} assists")
    for a in g["absent"]:
        facts.append(f"{a['name']} ({a['team']}) did not play: {a['why']}")
    for s in g["surprises"]:
        facts.append(f"{s['name']} had {_count(s['actual'], s['label'])} in {_count(s['min'], 'minutes')}, {s['direction']} "
                     f"the projected range of {_n(s['low'])} to {_n(s['high'])}")
    return facts


def flow_facts(g):
    """How the game unfolded, as sentences -- empty without a summary."""
    f, w, l = g.get("flow"), g["winner"], g["loser"]
    if not f:
        return []
    out = []
    hw, hl = f["half"]
    if hl > hw:
        out.append(f"{l} led {hl}–{hw} at halftime; {w} came back to win.")
    elif hw > hl:
        out.append(f"{w} led {hw}–{hl} at halftime.")
    else:
        out.append(f"It was tied {hw}–{hl} at halftime.")
    if f["decisive"]:
        name, a, b = f["decisive"]
        out.append(f"{w} won the {name} quarter {a}–{b}.")
    if f["overtime"]:
        out.append("The game went to overtime.")
    tw, tl = g["tw"], g["tl"]
    if tw.get("biggest_lead") is not None:
        out.append(f"{w}'s biggest lead was {tw['biggest_lead']}.")
    if (tl.get("biggest_lead") or 0) >= 10:
        out.append(f"{l} led by as many as {tl['biggest_lead']}.")
    if (tw.get("lead_changes") or 0) >= 1:
        out.append(f"The lead changed hands {tw['lead_changes']} times.")
    runs = [(t, d.get("run") or 0) for t, d in ((w, tw), (l, tl))]
    team, run = max(runs, key=lambda x: x[1])
    if run >= NOTABLE_RUN:
        out.append(f"{team} had a {run}-point run.")
    return out


def fallback_reason(g):
    """A plain 'why' built only from the game's own facts, for when the
    model is off or its line was rejected. Never empty for a played game."""
    w, margin, f = g["winner"], g["margin"], g.get("flow")
    if f and f["half"][1] > f["half"][0]:
        hw, hl = f["half"]
        first = f"{w} came back from {hl}–{hw} down at halftime to win by {margin}"
    elif f and f["decisive"]:
        name, a, b = f["decisive"]
        first = f"{w} won by {margin}, taking the {name} quarter {a}–{b}"
    else:
        first = f"{w} won by {margin}"
    sentences = [first + "."]
    # Only an edge that helped the winner explains the win.
    edge = next((e for e in g["edges"] if not e.endswith("won despite it)")), None)
    if edge:
        sentences.append(edge[0].upper() + edge[1:] + ".")
    lead = next((s for s in g["top"] if s["team"] == w), None)
    if lead:
        sentences.append(f"{lead['name']} led {w} with {lead['pts']} points.")
    return " ".join(sentences)


# ------------------------------------------------------------- the page

def render(day, games, source, reasons):
    played = [g for g in games if not g["missing"]]
    checked = sum(g["checked"] for g in played)
    inside = sum(g["inside"] for g in played)
    lines = [f"# NBA journal — {day.strftime('%a %-d %b %Y')} (US date)", ""]
    summary = f"{len(games)} game{'s' if len(games) != 1 else ''}"
    if checked:
        summary += (f" · projections: {inside} of {checked} points/rebounds/assists "
                    f"lines landed inside the 80% range ({round(100 * inside / checked)}%)")
    lines += [summary, ""]
    no_flow = [g for g in played if not g.get("flow")]
    if len(played) < len(games) or no_flow:
        gaps = []
        if len(played) < len(games):
            gaps.append(f"{len(games) - len(played)} box score(s)")
        if no_flow:
            gaps.append(f"quarter scores for {len(no_flow)} game(s)")
        lines += [INCOMPLETE_MARKER,
                  f"> {' and '.join(gaps).capitalize()} weren't available yet. "
                  "Run this again (with internet) to fill them in.", ""]

    for g in sorted(games, key=lambda g: g["game_id"]):
        if g["missing"]:
            lines += [f"## {g['away']} at {g['home']}", "", "_Box score not available yet._", ""]
            continue
        venue = f"  ·  at {g['home']}" if g["home"] else ""
        lines += [f"## {g['winner']} {g['tw']['pts']} – {g['tl']['pts']} {g['loser']}{venue}", ""]
        text, kind = reasons.get(g["game_id"]) or (fallback_reason(g), "auto")
        tag = " _(auto)_" if kind == "auto" else ""
        lines += [f"**Why:** {text}{tag}", ""]

        flow = g.get("flow")
        if flow:
            ot = flow["overtime"]
            head = "| | Q1 | Q2 | Q3 | Q4 |" + (" OT |" if ot else "") + " Final |"
            lines += [head, "|---" * (6 + ot) + "|"]
            for team, qs, extra, pts in ((g["winner"], flow["qw"], flow["otw"], g["tw"]["pts"]),
                                         (g["loser"], flow["ql"], flow["otl"], g["tl"]["pts"])):
                cells = " | ".join(str(q) for q in qs) + (f" | {extra}" if ot else "")
                lines.append(f"| {team} | {cells} | **{pts}** |")
            lines += [""] + [f"- {fact}" for fact in flow_facts(g)] + [""]

        lines += ["**What the box score shows**", ""]
        lines += [f"- {e}" for e in (g["edges"] or ["No big team stat gap — close across the board."])]
        extra_cols = [("paint", "Paint"), ("fastbreak", "Fast break"), ("second", "2nd chance"),
                      ("bench", "Bench")]
        extra_cols = [(k, h) for k, h in extra_cols if g["tw"].get(k) is not None]
        header = ["Team", "FG%", "3PT", "FTA", "OREB", "AST", "TOV"] + [h for _, h in extra_cols]
        lines += ["", "| " + " | ".join(header) + " |", "|---" * len(header) + "|"]
        for team, t in ((g["winner"], g["tw"]), (g["loser"], g["tl"])):
            cells = [team, f"{_n(t['fg_pct'])}%", f"{t['fg3m']}/{t['fg3a']}", str(t["fta"]),
                     str(t["oreb"]), str(t["ast"]), str(t["tov"])]
            cells += [str(t.get(k, "")) for k, _ in extra_cols]
            lines.append("| " + " | ".join(cells) + " |")
        lines += ["", "**Top performers:** " + "; ".join(
            f"{s['name']} ({s['team']}) {s['pts']} pts, {s['reb']} reb, {s['ast']} ast"
            for s in g["top"])]
        if g["absent"]:
            lines += ["", "**Didn't play:** " + "; ".join(
                f"{a['name']} ({a['team']}) — {a['why']}" for a in g["absent"])]
        if g["surprises"]:
            lines += ["", "**Projection misses:**", ""]
            lines += [f"- {s['name']}: {_count(s['actual'], s['label'])} in {_count(s['min'], 'minutes')}, {s['direction']} "
                      f"the {_n(s['low'])}–{_n(s['high'])} range" for s in g["surprises"]]
        if g["checked"]:
            lines += ["", f"_Projections this game: {g['inside']} of {g['checked']} lines in range._"]
        lines.append("")

    lines += ["---", "",
              f"_Games from {source}. Projections are the app's baseline, rebuilt from games "
              "before this date; no matchup adjustments. One night is noise — the season record "
              "is the real measure. \"Why\" lines are written by a local model from the facts "
              "on this page only and checked before they're kept; _(auto)_ marks a plain summary "
              "used when the model was off or its line didn't pass the check._", ""]
    return "\n".join(lines)


def journal_day(day, generate=None, out_dir=JOURNAL_DIR, force=False, log=print):
    path = os.path.join(out_dir, f"{day.isoformat()}.md")
    if os.path.exists(path) and not force:
        with open(path) as f:
            if INCOMPLETE_MARKER not in f.read():
                log(f"{day}: already written ({path}) -- use --force to redo")
                return path
    games_by_id, source = list_games(day)
    if not games_by_id:
        log(f"{day}: no games found")
        return None

    games = [analyse_game(gid, g["home"], g["away"], day) for gid, g in games_by_id.items()]
    reasons = {}
    for g in games:
        if g["missing"] or generate is None:
            continue
        note, why_not = whisper.write_note(game_facts(g), generate, REASON_MAX_WORDS)
        label = f"{g['winner']} over {g['loser']}"
        if note:
            reasons[g["game_id"]] = (note, "model")
            log(f"  {label}: {note}")
        else:
            log(f"  {label}: model line rejected ({why_not}) -- using the plain summary")

    os.makedirs(out_dir, exist_ok=True)
    with open(path, "w") as f:
        f.write(render(day, games, source, reasons))
    log(f"{day}: {len(games)} games -> {path}")
    return path


def main(argv=None, generate=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--date", type=datetime.date.fromisoformat, default=None,
                    help="US date YYYY-MM-DD (default: US Eastern yesterday)")
    ap.add_argument("--days", type=int, default=1, help="this many nights, ending at --date")
    ap.add_argument("--no-model", action="store_true", help="skip the written reason lines")
    ap.add_argument("--model", default=whisper.DEFAULT_MODEL)
    ap.add_argument("--force", action="store_true", help="rewrite nights already written")
    ap.add_argument("--out-dir", default=JOURNAL_DIR)
    args = ap.parse_args(argv)

    end = args.date or us_yesterday()
    if generate is None and not args.no_model:
        generate = whisper.make_generator(args.model, REASON_PROMPT, max_tokens=160)
    for offset in range(args.days - 1, -1, -1):
        journal_day(end - datetime.timedelta(days=offset), generate if not args.no_model else None,
                    out_dir=args.out_dir, force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
