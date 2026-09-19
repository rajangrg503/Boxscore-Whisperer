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

Keeping the cache fresh in season
---------------------------------
stats.nba.com blocks the machines that could refresh it automatically.
Measured on 19 Sep 2026 with `tools/probe_sources.py`, from a GitHub
Actions runner:

| Source | Result |
|---|---|
| stats.nba.com (raw, nba_api, game log) | timed out, 25s each |
| cdn.nba.com static schedule | 403 Access Denied |
| hoopR mirror on GitHub | 200 OK |

So the refresh runs on a normal home machine instead:

    bash tools/scheduled_refresh.sh --dry-run   # refresh, show the diff, push nothing
    bash tools/scheduled_refresh.sh             # refresh, commit, push

It refuses to push when the tree is dirty, when it isn't on `main`, or
when the refresh would delete more than 50 cache files, and it says so
when the watchdog reports an endpoint failing validation.

To run it every morning on a Mac:

    bash tools/install_refresh_job.sh

That writes and loads the launchd plist from this checkout's real path,
so there is nothing to hand-edit.

**The checkout must not live in `~/Documents`, `~/Desktop` or
`~/Downloads`.** macOS protects those folders, and the `/bin/bash` that
launchd spawns has no permission to read them — the job installs
cleanly, reports exit code 126, and never runs a single line. It failed
that way here for three days in September 2026 before anyone noticed,
because launchd cannot raise a consent prompt at 08:30 with nobody at
the machine. The installer refuses to proceed from a protected folder
for that reason; `~/Projects` or anything else under the home folder
needs no permission at all.

The app shows the cache's age in the header during the season
(engine/freshness.py), so a refresh that stops running is visible rather
than silent — though as the above shows, "visible" only helps if
somebody looks. launchd's own stderr now lands in `logs/launchd.err`,
next to `logs/refresh.log`, instead of in `/tmp`.


Hit rates, and not counting the same games twice
------------------------------------------------
The L5 / L10 / L20 / Season row is the most scannable thing on the page,
which makes it the easiest to misread: each badge is a percentage with
no denominator, so four greens look like four pieces of evidence. When
the log is shorter than the widest window they are one piece of evidence
shown four times -- five head-to-head games against one opponent gave
L5, L10, L20 and All H2H all computed over those same five games, and
the page read 100% / 100% / 100% / 100%.

engine/hit_rates.py builds the windows, then collapses any that cover
the same games, keeping the widest honest label, and carries the game
count on every badge:

| games in the log | what the row shows |
|---|---|
| 30 | L5 (5) · L10 (10) · L20 (20) · Season (30) |
| 8 | L5 (5) · Season (8) |
| 5 | Season (5) — one badge, not four |

Under ten games the page adds a line saying how far a single game moves
the number (`sample_caveat`).

**The windows count games against the selected opponent.** That is the
question a reader is asking, and the head-to-head table sits directly
above the row -- reading "he has cleared this every time against Denver"
three inches above a row saying 40% is a contradiction that makes the
page useless. (The 40% was his last five games against anyone; against
Denver it was five from five.)

The opponent number cannot stand alone, though. Across 136 players and
~4,000 player-opponent pairs in the cache:

| games vs ONE opponent | |
|---|---|
| median | 6 |
| fewer than 5 | 31% of matchups |
| fewer than 10 | 85% of matchups |
| 20 or more | 0% |

So `against_opponent()` leads with the opponent windows and carries the
season rate alongside, each with its own game count. A six-game matchup
collapses to one badge plus the season, never four windows.


On a phone's home screen
------------------------
The site is a plain web app, not an App Store build. Added to an iPhone
home screen it launches full-screen with its own icon -- iOS does the
standalone part by itself; what it needs from us is an
`apple-touch-icon`, a short name, and a theme colour, or it invents a
grey letter on black and truncates the title.

Streamlit owns the page `<head>` and offers no hook into it, and
`st.markdown` strips `<script>`, so `_install_home_screen_tags()` in
app.py adds them from a zero-height component iframe. It is idempotent
and wrapped in try/catch: if a future Streamlit closes that door the
cost is a plainer icon, not a broken page.

