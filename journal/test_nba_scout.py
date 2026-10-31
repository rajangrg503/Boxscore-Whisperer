#!/usr/bin/env python3
"""Offline tests for nba_scout.py. No network, so these run anywhere.

    python3 journal/test_nba_scout.py

What they cover is exactly the four bugs the rewrite fixed, plus the
wrong-channel guard added afterwards. What they CANNOT cover is whether
YouTube answers -- youtube.com is refused by the proxy from both the
cloud container and the device VM (403 on CONNECT, 31 Oct 2026), so the
live paths only run on the Mac. Use `--check` there.
"""
import datetime as dt
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nba_scout as S                                        # noqa: E402

FAILED = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}: got {got!r}, want {want!r}")
        FAILED.append(name)


def days_ago(n):
    return dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=n)


print("pick(): the date rule")
recent = {"id": "new", "title": "Thunder vs Rockets postgame reaction",
          "published": days_ago(1)}
stale = {"id": "old", "title": "Thunder vs Lakers postgame reaction",
         "published": days_ago(400)}
undated = {"id": "nodate", "title": "Thunder postgame reaction",
           "published": None}
offtopic = {"id": "off", "title": "Our top 10 Thunder jerseys of all time",
            "published": days_ago(1)}

check("undated entry is rejected", S.pick([undated], 7)[0], None)
check("400-day-old entry is rejected", S.pick([stale], 7)[0], None)
check("recent game video is taken", S.pick([recent, stale], 7)[0]["id"], "new")
check("non-game title is rejected", S.pick([offtopic], 7)[0], None)
check("--any-title overrides the title filter",
      S.pick([offtopic], 7, any_title=True)[0]["id"], "off")

print("\nlooks_like_game_coverage()")
check("postgame", S.looks_like_game_coverage("Postgame: Thunder beat Rockets"), True)
check("jersey listicle", S.looks_like_game_coverage("Top 10 jerseys ever"), False)

print("\nentries_from_feed(): parsing and the wrong-channel guard")
ATOM_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns="http://www.w3.org/2005/Atom">
  <author><name>Thunder Talk</name></author>
  <entry>
    <yt:videoId>abc123</yt:videoId>
    <title>Thunder vs Rockets postgame</title>
    <published>2026-10-30T04:15:00+00:00</published>
  </entry>
</feed>"""


class FakeResponse:
    content = ATOM_XML

    def raise_for_status(self):
        return None


S.requests = types.SimpleNamespace(get=lambda *a, **k: FakeResponse())
entries = S.entries_from_feed("UCxxxxxxxxxxxxxxxxxxxxxx")
check("one entry parsed", len(entries), 1)
check("video id", entries[0]["id"], "abc123")
check("title", entries[0]["title"], "Thunder vs Rockets postgame")
check("published parsed", entries[0]["published"].year, 2026)
check("channel name carried through", entries[0]["channel"], "Thunder Talk")

print("\nthe four bugs are gone from the CODE")
# The docstring deliberately NAMES all four bugs so the next reader knows
# what was wrong. A plain substring search over the file therefore finds
# them in the prose and fails. Walk the AST and look at live string
# literals only, skipping every docstring.
import ast
src_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nba_scout.py")
tree = ast.parse(open(src_path, encoding="utf-8").read())
docstrings = set()
for node in ast.walk(tree):
    body = getattr(node, "body", None)
    if (isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        docstrings.add(id(body[0].value))
live = [n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and id(n) not in docstrings]
check("no relevance-search fallback in code",
      any("ytsearch" in v for v in live), False)
check("no 'sublangs' key in code", any(v == "sublangs" for v in live), False)
check("no entity markup in code",
      any("\u3010" in v for v in live), False)
check("subtitleslangs IS the option used",
      any(v == "subtitleslangs" for v in live), True)
check("pistons key is clean", any(v == "pistons" for v in live), True)

print()
if FAILED:
    print(f"{len(FAILED)} FAILED: {', '.join(FAILED)}")
    raise SystemExit(1)
print("all offline tests pass -- the network paths still need `--check` on the Mac")
