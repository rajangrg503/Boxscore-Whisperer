"""guard / wing / big from a player's listed size.

One definition, used by the engine's teammate layer and by
tools/matchup_holes.py and tools/matchup_edge.py, so the three cannot
drift apart.

WHY THE ENGINE NEEDS IT
Karma, 31 Oct 2026: "sharpe being there or not shouldnt affect avdija
number in any way. they dont eat into eachother." Avdija's 71 games
last season agreed exactly -- his shot attempts per 36 were identical
with and without Sharpe (a guard), and moved a full attempt with and
without Jerami Grant (a forward). An absent teammate redistributes
shots only if he was eating from the same plate, and the plate is the
archetype.

The thresholds are stated, not tuned: nothing here has been fitted to
an outcome, and the moment one of them is, it belongs in a sweep with
a measurement beside it.
"""
from engine.cache import read_payload
from engine.team_ids import TEAM_ID_BY_ABBR

GUARD_MAX_IN = 77        # 6-5 and under
WING_MAX_IN = 81         # 6-6 to 6-9
WING_MIN_LB = 206        # below this a 6-6 body is a guard, not a wing
BIG_MIN_LB = 232         # a 6-9 listed F at this weight is playing the 5

ARCHETYPES = ("guard", "wing", "big")


def inches(height):
    try:
        feet, inch = str(height).split("-")
        return int(feet) * 12 + int(inch)
    except Exception:
        return 0


def pounds(weight):
    try:
        return int(float(weight))
    except Exception:
        return 0


def archetype(position, height, weight):
    """guard / wing / big, with the two corrections that matter: a light
    6-6 is a guard, and a heavy 6-9 forward is a big."""
    pos = str(position or "")
    tall, heavy = inches(height), pounds(weight)
    if tall == 0:
        return None
    if tall > WING_MAX_IN or pos in ("C", "C-F", "F-C"):
        return "big"
    if tall <= GUARD_MAX_IN:
        return "guard"
    if heavy < WING_MIN_LB:
        return "guard"          # 6-6, 195: a guard who is tall
    if tall >= WING_MAX_IN and heavy >= BIG_MIN_LB:
        return "big"            # 6-9, 240 listed F: he is playing the 5
    return "wing"


_index = None


def _roster_index():
    """{player_id: archetype} and {folded name: archetype} across every
    cached roster. Built once per process."""
    global _index
    if _index is None:
        by_id, by_name = {}, {}
        for abbr, tid in TEAM_ID_BY_ABBR.items():
            for row in ((read_payload(f"roster_{tid}") or {}).get("data") or []):
                kind = archetype(row.get("POSITION"), row.get("HEIGHT"), row.get("WEIGHT"))
                if kind:
                    by_id[int(row["PLAYER_ID"])] = kind
                    by_name[str(row["PLAYER"]).strip().lower()] = kind
        _index = (by_id, by_name)
    return _index


def of_player(player_id):
    """Archetype for a player id, looked up across every roster -- a former
    teammate who has moved is found where he is now."""
    return _roster_index()[0].get(int(player_id))


def of_name(name):
    return _roster_index()[1].get(str(name or "").strip().lower())
