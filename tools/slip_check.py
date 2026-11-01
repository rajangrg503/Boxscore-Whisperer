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
import json
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
import picks as pk
import role_blend as rb
import matchup_edge as mx  # noqa: E402

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


def _season_parts(pid, day):
    """(this season's games before `day`, last season's games)."""
    season = season_for_date(day)
    cur = pk._log(pid, season)
    if not cur.empty:
        cur = cur[cur["_d"].map(lambda d: d is not None and d < day)]
    prev = pk._log(pid, _prev_season(season))
    return cur, prev


def _mpg_and_rate(frame):
    """(minutes per game, shot attempts per 36) -- the two numbers that say
    whether a role is the same role."""
    if frame is None or frame.empty or "MIN" not in frame:
        return None, None
    mins = pd.to_numeric(frame["MIN"], errors="coerce").dropna()
    if mins.empty or mins.sum() <= 0:
        return None, None
    fga = pd.to_numeric(frame.get("FGA"), errors="coerce").dropna() if "FGA" in frame else None
    rate = float(fga.sum()) / float(mins.sum()) * 36 if fga is not None and len(fga) else None
    return float(mins.mean()), rate


def _out_ids(team, out_names):
    """Tonight's absentees on `team`, as player ids, so a team is not
    credited with coverage it will not have on the floor."""
    if not team or not out_names:
        return ()
    from engine.cache import read_payload
    from engine.team_ids import TEAM_ID_BY_ABBR
    wanted = {pk._norm(n) for n in out_names}
    ids = []
    for row in ((read_payload(f"roster_{TEAM_ID_BY_ABBR.get(team, 0)}") or {}).get("data") or []):
        if pk._norm(row["PLAYER"]) in wanted:
            ids.append(int(row["PLAYER_ID"]))
    return tuple(ids)


def role_read(pid, day):
    """How much of last season still describes this player.

    Returned on every leg so the reason a projection moved is visible
    rather than buried: minutes then and now, and the similarity that
    sets how much weight last season keeps.
    """
    cur, prev = _season_parts(pid, day)
    mpg_cur, rate_cur = _mpg_and_rate(cur)
    mpg_prev, rate_prev = _mpg_and_rate(prev)
    if mpg_cur is None or mpg_prev is None:
        return {"similarity": 1.0 if mpg_prev is None else 0.5,
                "mpg_cur": mpg_cur, "mpg_prev": mpg_prev,
                "n_cur": 0 if cur is None or cur.empty else len(cur),
                "minutes_up": False, "only": "this" if mpg_prev is None else "last"}
    sim = rb.role_similarity(mpg_cur, mpg_prev, rate_cur, rate_prev)
    return {"similarity": round(sim, 2), "mpg_cur": round(mpg_cur, 1),
            "mpg_prev": round(mpg_prev, 1), "n_cur": len(cur),
            "minutes_up": mpg_cur > mpg_prev * 1.12, "only": None}


def blended_stats(pid, day, cols):
    """(center, sd, apart) for `cols`, from BOTH seasons.

    Replaces the five-game cliff in projection_frame: last season is
    scaled to this season's minutes and kept in proportion to how much
    the role carried over, instead of being switched off the moment a
    fifth game is played.
    """
    cur, prev = _season_parts(pid, day)
    role = role_read(pid, day)
    sim = role["similarity"]
    mpg_cur, mpg_prev = role.get("mpg_cur"), role.get("mpg_prev")

    def parts(frame):
        if frame is None or frame.empty or not all(c in frame for c in cols):
            return None, None, 0
        stats, n = stats_from_gamelog(frame.drop(columns=["_d"], errors="ignore"))
        if len(cols) == 1:
            mean, spread = stats.get(cols[0], (None, None))
            if mean is None or pd.isna(mean):
                return None, None, 0
            return float(mean), float(spread or 0.0), n
        totals = sum(frame[c].astype(float) for c in cols)
        return float(sum(float(stats[c][0]) for c in cols)), float(totals.std() or 0.0), n

    c_mean, c_sd, c_n = parts(cur)
    p_mean, p_sd, _ = parts(prev)
    if p_mean is not None and mpg_cur and mpg_prev:
        p_mean = rb.scale_to_minutes(p_mean, mpg_cur, mpg_prev)
        p_sd = rb.scale_to_minutes(p_sd, mpg_cur, mpg_prev)
    if c_mean is None and p_mean is None:
        return None, None, None, role
    apart = abs(c_mean - p_mean) if (c_mean is not None and p_mean is not None) else 0.0
    center = rb.blend_value(c_n, c_mean, p_mean, sim)
    base = c_sd if c_sd else p_sd
    if c_sd and p_sd:
        base = rb.blend_value(c_n, c_sd, p_sd, sim)
    return center, rb.widen(base, sim, apart), apart, role


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


