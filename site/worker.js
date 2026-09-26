// boxscorewhisperer.com — the landing page.
//
// The page itself lives in this repo at site/index.html, and this Worker
// serves it. It is deliberately not a copy: paste this in once and never
// again, because editing the page means editing the repo and pushing,
// not pasting twelve kilobytes into a Cloudflare text box and hoping the
// two copies stayed the same. The one that is live is the one on main.
//
// Cached at the edge for five minutes, so a push shows up quickly and a
// burst of traffic does not become a burst of requests to GitHub.

const SOURCE =
  "https://raw.githubusercontent.com/rajangrg503/Boxscore-Whisperer/main/site/index.html";

const ROBOTS = "User-agent: *\nAllow: /\nSitemap: https://boxscorewhisperer.com/sitemap.xml\n";

const SITEMAP =
  '<?xml version="1.0" encoding="UTF-8"?>\n' +
  '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' +
  "<url><loc>https://boxscorewhisperer.com/</loc><changefreq>weekly</changefreq></url>\n" +
  "</urlset>\n";

export default {
  async fetch(request) {
    const path = new URL(request.url).pathname;

    if (path === "/robots.txt") {
      return new Response(ROBOTS, {
        headers: { "content-type": "text/plain; charset=utf-8" },
      });
    }
    if (path === "/sitemap.xml") {
      return new Response(SITEMAP, {
        headers: { "content-type": "application/xml; charset=utf-8" },
      });
    }

    const upstream = await fetch(SOURCE, { cf: { cacheTtl: 300, cacheEverything: true } });

    // GitHub being down must not take the domain down with it.
    if (!upstream.ok) {
      return new Response(
        "<!doctype html><meta charset=utf-8><title>Boxscore Whisperer</title>" +
          "<body style=\"background:#0a0d12;color:#e8edf5;font:16px system-ui;text-align:center;padding:80px 20px\">" +
          "<h1>Boxscore Whisperer</h1><p>The page is briefly unavailable. " +
          "<a style=\"color:#22c55e\" href=\"https://boxscorewhisperer.com/app\">Open the projector</a></p>",
        { status: 200, headers: { "content-type": "text/html; charset=utf-8" } }
      );
    }

    return new Response(upstream.body, {
      headers: {
        "content-type": "text/html; charset=utf-8",
        "cache-control": "public, max-age=300",
        "x-content-type-options": "nosniff",
        "referrer-policy": "strict-origin-when-cross-origin",
      },
    });
  },
};
