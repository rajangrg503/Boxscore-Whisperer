#!/usr/bin/env python3
"""Write a plain-English note for each saved prediction, using a local
Llama 3.2 model through Ollama. Runs on your Mac, never on Streamlit.

    python3 tools/whisper.py --dry-run          # print notes, save nothing
    python3 tools/whisper.py                    # fill in every missing note
    python3 tools/whisper.py --limit 5          # just a few, to try it
    python3 tools/whisper.py --redo             # rewrite notes that exist

Needs: the Ollama app running, `ollama pull llama3.2`, and
`pip install ollama` (local only -- it is deliberately NOT in
requirements.txt, because the deployed app never calls a model).

To write into the production log, export DATABASE_URL first, exactly
as the app sees it. Without it this works on the local
prediction_log.csv, like everything else in the repo.

WHAT IT WRITES
  whisper        -- pending rows: why the projection sits where it does.
  whisper_recap  -- resolved rows: how the game compared with the range.

WHY THE MODEL NEVER SEES THE RAW ROW
A 3B model will happily invent an injury, a streak or a stat. So the
row is first reduced, in plain Python, to a short list of facts the
engine actually produced (build_pregame_facts / build_recap_facts).
The model is only asked to phrase those facts. Then check_note()
rejects any note that contains a number not in the facts, uses
betting language, or runs long. A rejected note is retried once and
otherwise skipped -- an empty cell is better than a wrong sentence on
a public page.

WHY THE MODEL RUNS OUTSIDE THE LOCK
Generating is slow (seconds per row). Holding tracker._locked() that
long would block every visitor's "Save to tracker". So notes are
generated from an unlocked read, then written in one short locked
read-modify-write that only fills cells which are still empty (or, with
--redo, rows that still exist). A row deleted or re-saved in between
is simply left alone.
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from engine import tracker  # noqa: E402
from engine.adjustments.registry import LAYER_DISPLAY, NEVER_APPLIED_BY_DESIGN  # noqa: E402

DEFAULT_MODEL = "llama3.2"
MAX_WORDS = 45
# Headline stats only: a note listing nine ranges is a table, not a note.
HEADLINE_STATS = [("PTS", "points"), ("REB", "rebounds"), ("AST", "assists")]
LAYER_LABELS = dict(LAYER_DISPLAY)

# Words that would turn a projection note into a tip. Kept here, in
# one list, so the prompt and the checker can never disagree.
BANNED_WORDS = [
    "bet", "bets", "betting", "wager", "lock", "locks", "parlay", "odds",
    "over", "under", "sportsbook", "value", "play", "fade", "hammer",
    "guaranteed", "sure thing", "smash",
]

SYSTEM_PROMPT = f"""You write one short note for an NBA stat-projection app.
Rules:
- Use ONLY the facts given. Do not add injuries, streaks, history or any number that is not in the facts.
- 1 or 2 sentences, at most {MAX_WORDS} words. Plain, calm English.
- Describe, never advise. Never use these words: {", ".join(BANNED_WORDS)}.
- Do not start with the player's full name; the app already shows it.
- Output the note only. No title, no quotes, no bullet points."""


# ---------------------------------------------------------------- facts

def _num(v):
    """A clean number for a fact line, or None. 12.0 -> '12', 12.5 -> '12.5'."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return str(int(f)) if f == int(f) else f"{f:.1f}"


def _layer_moves(layers_json):
    """[(label, 'up' | 'down')] for every adjustment that actually fired.

    Direction comes from the multipliers the engine logged, averaged
    over stats, so a layer that nudged points up and rebounds down by
    the same amount is reported as neither."""
    try:
        layers = json.loads(layers_json) if isinstance(layers_json, str) else {}
    except ValueError:
        return []
    moves = []
    for name, _label in LAYER_DISPLAY:
        info = layers.get(name)
        if not info or not info.get("applied") or name in NEVER_APPLIED_BY_DESIGN:
            continue
        values = [v for v in (info.get("value") or {}).values() if isinstance(v, (int, float))]
        if not values:
            continue
        avg = sum(values) / len(values)
        if abs(avg - 1.0) < 0.01:
            continue
        moves.append((LAYER_LABELS[name], "up" if avg > 1 else "down"))
    return moves


