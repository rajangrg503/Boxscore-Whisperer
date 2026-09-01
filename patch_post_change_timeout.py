"""
Patches app.py to shorten the live LeagueDashTeamStats timeout (used for
post-roster-change opponent defense) from the default 30s to 5s. Same
idea as the Synergy fix -- this call reliably fails on Streamlit Cloud,
so there's no reason to wait 30 seconds to find that out. It already has
a graceful fallback message, this just makes it arrive faster.

Run this once, from the same folder as app.py.

Usage:
    python3 patch_post_change_timeout.py
"""

from pathlib import Path

TARGET = Path("app.py")

OLD = '''    def _fetch():
        stats = leaguedashteamstats.LeagueDashTeamStats(
            season=CURRENT_SEASON, measure_type_detailed_defense="Advanced",
            date_from_nullable=date_from_str,
        )
        df = stats.get_data_frames()[0]
        cols = ["TEAM_ID", "TEAM_NAME", "DEF_RATING", "PACE", "GP"]
        return df[[c for c in cols if c in df.columns]].copy()'''

NEW = '''    def _fetch():
        stats = leaguedashteamstats.LeagueDashTeamStats(
            season=CURRENT_SEASON, measure_type_detailed_defense="Advanced",
            date_from_nullable=date_from_str,
            timeout=5,
        )
        df = stats.get_data_frames()[0]
        cols = ["TEAM_ID", "TEAM_NAME", "DEF_RATING", "PACE", "GP"]
        return df[[c for c in cols if c in df.columns]].copy()'''


def main():
    text = TARGET.read_text()

    if NEW in text:
        print("Already patched -- no changes needed.")
        return

    if OLD not in text:
        print("Could not find the expected _fetch() block in app.py.")
        print("The file may differ from what this patch expects -- ")
        print("open app.py and edit it by hand instead. No changes made.")
        return

    TARGET.write_text(text.replace(OLD, NEW))
    print("Patched app.py successfully.")
    print("Post-roster-change team stats will now time out in 5s instead of 30s.")


if __name__ == "__main__":
    main()
