"""Kickoff controller — STUB. All kickoff logic moved to OpenPlayEngine.

This file exists only to provide the KOState enum for backwards
compatibility.  The KickoffController class is kept as a stub so
existing references in run.py do not break, but its update() method
is a no-op.
"""

from enum import Enum, auto


class KOState(Enum):
    """Kickoff states — kept for backwards compat only."""
    KO_IN_AIR = auto()
    KO_BOUNCED = auto()
    KO_NOT_10_UNTOUCHED = auto()
    KO_NOT_10_TOUCHED_RCVR = auto()
    KO_NOT_10_TOUCHED_KICKER = auto()
    KO_OUT_ON_FULL_TOUCH = auto()
    KO_OUT_ON_FULL_INGOAL = auto()
    KO_KICK_DEAD_ON_FULL = auto()
    KO_KICK_DEAD_BOUNCE = auto()
    KO_LIVE = auto()
    KO_LIVE_INGOAL_RETURN = auto()
    KO_LIVE_INGOAL_TACKLED = auto()
    KO_OVER_SIDE_LINE_BOUNCE = auto()
    KO_SECOND_HALF = auto()


class KickoffController:
    """STUB — all kickoff logic moved to OpenPlayEngine.

    This class exists for backwards compatibility only.
    Do not call update() — OPE owns kickoff.
    """

    def __init__(self):
        self.ko_state = KOState.KO_IN_AIR
        self.active = False
        self.world_state = None

    def reset(self):
        self.ko_state = KOState.KO_IN_AIR
        self.active = False
        self.world_state = None

    def start_kickoff(self):
        pass

    def on_catch_complete(self):
        pass

    def update(self, ball_x, ball_y, ball_z, ball_grounded, touched_by=None):
        pass

    def end_kickoff(self):
        pass
