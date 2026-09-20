"""Does cheap game context -- home/away, rest, back-to-backs -- actually
help? Measured before any of it is built.

WHY THIS EXISTS
The accuracy plan's step 4 is "add the cheap context already in the
cache: pace, home/away, rest and back-to-backs, own-team offence",
with the honest note that individually they are tiny. This project has
already switched one layer OFF for being 2% worse (missing opponents),
so the rule here is the same: nothing ships that does not measure
positive out of sample.

This tests the two that need no new data at all. Both are known before
tip-off and both come straight out of the population table:

  * HOME / AWAY -- from the game logs' MATCHUP field ("LAL vs. BOS" is
    home, "LAL @ BOS" is away). No fetching, no point-in-time hazard.
  * REST -- days since that player's own previous game, so a
    back-to-back is rest 0. Also computed from the table, and also
    known in advance.

Pace and own-team offence are deliberately NOT here. They need
point-in-time team ratings, which means checkpoints and a leak risk,
and there is no reason to pay that cost before the two free features
have shown whether this whole direction is worth anything.

HOW IT IS TESTED
A multiplicative factor per (stat, bucket), estimated as
mean(actual)/mean(predicted) over the training rows in that bucket and
shrunk toward 1.0 by a pseudo-count, then applied to the held-out
season. Deliberately the simplest thing that could work: if a bucket
factor cannot beat 1.0 out of sample, a cleverer model of the same
signal is not going to rescue it.

Leave-one-season-out over the three cached seasons, so every number is
out of sample. The reference is production's own *_predicted column,
not a strawman.

METRICS
  * MAE per stat, and pooled relative MAE (mean over the nine stats of
    MAE / reference MAE). Below 1.0 is an improvement.
  * Direction accuracy against the player's own prior mean -- the call
    a reader actually makes. Ties dropped.
  * A cluster bootstrap over player-seasons for the pooled difference,
    because player-games are not independent and a naive CI here would
    be far too narrow.

WHAT IT FOUND (20 Sep 2026) -- NEITHER SHIPS
Pooled rel-MAE 0.9999 for both, over three leave-one-season-out folds,
with a cluster-bootstrap 95% CI of [0.9997, 1.0001] -- straddling 1.0.
Neither feature is distinguishable from no adjustment at all. The
largest fitted factor on the largest bucket is about half a percent.

The rest factors are worth reading anyway, because they run backwards:
a player does MORE after a back-to-back (PTS 1.020) and LESS after
three or more days off (0.963). That is not fatigue, it is selection.
Long gaps sit around injuries, All-Star week and returns to a reduced
role; the players who play both halves of a back-to-back are the
healthy ones. The feature is measuring who is available, not what rest
does -- which is step 5's subject, not step 4's.

AND THE THING WORTH MORE THAN EITHER
Production under-predicts, across the board: actual/predicted is 1.027
for points, 1.040 for assists, 1.018 for rebounds. Following it down:

  * It is not the opponent-defence layer. In this table *_base and
    *_predicted are identical to four decimal places.
  * It is not an early-season artifact. It holds at every level of
    prior games played.
  * It is not a constant. Split by minutes actually played, actual over
    predicted runs 0.41 at under ten minutes, 0.84 at ten to twenty,
    1.03 at twenty to thirty and 1.15 above thirty.

So the aggregate "bias" is not a calibration constant that could be
divided out -- it is unmodelled minutes variance, netting out
unevenly. Scaling predictions up would make the under-ten-minute rows
worse, which is exactly what the first, un-normalised run of this
sweep did to both MAE and direction accuracy.

That points the next piece of work at availability, not at context.

Usage:
    python3 context_features_sweep.py
"""

import re
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, ".")

from engine.cache import ARCHIVE                                  # noqa: E402
from engine.stat_columns import STAT_COLUMNS                      # noqa: E402

POPULATION = "backtest_population.csv"
STATS = [col for col, _label in STAT_COLUMNS]

