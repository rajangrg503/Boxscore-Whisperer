"""Does knowing a player's recent availability help? Measured first.

WHY THIS EXISTS
Accuracy plan step 5 is availability, and two independent findings
point at it:

  * Split by minutes actually played, production's actual/predicted
    runs 0.41 under ten minutes and 1.15 above thirty
    (context_features_sweep.py). The error is not spread evenly -- it
    is concentrated in games where a player barely featured, and we
    had no way to see those coming.
  * The rest factors in that same sweep run BACKWARDS: more production
    after a back-to-back, less after three or more days off. That is
    not fatigue, it is availability leaking into a feature that was
    not meant to measure it.

THE CONSTRAINT THAT DECIDES THE SHAPE OF THIS
The obvious source is the NBA's own injury report. It is free and
public, but it is PDF-only and published daily -- so there is no
archive of what it said on a Tuesday in January 2024, which means a
feature built on it CANNOT BE BACKTESTED. This project's rule is that
nothing ships without measuring positive out of sample, and it has
already switched one layer off and declined two more on that basis.

So this measures the availability signal that IS in the cache and IS
point-in-time safe: what the player's own recent record says about
whether he is about to play a full game. If that is worth nothing, an
injury report is unlikely to rescue it and the legal work of getting
one is not worth starting. If it is worth something, that number is
also the floor an injury report would have to beat.

THE FEATURES, ALL STRICTLY PRIOR
  * missed_recent  -- how many of his team's last five games he did
    not appear in. The population only contains games a player played,
    so his absences are reconstructed from his team's game dates.
  * minutes_form   -- mean minutes over his last three appearances,
    divided by his season-to-date MPG. Below 1.0 is a shrinking role.
  * short_stint    -- how many of his last three appearances were
    under ten minutes.

Each is known before tip-off. None uses the target game.

WHAT IS MEASURED
1. Whether they predict the thing that actually hurts: a sub-ten-minute
   game. Reported as the rate of such games in each bucket, because a
   feature that cannot separate them is not an availability feature.
2. Whether a simple bucket adjustment beats production's own
   prediction out of sample -- same leave-one-season-out design,
   normalised factors and cluster bootstrap as
   context_features_sweep.py, so the two results are comparable.

WHAT IT FOUND (20 Sep 2026) -- NOTHING SHIPS, AND THAT IS THE ANSWER
The features work. They just have nowhere to go.

THEY SEPARATE, AND STRONGLY. Against a 14.3% base rate of
sub-ten-minute games:

    2-3 short stints in his last 3      61.8%   4.6x
    minutes form under 0.7              48.6%   3.6x
    missed 3+ of the team's last 5      40.7%   2.8x
    none of the above (52,298 rows)      3.9%   0.3x

That is a real, large signal about whether a player is about to
feature at all.

THEY DO NOTHING FOR THE PROJECTION. Best pooled rel-MAE 0.9998, CI
[0.9987, 0.9997]. Because the problem is not the mean: a player with
two short stints behind him has a 62% chance of another and a 38%
chance of a normal night. Shifting his average down five percent does
not describe that, and the numbers say so -- production actually
UNDER-predicts that bucket by 20% (actual/predicted 1.197), because
when those players do play they blow past a projection built from
their recent scraps.

AND THEY DO NOTHING FOR THE INTERVAL EITHER, which is where this was
expected to land. Coverage of the app's 80% range, by bucket:

    none short      78.8%      1 short    81.8%     2-3 short   84.8%
    form >1.1       78.4%      0.9-1.1    80.2%     under 0.7   88.5%
    missed none     81.7%      missed 2   79.9%     missed 3+   82.1%

Every bucket lands between 78% and 89% against a nominal 80%. The
interval is ALREADY doing this job: a player with erratic recent
minutes has a large prior spread, the distribution widens on its own,
and no availability feature is needed to make it happen. The
calibration work holds up under a stress test it was not designed for.

SO: NO AVAILABILITY LAYER, AND NO INJURY REPORT
If the strongest availability signal available cannot move the mean or
the range, an injury report is a better measurement of a quantity the
model has no use for. That saves the whole PDF-parsing and
terms-of-use exercise, which was the expensive part of step 5.

WHERE IT DOES BELONG: TELLING THE READER
"Under ten minutes in two of his last three -- 62% of players in that
spot go short again" is worth knowing to somebody deciding whether to
take a prop, even though it changes none of our numbers. It is a
measured base rate, not a projection, and this repository can publish
it honestly. That is a presentation change, not a model change.

Usage:
    python3 availability_sweep.py
"""

