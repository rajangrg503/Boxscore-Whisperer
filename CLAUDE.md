# Boxscore Whisperer — rules for Claude sessions

Owner: karma (Rajan Gurung), Sydney (AEDT). NBA player-prop projections, a local Journal app
(localhost:8501, `tools/journal_app.py`), X account @BoxscoreWhisper. Project docs (results,
morning screens, staking rules) live in the claude.ai project "boxscore with cowork", not here.

## Where things run
- Claude reaches this repo through the device shell: `$HOME/mnt/Projects--Boxscore-Whisperer`
  (on the Mac: `~/Projects/Boxscore-Whisperer`). The cloud container cannot see it.
- **Network:** ESPN (`site.api.espn.com`), stats.nba.com and Sportsbet's API are blocked from both
  shells. Fetch them with `fetch()` in Chrome (ESPN from any tab; stats.nba.com from an nba.com tab;
  Sportsbet from a sportsbet.com.au tab). Chrome tool output is cut at ~1000 characters: build
  compact strings and read them in chunks (see `tools/defender_matchups_fetch.js`).
- `tools/opp_allows.py` takes ~20 s per run: 3–6 per shell call, never more.
- The stats cache (`data_cache/`) is refreshed by a job on the Mac; last night's games appear the next
  morning. Results are scored from ESPN box scores, not the cache.

## Git
- Commit as karma: `GIT_AUTHOR_NAME/GIT_COMMITTER_NAME="Rajan Gurung"`,
  `GIT_AUTHOR_EMAIL/GIT_COMMITTER_EMAIL=rajangrg2982@gmail.com`; end messages with the session's
  Co-Authored-By / Claude-Session lines.
- Deletes are blocked in the device shell, so git leaves lock files: after every commit run
  `for f in index.lock HEAD.lock; do [ -e .git/$f ] && mv .git/$f .git/stale-$f-$(date +%s); done`.
- Claude cannot push. karma runs `cd ~/Projects/Boxscore-Whisperer && git push`; tell him when there
  are commits waiting.
- Never commit files another session left dirty (currently `journal/FIELDS.md`, `tests/test_picks.py`,
  `tools/journal_app.py`, `tools/picks.py`). `git add` only the files you changed.
- `journal/` is gitignored on purpose (bookmaker lines and bets stay local). Never commit it.

## Data files
- `data/player_notes.json`: karma's reads. Write with `json.dump(d, f, indent=2, ensure_ascii=False)`
  plus a trailing newline (indent=1 once rewrote the whole file). Merge, never delete karma's notes.
  Run `python3 -m pytest -q tests/test_player_notes.py` after editing.
- `_game_reads` are observations to check after games, never betting instructions.
- Journal picks `journal/picks/<id>.json`: `placed_at` and `saved_at` must be full ISO timestamps
  with offset (e.g. `2026-11-09T07:15:00+11:00`); a guessed "07:1x" crashed the Journal. Look up `pid`
  with `nba_api.stats.static.players` (never guess). `us_date` is the US game date.

## Betting and posting rules (karma's)
- Claude may load a Sportsbet slip and enter stakes, but never presses Place Bet / Confirm Bets.
  No sign-ins, no passwords. Dabble and iPhone Mirroring are read only.
- Straights $3 flat; multis $2, max 3 a day; ceiling tickets from the experiment fund; no player-stat
  in more than 2 slips; max 2 straights per game.
- X posts only from @BoxscoreWhisper: check `[data-testid="SideNav_AccountSwitcher_Button"]` right
  before every Post click. No odds or bookmaker names; end with "Not betting advice. 18+"; ≤280
  weighted characters (emoji count 2). Ask before deleting or editing public posts. Don't reply to
  commenters unless asked.
- When karma rejects a rule ("no dont…"), revert it.

## Tools worth knowing
- `tools/advantage_finder.py <US date> --top 3`: biggest edge per game (strength vs opponent leak);
  used for straights and ceiling bets. `--played`, `--backtest START END`.
- `tools/slip_check.py` (`check_leg`): model chance for a leg. `tools/opp_allows.py OPP STAT`.
- `tools/team_defense.py --before DATE --game AWAY HOME`: team-ratings score for the box-score cards.
- `tools/defender_matchups.py`: player vs tonight's defenders (context only; backtest says don't filter on it).
- `tools/game_lens.py`, `tools/ceiling_screen.py`, `tools/clv_report.py`.
