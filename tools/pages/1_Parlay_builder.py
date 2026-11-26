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


@st.cache_data(show_spinner="Reading the saved lines…")
def legs_for(date, stamp, fitted):
    raw = [dict(x) for x in FEED["nights"][date]["legs"]] if FEED_MODE else pb.load_legs(date)
    return pb._annotate(raw, source_cal())


@st.cache_data(show_spinner="Building…")
def built(date, stamp, fitted, mode, n_legs=3, game=None, target=500.0, bonus=False, bet_return=False, loose=False,
          reads_only=False):
    legs, cal, same = legs_for(date, stamp, fitted), source_cal(), pb.load_same()
    if reads_only:
        legs = [x for x in legs if x.get("reads_for") and not x.get("reads_against")]
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


def render_slip(legs, e, key=None):
    """One slip: the headline numbers, then a row per leg."""
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
          "Reads": reads_text(x),
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

check_tab, ladder_tab, long_tab, sgm_tab, big_tab, bonus_tab = st.tabs(
    ["Check a slip", "Ladder", "Longshot", "Same-game multi", "Big ticket", "Bonus bet"])

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
        render_slip(slip, e)
        drop = st.selectbox("Remove a leg", [None] + slip, format_func=lambda x: "—" if x is None else pb.leg_label(x))
        if drop is not None:
            st.session_state.pb_slip = [k for k in slip_keys if k != leg_key(drop)]
            st.rerun()

with ladder_tab:
    st.caption("Singles near 2.00 (and two-game pairs when they beat a single, which is rare). Every single in this "
               "band costs about the same, so they're ordered by the player's own record at the line.")
    render_ranked(built(date, stamp, fitted, "ladder", reads_only=reads_only), "lad", legs, dict(purpose="single", lo=1.9, hi=2.15))

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
                   loose=long_loose, reads_only=reads_only)
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
                   reads_only=reads_only)
    shown_legs(ranked, n_sgm)
    render_ranked(ranked, "sgm", legs, dict(purpose="sgm", lo=1.1 if sgm_loose else 1.25, hi=6.0 if sgm_loose else 3.6,
                                           loose=sgm_loose, same_game=True), bet_return=sgm_return)

with big_tab:
    target = st.select_slider("Target price", [50, 100, 250, 500, 1000, 2500, 5000, 10000, 50000, 100000, 1000000],
                              value=500, format_func=lambda v: f"{v:,}")
    st.caption("The cheapest route to a big price: the legs that give up least per unit of odds, one per team. "
               "It is still a lottery ticket.")
    render_ranked(built(date, stamp, fitted, "big", target=float(target), reads_only=reads_only), "big", legs, dict(purpose="multi", lo=1.8, hi=3.6))

with bonus_tab:
    st.caption("The slips that turn $1 of bonus bet into the most cash on average.")
    render_ranked(built(date, stamp, fitted, "bonus", reads_only=reads_only), "bon", legs, dict(purpose="multi", lo=2.0, hi=6.0), stake="bonus")
