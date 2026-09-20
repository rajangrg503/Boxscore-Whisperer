"""The season's record so far -- and what it is not yet allowed to claim.

WHAT THIS IS FOR
Every accuracy figure on the site today comes from a BACKTEST: a replay
of seasons already played, scored against results that existed before
the model did. That work is careful and it is still, structurally, a
claim about the past made by someone who could see it.

The forward test is the other kind. tools/score_forward_test.py writes
one file per night into results/ -- what we projected before tip-off,
what happened, whether the range held, which way we leaned, and what
the leg paid. Nobody else in this market publishes one. This module
turns that pile of nights into the figures a reader sees, and it is
mostly a set of refusals.

THE THREE CLAIMS, IN DESCENDING ORDER OF STRENGTH
  COVERAGE       did the 80% range contain the result? Reproducible
                 from this repository alone, and it is what the app
                 says on its face.
  AGAINST THE    when we disagreed with the market, were we right?
  LINE           Needs an odds feed, so it is audited by digest.
  THE MONEY      would following us have paid? Reported only as the
                 night-level aggregates the scorer was willing to
                 publish -- see engine/pricing.py's licence note.

WHY THE GATES EXIST
The first fortnight of a season is the single most dangerous moment for
this page. There will be four nights of data, an audience seeing the
site for the first time, and a number that could read 61% in large type
purely because 40 legs landed well. Publishing that would destroy the
one thing this product has -- and it would do it by publishing a TRUE
number, computed correctly, that simply does not mean what a reader
would take it to mean.

So nothing -- no rate, no interval, no units figure -- is shown until
the record clears MIN_NIGHTS_TO_STATE, and then MIN_LEGS_TO_STATE on
top. Below that the page says what it has, the nights and the legs and
the fact that it is too early, and nothing more. Above it, every rate
carries a Wilson interval, because a percentage without one invites
being read as more precise than it is, and because an interval that
spans the bar says "we cannot tell yet" in a way a bare 54% never
does.

THE INTERVAL IS THE HEADLINE, NOT THE DECORATION
For the against-the-line and money figures there is a specific bar to
clear, and it is not 50%. It is the break-even the prices actually
demanded -- measured at 53.3% on the one real capture, not the 52.38%
that a -110 assumption gives. A record whose interval includes the
break-even has not shown an edge, however good its midpoint looks, and
verdict_for() says so in those words.

Nothing here knows about Streamlit. It returns plain numbers and plain
sentences for the app to draw.
"""

import json
import math
import os

# THE GATE THAT MATTERS, AND THE ONE I NEARLY SHIPPED WITHOUT
#
# The first version of this module gated on counts alone, and a render
# of four simulated opening nights put "+25.60 units, +11.9%" and an
# 82.7% coverage figure on the page. Both were arithmetically correct.
# Both were noise. Four nights cleared a 1,000-claim gate because a
# single slate produces about three hundred projected stat lines.
#
# The counts were never the right denominator. A night's player-games
# are not independent draws: one blowout, one early foul-out epidemic,
# one slate where every favourite covered, and three hundred claims
# move together. A Wilson interval over claims treats them as three
# hundred observations and reports a false precision. The effective
# sample size of this page is NIGHTS.
#
# So nothing is published -- no rate, no interval, and no units figure
# -- until the record spans at least this many scored nights. Roughly a
# month of a season. Everything below is a secondary floor for the case
# where the nights are unusually thin.
MIN_NIGHTS_TO_STATE = 20

# A NIGHT WITH NOTHING IN IT IS NOT A NIGHT
#
# The NBA plays preseason from 3 to 16 October 2026, and
# engine/game_log.py asks the API for "Regular Season" and "Playoffs"
# only -- there is not one preseason row in the cache. So a preseason
# night scores every player as VOID, writes that record, and publishes
# it. Ten of those in a fortnight, and this page would believe it was
# halfway to its twenty-night gate on nothing at all.
#
# The same shape arrives without preseason: a morning when the refresh
# failed leaves the cache with no box scores, and every player voids
# for that reason instead. Both are "we have no evidence from this
# night", and neither should advance a counter whose whole job is to
# say how much evidence there is.
#
# So the gate counts nights that SETTLED SOMETHING. Structural rather
# than a calendar check, because it catches the failed-refresh case
# too, and because a date in a constant rots every October.
def has_evidence(night):
    """Did this night settle anything at all?"""
    totals = (night or {}).get("totals") or {}
    return bool(totals.get("scored") or totals.get("legs"))

