#!/usr/bin/env python3
"""ESPN athlete_id -> NBA PLAYER_ID, built from the two halves we can reach.

    python3 tools/build_crosswalk.py
    python3 tools/build_crosswalk.py --season 2027 --out engine/espn_nba_crosswalk.json

WHY THIS EXISTS
stats.nba.com is blocked from every host this project can automate on. That
is measured, not assumed: tools/probe_sources.py reports it FAILED from a
GitHub runner (19 Sep 2026, read timeout on all three endpoints, and 403 on
cdn.nba.com) and again from a cloud container (24 Oct 2026, the proxy refuses
the connection). The same probe reports the hoopR mirror on
raw.githubusercontent.com answering 200 from both.

So the only reachable source of play-by-play, shot coordinates and post-game
box scores is that mirror -- which is ESPN-sourced and keyed on ESPN athlete
ids, while every game log, roster and matchup in data_cache.zip is keyed on
NBA PLAYER_ID. Nothing from the mirror can be joined to anything we hold until
those two id spaces are bridged. This bridges them.

WHY NOT JUST USE THE ONE THEY PUBLISH
hoopR publishes nba/crosswalk/parquet/nba_player_crosswalk_{season}.parquet,
and it looks like exactly what is needed: 414 rows for 2027, all 30 teams,
espn_athlete_id and fox_athlete_id populated on every row.

Its nba_player_id column is empty on every row, and match_method reads
"unmatched" on every row. Their NBA side is built by
hoopR::nba_player_crosswalk(), which calls stats.nba.com -- so it fails for
the same reason ours does. Their file is still useful: it is a clean, current
ESPN roster, which is the half we cannot get. The NBA half comes from our own
cached rosters, and the join is done here.

MATCHING
Three passes, each recorded on the row, so a consumer can see how a player was
matched and distrust it accordingly:

    exact     folded name and team agree
    name      folded name agrees and is unique league-wide, team does not --
              an in-season move, or one source's roster is staler than the
              other's. Same player either way.
    initial   first initial + surname + team, for "CJ" against "C.J."

Nothing is matched on a guess. Anything left over is reported and left out,
because a wrong id silently attaches one player's shots to another.

Measured on the 2027 file: 414 of 414, 413 exact and 1 by name. Spot-checked
against seven ids known independently (Gilgeous-Alexander 1628983, Jokic
203999, Doncic 1629029, Embiid, Tatum, Curry, James) -- all seven correct, no
duplicate id on either side.

WHY THE OUTPUT IS NOT COMMITTED
Rosters change, and so does the mirror's copy of them. A snapshot checked in
today is wrong by December in a way nobody would notice, so the output is
gitignored and this script is the artifact. Re-run it when the roster moves.
"""
import argparse
import io
import json
import os
import sys

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.cache import read_payload                          # noqa: E402
from engine.players import fold_diacritics                     # noqa: E402
from engine.season import CURRENT_SEASON                       # noqa: E402
from engine.team_ids import TEAM_ID_BY_ABBR                    # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(REPO_ROOT, "engine", "espn_nba_crosswalk.json")

MIRROR = ("https://raw.githubusercontent.com/sportsdataverse/"
          "hoopR-nba-data/main/nba/crosswalk/parquet/"
          "nba_player_crosswalk_{season}.parquet")

# ESPN's abbreviations differ from the NBA's for a handful of teams. Anything
# not listed is already identical in both.
ESPN_TO_NBA_ABBR = {
    "GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP",
    "UTAH": "UTA", "WSH": "WAS", "PHO": "PHX",
}

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def hoopr_season(season=None):
    """hoopR labels a season by its END year: 2026-27 is 2027."""
    season = season or CURRENT_SEASON
    return int(season.split("-")[0]) + 1


def norm(name):
    """A name reduced to what two independent sources are likely to agree on.

    engine/players.fold_diacritics handles Jokic/Jokic and Schroder/Schroder;
    the rest strips punctuation (C.J. -> cj) and drops generational suffixes,
    which the two sources disagree about more often than they agree.
    """
    folded = fold_diacritics(str(name)).lower()
    folded = "".join(c if (c.isalnum() or c.isspace()) else " " for c in folded)
    return " ".join(p for p in folded.split() if p and p not in SUFFIXES)