import sys

import numpy as np
import pandas as pd

sys.path.insert(0, ".")

from engine.stat_columns import STAT_COLUMNS                      # noqa: E402

POPULATION = "backtest_population.csv"
STATS = [col for col, _label in STAT_COLUMNS]

SHRINK_GAMES = 200
BOOTSTRAP_ROUNDS = 300
RNG = np.random.default_rng(20260920)

SHORT_STINT_MINUTES = 10.0
RECENT_TEAM_GAMES = 5
RECENT_APPEARANCES = 3


def load():
    frame = pd.read_csv(POPULATION, dtype={"game_id": str})
    frame["game_date"] = pd.to_datetime(frame["game_date"])
    frame = frame.sort_values(["player_id", "season", "game_date"]).reset_index(drop=True)

    # ---- his team's game dates, so absences can be seen at all ----
    # The population holds only games a player PLAYED. A game he missed
    # leaves no row, so the only way to count absences is to ask which
    # games his team played and check which of them he is in.
    team_games = (frame[["team_id", "season", "game_date"]]
                  .drop_duplicates()
                  .sort_values(["team_id", "season", "game_date"]))

    played = {(int(row.player_id), row.season, row.game_date)
              for row in frame.itertuples()}

    dates_by_team = {}
    for (team_id, season), rows in team_games.groupby(["team_id", "season"]):
        dates_by_team[(team_id, season)] = rows["game_date"].to_numpy()

    missed = np.empty(len(frame))
    missed[:] = np.nan
    for index, row in enumerate(frame.itertuples()):
        dates = dates_by_team.get((row.team_id, row.season))
        if dates is None:
            continue
        before = dates[dates < np.datetime64(row.game_date)]
        if len(before) < RECENT_TEAM_GAMES:
            continue
        window = before[-RECENT_TEAM_GAMES:]
        missed[index] = sum(
            1 for date in window
            if (int(row.player_id), row.season, pd.Timestamp(date)) not in played)
    frame["missed_recent"] = missed

    # ---- recent minutes against his own season rate ----
    grouped = frame.groupby(["player_id", "season"], sort=False)
    recent_minutes = (grouped["minutes"]
                      .rolling(RECENT_APPEARANCES, min_periods=RECENT_APPEARANCES)
                      .mean().shift(1).reset_index(level=[0, 1], drop=True))
    frame["minutes_form"] = recent_minutes / frame["mpg_prior"]

    short = (frame["minutes"] < SHORT_STINT_MINUTES).astype(float)
    frame["short_stint_recent"] = (short.groupby(
        [frame["player_id"], frame["season"]])
        .rolling(RECENT_APPEARANCES, min_periods=RECENT_APPEARANCES)
        .sum().shift(1).reset_index(level=[0, 1], drop=True))

    # ---- what we are trying to see coming ----
    frame["is_short"] = (frame["minutes"] < SHORT_STINT_MINUTES).astype(float)

    frame["missed_bucket"] = pd.cut(
        frame["missed_recent"], bins=[-1, 0, 1, 2, 5],
        labels=["played all 5", "missed 1", "missed 2", "missed 3+"])
    frame["form_bucket"] = pd.cut(
        frame["minutes_form"], bins=[-0.01, 0.7, 0.9, 1.1, 99],
        labels=["<0.7", "0.7-0.9", "0.9-1.1", ">1.1"])
    frame["stint_bucket"] = pd.cut(
        frame["short_stint_recent"], bins=[-1, 0, 1, 3],
        labels=["none short", "1 short", "2-3 short"])
    return frame


def separation(frame, feature):
    """Does this feature actually flag the games that hurt?

    A feature that cannot separate sub-ten-minute games is not an
    availability feature, whatever it does to average error.
    """
    rows = []
    usable = frame.dropna(subset=[feature])
    overall = usable["is_short"].mean()
    for bucket, group in usable.groupby(feature, observed=True):
        rate = group["is_short"].mean()
        ratio = group["PTS_actual"].sum() / group["PTS_predicted"].sum()
        rows.append({"bucket": str(bucket), "n": len(group),
                     "short_rate": rate, "lift": rate / overall if overall else np.nan,
                     "pts_actual_over_predicted": ratio})
    return pd.DataFrame(rows), overall


