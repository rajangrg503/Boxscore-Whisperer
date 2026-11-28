"""Parlay builder -- a page of the Boxscore Journal (http://localhost:8501), and, through
pages/Parlay_builder.py, an unlisted page of the public app for karma's phone.

    python3 -m streamlit run tools/journal_app.py      # then "Parlay builder" in the sidebar

It puts tools/parlay_builder.py on screen: pick a night with saved Sportsbet lines, then either
check a slip leg by leg or let it build a ladder single, a longshot, a same-game multi, a big ticket
or a bonus-bet slip. Every slip shows the true chance, the fair price and what $1 comes back as on
average, from margins measured on our own saved nights.

It does not find winning legs (nothing we tested beat the price). It shows what each slip gives up.

Two sources. In the Journal it reads journal/lines live and can save a fresh snapshot. On the phone it
reads data/parlay_feed.json.gz, published from the Mac by tools/publish_builder.sh (karma's choice,
26 Nov 2026: the builder's lines go out with the public app; his bets in journal/picks never do).
"""
import datetime
import glob
import os
import sys

import streamlit as st

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
import parlay_builder as pb  # noqa: E402

# On the phone (the public app's /Parlay_builder page) there is no Mac and no journal/ folder: the page
# reads data/parlay_feed.json.gz, which `bash tools/publish_builder.sh` writes and pushes from the Mac.
FEED_MODE = os.environ.get("BW_BUILDER_FEED") == "1"
FEED = pb.load_feed() if FEED_MODE else {}
if not FEED_MODE:
    import line_snapshot as ls  # noqa: E402  (reads Sportsbet; only possible from the Mac)

st.set_page_config(page_title="Parlay builder", layout="wide")
st.markdown("""
<style>
.block-container { max-width: 980px; padding-top: 2.4rem; padding-bottom: 4rem; }
.pb-card { border: 1px solid rgba(128,128,128,.28); border-radius: 12px; padding: 14px 18px; margin: 10px 0 4px; }
.pb-big { font-size: 1.5rem; font-weight: 700; line-height: 1.2; }
.pb-muted { opacity: .7; font-size: .86rem; }
.pb-neg { color: #f87171; } .pb-pos { color: #4ade80; }
</style>
""", unsafe_allow_html=True)


def nice_date(iso):
    d = datetime.date.fromisoformat(iso)
    return f"{d:%a} {d.day} {d:%b} (US)"


def stamp_for(date):
    files = sorted(glob.glob(os.path.join(pb.ROOT, "journal", "lines", date, "*.json")))
    return os.path.basename(files[-1]) if files else ""


def source_cal():
    return FEED.get("cal", {}) if FEED_MODE else pb.load_cal()


@st.cache_data(show_spinner="Reading the saved lines and the game…")
def night_data(date, stamp, fitted):
    """(legs, read): every priced leg, each carrying our read's chance and read v price, and the Game read.
    The phone gets both ready-made in the feed."""
    if FEED_MODE:
        n = FEED["nights"][date]
        return [dict(x) for x in n["legs"]], n.get("read", {}), n.get("threes", [])
    import game_read as gr
    raw = pb.load_legs(date)
    night = gr.read_night(date, raw)
    gr.annotate_legs(raw, night)
    return raw, {g: {"shape": r["shape"], "picks": r["picks"], "steady": r.get("steady", []), "vthem": r.get("vthem", [])}
                 for g, r in night.items()}, gr.threes_table(night, raw)


def legs_for(date, stamp, fitted):  # noqa: D103
    return pb._annotate([dict(x) for x in night_data(date, stamp, fitted)[0]], source_cal())


@st.cache_data(show_spinner="Building…")
def built(date, stamp, fitted, mode, n_legs=3, game=None, target=500.0, bonus=False, bet_return=False, loose=False,
          reads_only=False, read_ok=True):
    legs, cal, same = legs_for(date, stamp, fitted), source_cal(), pb.load_same()
    if reads_only:
        legs = [x for x in legs if x.get("reads_for") and not x.get("reads_against")]
    if read_ok and not loose:
        legs = [x for x in legs if (x.get("read_edge") or -1) >= 0]   # our read must rate the leg at the price or better
    stake = "bonus" if bonus else "cash"
    if mode == "ladder":
        return pb.build_ladder(legs, cal, same, top=8)
    if mode == "longshot":
        return pb.build_longshot(legs, cal, same, n_legs, top=6, stake=stake, bet_return=bet_return, loose=loose)
    if mode == "sgm":
        return pb.build_sgm(legs, cal, same, game, n_legs, top=6, bet_return=bet_return, loose=loose)
    if mode == "big":
        return pb.build_big(legs, cal, same, target, top=3)
    return pb.build_bonus(legs, cal, same, top=6)


