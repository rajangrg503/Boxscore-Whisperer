#!/usr/bin/env python3
"""pbp_patterns.py: what each team runs, and what each defence has no answer for.

Built from NBA.com play-by-play (karma, 14 Nov 2026: "if you check the pattern on
play by play you can determine what team likes to run, what opponents has nothing
for it"). Every field-goal attempt goes into one bucket by how the shot was
created (putback, transition, cut, drive, other rim, pull-up two, other mid-range,
off-the-dribble three, set three, deep three). For each team we keep, on offence
and on defence: attempts, makes, points, assisted makes, and "streaks" (the same
bucket scoring 3 times inside 3 minutes, i.e. a pattern the defence did not stop).

Getting the data. cdn.nba.com is blocked from the device shell and the cloud
container but answers from an nba.com tab, so normally:
  1. run tools/pbp_patterns_fetch.js in Chrome on an nba.com page
  2. save window._ppChunks, in order, to data/patterns/raw_<season>_<date>.txt
  3. python3 tools/pbp_patterns.py --import data/patterns/raw_<season>_<date>.txt --asof <date> [--sum <window._ppSum>]
On a machine that can reach cdn.nba.com: python3 tools/pbp_patterns.py --fetch --asof <date>

Use:
  python3 tools/pbp_patterns.py POR SAC          matchup card, both directions

Flags (backtested 14 Nov on 113 games, each judged only on games before it):
  OPEN / SHUT    the offence leans on a shot type and the defence allows the most /
                 the fewest of it. Shot VOLUME follows the defence: against SHUT
                 (bottom 10) offences got 10.0 of their usual 11.1 such shots and
                 more than usual only 34% of the time (bottom 5: 9.4 v 11.3, 27%);
                 against OPEN they got their usual amount. The team's overall points
                 per shot did not drop: the shots move to other types.
  FEEDS / DEAD END  points per shot allowed. Only kept for drives and other mid-range,
                 the two shot types where a defence's points per shot allowed carried
                 to the next game. Elsewhere it was noise (FEEDS +0.03 v DEAD END
                 +0.00 points per shot over 104 games).
  NO ANSWER      streaks allowed; not tested.
Limits: the feed does not name the action (pick-and-roll, handoff, screen), only
how the shot was taken and who passed; a good pass that leads to a miss is not
recorded. This is a READ, not a pick rule: none of it is tested against props.

The bucket rules MUST match tools/pbp_patterns_fetch.js.
"""
import argparse
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "patterns")

BUCKETS = ["putback", "transition", "cut", "drive", "rim_other", "pullup2",
           "mid_other", "three_dribble", "three_set", "three_deep"]
LABEL = {
    "putback": "Putbacks and tips",
    "transition": "Fast break",
    "cut": "Cuts and lobs",
    "drive": "Drives",
    "rim_other": "Other rim (post, roll, standing)",
    "pullup2": "Pull-up twos",
    "mid_other": "Other mid-range",
    "three_dribble": "Threes off the dribble",
    "three_set": "Set threes (under 26 ft)",
    "three_deep": "Deep threes (26 ft+)",
}
MIN_SHARE = 0.05     # ignore buckets under 5% of a team's shots when flagging
TOP = 10             # "likes to run it" / "gives it up" = top 10 of 30
# Points per shot allowed only predicted the next game for these (14 Nov backtest).
PPS_BUCKETS = ("drive", "mid_other")
STREAK_WINDOW = 180  # seconds


def bucket(a):
    """Bucket for one field-goal action from the NBA.com play-by-play feed."""
    d = (a.get("descriptor") or "").lower()
    q = a.get("qualifiers") or []
    st = (a.get("subType") or "").lower()
    dist = a.get("shotDistance") or 0
    three = a.get("actionType") == "3pt"
    if re.search(r"putback|tip", d):
        return "putback"
    if "fastbreak" in q:
        return "transition"
    if three:
        if re.search(r"pullup|step back", d):
            return "three_dribble"
        return "three_deep" if dist >= 26 else "three_set"
    if re.search(r"cutting|alley-oop", d):
        return "cut"
    if "driving" in d:
        return "drive"
    if st in ("layup", "dunk") or dist <= 5:
        return "rim_other"
    if re.search(r"pullup|step back", d):
        return "pullup2"
    return "mid_other"


