"""
Diagnostic for app.py: finds variables that are set only inside the
"if submitted:" block (i.e. only exist on the very first run after
clicking Predict statline) but are also referenced later in the script,
in the section that runs on EVERY rerun (including reruns triggered by
other widgets). Those are exactly the shape of bug we already found
twice: NameError on h2h_cutoff, then post_change_thin_sample.

This is a heuristic, not a guarantee -- it flags CANDIDATES for you to
check by eye, not confirmed bugs. It will have some false positives
(e.g. loop variables, or names re-assigned locally before use in the
later section). Read the flagged list; anything that looks like a real
computed value (not a throwaway loop variable) is worth checking.

Run this once, from the same folder as app.py.

Usage:
    python3 find_missing_session_state_vars.py
"""

import re
from pathlib import Path

TARGET = Path("app.py")


def main():
    lines = TARGET.read_text().splitlines()

    # Find the boundaries of the three sections we care about.
    submitted_start = None
    save_dict_start = None
    save_dict_end = None
    restore_start = None

    for i, line in enumerate(lines):
        if submitted_start is None and re.match(r"\s*if submitted:", line):
            submitted_start = i
        if save_dict_start is None and 'st.session_state["results"] = {' in line:
            save_dict_start = i
        if save_dict_start is not None and save_dict_end is None and i > save_dict_start:
            if line.strip() == "}":
                save_dict_end = i
        if restore_start is None and 'if "results" in st.session_state:' in line:
            restore_start = i

    if None in (submitted_start, save_dict_start, save_dict_end, restore_start):
        print("Could not locate all expected sections -- app.py may have")
        print("changed structure since this script was written. Aborting.")
        return

    # 1. Variables assigned anywhere between "if submitted:" and the end
    #    of the save dict -- these only exist on the first run.
    submission_block = lines[submitted_start:save_dict_end]
    assign_pattern = re.compile(r"^\s{4,}([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*[^=]")
    submitted_vars = set()
    for line in submission_block:
        m = assign_pattern.match(line)
        if m:
            submitted_vars.add(m.group(1))

    # 2. Keys already saved into st.session_state["results"] (these are safe).
    key_pattern = re.compile(r'"\w+":\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*,')
    saved_vars = set()
    for line in lines[save_dict_start:save_dict_end]:
        m = key_pattern.search(line)
        if m:
            saved_vars.add(m.group(1))

    # 3. Names referenced anywhere after the restore block starts --
    #    this is the code that runs on EVERY rerun.
    restore_block_text = "\n".join(lines[restore_start:])
    name_pattern = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b")

    at_risk = []
    for var in sorted(submitted_vars - saved_vars):
        # Skip if it's re-assigned locally within the restore block
        # (e.g. as a loop variable or widget default) -- search for an
        # assignment to it there, which would make it safe.
        reassigned = re.search(rf"^\s*{re.escape(var)}\s*=", restore_block_text, re.MULTILINE)
        if reassigned:
            continue
        # Flag it if it's referenced at all in the restore block.
        if re.search(rf"\b{re.escape(var)}\b", restore_block_text):
            at_risk.append(var)

    if not at_risk:
        print("No further at-risk variables found. Looks clean!")
        return

    print(f"Found {len(at_risk)} variable(s) that may need the same fix:\n")
    for var in at_risk:
        print(f"  - {var}")
    print("\nFor each one: check if it's a real computed value used later")
    print("(likely needs adding to the save dict + restore block), or a")
    print("false positive (loop variable, unrelated name, etc).")


if __name__ == "__main__":
    main()