def leg_key(x):
    return (x["game"], x["player"], x["stat"], x["kind"], float(x["th"]))


def market_label(x):
    what = f"{x['th']:g}+" if x["kind"] == "alt" else f"{'over' if x['kind'] == 'over' else 'under'} {x['th']:g}"
    return f"{x['stat']} {what}  @{x['price']:.2f}"


def pct(v):
    return "--" if v is None else f"{100 * v:.0f}%"


def chance_text(p):
    return f"{100 * p:.1f}%" if p >= 0.02 else f"1 in {1 / max(p, 1e-9):,.0f}"


def reads_text(x):
    """Our reasons for (+) and against (!) the leg: head-to-head, who is out, karma's notes."""
    parts = [f"+ {r}" for r in x.get("reads_for") or []] + [f"! {r}" for r in x.get("reads_against") or []]
    return "  ·  ".join(parts) if parts else "--"


def short_line(legs, e):
    """Under the headline: how often a multi misses by exactly one leg (nothing extra for a single)."""
    if len(legs) == 1:
        return ""
    return f"Misses by exactly one leg {100 * e['one_short']:.0f}% of the time."


PLACED = None          # set once the night is chosen: bets already placed (Journal only)


COIN_FLIP = 0.03          # within 3 points of the price either way: too close to call (game_read.COIN_FLIP)


def backed_text(edge):
    """karma (27 Nov): 'i dont know how read which one is backed'. Say it plainly."""
    if edge is None:
        return "--"
    return "✅ backed" if edge >= COIN_FLIP else "⚖️ coin flip" if edge > -COIN_FLIP else "❌ not backed"


def edge_text(x):
    if x.get("read_edge") is None:
        return "--"
    # percentage points of CHANCE, not basketball points (karma read "+12 pts" on assists as 12 points)
    return f"{100 * x['read_edge']:+.0f}% chance" + (", too short" if x["read_edge"] <= -COIN_FLIP else "")


def render_slip(legs, e, key=None):
    """One slip: karma's rules first (anything that breaks one is said out loud), the headline numbers,
    then a row per leg."""
    for w in pb.rule_warnings(legs, PLACED, e["stake"]):
        st.warning(w)
    est = e["price_is_estimate"] and len({x["game"] for x in legs}) < len(legs)
    if e["stake"] == "bonus":
        value = f"$1 of bonus bet comes back as <b>{100 * e['return_per_dollar']:.0f}c</b>"
    else:
        cls = "pb-pos" if e["roi"] > 0 else "pb-neg"
        value = f"expected return <b class='{cls}'>{100 * e['roi']:+.0f}%</b>"
    st.markdown(
        f"<div class='pb-card'><span class='pb-big'>{e['price']:,.2f}</span>"
        f"<span class='pb-muted'>{' (estimated price)' if est else ''}</span>"
        f" &nbsp;·&nbsp; true chance <b>{chance_text(e['chance'])}</b>"
        f" &nbsp;·&nbsp; fair price <b>{e['fair_price']:,.2f}</b> &nbsp;·&nbsp; {value}"
        f" &nbsp;·&nbsp; {len(legs)} leg{'s' if len(legs) != 1 else ''}"
        f"<div class='pb-muted'>{short_line(legs, e)}"
        f"{' Same-game price is an estimate: type the real one in Check a slip.' if est else ''}</div></div>",
        unsafe_allow_html=True)
    st.dataframe(
        [{"Leg": pb.leg_label(x), "Game": x["game"], "Price": round(x["price"], 2), "Lands": pct(p),
          "Leg return": f"{100 * (p * x['price'] - 1):+.0f}%",
          "Season": f"{pct(x.get('hr_season'))} of {x.get('n_season') or 0}", "Last 8": pct(x.get("hr_l8")),
          "v this team": (f"{x.get('h2h_hits')} of {x.get('h2h_n')}" if x.get("h2h_n") else "--"),
          "Read v price": edge_text(x), "Reads": reads_text(x),
          "Min": round(x.get("min_l10") or 0)} for x, p in zip(legs, e["leg_probs"])],
        hide_index=True, width="stretch")
    if key and st.button("Send to Check a slip", key=key):
        st.session_state.pb_slip = [leg_key(x) for x in legs]
        st.toast("Loaded into Check a slip")


