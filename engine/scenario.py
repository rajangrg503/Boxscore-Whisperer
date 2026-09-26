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

THE SECOND HAZARD: THE READER'S SENTENCE IS NOT THE PARSER'S SENTENCE
The first version split on punctuation and conjunctions alone, and
handed each piece to the matcher as a complete assertion. Two everyday
shapes broke on that, and both were found by watching Karma type:

    "chet is out sga will be double teamed"

  -- one clause with two names in it, because nobody punctuates a
  scratch note. Two candidates, so it refused the whole thing as
  ambiguous and applied NOTHING, including the half it measures best.

    "chet and jalen williams are out"

  -- split on "and" into "chet" and "jalen williams are out". The
  second half carries the verb, so Jalen was marked out and Chet was
  quietly dropped as unmeasurable. That is the worse of the two: it
  looks like it worked, and the lineup it projects is not the one the
  reader described.

Both need to know WHERE in the clause each name sits, which is what
_words() and _mention_spans() below are for. Two rules use it:

  SPLIT  a clause in two before a name, when a state phrase has
         already completed after an earlier name -- "chet is out" is
         a finished assertion, so whatever follows starts a new one.

  CARRY  a clause that is nothing but a name takes the state of the
         clause after it, when a comma or an "and" joined them --
         the list shape, "chet, jalen and dort are out".

Both are deliberately narrow, and the narrowness is the point. CARRY
refuses to fire on "sga plays more, chet is out", because "sga plays
more" is an assertion of its own rather than a name in a list, and
inheriting "out" there would mark out a player the reader said was
playing MORE. SPLIT refuses to fire when it cannot locate every name
it matched, falling back to the old whole-clause behaviour. Neither
rule ever resolves an ambiguous name: "chet is out williams is out"
splits into two assertions and then refuses the second one, which is
the same refusal as before, now attached to the right half of the
sentence.
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

# A number of minutes, which IS something the engine can act on: the
# baseline is a per-minute rate times projected minutes, so replacing
# the minutes is the one reader input that changes a projection without
# inventing anything (engine/minutes.py::minutes_aware_means).
#
# Checked BEFORE the undecided list below, and this ordering is the
# whole point. "only play 20 minutes due to minutes restriction"
# contains "minutes restriction", which is undecided -- a phrase that
# means "we do not know how long he plays". But the reader just SAID
# how long: twenty. Refusing that as half-available would be the app
# ignoring the answer while quoting the question.
MINUTES_PATTERN = re.compile(r"\b(\d{1,2})\s*(?:minutes|minute|mins|min|mpg)\b")

# Below this the number is not a rotation, and above it there is no
# such game. A typo ("2 minutes", "90 minutes") is refused rather than
# projected, because a per-minute rate multiplied by a nonsense number
# is a nonsense line delivered with a straight face.
MIN_MINUTES, MAX_MINUTES = 4, 48

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

# The same breaks, with the separator kept, so a clause can be asked
# what joined it to the one before. A comma or an "and" is WEAK: it is
# how people list names ("chet, jalen and dort are out"). A full stop,
# a "so" or a "because" is not -- those introduce a new assertion, and
# a state must never be carried backwards across one.
_CLAUSE_BREAK_KEPT = re.compile("(" + CLAUSE_BREAK.pattern + ")", re.IGNORECASE)
_WEAK_BREAK = re.compile(r"^\s*(?:,|and)\s*$", re.IGNORECASE)

# Words that may sit beside a name in a list without making the clause
# an assertion about anything. Kept tiny on purpose: every word added
# here is a guess about what somebody meant, and the cost of guessing
# wrong is a player marked out who was not.
LIST_FILLER = ("and", "both", "also", "too")

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
    return [clause for clause, _weak in _clause_parts(text)]


def _clause_parts(text):
    """[(clause, joined weakly to the clause before it)].

    Same split as clauses(), keeping what the split was made on, so
    the CARRY rule can tell a list from a new sentence.
    """
    bits = _CLAUSE_BREAK_KEPT.split(str(text or ""))
    parts, weak = [], False
    for index, bit in enumerate(bits):
        if index % 2:
            weak = bool(_WEAK_BREAK.match(bit))
            continue
        clause = bit.strip()
        if clause:
            parts.append((clause, weak))
    return parts