def zone(b):
    if b.startswith("three"):
        return "3"
    return "r" if b in ("putback", "transition", "cut", "drive", "rim_other") else "m"


def clock_seconds(a):
    """Elapsed game seconds for an action (period + ISO clock like PT05M14.00S)."""
    m = re.match(r"PT(\d+)M([\d.]+)S", a.get("clock") or "PT12M00.00S")
    left = int(m.group(1)) * 60 + float(m.group(2))
    q = a.get("period") or 1
    base = (q - 1) * 720 if q <= 4 else 2880 + (q - 5) * 300
    return base + ((720 if q <= 4 else 300) - left)


def streaks(times, window=STREAK_WINDOW):
    """Count non-overlapping runs of 3 makes inside `window` seconds."""
    t = sorted(times)
    n = p = 0
    while p + 2 < len(t):
        if t[p + 2] - t[p] <= window:
            n += 1
            p += 3
        else:
            p += 1
    return n


def checksum(s):
    """Same rolling sum as the fetch script, to catch a mistyped chunk."""
    total = 0
    for i, c in enumerate(s):
        total = (total + ord(c) * (i % 97 + 1)) % 1000000007
    return total


def _blank():
    return {"g": 0, "o": [[0, 0, 0, 0] for _ in BUCKETS], "d": [[0, 0, 0] for _ in BUCKETS],
            "so": [0] * len(BUCKETS), "sd": [0] * len(BUCKETS), "pairs": {}}


def aggregate(games, season=""):
    """games: iterable of (away, home, actions). Returns the same shape the fetch script exports."""
    T = {}
    n = 0
    for away, home, actions in games:
        n += 1
        for t in (away, home):
            T.setdefault(t, _blank())["g"] += 1
        makes = {}
        for a in actions:
            if not a.get("isFieldGoal") or not a.get("teamTricode"):
                continue
            off = a["teamTricode"]
            de = home if off == away else away
            b = bucket(a)
            i = BUCKETS.index(b)
            made = a.get("shotResult") == "Made"
            pts = (3 if a.get("actionType") == "3pt" else 2) if made else 0
            o, d = T[off]["o"][i], T[de]["d"][i]
            o[0] += 1
            d[0] += 1
            if made:
                o[1] += 1
                d[1] += 1
                o[2] += pts
                d[2] += pts
                if a.get("assistPersonId"):
                    o[3] += 1
                    k = "%s>%s|%s" % ((a.get("assistPlayerNameInitial") or "?").split(" ")[-1],
                                      (a.get("playerNameI") or "?").split(" ")[-1], zone(b))
                    T[off]["pairs"][k] = T[off]["pairs"].get(k, 0) + 1
                makes.setdefault((off, i), []).append(clock_seconds(a))
        for (off, i), times in makes.items():
            de = home if off == away else away
            k = streaks(times)
            T[off]["so"][i] += k
            T[de]["sd"][i] += k
    out = {"season": season, "games": n, "buckets": list(BUCKETS), "teams": {}}
    for t in sorted(T):
        x = T[t]
        pairs = sorted(x["pairs"].items(), key=lambda kv: -kv[1])[:5]
        out["teams"][t] = {"g": x["g"], "o": x["o"], "d": x["d"], "so": x["so"], "sd": x["sd"],
                           "pairs": [[k, v] for k, v in pairs]}
    return out


def validate(data):
    if data.get("buckets") != BUCKETS:
        raise ValueError("bucket list does not match this tool: %r" % (data.get("buckets"),))
    for t, x in data["teams"].items():
        if len(x["o"]) != len(BUCKETS) or len(x["d"]) != len(BUCKETS):
            raise ValueError("bad bucket arrays for %s" % t)
        if len(x["so"]) != len(BUCKETS) or len(x["sd"]) != len(BUCKETS):
            raise ValueError("bad streak arrays for %s" % t)
    return data