def swap_label(x):
    return (f"{pb.leg_label(x)}  @{x['price']:.2f}  ·  {x['game']}  ·  season {pct(x.get('hr_season'))}, "
            f"last 8 {pct(x.get('hr_l8'))}")


def render_ranked(ranked, prefix, all_legs=None, rules=None, stake="cash", bet_return=False):
    """Built slips, each with a 'Swap a leg' box: pick a leg, pick its replacement, and the slip is re-priced.
    rules = what a replacement must satisfy (the tab's price band and leg rules)."""
    if not ranked:
        st.info("Nothing passes the rules on this night.")
    by_key = {leg_key(x): x for x in (all_legs or [])}
    for i, (_, legs, e) in enumerate(ranked):
        slot = f"{prefix}{i}"
        original = [leg_key(x) for x in legs]
        state = st.session_state.get(f"pb_swap_{slot}")
        if not state or state["original"] != original or any(k not in by_key for k in state["now"]):
            state = {"original": original, "now": list(original)}       # a fresh build: forget old swaps
        slip = [by_key.get(k, x) for k, x in zip(state["now"], legs)] if by_key else legs
        swapped = state["now"] != original
        if swapped:
            e = pb.evaluate(slip, cal=source_cal(), same=pb.load_same(), stake=stake, bet_return=bet_return)
        render_slip(slip, e, key=slot)
        if not by_key or rules is None:
            continue
        with st.expander("Swap a leg" + ("  (changed from the built slip)" if swapped else "")):
            pick = st.selectbox("Leg to replace", [None] + list(range(len(slip))), key=f"pb_swapleg_{slot}",
                                format_func=lambda j, slip=slip: "Choose a leg…" if j is None else pb.leg_label(slip[j]))
            if pick is not None:
                options = pb.alternatives(all_legs, slip, pick, **rules)
                if not options:
                    st.caption("No other leg fits the rules here. Tick **Loosen the rules** for more.")
                else:
                    choice = st.selectbox("Replace it with", [None] + options, key=f"pb_swapto_{slot}_{pick}",
                                          format_func=lambda x: "Choose a replacement…" if x is None else swap_label(x))
                    if choice is not None:
                        state["now"][pick] = leg_key(choice)
                        st.session_state[f"pb_swap_{slot}"] = state
                        del st.session_state[f"pb_swapleg_{slot}"]
                        st.rerun()
            if swapped and st.button("Undo swaps", key=f"pb_undo_{slot}"):
                st.session_state.pop(f"pb_swap_{slot}", None)
                st.rerun()


# ------------------------------------------------------------------ page

st.title("Parlay builder")
st.caption("True chance, fair price and expected return for any slip, from Sportsbet margins measured on our own "
           "saved nights. It doesn't find winning legs. It shows what each slip gives up.")

if FEED_MODE:
    nights = sorted(FEED.get("nights", {}))
    if not nights:
        st.info("No lines have been published yet. On the Mac: `bash tools/publish_builder.sh`.")
        st.stop()
    date = st.selectbox("Night", nights, format_func=nice_date)
    cal, stamp = source_cal(), FEED.get("made_at", "")
    projected = FEED["nights"][date].get("has_slate", False)
    saved_text = f"lines published {stamp[11:16]} on {stamp[8:10]}/{stamp[5:7]} (Sydney time)"
else:
    nights = sorted(pb.line_nights(), reverse=True)
    left, right = st.columns([2, 1])
    with right:
        st.write("")
        if st.button("Save Sportsbet's lines now", width="stretch"):
            try:
                saved = ls.snapshot()
                for night in sorted(saved):                 # who is out / day-to-day, straight from ESPN
                    try:
                        pb.save_injuries(night)
                    except Exception as error:              # noqa: BLE001 -- shown, and the lines are still saved
                        st.warning(f"Lines saved, but the injury list for {night} couldn't be read: {str(error)[:120]}")
                st.cache_data.clear()
                st.toast("Saved lines for " + (", ".join(sorted(saved)) or "no upcoming games"))
                nights = sorted(pb.line_nights(), reverse=True)
            except ls.Unreachable as error:
                st.warning(f"Couldn't read Sportsbet's lines: {error}")
    if not nights:
        st.info("No saved lines yet. Press **Save Sportsbet's lines now**.")
        st.stop()
    with left:
        date = st.selectbox("Night", nights, format_func=nice_date)
    cal = source_cal()
    if not cal:
        st.warning("The margins haven't been measured on this Mac yet.")
        if st.button("Measure Sportsbet's margins (takes a couple of minutes the first time)"):
            with st.spinner("Grading every saved night…"):
                pb.calibrate(verbose=False)
            st.rerun()
        st.stop()
    stamp = stamp_for(date)
    projected = pb.has_slate(date)
    saved_text = f"lines saved {stamp[:2]}:{stamp[2:4]}"

