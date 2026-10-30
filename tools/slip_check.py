"""Check a slip before placing it -- the evidence behind each leg, from
Boxscore Whisperer's own engine, using only games before that night.

For every leg:
  * RECENT FORM  how often he cleared this exact line in his last 10.
  * MINUTES      his last 10 minutes, and whether they've been steady.
                 Unsteady minutes were behind most early misses.
  * PROJECTION   the engine's baseline, moved by the opponent-defense
                 layer and (if you say who's out) the missing-teammates
                 layer -- the same layers and formulas as the app -- and
                 the chance that clears the line, from the calibrated
                 distribution. Combo stats use a normal approximation
                 over his own game-by-game totals: marked rough.
  * WARNINGS     too few games for a projection, two legs on one player
                 (they tend to land or miss together), a leg already in
                 another open slip, unsteady minutes, a line he's rarely
                 cleared lately.

For the slip: the chance every leg lands if the legs were independent,
beside the chance Sportsbet's price implies. Same-game legs are NOT
independent -- that's said on the page, not hidden.

It shows evidence. It doesn't say what to bet, and whether these
estimates beat Sportsbet's prices is exactly what the Picks tab measures
afterwards (calibration below), rather than something assumed.

One honest leak, for checks run on games already played: the defense
layer uses the team's season-to-date rating as of now, not as of that
night. Everything else is strictly games before the date.
"""

import datetime
import math
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import pandas as pd  # noqa: E402

from engine import cache  # noqa: E402
from engine.baseline_stats import stats_from_gamelog  # noqa: E402
from engine.distribution import distribution_for  # noqa: E402
from engine.season import season_for_date  # noqa: E402
from engine.team_ids import TEAM_ID_BY_ABBR  # noqa: E402

import daily_journal as dj  # noqa: E402
import picks as pk  # noqa: E402

RECENT = 10
THIN_SEASON = 10              # fewer current-season games than this: ranges run narrow
UNSTEADY_MINUTES_SD = 6.0      # minutes spread (sd, last 10) that counts as unsteady
LOW_RECENT_SHARE = 0.4         # cleared the line in under 40% of the last 10
DEF_ADJUSTMENT_STRENGTH = 0.5  # engine/adjustments/defense.py
MIN_TEAM_GAMES = 5             # current-season defense once teams have this many games


def _prev_season(season):
    start = int(season[:4])
    return f"{start - 1}-{str(start)[-2:]}"


def history(pid, day):
    """Every game before `day`: this season first, then last season's,
    newest first."""
    season = season_for_date(day)
    frames = []
    for s in (season, _prev_season(season)):
        f = pk._log(pid, s)
        if not f.empty:
            f = f[f["_d"].map(lambda d: d is not None and d < day)]
            frames.append(f.sort_values("_d", ascending=False))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def projection_frame(pid, day):
    """The games the engine projects from: this season once he has five,
    otherwise last season's -- same rule as the app."""
    season = season_for_date(day)
    cur = pk._log(pid, season)
    if not cur.empty:
        cur = cur[cur["_d"].map(lambda d: d is not None and d < day)]
    if len(cur) >= dj.MIN_PRIOR_GAMES:
        return cur, season
    prev = pk._log(pid, _prev_season(season))
    return prev, _prev_season(season)


_defense_cache = {}


def defense_multiplier(opponent, day):
    """(multiplier, note) from the opponent's defensive rating vs the
    league -- engine/adjustments/defense.py's formula, from the cache."""
    if not opponent or opponent not in TEAM_ID_BY_ABBR:
        return 1.0, None
    season = season_for_date(day)
    key = season
    if key not in _defense_cache:
        table = None
        for s in (season, _prev_season(season)):
            payload = cache.read_payload(f"team_stats_advanced_{s}")
            frame = pd.DataFrame((payload or {}).get("data") or [])
            if not frame.empty and "DEF_RATING" in frame and (
                    s != season or frame.get("GP", pd.Series([0])).max() >= MIN_TEAM_GAMES):
                table = (frame, s)
                break
        _defense_cache[key] = table
    table = _defense_cache[key]
    if table is None:
        return 1.0, None
    frame, used = table
    row = frame[frame["TEAM_ID"] == TEAM_ID_BY_ABBR[opponent]]
    if row.empty:
        return 1.0, None
    rating, league = float(row["DEF_RATING"].iloc[0]), float(frame["DEF_RATING"].mean())
    mult = 1 + DEF_ADJUSTMENT_STRENGTH * (rating - league) / league
    tougher = "tougher" if mult < 1 else "softer"
    return mult, (f"{opponent} defense is {tougher} than average "
                  f"({rating:.1f} vs {league:.1f}, {used}): x{mult:.3f}")


