"""Boxscore Journal -- a page on your Mac for reading and writing the
nightly game journal.

    python3 -m streamlit run tools/journal_app.py

Opens in your browser at http://localhost:8501. It runs on your Mac only:
Streamlit Community Cloud deploys app.py, not this file, and Llama only
runs here anyway. It reads and writes the same journal/ folder as
tools/daily_journal.py, so the Terminal command and this page always
agree.

WHAT IT SHOWS
  Night   -- every game on one night: the score, the Why line (and who
             wrote it), quarter by quarter, then the box score, the
             projection misses and the exact facts Llama was given.
  Misses  -- where the projections go wrong across every night: by stat,
             whether the misses came from minutes or per-minute output,
             and the players who keep missing the same way.
  Picks   -- your own bet slips: drop in Sportsbet screenshots, check what
             was read, and every leg is graded against the box score and
             the app's projection. Describes how picks went; never
             suggests one.
  Season  -- how often the projections landed in their 80% range, night
             by night, and how many Why lines Llama wrote.

The sidebar writes a night: pick the US date, press the button, and each
game appears as Llama finishes it.
"""

import datetime
import glob
import html
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

import daily_journal as dj  # noqa: E402
import journal_insights as ji  # noqa: E402
import picks as pk  # noqa: E402
import slip_check as sc  # noqa: E402
import whisper  # noqa: E402

JOURNAL_DIR = os.environ.get("BW_JOURNAL_DIR", "").strip() or dj.JOURNAL_DIR
PICKS_DIR = os.path.join(JOURNAL_DIR, "picks")
MODEL = whisper.DEFAULT_MODEL
FAVICON = os.path.join(REPO_ROOT, "assets", "favicon.png")

st.set_page_config(
    page_title="Boxscore Journal",
    page_icon=FAVICON if os.path.exists(FAVICON) else None,
    layout="wide",
    initial_sidebar_state="auto",
)

# Same tokens as app.py, so the journal reads as part of the same product.
st.markdown("""
<style>
:root {
    --jr-bg: #0a0d12; --jr-surface: #11151c; --jr-border: #252b38;
    --jr-text: #e7eaf0; --jr-muted: #9aa3b2; --jr-faint: #6b7385;
    --jr-green: #22c55e; --jr-green-text: #4ade80; --jr-green-soft: rgba(34,197,94,.10);
    --jr-amber: #fbbf24; --jr-amber-soft: rgba(251,191,36,.10); --jr-amber-line: rgba(251,191,36,.26);
}
.block-container { max-width: 920px; padding-top: 2.4rem; padding-bottom: 4rem; }
section[data-testid="stSidebar"] .block-container { padding-top: 1.6rem; }

.jr-brand { font-weight: 800; font-size: 1.05rem; letter-spacing: -0.01em; margin: 0 0 1.2rem; }
.jr-brand span { color: var(--jr-green-text); }

.jr-date { font-size: clamp(1.7rem, 4vw, 2.3rem); font-weight: 800; letter-spacing: -0.025em;
           line-height: 1.1; margin: 0; }
.jr-summary { color: var(--jr-muted); font-size: 1rem; line-height: 1.55; margin: .5rem 0 1.4rem;
              max-width: 62ch; }
.jr-summary b { color: var(--jr-text); font-weight: 600; font-variant-numeric: tabular-nums; }

.jr-note { border: 1px solid var(--jr-amber-line); background: var(--jr-amber-soft);
           color: var(--jr-text); border-radius: 10px; padding: .7rem .9rem; font-size: .92rem;
           margin: 0 0 1.4rem; line-height: 1.5; }

.jr-score { display: flex; align-items: baseline; flex-wrap: wrap; column-gap: .55rem; row-gap: .2rem;
            font-variant-numeric: tabular-nums; }
.jr-score .team { font-weight: 700; font-size: 1.05rem; color: var(--jr-faint); }
.jr-score .pts { font-weight: 800; font-size: 2.1rem; line-height: 1; color: var(--jr-faint); }
.jr-score .win { color: var(--jr-text); }
.jr-score .sep { color: var(--jr-faint); font-size: 1.4rem; }
.jr-score .where { margin-left: auto; color: var(--jr-faint); font-size: .86rem; }

.jr-why { font-size: 1.2rem; line-height: 1.55; font-weight: 500; color: var(--jr-text);
          border-left: 2px solid var(--jr-green); padding: .05rem 0 .05rem 1rem;
          margin: 1rem 0 .4rem; max-width: 64ch; }
.jr-why.auto { border-left-color: var(--jr-amber); }
.jr-by { font-size: .83rem; color: var(--jr-faint); margin: 0 0 1rem 1.1rem; }

.stMarkdown table.jr-q { border-collapse: collapse; border: none; font-variant-numeric: tabular-nums;
        font-size: .9rem; margin: .1rem 0 .5rem; width: auto; }
.stMarkdown table.jr-q tr, .stMarkdown table.jr-q th, .stMarkdown table.jr-q td {
        border: none; background: transparent; }
.stMarkdown table.jr-q th { color: var(--jr-faint); font-weight: 500; text-align: right;
        padding: .1rem .9rem .25rem; }
.stMarkdown table.jr-q td { text-align: right; padding: .12rem .9rem; color: var(--jr-faint); }
.stMarkdown table.jr-q th:first-child, .stMarkdown table.jr-q td:first-child {
        text-align: left; padding-left: 0; font-weight: 600; }
.stMarkdown table.jr-q tr.win td { color: var(--jr-text); }
.stMarkdown table.jr-q td.final { font-weight: 700; }
.stMarkdown table.jr-q tr td.won { color: var(--jr-green-text); font-weight: 700; }

.jr-missing { color: var(--jr-muted); font-size: .95rem; margin: 0; }
.jr-missing b { color: var(--jr-text); }

.jr-list { margin: .2rem 0 .8rem; padding-left: 1.1rem; line-height: 1.6; }
.jr-list li { margin: .15rem 0; }
.jr-facts { font-size: .9rem; color: var(--jr-muted); }
.jr-h { font-weight: 600; font-size: .95rem; margin: .4rem 0 .2rem; }

.jr-status { font-size: .88rem; color: var(--jr-muted); line-height: 1.45; margin: .2rem 0 .6rem; }
.jr-status .dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%;
                  margin-right: .45rem; vertical-align: .08rem; background: var(--jr-faint); }
.jr-status.ready .dot { background: var(--jr-green); box-shadow: 0 0 0 3px var(--jr-green-soft); }
.jr-status code { font-size: .8rem; white-space: nowrap; }

.jr-h2 { font-weight: 700; font-size: 1.15rem; letter-spacing: -0.01em; margin: 2rem 0 .3rem; }
.jr-p { color: var(--jr-muted); line-height: 1.6; max-width: 64ch; margin: 0 0 .8rem; }
.jr-p b { color: var(--jr-text); font-weight: 600; font-variant-numeric: tabular-nums; }
.jr-split { display: flex; height: 10px; border-radius: 5px; overflow: hidden; max-width: 520px;
            margin: .6rem 0 .4rem; background: var(--jr-border); }
.jr-split span { display: block; height: 100%; }
.jr-stat { display: grid; grid-template-columns: 5.5rem minmax(0, 1fr) auto; align-items: center;
           column-gap: 1rem; margin: .55rem 0; max-width: 760px; }
.jr-stat .jr-split { margin: 0; max-width: none; }
.jr-stat-name { font-weight: 600; }
.jr-stat-nums { font-size: .88rem; color: var(--jr-muted); font-variant-numeric: tabular-nums;
                white-space: nowrap; }
.jr-stat-nums b { color: var(--jr-text); font-weight: 600; }
.jr-faint { color: var(--jr-faint); }
@media (max-width: 640px) {
  .jr-stat { grid-template-columns: 5rem minmax(0, 1fr); row-gap: .25rem; }
  .jr-stat-nums { grid-column: 2; }
}
.jr-key { font-size: .85rem; color: var(--jr-muted); margin: 0 0 .6rem; }
.jr-key i { display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin: 0 .35rem 0 .9rem;
            vertical-align: -.05rem; }
.jr-key i:first-child { margin-left: 0; }
.stMarkdown table.jr-legs { width: 100%; border-collapse: collapse; border: none; font-size: .92rem;
           font-variant-numeric: tabular-nums; margin: .6rem 0 .2rem; }
.stMarkdown table.jr-legs tr, .stMarkdown table.jr-legs td { border: none; background: transparent; }
.stMarkdown table.jr-legs td { padding: .34rem .7rem .34rem 0; border-top: 1px solid var(--jr-border);
              vertical-align: top; color: var(--jr-muted); }
.stMarkdown table.jr-legs tr:first-child td { border-top: none; }
.stMarkdown table.jr-legs td.who { color: var(--jr-text); font-weight: 500; }
.stMarkdown table.jr-legs td.num { text-align: right; white-space: nowrap; }
.stMarkdown table.jr-legs td.num b { color: var(--jr-text); font-weight: 600; }
.jr-legs .m-proj { display: none; }
@media (max-width: 640px) {
  .stMarkdown table.jr-legs td.proj { display: none; }
  .jr-legs .m-proj { display: block; font-size: .8rem; color: var(--jr-faint); margin-top: .1rem; }
  .stMarkdown table.jr-legs td.who { width: 40%; }
}
.jr-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: .1rem;
          background: var(--jr-faint); vertical-align: .1rem; }
.jr-dot.won { background: #22c55e; } .jr-dot.lost { background: #f87171; }
.jr-slip-head { display: flex; align-items: baseline; flex-wrap: wrap; gap: .2rem .8rem; }
.jr-slip-head .title { font-weight: 700; font-size: 1.08rem; }
.jr-slip-head .when { color: var(--jr-faint); font-size: .86rem; }
.jr-chip { margin-left: auto; font-size: .82rem; font-weight: 600; padding: .12rem .55rem; border-radius: 999px;
           border: 1px solid var(--jr-border); color: var(--jr-muted); }
.jr-chip.won { color: #4ade80; border-color: rgba(34,197,94,.35); }
.jr-chip.lost { color: #f87171; border-color: rgba(248,113,113,.3); }
.jr-cal { grid-template-columns: 9.5rem minmax(0, 1fr) auto; }
.jr-mark { position: absolute; top: -3px; width: 2px; height: 16px; background: #e7eaf0;
           border-radius: 1px; }
@media (max-width: 640px) { .jr-cal { grid-template-columns: 8rem minmax(0, 1fr); } }
.jr-warn li { color: var(--jr-text); }
.jr-warn li::marker { color: #fbbf24; }
.jr-empty { border: 1px dashed var(--jr-border); border-radius: 12px; padding: 2rem 1.4rem;
            color: var(--jr-muted); max-width: 52ch; line-height: 1.6; }
.jr-empty b { color: var(--jr-text); }
</style>
""", unsafe_allow_html=True)