fitted = (len(cal.get("fitted_on", [])), pb.VERSION)
all_legs = legs_for(date, stamp, fitted)
PLACED = None if FEED_MODE else pb.placed_today(date)
BET_PIDS = set() if FEED_MODE else pb.placed_players(date)
if not all_legs:
    st.info("No priced legs saved for this night.")
    st.stop()
with_read = sum(1 for x in all_legs if x["kind"] == "alt" and x.get("reads_for") and not x.get("reads_against"))
reads_only = st.checkbox(
    f"Only build from legs with a read behind them ({with_read} of them tonight)", key="pb_reads_only",
    help="A read = a head-to-head boost, a top-3 creator with a teammate out, an opponent missing two or more, "
         "or one of karma's own notes. Legs with a read against them (head-to-head veto, wrong stat for him) "
         "are left out. Reads order and filter the lists; they have not been shown to beat the price.")
legs = [x for x in all_legs if x.get("reads_for") and not x.get("reads_against")] if reads_only else all_legs
with_edge = sum(1 for x in all_legs if x["kind"] == "alt" and (x.get("read_edge") or -1) >= 0)
read_ok = st.checkbox(
    f"Only use legs our read rates at or above Sportsbet's price ({with_edge} of them tonight)", value=True,
    key="pb_read_ok",
    help="Each leg's 'Read v price' is the Game read's chance for it (his season at tonight's minutes, moved by "
         "the matchup, then corrected by how far the read was off in the 1-25 Nov backtest) minus the chance "
         "Sportsbet's price implies. With this on, the built slips and the swaps only use legs at zero or above, "
         "so the builder never suggests a leg our own read says is overpriced. 'Loosen the rules' ignores it.")
if read_ok:
    legs = [x for x in legs if (x.get("read_edge") or -1) >= 0]
games = sorted({x["game"] for x in all_legs})
st.caption(f"{len(all_legs):,} priced legs in {len(games)} games · {saved_text} · "
           f"margins measured on {fitted[0]} nights")
if not projected:
    st.warning(pb.NO_SLATE)
with st.expander("How to read the numbers"):
    st.markdown(
        "- **Price**: what Sportsbet pays for the slip.\n"
        "- **True chance**: how often slips at this price have actually landed on our saved nights. It is the "
        "price with Sportsbet's cut taken out, so it already reflects tonight's opponent and injuries.\n"
        "- **Fair price**: what the slip would pay with no cut. The gap between it and the price is the cut.\n"
        "- **Expected return**: what you'd average per $1 if you placed this kind of bet many times. "
        "**−7%** means $1 comes back as about 93c on average: you still win or lose each bet, but over "
        "100 bets of $3 you'd expect to be down about $21. Closer to zero is cheaper.\n"
        "- **Lands** and **Leg return**: the same two numbers for one leg.\n"
        "- **Season**, **Last 8**, **v this team**: how often he has cleared this line this season, in his "
        "last 8 games, and against tonight's opponent (the last three seasons). His own record, not a forecast.\n"
        "- **Reads**: our reasons for (+) and against (!) the leg that the price doesn't show you: his output v this "
        "team compared with his usual rate, a top-3 creator with a teammate out, an opponent missing two or more, "
        "and karma's own notes. Among legs that cost the same, more reads rank higher. Over the 23 saved nights "
        "reads like these have not been shown to beat the price, which is why every one is shown rather than trusted.")

read_tab, threes_tab, check_tab, ladder_tab, long_tab, sgm_tab, big_tab, bonus_tab, score_tab = st.tabs(
    ["Game read", "Threes v them", "Check a slip", "Ladder", "Longshot", "Same-game multi", "Big ticket", "Bonus bet",
     "Scoreboard"])


@st.cache_data(show_spinner="Reading the games…")
def game_reads(date, stamp, fitted):
    return night_data(date, stamp, fitted)[1]


