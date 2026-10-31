"""Moved to engine/role_blend.py on 31 Oct 2026 so the app can use it.
This shim keeps tools/rank_lines.py and tools/slip_check.py working."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.role_blend import *          # noqa: F401,F403
from engine.role_blend import PRIOR_W, UNKNOWN_CENTER  # noqa: F401
