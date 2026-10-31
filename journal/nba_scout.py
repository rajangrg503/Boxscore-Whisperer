#!/usr/bin/env python3
"""Pull the latest Locked On episode per team, and refuse to pull an old one.

    python3 nba_scout.py thunder
    python3 nba_scout.py batch
    python3 nba_scout.py thunder --check          # pick a video, download nothing
    python3 nba_scout.py batch --max-age-days 7
    python3 nba_scout.py clean

WHY THIS WAS REWRITTEN
On 31 Oct 2026 twelve transcripts were read and eleven were stale -- the
Hawks file was Quin Snyder's hiring, the Nets file was the James Harden
trade, the Warriors file was Klay Thompson's return from injury. Years
old. Only the Pistons file was current. Four separate faults, all of
which let an old episode through or stopped a current one:

1. THE RELEVANCE SEARCH. The fallback was

       ydl.extract_info(f"ytsearch1:{handle} postgame")

   and ytsearch ranks by RELEVANCE, not date, so it returns whichever
   episode of that show is most popular all-time. That is where the
   2022 and 2023 episodes came from. A fallback that silently answers
   with a four-year-old episode is worse than no fallback, so it is
   gone: this returns None and says so.

2. THE DATE HOLE. The age check read

       if upload_date:
           ...reject if older than the cutoff...
       candidates.append(entry)

   so an entry with NO upload_date skipped the check and was kept.
   yt-dlp frequently omits upload_date on playlist entries, so "date
   missing" silently meant "date fine". Here a missing date is a
   rejection.

3. THE CORRUPTED KEY. TEAM_HANDLES carried
   "【entity-pistons¦canonical_name=Detroit Pistons】" instead of
   "pistons", so `nba_scout.py pistons` raised KeyError and a batch run
   wrote a file named after the mangled key. Something annotated the
   dict on a paste; only that one key was hit.

4. THE IGNORED OPTION. 'sublangs' is not a yt-dlp option. The name is
   'subtitleslangs', so the English filter never applied.

WHY RSS INSTEAD OF yt-dlp FOR THE LISTING
The old listing used extract_flat=False with playlistend=15, which makes
yt-dlp fully resolve fifteen videos per team: about 450 page fetches for
a batch, which is what the 429 handler and the 15-25 second sleeps were
there to survive.

YouTube publishes every channel as an Atom feed:

    https://www.youtube.com/feeds/videos.xml?channel_id=UC...

Fifteen newest videos, each with a real <published> timestamp, as plain
XML. One cheap request per team, dates that are always present, and no
rate limiting. The handle -> channel_id lookup happens once per channel
and is cached in .channel_ids.json beside this file.

yt-dlp is still used for the captions themselves -- that part was fine.

WHAT IS NOT TESTED HERE
The network paths. This project's automated hosts cannot reach YouTube
(and yt_dlp is not installed on them), so the feed parsing, the handle
resolution and the caption download have only been exercised by their
pure logic. Run `--check` on a couple of teams before trusting a batch.
"""
import argparse
import glob
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree

import requests

try:
    import yt_dlp
except ImportError:                      # --check and the parsers work without it
    yt_dlp = None

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "transcripts_output")
CID_CACHE = os.path.join(HERE, ".channel_ids.json")

FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
ATOM = "{http://www.w3.org/2005/Atom}"
YT = "{http://www.youtube.com/xml/schemas/2015}"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
TIMEOUT = 20

# A title has to look like game coverage, not a draft special or a
# listener-mailbag episode.
POSTGAME_WORDS = ("postgame", "post game", "post-game", "recap",
                  "reaction", "highlights", "instant")

