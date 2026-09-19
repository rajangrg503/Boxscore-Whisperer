This folder holds cached NBA data as JSON files, one per (player,
season) or (team-stats, season) lookup. It's read by app.py's
cached_or_live() function as a fallback when a live nba_api call fails
(e.g. when the app is running on Streamlit Cloud, where nba_api is
blocked).

To populate/update this folder:
1. Run `python3 refresh_cache.py` on your own machine (where nba_api
   works).
2. Commit and push the updated files in this folder to GitHub.
3. Streamlit Cloud will pick up the new data on its next redeploy, or
   trigger a manual reboot from the app's "Manage app" menu.

This folder can be safely deleted and regenerated at any time -- it's
just a cache, not a source of truth.

Saved predictions (the sidebar tracker)
---------------------------------------
Streamlit Community Cloud wipes the app's files on every reboot, so the
live app keeps saved predictions in a free Postgres database (Neon)
instead of prediction_log.csv. The app uses the database whenever a
`DATABASE_URL` is set, either as an environment variable or in the
Streamlit app's Secrets:

    DATABASE_URL = "postgresql://...neon.tech/neondb?sslmode=require&channel_binding=require"

The table is created automatically on first use. Without a
`DATABASE_URL` (local runs, the test suite) the tracker keeps using
prediction_log.csv next to app.py, exactly as before.

To run the tracker tests against a throwaway Postgres too:

    BW_TEST_DATABASE_URL=postgresql://... pytest tests/test_tracker.py \
        tests/test_layer_accuracy.py tests/test_log_store.py

Never point BW_TEST_DATABASE_URL at the production database: the tests
empty the table.

Strong leans and the clearest read
----------------------------------
The "Strong leans" block and the "Clearest read(s)" on both tabs come
from two generated files:

* engine/lean_models.json -- the per-stat models, from
  `python3 lean_model_sweep.py`
* engine/lean_tiers.json -- each lean's grade and its held-out record,
  from `python3 clearest_read_sweep.py` (run it after the first one)

Both only say whether a stat lands above or below the player's own
season average. Leans start once a player has 10 regular-season games
in the current season.

Ranges and "chance he clears the line"
--------------------------------------
Each stat card shows a calibrated 80% range, and, when a line is typed
in, the chance the player clears it. Both come from a per-stat
distribution in engine/stat_distribution.json, written by
`python3 calibration_sweep.py` and read by engine/distribution.py.

The sweep fits several candidate distributions, scores them on seasons
they were never fitted on, and keeps the best per stat. It also records
what the old fixed band did, for comparison. Rerun it after any change
that moves the projections themselves.

The backtest population
-----------------------
`build_backtest_population.py` builds the case set the published numbers
use: every player in the three cached seasons, from his 6th played game
of a season onward, decided as of each game date, from the cached box
scores (70,944 player-games, 746 players). It writes
backtest_population.csv and backtest_player_games.csv.

`run_backtest.py` is the older list -- the top 150 players by minutes
played over the WHOLE season, which is only knowable in April. It is
kept as a cross-check: on the games the two sets share, the baselines
and predictions match exactly.

The sweeps take `--population` to fit on the honest set:

    python3 build_backtest_population.py
    python3 lean_model_sweep.py --population
    python3 clearest_read_sweep.py --population
    python3 calibration_sweep.py --population
