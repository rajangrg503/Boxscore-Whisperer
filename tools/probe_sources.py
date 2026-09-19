"""Can a scheduled job reach the data this app runs on? -- a probe, run
from wherever the job would run (GitHub Actions, a laptop, a VPS).

WHY THIS EXISTS
The live app on Streamlit Community Cloud cannot reach stats.nba.com:
every prediction is served from data_cache/, which only refreshes when
someone runs refresh_cache.py on a machine the NBA doesn't block and
pushes the result. During a season that is a standing risk -- a few days
of nobody running it and the site quietly serves stale numbers.

Automating it needs one fact nobody publishes: which hosts a runner can
actually reach. stats.nba.com blocks a lot of cloud IPs (widely reported
for Heroku and AWS), but GitHub's runners are untested. This script
measures it instead of guessing, and also probes the fallbacks, so the
refresh job can be built against whatever actually answers.

WHAT IT PROBES
  stats.nba.com      the endpoints refresh_cache.py uses, through
                     nba_api itself (same headers, same timeouts)
  cdn.nba.com        the static schedule JSON -- a different host and a
                     plain CDN, so it may answer where stats does not
  raw.githubusercontent.com
                     the hoopR mirror of NBA box scores, a plain git
                     repo, as a last-resort source that cannot be
                     IP-blocked in the same way

Every probe reports ok/failed, HTTP status, latency and a short sample
of what came back, so a run's log is enough to decide the design. It
makes a handful of read-only requests, spaced out, and writes nothing.

Usage:
    python3 tools/probe_sources.py            # everything
    python3 tools/probe_sources.py --json     # machine-readable summary
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone

REQUEST_TIMEOUT = 25
PAUSE_SECONDS = 1.5

# The same headers nba_api sends; stats.nba.com rejects anything else.
STATS_HEADERS = {
    "Host": "stats.nba.com",
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://stats.nba.com/",
    "Origin": "https://stats.nba.com",
    "Connection": "keep-alive",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
}


def probe(name, host, fn):
    """Runs one probe and returns a result row. Never raises: a probe
    that fails is the answer, not an error."""
    started = time.time()
    try:
        status, detail = fn()
        ok = status == 200
    except Exception as exc:                      # noqa: BLE001 - reporting the failure IS the job
        status, detail, ok = None, f"{type(exc).__name__}: {exc}"[:200], False
    elapsed = time.time() - started
    return {"probe": name, "host": host, "ok": ok, "status": status,
            "seconds": round(elapsed, 1), "detail": detail}


def _get(url, headers=None, params=None):
    import requests
    response = requests.get(url, headers=headers or {}, params=params or {},
                            timeout=REQUEST_TIMEOUT)
    body = response.text[:120].replace("\n", " ")
    return response.status_code, f"{len(response.content):,} bytes; {body}"


def probe_stats_raw():
    """stats.nba.com, the exact call refresh_cache.py makes for team
    defensive ratings, as a plain request."""
    return _get("https://stats.nba.com/stats/leaguedashteamstats",
                headers=STATS_HEADERS,
                params={"Season": "2025-26", "SeasonType": "Regular Season",
                        "MeasureType": "Advanced", "PerMode": "PerGame",
                        "LeagueID": "00", "Conference": "", "DateFrom": "", "DateTo": "",
                        "Division": "", "GameScope": "", "GameSegment": "", "Height": "",
                        "LastNGames": "0", "Location": "", "Month": "0",
                        "OpponentTeamID": "0", "Outcome": "", "PORound": "0",
                        "PaceAdjust": "N", "Period": "0", "PlayerExperience": "",
                        "PlayerPosition": "", "PlusMinus": "N", "Rank": "N",
                        "SeasonSegment": "", "ShotClockRange": "", "StarterBench": "",
                        "TeamID": "0", "TwoWay": "0", "VsConference": "", "VsDivision": ""})


def probe_stats_via_nba_api():
    """The same endpoint through nba_api, which is what the refresh
    scripts actually use."""
    from nba_api.stats.endpoints import leaguedashteamstats
    stats = leaguedashteamstats.LeagueDashTeamStats(
        season="2025-26", measure_type_detailed_defense="Advanced", timeout=REQUEST_TIMEOUT)
    df = stats.get_data_frames()[0]
    return 200, f"{len(df)} teams, columns include {list(df.columns)[:4]}"


def probe_stats_gamelog():
    """A player game log -- the other call the refresh depends on."""
    from nba_api.stats.endpoints import playergamelog
    log = playergamelog.PlayerGameLog(player_id="203999", season="2025-26",
                                      season_type_all_star="Regular Season",
                                      timeout=REQUEST_TIMEOUT)
    df = log.get_data_frames()[0]
    return 200, f"{len(df)} games for Nikola Jokic in 2025-26"


def probe_cdn_schedule():
    """cdn.nba.com's static schedule -- a plain CDN, different host."""
    return _get("https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json")


def probe_hoopr_mirror():
    """The hoopR mirror of NBA data on GitHub: a git repo, so it can't
    be IP-blocked the way stats.nba.com is."""
    return _get("https://raw.githubusercontent.com/sportsdataverse/"
                "hoopR-nba-data/main/README.md")


PROBES = [
    ("stats.nba.com (raw request)", "stats.nba.com", probe_stats_raw),
    ("stats.nba.com (via nba_api)", "stats.nba.com", probe_stats_via_nba_api),
    ("stats.nba.com (player game log)", "stats.nba.com", probe_stats_gamelog),
    ("cdn.nba.com (static schedule)", "cdn.nba.com", probe_cdn_schedule),
    ("hoopR mirror (GitHub)", "raw.githubusercontent.com", probe_hoopr_mirror),
]


def run_all():
    results = []
    for name, host, fn in PROBES:
        results.append(probe(name, host, fn))
        time.sleep(PAUSE_SECONDS)
    return results


def verdict(results):
    """What the refresh job can be built on, in one line."""
    by_host = {}
    for r in results:
        by_host.setdefault(r["host"], []).append(r["ok"])
    if all(by_host.get("stats.nba.com", [False])):
        return "stats.nba.com works here: the refresh can run unchanged."
    if any(by_host.get("stats.nba.com", [])):
        return ("stats.nba.com answers some calls and not others: rate limiting rather than "
                "an IP block. A slower, retrying refresh may work.")
    if any(by_host.get("cdn.nba.com", [])) or any(by_host.get("raw.githubusercontent.com", [])):
        return ("stats.nba.com is blocked here, but a fallback source answers: the refresh has "
                "to be rebuilt on the fallback, or run somewhere else.")
    return "nothing answered: this runner has no useful network access."


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = parser.parse_args()

    results = run_all()
    summary = {"ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "results": results, "verdict": verdict(results)}
    if args.json:
        print(json.dumps(summary, indent=1))
        return 0

    print(f"Probed at {summary['ran_at']}\n")
    print(f"{'probe':34s} {'result':8s} {'status':>6s} {'secs':>6s}  detail")
    for r in results:
        status = "" if r["status"] is None else r["status"]
        print(f"{r['probe']:34s} {'ok' if r['ok'] else 'FAILED':8s} {status:>6} "
              f"{r['seconds']:>6.1f}  {r['detail'][:90]}")
    print(f"\nVerdict: {summary['verdict']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