STAT_WORD = {"PTS": "points", "REB": "rebounds", "AST": "assists", "FG3M": "threes", "PA": "points + assists",
             "PR": "points + rebounds", "RA": "rebounds + assists", "PRA": "points + rebounds + assists"}


def read_table(picks):
    st.dataframe(
        [{"Backed?": backed_text(k.get("edge")),
          "Pick": f"{k['player']} {k['rung']}+ {STAT_WORD[k['stat']]}" + ("  (day-to-day)" if k.get("dtd") else "")
                  + ("  (already bet on him)" if k.get("pid") in BET_PIDS else ""),
          "Read says": pct(k["share"]), "Read adds": f"{100 * k.get('lift', 0):+.0f}% chance", "Expects": k["expected"],
          "This season": k.get("season", "--"), "v them": k.get("v_opp", "--"),
          "Why": "  ·  ".join(k["why"]) or "his season, no adjustment",
          "Read v price": (f"{100 * k['edge']:+.0f}% chance" if k.get("edge") is not None else "--"),
          "Sportsbet": (f"{k['price']:.2f}" if k.get("price") else "--")} for k in picks],
        hide_index=True, width="stretch")


with read_tab:
    reads_by_game = game_reads(date, stamp, fitted)
    st.caption("What WE expect from the matchup and each player's own history, with no price involved: his rate "
               "this season at tonight's minutes, moved by how he has played v this opponent, what this opponent "
               "allows his type of player, and who is out. 'Read says' is the share of his games this season, "
               "re-run through that read, that reach the rung; 'This season' and 'v them' are what he actually did. "
               "A big gap between the two means the read is leaning on thin history. The Sportsbet price is only "
               "looked up afterwards.")
    if not reads_by_game:
        st.info("No read for this night yet" + (": publish again from the Mac." if FEED_MODE else "."))
    else:
        st.subheader("Most likely to land at a fair price")
        st.caption("All games: our read 60%+, with the read AND his season record both at or above what the price "
                   "implies; 24+ minute players, nobody out or day-to-day, one leg per player, prices 1.25-2.2. "
                   + ("Games already at your slip limit and players you've already bet tonight are left out. "
                      if not FEED_MODE else "On the phone your placed bets can't be seen, so check the slip limits. ")
                   + "Big-favourite and opponent rules aren't checked here.")
        likely = pb.likely_to_land(all_legs, PLACED, BET_PIDS)
        if likely:
            st.dataframe(
                [{"Leg": pb.leg_label(x), "Game": x["game"], "Price": round(x["price"], 2),
                  "Price implies": pct(1 / x["price"]), "Read": pct(x["read"]),
                  "This season": f"{pct(x.get('hr_season'))} of {x.get('n_season') or 0}", "Last 8": pct(x.get("hr_l8")),
                  "v them": (f"{x.get('h2h_hits')} of {x.get('h2h_n')}" if x.get("h2h_n") else "--")} for x in likely],
                hide_index=True, width="stretch")
        else:
            st.info("Nothing tonight is both likely to land and fairly priced.")
        read_game = st.selectbox("Game", sorted(reads_by_game), key="pb_read_game")
        if PLACED and PLACED[0].get(read_game, 0) >= pb.SLIPS_PER_GAME:
            st.warning(f"{read_game} already has {PLACED[0][read_game]} slips placed (limit {pb.SLIPS_PER_GAME}): "
                       "nothing more on this game tonight.")
        r = reads_by_game[read_game]
        if r["shape"]:
            with st.expander("How this game is decided (team v team)", expanded=True):
                st.text("\n".join(line.strip() for line in r["shape"]))
        hide_short = st.checkbox(
            "Hide picks the price is too short for", value=True, key="pb_read_hide_short",
            help="'Read v price' is the read's chance (mapped to what such reads actually landed at in the 1-25 Nov "
                 "backtest) minus the chance Sportsbet's price implies. ✅ backed: 3+ points above the price. "
                 "⚖️ coin flip: within 3 points either way, shown so you can decide. ❌ not backed: 3+ points "
                 "below, hidden when this is ticked. Picks with no price yet stay.")
        if hide_short:
            r = dict(r, picks=[k for k in r["picks"] if k.get("edge") is None or k["edge"] > -COIN_FLIP])
        st.subheader("Biggest advantages")
        st.caption("Upside picks: the highest rung he reaches in 25-60% of his games as read. Ordered by 'Read adds': "
                   "how much the read raises his chance at that number over his plain season. Only stats he "
                   "produces in volume (10+ points, 4+ rebounds, 3+ assists, 1.5+ threes).")
        read_table([k for k in r["picks"] if k["kind"] == "upside"][:12])
        st.subheader("Floors")
        st.caption("Safe picks: the highest rung he clears in 80%+ of his games as read.")
        read_table([k for k in r["picks"] if k["kind"] == "safe"][:14])
        st.subheader("Steady")
        st.caption("karma, 27 Nov: 'coward has been so consistent. why isnt he showing up'. Each player's best "
                   "rung he cleared in 7+ of his last 10, priced, whatever the matchup says. ★ = on karma's own "
                   "steady list. ❌ ones are left out unless they're karma's.")
        steady = r.get("steady") or []
        if steady:
            st.dataframe(
                [{"Backed?": backed_text(k["edge"]),
                  "Pick": ("★ " if k["karma"] else "") + f"{k['player']} {k['rung']}+ {STAT_WORD[k['stat']]}"
                          + ("  (day-to-day)" if k.get("dtd") else ""),
                  "Last 10": k["last10"], "Read says": pct(k["share"]), "Sportsbet": f"{k['price']:.2f}",
                  "Read v price": f"{100 * k['edge']:+.0f}% chance"} for k in steady[:12]],
                hide_index=True, width="stretch")
        else:
            st.info("No steady priced rungs for this game yet.")
        st.subheader("80%+ v them")
        st.caption("karma, 28 Nov: 'a list of 80% or above vs this team. or 4 of 5, 6 of 8'. His highest number "
                   "cleared in 80%+ of his games v tonight's opponent (last three seasons, 5+ games), every stat "
                   "and combo. A record to look at: check the Backed? column and his last 10 before using one.")
        vthem = r.get("vthem") or []
        if vthem:
            st.dataframe(
                [{"Backed?": backed_text(k["edge"]) if k.get("edge") is not None else "no price",
                  "Pick": f"{k['player']} {k['rung']}+ {STAT_WORD[k['stat']]}" + ("  (day-to-day)" if k.get("dtd") else ""),
                  "v them": k["v"], "His games v them": ", ".join(f"{v:g}" for v in k["v_vals"]),
                  "Last 10 at it": k["last10"], "Read says": pct(k["read"]),
                  "Sportsbet": f"{k['price']:.2f}" if k.get("price") else "--"} for k in vthem[:20]],
                hide_index=True, width="stretch")
        else:
            st.info("Nobody has 5+ games v this opponent at 80%+ yet.")
        priced = [k for k in r["picks"] if k.get("price")]
        chosen = st.multiselect("Send picks to Check a slip", priced, key=f"pb_read_send_{read_game}",
                                format_func=lambda k: f"{k['player']} {k['rung']}+ {STAT_WORD[k['stat']]} @{k['price']:.2f}")
        if chosen and st.button("Send", key="pb_read_send_btn"):
            wanted = {(k["pid"], k["stat"], float(k["rung"])) for k in chosen}
            st.session_state.pb_slip = [leg_key(x) for x in all_legs
                                        if x["kind"] == "alt" and (x["pid"], x["stat"], float(x["th"])) in wanted]
            st.toast("Loaded into Check a slip")


