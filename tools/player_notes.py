"""karma's hand-kept player reads (data/player_notes.json).

5 Nov 2026: Scheierman started for a resting Tatum and went 29 min,
9/11, against our under 12.5 PR; Next Level Bets took the overs. karma:
"he's a high intensity player, he maximises any minutes he gets." The
projection multiplies bench per-minute rates by bench-ish minutes, and a
motor player does not stay at bench rates when he inherits a starter's
minutes. Until there is a measured starting-games split, the rule is
blunt: no unders on a 'motor' player whose team is missing a starter.
"""
import json
import os

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "player_notes.json")
STARTER_OUT_MPG = 28.0


def load():
    try:
        with open(PATH) as f:
            return {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    except (OSError, ValueError):
        return {}


def tags(name, notes=None):
    return set((notes if notes is not None else load()).get(name, {}).get("tags", []))


def blocks_under(name, starter_out, notes=None):
    """True when this is an under on a motor player whose team is missing a
    starter (someone averaging STARTER_OUT_MPG+ minutes)."""
    return "motor" in tags(name, notes) and bool(starter_out)