def teammates_multipliers(pid, day, out_names):
    """{stat: multiplier} from the engine's missing-teammates layer, or {}."""
    if not out_names:
        return {}, None
    try:
        from engine.adjustments.teammates import get_teammate_availability_adjustment
        frame, season = projection_frame(pid, day)
        result = get_teammate_availability_adjustment(pid, list(out_names), season,
                                                      df=frame.drop(columns=["_d"], errors="ignore"))
    except Exception:
        return {}, None
    if not result.applied:
        return {}, None
    return {col: result.multiplier_for(col) for col in ("PTS", "REB", "AST", "FG3M", "STL", "BLK")}, \
        f"with {', '.join(out_names)} out: measured from his games without them"


def _normal_over(line, mean, sd):
    if sd <= 0:
        return 1.0 if mean > line else 0.0
    return 0.5 * math.erfc((line - mean) / (sd * math.sqrt(2)))


def opponent_for(leg):
    """The other team in the leg's game, from the slip (or, for a game
    already played, his game log)."""
    game = leg.get("game") or ""
    teams = [t for t in game.split("@") if t]
    if leg.get("team") and len(teams) == 2 and leg["team"] in teams:
        return teams[1] if teams[0] == leg["team"] else teams[0]
    if leg.get("pid") and leg.get("us_date"):
        day = datetime.date.fromisoformat(leg["us_date"])
        f = pk._log(leg["pid"], season_for_date(day))
        row = f[f["_d"] == day] if not f.empty else f
        if len(row):
            return str(row.iloc[0]["MATCHUP"]).split()[-1]
    return None


def check_leg(leg, out_names=None):
    """The evidence behind one leg, from games before its night."""
    out = {"flags": [], "chance": None, "proj": None, "recent": None, "minutes": None,
           "notes": [], "rough": False}
    if not leg.get("pid") or leg.get("line") is None or not leg.get("us_date"):
        out["flags"].append("Couldn't read this leg fully, so it can't be checked.")
        return out
    day = datetime.date.fromisoformat(leg["us_date"])
    cols = pk.STATS[leg["stat"]][0]
    past = history(leg["pid"], day)
    if past.empty or not all(c in past for c in cols):
        out["flags"].append("No games on record: no projection or recent form.")
        return out

    # Recent form against this exact line, and minutes.
    under = leg.get("side") == "under"
    recent = past.head(RECENT)
    values = [float(sum(float(r[c]) for c in cols)) for _, r in recent.iterrows()]
    over = sum((v < leg["line"]) if under else (v > leg["line"]) for v in values)
    out["recent"] = {"over": over, "of": len(values), "values": values, "under": under}
    minutes = pd.to_numeric(recent.get("MIN"), errors="coerce").dropna()
    if len(minutes):
        sd = float(minutes.std()) if len(minutes) > 1 else 0.0
        out["minutes"] = {"median": float(minutes.median()), "low": float(minutes.min()),
                          "high": float(minutes.max()), "steady": sd < UNSTEADY_MINUTES_SD}
        if sd >= UNSTEADY_MINUTES_SD:
            out["flags"].append(f"Minutes have swung a lot lately ({minutes.min():.0f} to "
                                f"{minutes.max():.0f} in his last {len(minutes)}).")
    if len(values) >= 5 and over / len(values) < LOW_RECENT_SHARE:
        out["flags"].append(f"{'Stayed under' if under else 'Cleared'} this line in only {over} of "
                            f"his last {len(values)}.")

    # The engine's projection.
    frame, used_season = projection_frame(leg["pid"], day)
    if used_season == season_for_date(day) and len(frame) < THIN_SEASON:
        out["thin"] = len(frame)
        out["flags"].append(
            f"Projected from only {len(frame)} games this season (the engine drops last season "
            "at 5). Ranges this early run too narrow, so the chance is likely overstated.")
    if len(frame) < dj.MIN_PRIOR_GAMES:
        out["flags"].append("Too few games for a projection (a rookie, or back from a long "
                            "absence).")
        return out
    opponent = opponent_for(leg)
    d_mult, d_note = defense_multiplier(opponent, day)
    t_mults, t_note = teammates_multipliers(leg["pid"], day, out_names)
    out["notes"] = [n for n in (d_note, t_note) if n]
    out["opponent"] = opponent
    stats, n_games = stats_from_gamelog(frame.drop(columns=["_d"], errors="ignore"))
    if len(cols) == 1:
        mean, spread = stats.get(cols[0], (None, None))
        if mean is None or pd.isna(mean):
            return out
        mult = d_mult * t_mults.get(cols[0], 1.0)
        dist = distribution_for(cols[0], mean * mult, spread, n_games)
        if dist is not None:
            low, high = dist.interval(0.8)
            out["proj"] = {"low": round(low, 1), "mid": round(dist.quantile(0.5), 1),
                           "high": round(high, 1)}
            over_chance = float(dist.sf(leg["line"]))
            out["chance"] = round(1 - over_chance if under else over_chance, 3)
            return out
        sd, center = float(spread or 0), mean * mult
    else:
        # Combos: the engine's per-stat means, summed and adjusted; the
        # spread from his own game totals, which carries how the stats
        # move together. The engine doesn't calibrate combos: rough.
        center = sum(float(stats[c][0]) * d_mult * t_mults.get(c, 1.0) for c in cols)
        totals = sum(frame[c].astype(float) for c in cols)
        sd = float(totals.std()) if len(totals) > 1 else 0.0
        out["rough"] = True
    out["proj"] = {"low": round(max(center - 1.2816 * sd, 0), 1), "mid": round(center, 1),
                   "high": round(center + 1.2816 * sd, 1)}
    over_chance = _normal_over(leg["line"], center, sd)
    out["chance"] = round(1 - over_chance if under else over_chance, 3)
    return out