def build_pregame_facts(row):
    """Facts for a pending row, or None if there is nothing worth saying."""
    facts = [f"Opponent: {row.get('opponent_full_name')}"]
    for col, label in HEADLINE_STATS:
        lo, mid, hi = (_num(row.get(f"{col}_{k}")) for k in ("low", "mid", "high"))
        if mid is None:
            continue
        line = f"Projected {label}: {mid} (likely range {lo} to {hi})"
        base = _num(row.get(f"{col}_base"))
        if base is not None and base != mid:
            line += f", from a starting baseline of {base}"
        facts.append(line)
    moves = _layer_moves(row.get("layers_json"))
    if moves:
        facts.append("Adjustments applied: " + "; ".join(f"{lbl} moved it {d}" for lbl, d in moves))
    else:
        facts.append("No matchup adjustments changed this projection; it is the player's baseline.")
    return facts if len(facts) > 2 else None


def build_recap_facts(row):
    """Facts for a resolved row, or None if the actuals are missing."""
    facts = [f"Opponent: {row.get('opponent_full_name')}"]
    for col, label in HEADLINE_STATS:
        actual = _num(row.get(f"{col}_actual"))
        lo, hi = _num(row.get(f"{col}_low")), _num(row.get(f"{col}_high"))
        if actual is None or lo is None:
            continue
        hit = row.get(f"{col}_hit")
        verdict = "inside" if str(hit).strip().lower() in {"true", "1", "1.0"} else "outside"
        facts.append(f"Actual {label}: {actual}, {verdict} the projected range of {lo} to {hi}")
    if len(facts) == 1:
        return None
    for name, label in (("min", "Minutes"), ("fg_pct", "Field-goal %")):
        act, avg = _num(row.get(f"{name}_actual")), _num(row.get(f"{name}_season_avg"))
        if act is not None and avg is not None:
            facts.append(f"{label}: {act} this game vs {avg} season average")
    names = row.get("key_teammate_out_names")
    if isinstance(names, str) and names.strip():
        facts.append(f"Key teammates out: {names.strip()}")
    return facts


# ---------------------------------------------------------------- checking

_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def check_note(note, facts, max_words=MAX_WORDS):
    """None if the note is safe to publish, else the reason it isn't."""
    if not note:
        return "empty"
    if len(note.split()) > max_words:
        return "too long"
    lowered = note.lower()
    for word in BANNED_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            return f"used banned word '{word}'"
    allowed = set(_NUMBER.findall(" ".join(facts)))
    for n in _NUMBER.findall(note):
        trimmed = n.rstrip("0").rstrip(".") if "." in n else n  # "12.0" -> "12"
        if n not in allowed and trimmed not in allowed:
            return f"number {n} is not in the facts"
    return None


def _clean(text):
    text = (text or "").strip().strip('"').strip()
    return re.sub(r"\s+", " ", text)


# ---------------------------------------------------------------- model

def make_generator(model, system_prompt=SYSTEM_PROMPT, max_tokens=120):
    """Returns generate(facts, temperature) -> str. Fails early, with a
    readable message, if Ollama isn't installed, running or pulled."""
    try:
        import ollama
    except ImportError:
        sys.exit("The `ollama` package isn't installed. Run: pip install ollama")

    try:
        ollama.show(model)
    except ollama.ResponseError:
        sys.exit(f"Model '{model}' isn't pulled yet. Run: ollama pull {model}")
    except Exception:
        sys.exit("Can't reach Ollama. Open the Ollama app (or run `ollama serve`) and try again.")

    def generate(facts, temperature):
        response = ollama.chat(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "Facts:\n- " + "\n- ".join(facts)},
            ],
            options={"temperature": temperature, "num_predict": max_tokens},
        )
        return _clean(response["message"]["content"])

    return generate


# Split after . ! ? followed by a capital -- but not after "Jr.", "Sr."
# or a lone initial ("J. Smith"), which end in a full stop mid-sentence.
_SENTENCE_END = re.compile(r"(?<!\bJr)(?<!\bSr)(?<!\b[A-Z])([.!?])\s+(?=[A-Z\"'])")


def sentences(text):
    parts = _SENTENCE_END.split(text or "")
    # split() keeps each captured terminator as its own item; stitch back.
    out = []
    for i in range(0, len(parts), 2):
        out.append(parts[i] + (parts[i + 1] if i + 1 < len(parts) else ""))
    return [p.strip() for p in out if p.strip()]


