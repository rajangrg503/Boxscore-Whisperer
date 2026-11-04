#!/usr/bin/env python3
"""Open the live app in a real browser so Community Cloud never puts it to sleep.

    python tools/keep_awake.py
    python tools/keep_awake.py --url https://boxscore-whisperer-9t5hpcdqyt7jmlsdbntljv.streamlit.app/

WHY THIS EXISTS
Streamlit Community Cloud puts an app to sleep when nobody has opened it
for a while. A sleeping app shows a "Zzzz" page with a wake-up button,
and a tab that was already open can sit on "Your app is in the oven"
for half an hour without ever showing that button (karma, 1 Nov 2026).
Whoever finds it asleep is the visitor we least want to lose.

A plain HTTP request is NOT a visit. curl gets the static shell back
with a 200 and the app goes to sleep anyway, because what counts as
traffic is a browser session that connects to the app over its
websocket. So this drives a real headless Chromium:

  1. Open the URL.
  2. If the sleep page is showing, press the wake-up button.
  3. Wait until the app itself has rendered -- the title text in the
     app's own frame, not just the hosting shell around it.

It exits non-zero if the app is not up within the time limit, so the
GitHub Actions run goes red and GitHub's failure email is the alert.

Runs from .github/workflows/keep-awake.yml every few hours. Costs
nothing: Actions minutes are free on a public repository.
"""
import argparse
import sys
import time

from playwright.sync_api import sync_playwright

DEFAULT_URL = "https://boxscore-whisperer-9t5hpcdqyt7jmlsdbntljv.streamlit.app/"
WAKE_TEXT = "get this app back up"       # the button on the sleep page
APP_TEXT = "Boxscore Whisperer"          # the app's own heading
RELOAD_EVERY = 75                         # seconds on one load before reloading


def app_is_rendered(page):
    """True once the heading is visible inside any frame. The app runs in
    an iframe under the hosting page, so the top document alone says
    nothing about whether the app itself loaded."""
    for frame in page.frames:
        try:
            if frame.get_by_text(APP_TEXT, exact=False).first.is_visible(timeout=500):
                return True
        except Exception:                                   # noqa: BLE001
            continue
    return False


def press_wake_button(page):
    for frame in page.frames:
        try:
            button = frame.get_by_role("button", name=WAKE_TEXT)
            if button.count() and button.first.is_visible(timeout=500):
                button.first.click()
                return True
        except Exception:                                   # noqa: BLE001
            continue
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--timeout", type=int, default=540, help="seconds to wait for the app")
    args = ap.parse_args(argv)

    # The app itself, without the hosting shell around it. 3-4 Nov 2026:
    # two runs sat five minutes on the shell's spinner and failed, while
    # the same URL opened by hand came up once reloaded, and /~/+/ rendered
    # in ten seconds. A tab stuck on the spinner never recovers on its own,
    # so the page is reloaded every RELOAD_EVERY seconds, alternating
    # between the shell and the app's own path.
    direct = args.url.rstrip("/") + "/~/+/"
    urls = [args.url, direct]

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        woke = False
        attempt = 0
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            url = urls[attempt % len(urls)]
            attempt += 1
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            except Exception as exc:                        # noqa: BLE001
                print(f"load failed ({type(exc).__name__}) on {url}")
                continue
            reload_at = time.time() + RELOAD_EVERY
            while time.time() < min(reload_at, deadline):
                if app_is_rendered(page):
                    # Stay connected a little: the visit should look like one.
                    page.wait_for_timeout(20_000)
                    print(f"awake{' (woken from sleep)' if woke else ''} on try {attempt}: {url}")
                    browser.close()
                    return 0
                if not woke and press_wake_button(page):
                    woke = True
                    print("app was asleep -- pressed the wake-up button")
                    reload_at = time.time() + args.timeout    # waking takes a while
                page.wait_for_timeout(5_000)
            print(f"not rendered after {RELOAD_EVERY}s on {url}; reloading")

        print(f"NOT UP after {args.timeout}s and {attempt} loads: {args.url}", file=sys.stderr)
        for frame in page.frames:
            try:
                print(f"  frame {frame.url[:90]}: {frame.inner_text('body')[:200]!r}",
                      file=sys.stderr)
            except Exception:                               # noqa: BLE001
                continue
        browser.close()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
