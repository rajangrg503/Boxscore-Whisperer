#!/usr/bin/env python3
"""Our model's most likely box score, as X-ready card images.

    python3 tools/box_cards.py 2026-11-15
    python3 tools/box_cards.py 2026-11-15 --out journal/cards/2026-11-15.html

WHY
Since 6 Nov the box-score post has shown one dark card per game, two
cards per image. The card was built by a script that lived only in
Claude's scratch space, and it was rebuilt by hand in the browser on
15 Nov. This puts it in the repo.

WHAT IT DOES
- Reads the Journal's slate, journal/slate/<US date>.json ("Project these
  games"): who plays, minutes, rebounds, assists, who is out or
  day-to-day.
- Takes each game's SCORE from the team ratings (tools/team_defense.py,
  games before the slate date, half-life 6) and scales each team's player
  points so they add up to it (the format agreed on 9 Nov). The ratings do
  not adjust for injuries; the card says so.
- Writes one self-contained HTML page: each two-card image is a section
  exactly one viewport tall, so the browser can scroll to section N and
  screenshot it. An odd game count gets a slate-summary card beside the
  last game.
- Prints the post text (every score, ≤ 280 characters).

journal/ is gitignored, so the HTML stays out of the repository.
"""
import argparse
import datetime
import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
REPO = os.path.dirname(HERE)
SYDNEY = datetime.timezone(datetime.timedelta(hours=11))  # AEDT; good Oct–Apr
SUFFIX = re.compile(r"\s+(Jr\.?|Sr\.?|II|III|IV)$")
ROWS = 9
LONG = 17


def last_name(full):
    """'Jaren Jackson Jr.' -> 'Jackson Jr.', 'Dereck Lively II' -> 'Lively II'."""
    full = re.sub(r"\s*\(.*\)$", "", full).strip()
    m = SUFFIX.search(full)
    core = full[: m.start()] if m else full
    last = core.split()[-1] if core.split() else core
    return f"{last} {m.group(1)}" if m else last


def short_name(full, limit=LONG):
    """Full name unless it is too long for the card, then 'S. Gilgeous-Alexander'."""
    if len(full) <= limit:
        return full
    first = full.split()[0]
    return f"{first[0]}. {last_name(full)}"


def name_list(names):
    """Last names, but full names where two share a last name (OKC's two Williamses)."""
    lasts = [last_name(n) for n in names]
    return [re.sub(r"\s*\(.*\)$", "", n) if lasts.count(l) > 1 else l for n, l in zip(names, lasts)]


def tip_aedt(tipoff):
    t = datetime.datetime.fromisoformat(tipoff.replace("Z", "+00:00")).astimezone(SYDNEY)
    return t.strftime("%-I:%M%p").lower(), t


def scale(players, score):
    """Player points scaled to the team score; rounding can leave it a point or two off."""
    total = sum(p["PTS"] for p in players) or 1
    k = score / total
    return [dict(p, PTS=p["PTS"] * k) for p in players]


def capped(players):
    """Three or more players at the model's top minute figure looks like a cap, not a forecast."""
    mins = [round(p["min"]) for p in players]
    top = max(mins) if mins else 0
    return top >= 36 and mins.count(top) >= 3


def build(us_date, slate=None, scores=None):
    """Card data for every game. `slate` and `scores` can be passed in for tests."""
    if slate is None:
        slate = json.load(open(os.path.join(REPO, "journal", "slate", f"{us_date}.json")))
    if scores is None:
        import team_defense as td
        R = td.ratings(td.team_games(), datetime.date.fromisoformat(us_date), 6.0, False)
        scores = {}
        for g in slate["games"]:
            a, h = td.project(R, g["away"], g["home"])
            scores[(g["away"], g["home"])] = (round(a), round(h))
    games = []
    for g in sorted(slate["games"], key=lambda g: g["tipoff"]):
        a, h = g["away"], g["home"]
        sa, sh = scores[(a, h)]
        tip, t = tip_aedt(g["tipoff"])
        dtd = g.get("day_to_day") if isinstance(g.get("day_to_day"), dict) else {}
        card = {"away": a, "home": h, "score": [sa, sh], "tip": tip, "day": t.strftime("%a %-d %b"), "teams": {}}
        for team, sc in ((a, sa), (h, sh)):
            ps = sorted(scale(g["players"].get(team, []), sc), key=lambda p: -p["min"])
            card["teams"][team] = {
                "rows": [[short_name(p["name"]), round(p["min"]), round(p["PTS"]), round(p["REB"]), round(p["AST"])]
                         for p in ps[:ROWS]],
                "out": name_list(g.get("out", {}).get(team, [])),
                "questionable": name_list(dtd.get(team, [])),
                "capped": capped(ps),
            }
        games.append(card)
    return games


def post_text(games):
    day = games[0]["day"] if games else ""
    lines = [f"{day}: our model's most likely box scores", ""]
    lines += [f"{c['away']} {c['score'][0]} @ {c['home']} {c['score'][1]}" for c in games]
    lines += ["", "Scores: team ratings, no injury adjustment.", "Not betting advice. 18+"]
    return "\n".join(lines)