# ------------------------------------------------------------------ data

def esc(value):
    return html.escape(str(value))


def keep_scores_together(text):
    """'63–61' must not break across lines: glue the dash to its numbers."""
    import re
    return re.sub(r"(\d)–(\d)", "\\1\u2060–\u2060\\2", text)


def nice_date(iso, long=False):
    d = datetime.date.fromisoformat(iso)
    return d.strftime("%A %-d %B" if long else "%a %-d %b")


def load_nights():
    """{iso date: night} for every night in the journal folder, newest
    first. A night written before this app has only its .md page."""
    nights = {}
    for path in glob.glob(os.path.join(JOURNAL_DIR, "*.md")):
        iso = os.path.basename(path)[:-3]
        try:
            datetime.date.fromisoformat(iso)
        except ValueError:
            continue
        with open(path) as f:
            nights[iso] = {"date": iso, "legacy": True, "md": f.read()}
    for path in glob.glob(os.path.join(JOURNAL_DIR, "*.json")):
        try:
            with open(path) as f:
                night = json.load(f)
            nights[night["date"]] = night
        except (OSError, ValueError, KeyError):
            continue
    return dict(sorted(nights.items(), reverse=True))


@st.cache_data(ttl=20, show_spinner=False)
def llama_status(model):
    """'ready', or what's missing, without ever waiting more than a moment."""
    try:
        import ollama
    except ImportError:
        return "no_package"
    try:
        ollama.Client(timeout=3).show(model)
        return "ready"
    except ollama.ResponseError:
        return "no_model"
    except Exception:
        return "not_running"


STATUS_TEXT = {
    "ready": "Llama is ready.",
    "not_running": "Llama isn't running. Open the Ollama app to have it write the Why lines.",
    "no_model": f"Llama isn't downloaded yet. In Terminal:<br><code>ollama pull {MODEL}</code>",
    "no_package": "The Llama connector isn't installed. In Terminal:<br>"
                  "<code>python3 -m pip install ollama</code>",
}


# ------------------------------------------------------------------ pieces

def summary_line(night):
    games = night.get("games", [])
    played = [g for g in games if not g.get("missing")]
    parts = [f"<b>{len(games)}</b> game{'s' if len(games) != 1 else ''}."]
    if night.get("checked"):
        pct = round(100 * night["inside"] / night["checked"])
        parts.append(f"Projections landed in the 80% range on <b>{night['inside']}</b> of "
                     f"<b>{night['checked']}</b> lines (<b>{pct}%</b>).")
    if played:
        by_model = sum(1 for g in played if (g.get("why") or {}).get("by") == "model")
        if by_model:
            parts.append(f"Llama wrote <b>{by_model}</b> of <b>{len(played)}</b> Why lines.")
        else:
            parts.append("All Why lines are plain summaries.")
    return " ".join(parts)


def who_wrote(why):
    if why.get("by") == "model":
        return "Written by Llama."
    if why.get("rejected"):
        return (f"Plain summary. Llama's line didn't pass the checks "
                f"({esc(why['rejected'])}).")
    if why.get("model_on"):
        return "Plain summary."
    return "Plain summary. Llama was off when this night was written."