# Below this many settled legs, no rate is published either. Chosen so
# that a Wilson interval on a coin-flip has a half-width under about
# five points -- the width at which a 55% and a 50% record stop being
# the same picture. At 400 legs that half-width is 4.9 points.
MIN_LEGS_TO_STATE = 400

# Coverage is a different question with a different denominator: every
# projected stat for every player who played, so it accumulates about
# ten times faster than legs do.
MIN_CLAIMS_TO_STATE = 1000

# The break-even a typical prop in this feed demands, measured rather
# than assumed -- engine/pricing.py has the derivation. Used only to
# say whether a record has cleared its bar, never to settle a leg.
TYPICAL_BREAK_EVEN = 0.533

# The nominal the intervals are built at.
Z = 1.96


def wilson(successes, total, z=Z):
    """A confidence interval that behaves at the edges.

    Same function as availability_sweep.py's, for the same reason: the
    normal approximation gives negative lower bounds on thin buckets,
    and a rate published without an interval invites being read as more
    precise than it is.
    """
    if not total:
        return None
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = (z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
              / denominator)
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def read_nights(result_dir):
    """Every scored night, oldest first. Unreadable files are counted,
    not skipped silently -- a results/ directory quietly losing a third
    of its nights would move every figure below."""
    nights, unreadable = [], 0
    if not os.path.isdir(result_dir):
        return nights, unreadable
    for name in sorted(os.listdir(result_dir)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(result_dir, name)) as handle:
                nights.append(json.load(handle))
        except (OSError, ValueError):
            unreadable += 1
    return nights, unreadable


def _rate(successes, total, minimum, nights=None):
    """A rate, or None when there is not enough to say one.

    None is the important return value here. It is not "0%" and it is
    not "we are still calculating"; it is the page declining to put a
    number in front of somebody.

    nights is checked first and separately, because it is the gate that
    actually binds -- see MIN_NIGHTS_TO_STATE. Passing None skips it,
    which is only for callers testing the count floor on its own.
    """
    if nights is not None and nights < MIN_NIGHTS_TO_STATE:
        return None
    if not total or total < minimum:
        return None
    interval = wilson(successes, total)
    return {"successes": successes, "total": total,
            "rate": successes / total,
            "lo": interval[0], "hi": interval[1]}