CSS = """
*{box-sizing:border-box}html,body{margin:0;background:#0b0f14;font-family:Inter,Helvetica,Arial,sans-serif}
section{height:100vh;display:flex;gap:4px;padding:4px}
.card{flex:1;height:100%;background:linear-gradient(180deg,#111827,#0b0f14);border:1px solid #1f2937;border-radius:14px;padding:12px 18px;color:#e5e7eb;display:flex;flex-direction:column;overflow:hidden}
.top{font-size:13px;letter-spacing:2px;color:#22c55e;font-weight:700}
.mt{font-size:30px;font-weight:800;margin:2px 0 4px}.mt span{color:#6b7280;font-weight:600}.mt .tip{font-size:13px;margin-left:10px;color:#9ca3af}
.team{margin-top:8px}.th{display:flex;justify-content:space-between;align-items:baseline;border-bottom:1px solid #374151;padding-bottom:2px}
.ab{font-size:21px;font-weight:800}.sc{font-size:30px;font-weight:800}.win{color:#22c55e}
table{width:100%;border-collapse:collapse;font-size:16px}td{padding:2.6px 4px;text-align:right;color:#d1d5db}
td.n{text-align:left;color:#f3f4f6}td.b{font-weight:700;color:#fff}tr.hd td{font-size:11.5px;color:#6b7280;letter-spacing:1px}
.ou{font-size:13px;color:#9ca3af;margin-top:3px}.ou b{color:#d1d5db}.flag{color:#f59e0b}
.ft{margin-top:auto;font-size:11px;color:#6b7280;border-top:1px solid #1f2937;padding-top:5px}
"""
FOOT = ('<div class=ft>Score: team ratings (no injury adjustment). Players: our player model.<br>'
        '@BoxscoreWhisper · Not betting advice. 18+</div>')


def _team(c, t):
    d = c["teams"][t]
    sc = c["score"][0] if t == c["away"] else c["score"][1]
    win = sc == max(c["score"]) and c["score"][0] != c["score"][1]
    rows = "".join(f"<tr><td class=n>{html.escape(r[0])}</td><td>{r[1]}</td><td class=b>{r[2]}</td><td>{r[3]}</td><td>{r[4]}</td></tr>"
                   for r in d["rows"])
    notes = "<b>Out:</b> " + (html.escape(", ".join(d["out"])) or "none")
    if d["questionable"]:
        notes += " &nbsp;·&nbsp; <b>Questionable:</b> " + html.escape(", ".join(d["questionable"]))
    if d["capped"]:
        notes += " &nbsp;·&nbsp; <span class=flag>minutes capped by the model</span>"
    return (f"<div class=team><div class=th><span class=ab>{t}</span><span class='sc{' win' if win else ''}'>{sc}</span></div>"
            f"<table><tr class=hd><td class=n>PLAYER</td><td>MIN</td><td>PTS</td><td>REB</td><td>AST</td></tr>{rows}</table>"
            f"<div class=ou>{notes}</div></div>")


def card_html(c):
    return (f"<div class=card><div class=top>OUR MODEL'S MOST LIKELY BOX SCORE</div>"
            f"<div class=mt>{c['away']} <span>@</span> {c['home']} <span class=tip>{c['day']} · {c['tip']} AEDT</span></div>"
            f"{_team(c, c['away'])}{_team(c, c['home'])}{FOOT}</div>")


def summary_html(games, late=()):
    rows = "".join(f"<tr><td class=n>{c['away']} @ {c['home']}</td><td>{c['tip']}</td><td class=b>{c['score'][0]}–{c['score'][1]}</td></tr>"
                   for c in games)
    late_line = f"<div class=ou style='font-size:13px;margin-top:12px'><b>Late calls:</b> {html.escape(' · '.join(late))}</div>" if late else ""
    return (f"<div class=card><div class=top>{games[0]['day'].upper()} · SLATE SUMMARY</div><div class=mt>{len(games)} games</div>"
            f"<table><tr class=hd><td class=n>GAME</td><td>TIP (AEDT)</td><td>OUR SCORE</td></tr>{rows}</table>{late_line}{FOOT}</div>")


def page(games):
    late = []
    for c in games:
        for t in (c["away"], c["home"]):
            late += c["teams"][t]["questionable"]
    sections = []
    for i in range(0, len(games), 2):
        pair = games[i:i + 2]
        right = card_html(pair[1]) if len(pair) == 2 else summary_html(games, late)
        sections.append(f"<section id=s{i // 2}>{card_html(pair[0])}{right}</section>")
    return f"<!doctype html><html><head><meta charset=utf-8><style>{CSS}</style></head><body>{''.join(sections)}</body></html>"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("us_date")
    ap.add_argument("--out", help="HTML path (default journal/cards/<US date>.html)")
    ap.add_argument("--json", action="store_true", help="print the card data instead of writing HTML")
    a = ap.parse_args(argv)
    games = build(a.us_date)
    if a.json:
        print(json.dumps(games, ensure_ascii=False, separators=(",", ":")))
        return
    out = a.out or os.path.join(REPO, "journal", "cards", f"{a.us_date}.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w").write(page(games))
    text = post_text(games)
    print(out)
    print(f"{len(games)} games, {(len(games) + 1) // 2} images\n--- post text ({len(text)} chars) ---\n{text}")


if __name__ == "__main__":
    main()
