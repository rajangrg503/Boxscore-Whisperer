# Journal data: field guide (schema 2)

One file per night: `journal/<US date>.json` (for example `2026-10-30.json`),
written by tools/daily_journal.py -- every morning by the 08:30 job, or from
the journal app. Nights with `"complete": false` are rewritten on the next run.
All projections use only games before that night.

## Night
- `date`, `source` (where the game list came from), `written_at`, `schema`
- `complete`: every box score and quarter score was in, and Llama ran if wanted
- `checked` / `inside`: projection lines this night / how many landed in range
- `games`: list, below

## Game
- `game_id`, `home`, `away`, `winner`, `loser`, `margin`, `missing` (no box score yet)
- `tw` / `tl`: winner's / loser's team totals (pts, fg_pct, fg3m, fta, oreb, ast, tov,
  paint, fastbreak, second, off_tov, bench, biggest_lead, run, lead_changes)
- `flow`: quarters (`qw`, `ql`), `half`, `decisive` quarter, `overtime`
- `main_story`, `why` (`text`, `by`: "model" or "auto", `rejected`), `facts` (what Llama saw)
- `minutes`: EVERY player in the box score: `pid`, `name`, `team`, `min`, `usual`
  (median of his last 10 games before this night), `delta` (= min - usual), `played`,
  `comment` (the NBA's DNP/inactive note)
- `rotation`: per team, `down` = played 6+ fewer than usual (DNPs count if
  usually 10+ minutes), `up` = played 3+ more
- `lines`: one per projected player and stat (PTS, REB, AST), below

## Line
- `pid`, `name`, `team`, `stat`, `low` / `mid` / `high` (80% range and median),
  `actual`, `side` ("inside", "above", "below")
- `min`, `usual_min`, `min_delta` (= min - usual_min; negative = fewer minutes)
- `absorbed_by`: when he played 6+ fewer, the teammates who played more
  (`name`, `pid`, `delta`), biggest first
- `minutes_from`: when he played 6+ more, the teammates who played fewer