def summarise(result_dir):
    """Everything the page may say, and the counts behind what it may not.

    The counts are always returned even when the rates are withheld, so
    the thin-sample state can say "6 nights, 231 legs, too early" rather
    than an empty panel that looks broken.
    """
    all_nights, unreadable = read_nights(result_dir)
    nights = [n for n in all_nights if has_evidence(n)]
    empty = len(all_nights) - len(nights)
    totals = {"scored": 0, "covered": 0, "void_players": 0,
              "legs": 0, "legs_correct": 0, "legs_push": 0,
              "strong_legs": 0, "strong_correct": 0}
    purse = {"all": {"legs": 0, "staked": 0.0, "profit": 0.0,
                     "bar_weighted": 0.0, "bar_legs": 0,
                     "withheld_legs": 0, "withheld_nights": 0},
             "strong": {"legs": 0, "staked": 0.0, "profit": 0.0,
                        "bar_weighted": 0.0, "bar_legs": 0,
                        "withheld_legs": 0, "withheld_nights": 0}}
    dates = []

    for night in nights:
        for key in totals:
            totals[key] += (night.get("totals") or {}).get(key, 0) or 0
        if night.get("game_date"):
            dates.append(night["game_date"])
        for key, pot in purse.items():
            tallied = (night.get("money") or {}).get(key) or {}
            if tallied.get("withheld"):
                pot["withheld_legs"] += tallied.get("legs", 0)
                pot["withheld_nights"] += 1
            elif tallied.get("legs"):
                pot["legs"] += tallied["legs"]
                pot["staked"] += tallied.get("staked", 0.0)
                pot["profit"] += tallied.get("profit", 0.0)
                if tallied.get("break_even") is not None:
                    pot["bar_weighted"] += tallied["break_even"] * tallied["legs"]
                    pot["bar_legs"] += tallied["legs"]

    for pot in purse.values():
        pot["roi"] = (100.0 * pot["profit"] / pot["staked"]) if pot["staked"] else None
        pot["break_even"] = (pot["bar_weighted"] / pot["bar_legs"]
                             if pot["bar_legs"] else None)
        # The units figure gets the same gate as every rate, and needs
        # it more. A four-night ROI is the single most screenshotable
        # number this page can produce and the least meaningful one.
        pot["publishable"] = (len(nights) >= MIN_NIGHTS_TO_STATE
                              and pot["legs"] >= MIN_LEGS_TO_STATE)

    return {
        "nights": len(nights),
        # Recorded and reported, never counted. Preseason and
        # failed-refresh mornings both land here.
        "empty_nights": empty,
        "unreadable": unreadable,
        "first_date": min(dates) if dates else None,
        "last_date": max(dates) if dates else None,
        "totals": totals,
        "coverage": _rate(totals["covered"], totals["scored"],
                          MIN_CLAIMS_TO_STATE, len(nights)),
        "against_line": _rate(totals["legs_correct"], totals["legs"],
                              MIN_LEGS_TO_STATE, len(nights)),
        "strong": _rate(totals["strong_correct"], totals["strong_legs"],
                        MIN_LEGS_TO_STATE, len(nights)),
        "money": purse,
    }


def verdict_for(rate, bar):
    """Has this record cleared the bar, or is it still undecided?

    Three answers, and the middle one is the honest default for most of
    a season. An interval straddling the bar is not a weak yes; it is a
    "we cannot tell yet", and saying so is the whole point of showing
    the interval at all.
    """
    if not rate:
        return None
    if rate["lo"] > bar:
        return "above"
    if rate["hi"] < bar:
        return "below"
    return "undecided"


def headline(summary):
    """One sentence for the top of the panel.

    Deliberately boring while the sample is thin. The temptation on
    4 November is to lead with a number; this leads with the denominator
    until the denominator earns it.
    """
    nights = summary["nights"]
    if not nights:
        return ("Nothing scored yet. The first night of the season settles "
                "the morning after it is played.")

    totals = summary["totals"]
    span = ""
    if summary["first_date"] and summary["last_date"]:
        span = (f" ({summary['first_date']})" if nights == 1
                else f" ({summary['first_date']} to {summary['last_date']})")

    coverage = summary["coverage"]
    if coverage is None:
        return (f"{nights} night{'s' if nights != 1 else ''} scored"
                f"{span}: {totals['scored']} projection(s) and "
                f"{totals['legs']} settled leg(s) so far. Too early to put a "
                f"percentage on it — this page quotes nothing until "
                f"{MIN_NIGHTS_TO_STATE} nights have been scored, because a "
                f"single night's projections rise and fall together and "
                f"counting them as separate evidence would overstate how "
                f"much we know.")

    return (f"{nights} nights scored{span}: {totals['scored']} projections and "
            f"{totals['legs']} settled legs.")


def line_for(label, rate, bar=None, bar_label=None):
    """One measured rate, as a sentence carrying its own denominator.

    Returns None when the rate is withheld, so a caller can skip the
    row rather than print a blank one.
    """
    if not rate:
        return None
    text = (f"{label}: {rate['rate']:.1%} "
            f"({rate['successes']} of {rate['total']}, "
            f"95% CI {rate['lo']:.1%}–{rate['hi']:.1%})")
    if bar is None:
        return text
    verdict = verdict_for(rate, bar)
    name = bar_label or f"{bar:.1%}"
    if verdict == "above":
        text += f" — clear of the {name} it has to beat"
    elif verdict == "below":
        text += f" — short of the {name} it has to beat"
    else:
        text += f" — the interval still spans the {name} it has to beat"
    return text