def factors(train, feature, shrink=SHRINK_GAMES):
    """Normalised multiplicative factors -- see context_features_sweep."""
    out, counts = {}, {}
    for stat in STATS:
        actual, predicted = f"{stat}_actual", f"{stat}_predicted"
        raw = {}
        for bucket, rows in train.dropna(subset=[feature]).groupby(feature,
                                                                  observed=True):
            rows = rows.dropna(subset=[actual, predicted])
            total = rows[predicted].sum()
            if len(rows) < 30 or total <= 0:
                continue
            ratio = rows[actual].sum() / total
            weight = len(rows) / (len(rows) + shrink)
            raw[bucket] = 1.0 + weight * (ratio - 1.0)
            counts[bucket] = len(rows)
        if not raw:
            continue
        total_rows = sum(counts[b] for b in raw)
        centre = sum(raw[b] * counts[b] for b in raw) / total_rows
        for bucket, value in raw.items():
            out[(stat, bucket)] = value / centre
    return out


def evaluate(test, feature, table):
    rows = []
    buckets = test[feature].to_numpy()
    for stat in STATS:
        predicted = test[f"{stat}_predicted"].to_numpy(dtype=float)
        actual = test[f"{stat}_actual"].to_numpy(dtype=float)
        prior = test[f"{stat}_base"].to_numpy(dtype=float)
        multiplier = np.array([table.get((stat, bucket), 1.0) for bucket in buckets])
        adjusted = predicted * multiplier
        ok = ~np.isnan(actual) & ~np.isnan(predicted)
        base_mae = np.abs(predicted[ok] - actual[ok]).mean()
        new_mae = np.abs(adjusted[ok] - actual[ok]).mean()

        call = np.sign(predicted[ok] - prior[ok])
        new_call = np.sign(adjusted[ok] - prior[ok])
        truth = np.sign(actual[ok] - prior[ok])
        live = (truth != 0)
        base_dir = (call[live] == truth[live]).mean() if live.sum() else np.nan
        new_dir = (new_call[live] == truth[live]).mean() if live.sum() else np.nan

        rows.append({"stat": stat, "base_mae": base_mae, "new_mae": new_mae,
                     "rel": new_mae / base_mae if base_mae else np.nan,
                     "base_dir": base_dir, "new_dir": new_dir})
    return pd.DataFrame(rows)


def bootstrap(test, feature, table, rounds=BOOTSTRAP_ROUNDS):
    keys = (test["player_id"].astype(str) + "|" + test["season"].astype(str)).to_numpy()
    unique = np.unique(keys)
    index_by_key = {key: np.where(keys == key)[0] for key in unique}
    values = []
    for _ in range(rounds):
        drawn = RNG.choice(unique, size=len(unique), replace=True)
        rows = np.concatenate([index_by_key[key] for key in drawn])
        values.append(evaluate(test.iloc[rows], feature, table)["rel"].mean())
    return np.nanpercentile(values, [2.5, 97.5])


def report(frame, feature, label):
    print(f"\n{'=' * 74}\n{label}\n{'=' * 74}")
    usable = frame.dropna(subset=[feature])

    table, overall = separation(usable, feature)
    print(f"  sub-{SHORT_STINT_MINUTES:.0f}-minute games overall: {100 * overall:.1f}%")
    print(f"  {'bucket':14s} {'rows':>8s} {'short%':>8s} {'lift':>6s} {'PTS a/p':>9s}")
    for _, row in table.iterrows():
        print(f"  {row['bucket']:14s} {row['n']:8,d} {100 * row['short_rate']:7.1f}% "
              f"{row['lift']:6.2f} {row['pts_actual_over_predicted']:9.4f}")

    seasons = sorted(usable["season"].unique())
    rels, last = [], None
    for held_out in seasons:
        train = usable[usable["season"] != held_out]
        test = usable[usable["season"] == held_out]
        fitted = factors(train, feature)
        result = evaluate(test, feature, fitted)
        rels.append(result["rel"].mean())
        last = (test, fitted, result)
    pooled = float(np.mean(rels))
    print(f"\n  pooled rel-MAE over {len(seasons)} folds: {pooled:.4f} "
          f"({'better' if pooled < 1 else 'WORSE'} than production)")

    test, fitted, result = last
    low, high = bootstrap(test, feature, fitted)
    print(f"  95% CI (last fold): [{low:.4f}, {high:.4f}]"
          + ("  -> includes 1.0, no effect" if low <= 1.0 <= high else ""))
    print(f"  direction, last fold: "
          f"{100 * result['base_dir'].mean():.1f}% -> {100 * result['new_dir'].mean():.1f}%")
    return pooled


