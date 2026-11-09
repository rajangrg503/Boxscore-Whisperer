// Chrome fetcher for tools/defender_matchups.py.
// stats.nba.com only answers from an nba.com page, so run this with the
// Chrome javascript tool on a tab at https://www.nba.com/stats.
//
// 1. Paste this file, then start a run (it continues in the background):
//      window._dmRun([[pid, oppTeamId], ...], '2026-27')
// 2. Poll window._dmDone until true (about 0.6 s per player).
// 3. Read window._dmChunks[i] one at a time (each under ~950 characters,
//    because the tool output is cut at about 1000), save them to
//    data/matchups/raw_<season>_<date>.txt, then import:
//      python3 tools/defender_matchups.py --import <file> --season <season> --asof <date>
//
// Line format: pid|oppTid|poss,pts,ast,tov,fgm,fga,fta|id~name~gp~poss~pts~ast~tov~fgm~fga~fta;...
// (top 3 defenders on the opponent's CURRENT roster, the rest summed as 'rest').
window._dmRos = window._dmRos || {};
window._dmMu = window._dmMu || {};
window._dmRun = async function (pairs, season, rosterSeason) {
  rosterSeason = rosterSeason || '2026-27';
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  window._dmDone = false; window._dmErrs = []; const lines = [];
  for (const [pid, tid] of pairs) {
    try {
      if (!window._dmRos[tid]) {
        const r = await fetch(`https://stats.nba.com/stats/commonteamroster?LeagueID=00&Season=${rosterSeason}&TeamID=${tid}`).then(r => r.json());
        window._dmRos[tid] = r.resultSets[0].rowSet.map(x => x[14]); await sleep(250);
      }
      const k = pid + '|' + season;
      if (!window._dmMu[k]) {
        const r = await fetch(`https://stats.nba.com/stats/leagueseasonmatchups?LeagueID=00&PerMode=Totals&Season=${season}&SeasonType=Regular%20Season&OffPlayerID=${pid}`).then(r => r.json());
        window._dmMu[k] = r.resultSets[0].rowSet; await sleep(300);
      }
      const rows = window._dmMu[k]; if (!rows.length) continue;
      const ros = new Set(window._dmRos[tid]); const t = [0, 0, 0, 0, 0, 0, 0]; const vs = [];
      for (const x of rows) {
        // 7 PARTIAL_POSS, 8 PTS, 10 AST, 11 TOV, 13 FGM, 14 FGA, 24 FTA
        [7, 8, 10, 11, 13, 14, 24].forEach((c, i) => t[i] += x[c]);
        if (ros.has(x[3])) vs.push([x[3], x[4], x[5], x[7], x[8], x[10], x[11], x[13], x[14], x[24]]);
      }
      t[0] = Math.round(t[0] * 10) / 10;
      vs.sort((a, b) => b[3] - a[3]);
      const top = vs.slice(0, 3), rest = vs.slice(3), agg = [0, 'rest', 0, 0, 0, 0, 0, 0, 0, 0];
      for (const v of rest) for (let i = 2; i < 10; i++) agg[i] += v[i];
      const fmt = v => [v[0], String(v[1]).split(' ').slice(-1)[0].replace(/[~;|]/g, ''), v[2], Math.round(v[3] * 10) / 10, ...v.slice(4)].join('~');
      lines.push([pid, tid, t.join(','), top.concat(rest.length ? [agg] : []).map(fmt).join(';')].join('|'));
    } catch (e) { window._dmErrs.push(pid + ':' + e); }
  }
  const ch = []; let cur = '';
  for (const l of lines) { if ((cur + l).length + 1 > 950) { ch.push(cur); cur = ''; } cur += l + '\n'; }
  if (cur) ch.push(cur);
  window._dmChunks = ch; window._dmDone = true;
};
'defender_matchups_fetch loaded';