with threes_tab:
    threes = night_data(date, stamp, fitted)[2]
    st.caption("'Most likely' is the most threes he made in 70%+ of his games against tonight's opponent, with how "
               "often he got there v them and in his last 10, its Sportsbet price and whether our read backs it. "
               "Most likely first. Below that: every player with 2+ games against tonight's opponent (last three "
               "seasons) who averages a three "
               "or more as read: his threes made in those games (newest first), his average v them and this "
               "season, and what the read expects tonight. 'Best leg' is the Sportsbet threes leg our read rates "
               "best against its price. Watch 'for another team': history from a different team is a different role. "
               "Threes are straights only.")
    t_games = ["All games"] + sorted({r["game"] for r in threes})
    t_game = st.selectbox("Game", t_games, key="pb_threes_game")
    shown = [r for r in threes if t_game == "All games" or r["game"] == t_game]
    if not shown:
        st.info("No player has enough history against tonight's opponent.")
    else:
        def leg_text(leg):
            if not leg:
                return "--"
            what = f"{leg['th']:g}+" if leg["kind"] == "alt" else f"over {leg['th']:g}"
            return f"{what} @{leg['price']:.2f}"
        st.dataframe(
            [{"Player": r["player"] + ("  (day-to-day)" if r.get("dtd") else "")
                        + ("  (already bet on him)" if r["pid"] in BET_PIDS else ""),
              "Most likely": (f"{r['likely']}+ threes" if r.get("likely") else "--"),
              "v them at it": r.get("likely_v", "--"),
              "Last 10 at it": r.get("likely_last10", "--"),
              "Price": (f"{r['likely_price']:.2f}" if r.get("likely_price") else "--"),
              "Backed?": backed_text(None) if not r.get("likely_backed") else
                         {"backed": "✅ backed", "coin flip": "⚖️ coin flip", "not backed": "❌ not backed"}[r["likely_backed"]],
              "Game": r["game"],
              "v them (newest first)": " · ".join(str(m) for m in r["v_makes"]),
              "v them avg": f"{r['v_avg']:.1f} in {r['v_games']}",
              "for another team": r["v_other_team"] or "",
              "This season": f"{r['season_avg']:.1f} ({r['season_att']} att)",
              "Read expects": r["expects"],
              "Best leg": leg_text(r["leg"]),
              "Read": pct(r["leg"]["read"]) if r["leg"] else "--",
              "Season at it": pct(r["leg"]["hr_season"]) if r["leg"] else "--",
              "v them at it": (f"{r['leg']['h2h_hits']} of {r['leg']['h2h_n']}" if r["leg"] and r["leg"].get("h2h_n") else "--"),
              "Read v price": (f"{100 * r['leg']['read_edge']:+.0f}% chance" if r["leg"] else "--")} for r in shown],
            hide_index=True, width="stretch")