def _words(text):
    """(folded word, start, end) for every word, spans into `text`.

    The spans point at the ORIGINAL string rather than the folded one,
    because a clause that gets cut is echoed back to the reader in the
    panel and folding is lossy -- cutting the folded text would quietly
    rewrite their sentence in lower case with the punctuation gone.

    One typed token can fold to several words ("Gilgeous-Alexander" ->
    "gilgeous", "alexander"). Both carry the whole token's span, so a
    cut can never land inside a hyphenated name.
    """
    words = []
    for match in re.finditer(r"\S+", text):
        for word in _fold(match.group(0)).split():
            words.append((word, match.start(), match.end()))
    return words


def _runs(words, needle):
    """Every (first, last+1) index range where `needle` appears."""
    size = len(needle)
    if not size or size > len(words):
        return []
    plain = [w for w, _s, _e in words]
    return [(i, i + size) for i in range(len(plain) - size + 1)
            if plain[i:i + size] == needle]


def _mention_spans(clause, roster):
    """{player_id: (first word, last word + 1)} -- best effort.

    Deliberately a SEPARATE pass from _candidates(), which is left
    exactly as it was. _candidates decides who a clause could mean and
    is the matcher the refusals are built on; this one only answers
    "and where does that name sit", and is allowed to come back empty.
    A caller that cannot find a position for every candidate falls back
    to treating the clause as one assertion -- the old behaviour, which
    is never wrong, only blunt.
    """
    words = _words(clause)
    plain = [w for w, _s, _e in words]
    spans = {}
    for player_id, name in roster:
        name_words = _fold(name).split()
        found = _runs(words, name_words)
        if found:
            spans[player_id] = found[0]
            continue
        parts = [p for p in name_words if len(p) > 1]
        hit = next((k for k, w in enumerate(plain) if w in parts), None)
        if hit is None:
            letters = _initials(name)
            if len(letters) >= 2:
                hit = next((k for k, w in enumerate(plain) if w == letters), None)
        if hit is not None:
            spans[player_id] = (hit, hit + 1)
    return spans


def _signal_ends(words):
    """Where each state phrase finishes, as a word index.

    Only the literal phrase lists -- the denials are regexes and are
    not needed here, because a denial is still one assertion about one
    player and nothing is ever cut out of it.
    """
    ends = set()
    for phrase in OUT_SIGNALS + ARRIVING_SIGNALS + UNDECIDED:
        for _first, last in _runs(words, _fold(phrase).split()):
            ends.add(last)
    return ends


def _cut_points(clause, rosters):
    """Character offsets where this clause starts saying a new thing.

    The rule: cut before a name when some EARLIER name already has a
    completed state phrase after it. "chet is out sga will be double
    teamed" cuts before "sga", because "chet ... is out" is a finished
    assertion by then.

    Requiring an earlier name is what keeps "missing chet dort" whole:
    the state phrase there comes first, so nothing has been asserted
    about anybody yet and the clause is exactly as ambiguous as it
    looks.
    """
    words = _words(clause)
    if len(words) < 2:
        return []
    ends = _signal_ends(words)
    if not ends:
        return []

    starts = []
    for roster in rosters:
        spans = _mention_spans(clause, roster)
        for player_id, _name in _candidates(clause, roster):
            if player_id not in spans:
                return []  # cannot see where this name is -- do not cut
            starts.append(spans[player_id][0])
    if len(set(starts)) < 2:
        return []

    cuts = []
    for start in sorted(set(starts)):
        if start and any(s < end <= start for end in ends for s in starts):
            cuts.append(words[start][1])
    return cuts


def _is_only_a_name(clause, rosters):
    """True when the clause says nothing except somebody's name.

    The gate on the CARRY rule, and the whole reason it is safe. "chet"
    is a name in a list; "sga plays more" is a claim, and a claim never
    inherits a state from the clause after it however it is punctuated.
    """
    words = _words(clause)
    if not words:
        return False
    covered = set()
    for roster in rosters:
        spans = _mention_spans(clause, roster)
        for player_id, _name in _candidates(clause, roster):
            if player_id not in spans:
                return False
            first, last = spans[player_id]
            covered.update(range(first, last))
    if not covered:
        return False
    leftover = [w for index, (w, _s, _e) in enumerate(words)
                if index not in covered]
    return all(w in LIST_FILLER for w in leftover)


