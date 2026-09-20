"""How good is a projection built from last season? Measured at last.

WHY THIS EXISTS
Until a player has five games in the current season, the app builds
his whole baseline from last season's log
(engine/game_log.resolve_season_gamelog). That path has never been
backtested. The accuracy research flagged the gap in September 2026 --
"the early-season path has no backtest coverage at all" -- and it is
structural rather than an oversight: build_backtest_population.py
requires MIN_BASELINE_GAMES prior IN-SEASON games before a player
enters the set, so by construction it contains none of these cases.

That mattered enough to fix because a judgment was shipped on top of
the gap. engine/confidence.py now deducts 15 points when the baseline
comes from a finished season, and 15 was chosen to match the file's
other untested-input deductions -- a defensible guess, and still a
guess. This measures the thing so the guess can be replaced.

It is also the most time-sensitive measurement available: the path
goes live on opening night and governs every projection on the site
for the first fortnight of a season, which is when the most people see
it for the first time.

WHAT IT MEASURES
For each cached season that has a season before it (2024-25 and
2025-26; 2023-24 has no predecessor in the cache):

  EARLY   a player's first games of the season, projected from the
          PREVIOUS season's full log -- exactly what the app does,
          through the same stats_from_gamelog seam.
  MATURE  the same players' later games, projected from their
          in-season log, which is what the app does once it can.

Both are scored against what the player actually did, so the question
"how much worse is last season's baseline" has an answer in the same
units as everything else here.

TRANSLATING AN ERROR INTO RUBRIC POINTS
A ratio of MAEs does not convert into confidence points on its own.
So this also measures the in-season error CURVE -- accuracy as a
function of how many prior games the baseline has -- and reads the
prior-season baseline against it: "as accurate as an in-season
baseline of about N games".

That lands the answer in the rubric's own currency, because the rubric
already bands on exactly that number: 80 points at 20+ games, 55 at
5-19, 20 below 5. If last season's log performs like an in-season
baseline of eight games, it belongs in the 55 band, and the deduction
that puts it there is 25 rather than 15.

WHAT IT FOUND (20 Sep 2026)
3,801 early-season player-games across 2024-25 and 2025-26, against
8,487 mature ones.

UNPAIRED, last season's baseline looks 5.8% worse on pooled MAE, CI
[1.038, 1.085]. That number is wrong, and the curve underneath it says
so: in-season error RISES with the number of prior games (1.42 at 5-9,
1.54 at 35-60), which is not the baseline decaying. MAE is
unnormalised, players who reach fifty games are higher-usage players,
and a bigger number carries a bigger absolute error. The early and
mature rows are different populations.

PAIRED within player-season -- the same man, the same season, his
first games against his later ones -- the effect nearly disappears:

    median ratio            1.0301
    95% CI                  [0.9978, 1.0592]
    worse for               53.2% of 742 players

Three percent, and an interval that includes no difference at all.
Barely more than half of players were worse off. And this is an upper
bound on the baseline's contribution, because games 1-5 of a season
are intrinsically harder to predict -- rotations unsettled, minutes
unstable -- so some of even that 3% belongs to the games rather than
to last season's log.

WHAT IT CHANGED
engine/confidence.py deducted 15 for a prior-season baseline, chosen
to match the file's other untested-input deductions. The measurement
does not support it. Fifteen is the deduction for "an untested
estimate" and is enough to move a projection from High to Medium; a
three-percent effect that may be zero is not that.

It is now 5 -- the same as the file's existing deduction for the
defence layer using last season's ratings, which is the closest
comparable and now an anchored choice rather than an invented one. A
plain early-season projection scores 75 and says High, while the card
still states plainly that the number is built from last season.

Which is the right combination: say what the number is made of, and do
not overstate how much it costs.

Usage:
    python3 early_season_sweep.py
"""

import re
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, ".")

from engine.actuals import parse_game_date                        # noqa: E402
from engine.baseline_stats import stats_from_gamelog              # noqa: E402
from engine.cache import ARCHIVE                                  # noqa: E402
from engine.stat_columns import STAT_COLUMNS                      # noqa: E402

STATS = [col for col, _label in STAT_COLUMNS]

# The app's own threshold: below this many current-season games it
# falls back to last season. Kept as a name so the two cannot drift.
FALLBACK_BELOW_GAMES = 5

# Buckets of "how many prior in-season games does this baseline have",
# chosen to straddle the rubric's own bands (5 and 20).
PRIOR_BUCKETS = [(5, 9), (10, 19), (20, 34), (35, 60)]

# The mature side is only a ruler, and building a baseline for every
# prefix of every player-season is quadratic -- about 105,000 calls
# through stats_from_gamelog, which takes longer than the measurement
# is worth. Sampling evenly spaced games per player-season gives the
# same curve for a fraction of the work.
#
# Deliberately NOT applied to the early side: those rows are the thing
# being measured, and every one of them goes through the real seam.
MATURE_SAMPLES_PER_PLAYER = 10