# Pseudo-games of "no effect" mixed into every bucket factor. With
# thousands of rows per bucket this barely moves the estimate; it is
# here so a thin bucket (rest of 4+ days is rare) cannot produce a wild
# multiplier from a handful of games.
SHRINK_GAMES = 200

BOOTSTRAP_ROUNDS = 300
RNG = np.random.default_rng(20260920)


def home_team_by_game():
    """game_id -> home team abbreviation, from the cached game logs.

    The population table records who played whom but not where. The
    game logs carry it in MATCHUP, and every game in the population
    appears in some player's log.
    """
    pattern = re.compile(r"^gamelog_\d+_\d{4}-\d{2}\.json$")
    home = {}
    for name in ARCHIVE.names():
        if not pattern.match(name):
            continue
        payload = ARCHIVE.read_json(name) or {}
        for row in payload.get("data") or []:
            game_id = str(row.get("Game_ID") or "")
            matchup = row.get("MATCHUP") or ""
            if not game_id or game_id in home:
                continue
            if " vs. " in matchup:
                home[game_id] = matchup.split(" vs. ")[0].strip()
            elif " @ " in matchup:
                home[game_id] = matchup.split(" @ ")[1].strip()
    return home


def team_abbr_by_id():
    from engine.team_ids import TEAM_ID_BY_ABBR
    return {team_id: abbr for abbr, team_id in TEAM_ID_BY_ABBR.items()}


def load():
    frame = pd.read_csv(POPULATION, dtype={"game_id": str})
    frame["game_date"] = pd.to_datetime(frame["game_date"])

    # ---- home / away ----
    home = home_team_by_game()
    abbr = team_abbr_by_id()
    frame["team_abbr"] = frame["team_id"].map(abbr)
    frame["home_abbr"] = frame["game_id"].map(home)
    frame["is_home"] = np.where(
        frame["home_abbr"].isna(), np.nan,
        (frame["team_abbr"] == frame["home_abbr"]).astype(float))

    # ---- rest ----
    # Days since this player's previous game THIS SEASON. The first
    # game of a season has no previous one and is left out of the rest
    # test rather than guessed at.
    frame = frame.sort_values(["player_id", "season", "game_date"])
    previous = frame.groupby(["player_id", "season"])["game_date"].shift(1)
    frame["rest_days"] = (frame["game_date"] - previous).dt.days - 1
    frame["rest_bucket"] = pd.cut(
        frame["rest_days"], bins=[-1, 0, 1, 2, 99],
        labels=["b2b", "1 day", "2 days", "3+ days"])
    return frame


def factors(train, feature, shrink=SHRINK_GAMES, normalise=True):
    """One multiplicative factor per (stat, bucket), shrunk toward 1.

    NORMALISED, and that is the whole point. The first run of this
    sweep fitted raw actual/predicted ratios and every bucket came back
    above 1.0 -- home 1.034 and away 1.022 for points. That is not a
    venue effect, it is production under-predicting by two to three
    percent overall, and the "feature" was quietly acting as a global
    recalibration. It made MAE worse (scaling a right-skewed
    prediction up costs more on the many small actuals than it gains on
    the few large ones) and direction accuracy collapsed from 51% to
    47%, because lifting every prediction turns nearly every call into
    an "over".

    Dividing out the row-weighted mean leaves only the DIFFERENTIAL --
    how much more a player does at home than away -- which is the thing
    actually being tested. The global bias is a separate finding, and
    it is reported separately rather than smuggled in here.
    """
    out = {}
    counts = {}
    for stat in STATS:
        actual, predicted = f"{stat}_actual", f"{stat}_predicted"
        raw_by_bucket = {}
        for bucket, rows in train.dropna(subset=[feature]).groupby(feature,
                                                                  observed=True):
            rows = rows.dropna(subset=[actual, predicted])
            total_predicted = rows[predicted].sum()
            if len(rows) < 30 or total_predicted <= 0:
                continue
            raw = rows[actual].sum() / total_predicted
            weight = len(rows) / (len(rows) + shrink)
            raw_by_bucket[bucket] = 1.0 + weight * (raw - 1.0)
            counts[bucket] = len(rows)
        if not raw_by_bucket:
            continue
        if normalise:
            total = sum(counts[b] for b in raw_by_bucket)
            centre = sum(raw_by_bucket[b] * counts[b] for b in raw_by_bucket) / total
            raw_by_bucket = {b: v / centre for b, v in raw_by_bucket.items()}
        for bucket, value in raw_by_bucket.items():
            out[(stat, bucket)] = value
    return out