TEAM_HANDLES = {
    "hawks": "@LockedOnHawks", "celtics": "@LockedOnCeltics",
    "nets": "@LockedOnNets", "hornets": "@LockedOnHornets",
    "bulls": "@LockedOnBulls", "cavaliers": "@LockedOnCavs",
    "mavericks": "@LockedOnMavs", "nuggets": "@DNVR_Sports",
    "pistons": "@LockedOnPistons", "warriors": "@LockedOnWarriors",
    "rockets": "@LockedOnRockets", "pacers": "@LockedOnPacers",
    "clippers": "@LockedOnClippers", "lakers": "@LockedOnLakers",
    "grizzlies": "@LockedOnGrizzlies", "heat": "@LockedOnHeat",
    "bucks": "@LockedOnBucks", "timberwolves": "@LockedOnWolves",
    "pelicans": "@LockedOnPelicans", "knicks": "@LockedOnKnicks",
    "thunder": "@LockedOnThunder", "magic": "@LockedOnMagic",
    "76ers": "@LockedOn76ers", "suns": "@LockedOnSuns",
    "blazers": "@LockedOnBlazers", "kings": "@LockedOnKings",
    "spurs": "@LockedOnSpurs", "raptors": "@LockedOnRaptors",
    "jazz": "@LockedOnJazz", "wizards": "@LockedOnWizards",
}


# ---------------------------------------------------------------- helpers

def looks_like_game_coverage(title):
    return any(word in (title or "").lower() for word in POSTGAME_WORDS)


def parse_published(text):
    """RSS <published> -> aware datetime, or None. None means reject."""
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_upload_date(text):
    """yt-dlp upload_date (YYYYMMDD) -> aware datetime, or None."""
    if not text:
        return None
    try:
        return datetime.strptime(str(text), "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def pick(entries, max_age_days, any_title=False):
    """The newest entry that is recent enough and looks like game coverage.

    `entries` is [{id, title, published(datetime or None)}]. An entry with
    no date is DROPPED -- that is fault 2 in the docstring. Returns
    (entry, reason_if_none).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    dated = [e for e in entries if e.get("published") is not None]
    undated = len(entries) - len(dated)

    fresh = [e for e in dated if e["published"] >= cutoff]
    if not fresh:
        newest = max((e["published"] for e in dated), default=None)
        age = f", newest is {(datetime.now(timezone.utc) - newest).days}d old" if newest else ""
        return None, f"nothing within {max_age_days} days{age} ({undated} undated dropped)"

    wanted = fresh if any_title else [e for e in fresh if looks_like_game_coverage(e["title"])]
    if not wanted:
        return None, (f"{len(fresh)} recent video(s) but none look like game "
                      f"coverage; --any-title to take the newest anyway")

    wanted.sort(key=lambda e: e["published"], reverse=True)
    return wanted[0], None


# ------------------------------------------------------- channel listing

def load_cids():
    try:
        with open(CID_CACHE) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def save_cids(cids):
    try:
        with open(CID_CACHE, "w") as handle:
            json.dump(cids, handle, indent=1, sort_keys=True)
    except OSError:
        pass


def channel_id(handle, cids):
    """Resolve @Handle -> UC..., once, then cache it."""
    if handle in cids:
        return cids[handle]
    url = f"https://www.youtube.com/{handle}/videos"
    try:
        body = requests.get(url, headers=UA, timeout=TIMEOUT).text
    except Exception as exc:                  # noqa: BLE001 -- reported
        print(f"    channel page failed: {type(exc).__name__}")
        return None
    match = (re.search(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"', body)
             or re.search(r'"externalId":"(UC[\w-]{22})"', body)
             or re.search(r'"channelId":"(UC[\w-]{22})"', body))
    if not match:
        print("    could not find a channel id on the page")
        return None
    cids[handle] = match.group(1)
    save_cids(cids)
    return match.group(1)


def entries_from_feed(cid):
    """[{id, title, published}] from the channel's Atom feed, newest first."""
    try:
        response = requests.get(FEED.format(cid=cid), headers=UA, timeout=TIMEOUT)
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)
    except Exception as exc:                  # noqa: BLE001 -- reported
        print(f"    feed failed: {type(exc).__name__}: {str(exc)[:70]}")
        return None
    author = (root.findtext(f"{ATOM}author/{ATOM}name") or "").strip()
    out = []
    for entry in root.findall(f"{ATOM}entry"):
        vid = entry.findtext(f"{YT}videoId")
        if not vid:
            continue
        out.append({
            "id": vid,
            "title": (entry.findtext(f"{ATOM}title") or "").strip(),
            "published": parse_published(entry.findtext(f"{ATOM}published")),
            "channel": author,
        })
    return out