with check_tab:
    by_key = {leg_key(x): x for x in all_legs}
    slip_keys = [k for k in st.session_state.setdefault("pb_slip", []) if k in by_key]
    c1, c2, c3, c4 = st.columns([1.1, 1.6, 1.6, 0.7])
    game = c1.selectbox("Game", games, key="pb_game")
    players = sorted({x["player"] for x in all_legs if x["game"] == game})
    player = c2.selectbox("Player", players, key="pb_player")
    markets = sorted([x for x in all_legs if x["game"] == game and x["player"] == player],
                     key=lambda x: (x["stat"], x["kind"] != "alt", x["th"]))
    pick = c3.selectbox("Market", markets, format_func=market_label, key="pb_market")
    c4.write("")
    c4.write("")
    if c4.button("Add leg", width="stretch") and pick is not None and leg_key(pick) not in slip_keys:
        slip_keys.append(leg_key(pick))
    st.session_state.pb_slip = slip_keys
    if not slip_keys:
        st.info("Add legs above, or press **Send to Check a slip** on any built slip.")
    else:
        slip = [by_key[k] for k in slip_keys]
        o1, o2, o3, o4 = st.columns([1.4, 1, 1, 0.8])
        price = o1.number_input("Sportsbet's price for the whole slip (0 = work it out)", min_value=0.0, value=0.0, step=0.05)
        bonus = o2.checkbox("Bonus bet", help="The stake isn't returned.")
        bet_return = o3.checkbox("Bet Return", help="Stake back as a bonus bet when exactly one leg misses (3+ legs).")
        if o4.button("Clear", width="stretch"):
            st.session_state.pb_slip = []
            st.rerun()
        e = pb.evaluate(slip, price=price or None, cal=cal, same=pb.load_same(),
                        stake="bonus" if bonus else "cash", bet_return=bet_return)
        if FEED_MODE:
            st.caption("On the phone the slips you've already placed can't be seen, so the per-game and "
                       "multis-per-day limits aren't checked here.")
        render_slip(slip, e)
        drop = st.selectbox("Remove a leg", [None] + slip, format_func=lambda x: "—" if x is None else pb.leg_label(x))
        if drop is not None:
            st.session_state.pb_slip = [k for k in slip_keys if k != leg_key(drop)]
            st.rerun()

with ladder_tab:
    st.caption("Singles near 2.00 (and two-game pairs when they beat a single, which is rare). Every single in this "
               "band costs about the same, so they're ordered by the player's own record at the line.")
    render_ranked(built(date, stamp, fitted, "ladder", reads_only=reads_only, read_ok=read_ok), "lad", legs, dict(purpose="single", lo=1.9, hi=2.15))

PLAY_HELP = ("For playing around, not for betting: any stat (combos and threes too), any player averaging 20+ "
             "minutes, and no limit on legs from one team.")


def shown_legs(ranked, asked):
    if ranked and len(ranked[0][1]) < asked:
        st.caption(f"Only {len(ranked[0][1])} legs pass the rules here. Tick **Loosen the rules** for more.")


