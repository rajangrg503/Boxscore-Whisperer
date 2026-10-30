"""Where the projections go wrong -- patterns across every night in the
journal, for the journal app's Misses tab.

Reads the per-line records tools/daily_journal.py keeps for every
player, stat and night (the baseline projection the app shows before any
adjustment, its 80% range, and what happened), and answers three
questions:

  1. BY STAT -- for points, rebounds and assists, how often the result
     landed inside the range, above it, or below it. A calibrated 80%
     range misses about 10% each way; a lopsided split is a bias.

  2. WHY THE MISSES HAPPENED -- each miss is put down to MINUTES when the
     player's minutes were at least MINUTES_GAP away from his usual (the
     median of his last ten games), and to PER-MINUTE OUTPUT otherwise.
     If most misses are minutes, the projection is fine and the minutes
     guess is what to improve -- and the reverse. This is a heuristic,
     not a decomposition: a player can play more because he was hot.

  3. PLAYERS -- who keeps landing on the same side of his range.

THE SAMPLE IS SMALL FOR A LONG TIME
One night's lines all move together (a blowout benches everybody), so
the effective sample is nights, not lines. Every figure carries its
counts, and the app says plainly when there are too few nights to read
anything into it -- the same discipline as engine/forward_record.py.
"""

import pandas as pd

STATS = [("PTS", "Points"), ("REB", "Rebounds"), ("AST", "Assists")]
MINUTES_GAP = 6          # minutes away from usual that counts as "minutes"
MIN_NIGHTS = 10          # below this, patterns are labelled early
MIN_PLAYER_GAMES = 3     # a player needs this many games to be listed
MIN_PLAYER_SHARE = 0.5   # ...and to miss the same way in at least half


def lines_frame(nights):
    """Every recorded projection line, one row each, across all nights.
    Nights written before lines were recorded contribute nothing."""
    rows = []
    for night in nights:
        for g in night.get("games", []):
            for line in g.get("lines") or []:
                rows.append({**line, "date": night["date"], "game_id": g["game_id"]})
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["side"] = "inside"
    frame.loc[frame["actual"] > frame["high"], "side"] = "above"
    frame.loc[frame["actual"] < frame["low"], "side"] = "below"
    usual = pd.to_numeric(frame["usual_min"], errors="coerce")
    frame["min_gap"] = frame["min"] - usual
    frame["cause"] = None
    missed = frame["side"] != "inside"
    known = usual.notna()
    frame.loc[missed & known & (frame["min_gap"].abs() >= MINUTES_GAP), "cause"] = "minutes"
    frame.loc[missed & known & (frame["min_gap"].abs() < MINUTES_GAP), "cause"] = "per_minute"
    return frame


def nights_with_lines(nights):
    return sorted({n["date"] for n in nights
                   if any(g.get("lines") for g in n.get("games", []))})


def by_stat(frame):
    """Per stat: lines, and the share inside / above / below the range."""
    out = []
    for col, label in STATS:
        part = frame[frame["stat"] == col]
        n = len(part)
        if not n:
            continue
        counts = part["side"].value_counts()
        out.append({
            "stat": label, "lines": n,
            "inside": round(100 * counts.get("inside", 0) / n, 1),
            "above": round(100 * counts.get("above", 0) / n, 1),
            "below": round(100 * counts.get("below", 0) / n, 1),
        })
    return pd.DataFrame(out)


def causes(frame):
    """How the misses split between minutes and per-minute output, and
    which way the minutes went when they were the cause."""
    missed = frame[frame["cause"].notna()]
    n = len(missed)
    if not n:
        return None
    by_minutes = missed[missed["cause"] == "minutes"]
    return {
        "misses": n,
        "minutes": len(by_minutes),
        "per_minute": n - len(by_minutes),
        "minutes_share": round(100 * len(by_minutes) / n),
        # Above the range because he played more, below because he played less.
        "more_minutes_above": int(((by_minutes["side"] == "above") & (by_minutes["min_gap"] > 0)).sum()),
        "fewer_minutes_below": int(((by_minutes["side"] == "below") & (by_minutes["min_gap"] < 0)).sum()),
    }


def players(frame):
    """Players who keep landing on the same side of their range.

    Counted per GAME, not per line: a player's points, rebounds and
    assists in one game are one piece of evidence, not three. A game
    counts as 'above' if any of his lines was above and none below, and
    the reverse for 'below'."""
    if frame.empty:
        return pd.DataFrame()
    per_game = (frame.groupby(["pid", "name", "team", "game_id"])["side"]
                .agg(lambda sides: "above" if "above" in set(sides) and "below" not in set(sides)
                     else "below" if "below" in set(sides) and "above" not in set(sides)
                     else "mixed" if {"above", "below"} <= set(sides) else "inside")
                .reset_index())
    minutes_cause = (frame[frame["cause"].notna()].groupby("pid")["cause"]
                     .agg(lambda c: (c == "minutes").mean()))
    rows = []
    for (pid, name), part in per_game.groupby(["pid", "name"]):
        games = len(part)
        if games < MIN_PLAYER_GAMES:
            continue
        above = int((part["side"] == "above").sum())
        below = int((part["side"] == "below").sum())
        side, count = ("above", above) if above >= below else ("below", below)
        if count / games < MIN_PLAYER_SHARE or count < 2:
            continue
        share = minutes_cause.get(pid)
        rows.append({
            "Player": name, "Team": part["team"].iloc[-1], "Games": games,
            "Pattern": "Beats his range" if side == "above" else "Falls short of his range",
            "Games that way": count,
            "Mostly": None if share is None else ("minutes" if share >= 0.5 else "per-minute output"),
            "_rank": count / games + count / 100,
        })
    table = pd.DataFrame(rows)
    if table.empty:
        return table
    return table.sort_values("_rank", ascending=False).drop(columns="_rank").reset_index(drop=True)


def headline(frame, n_nights):
    """One plain sentence for the top of the tab."""
    if frame.empty:
        return None
    inside = (frame["side"] == "inside").mean() * 100
    above = (frame["side"] == "above").mean() * 100
    below = (frame["side"] == "below").mean() * 100
    lean = ""
    if abs(above - below) >= 4:
        lean = (" Results land above the range more often than below it, so the projections "
                "run low." if above > below else
                " Results land below the range more often than above it, so the projections "
                "run high.")
    return (f"Across {n_nights} night{'s' if n_nights != 1 else ''} and {len(frame):,} lines, "
            f"{inside:.0f}% landed in the 80% range, {above:.0f}% above it and "
            f"{below:.0f}% below.{lean}")
