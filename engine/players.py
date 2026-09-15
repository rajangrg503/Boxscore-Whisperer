"""Player/team identity resolution.

nba_api's players.find_players_by_full_name() does an unanchored,
case-insensitive regex search across every player in league history --
active or retired -- not an exact match. Two different real people can
share an exact full name: confirmed empirically that "Brandon Williams"
resolves to a RETIRED player (id 1585) as well as a currently-active one
(id 1630314). The naive `match[0]` pattern used everywhere this was
called silently picked the retired player -- a real, reproducible
wrong-answer bug, not a hypothetical edge case. See
tests/test_player_resolution.py for the regression test.

get_player_id() is the one place this resolution should happen. Every
caller that used to do its own `players.find_players_by_full_name(name)`
+ `match[0]` should call this instead.

fold_diacritics()/player_search_label() below are NOT for get_player_id()
-- confirmed by reading nba_api's own source (players._find_players,
players._strip_accents) that find_players_by_full_name() already does
NFD-normalize + strip combining-mark matching internally, so
get_player_id("Jokic") already resolves to "Nikola Jokić" correctly with
zero changes needed here. The real diacritics bug is one layer up, in
app.py's st.selectbox/st.multiselect widgets: Streamlit's client-side
dropdown search does a plain substring match against each option's
rendered label, with no accent-folding of its own, so typing "Jokic"
never surfaces "Nikola Jokić" as a candidate to click -- get_player_id()
is never even reached. player_search_label() is a format_func for those
widgets that appends an ASCII-folded alias so the plain-substring client
search can find it, while the real (accented) name stays what's shown
first and what st.selectbox actually returns as the selected value.
"""

import unicodedata

from nba_api.stats.static import players, teams


def fold_diacritics(name):
    """ASCII-folded form of a name (e.g. "Jokić" -> "Jokic",
    "Dončić" -> "Doncic") -- NFKD decomposes each accented character
    into its base letter plus a combining mark, then the ascii
    encode/decode with errors="ignore" drops marks that have no plain-
    ASCII equivalent."""
    return unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")


def player_search_label(name):
    """format_func for a player-name selectbox/multiselect: appends an
    ASCII-folded alias in parens when it differs from the real name, so
    Streamlit's own client-side dropdown search (plain substring match
    against this rendered label) can find e.g. "Nikola Jokić" when
    someone types the unaccented "Jokic". Returns the name unchanged
    when there's nothing to fold (the common case), so this is safe to
    apply uniformly to every player-name widget in the app, including
    ones like "None" sentinels mixed into an options list."""
    folded = fold_diacritics(name)
    return name if folded == name else f"{name} ({folded})"


def get_player_id(name):
    """Resolve a full player name to (id, full_name, ambiguity_note).

    ambiguity_note is None when resolution was unambiguous. Otherwise
    it's a human-readable string explaining that multiple players share
    this name and which one was picked -- callers should surface it
    rather than silently trusting a guess.
    """
    matches = players.find_players_by_full_name(name)
    if not matches:
        return None, None, None

    exact = [m for m in matches if m["full_name"].lower() == name.lower()]
    candidates = exact if exact else matches

    if len(candidates) == 1:
        return candidates[0]["id"], candidates[0]["full_name"], None

    active = [m for m in candidates if m.get("is_active")]
    chosen = active[0] if len(active) == 1 else (active[0] if active else candidates[0])

    note = (
        f"Multiple players named '{chosen['full_name']}' found ({len(candidates)}) -- "
        f"resolved to {'the active one' if active else 'the most recent match'}, "
        f"but verify this is who you meant."
    )
    return chosen["id"], chosen["full_name"], note


def get_team_id(name):
    match = teams.find_teams_by_full_name(name)
    if not match:
        return None, None, None
    return match[0]["id"], match[0]["full_name"], match[0]["abbreviation"]
