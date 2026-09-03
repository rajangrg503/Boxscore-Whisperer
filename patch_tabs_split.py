#!/usr/bin/env python3
"""
patch_tabs_split.py

Visual polish patch 4/4 (the riskiest one, per session notes -- large
block re-indent). Splits the single-player predictor and "Predict a full
matchup" sections into st.tabs(["Single Player", "Full Matchup"]).

Uses LINE-NUMBER-based block replacement rather than exact-text matching,
per the established workflow: this is a 900+ line re-indent, and the
line-number approach is more robust against whitespace surprises for
edits this large.

ALL boundaries below were confirmed via `grep -n` on unique anchor
strings (not eyeballed from unlabeled sed output). A first draft of this
script had two boundary bugs caught during that verification pass:
  - TAB2A_END was one line short (missed the header caption's closing
    paren at line 2368).
  - DEFS_START was 3 lines too early (pointed into the middle of the
    header caption instead of the blank lines before `def get_team_roster`
    at line 2371).
Both are fixed below.

Confirmed boundaries (1-indexed):
  1582-2351  Tab 1 body: `with st.form("predictor_form"):` through the
             closing paren of the disclaimer st.caption(...) call.
  2352-2357  Blank lines + "# === Predict a full matchup ===" comment +
             st.divider() -- DROPPED (st.tabs() replaces the need for
             a visual divider/section header at this point).
  2358-2368  Tab 2 header: st.subheader(...) through st.caption(...)'s
             closing paren.
  2371-2424  get_team_roster() and predict_player_vs_opponent() defs
             (plus surrounding blank lines) -- UNTOUCHED, left at top
             level. No reason to re-indent function bodies for a purely
             visual change.
  2425-2486  Tab 2 form: `with st.form("matchup_form"):` through EOF.

Confirmed: no multi-line triple-quoted strings fall inside any span that
gets re-indented, so a blanket "+4 spaces per non-blank line" re-indent
is safe.

Idempotent: checks for the patch marker first; if already applied, skips.
Verifies total line count AND every anchor line via exact substring
match before writing anything; aborts with no changes if any check fails.
"""

import sys

TARGET_FILE = "app.py"
MARKER = "# patch_tabs_split"

EXPECTED_TOTAL_LINES = 2486

# 0-indexed boundaries derived from the confirmed 1-indexed line numbers above.
TAB1_START, TAB1_END = 1581, 2351      # file lines 1582-2351 inclusive
TAB2A_START, TAB2A_END = 2357, 2368    # file lines 2358-2368 inclusive
DEFS_START = 2370                       # file line 2371 (def get_team_roster)
TAB2B_START = 2424                      # file line 2425 (with st.form(...))

# (0-indexed line index, required substring) -- every one must match exactly.
ANCHOR_CHECKS = [
    (1581, 'with st.form("predictor_form"):'),
    (2349, 'not a trained predictive model'),
    (2350, ')'),
    (2357, 'st.subheader("Predict a full matchup")'),
    (2366, 'stays in the single-player tool for now'),
    (2367, ')'),
    (2370, 'def get_team_roster(team_id):'),
    (2390, 'def predict_player_vs_opponent(player_id, player_name, opponent_id):'),
    (2424, 'with st.form("matchup_form"):'),
]


def indent_block(lines, spaces=4):
    prefix = " " * spaces
    out = []
    for line in lines:
        if line.strip() == "":
            out.append(line)  # don't pad blank lines with trailing whitespace
        else:
            out.append(prefix + line)
    return out


def main():
    with open(TARGET_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    if MARKER in content:
        print("Already patched (marker found) -- skipping, no changes made.")
        return

    lines = content.splitlines(keepends=True)
    actual_total = len(lines)
    print(f"total lines found: {actual_total} (expected: {EXPECTED_TOTAL_LINES})")

    if actual_total != EXPECTED_TOTAL_LINES:
        print("ABORTING -- file line count doesn't match what was confirmed via `wc -l`.")
        print("The file has changed since boundaries were confirmed. Re-run the")
        print("grep -n boundary-confirmation commands and update this script before retrying.")
        sys.exit(1)

    mismatches = []
    for idx, expected_substring in ANCHOR_CHECKS:
        actual_line = lines[idx] if 0 <= idx < len(lines) else ""
        if expected_substring not in actual_line:
            mismatches.append((idx + 1, expected_substring, actual_line.rstrip()))

    if mismatches:
        print("ABORTING -- anchor line mismatch(es). No changes made.")
        for line_no, expected, actual in mismatches:
            print(f"  line {line_no}: expected to contain {expected!r}, found {actual!r}")
        sys.exit(1)

    print("All anchor checks passed. Building new file...")

    tab1_body = lines[TAB1_START:TAB1_END]
    tab2a_header = lines[TAB2A_START:TAB2A_END]
    defs_block = lines[DEFS_START:TAB2B_START]  # both defs, untouched, top-level
    tab2b_body = lines[TAB2B_START:]

    new_lines = []
    new_lines.extend(lines[:TAB1_START])  # everything before the form, unchanged
    new_lines.append('tab1, tab2 = st.tabs(["Single Player", "Full Matchup"])  ' + MARKER + '\n')
    new_lines.append('\n')
    new_lines.append('with tab1:\n')
    new_lines.extend(indent_block(tab1_body))
    new_lines.append('\n')
    new_lines.append('with tab2:\n')
    new_lines.extend(indent_block(tab2a_header))
    new_lines.append('\n')
    new_lines.extend(defs_block)  # untouched, top-level
    new_lines.append('\n')
    new_lines.append('with tab2:\n')
    new_lines.extend(indent_block(tab2b_body))

    new_content = "".join(new_lines)

    with open(TARGET_FILE, "w", encoding="utf-8") as f:
        f.write(new_content)

    print("Patched: single-player predictor and full-matchup predictor now split")
    print('into st.tabs(["Single Player", "Full Matchup"]).')
    print("Restart Streamlit (Ctrl+C then `streamlit run app.py`) to see the change.")
    print("")
    print("IMPORTANT: this was a large re-indent. Please test BOTH tabs thoroughly:")
    print("  - Single Player tab: run a prediction end-to-end, check Advanced options.")
    print("  - Full Matchup tab: run a matchup prediction end-to-end.")
    print("If anything looks broken, restore from git or your backup and let me know")
    print("what broke so I can fix the patch rather than re-guessing boundaries.")


if __name__ == "__main__":
    main()
