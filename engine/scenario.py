"""A typed scenario, turned into the controls the engine already has.

WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT
It reads a sentence a reader typed and fills in the Advanced options.
It does NOT produce a number, and it does not invent an adjustment for
anything the engine cannot already measure.

That restraint is the whole design. The app's claim is that every
adjustment is shown and the working can be checked. A box that quietly
turned "Shai will be double teamed" into +1.4 points would be the
opposite: an unmeasured number, produced by something nobody can
inspect, sitting inside the one product in this market whose pitch is
that its numbers can be inspected.

So the output has two halves, and the second is not an apology:

    applied     the clauses that map to a measured layer
    unmatched   every other clause, echoed back with the reason

A reader who types six clauses and sees one applied has learned
something true about the model. Hiding that would be the dishonest
version.

WHY NO LANGUAGE MODEL
Because the split above falls out of the problem rather than being
imposed on it. The clauses a keyword parser can handle -- a player's
name plus "out", "injured", "resting" -- are precisely the ones the
engine can act on. The clauses that would need real language
understanding ("double teamed", "plays off ball", "handles the ball")
are precisely the ones it cannot act on whatever it understood, because
there is no usage layer and no defensive-attention layer to put them
in. Those only need echoing back verbatim.

A model would cost money per call, be non-deterministic, and could not
be tested the way the rest of this repository is. It would buy
nothing.

THE HAZARD THIS FILE IS MOSTLY ABOUT: THE WRONG PLAYER
Oklahoma City roster both Jalen Williams and Jaylin Williams. The
league has several Joneses and several Johnsons. A parser that resolves
"Williams is out" to whichever it found first would silently mark the
wrong man out, and the projection would be confidently wrong with no
sign anything went astray -- the exact failure this codebase keeps
finding in other shapes.

So an ambiguous name is NEVER guessed. It goes to unmatched, naming the
people it could have meant, and the reader picks. Under-applying is
recoverable; applying to the wrong player is not.
"""

import re
import unicodedata

# Phrases that mean the player is unavailable. Checked as whole words
# against the normalised clause.
OUT_SIGNALS = (
    "out", "injured", "injury", "sidelined", "scratched", "absent",
    "resting", "rest", "sitting", "sits", "benched", "suspended",
    "ruled out", "not playing", "won't play", "wont play",
    "will not play", "doesn't play", "doesnt play", "misses", "missing",
    "dnp", "inactive",
)

# Phrases that mean the player is newly available to this lineup.
ARRIVING_SIGNALS = (
    "returns", "returning", "is back", "back from", "debut", "debuts",
    "just signed", "just joined", "newly signed", "traded to", "activated",
)

# A denial of an out-signal. Checked BEFORE the signals, because
# "not playing" is an out-signal and "not out" is its opposite; a single
# pass over the words cannot tell them apart.
DENIALS = (
    r"\bnot\s+out\b", r"\bisn'?t\s+out\b", r"\bnot\s+injured\b",
    r"\bisn'?t\s+injured\b", r"\bno\s+longer\s+out\b",
    r"\bnot\s+sitting\b", r"\bnot\s+resting\b", r"\bnot\s+missing\b",
)

# States we cannot act on, because the layer takes a player in or out
# and nothing in between. Named separately so the reason can say so.
UNDECIDED = ("questionable", "doubtful", "game-time decision",
             "game time decision", "probable", "limited minutes",
             "minutes restriction", "on a minutes limit")

# Where one clause ends and the next begins. "and", "but" and "so" are
# included because people write the whole night as one sentence:
# "Chet is out and Shai gets double teamed so SGA plays off ball".
CLAUSE_BREAK = re.compile(
    r"[.;\n]|,\s*|\s+\band\b\s+|\s+\bbut\b\s+|\s+\bso\b\s+|\s+\bbecause\b\s+",
    re.IGNORECASE,
)

# The multiselects this fills cap out at five.
MAX_PER_CONTROL = 5


# Letters NFKD will not take apart, because they are letters in their
# own right rather than a base plus an accent. Without these, the
# stripping below DELETES them: Nikola Đurišić folded to "urisic" and
# stopped matching anyone who typed "Durisic".
TRANSLITERATE = str.maketrans({
    "Đ": "D", "đ": "d", "Ø": "O", "ø": "o",
    "Ł": "L", "ł": "l", "ß": "ss", "Æ": "AE",
    "æ": "ae", "Œ": "OE", "œ": "oe", "Þ": "Th",
    "þ": "th", "İ": "I", "ı": "i",
})