def check_slip(slip, open_slips=(), out_names=None):
    """Every leg checked, plus slip-level warnings and the combined chance."""
    legs = [dict(l, check=check_leg(l, out_names)) for l in slip["legs"]]
    seen = {}
    for l in legs:
        seen.setdefault((l.get("player"), l.get("us_date")), []).append(l)
    for group in seen.values():
        if len(group) > 1:
            for l in group:
                l["check"]["flags"].append(
                    f"{len(group)} legs on {l['player']} in this slip: they tend to land or "
                    "miss together.")
    elsewhere = {}
    for other in open_slips:
        if other.get("id") == slip.get("id"):
            continue
        for l in other["legs"]:
            elsewhere.setdefault((l.get("player"), l.get("stat"), l.get("us_date")), 0)
            elsewhere[(l.get("player"), l.get("stat"), l.get("us_date"))] += 1
    for l in legs:
        n = elsewhere.get((l.get("player"), l.get("stat"), l.get("us_date")))
        if n:
            l["check"]["flags"].append(
                f"Also in {n} other open slip{'s' if n != 1 else ''}: one game decides "
                f"{'both' if n == 1 else 'all of them'}.")
    chances = [l["check"]["chance"] for l in legs if l["check"]["chance"] is not None]
    # With unchecked legs this is an upper limit: another leg can only lower it.
    combined = math.prod(chances) if chances else None
    games = {l.get("game") or l.get("us_date") for l in legs}
    return {
        "legs": legs,
        "thin": sum(1 for l in legs if l["check"].get("thin")),
        "combined": round(combined, 4) if combined is not None else None,
        "unchecked": len(legs) - len(chances),
        "implied": round(1 / slip["price"], 4) if slip.get("price") else None,
        "same_game": len(legs) > 1 and len(games) == 1,
    }


def calibration(graded_slips):
    """How legs landed, grouped by the chance the check gave them before
    the game, and by warning. This is what says whether to trust it."""
    rows = []
    for s in graded_slips:
        for l in s["legs"]:
            if l["result"] not in ("won", "lost"):
                continue
            c = check_leg(l)
            rows.append({"chance": c["chance"], "won": l["result"] == "won",
                         "unsteady": bool(c["minutes"] and not c["minutes"]["steady"]),
                         "low_recent": any(" this line in only " in f for f in c["flags"])})
    frame = pd.DataFrame(rows)
    if frame.empty:
        return None
    buckets = []
    for lo, hi, label in ((0, .5, "Under 50%"), (.5, .65, "50 to 65%"), (.65, .8, "65 to 80%"),
                          (.8, 1.01, "80% or more")):
        part = frame[frame["chance"].between(lo, hi, inclusive="left")]
        if len(part):
            buckets.append({"label": label, "legs": len(part), "landed": int(part["won"].sum()),
                            "expected": round(float(part["chance"].mean()) * 100)})
    flags = []
    for col, label in (("unsteady", "Unsteady minutes"), ("low_recent", "Rarely cleared lately")):
        part, rest = frame[frame[col]], frame[~frame[col]]
        if len(part):
            flags.append({"label": label, "legs": len(part), "landed": int(part["won"].sum()),
                          "others": len(rest), "others_landed": int(rest["won"].sum())})
    return {"buckets": buckets, "flags": flags, "legs": len(frame),
            "no_chance": int(frame["chance"].isna().sum())}