def entries_from_ytdlp(handle):
    """Fallback listing. Same strict date rule -- no relevance search."""
    if yt_dlp is None:
        return None
    opts = {"quiet": True, "skip_download": True, "no_warnings": True,
            "extract_flat": "in_playlist", "playlistend": 15,
            "extractor_args": {"youtube": {"player_client": ["android"]}}}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/{handle}/videos",
                                    download=False)
    except Exception as exc:                  # noqa: BLE001 -- reported
        print(f"    yt-dlp listing failed: {type(exc).__name__}")
        return None
    out = []
    for entry in (info or {}).get("entries") or []:
        if not entry:
            continue
        stamp = entry.get("timestamp") or entry.get("release_timestamp")
        published = (datetime.fromtimestamp(stamp, timezone.utc) if stamp
                     else parse_upload_date(entry.get("upload_date")))
        out.append({"id": entry.get("id"), "title": entry.get("title") or "",
                    "published": published})
    return [e for e in out if e["id"]]


def latest_episode(handle, cids, max_age_days, any_title=False):
    """(entry, reason) -- the feed first, yt-dlp as backup, never a search."""
    cid = channel_id(handle, cids)
    entries = entries_from_feed(cid) if cid else None
    if not entries:
        print("    feed unavailable, trying yt-dlp listing")
        entries = entries_from_ytdlp(handle)
    if not entries:
        return None, "no listing available (feed and yt-dlp both failed)"
    return pick(entries, max_age_days, any_title)


# ------------------------------------------------------------- captions

