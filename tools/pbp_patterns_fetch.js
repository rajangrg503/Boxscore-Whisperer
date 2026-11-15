// pbp_patterns_fetch.js: build the per-team pattern table from NBA.com play-by-play.
//
// stats.nba.com and cdn.nba.com are blocked from the device shell and the cloud
// container, but answer fetch() from an nba.com tab. So: open any nba.com page in
// Chrome, run this whole file with the javascript tool, wait until
// window._pp.done === window._pp.total, then read window._ppChunks one at a time
// (tool output is cut at about 1000 characters) and save them, in order, to
// data/patterns/raw_<season>_<date>.txt. Import with:
//   python3 tools/pbp_patterns.py --import data/patterns/raw_<season>_<date>.txt --asof <date>
//
// The bucket rules below MUST match bucket() in tools/pbp_patterns.py.
(async () => {
  const BK = ['putback','transition','cut','drive','rim_other','pullup2','mid_other','three_dribble','three_set','three_deep'];
  const bucket = a => {
    const d = (a.descriptor || '').toLowerCase(), q = a.qualifiers || [], st = (a.subType || '').toLowerCase();
    const dist = a.shotDistance || 0, three = a.actionType === '3pt';
    if (/putback|tip/.test(d)) return 'putback';
    if (q.includes('fastbreak')) return 'transition';
    if (three) return /pullup|step back/.test(d) ? 'three_dribble' : (dist >= 26 ? 'three_deep' : 'three_set');
    if (/cutting|alley-oop/.test(d)) return 'cut';
    if (/driving/.test(d)) return 'drive';
    if (st === 'layup' || st === 'dunk' || dist <= 5) return 'rim_other';
    if (/pullup|step back/.test(d)) return 'pullup2';
    return 'mid_other';
  };
  const zone = b => b.startsWith('three') ? '3' : (['putback','transition','cut','drive','rim_other'].includes(b) ? 'r' : 'm');
  const sec = a => { const m = (a.clock || 'PT12M00.00S').match(/PT(\d+)M([\d.]+)S/); const left = +m[1] * 60 + +m[2]; const q = a.period;
    return (q <= 4 ? (q - 1) * 720 : 2880 + (q - 5) * 300) + ((q <= 4 ? 720 : 300) - left); };
  const sched = await fetch('https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json').then(r => r.json());
  const games = [];
  for (const d of sched.leagueSchedule.gameDates) for (const g of d.games)
    if (g.gameId.startsWith('002') && g.gameStatus === 3) games.push({ id: g.gameId, a: g.awayTeam.teamTricode, h: g.homeTeam.teamTricode });
  const T = {};
  const team = t => T[t] || (T[t] = { g: 0, o: BK.map(() => [0, 0, 0, 0]), d: BK.map(() => [0, 0, 0]), so: BK.map(() => 0), sd: BK.map(() => 0), pairs: {} });
  window._pp = { done: 0, total: games.length, failed: [] };
  const one = async g => {
    try {
      const j = await fetch('https://cdn.nba.com/static/json/liveData/playbyplay/playbyplay_' + g.id + '.json').then(r => r.json());
      const fg = j.game.actions.filter(a => a.isFieldGoal && a.teamTricode);
      team(g.a).g++; team(g.h).g++;
      const makes = {};
      for (const a of fg) {
        const off = a.teamTricode, def = off === g.a ? g.h : g.a, b = bucket(a), i = BK.indexOf(b);
        const made = a.shotResult === 'Made', pts = made ? (a.actionType === '3pt' ? 3 : 2) : 0;
        const o = team(off).o[i], d = team(def).d[i];
        o[0]++; d[0]++; if (made) { o[1]++; d[1]++; o[2] += pts; d[2] += pts;
          if (a.assistPersonId) { o[3]++; const k = (a.assistPlayerNameInitial || '?').split(' ').pop() + '>' + (a.playerNameI || '?').split(' ').pop() + '|' + zone(b); team(off).pairs[k] = (team(off).pairs[k] || 0) + 1; }
          (makes[off + '|' + i] = makes[off + '|' + i] || []).push(sec(a)); }
      }
      for (const k in makes) { const [off, i] = k.split('|'); const def = off === g.a ? g.h : g.a; const t = makes[k].sort((x, y) => x - y); let n = 0, p = 0;
        while (p + 2 < t.length) { if (t[p + 2] - t[p] <= 180) { n++; p += 3; } else p++; }
        team(off).so[+i] += n; team(def).sd[+i] += n; }
    } catch (e) { window._pp.failed.push(g.id); }
    window._pp.done++;
  };
  let p = 0; const worker = async () => { while (p < games.length) await one(games[p++]); };
  await Promise.all([1, 2, 3, 4, 5].map(worker));
  const out = { season: sched.leagueSchedule.seasonYear, games: games.length - window._pp.failed.length, buckets: BK, teams: {} };
  for (const t of Object.keys(T).sort()) { const x = T[t];
    out.teams[t] = { g: x.g, o: x.o, d: x.d, so: x.so, sd: x.sd, pairs: Object.entries(x.pairs).sort((a, b) => b[1] - a[1]).slice(0, 5) }; }
  const s = JSON.stringify(out); let sum = 0; for (let i = 0; i < s.length; i++) sum = (sum + s.charCodeAt(i) * (i % 97 + 1)) % 1000000007;
  window._ppRaw = s; window._ppSum = sum; window._ppChunks = []; for (let i = 0; i < s.length; i += 900) window._ppChunks.push(s.slice(i, i + 900));
})();