def _teams_in(frame):
    """The team abbreviations a game log belongs to."""
    if frame is None or frame.empty or "MATCHUP" not in frame:
        return set()
    return {str(m).split()[0] for m in frame["MATCHUP"] if str(m).split()}


def _shared_games(pid, mate_pid, day):
    """(games together, games apart) -- counting only seasons in which they
    were actually TEAMMATES.

    Without that restriction Avdija showed "measured from 60 games
    without Ja Morant", which were last season's games, when Morant
    played for Memphis. A game without a man who was never on your team
    says nothing about what happens when he is hurt; counting it turns
    an empty sample into a confident one.
    """
    together = apart = 0
    for season in (season_for_date(day), _prev_season(season_for_date(day))):
        mine = pk._log(pid, season)
        theirs = pk._log(mate_pid, season)
        if mine is None or mine.empty:
            continue
        mine = mine[mine["_d"].map(lambda d: d is not None and d < day)]
        if not (_teams_in(mine) & _teams_in(theirs)):
            continue                      # not teammates that season
        mate_days = set() if theirs is None or theirs.empty else set(theirs["_d"])
        for d in mine["_d"]:
            if d in mate_days:
                together += 1
            else:
                apart += 1
    return together, apart


def teammate_evidence(pid, out_names, day, team=None):
    """What the missing-teammates layer actually has to go on.

    Avdija, 31 Oct 2026: Ja Morant was out and the layer returned a
    multiplier of 1.0 -- which reads exactly like "checked, makes no
    difference" and in fact meant "nothing to check". Morant had played
    20 games for Memphis last season and all four of Avdija's games this
    one, so "Avdija without Morant" was a sample of zero.

    Shaedon Sharpe is the opposite trap: 55 games alongside Avdija last
    season and none this one, so every current-season game is ALREADY
    without him and a 'without Sharpe' bump would be counted twice.

    Returns one verdict per absent teammate so the difference between
    no effect, no evidence, and already-priced-in is visible instead of
    arriving as the same number.

    A fourth case, karma's, 31 Oct: "sharpe being there or not shouldnt
    affect avdija number in any way. they dont eat into eachother." His
    71 games last season say it exactly --

        with / without Sharpe (G, 6-5)   FGA/36  17.3 -> 17.3
        with / without Holiday (G, 6-4)  FGA/36  17.4 -> 17.1
        with / without Grant (F, 6-7)    FGA/36  16.9 -> 18.1

    Shot volume does not move for the guards and moves a full attempt
    for the forward. An absent teammate only redistributes shots if he
    was eating from the same plate, so the adjustment is gated on
    archetype rather than applied to whoever happens to be out.
    """
    from engine.team_ids import TEAM_ID_BY_ABBR
    from engine.cache import read_payload
    ids = {}
    for abbr, tid in TEAM_ID_BY_ABBR.items():
        for row in ((read_payload(f"roster_{tid}") or {}).get("data") or []):
            ids[pk._norm(row["PLAYER"])] = int(row["PLAYER_ID"])
    verdicts = []
    for name in (out_names or []):
        mate = ids.get(pk._norm(name))
        if not mate:
            continue
        together, apart = _shared_games(pid, mate, day)
        cur = pk._log(pid, season_for_date(day))
        cur_n = 0 if cur is None or cur.empty else len(
            cur[cur["_d"].map(lambda d: d is not None and d < day)])
        cur_together = 0
        if cur_n:
            theirs = pk._log(mate, season_for_date(day))
            mate_days = set() if theirs is None or theirs.empty else set(theirs["_d"])
            cur_together = sum(1 for d in cur["_d"] if d in mate_days)
        mine = mx.player_archetype(pid, team) if team else None
        theirs = mx.archetype_anywhere(mate)
        if mine and theirs and mine != theirs:
            verdicts.append((name, "elsewhere", f"{name} is a {theirs} and he is a {mine}, so "
                             f"they do not take shots from each other (this says nothing about "
                             f"who creates them)"))
        elif together == 0 and apart == 0:
            verdicts.append((name, "none", f"{name} has never been his teammate in a game on "
                             f"record, so there is nothing measuring what changes without him"))
        elif apart == 0:
            verdicts.append((name, "none", f"{name} has played in every one of the {together} "
                             f"games this is measured from, so there is no evidence of what "
                             f"changes without him"))
        elif cur_n and cur_together == 0:
            verdicts.append((name, "priced", f"{name} has missed all {cur_n} of his games this "
                             f"season, so his absence is already in these numbers"))
        else:
            verdicts.append((name, "measured", f"measured from {apart} game(s) without {name}, "
                             f"who plays the same position"))
    return verdicts


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


