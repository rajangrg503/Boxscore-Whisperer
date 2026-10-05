"""Does a player's own POINTS-PER-SHOT predict his points better than his
own POINTS-PER-MINUTE does? -- the measurement behind the proposed shots
control.

WHY THIS EXISTS
On 4 Oct 2026 the model missed two lines badly in one night: Hachimura
6.5 projected against 21 actual, Jokic 12.0 against 3. Karma had called
both beforehand, and when asked what he meant said:

    "when i said hachimura will have bigger role, i mean he's going to
    shoot more. more fg attempt. in lakers he played minutes but didnt
    get much shot up"

Both reads were about SHOT VOLUME, and the app had nowhere to put
either. It has a minutes control -- the reader sets minutes and every
row scales off measured per-minute rates -- and nothing below it.

Neither miss was reachable by picking different games to average. Both
shot rates were outside the player's own season entirely: Hachimura took
0.724 FGA/min against a personal high of 0.500 across 78 games, Jokic
0.070 against a personal low well above it. A "project him from his own
high-usage games" baseline was tried and measured first: it returns 7.5
points for Hachimura, against a model line of 6.5 and an actual of 21.
You cannot subset your way to a behaviour the sample never contained.

WHAT IT COMPARES
Both arms get exactly one piece of same-night information, because that
is the real question -- today the reader supplies minutes, and the
proposal is that he supply shots instead:

    A (today's control)   prior PTS/MIN  x  actual MIN
    B (proposed control)  prior PTS/FGA  x  actual FGA

Strictly point-in-time: game i is predicted from games 0..i-1 of that
player's own season. No lookahead, no cross-season leakage. That
discipline is the same one engine/backtest_point_in_time.py keeps, and
it is the only reason these numbers mean anything.

WHY IT ALSO ADDS NOISE
Arm B above is handed the player's EXACT shot count, which nobody has
before tip-off. A reader would be guessing, so the second half of the
run gives A the exact minutes and gives B a deliberately wrong shot
count, then finds the point where B stops being worth having. A is
advantaged throughout. Measured 5 Oct 2026 over 62,604 player-games:

    exact        +22.0%      +/-3 shots   +5.2%
    +/-1 shot    +18.7%      +/-4 shots   -4.0%   <- break-even here
    +/-2 shots   +13.0%      +/-5 shots  -14.1%

and the asymmetry that is the actual design finding: the minutes control
loses only 5% when the minutes guess is six minutes wrong. Minutes is
blunt but forgiving; shots is sharp but brittle. That is a
specification, not a defect -- shots belongs on the card as an addition
for a reader with a specific read, never as a default, and the app
should push back when the number is far outside the player's own rate.

Points only. Minutes drives every row on the card; shots drives points
and the shooting stats. Rebounds and assists still need minutes.
"""

import argparse
import glob
import json
import os
import random
import statistics


MIN_PRIOR = 10
DEFAULT_CACHE = "data_cache"


def walk_forward(games, min_prior=MIN_PRIOR):
    """Yield (pts_per_min, pts_per_fga, min, fga, pts) per scorable game.

    The running totals are updated AFTER each game is emitted, so the
    rates handed out for game i contain games 0..i-1 and nothing else.
    Getting this backwards is the classic way to produce a backtest that
    looks excellent and means nothing, so the ordering here is the point
    of the function.

    Games the player did not appear in carry no rates and no target, and
    are dropped rather than counted as zeros -- a DNP is not a
    performance.
    """
    played = [g for g in games
              if g.get("MIN") and g["MIN"] > 0 and g.get("FGA") is not None]
    played.sort(key=lambda g: g.get("GAME_DATE", ""))

    cum_min = cum_fga = cum_pts = 0.0
    for i, g in enumerate(played):
        if i >= min_prior and cum_min > 0 and cum_fga > 0:
            yield (cum_pts / cum_min, cum_pts / cum_fga,
                   g["MIN"], g["FGA"], g["PTS"])
        cum_min += g["MIN"]
        cum_fga += g["FGA"]
        cum_pts += g["PTS"]


def mae_minutes(records):
    """Arm A: his own points-per-minute times the minutes he played."""
    return statistics.mean(
        abs(ppm * mins - pts) for ppm, _, mins, _, pts in records)


def mae_shots(records, noise=0, rng=None):
    """Arm B: his own points-per-shot times the shots he took, with the
    shot count optionally knocked off by up to `noise` either way.

    noise=0 is the oracle version and is not the honest one; it is kept
    because without it there is nothing for the noisy runs to decay from.
    A negative shot count is impossible, so the floor is zero.
    """
    rng = rng or random.Random(7)
    total = 0.0
    for _, ppf, _, fga, pts in records:
        shots = max(0, fga + rng.randint(-noise, noise)) if noise else fga
        total += abs(ppf * shots - pts)
    return total / len(records)


def load(cache_dir=DEFAULT_CACHE):
    """Every cached gamelog, as (player_id, season, rows)."""
    for path in sorted(glob.glob(os.path.join(cache_dir, "gamelog_*.json"))):
        try:
            payload = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows = payload.get("data") if isinstance(payload, dict) else payload
        if isinstance(rows, list) and rows:
            stem = os.path.basename(path)[len("gamelog_"):-len(".json")]
            yield stem, rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    ap.add_argument("--min-prior", type=int, default=MIN_PRIOR)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    records, players = [], 0
    for _, rows in load(args.cache):
        got = list(walk_forward(rows, args.min_prior))
        if got:
            players += 1
            records.extend(got)

    if not records:
        print(f"no gamelogs under {args.cache}/ -- is the cache unpacked?")
        return

    a = mae_minutes(records)
    b = mae_shots(records)
    print(f"{players:,} player-seasons   {len(records):,} player-games "
          f"(>= {args.min_prior} prior games, same season)\n")
    print(f"  A  prior PTS/MIN x actual MIN :  MAE {a:.3f}")
    print(f"  B  prior PTS/FGA x actual FGA :  MAE {b:.3f}"
          f"   ({(a - b) / a * 100:+.1f}%)\n")

    print("  B with the shot guess deliberately wrong:")
    for k in range(1, 7):
        noisy = mae_shots(records, noise=k, rng=random.Random(args.seed))
        gap = (a - noisy) / a * 100
        print(f"    +/-{k} shots   MAE {noisy:.3f}   ({gap:+5.1f}% vs A)"
              f"   {'beats A' if noisy < a else 'WORSE than A'}")

    print("\n  For contrast, A with the minutes guess wrong:")
    rng = random.Random(args.seed)
    for k in (2, 4, 6):
        noisy = statistics.mean(
            abs(ppm * max(0.0, mins + rng.uniform(-k, k)) - pts)
            for ppm, _, mins, _, pts in records)
        print(f"    +/-{k} min      MAE {noisy:.3f}   ({(a - noisy) / a * 100:+5.1f}% vs A)")


if __name__ == "__main__":
    main()
