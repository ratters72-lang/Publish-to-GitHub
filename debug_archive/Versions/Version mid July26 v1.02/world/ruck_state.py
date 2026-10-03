"""Ruck state / phase enums — kept for backwards compatibility only.

All ruck lifecycle logic has been moved into OpenPlayEngine's single FSM.
This file now only provides the RuckState backwards-compatibility enum.
"""

from enum import Enum


class _RuckStateBackwardsCompat(Enum):
    """Legacy enum — kept so old code importing RuckState from world.ruck_state still works.

    New code should use OpenPlayEngine's PlayPhase enum directly.
    """
    NONE = "NONE"
    FREEZE = "FREEZE"
    RELEASE = "RELEASE"
    TACKLE_COMPLETE = "TACKLE_COMPLETE"


RuckState = _RuckStateBackwardsCompat
