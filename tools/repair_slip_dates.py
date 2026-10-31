#!/usr/bin/env python3
"""Fill a saved slip's missing us_date from the players' own game logs.

    python3 tools/repair_slip_dates.py --dry-run
    python3 tools/repair_slip_dates.py

WHY THIS EXISTS
A slip is parsed once and then SAVED to journal/picks/<id>.json. The
card you look at renders from that file. So when the parser could not
read a game date, the null was written to disk and stayed there: fixing
the parser afterwards does nothing for slips already saved, and every
leg goes on reporting "Couldn't read this leg fully, so it can't be
checked" because slip_check needs pid, line AND us_date.

Rather than ask anyone to re-enter a slip, the date is recovered from
the slip's own timestamp. A slip is placed in Sydney hours before the
games tip, and Sportsbet's own slate is the US night: converting
placed_at to New York gives that night directly, which is the same
conversion the parser does for a game row it CAN read.

The first attempt looked for the player's nearest game in his log
instead, and got it wrong in a way worth recording: a bet placed on a
game that has not been played yet has no log entry for that night, so
"nearest game" quietly returned the PREVIOUS one -- 29 Oct for a 31 Oct
slip. Evidence that cannot exist yet is not evidence.

Where the game HAS since been played, the log confirms the date and
supplies the matchup. Where it has not, the date stands on the
timestamp alone and is marked so.
"""
import argparse
import datetime
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import picks as pk                                            # noqa: E402

PICKS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "journal", "picks")


def season_for(day):
    return f"{day.year}-{str(day.year + 1)[2:]}" if day.month >= 9 else \
           f"{day.year - 1}-{str(day.year)[2:]}"


def played_on(pid, day):
    """(True, matchup) if this player has a log row for `day`."""
    for season in {season_for(day)}:
        frame = pk._log(pid, season)
        if frame is None or frame.empty or "_d" not in frame:
            continue
        for _, row in frame.iterrows():
            d = row["_d"]
            if isinstance(d, datetime.datetime):
                d = d.date()
            if d == day:
                return True, str(row.get("MATCHUP", ""))
    return False, ""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    fixed = files = skipped = 0
    for path in sorted(glob.glob(os.path.join(PICKS_DIR, "*.json"))):
        slip = json.load(open(path))
        legs = slip.get("legs") or []
        if not any(leg.get("us_date") is None for leg in legs):
            continue
        stamp = slip.get("placed_at") or slip.get("saved_at")
        if not stamp:
            print(f"{os.path.basename(path)}: no timestamp, left alone")
            continue
        placed = datetime.datetime.fromisoformat(stamp)
        if placed.tzinfo is None:
            placed = placed.replace(tzinfo=pk.SYDNEY)
        night = placed.astimezone(pk.NEW_YORK).date()
        changed = False
        for leg in legs:
            if leg.get("us_date") or not leg.get("pid"):
                continue
            leg["us_date"] = night.isoformat()
            seen, matchup = played_on(int(leg["pid"]), night)
            if seen and matchup and not leg.get("game"):
                leg["game"] = matchup.replace(" vs. ", "@").replace(" @ ", "@").strip()
            leg["needs_review"] = [m for m in (leg.get("needs_review") or [])
                                   if m != "game date not read"]
            mark = "confirmed in the log" if seen else "not played yet, from the timestamp"
            print(f"  {leg.get('player'):20s} -> {night.isoformat()}  "
                  f"{leg.get('game') or '':10s} ({mark})")
            fixed += 1
            changed = True
        if changed:
            files += 1
            if not args.dry_run:
                with open(path, "w") as handle:
                    json.dump(slip, handle, indent=1)
    verb = "would fix" if args.dry_run else "fixed"
    print(f"\n{verb} {fixed} leg(s) across {files} slip(s); {skipped} left alone")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
