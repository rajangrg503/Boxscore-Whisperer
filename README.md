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
