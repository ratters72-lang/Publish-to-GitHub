"""PTBManager — centralised PTB lifecycle for the dummy-half.

Owns:
  - DH role assignment (DefenderRole.DUMMY_HALF)
  - DH target updates (prep -> exec -> pickup)
  - DH freeze/unfreeze
  - Ball transfer from BC to DH on pickup

OPE remains the single FSM; PTBManager is a delegate called *from*
OPE's PTB phase handlers.  No parallel FSM.
"""

import math

from player.player import DefenderRole


# ── DH movement speed for PTB phases (self-contained, avoids circular imports) ──
_DH_MOVE_SPEED_MPS: float = 6.0


class PTBManager:
    """Controls DH movement and pickup through PTB_PREP -> PTB_EXEC -> PTB.

    State machine (internal): idle -> prep -> exec -> ptb -> complete.

    Usage::

        ptb_manager = PTBManager()
        ptb_manager.start_prep(dh, ruck_x, ruck_y)
        # each frame during prep:
        ready = ptb_manager.update_prep(dt, dh, ruck_x, ruck_y)
        if ready:
            ptb_manager.start_exec(dh, ruck_x, ruck_y)
        # each frame during exec:
        ready = ptb_manager.update_exec(dt, dh, ruck_x, ruck_y)
        if ready:
            ptb_manager.complete(dh, bc)
    """

    def __init__(self):
        self.state: str = "idle"        # idle -> prep -> exec -> ptb -> complete
        self.timer: float = 0.0

    # ── Public API ──────────────────────────────────────────

    def start_prep(self, dh, ruck_x: float, ruck_y: float) -> None:
        """Enter PTB_PREP: assign DUMMY_HALF role, unfreeze, set target behind ball."""
        if dh is None:
            return
        dh.defender_role = DefenderRole.DUMMY_HALF
        dh.frozen = False
        dh.tackle_locked = False
        dh.target_x = ruck_x - 0.8
        dh.target_y = ruck_y
        self.state = "prep"
        self.timer = 0.0

    def update_prep(self, dt: float, dh, ruck_x: float, ruck_y: float) -> bool:
        """Advance prep timer.  Move DH toward behind-ball position (0.8m behind).

        Directly drives DH movement toward ruck_x - 0.8 at _DH_MOVE_SPEED_MPS.
        Return True when ready for exec (0.35s elapsed).
        """
        if dh is None:
            return False
        self.timer += dt

        # Set target for external reference
        target_x = ruck_x - 0.8
        target_y = ruck_y
        dh.target_x = target_x
        dh.target_y = target_y

        # Direct movement to behind-ball position
        dx = target_x - dh.x
        dy = target_y - dh.y
        dist = math.hypot(dx, dy)
        if dist > 0.1:
            step = _DH_MOVE_SPEED_MPS * dt
            factor = min(step / dist, 1.0)
            dh.x += dx * factor
            dh.y += dy * factor
            dh.vx = (dx / dist) * _DH_MOVE_SPEED_MPS
            dh.vy = (dy / dist) * _DH_MOVE_SPEED_MPS
        else:
            dh.vx = 0.0
            dh.vy = 0.0

        return self.timer >= 0.35

    def start_exec(self, dh, ruck_x: float, ruck_y: float) -> None:
        """Enter PTB_EXEC: move DH to scoop position (0.3m behind ball)."""
        if dh is None:
            return
        dh.target_x = ruck_x - 0.3
        dh.target_y = ruck_y
        self.state = "exec"
        self.timer = 0.0

    def update_exec(self, dt: float, dh, ruck_x: float, ruck_y: float) -> bool:
        """Advance exec timer.  Move DH toward scoop position (0.3m behind ball).

        Directly drives DH movement toward ruck_x - 0.3 at _DH_MOVE_SPEED_MPS.
        Return True when ready for PTB pickup (0.30s elapsed).
        """
        if dh is None:
            return False
        self.timer += dt

        # Set target for external reference
        scoop_x = ruck_x - 0.3
        scoop_y = ruck_y
        dh.target_x = scoop_x
        dh.target_y = scoop_y

        # Direct movement to scoop position
        dx = scoop_x - dh.x
        dy = scoop_y - dh.y
        dist = math.hypot(dx, dy)
        if dist > 0.1:
            step = _DH_MOVE_SPEED_MPS * dt
            factor = min(step / dist, 1.0)
            dh.x += dx * factor
            dh.y += dy * factor
            dh.vx = (dx / dist) * _DH_MOVE_SPEED_MPS
            dh.vy = (dy / dist) * _DH_MOVE_SPEED_MPS
        else:
            dh.vx = 0.0
            dh.vy = 0.0

        return self.timer >= 0.30

    def complete(self, dh, bc) -> None:
        """Transfer ball from BC to DH, mark PTB complete."""
        if dh is None:
            return
        if bc is not None and bc is not dh:
            bc.possessed = False
        dh.possessed = True
        dh.state = "BALL_CARRIER"
        self.state = "complete"

    def reset(self) -> None:
        """Return to idle for next tackle cycle."""
        self.state = "idle"
        self.timer = 0.0