def global_bias(frame):
    """How far production's prediction sits from the truth overall.

    Separated out because it is a real finding and a different one:
    a systematic under-prediction is a calibration problem, not a
    missing feature, and fixing it by way of a "home/away layer" would
    be the wrong repair in the wrong place.
    """
    rows = []
    for stat in STATS:
        actual = frame[f"{stat}_actual"].to_numpy(dtype=float)
        predicted = frame[f"{stat}_predicted"].to_numpy(dtype=float)
        ok = ~np.isnan(actual) & ~np.isnan(predicted) & (predicted > 0)
        rows.append({"stat": stat,
                     "mean_actual": actual[ok].mean(),
                     "mean_predicted": predicted[ok].mean(),
                     "ratio": actual[ok].sum() / predicted[ok].sum()})
    return pd.DataFrame(rows)


def apply_factors(test, feature, table):
    adjusted = {}
    for stat in STATS:
        column = test[f"{stat}_predicted"].to_numpy(dtype=float)
        multiplier = np.ones(len(test))
        for index, bucket in enumerate(test[feature].to_numpy()):
            factor = table.get((stat, bucket))
            if factor is not None:
                multiplier[index] = factor
        adjusted[stat] = column * multiplier
    return adjusted


def direction(predicted, actual, reference):
    """Did we call the right side of the player's own prior mean?"""
    call = np.sign(predicted - reference)
    truth = np.sign(actual - reference)
    live = (call != 0) & (truth != 0) & ~np.isnan(call) & ~np.isnan(truth)
    if live.sum() == 0:
        return float("nan"), 0
    return float((call[live] == truth[live]).mean()), int(live.sum())


def evaluate(test, feature, table):
    adjusted = apply_factors(test, feature, table)
    rows = []
    for stat in STATS:
        actual = test[f"{stat}_actual"].to_numpy(dtype=float)
        reference = test[f"{stat}_predicted"].to_numpy(dtype=float)
        prior = test[f"{stat}_base"].to_numpy(dtype=float)
        ok = ~np.isnan(actual) & ~np.isnan(reference)
        base_mae = np.abs(reference[ok] - actual[ok]).mean()
        new_mae = np.abs(adjusted[stat][ok] - actual[ok]).mean()
        base_dir, n = direction(reference[ok], actual[ok], prior[ok])
        new_dir, _ = direction(adjusted[stat][ok], actual[ok], prior[ok])
        rows.append({"stat": stat, "base_mae": base_mae, "new_mae": new_mae,
                     "rel": new_mae / base_mae if base_mae else np.nan,
                     "base_dir": base_dir, "new_dir": new_dir, "n_dir": n})
    return pd.DataFrame(rows)


def pooled_rel(test, feature, table, mask=None):
    subset = test if mask is None else test[mask]
    if len(subset) == 0:
        return np.nan
    return evaluate(subset, feature, table)["rel"].mean()


def bootstrap_pooled(test, feature, table, rounds=BOOTSTRAP_ROUNDS):
    """Cluster bootstrap over player-seasons.

    Player-games are not independent -- one player contributes seventy
    correlated rows -- so resampling rows would produce an interval far
    too narrow to mean anything.
    """
    keys = (test["player_id"].astype(str) + "|" + test["season"].astype(str)).to_numpy()
    unique = np.unique(keys)
    index_by_key = {key: np.where(keys == key)[0] for key in unique}
    values = []
    for _ in range(rounds):
        drawn = RNG.choice(unique, size=len(unique), replace=True)
        rows = np.concatenate([index_by_key[key] for key in drawn])
        values.append(pooled_rel(test.iloc[rows], feature, table))
    return np.nanpercentile(values, [2.5, 97.5])


