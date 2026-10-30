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
import whisper  # noqa: E402

JOURNAL_DIR = os.environ.get("BW_JOURNAL_DIR", "").strip() or dj.JOURNAL_DIR
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

wrote = st.session_state.pop("just_wrote", None)
if wrote:
    st.toast(f"Wrote {nice_date(wrote, long=True)}")

night_tab, misses_tab, season_tab = st.tabs(["Night", "Misses", "Season"])
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
with season_tab:
    render_season(nights)