def clean_vtt(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as handle:
            raw = handle.read()
    except OSError:
        return ""
    lines = []
    for line in raw.splitlines():
        if "-->" in line or "WEBVTT" in line or line.startswith("Kind:") or not line.strip():
            continue
        line = re.sub(r"<[^>]+>", "", line).strip()
        if not line or re.fullmatch(r"\d+", line):
            continue
        if not lines or lines[-1] != line:
            lines.append(line)
    return " ".join(lines)


# Caption clients, tried in order. YouTube rate-limits them separately,
# and "android" -- which this used to request on its own -- is the most
# heavily throttled of the three, so a 429 on it says nothing about
# whether the others would answer.
CAPTION_CLIENTS = (["ios"], ["web_safari"], ["android"])


def get_transcript(vid, tmp, cookies_from=None):
    """Captions for one video, retrying a 429 instead of sleeping through it.

    The old handler slept sixty seconds on a 429 and THEN returned None,
    so the wait bought nothing: the request it was waiting for was never
    re-sent. It now retries, backing off 30s, 60s, 120s, and moves to the
    next client rather than re-asking the one that just refused.

    A 429 on every client usually means YouTube wants a signed-in
    request. --cookies-from chrome passes the browser's own cookies and
    normally clears it. That reads the cookie jar of a browser on this
    machine, so it is opt-in and never the default.
    """
    if yt_dlp is None:
        print("    yt_dlp not installed here -- cannot fetch captions")
        return None
    base = {"skip_download": True, "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["en", "en-US", "en-orig"],   # was 'sublangs'
            "subtitlesformat": "vtt",
            "outtmpl": os.path.join(tmp, vid), "quiet": True,
            "no_warnings": True, "sleep_interval": 3,
            "sleep_interval_requests": 2, "retries": 3,
            "extractor_retries": 3}
    if cookies_from:
        base["cookiesfrombrowser"] = (cookies_from,)
    backoff = 30
    throttled = False
    for client in CAPTION_CLIENTS:
        opts = dict(base)
        opts["extractor_args"] = {"youtube": {"player_client": list(client)}}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info(f"https://www.youtube.com/watch?v={vid}",
                                 download=True)
            break
        except Exception as exc:              # noqa: BLE001 -- reported
            if "429" in str(exc) or "Too Many Requests" in str(exc):
                throttled = True
                print(f"    429 on {client[0]}, waiting {backoff}s then "
                      f"trying the next client")
                time.sleep(backoff)
                backoff = min(backoff * 2, 120)
                continue
            print(f"    captions failed on {client[0]}: "
                  f"{type(exc).__name__}: {str(exc)[:70]}")
            return None
    else:
        if throttled and not cookies_from:
            print("    throttled on every client. Re-run with "
                  "--cookies-from chrome (or firefox/safari/edge) to send "
                  "your browser's YouTube cookies.")
        return None
    vtts = sorted(glob.glob(os.path.join(tmp, f"{vid}*.vtt")))
    if not vtts:
        return None
    text = clean_vtt(vtts[0])
    for path in vtts:
        try:
            os.remove(path)
        except OSError:
            pass
    return text


# ------------------------------------------------------------- per team

def process_team(key, out, cids, max_age_days, check_only=False,
                 any_title=False, cookies_from=None):
    handle = TEAM_HANDLES[key]
    print(f"--> {key.upper():14s} {handle}")

    entry, reason = latest_episode(handle, cids, max_age_days, any_title)
    if entry is None:
        return f"NO  {key.upper()}: {reason}"

    when = entry["published"].strftime("%Y-%m-%d")
    age = (datetime.now(timezone.utc) - entry["published"]).days
    print(f"    {when} ({age}d)  {entry['title'][:70]}")
    if check_only:
        return f"OK  {key.upper()}: would take {entry['id']} from {when}"

    text = get_transcript(entry["id"], out, cookies_from)
    if not text:
        return f"NO  {key.upper()}: no captions for {entry['id']}"

    path = os.path.join(out, f"{key}_latest.txt")
    with open(path, "w", encoding="utf-8") as handle_out:
        handle_out.write(
            f"TEAM: {key}\n"
            f"URL: https://youtu.be/{entry['id']}\n"
            f"CHANNEL: {entry.get('channel') or handle}\n"
            f"TITLE: {entry['title']}\n"
            f"PUBLISHED: {when} ({age} days old at fetch)\n"
            f"FETCHED: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n"
            f"{text}"
        )
    return f"YES {key.upper()}: {len(text)} chars from {when}"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", nargs="?", default="thunder",
                        help="a team key, or 'batch', or 'clean'")
    parser.add_argument("--max-age-days", type=int, default=14,
                        help="reject anything older than this (default 14)")
    parser.add_argument("--check", action="store_true",
                        help="report what would be fetched, download nothing")
    parser.add_argument("--any-title", action="store_true",
                        help="accept the newest recent video even if the title "
                             "does not look like game coverage")
    parser.add_argument("--cookies-from", default=None,
                        choices=["chrome", "firefox", "safari", "edge", "brave"],
                        help="send that browser's YouTube cookies with the "
                             "caption request. Use it when every client "
                             "returns 429; it reads the browser's cookie jar "
                             "on this machine, so it is never the default.")
    args = parser.parse_args(argv)

    target = args.target.lower()
    os.makedirs(OUT_DIR, exist_ok=True)

    if target == "clean":
        removed = 0
        for path in glob.glob(os.path.join(OUT_DIR, "*")):
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
        print(f"cleaned transcripts_output ({removed} file(s))")
        return 0

    cids = load_cids()

    if target == "batch":
        results = []
        keys = sorted(TEAM_HANDLES)
        for i, key in enumerate(keys, 1):
            results.append(process_team(key, OUT_DIR, cids, args.max_age_days,
                                        args.check, args.any_title,
                                        args.cookies_from))
            print(f"    {results[-1]}")
            if i < len(keys) and not args.check:
                time.sleep(random.randint(8, 14))
        print("\n--- summary ---")
        for line in results:
            print(" ", line)
        good = sum(1 for r in results if r.startswith(("YES", "OK")))
        print(f"\n{good} of {len(results)} team(s) got a recent episode.")
        return 0 if good else 1

    if target not in TEAM_HANDLES:
        parser.error(f"unknown team '{target}'. Known: {', '.join(sorted(TEAM_HANDLES))}")

    print(process_team(target, OUT_DIR, cids, args.max_age_days,
                       args.check, args.any_title, args.cookies_from))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