def money_line(pot, label):
    """The units figure, or None when nothing publishable has landed.

    Refuses on the gate as well as on an empty pot. summarise() sets
    `publishable`; a pot without the key has not been through it and is
    treated as not publishable rather than waved past.
    """
    if not pot or not pot.get("legs") or not pot.get("publishable"):
        return None
    sign = "+" if pot["profit"] >= 0 else ""
    text = (f"{label}: {sign}{pot['profit']:.2f} units on {pot['legs']} leg(s) "
            f"at one unit each ({pot['roi']:+.1f}%)")
    if pot.get("break_even") is not None:
        text += f", against a {pot['break_even']:.1%} break-even"
    return text


def pending(summary):
    """What is measured but not yet shown, and what it is waiting for.

    Silence and refusal look identical on a page. A reader who sees
    coverage and the against-the-line rate, and no figure for the legs
    we would actually have listed, has no way to tell whether that
    number is being withheld or was never computed -- and the second
    reading is the one that makes a product look like it is hiding
    something.

    The listed legs accumulate at roughly a quarter the rate of all
    legs, so that row is the last to appear by a wide margin. Naming it
    is the difference between "we do not show that" and "not yet, and
    here is the bar".
    """
    if summary["nights"] < MIN_NIGHTS_TO_STATE:
        return []          # the headline already says it, in full
    totals = summary["totals"]
    out = []
    if summary["strong"] is None and totals["strong_legs"]:
        out.append(
            f"The record on the legs we would actually have listed is "
            f"being measured too — {totals['strong_correct']} of "
            f"{totals['strong_legs']} so far — but it is not quoted as a "
            f"rate until {MIN_LEGS_TO_STATE} of them have settled. Those "
            f"accumulate about four times more slowly than the rest.")
    for key, label in (("strong", "the listed legs"),
                       ("all", "every leg")):
        money = summary["money"][key]
        if money["legs"] and not money["publishable"]:
            out.append(
                f"The units figure for {label} is withheld on the same "
                f"rule ({money['legs']} priced legs so far).")
    return out


def caveats(summary):
    """Everything a reader deserves to know before believing the numbers.

    Always returned, never suppressed once the sample is healthy. A
    caveat that disappears when the figures look good is advertising.
    """
    out = [
        "Projections are the app's baseline — no opponent-defence "
        "multiplier, no teammate or scheme adjustments. Those are choices "
        "a reader makes on the page, and a forward test should measure "
        "what the app produces on its own.",
        "A player who did not play is void, not a miss. Voids are counted "
        "and kept out of every percentage.",
    ]
    totals = summary["totals"]
    if totals.get("void_players"):
        out.append(f"{totals['void_players']} player-game(s) voided so far.")
    if totals.get("legs_push"):
        out.append(f"{totals['legs_push']} leg(s) landed exactly on the line "
                   f"and are not counted either way.")
    for key, label in (("all", "every leg"), ("strong", "the listed legs")):
        pot = summary["money"][key]
        if pot["withheld_legs"]:
            out.append(
                f"{pot['withheld_legs']} priced leg(s) across "
                f"{pot['withheld_nights']} night(s) are excluded from the "
                f"units figure for {label}: those nights were too thin to "
                f"aggregate without republishing the bookmaker's prices.")
    if summary.get("empty_nights"):
        out.append(
            f"{summary['empty_nights']} night(s) settled nothing — every "
            f"player void — and are not counted above. Preseason games are "
            f"the usual reason: they are exhibition matches, the app does "
            f"not hold box scores for them, and they are not evidence "
            f"about anything.")
    if summary["unreadable"]:
        out.append(f"{summary['unreadable']} scored night(s) could not be "
                   f"read and are missing from these totals.")
    out.append(
        "The market's lines are never published here. Each night's record "
        "carries the SHA-256 of the odds snapshot it was scored against, "
        "and the raw snapshot is produced on request.")
    return out