def main():
    frame = load()
    if "--write-rates" in sys.argv:
        path, rates = write_rates(frame)
        print(f"wrote {path}")
        print(f"  overall {100 * rates['overall']['rate']:.1f}% "
              f"(n={rates['overall']['n']:,})")
        for bucket, row in rates["by_short_stints"].items():
            print(f"  {bucket:12s} {100 * row['rate']:5.1f}% "
                  f"[{100 * row['low']:.1f}, {100 * row['high']:.1f}]  n={row['n']:,}")
        return 0
    print(f"{len(frame):,} player-games")
    for feature, label in (("missed_bucket", f"GAMES MISSED out of the team's last {RECENT_TEAM_GAMES}"),
                           ("form_bucket", f"MINUTES FORM (last {RECENT_APPEARANCES} appearances / season MPG)"),
                           ("stint_bucket", f"SHORT STINTS in the last {RECENT_APPEARANCES} appearances")):
        print(f"  {feature}: {frame[feature].notna().sum():,} rows usable")

    results = {}
    results["missed"] = report(frame, "missed_bucket",
                               f"GAMES MISSED out of the team's last {RECENT_TEAM_GAMES}")
    results["form"] = report(frame, "form_bucket",
                             f"MINUTES FORM (last {RECENT_APPEARANCES} / season MPG)")
    results["stint"] = report(frame, "stint_bucket",
                              f"SHORT STINTS in the last {RECENT_APPEARANCES} appearances")

    print(f"\n{'=' * 74}\nVERDICT\n{'=' * 74}")
    for name, value in results.items():
        print(f"  {name:8s} pooled rel-MAE {value:.4f}  -> "
              f"{'ships' if value < 0.999 else 'does NOT ship'}")




# ---------------------------------------------------------------------
# The table the app reads.
#
# The sweep above is the evidence that these rates are real and that no
# projection layer should be built on them. This writes the rates
# themselves, so the page can state a measured base rate instead of a
# number somebody typed.
#
# Descriptive, not predictive: these are historical frequencies with
# their sample sizes and a Wilson interval, and the app presents them
# as exactly that.
# ---------------------------------------------------------------------
RATES_PATH = "engine/availability_rates.json"


def wilson(successes, total, z=1.96):
    """A confidence interval that behaves at the edges.

    The normal approximation gives nonsense near 0 and 1 -- negative
    lower bounds on rare buckets -- and a rate published without an
    interval invites being read as more precise than it is.
    """
    if total == 0:
        return (float("nan"), float("nan"))
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = (z * np.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
              / denominator)
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def build_rates(frame):
    rates = {"short_stint_minutes": SHORT_STINT_MINUTES,
             "recent_appearances": RECENT_APPEARANCES,
             "seasons": sorted(frame["season"].unique().tolist()),
             "player_games": int(len(frame)),
             "overall": {}, "by_short_stints": {}, "by_minutes_form": {}}

    overall_rows = frame.dropna(subset=["is_short"])
    successes = int(overall_rows["is_short"].sum())
    total = int(len(overall_rows))
    low, high = wilson(successes, total)
    rates["overall"] = {"rate": successes / total, "n": total,
                        "low": low, "high": high}

    for feature, key in (("stint_bucket", "by_short_stints"),
                         ("form_bucket", "by_minutes_form")):
        for bucket, group in frame.dropna(subset=[feature]).groupby(feature,
                                                                   observed=True):
            successes = int(group["is_short"].sum())
            total = int(len(group))
            low, high = wilson(successes, total)
            rates[key][str(bucket)] = {
                "rate": successes / total, "n": total,
                "low": low, "high": high,
                "lift": (successes / total) / rates["overall"]["rate"],
            }
    return rates


def write_rates(frame, path=RATES_PATH):
    import json
    rates = build_rates(frame)
    with open(path, "w") as handle:
        json.dump(rates, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return path, rates


if __name__ == "__main__":
    main()