def import_raw(path, asof, expect_sum=None, out_dir=DATA):
    raw = "".join(line.rstrip("\n") for line in open(path, encoding="utf-8"))
    if expect_sum is not None and checksum(raw) != int(expect_sum):
        raise ValueError("checksum %d does not match expected %s: a chunk was mistyped or is missing"
                         % (checksum(raw), expect_sum))
    data = validate(json.loads(raw))
    data["asof"] = asof
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "%s_%s.json" % (data.get("season") or "season", asof))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.write("\n")
    return out


def fetch(asof, out_dir=DATA):
    """Direct fetch, for a machine that can reach cdn.nba.com (not the device shell)."""
    import urllib.request

    def get(url):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))

    sched = get("https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json")
    games = []
    for d in sched["leagueSchedule"]["gameDates"]:
        for g in d["games"]:
            if g["gameId"].startswith("002") and g["gameStatus"] == 3:
                acts = get("https://cdn.nba.com/static/json/liveData/playbyplay/playbyplay_%s.json"
                           % g["gameId"])["game"]["actions"]
                games.append((g["awayTeam"]["teamTricode"], g["homeTeam"]["teamTricode"], acts))
    data = aggregate(games, sched["leagueSchedule"].get("seasonYear", ""))
    data["asof"] = asof
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "%s_%s.json" % (data["season"] or "season", asof))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.write("\n")
    return out


def load(path=None, data_dir=DATA):
    if path is None:
        files = sorted(f for f in glob.glob(os.path.join(data_dir, "*.json")))
        if not files:
            raise FileNotFoundError("no pattern file in %s: run the fetch script and --import first" % data_dir)
        path = files[-1]
    return validate(json.load(open(path, encoding="utf-8")))


def _rank(values, team, high_first=True):
    order = sorted(values, key=lambda t: values[t], reverse=high_first)
    return order.index(team) + 1


def profile(data):
    """Per team and bucket: shares, points per shot, streaks per game, and league ranks (1 = most)."""
    P = {}
    for t, x in data["teams"].items():
        fo = sum(r[0] for r in x["o"]) or 1
        fd = sum(r[0] for r in x["d"]) or 1
        g = x["g"] or 1
        P[t] = {}
        for i, b in enumerate(BUCKETS):
            o, d = x["o"][i], x["d"][i]
            P[t][b] = {
                "o_share": o[0] / fo, "o_pps": o[2] / o[0] if o[0] else 0.0,
                "o_ast": o[3] / o[1] if o[1] else 0.0, "o_streak": x["so"][i] / g,
                "d_share": d[0] / fd, "d_pps": d[2] / d[0] if d[0] else 0.0,
                "d_streak": x["sd"][i] / g, "o_fga": o[0], "d_fga": d[0],
            }
    for b in BUCKETS:
        for key in ("o_share", "o_pps", "o_streak", "d_share", "d_pps", "d_streak"):
            vals = {t: P[t][b][key] for t in P}
            for t in P:
                P[t][b][key + "_rk"] = _rank(vals, t)
    return P


def matchup(data, off, de, P=None):
    """Rows for `off`'s offence against `de`'s defence, biggest part of the offence first."""
    P = P or profile(data)
    n = len(P)
    rows = []
    for b in BUCKETS:
        o, d = P[off][b], P[de][b]
        parts = []
        if o["o_share"] >= MIN_SHARE and o["o_share_rk"] <= TOP:
            # volume: does this defence let teams take a lot of this shot, or take it away?
            if d["d_share_rk"] <= TOP:
                parts.append("OPEN")
            elif d["d_share_rk"] > n - TOP:
                parts.append("SHUT")
            # efficiency: only where points per shot allowed carried over
            if b in PPS_BUCKETS:
                if d["d_pps_rk"] <= TOP:
                    parts.append("FEEDS")
                elif d["d_pps_rk"] > n - TOP:
                    parts.append("DEAD END")
        # Streak counts rise with volume, so also require that the defence is in the
        # worse half for points per shot on the pattern before calling it "no answer".
        if (d["d_streak_rk"] <= 5 and d["d_pps_rk"] <= n // 2 and o["o_share"] >= MIN_SHARE
                and d["d_streak"] > 0):
            parts.append("NO ANSWER")
        flag = " + ".join(parts)
        rows.append({"bucket": b, "flag": flag, **{k: o[k] for k in o if k.startswith("o_")},
                     **{k: d[k] for k in d if k.startswith("d_")}})
    rows.sort(key=lambda r: -r["o_share"])
    return rows


