"""
Adds a "Use only this source, no season blending" checkbox near the
Baseline source selector. When checked (and real head-to-head games
exist), the blend call passes shrinkage_k=0, which makes the season
average contribute zero weight -- so the selected baseline (Last 5 /
Last 10 games vs. opponent) is used on its own, not blended.

When unchecked (default), behavior is unchanged -- still blended with
the season average using the normal shrinkage_k default.

Corrected version: fixes the blend-call indentation (16 spaces, not
12) that caused the first attempt to find 0 matches.

Usage:
    python3 patch_raw_baseline_toggle_v2.py
Run from the same folder as app.py.
"""

import pathlib

APP_PATH = pathlib.Path("app.py")

MARKER = "raw_baseline_input"

OLD_UI = '''    st.caption(
        "Head-to-head baselines use real games vs. this specific opponent -- more "
        "relevant if a player has a real history against this team, but based on a "
        "much smaller sample than a full season."
    )

    line1, line2, line3 = st.columns(3)'''

NEW_UI = '''    st.caption(
        "Head-to-head baselines use real games vs. this specific opponent -- more "
        "relevant if a player has a real history against this team, but based on a "
        "much smaller sample than a full season."
    )
    raw_baseline_input = st.checkbox(
        "Use only this source, no season blending",
        value=False,
    )
    st.caption(
        "By default, even a head-to-head baseline is blended with the season "
        "average for reliability (a handful of games can't fully override a "
        "full season on their own). Check this to use the selected baseline "
        "source on its own instead -- only applies when a head-to-head option "
        "is selected above."
    )

    line1, line2, line3 = st.columns(3)'''

OLD_CALC = """                baseline_stats, blend_weights = blend_baseline_stats(
                    season_stats, team_h2h=team_h2h_stats, team_h2h_n=team_h2h_n,
                    extra_sources=extra_sources,
                )"""

NEW_CALC = """                if raw_baseline_input and team_h2h_n > 0:
                    baseline_stats, blend_weights = blend_baseline_stats(
                        season_stats, shrinkage_k=0,
                        team_h2h=team_h2h_stats, team_h2h_n=team_h2h_n,
                        extra_sources=extra_sources,
                    )
                else:
                    baseline_stats, blend_weights = blend_baseline_stats(
                        season_stats, team_h2h=team_h2h_stats, team_h2h_n=team_h2h_n,
                        extra_sources=extra_sources,
                    )"""


def main():
    if not APP_PATH.exists():
        print("ERROR: app.py not found in current directory.")
        return

    text = APP_PATH.read_text()

    if MARKER in text:
        print("Already patched -- 'raw_baseline_input' found. No changes made.")
        return

    ui_count = text.count(OLD_UI)
    calc_count = text.count(OLD_CALC)
    print(f"UI insertion point found: {ui_count} occurrence(s) (expected 1)")
    print(f"Blend call found: {calc_count} occurrence(s) (expected 1)")

    if ui_count != 1 or calc_count != 1:
        print()
        print("Stopping -- counts don't match. Nothing was changed.")
        return

    text = text.replace(OLD_UI, NEW_UI, 1)
    text = text.replace(OLD_CALC, NEW_CALC, 1)
    APP_PATH.write_text(text)
    print()
    print("Patched successfully. New checkbox added below the Baseline source")
    print("caption. When checked with a head-to-head option selected, the")
    print("baseline uses only that source -- no season-average blending.")
    print("Restart Streamlit to see it.")


if __name__ == "__main__":
    main()