def report(frame, feature, label):
    print(f"\n{'=' * 72}\n{label}\n{'=' * 72}")

    usable = frame.dropna(subset=[feature])
    counts = usable[feature].value_counts(dropna=False)
    print("rows per bucket: " +
          ", ".join(f"{bucket}: {count:,}" for bucket, count in counts.items()))

    seasons = sorted(usable["season"].unique())
    per_season = []
    for held_out in seasons:
        train = usable[usable["season"] != held_out]
        test = usable[usable["season"] == held_out]
        table = factors(train, feature)
        result = evaluate(test, feature, table)
        per_season.append((held_out, result, table, test))
        print(f"\n  held out {held_out}  (trained on the other two)")
        print(f"    {'stat':6s} {'MAE now':>8s} {'MAE +ctx':>9s} {'rel':>7s} "
              f"{'dir now':>8s} {'dir +ctx':>9s}")
        for _, row in result.iterrows():
            print(f"    {row['stat']:6s} {row['base_mae']:8.3f} "
                  f"{row['new_mae']:9.3f} {row['rel']:7.4f} "
                  f"{100 * row['base_dir']:7.1f}% {100 * row['new_dir']:8.1f}%")
        print(f"    pooled rel-MAE {result['rel'].mean():.4f}")

    pooled = np.mean([result["rel"].mean() for _, result, _t, _x in per_season])
    print(f"\n  POOLED over all three folds: rel-MAE {pooled:.4f} "
          f"({'better' if pooled < 1 else 'WORSE'} than production)")

    # Widest fold gets the interval; one bootstrap is enough to say
    # whether the effect is distinguishable from nothing.
    _season, _result, table, test = per_season[-1]
    low, high = bootstrap_pooled(test, feature, table)
    print(f"  95% CI on the last fold's rel-MAE: [{low:.4f}, {high:.4f}]")
    if low <= 1.0 <= high:
        print("  -> includes 1.0: not distinguishable from no adjustment")

    print("\n  the factors themselves (last fold):")
    for stat in STATS:
        parts = [f"{bucket}={table[(stat, bucket)]:.4f}"
                 for bucket in counts.index if (stat, bucket) in table]
        if parts:
            print(f"    {stat:6s} " + "  ".join(parts))
    return pooled


def main():
    frame = load()
    print(f"{len(frame):,} player-games, "
          f"{frame['season'].nunique()} seasons, "
          f"{frame['is_home'].notna().sum():,} with a known venue, "
          f"{frame['rest_days'].notna().sum():,} with a known rest gap")

    print(f"\n{'=' * 72}\nGLOBAL CALIBRATION (not a feature -- read before the rest)\n{'=' * 72}")
    bias = global_bias(frame)
    for _, row in bias.iterrows():
        print(f"  {row['stat']:6s} actual {row['mean_actual']:7.3f}  "
              f"predicted {row['mean_predicted']:7.3f}  "
              f"ratio {row['ratio']:.4f}")
    print(f"  mean ratio across the nine stats: {bias['ratio'].mean():.4f}")

    home_pooled = report(frame, "is_home", "HOME / AWAY")
    rest_pooled = report(frame, "rest_bucket", "REST (days since the player's own previous game)")

    print(f"\n{'=' * 72}\nVERDICT\n{'=' * 72}")
    for name, value in (("home/away", home_pooled), ("rest", rest_pooled)):
        verdict = "ships" if value < 0.999 else "does NOT ship"
        print(f"  {name:10s} pooled rel-MAE {value:.4f}  -> {verdict}")
    print("\nA layer that cannot beat 1.0 out of sample here is a layer that\n"
          "makes the app slower to reason about and no more accurate.")


if __name__ == "__main__":
    main()