BOOTSTRAP_ROUNDS = 300
RNG = np.random.default_rng(20260920)


def cached_logs():
    """{season: {player_id: DataFrame}}, oldest game first."""
    pattern = re.compile(r"^gamelog_(\d+)_(\d{4}-\d{2})\.json$")
    out = defaultdict(dict)
    for name in ARCHIVE.names():
        match = pattern.match(name)
        if not match:
            continue
        payload = ARCHIVE.read_json(name) or {}
        rows = payload.get("data") or []
        if not rows:
            continue
        frame = pd.DataFrame(rows)
        if "GAME_DATE" not in frame.columns:
            continue
        frame["_date"] = frame["GAME_DATE"].map(parse_game_date)
        frame = frame.dropna(subset=["_date"]).sort_values("_date")
        if frame.empty:
            continue
        out[match.group(2)][match.group(1)] = frame.reset_index(drop=True)
    return out


def previous(season):
    start = int(season.split("-")[0])
    return f"{start - 1}-{str(start)[-2:]}"


def rows_for(logs):
    """Every scoreable case, early and mature, as one long table."""
    records = []
    for season, by_player in logs.items():
        prior_season = previous(season)
        if prior_season not in logs:
            continue
        for player_id, frame in by_player.items():
            prior_frame = logs[prior_season].get(player_id)
            if prior_frame is None or len(prior_frame) < 10:
                continue

            # What the app would project before this season has five
            # games of its own: last season's whole log.
            prior_stats, prior_n = stats_from_gamelog(prior_frame)

            mature_indices = set()
            tail = range(FALLBACK_BELOW_GAMES, len(frame))
            if tail:
                step = max(1, len(tail) // MATURE_SAMPLES_PER_PLAYER)
                mature_indices = set(list(tail)[::step])

            for index in range(len(frame)):
                if index >= FALLBACK_BELOW_GAMES and index not in mature_indices:
                    continue
                actual = frame.iloc[index]
                in_season_prior = index          # games already played this season
                early = in_season_prior < FALLBACK_BELOW_GAMES

                if early:
                    stats, n_behind = prior_stats, prior_n
                    kind = "early"
                else:
                    stats, n_behind = stats_from_gamelog(frame.iloc[:index])
                    kind = "mature"

                row = {"season": season, "player_id": player_id,
                       "kind": kind, "n_behind": int(n_behind),
                       "in_season_prior": in_season_prior}
                keep = False
                for col in STATS:
                    if col not in actual:
                        continue
                    predicted = stats.get(col, (np.nan, np.nan))[0]
                    truth = actual[col]
                    if pd.isna(predicted) or pd.isna(truth):
                        continue
                    row[f"{col}_pred"] = float(predicted)
                    row[f"{col}_actual"] = float(truth)
                    keep = True
                if keep:
                    records.append(row)
    return pd.DataFrame(records)


def pooled_mae(frame):
    """Mean over the nine stats of each stat's MAE, and the per-stat table."""
    per_stat = {}
    for col in STATS:
        pred, actual = f"{col}_pred", f"{col}_actual"
        if pred not in frame.columns:
            continue
        subset = frame[[pred, actual]].dropna()
        if subset.empty:
            continue
        per_stat[col] = float((subset[pred] - subset[actual]).abs().mean())
    return (float(np.mean(list(per_stat.values()))) if per_stat else np.nan), per_stat


def paired_ratio(frame):
    """Early vs mature error WITHIN each player-season.

    The unpaired comparison is confounded and the first run of this
    showed it plainly: pooled MAE rose with the number of prior games
    (1.42 at 5-9, 1.54 at 35-60), which is not the baseline getting
    worse. It is composition. MAE is unnormalised, players who reach
    fifty games are higher-usage players, and a bigger number carries
    a bigger absolute error.

    So the mature rows and the early rows are different populations,
    and dividing one by the other measures the mix as much as the
    method. Pairing inside a player-season removes it: the same man,
    the same season, his first games against his later ones.
    """
    rows = []
    for (season, player_id), group in frame.groupby(["season", "player_id"]):
        early = group[group["kind"] == "early"]
        mature = group[group["kind"] == "mature"]
        if len(early) < 3 or len(mature) < 3:
            continue
        early_mae, _ = pooled_mae(early)
        mature_mae, _ = pooled_mae(mature)
        if not mature_mae or np.isnan(early_mae) or np.isnan(mature_mae):
            continue
        rows.append({"season": season, "player_id": player_id,
                     "early": early_mae, "mature": mature_mae,
                     "ratio": early_mae / mature_mae})
    return pd.DataFrame(rows)


def relative_curve(mature):
    """In-season MAE by how many prior games the baseline had.

    This is the ruler the prior-season baseline gets held against.
    """
    curve = []
    for low, high in PRIOR_BUCKETS:
        bucket = mature[(mature["n_behind"] >= low) & (mature["n_behind"] <= high)]
        if len(bucket) < 200:
            continue
        pooled, _per_stat = pooled_mae(bucket)
        curve.append({"low": low, "high": high, "n": len(bucket), "pooled_mae": pooled})
    return curve


def bootstrap_ratio(early, reference, rounds=BOOTSTRAP_ROUNDS):
    """Cluster bootstrap over players for early/reference pooled MAE."""
    def keys(frame):
        return (frame["player_id"].astype(str) + "|" + frame["season"].astype(str)).to_numpy()

    early_keys, ref_keys = keys(early), keys(reference)
    early_index = {k: np.where(early_keys == k)[0] for k in np.unique(early_keys)}
    ref_index = {k: np.where(ref_keys == k)[0] for k in np.unique(ref_keys)}
    shared = sorted(set(early_index) & set(ref_index))
    if not shared:
        return (np.nan, np.nan)
    values = []
    for _ in range(rounds):
        drawn = RNG.choice(shared, size=len(shared), replace=True)
        e = early.iloc[np.concatenate([early_index[k] for k in drawn])]
        r = reference.iloc[np.concatenate([ref_index[k] for k in drawn])]
        e_mae, _ = pooled_mae(e)
        r_mae, _ = pooled_mae(r)
        if r_mae:
            values.append(e_mae / r_mae)
    return tuple(np.nanpercentile(values, [2.5, 97.5])) if values else (np.nan, np.nan)


def main():
    print("reading cached game logs ...")
    logs = cached_logs()
    print("  seasons: " + ", ".join(f"{s} ({len(p)} players)"
                                    for s, p in sorted(logs.items())))

    frame = rows_for(logs)
    if frame.empty:
        print("nothing scoreable -- are the game logs cached?")
        return 1

    early = frame[frame["kind"] == "early"]
    mature = frame[frame["kind"] == "mature"]
    print(f"\n{len(early):,} early-season player-games "
          f"(the app's fallback path, projected from last season)")
    print(f"{len(mature):,} mature player-games (projected in-season)")

    early_mae, early_per_stat = pooled_mae(early)
    mature_mae, mature_per_stat = pooled_mae(mature)

    print(f"\n{'stat':6s} {'last season':>12s} {'in-season':>10s} {'ratio':>7s}")
    for col in STATS:
        if col in early_per_stat and col in mature_per_stat:
            ratio = early_per_stat[col] / mature_per_stat[col]
            print(f"{col:6s} {early_per_stat[col]:12.3f} "
                  f"{mature_per_stat[col]:10.3f} {ratio:7.3f}")
    print(f"\npooled: last season {early_mae:.4f}  in-season {mature_mae:.4f}  "
          f"ratio {early_mae / mature_mae:.4f}")

    low, high = bootstrap_ratio(early, mature)
    print(f"95% CI on the ratio (cluster bootstrap over players): "
          f"[{low:.4f}, {high:.4f}]")

    paired = paired_ratio(frame)
    print(f"\nPAIRED, within player-season ({len(paired):,} players with both):")
    if not paired.empty:
        print(f"  median ratio {paired['ratio'].median():.4f}   "
              f"mean {paired['ratio'].mean():.4f}")
        worse = float((paired["ratio"] > 1).mean())
        print(f"  last season's baseline was worse for {100 * worse:.1f}% of them")
        boot = [paired["ratio"].sample(len(paired), replace=True,
                                       random_state=seed).median()
                for seed in range(BOOTSTRAP_ROUNDS)]
        low_p, high_p = np.percentile(boot, [2.5, 97.5])
        print(f"  95% CI on the median ratio: [{low_p:.4f}, {high_p:.4f}]")

    print("\nin-season error by how many prior games the baseline had:")
    curve = relative_curve(mature)
    for point in curve:
        print(f"  {point['low']:2d}-{point['high']:<2d} games  "
              f"n={point['n']:6,d}  pooled MAE {point['pooled_mae']:.4f}")

    print("  (this curve is CONFOUNDED by composition -- see paired_ratio --")
    print("   and is printed for the record rather than used as a ruler.)")

    print(f"\nlast season's log scores {early_mae:.4f}.")
    equivalent = None
    for point in curve:
        if early_mae <= point["pooled_mae"]:
            equivalent = point
            break
    if equivalent:
        print(f"  -> as accurate as an in-season baseline of "
              f"{equivalent['low']}-{equivalent['high']} games.")
    elif curve:
        print(f"  -> worse than an in-season baseline of any size measured here "
              f"(best bucket {curve[-1]['pooled_mae']:.4f}).")

    print("\nWhat that means for engine/confidence.py's rubric, which bands on")
    print("exactly this number -- 80 points at 20+ prior games, 55 at 5-19,")
    print("20 below 5. The deduction should put a prior-season baseline in")
    print("whichever band it actually performs like.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