with long_tab:
    a, b, c, d = st.columns([1.6, 1, 1, 1.4])
    n_long = a.slider("Legs", 2, 20, min(3, max(2, len(games))), key="pb_long_n")
    long_bonus = b.checkbox("Bonus bet", key="pb_long_bonus")
    long_return = c.checkbox("Bet Return", key="pb_long_return")
    long_loose = d.checkbox("Loosen the rules", key="pb_long_loose", help=PLAY_HELP)
    st.caption("Rungs at 2.0–3.6 spread evenly over the night's games (the price band that gives up least). "
               "With more legs than games, each game takes two or more.")
    ranked = built(date, stamp, fitted, "longshot", n_legs=n_long, bonus=long_bonus, bet_return=long_return,
                   loose=long_loose, reads_only=reads_only, read_ok=read_ok)
    shown_legs(ranked, n_long)
    render_ranked(ranked, "lon", legs, dict(purpose="multi", lo=2.0, hi=3.6, loose=long_loose),
                  stake="bonus" if long_bonus else "cash", bet_return=long_return)

with sgm_tab:
    a, b, c, d = st.columns([1.2, 1.4, 1, 1.4])
    sgm_game = a.selectbox("Game", games, key="pb_sgm_game")
    n_sgm = b.slider("Legs", 2, 20, 4, key="pb_sgm_n")
    sgm_return = c.checkbox("Bet Return", key="pb_sgm_return")
    sgm_loose = d.checkbox("Loosen the rules", key="pb_sgm_loose", help=PLAY_HELP)
    st.caption("One leg per player, single stats, at most half the legs from one team (teammates' scoring legs "
               "work against each other). The price shown is an estimate, 0.96 × the product of the legs.")
    ranked = built(date, stamp, fitted, "sgm", n_legs=n_sgm, game=sgm_game, bet_return=sgm_return, loose=sgm_loose,
                   reads_only=reads_only, read_ok=read_ok)
    shown_legs(ranked, n_sgm)
    render_ranked(ranked, "sgm", legs, dict(purpose="sgm", lo=1.1 if sgm_loose else 1.25, hi=6.0 if sgm_loose else 3.6,
                                           loose=sgm_loose, same_game=True), bet_return=sgm_return)

with big_tab:
    target = st.select_slider("Target price", [50, 100, 250, 500, 1000, 2500, 5000, 10000, 50000, 100000, 1000000],
                              value=500, format_func=lambda v: f"{v:,}")
    st.caption("The cheapest route to a big price: the legs that give up least per unit of odds, one per team. "
               "It is still a lottery ticket.")
    render_ranked(built(date, stamp, fitted, "big", target=float(target), reads_only=reads_only, read_ok=read_ok), "big", legs, dict(purpose="multi", lo=1.8, hi=3.6))

with bonus_tab:
    st.caption("The slips that turn $1 of bonus bet into the most cash on average.")
    render_ranked(built(date, stamp, fitted, "bonus", reads_only=reads_only, read_ok=read_ok), "bon", legs, dict(purpose="multi", lo=2.0, hi=6.0), stake="bonus")


@st.cache_data(show_spinner="Grading the saved nights…")
def local_scoreboard(n_nights, version):
    return {"reads": pb.scoreboard(), "slips": pb.log_results()}


def score_table(rows, unit):
    if not rows:
        st.info("Nothing graded yet.")
        return
    st.dataframe(
        [{"": r["read"], unit: r["n"], "Nights": r["nights"], "Predicted to land": pct(r["predicted"]),
          "Landed": pct(r["actual"]), "Return per $1": f"{100 * r['roi']:+.0f}%",
          "Likely range": f"{100 * r['lo']:+.0f}% to {100 * r['hi']:+.0f}%"} for r in rows],
        hide_index=True, width="stretch")


with score_tab:
    board = FEED.get("scoreboard", {}) if FEED_MODE else local_scoreboard(len(pb.line_nights()), pb.VERSION)
    st.caption("How legs carrying each read have actually done on every saved night: one X+ rung per player and "
               "stat (nearest 2.00), priced 1.3-4.0. 'All legs' is the cost of betting blind. A read earns its "
               "place when its row is clearly better than that, with a likely range that isn't just noise.")
    score_table(board.get("reads", []), "Legs")
    st.caption("The builder's own suggestions, written down before the games by the publish script and graded "
               "once the night is in.")
    score_table(board.get("slips", []), "Slips")