def quarters_table(g):
    f = g.get("flow")
    if not f:
        return ""
    ot = f.get("overtime")
    heads = ["", "Q1", "Q2", "Q3", "Q4"] + (["OT"] if ot else []) + ["Final"]
    rows = []
    for team, qs, extra, pts, other in (
            (g["winner"], f["qw"], f["otw"], g["tw"]["pts"], f["ql"]),
            (g["loser"], f["ql"], f["otl"], g["tl"]["pts"], f["qw"])):
        is_win = team == g["winner"]
        cells = [f"<td>{esc(team)}</td>"]
        for mine, theirs in zip(qs, other):
            won = " class='won'" if is_win and mine - theirs >= dj.STORY_QUARTER else ""
            cells.append(f"<td{won}>{mine}</td>")
        if ot:
            cells.append(f"<td>{extra}</td>")
        cells.append(f"<td class='final'>{pts}</td>")
        rows.append(f"<tr class='{'win' if is_win else ''}'>{''.join(cells)}</tr>")
    return ("<table class='jr-q'><thead><tr>" + "".join(f"<th>{h}</th>" for h in heads)
            + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>")


def game_header(g):
    venue = f"at {esc(g['home'])}, " if g.get("home") else ""
    return (
        "<div class='jr-score'>"
        f"<span class='team win'>{esc(g['winner'])}</span>"
        f"<span class='pts win'>{g['tw']['pts']}</span>"
        "<span class='sep'>–</span>"
        f"<span class='pts'>{g['tl']['pts']}</span>"
        f"<span class='team'>{esc(g['loser'])}</span>"
        f"<span class='where'>{venue}won by {g['margin']}</span>"
        "</div>"
    )


def render_details(g):
    box, misses, given = st.tabs(["Box score", "Projection misses", "What Llama was given"])
    with box:
        left, right = st.columns([1.1, 1], gap="large")
        with left:
            st.markdown("<p class='jr-h'>What separated the teams</p>", unsafe_allow_html=True)
            edges = g.get("edges") or ["No big team stat gap; it was close across the board."]
            st.markdown("<ul class='jr-list'>" + "".join(f"<li>{esc(e)}</li>" for e in edges)
                        + "</ul>", unsafe_allow_html=True)
            st.markdown("<p class='jr-h'>Top performers</p>", unsafe_allow_html=True)
            st.markdown("<ul class='jr-list'>" + "".join(
                f"<li>{esc(s['name'])} ({esc(s['team'])}): {s['pts']} pts, {s['reb']} reb, "
                f"{s['ast']} ast</li>" for s in g.get("top", [])) + "</ul>", unsafe_allow_html=True)
            if g.get("absent"):
                st.markdown("<p class='jr-h'>Didn't play</p>", unsafe_allow_html=True)
                st.markdown("<ul class='jr-list'>" + "".join(
                    f"<li>{esc(a['name'])} ({esc(a['team'])}): {esc(a['why'])}</li>"
                    for a in g["absent"]) + "</ul>", unsafe_allow_html=True)
        with right:
            cols = [("fg_pct", "FG%"), ("fg3m", "3PM"), ("fta", "FTA"), ("oreb", "OREB"),
                    ("ast", "AST"), ("tov", "TOV"), ("paint", "Paint"), ("fastbreak", "Fast break"),
                    ("second", "2nd chance"), ("bench", "Bench")]
            table = {"": [g["winner"], g["loser"]]}
            for key, label in cols:
                if g["tw"].get(key) is not None:
                    table[label] = [g["tw"][key], g["tl"][key]]
            frame = pd.DataFrame(table).set_index("").T
            st.dataframe(frame, use_container_width=True)
    with misses:
        if g.get("surprises"):
            st.markdown("<ul class='jr-list'>" + "".join(
                f"<li>{esc(s['name'])}: {dj._count(s['actual'], s['label'])} in "
                f"{dj._count(s['min'], 'minutes')}, {s['direction']} the "
                f"{dj._n(s['low'])}–{dj._n(s['high'])} range</li>"
                for s in g["surprises"]) + "</ul>", unsafe_allow_html=True)
        else:
            st.markdown("<p class='jr-facts'>No projection was far off in this game.</p>",
                        unsafe_allow_html=True)
        if g.get("checked"):
            st.caption(f"{g['inside']} of {g['checked']} projection lines landed in range.")
    with given:
        st.markdown("<p class='jr-facts'>Llama sees only these facts, with the main story "
                    "first. Python picks the main story; Llama's line is rejected if it leaves "
                    "it out, adds a number that isn't here, or uses betting language.</p>",
                    unsafe_allow_html=True)
        st.markdown("<ul class='jr-list jr-facts'>" + "".join(
            f"<li>{esc(fact)}</li>" for fact in g.get("facts", [])) + "</ul>",
            unsafe_allow_html=True)


def render_game(g):
    with st.container(border=True):
        if g.get("missing"):
            st.markdown(f"<p class='jr-missing'><b>{esc(g.get('away'))} at {esc(g.get('home'))}</b>. "
                        "The box score wasn't in yet. Rewrite this night to add it.</p>",
                        unsafe_allow_html=True)
            return
        why = g.get("why") or {"text": dj.fallback_reason(g), "by": "auto"}
        kind = "" if why.get("by") == "model" else " auto"
        st.markdown(
            game_header(g)
            + f"<p class='jr-why{kind}'>{keep_scores_together(esc(why['text']))}</p>"
            + f"<p class='jr-by'>{who_wrote(why)}</p>"
            + quarters_table(g),
            unsafe_allow_html=True)
        with st.expander("Box score, misses and Llama's facts"):
            render_details(g)


def render_night(night):
    st.markdown(f"<h1 class='jr-date'>{nice_date(night['date'], long=True)}</h1>",
                unsafe_allow_html=True)
    if night.get("legacy"):
        st.markdown("<p class='jr-summary'>Written before the journal app, so it's shown as a "
                    "plain page. Rewrite this night from the sidebar to see it as game cards.</p>",
                    unsafe_allow_html=True)
        st.markdown(night["md"].split("\n", 1)[1] if "\n" in night["md"] else night["md"])
        return
    st.markdown(f"<p class='jr-summary'>{summary_line(night)}</p>", unsafe_allow_html=True)
    if night.get("llama_missing"):
        st.markdown("<div class='jr-note'>Llama wasn't running when this night was written, so "
                    "the Why lines are plain summaries. The morning job writes it again once Llama "
                    "is running, or rewrite it from the sidebar.</div>", unsafe_allow_html=True)
    elif not night.get("complete"):
        st.markdown("<div class='jr-note'>Some box scores or quarter scores weren't in yet when "
                    "this night was written. Rewrite it from the sidebar to fill them in.</div>",
                    unsafe_allow_html=True)
    for g in night.get("games", []):
        render_game(g)


def render_season(nights):
    data = [n for n in nights.values() if not n.get("legacy") and n.get("checked")]
    if not data:
        st.markdown("<div class='jr-empty'><b>No season data yet.</b> Write a night from the "
                    "sidebar and it will show up here.</div>", unsafe_allow_html=True)
        return
    rows = []
    for n in sorted(data, key=lambda n: n["date"]):
        played = [g for g in n["games"] if not g.get("missing")]
        rows.append({
            "Night": nice_date(n["date"]), "date": n["date"],
            "Games": len(n["games"]),
            "In range": n["inside"], "Lines": n["checked"],
            "pct": round(100 * n["inside"] / n["checked"], 1),
            "Llama lines": sum(1 for g in played if (g.get("why") or {}).get("by") == "model"),
        })
    frame = pd.DataFrame(rows)
    inside, lines = int(frame["In range"].sum()), int(frame["Lines"].sum())
    st.markdown("<h1 class='jr-date'>The season so far</h1>", unsafe_allow_html=True)
    st.markdown(
        f"<p class='jr-summary'>Across <b>{len(frame)}</b> night{'s' if len(frame) != 1 else ''}, "
        f"<b>{inside:,}</b> of <b>{lines:,}</b> projection lines landed in the 80% range "
        f"(<b>{round(100 * inside / lines)}%</b>). The ranges are built to hold about 80% of "
        "results, so a night well below the line is worth a look. One night on its own is noise."
        "</p>", unsafe_allow_html=True)

    base = alt.Chart(frame).encode(
        x=alt.X("date:O", title=None, axis=alt.Axis(labelExpr="timeFormat(toDate(datum.value), '%-d %b')",
                                                    labelAngle=0)),
    )
    y_scale = alt.Scale(domain=[50, 100], clamp=True)
    line = base.mark_line(color="#22c55e", strokeWidth=2).encode(
        y=alt.Y("pct:Q", title="Lines in range (%)", scale=y_scale))
    points = base.mark_circle(color="#22c55e", size=70).encode(
        y=alt.Y("pct:Q", scale=y_scale),
        tooltip=[alt.Tooltip("Night:N"), alt.Tooltip("pct:Q", title="In range (%)"),
                 alt.Tooltip("In range:Q"), alt.Tooltip("Lines:Q"), alt.Tooltip("Games:Q")])
    target = alt.Chart(pd.DataFrame({"y": [80]})).mark_rule(
        color="#6b7385", strokeDash=[4, 4]).encode(y=alt.Y("y:Q", scale=y_scale))
    st.altair_chart((target + line + points).properties(height=280)
                    .configure_view(strokeWidth=0)
                    .configure_axis(grid=True, gridColor="#1b202b", domainColor="#252b38",
                                    labelColor="#9aa3b2", titleColor="#9aa3b2"),
                    use_container_width=True)
    st.caption("Dashed line: the 80% the ranges are built to hold.")

    table = frame[["Night", "Games", "In range", "Lines", "pct", "Llama lines"]].iloc[::-1]
    st.dataframe(table.rename(columns={"pct": "In range (%)"}), hide_index=True,
                 use_container_width=True)


MISS_COLORS = {"below": "#f87171", "inside": "#22c55e", "above": "#60a5fa"}


def rebuild(days, area):
    """Rewrite nights without Llama, keeping the lines it already wrote."""
    with area.status(f"Rebuilding {len(days)} night{'s' if len(days) != 1 else ''}",
                     expanded=True) as status:
        for iso in days:
            status.write(f"{nice_date(iso, long=True)}")
            try:
                dj.journal_day(datetime.date.fromisoformat(iso), None, out_dir=JOURNAL_DIR,
                               force=True, log=lambda *a: None)
            except Exception as error:
                status.write(f"Couldn't rebuild {nice_date(iso)}: {error}")
        status.update(label="Rebuilt", state="complete", expanded=False)


def render_misses(nights):
    data = [n for n in nights.values() if not n.get("legacy")]
    frame = ji.lines_frame(data)
    with_lines = ji.nights_with_lines(data)
    without = sorted((n["date"] for n in data if n["date"] not in with_lines), reverse=True)
    without += sorted((iso for iso, n in nights.items() if n.get("legacy")), reverse=True)

    st.markdown("<h1 class='jr-date'>Where projections miss</h1>", unsafe_allow_html=True)
    if frame.empty:
        st.markdown("<div class='jr-empty'><b>Nothing to compare yet.</b> This tab needs nights "
                    "written by this version of the journal, which records every projection line. "
                    + ("Rebuild your older nights below to bring them in." if without else
                       "Write a night from the sidebar to start.") + "</div>",
                    unsafe_allow_html=True)
    else:
        st.markdown(f"<p class='jr-summary'>{ji.headline(frame, len(with_lines))} "
                    "These are the app's baseline projections, before any matchup adjustment.</p>",
                    unsafe_allow_html=True)
        if len(with_lines) < ji.MIN_NIGHTS:
            st.markdown(f"<div class='jr-note'>Only {len(with_lines)} night"
                        f"{'s' if len(with_lines) != 1 else ''} so far. A night's games move "
                        "together, so read these as hints until there are about "
                        f"{ji.MIN_NIGHTS}.</div>", unsafe_allow_html=True)

        # 1. by stat
        stats = ji.by_stat(frame)
        st.markdown("<p class='jr-h2'>By stat</p>", unsafe_allow_html=True)
        st.markdown("<p class='jr-p'>A well-sized 80% range holds about 80% of results and misses "
                    "about 10% on each side. A bigger slice on one side is a lean.</p>",
                    unsafe_allow_html=True)
        rows = []
        for _, r in stats.iterrows():
            rows.append(
                "<div class='jr-stat'>"
                f"<span class='jr-stat-name'>{esc(r['stat'])}</span>"
                "<div class='jr-split'>"
                f"<span style='width:{r['below']}%;background:{MISS_COLORS['below']}'></span>"
                f"<span style='width:{r['inside']}%;background:{MISS_COLORS['inside']}'></span>"
                f"<span style='width:{r['above']}%;background:{MISS_COLORS['above']}'></span>"
                "</div>"
                f"<span class='jr-stat-nums'><b>{r['below']:.0f}%</b> below, "
                f"<b>{r['above']:.0f}%</b> above <span class='jr-faint'>({r['lines']:,} lines)"
                "</span></span></div>")
        st.markdown("".join(rows)
                    + f"<p class='jr-key'><i style='background:{MISS_COLORS['below']}'></i>Below "
                    f"the range<i style='background:{MISS_COLORS['inside']}'></i>In range"
                    f"<i style='background:{MISS_COLORS['above']}'></i>Above the range</p>",
                    unsafe_allow_html=True)

        # 2. why
        cause = ji.causes(frame)
        st.markdown("<p class='jr-h2'>Why the misses happened</p>", unsafe_allow_html=True)
        if cause:
            m, pm = cause["minutes_share"], 100 - cause["minutes_share"]
            st.markdown(
                f"<p class='jr-p'>Of <b>{cause['misses']:,}</b> misses, <b>{cause['minutes']:,}</b> "
                f"({m}%) came when the player's minutes were at least {ji.MINUTES_GAP} away from "
                f"his usual. The other <b>{cause['per_minute']:,}</b> ({pm}%) came at normal "
                "minutes, so it was his output per minute that surprised.</p>"
                "<div class='jr-split'>"
                f"<span style='width:{m}%;background:#fbbf24'></span>"
                f"<span style='width:{pm}%;background:#a78bfa'></span></div>"
                "<p class='jr-key'><i style='background:#fbbf24'></i>Minutes"
                "<i style='background:#a78bfa'></i>Per-minute output</p>"
                f"<p class='jr-p'>Among the minutes misses, <b>{cause['more_minutes_above']}</b> "
                f"were above the range after extra minutes and <b>{cause['fewer_minutes_below']}"
                "</b> below it after fewer. "
                + ("When minutes explain most misses, the minutes guess is the part worth "
                   "improving." if m >= 50 else
                   "When most misses come at normal minutes, the per-minute rates are the part "
                   "worth a closer look.") + "</p>",
                unsafe_allow_html=True)
        else:
            st.markdown("<p class='jr-p'>No misses with minutes data yet.</p>", unsafe_allow_html=True)

        # 3. players
        table = ji.players(frame)
        st.markdown("<p class='jr-h2'>Players who keep missing the same way</p>",
                    unsafe_allow_html=True)
        st.markdown(f"<p class='jr-p'>Listed once a player has {ji.MIN_PLAYER_GAMES}+ games and "
                    "has landed on the same side of his range in at least half of them. One game "
                    "counts once, whichever stats missed.</p>", unsafe_allow_html=True)
        if table.empty:
            st.markdown("<p class='jr-p'>Nobody yet. This fills in as more nights are written.</p>",
                        unsafe_allow_html=True)
        else:
            st.dataframe(table, hide_index=True, use_container_width=True)

    if without:
        st.markdown("<p class='jr-h2'>Older nights</p>", unsafe_allow_html=True)
        st.markdown(f"<p class='jr-p'>{len(without)} night{'s' if len(without) != 1 else ''} "
                    "were written before every line was recorded, so they're not counted above. "
                    "Rebuilding takes a few seconds a night and keeps any lines Llama wrote.</p>",
                    unsafe_allow_html=True)
        if st.button(f"Rebuild {len(without)} older night{'s' if len(without) != 1 else ''}"):
            rebuild(without, st)
            st.rerun()


STAT_CHOICES = {label.capitalize(): code for code, (_, label) in pk.STATS.items()}
STAT_LABELS = {code: label for label, code in STAT_CHOICES.items()}


@st.cache_data(ttl=300, show_spinner=False)
def graded(slip_json):
    return pk.grade_slip(json.loads(slip_json))


@st.cache_data(ttl=300, show_spinner=False)
def checked(slip_json, open_json="[]"):
    slip = json.loads(slip_json)
    return sc.check_slip(slip, json.loads(open_json), slip.get("out") or None)


@st.cache_data(ttl=300, show_spinner=False)
def calibration(slips_json):
    return sc.calibration([pk.grade_slip(s) for s in json.loads(slips_json)])


@st.cache_data(ttl=3600, show_spinner=False)
def roster_names():
    return pk.known_players()


def pct(x):
    if x is None:
        return "?"
    return "under 1%" if x < 0.01 else f"{x * 100:.0f}%"


def money(x):
    return f"A${x:,.2f}".replace(".00", "")


def trust_note(cal):
    """One plain sentence on whether the check's chances can be leaned on,
    from how your own legs actually landed."""
    if not cal:
        return "There are no settled legs yet to test the check's chances against."
    for b in cal["buckets"]:
        if b["legs"] >= 10:
            landed = round(100 * b["landed"] / b["legs"])
            if abs(landed - b["expected"]) >= 15:
                way = "low" if landed > b["expected"] else "high"
                return (f"So far its chances have run {way} on your picks: legs it gave "
                        f"{b['label'].lower()} landed {landed}% of the time, against about "
                        f"{b['expected']}% expected. Don't lean on the percentages yet.")
    if cal["legs"] < 100:
        return (f"It's been tested on {cal['legs']} of your legs so far, too few to know whether "
                "its chances are right.")
    return "On your legs so far, its chances have matched how often legs actually landed."


def check_table(check):
    rows, flags = [], []
    for l in check["legs"]:
        c = l["check"]
        recent = (f"{c['recent']['over']} of last {c['recent']['of']}" if c.get("recent") else "")
        mins = c.get("minutes")
        mins_txt = (f"{mins['low']:.0f}–{mins['high']:.0f} min" if mins else "")
        if c.get("proj"):
            proj = (f"mid {dj._n(c['proj']['mid'])}, <b>{pct(c['chance'])}</b>"
                    + (" (rough)" if c["rough"] else ""))
        else:
            proj = "no projection"
        rows.append(f"<tr><td class='who'>{esc(l['player'])}</td>"
                    f"<td>{esc(pk.leg_label(l))}<span class='m-proj'>{recent}, {proj}</span></td>"
                    f"<td class='num proj'>{recent}</td><td class='num proj'>{mins_txt}</td>"
                    f"<td class='num'>{proj}</td></tr>")
        flags += [f"<li><b>{esc(l['player'])}</b>: {esc(f)}</li>" for f in c["flags"]]
    html_out = ("<table class='jr-legs'><tr><td class='jr-faint'>Player</td><td class='jr-faint'>Leg</td>"
                "<td class='num proj jr-faint'>Landed lately</td><td class='num proj jr-faint'>Minutes</td>"
                "<td class='num jr-faint'>Projection, chance</td></tr>" + "".join(rows) + "</table>")
    if flags:
        html_out += f"<ul class='jr-list jr-warn'>{''.join(dict.fromkeys(flags))}</ul>"
    return html_out


def render_check(check, cal):
    combined, implied, unchecked = check["combined"], check["implied"], check["unchecked"]
    n = len(check["legs"])
    parts = []
    if combined is not None:
        bound = "at most " if unchecked else "about "
        parts.append(f"If the legs were independent, all {n} land {bound}<b>{pct(combined)}</b> of "
                     "the time" + (f" ({unchecked} leg{'s' if unchecked != 1 else ''} couldn't be "
                                   "checked, so it's an upper limit)" if unchecked else "") + ".")
    if implied:
        parts.append(f"Sportsbet's price implies <b>{pct(implied)}</b>, before its margin.")
    if check.get("thin"):
        parts.append(f"<b>{check['thin']} of {n}</b> legs are projected from fewer than "
                     f"{sc.THIN_SEASON} games this season, so this combined chance is likely "
                     "overstated.")
    if check["same_game"]:
        parts.append("These legs are all in one game, so they aren't independent; the real chance "
                     "can be higher or lower.")
    st.markdown(f"<p class='jr-p'>{' '.join(parts)}</p>" + check_table(check)
                + f"<p class='jr-by' style='margin:.4rem 0 0'>{esc(trust_note(cal))}</p>",
                unsafe_allow_html=True)
    notes = sorted({note for l in check["legs"] for note in l["check"].get("notes", [])})
    if notes:
        st.caption("Engine adjustments: " + "; ".join(notes) + ".")


def read_upload(upload):
    """Recognise and parse one uploaded screenshot, once per file."""
    data = upload.getvalue()
    # The parser's own fingerprint is part of the key: a fix to picks.py
    # must retire the reads it already got wrong, and keying on the image
    # alone froze them for the session.
    key = ("ocr_" + pk.hashlib.sha1(data).hexdigest()[:12]
           + "_" + pk.parser_fingerprint())
    if key not in st.session_state:
        os.makedirs(os.path.join(PICKS_DIR, "_incoming"), exist_ok=True)
        path = os.path.join(PICKS_DIR, "_incoming", key + os.path.splitext(upload.name)[1].lower())
        with open(path, "wb") as f:
            f.write(data)
        try:
            lines = pk.read_image_lines(path)
            st.session_state[key] = pk.parse_sportsbet(lines, roster_names())
        except pk.TextRecognitionUnavailable as error:
            st.session_state[key] = {"error": str(error)}
        except Exception as error:
            st.session_state[key] = {"error": f"Couldn't read this screenshot ({error})."}
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
    return key, data


def team_players(teams):
    names = roster_names()
    return sorted(display for _pid, team, display in names.values() if team in teams)


def review_upload(upload, open_slips, cal):
    """One screenshot: fix what was read, see the check, add a stake.
    Returns (slip, image bytes, file name) to save, or None."""
    key, data = read_upload(upload)
    parsed = st.session_state[key]
    box = st.container(border=True)
    with box:
        left, right = st.columns([1, 2.8], gap="medium")
        with left:
            st.image(data, use_container_width=True)
        with right:
            if "error" in parsed:
                st.markdown(f"<div class='jr-note'>{esc(parsed['error'])}</div>", unsafe_allow_html=True)
                return None
            sid = pk.slip_id(parsed)
            if os.path.exists(os.path.join(PICKS_DIR, f"{sid}.json")):
                st.markdown("<p class='jr-p'>This slip is already saved.</p>", unsafe_allow_html=True)
                return None
            for problem in parsed.get("problems", []):
                st.markdown(f"<div class='jr-note'>{esc(problem)}</div>", unsafe_allow_html=True)
            st.markdown("<p class='jr-h'>1. Check what was read</p>", unsafe_allow_html=True)
            frame = pd.DataFrame([{
                "Player": l["player"], "Side": (l.get("side") or "over").capitalize(),
                "Stat": STAT_LABELS.get(l["stat"], "Points"),
                "Line": l["line"],
                "US date": datetime.date.fromisoformat(l["us_date"]) if l["us_date"] else None,
                "Fix": ", ".join(l.get("needs_review") or []),
            } for l in parsed["legs"]])
            if frame.empty:
                frame = pd.DataFrame(columns=["Player", "Side", "Stat", "Line", "US date", "Fix"])
            edited = st.data_editor(
                frame, key=f"legs_{key}", num_rows="dynamic", hide_index=True,
                use_container_width=True,
                column_config={
                    "Side": st.column_config.SelectboxColumn(options=["Over", "Under"], required=True),
                    "Stat": st.column_config.SelectboxColumn(options=list(STAT_CHOICES), required=True),
                    "Line": st.column_config.NumberColumn(
                        format="%.1f", help="16+ points is Over 15.5"),
                    "US date": st.column_config.DateColumn(format="ddd D MMM",
                                                           help="The US date the game is played"),
                    "Fix": st.column_config.TextColumn(disabled=True),
                })
            st.caption("Compare each row with the screenshot and fix anything misread. "
                       "Rows can be added or deleted.")
    names = roster_names()
    games = {pk._norm(l["player"]): l.get("game") for l in parsed["legs"]}
    legs = []
    for _, row in edited.iterrows():
        if not str(row.get("Player") or "").strip():
            continue
        found = pk.match_player(str(row["Player"]), names)
        player = found[2] if found else str(row["Player"]).strip()
        when = row.get("US date")
        legs.append({
            "player": player, "pid": found[0] if found else None, "team": found[1] if found else None,
            "stat": STAT_CHOICES.get(row.get("Stat"), "PTS"),
            "line": None if pd.isna(row.get("Line")) else float(row["Line"]),
            "side": "under" if str(row.get("Side")).lower() == "under" else "over",
            "us_date": when.isoformat() if hasattr(when, "isoformat") and not pd.isna(when) else None,
            "price": None, "game": games.get(pk._norm(player)),
        })
    with box:
        st.markdown("<p class='jr-h'>2. The evidence behind each leg</p>", unsafe_allow_html=True)
        teams = sorted({t for l in legs for t in (l.get("game") or "").split("@") if t}
                       | {l["team"] for l in legs if l.get("team")})
        a, b, d, c = st.columns([1, 1, 1.2, 2])
        price = a.number_input("Price on the slip", value=float(parsed.get("price") or 0.0),
                               min_value=0.0, step=0.01, format="%.2f", key=f"price_{key}")
        stake = b.number_input("Stake (A$, optional)", value=0.0, min_value=0.0, step=1.0,
                               format="%.2f", key=f"stake_{key}")
        picker = d.selectbox("Picked by", ["You", "Boxscore Whisperer"], key=f"by_{key}")
        out = c.multiselect("Out tonight (optional)", team_players(teams), key=f"out_{key}",
                             help="Players ruled out. The engine measures how each leg's player "
                                  "does in games without them.")
        slip = {**parsed, "price": price or None, "legs": legs, "stake": stake or None,
                "out": out or None, "picked_by": picker.lower()}
        render_check(sc.check_slip(slip, open_slips, out or None), cal)
    return slip, data, upload.name


def slip_card(slip, open_json, cal):
    g = graded(json.dumps(slip, sort_keys=True))
    check = checked(json.dumps(slip, sort_keys=True), open_json)
    by_leg = [l["check"] for l in check["legs"]]
    placed = datetime.datetime.fromisoformat(slip["placed_at"]) if slip.get("placed_at") else None
    n = len(g["legs"])
    title = f"{n}-leg multi" + (f" at {slip['price']:.2f}" if slip.get("price") else "")
    if g["result"] == "won":
        chip, cls = "Landed", "won"
    elif g["result"] == "lost":
        chip = ("Lost by 1 leg" if g["legs_lost"] == 1 else f"Lost by {g['legs_lost']} legs")
        cls = "lost"
    else:
        waiting = sum(l["result"] == "pending" for l in g["legs"])
        chip, cls = (f"Waiting on {waiting} leg{'s' if waiting != 1 else ''}" if waiting
                     else "Needs checking"), ""
    back = pk.returned(g)
    stake_txt = ""
    if slip.get("stake"):
        stake_txt = f"{money(slip['stake'])} staked" + (f", {money(back)} back" if back is not None else "")
    rows = []
    for l, c in zip(g["legs"], by_leg):
        result = l["result"]
        actual = ("" if l["actual"] is None else f"<b>{dj._n(l['actual'])}</b>"
                  + (f" in {dj._n(l['min'])} min" if l.get("min") else ""))
        if result == "void":
            actual = "didn't play"
        if c.get("proj"):
            proj = f"mid {dj._n(c['proj']['mid'])}, {pct(c['chance'])}" + (" (rough)" if c["rough"] else "")
        else:
            proj = "no projection" if result != "unreadable" else ""
        rows.append(f"<tr><td style='width:1.1rem'><span class='jr-dot {result}'></span></td>"
                    f"<td class='who'>{esc(l['player'])}</td>"
                    f"<td>{esc(pk.leg_label(l))}<span class='m-proj'>{proj}</span></td>"
                    f"<td class='num'>{actual}</td><td class='num proj'>{proj}</td></tr>")
    with st.container(border=True):
        st.markdown(
            "<div class='jr-slip-head'>"
            f"<span class='title'>{esc(title)}</span>"
            + (f"<span class='when'>placed {placed.strftime('%a %-d %b, %-I:%M %p')}</span>" if placed else "")
            + (f"<span class='when'>{stake_txt}</span>" if stake_txt else "")
            + ("<span class='when'>picked by Boxscore Whisperer</span>"
               if slip.get("picked_by") == "boxscore whisperer" else "")
            + f"<span class='jr-chip {cls}'>{chip}</span></div>"
            "<table class='jr-legs'>" + "".join(rows) + "</table>",
            unsafe_allow_html=True)
        if g["result"] == "pending":
            with st.expander("The evidence behind each leg", expanded=True):
                render_check(check, cal)
        cols = st.columns([1.3, 1.1, 1.1, 2], gap="small")
        if slip.get("image"):
            with cols[0].popover("Screenshot", use_container_width=True):
                st.image(os.path.join(PICKS_DIR, slip["image"]))
        with cols[1].popover("Stake", use_container_width=True):
            new_stake = st.number_input("Stake (A$)", value=float(slip.get("stake") or 0.0),
                                        min_value=0.0, step=1.0, key=f"st_{slip['id']}")
            new_paid = st.number_input("Paid out (A$)", value=float(slip.get("paid") or 0.0),
                                       min_value=0.0, step=1.0, key=f"pd_{slip['id']}",
                                       help="Leave at 0 unless it paid something other than "
                                            "stake x price: a boost or a cash-out.")
            if st.button("Save stake", key=f"sv_{slip['id']}", type="primary"):
                pk.update_slip(slip["id"], PICKS_DIR, stake=new_stake or None,
                               paid=new_paid or None)
                graded.clear()
                st.rerun()
        with cols[2].popover("Remove", use_container_width=True):
            st.markdown("Remove this slip from your picks? This can't be undone.")
            if st.button("Remove slip", key=f"rm_{slip['id']}", type="primary"):
                pk.remove_slip(slip["id"], PICKS_DIR)
                graded.clear()
                checked.clear()
                st.rerun()


def bar_row(name, won, total, color="#22c55e"):
    share = 100 * won / total if total else 0
    return ("<div class='jr-stat'>"
            f"<span class='jr-stat-name'>{esc(name)}</span><div class='jr-split'>"
            f"<span style='width:{share}%;background:{color}'></span>"
            f"<span style='width:{100 - share}%;background:#f87171'></span></div>"
            f"<span class='jr-stat-nums'><b>{won}</b> of <b>{total}</b> "
            f"<span class='jr-faint'>({share:.0f}%)</span></span></div>")


def render_picks():
    st.markdown("<h1 class='jr-date'>Your picks</h1>", unsafe_allow_html=True)
    slips = pk.load_slips(PICKS_DIR)
    gs = [graded(json.dumps(s, sort_keys=True)) for s in slips]
    rec = pk.record(gs) if gs else None
    open_slips = [s for s, g in zip(slips, gs) if g["result"] == "pending"]
    open_json = json.dumps(open_slips, sort_keys=True)
    cal = calibration(json.dumps(slips, sort_keys=True)) if slips else None

    if rec and rec["legs"]:
        leg_pct = round(100 * rec["legs_won"] / rec["legs"])
        line = (f"<b>{rec['slips']}</b> slip{'s' if rec['slips'] != 1 else ''}. Legs landed "
                f"<b>{rec['legs_won']}</b> of <b>{rec['legs']}</b> ({leg_pct}%). Slips landed "
                f"<b>{rec['slips_won']}</b> of <b>{rec['settled']}</b>")
        line += (f", and <b>{rec['lost_by_one']}</b> lost by a single leg." if rec["lost_by_one"]
                 else ".")
        st.markdown(f"<p class='jr-summary'>{line}</p>", unsafe_allow_html=True)
    else:
        st.markdown("<p class='jr-summary'>Add Sportsbet screenshots below, before or after a "
                    "game. Each leg gets the evidence from Boxscore Whisperer's engine, and is "
                    "graded once the game is played.</p>", unsafe_allow_html=True)

    st.markdown("<p class='jr-h2'>Check or add slips</p>", unsafe_allow_html=True)
    st.markdown("<p class='jr-p'>Drop in a slip before you place it to see the evidence behind "
                "each leg, or after, to add it to your record. Nothing is saved until you press "
                "Save. This shows evidence; it doesn't suggest bets.</p>", unsafe_allow_html=True)
    st.session_state.setdefault("upload_round", 0)
    uploads = st.file_uploader("Sportsbet screenshots", type=["png", "jpg", "jpeg", "heic"],
                               accept_multiple_files=True,
                               key=f"uploads_{st.session_state.upload_round}",
                               label_visibility="collapsed")
    ready = [r for r in (review_upload(u, open_slips, cal) for u in (uploads or [])) if r]
    if ready:
        if st.button(f"Save {len(ready)} slip{'s' if len(ready) != 1 else ''}", type="primary"):
            for slip, data, name in ready:
                pk.save_slip(slip, data, name, picks_dir=PICKS_DIR)
            st.session_state.upload_round += 1
            st.session_state.saved_picks = len(ready)
            graded.clear()
            checked.clear()
            calibration.clear()
            st.rerun()

    if rec and rec["legs"]:
        render_record(rec, gs, cal)
    if slips:
        st.markdown("<p class='jr-h2'>Slips</p>", unsafe_allow_html=True)
        st.markdown("<p class='jr-p'>Each leg shows what the player did and, last, the engine's "
                    "projection and chance from games before that night.</p>",
                    unsafe_allow_html=True)
        for slip in slips:
            slip_card(slip, open_json, cal)


def render_record(rec, gs, cal):
    leg_pct = round(100 * rec["legs_won"] / rec["legs"])
    st.markdown("<p class='jr-h2'>Legs and slips</p>", unsafe_allow_html=True)
    st.markdown(bar_row("Legs", rec["legs_won"], rec["legs"])
                + bar_row("Slips", rec["slips_won"], rec["settled"]), unsafe_allow_html=True)
    if rec["priced"]:
        st.markdown(
            f"<p class='jr-p'>A multi needs every leg, so slips land far less often than legs: "
            f"at {leg_pct}% a leg, a 4-leg slip lands about {round(100 * (leg_pct / 100) ** 4)}% "
            f"of the time. Sportsbet's own prices on these {rec['priced']} slips added up to about "
            f"<b>{rec['implied_wins']}</b> expected wins, before its margin.</p>",
            unsafe_allow_html=True)

    cash = pk.money(gs)
    st.markdown("<p class='jr-h2'>Money</p>", unsafe_allow_html=True)
    if cash and not cash["settled_staked"]:
        st.markdown(f"<p class='jr-p'><b>{money(cash['open_staked'])}</b> is on slips still open. "
                    "Nothing with a stake has settled yet.</p>", unsafe_allow_html=True)
    elif cash:
        net = cash["net"]
        st.markdown(
            f"<p class='jr-p'>Across <b>{cash['slips']}</b> slip{'s' if cash['slips'] != 1 else ''} "
            f"with a stake: <b>{money(cash['settled_staked'])}</b> staked on settled slips, "
            f"<b>{money(cash['returned'])}</b> back, so <b>{'+' if net >= 0 else '−'}"
            f"{money(abs(net))}</b>"
            + (f". Another <b>{money(cash['open_staked'])}</b> is on slips still open."
               if cash["open_staked"] else ".") + "</p>", unsafe_allow_html=True)
        weeks = pd.DataFrame([{
            "Week of": w["week"].strftime("%a %-d %b"), "Slips": w["slips"],
            "Staked": money(w["staked"]), "Back": money(w["returned"]),
            "Net": ("+" if w["net"] >= 0 else "−") + money(abs(w["net"])),
        } for w in cash["weeks"]])
        st.dataframe(weeks, hide_index=True, use_container_width=True)
    else:
        st.markdown("<p class='jr-p'>Add a stake to a slip (the Stake button on each one) to see "
                    "what's gone in and come back, week by week.</p>", unsafe_allow_html=True)

    if len(rec.get("by_picker", {})) > 1:
        st.markdown("<p class='jr-h2'>By who picked</p>", unsafe_allow_html=True)
        st.markdown("".join(bar_row("Boxscore W." if who == "boxscore whisperer" else "You", *wl)
                            for who, wl in sorted(rec["by_picker"].items())), unsafe_allow_html=True)
    st.markdown("<p class='jr-h2'>By leg type</p>", unsafe_allow_html=True)
    order = ["points", "rebounds", "assists", "threes", "steals", "blocks", "combos"]
    st.markdown("".join(bar_row(k.capitalize(), *rec["by_kind"][k])
                        for k in order if k in rec["by_kind"]), unsafe_allow_html=True)
    st.markdown("<p class='jr-key'><i style='background:#22c55e'></i>Landed"
                "<i style='background:#f87171'></i>Lost</p>", unsafe_allow_html=True)

    st.markdown("<p class='jr-h2'>Do the checks work?</p>", unsafe_allow_html=True)
    if cal:
        st.markdown("<p class='jr-p'>Every settled leg, grouped by the chance the check gave it "
                    "from games before that night, against how often those legs actually landed. "
                    "If the check is right, each row lands close to its expected share.</p>",
                    unsafe_allow_html=True)
        rows = []
        for b in cal["buckets"]:
            landed = 100 * b["landed"] / b["legs"]
            rows.append(
                "<div class='jr-stat jr-cal'>"
                f"<span class='jr-stat-name'>Given {esc(b['label'].lower())}</span>"
                "<div class='jr-split' style='position:relative'>"
                f"<span style='width:{landed}%;background:#60a5fa'></span>"
                f"<span style='width:{100 - landed}%;background:#f87171'></span>"
                f"<i class='jr-mark' style='left:{b['expected']}%'></i></div>"
                f"<span class='jr-stat-nums'><b>{b['landed']}</b> of <b>{b['legs']}</b> landed "
                f"<span class='jr-faint'>({landed:.0f}%, expected about {b['expected']}%)</span>"
                "</span></div>")
        st.markdown("".join(rows) + "<p class='jr-key'><i style='background:#60a5fa'></i>Landed"
                    "<i style='background:#f87171'></i>Lost<i style='background:#e7eaf0;width:2px'>"
                    "</i>Expected, if the check were right</p>", unsafe_allow_html=True)
        flag_lines = [f"Legs flagged <b>{f['label'].lower()}</b> landed <b>{f['landed']}</b> of "
                      f"<b>{f['legs']}</b>; the rest landed <b>{f['others_landed']}</b> of "
                      f"<b>{f['others']}</b>." for f in cal["flags"]]
        st.markdown(f"<p class='jr-p'>{' '.join(flag_lines)} {esc(trust_note(cal))}"
                    + (f" {cal['no_chance']} legs had no projection." if cal["no_chance"] else "")
                    + "</p>", unsafe_allow_html=True)

    if rec["shared_losses"]:
        items = "".join(f"<li>{esc(p)} {esc(STAT_LABELS.get(s, s).lower())} on "
                        f"{nice_date(d)} lost in <b>{n}</b> slips</li>"
                        for (p, s, d), n in rec["shared_losses"])
        st.markdown("<p class='jr-h2'>One result, several slips</p>"
                    "<p class='jr-p'>The same leg in more than one slip means one game decides "
                    f"all of them.</p><ul class='jr-list'>{items}</ul>", unsafe_allow_html=True)


# ------------------------------------------------------------------ writing

def pretty_log(message):
    message = message.strip()
    if "model line rejected" in message:
        label, _, rest = message.partition(": model line rejected (")
        why = rest.split(")", 1)[0]
        return f"**{label}**: Llama's line didn't pass ({why}), so it gets the plain summary."
    if message.endswith(" games -> ") or " -> " in message:
        return None
    if ": " in message:
        label, _, text = message.partition(": ")
        return f"**{label}**: {text}"
    return message


def write_night(day, use_llama, area):
    generate = None
    if use_llama:
        try:
            generate = whisper.make_generator(MODEL, dj.REASON_PROMPT, max_tokens=160)
        except SystemExit as stop:
            area.warning(f"{stop} Writing plain summaries instead.")
    with area.status(f"Writing {nice_date(day.isoformat(), long=True)}", expanded=True) as status:
        def log(message):
            line = pretty_log(message)
            if line:
                status.write(line)
        status.write("Fetching the games and box scores.")
        try:
            path = dj.journal_day(day, generate, out_dir=JOURNAL_DIR, force=True, log=log)
        except Exception as error:  # network, API shape -- say what happened
            status.update(label="Couldn't write this night", state="error")
            area.error(f"{type(error).__name__}: {error}")
            return False
        if path is None:
            status.update(label=f"No games on {nice_date(day.isoformat(), long=True)}", state="error")
            return False
        status.update(label=f"Wrote {nice_date(day.isoformat(), long=True)}", state="complete",
                      expanded=False)
    return True


# ------------------------------------------------------------------ page

nights = load_nights()
# A night just written is chosen here, before the radio exists: Streamlit
# refuses to change a widget's value after it has been drawn.
if "goto_night" in st.session_state:
    st.session_state.night = st.session_state.pop("goto_night")
if "night" not in st.session_state or st.session_state.night not in nights:
    st.session_state.night = next(iter(nights), None)

with st.sidebar:
    st.markdown("<p class='jr-brand'>Boxscore <span>Journal</span></p>", unsafe_allow_html=True)
    st.markdown("<p class='jr-h'>Write a night</p>", unsafe_allow_html=True)
    latest = dj.us_yesterday()
    day = st.date_input("US date", value=latest, max_value=latest + datetime.timedelta(days=1),
                        format="DD/MM/YYYY",
                        help="Games are filed under the US date. Read from Sydney in the "
                             "morning, last night is usually one day behind your calendar.")
    status = llama_status(MODEL)
    st.markdown(f"<p class='jr-status {'ready' if status == 'ready' else ''}'>"
                f"<span class='dot'></span>{STATUS_TEXT[status]}</p>", unsafe_allow_html=True)
    use_llama = st.toggle("Write the Why lines with Llama", value=status == "ready",
                          disabled=status != "ready")
    exists = day.isoformat() in nights
    clicked = st.button("Rewrite this night" if exists else "Write this night",
                        type="primary", use_container_width=True)
    if status != "ready":
        if st.button("Check Llama again", use_container_width=True):
            llama_status.clear()
            st.rerun()

    if nights:
        st.divider()
        st.markdown("<p class='jr-h'>Nights</p>", unsafe_allow_html=True)
        st.radio("Nights", list(nights), key="night", label_visibility="collapsed",
                 format_func=lambda iso: nice_date(iso) + (
                     "" if nights[iso].get("legacy")
                     else f"  :gray[{len(nights[iso].get('games', []))} games]"))

if clicked:
    if write_night(day, use_llama, st):
        st.session_state.goto_night = day.isoformat()
        st.session_state.just_wrote = day.isoformat()
        st.rerun()

saved = st.session_state.pop("saved_picks", None)
if saved:
    st.toast(f"Saved {saved} slip{'s' if saved != 1 else ''}")
wrote = st.session_state.pop("just_wrote", None)
if wrote:
    st.toast(f"Wrote {nice_date(wrote, long=True)}")

night_tab, misses_tab, picks_tab, season_tab = st.tabs(["Night", "Misses", "Picks", "Season"])
with night_tab:
    if not nights:
        st.markdown(
            "<div class='jr-empty'><b>No nights written yet.</b><br>Pick a US date in the "
            "sidebar and press <b>Write this night</b>. Each game appears here with the score, "
            "a Why line and the box score behind it.</div>", unsafe_allow_html=True)
    else:
        render_night(nights[st.session_state.night])
with misses_tab:
    render_misses(nights)
with picks_tab:
    render_picks()
with season_tab:
    render_season(nights)