def _fold(text):
    """Lower case, accents stripped, punctuation to spaces.

    Accents matter: the roster carries Nikola Đurišić and Luka Dončić,
    and nobody types those."""
    plain = str(text).translate(TRANSLITERATE)
    decomposed = unicodedata.normalize("NFKD", plain)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9' ]+", " ", stripped.lower()).strip()


def _initials(full_name):
    parts = [p for p in _fold(full_name).split() if p]
    return "".join(p[0] for p in parts)


def clauses(text):
    """The typed scenario, split into the things it asserts."""
    parts = CLAUSE_BREAK.split(str(text or ""))
    return [p.strip() for p in parts if p and p.strip()]


def _candidates(clause, roster):
    """Everyone on `roster` this clause could be naming.

    `roster` is (player_id, full_name) pairs. Returns the matches with
    the strongest evidence only: a full-name hit beats a surname hit,
    so "Jalen Williams is out" resolves even though "Williams" alone
    would not.
    """
    folded = _fold(clause)
    words = set(folded.split())

    full, partial = [], []
    for player_id, name in roster:
        folded_name = _fold(name)
        if folded_name and folded_name in folded:
            full.append((player_id, name))
            continue
        parts = [p for p in folded_name.split() if len(p) > 1]
        if any(p in words for p in parts):
            partial.append((player_id, name))
            continue
        # "SGA". Only as initials of a real name, never a wordlist --
        # a hand-kept nickname table would drift the day someone is
        # traded.
        letters = _initials(name)
        if len(letters) >= 2 and letters in words:
            partial.append((player_id, name))

    return full or partial


def _state(clause):
    """out, arriving, undecided, denied, or None."""
    folded = _fold(clause)
    padded = f" {folded} "

    for pattern in DENIALS:
        if re.search(pattern, folded):
            return "denied"
    for phrase in UNDECIDED:
        if f" {_fold(phrase)} " in padded:
            return "undecided"
    for phrase in OUT_SIGNALS:
        if f" {_fold(phrase)} " in padded:
            return "out"
    for phrase in ARRIVING_SIGNALS:
        if f" {_fold(phrase)} " in padded:
            return "arriving"
    return None


def _names(matches):
    return ", ".join(name for _pid, name in matches)


def parse(text, teammates=(), opponents=(), subject=None):
    """Fill the controls from a typed scenario.

    `teammates` and `opponents` are (player_id, full_name) pairs -- the
    two rosters, NOT the whole league. A missing teammate has to be a
    teammate, and narrowing the candidates is also what makes the names
    resolvable at all: league-wide, half the surnames are ambiguous.

    `subject` is the (player_id, full_name) being projected, and the page
    leaves him out of `teammates` because he cannot be his own missing
    teammate. Pass him here anyway: without it, "SGA is out" comes back
    as "no player from either roster named here", which is true of the
    lists and misleading about the world -- he is on the roster, he is
    the person being projected. Optional; omitting it only costs that
    one message its precision.

    Returns a dict. `out_teammates`, `out_opponents` and `arriving` are
    ready for the widgets; `applied` and `unmatched` are what the page
    shows the reader.
    """
    teammates = list(teammates)
    opponents = list(opponents)
    subject_list = [subject] if subject else []

    out_teammates, out_opponents, arriving = [], [], None
    applied, unmatched = [], []

    for clause in clauses(text):
        state = _state(clause)
        here = _candidates(clause, teammates)
        there = _candidates(clause, opponents)
        matches = here + there

        if state is None:
            unmatched.append({
                "clause": clause,
                "reason": "no measured layer for this",
            })
            continue

        if not matches:
            if _candidates(clause, subject_list):
                unmatched.append({
                    "clause": clause,
                    "reason": (f"{subject[1]} is the player being projected -- "
                               f"pick somebody else to project if he sits"),
                })
            else:
                unmatched.append({
                    "clause": clause,
                    "reason": "no player from either roster named here",
                })
            continue

        if len(matches) > 1:
            # Never guessed. Jalen Williams and Jaylin Williams are
            # teammates; picking one would be wrong half the time and
            # silent either way.
            unmatched.append({
                "clause": clause,
                "reason": f"could mean {_names(matches)} -- pick one yourself",
            })
            continue

        player_id, name = matches[0]

        if state == "undecided":
            unmatched.append({
                "clause": clause,
                "reason": (f"{name} is either in or out as far as the model "
                           f"is concerned -- there is no half-available"),
            })
            continue

        if state == "denied":
            unmatched.append({
                "clause": clause,
                "reason": f"reads as a denial, so {name} was left in",
            })
            continue

        if state == "arriving":
            if here and arriving is None:
                arriving = player_id
                applied.append({"clause": clause, "control": "New teammate arriving",
                                "player": name})
            else:
                unmatched.append({
                    "clause": clause,
                    "reason": ("only one arriving teammate at a time"
                               if here else
                               f"{name} is on the other team, and there is no "
                               f"layer for an opponent arriving"),
                })
            continue

        # state == "out"
        target = out_teammates if here else out_opponents
        control = "Missing teammates" if here else "Missing opponent players"
        if player_id in target:
            continue
        if len(target) >= MAX_PER_CONTROL:
            unmatched.append({
                "clause": clause,
                "reason": f"more than {MAX_PER_CONTROL} players marked out",
            })
            continue
        target.append(player_id)
        applied.append({"clause": clause, "control": control, "player": name})

    return {
        "out_teammates": out_teammates,
        "out_opponents": out_opponents,
        "arriving": arriving,
        "applied": applied,
        "unmatched": unmatched,
    }