def assertions(text, rosters=()):
    """[(clause, state)] -- what the sentence actually claims.

    clauses() splits on punctuation; this applies the two rules in the
    module docstring on top of it, which is everything that needs to
    know where the names are. Returned as text plus state rather than
    as a structure, so the clause echoed into the panel is still a
    verbatim slice of what the reader typed.
    """
    parts = []
    for clause, weak in _clause_parts(text):
        cuts = _cut_points(clause, rosters)
        if not cuts:
            parts.append((clause, weak))
            continue
        bounds = [0] + cuts + [len(clause)]
        for index in range(len(bounds) - 1):
            piece = clause[bounds[index]:bounds[index + 1]].strip()
            if piece:
                # Only the first piece inherits how the clause was
                # joined: a cut means an assertion finished here, which
                # is the opposite of a list.
                parts.append((piece, weak if index == 0 else False))

    states = [_state(clause) for clause, _weak in parts]
    for index in range(len(parts) - 2, -1, -1):
        if states[index] is not None or states[index + 1] is None:
            continue
        if not parts[index + 1][1]:
            continue
        if _is_only_a_name(parts[index][0], rosters):
            states[index] = states[index + 1]
    return [(clause, state) for (clause, _weak), state in zip(parts, states)]


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


def minutes_in(clause):
    """The minutes this clause asserts, or None.

    Out-of-range comes back as None as well, and parse() tells those
    two apart by looking for the pattern itself -- a reader who typed
    "plays 2 minutes" gets told what the range is, rather than the
    generic "no measured layer for this", which would be false: there
    IS a layer for minutes and the number just was not one.
    """
    found = MINUTES_PATTERN.search(_fold(clause))
    if not found:
        return None
    value = int(found.group(1))
    if not MIN_MINUTES <= value <= MAX_MINUTES:
        return None
    return value


def _state(clause):
    """out, minutes, arriving, undecided, denied, or None."""
    folded = _fold(clause)
    padded = f" {folded} "

    for pattern in DENIALS:
        if re.search(pattern, folded):
            return "denied"

    # An out-signal outranks a number. "he is out for 20 minutes" is
    # not a rotation, it is a contradiction, and the safe reading of a
    # contradiction is the one that removes a player rather than the
    # one that invents minutes for him.
    said_out = any(f" {_fold(phrase)} " in padded for phrase in OUT_SIGNALS)

    if not said_out and minutes_in(clause) is not None:
        return "minutes"
    for phrase in UNDECIDED:
        if f" {_fold(phrase)} " in padded:
            return "undecided"
    if said_out:
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
    minutes_teammates, minutes_opponents = {}, {}
    applied, unmatched = [], []

    for clause, state in assertions(text, (teammates, opponents)):
        here = _candidates(clause, teammates)
        there = _candidates(clause, opponents)
        matches = here + there

        if state is None:
            # A number that looked like minutes but was refused. Saying
            # "no measured layer for this" here would be a lie by
            # omission: the layer exists, and the number is the problem.
            refused = MINUTES_PATTERN.search(_fold(clause))
            unmatched.append({
                "clause": clause,
                "reason": (
                    f"{refused.group(1)} minutes is outside "
                    f"{MIN_MINUTES}-{MAX_MINUTES}, which is as long as a "
                    f"player can be on the floor"
                    if refused is not None else "no measured layer for this"
                ),
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

        if state == "minutes":
            asserted = minutes_in(clause)
            target = minutes_teammates if here else minutes_opponents
            control = ("Minutes for a player" if here
                       else "Minutes for an opponent player")
            if player_id in target:
                continue
            if len(target) >= MAX_PER_CONTROL:
                unmatched.append({
                    "clause": clause,
                    "reason": f"minutes set for more than {MAX_PER_CONTROL} players",
                })
                continue
            target[player_id] = asserted
            applied.append({"clause": clause, "control": control,
                            "player": f"{name} — {asserted} minutes"})
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
        "minutes_teammates": minutes_teammates,
        "minutes_opponents": minutes_opponents,
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