**The public URL does not serve this app.** On Community Cloud
`https://<name>.streamlit.app/` serves Streamlit's own wrapper page -- a
React shell with an empty `<title>`, its own apple-touch-icon and its
own manifest -- and that shell runs the real app in a same-origin iframe
at `/~/+/`. A phone therefore reads the *wrapper's* head, not ours,
which is why the first attempt at this changed nothing visible:

    /                 Streamlit's wrapper   <- what iOS reads
    /~/+/             this app              <- where a component script runs
    /~/+/app/static/  static files

So the tags go to `window.top` rather than `window.parent`, and they
replace the wrapper's icon and manifest instead of politely skipping
them. The static prefix is derived from `window.parent.location`, which
is `/~/+/` on Community Cloud and `/` when run locally, rather than
hardcoded. The wrapper's blank title is filled in too -- that is what a
bookmark, a shared link and a browser tab all otherwise show a hostname
for.

The wrapper also gets its root background painted. Launched from a home
screen, iOS colours the status bar strip from the page behind it, and
the wrapper sets no background at all, so a white band sat above a black
app.

Streamlit's own toolbar (Share, Fork, the GitHub mark) is hidden at
phone widths. There is no room for it in a phone header, and on a public
app it invites a reader to go fork the repo from inside the product; on
a desktop it stays, where the space exists and it is a fair signal for a
tool whose pitch is that you can check the working.

The icons are generated, not hand-made, so they cannot drift from the
wordmark:

    python3 tools/make_icons.py     # -> static/

`static/` is served via `server.enableStaticServing` in
`.streamlit/config.toml`, which is read at server start -- a git push
alone will not apply it, the app has to be rebooted. An apple-touch-icon
has to be fetchable by URL, as iOS will not accept a data: URI for it,
and the manifest's icon paths are relative so they resolve under
whichever prefix the app is running on.


Projected minutes
-----------------
Every projection is a player's per-minute rate times the minutes we
expect him to play, not a flat per-game average:

    projected = 0.5 * mean(last 3 played games) + 0.5 * season MPG
    line_s    = (total stat s / total minutes) * projected

Config `min_3_0.5`, chosen leave-one-season-out on pooled relative MAE
(`minutes_model_sweep.py`). It lives in `engine/minutes.py` and enters
the app through `engine/baseline_stats.py`, so the live app and the
point-in-time backtest cannot disagree about it.

Measured on the honest 70,944-game population:

| | error (rel-MAE) | direction | balanced |
|---|---|---|---|
| flat per-game average | 1.0000 | 50.5% | 51.0% |
| **projected minutes** | **0.9910** | **53.3%** | **54.1%** |
| recent-form control | 0.9943 | 53.5% | 53.4% |

The recent-form control matters: if simply weighting a player's recent
scoring did as well, "minutes" would be a story rather than a mechanism.
On raw direction it looks better; on balanced accuracy and on error it
is worse. Recency shifts predictions toward whatever just happened,
which flatters raw direction through the base rate. Corrected for that,
the minutes model wins.

Two things it does not do:

- **It does not know about tonight.** No injury report, no rest, no
  blowout risk, no starter/bench change. It reads recent workload, not
  role.
- **It does not clear the vig.** 53.3% pooled direction is under the
  53.5% break-even at -115, and that metric is measured against the
  player's own average, not against a bookmaker's line, which is a
  sharper number to begin with.

Shipping it meant refitting the calibrated distributions: the 80% ranges
were fitted against the old predictions and stop being 80% the moment
the point estimate moves. The chain is

    build_backtest_population.py     # carries {stat}_flat and {stat}_base
    calibration_sweep.py --population  # refits engine/stat_distribution.json

`{stat}_flat` is kept alongside `{stat}_base` because the lean models
measure direction against the flat season average and that is still what
"season average" means to them. `run_backtest.py` emits both columns too,
so its cross-check against the population still holds: on the 29,914
games the two sets share, the flat baselines match to zero.

One seam worth knowing: the model is fitted on box-score minutes
(accurate to the second, 27.783) and runs live on game-log minutes,
which the NBA returns whole (28). Over those same shared games the
rounding moves a projection by a median of 0.18%, 0.55% at p95.