def merge(parsed, manual_teammates=(), manual_opponents=(), name_of=None):
    """Combine what the reader picked by hand with what their sentence
    said, and report anything the controls could not hold.

    `manual_teammates` / `manual_opponents` are NAMES, because that is
    what the page's widgets hand downstream. `name_of` maps a player_id
    from `parsed` to a name; a scenario hit whose id it cannot resolve is
    dropped and reported rather than passed on as an id where every
    consumer expects a name.

    MANUAL PICKS COME FIRST, and that is the whole reason this function
    exists rather than a one-line union at the call site. The controls
    hold MAX_PER_CONTROL players. A reader who clicked five names and
    then typed a sixth into the sentence has told us two things of
    unequal weight: the clicks are unambiguous, the sentence was parsed.
    So the clicks win the cap and the sentence overflows -- and the
    overflow is reported, never silently dropped, because a player the
    reader believes is out who is quietly not in the model is exactly
    the kind of wrong number this app exists to not produce.

    Returns (teammate_names, opponent_names, notes) where notes is a list
    of {"clause", "reason"} in `unmatched`'s shape, ready to append to it.
    """
    name_of = name_of or (lambda pid: None)
    notes = []

    def combine(manual, scenario_ids, control):
        out = []
        for name in manual:
            if name not in out:
                out.append(name)
        for player_id in scenario_ids:
            name = name_of(player_id)
            if name is None:
                notes.append({
                    "clause": f"player {player_id}",
                    "reason": "could not be matched to a name the model knows",
                })
                continue
            if name in out:
                continue  # already picked by hand; not a second slot
            if len(out) >= MAX_PER_CONTROL:
                notes.append({
                    "clause": name,
                    "reason": (f"{control} already holds {MAX_PER_CONTROL} "
                               f"players you picked -- not added"),
                })
                continue
            out.append(name)
        return out

    teammates = combine(manual_teammates, parsed.get("out_teammates", ()),
                        "Missing teammates")
    opponents = combine(manual_opponents, parsed.get("out_opponents", ()),
                        "Missing opponent players")
    return teammates, opponents, notes


def summary(parsed):
    """One line for the page, above the two columns."""
    # A caller that could not even look (no roster loaded, say) supplies
    # its own line. Without this, the count below would report "none of
    # this maps to a layer the model measures", which is a different and
    # untrue claim: nothing was measured because nothing could be read.
    if parsed.get("blocked"):
        return parsed["blocked"]
    used, ignored = len(parsed["applied"]), len(parsed["unmatched"])
    total = used + ignored
    if not total:
        return "Nothing typed yet."
    if not used:
        return (f"None of the {total} thing(s) you described maps to a layer "
                f"the model measures.")
    return (f"{used} of {total} applied. The rest are listed so you know "
            f"they were left out.")