def card(data, away, home):
    P = profile(data)
    lines = ["Patterns as of %s (%s games, %s). Ranks out of %d: 1 = most."
             % (data.get("asof", "?"), data.get("games", "?"), data.get("season", ""), len(P))]
    for off, de in ((away, home), (home, away)):
        lines.append("")
        lines.append("%s offence against %s defence" % (off, de))
        lines.append("  %-34s %6s %5s  %5s %5s  | %s allows: %6s %5s %5s %5s %7s  flag"
                     % ("how the shot is created", "share", "rank", "pts/sh", "rank", de, "share", "rank", "pts/sh", "rank", "streaks"))
        for r in matchup(data, off, de, P):
            if r["o_share"] < 0.03:
                continue
            lines.append("  %-34s %5.0f%% %5d  %5.2f %5d  | %12s %5.0f%% %5d %5.2f %5d %5.2f/g  %s"
                         % (LABEL[r["bucket"]], 100 * r["o_share"], r["o_share_rk"], r["o_pps"], r["o_pps_rk"],
                            "", 100 * r["d_share"], r["d_share_rk"], r["d_pps"], r["d_pps_rk"], r["d_streak"], r["flag"]))
        pairs = data["teams"][off].get("pairs") or []
        if pairs:
            z = {"r": "rim", "m": "mid", "3": "three"}
            lines.append("  top assist pairs: " + ", ".join(
                "%s to %s (%s) x%d" % (k.split("|")[0].split(">")[0], k.split("|")[0].split(">")[1],
                                       z.get(k.split("|")[1], "?"), v) for k, v in pairs))
    lines.append("")
    lines.append("OPEN / SHUT = the offence leans on it (top %d share) and the defence allows the most / fewest of it "
                 "(top / bottom %d share of shots allowed). Backtest: against SHUT, offences got fewer of those shots "
                 "(10.0 v 11.1 usual; more than usual only 34%% of the time) and took other shots instead. "
                 "FEEDS / DEAD END = points per shot allowed, drives and other mid-range only. "
                 "NO ANSWER = top-5 for letting that pattern score 3 times in 3 minutes, worse half for points per shot (not tested). "
                 "A read, not a pick rule: not tested against props." % (TOP, TOP))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("teams", nargs="*", help="AWAY HOME")
    ap.add_argument("--import", dest="imp", help="raw chunk file saved from the fetch script")
    ap.add_argument("--asof", help="date label for --import / --fetch, e.g. 2026-11-14")
    ap.add_argument("--sum", help="window._ppSum from the fetch script, to verify the import")
    ap.add_argument("--fetch", action="store_true", help="fetch directly (needs access to cdn.nba.com)")
    ap.add_argument("--file", help="pattern file to read (default: latest in data/patterns)")
    a = ap.parse_args(argv)
    if a.imp:
        if not a.asof:
            ap.error("--import needs --asof")
        print("saved", import_raw(a.imp, a.asof, a.sum))
        return 0
    if a.fetch:
        if not a.asof:
            ap.error("--fetch needs --asof")
        print("saved", fetch(a.asof))
        return 0
    data = load(a.file)
    if len(a.teams) == 2:
        away, home = (t.upper() for t in a.teams)
        for t in (away, home):
            if t not in data["teams"]:
                ap.error("unknown team %s" % t)
        print(card(data, away, home))
        return 0
    ap.error("give AWAY HOME, or --import / --fetch")


if __name__ == "__main__":
    sys.exit(main())