_slate_cache = {}


def _slate_opponent(team, day):
    """The other team in `team`'s game that night, from the availability
    feed. Returns None when the slate for that date was never captured --
    an unknown opponent is better than a guessed one."""
    if not team:
        return None
    key = day.isoformat()
    if key not in _slate_cache:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "slate_lines", f"availability_{day.strftime('%Y%m%d')}.json")
        pairs = {}
        try:
            with open(path) as handle:
                for game in (json.load(handle).get("games") or []):
                    home, away = game.get("home"), game.get("away")
                    if home and away:
                        pairs[home], pairs[away] = away, home
        except (OSError, ValueError):
            pairs = {}
        _slate_cache[key] = pairs
    return _slate_cache[key].get(team)


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
        # Not played yet, so no log row exists -- the same trap that dated
        # a 31 Oct slip to 29 Oct. For a game still to come the opponent
        # comes from the night's slate, written by tools/availability.py.
        return _slate_opponent(leg.get("team"), day)
    return None


def check_leg(leg, out_names=None):
    """The evidence behind one leg, from games before its night."""
    out = {"flags": [], "chance": None, "proj": None, "recent": None, "minutes": None,
           "notes": [], "rough": False}
    if pk.is_team(leg):
        out["flags"].append("Team bet: the engine projects players, not results, so this leg "
                            "isn't checked and the slip's combined chance leaves it out.")
        out["team"] = True
        return out
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

    # The projection, from BOTH seasons rather than whichever side of the
    # five-game cliff the player happens to sit on.
    center, sd, apart, role = blended_stats(leg["pid"], day, cols)
    out["role"] = role
    thin_prior = not role.get("mpg_prev")
    if center is None or (thin_prior and role.get("n_cur", 0) < dj.MIN_PRIOR_GAMES):
        out["flags"].append("Too few games for a projection (a rookie, or back from a long "
                            "absence).")
        return out
    if role.get("n_cur", 0) < THIN_SEASON:
        out["thin"] = role.get("n_cur", 0)
        carrying = (f"; last season carries the rest, scaled from {role.get('mpg_prev')} to "
                    f"{role.get('mpg_cur')} minutes" if role.get("mpg_prev") else "")
        out["flags"].append(
            f"Projected from only {role.get('n_cur', 0)} games this season{carrying}.")
    if role.get("similarity", 1.0) < 0.75:
        out["flags"].append(
            f"His role has moved ({role.get('mpg_prev')} to {role.get('mpg_cur')} minutes), so "
            f"last season only part-describes him and this chance is pulled toward a coin flip.")
    if role.get("minutes_up"):
        out["flags"].append(
            "Minutes are up on last season. Scaling the prior is linear, but production is "
            "damped on the way up, so this projection reads a little high.")
    opponent = opponent_for(leg)
    d_mult, d_note = defense_multiplier(opponent, day)
    t_mults, t_note = teammates_multipliers(leg["pid"], day, out_names)
    out["notes"] = [n for n in (d_note, t_note) if n]

    # A multiplier of 1.0 from the teammates layer reads like "checked,
    # no difference" and can mean "nothing to check". Say which.
    out["teammates"] = teammate_evidence(leg["pid"], out_names, day, leg.get("team"))
    for name, kind, text in out["teammates"]:
        if kind in ("none", "priced", "elsewhere"):
            # Not .capitalize(): these sentences start with a player's name
            # and it lowercases the rest of it ("Ja morant").
            out["flags"].append(text[0].upper() + text[1:] + ".")
            t_mults = {}
    out["opponent"] = opponent

    # Karma's question, asked of every leg: does this player have an
    # advantage in THIS matchup? Shown beside the chance, never folded
    # into it -- where the bodies are is a fact about the opponent's
    # roster, not a prediction about tonight.
    out["matchup"] = mx.read(leg["pid"], leg.get("team"), opponent,
                             season_for_date(day), exclude=_out_ids(opponent, out_names))
    if out["matchup"]:
        out["notes"].append(out["matchup"]["verdict"].capitalize() + ".")
    mult = d_mult * (t_mults.get(cols[0], 1.0) if len(cols) == 1
                     else sum(t_mults.get(c, 1.0) for c in cols) / len(cols))
    center = center * mult
    if len(cols) > 1:
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