def initial_key(name, team):
    parts = norm(name).split()
    return f"{parts[0][0]}|{parts[-1]}|{team}" if len(parts) >= 2 else None


def espn_side(season_year):
    """The mirror's roster: the half we cannot get from the NBA."""
    response = requests.get(MIRROR.format(season=season_year), timeout=40)
    response.raise_for_status()
    df = pd.read_parquet(io.BytesIO(response.content))
    df = df[["espn_athlete_id", "espn_full_name", "team_abbreviation"]].copy()
    df["abbr"] = (df["team_abbreviation"].astype(str).str.upper()
                  .map(lambda a: ESPN_TO_NBA_ABBR.get(a, a)))
    df["key"] = df["espn_full_name"].map(norm)
    return df


def nba_side():
    """Our half, straight off the cached rosters."""
    rows = []
    for abbr, team_id in TEAM_ID_BY_ABBR.items():
        payload = read_payload(f"roster_{team_id}")
        if payload is None:
            continue
        rows.extend({"nba_player_id": int(r["PLAYER_ID"]),
                     "nba_name": r["PLAYER"], "abbr": abbr}
                    for r in (payload.get("data") or []))
    df = pd.DataFrame(rows)
    df["key"] = df["nba_name"].map(norm)
    return df


def build(season_year):
    espn, nba = espn_side(season_year), nba_side()

    exact, by_name, by_initial = {}, {}, {}
    for _, r in nba.iterrows():
        exact[(r["key"], r["abbr"])] = r
        by_name.setdefault(r["key"], []).append(r)
        key = initial_key(r["nba_name"], r["abbr"])
        if key:
            by_initial.setdefault(key, []).append(r)

    matched, unmatched = [], []
    for _, e in espn.iterrows():
        hit = how = None
        if (e["key"], e["abbr"]) in exact:
            hit, how = exact[(e["key"], e["abbr"])], "exact"
        elif len(by_name.get(e["key"], [])) == 1:
            hit, how = by_name[e["key"]][0], "name"
        else:
            key = initial_key(e["espn_full_name"], e["abbr"])
            if key and len(by_initial.get(key, [])) == 1:
                hit, how = by_initial[key][0], "initial"

        if hit is None:
            unmatched.append({"espn_athlete_id": int(e["espn_athlete_id"]),
                              "espn_full_name": e["espn_full_name"],
                              "espn_team": e["abbr"]})
        else:
            matched.append({"espn_athlete_id": int(e["espn_athlete_id"]),
                            "nba_player_id": int(hit["nba_player_id"]),
                            "nba_name": hit["nba_name"],
                            "match_method": how})
    return matched, unmatched, len(espn), len(nba)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, default=None,
                        help="hoopR season label, the END year (default: this season)")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--dry-run", action="store_true",
                        help="report the match rate and write nothing")
    args = parser.parse_args(argv)

    season_year = args.season or hoopr_season()
    matched, unmatched, n_espn, n_nba = build(season_year)

    print(f"ESPN side (hoopR mirror, {season_year}): {n_espn} players")
    print(f"NBA side  (cached rosters):       {n_nba} players")
    rate = len(matched) / n_espn if n_espn else 0
    print(f"matched: {len(matched)} ({rate:.1%})")
    counts = {}
    for row in matched:
        counts[row["match_method"]] = counts.get(row["match_method"], 0) + 1
    for how, n in sorted(counts.items()):
        print(f"   {how:8s} {n}")

    if unmatched:
        print(f"\nunmatched ({len(unmatched)}) -- left out, not guessed at:")
        for row in unmatched[:25]:
            print(f"   {row['espn_full_name']:26s} {row['espn_team']}")

    ids = [m["nba_player_id"] for m in matched]
    espn_ids = [m["espn_athlete_id"] for m in matched]
    if len(set(ids)) != len(ids) or len(set(espn_ids)) != len(espn_ids):
        print("\nWARNING: duplicate ids in the output -- do not use this file")
        return 1

    if args.dry_run:
        print("\ndry run, nothing written")
        return 0

    with open(args.out, "w") as handle:
        json.dump(matched, handle, separators=(",", ":"))
    print(f"\nwritten: {args.out}  ({len(matched)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
