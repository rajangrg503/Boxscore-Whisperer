"""
Patches app.py to shorten the live Synergy API call timeout from the
default 30s to 5s. On Streamlit Cloud this call always fails (blocked
host), so there's no reason to wait 30 seconds to find that out --
failing fast means the manual-estimate fallback kicks in much sooner.

Run this once, from the same folder as app.py.

Usage:
    python3 patch_synergy_timeout.py
"""

from pathlib import Path

TARGET = Path("app.py")

OLD = '''    def _fetch():
        data = synergyplaytypes.SynergyPlayTypes(
            league_id="00",
            per_mode_simple="PerGame",
            player_or_team_abbreviation="T",
            season_type_all_star="Regular Season",
            season=season,
            type_grouping_nullable="defensive",
            play_type_nullable=play_type,
        )
        return data.get_data_frames()[0]'''

NEW = '''    def _fetch():
        data = synergyplaytypes.SynergyPlayTypes(
            league_id="00",
            per_mode_simple="PerGame",
            player_or_team_abbreviation="T",
            season_type_all_star="Regular Season",
            season=season,
            type_grouping_nullable="defensive",
            play_type_nullable=play_type,
            timeout=5,
        )
        return data.get_data_frames()[0]'''


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
    print("The Synergy live call will now time out in 5s instead of 30s.")


if __name__ == "__main__":
    main()