def trimmed_versions(note):
    """The note, then the note with its last sentence dropped, and so on
    down to one sentence. A cut-off final fragment (the model hit its token
    limit mid-sentence) is dropped up front, since it is never usable."""
    parts = sentences(note)
    if parts and parts[-1][-1] not in ".!?":
        parts = parts[:-1]
    return [" ".join(parts[:n]) for n in range(len(parts), 0, -1)]


def write_note(facts, generate, max_words=MAX_WORDS, extra_check=None):
    """(note, None) or (None, reason). Two tries: normal, then fully greedy.
    extra_check(note) -> None or a reason, for caller-specific rules.

    A draft that fails is not thrown away whole: its trailing sentences are
    dropped one at a time and each shorter version is checked the same way.
    The model writes its most important sentence first, so a draft that
    ran long, or went wrong in its last sentence, usually still has a good
    line at the front. Nothing is ever added or reworded -- only cut."""
    first_reason = None
    for temperature in (0.3, 0.0):
        draft = generate(facts, temperature)
        for note in trimmed_versions(draft) or [draft]:
            reason = check_note(note, facts, max_words)
            if reason is None and extra_check is not None:
                reason = extra_check(note)
            if reason is None:
                return note, None
            if first_reason is None:
                first_reason = reason
    return None, first_reason


# ---------------------------------------------------------------- run

def _is_blank(v):
    return v is None or (isinstance(v, float) and v != v) or str(v).strip() == ""


def plan(df, redo=False):
    """[(row_id, column, facts)] for every row that needs a note."""
    jobs = []
    for _, row in df.iterrows():
        if tracker.is_hypothetical(row.get("hypothetical")):
            continue  # a what-if isn't something to narrate as fact
        status = row.get("status")
        if status == "pending":
            column, facts = "whisper", build_pregame_facts(row)
        elif status == "resolved":
            column, facts = "whisper_recap", build_recap_facts(row)
        else:
            continue
        if facts and (redo or _is_blank(row.get(column))):
            jobs.append((row["id"], column, facts))
    return jobs


def save_notes(notes, redo=False):
    """notes: {(row_id, column): text}. One short locked write."""
    written = 0
    with tracker._locked():
        df = tracker.load_prediction_log()
        for col in ("whisper", "whisper_recap"):
            df[col] = df[col].astype(object)
        index_by_id = {rid: i for i, rid in df["id"].items()}
        for (rid, column), text in notes.items():
            i = index_by_id.get(rid)
            if i is None:
                continue  # deleted since we read it
            if not redo and not _is_blank(df.at[i, column]):
                continue  # someone else filled it meanwhile
            df.at[i, column] = text
            written += 1
        tracker.save_prediction_log(df)
    return written


def main(argv=None, generate=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--limit", type=int, default=None, help="only do this many rows")
    ap.add_argument("--redo", action="store_true", help="rewrite notes that already exist")
    ap.add_argument("--dry-run", action="store_true", help="print notes, save nothing")
    args = ap.parse_args(argv)

    backend = "Postgres (DATABASE_URL)" if tracker.log_store.using_database() else tracker.LOG_PATH
    print(f"Prediction log: {backend}")

    df = tracker.load_prediction_log()
    jobs = plan(df, redo=args.redo)[: args.limit]
    if not jobs:
        print("Nothing to write -- every eligible row already has a note.")
        return 0

    generate = generate or make_generator(args.model)
    notes, skipped = {}, 0
    for n, (rid, column, facts) in enumerate(jobs, 1):
        note, reason = write_note(facts, generate)
        kind = "recap" if column == "whisper_recap" else "pre-game"
        if note is None:
            skipped += 1
            print(f"[{n}/{len(jobs)}] {rid} {kind}: SKIPPED ({reason})")
            continue
        notes[(rid, column)] = note
        print(f"[{n}/{len(jobs)}] {rid} {kind}: {note}")

    if args.dry_run:
        print(f"\nDry run: {len(notes)} notes written, {skipped} skipped, nothing saved.")
        return 0
    written = save_notes(notes, redo=args.redo)
    print(f"\nSaved {written} notes ({skipped} skipped).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
