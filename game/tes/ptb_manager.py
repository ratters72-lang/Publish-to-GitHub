"""PTBManager — centralised PTB lifecycle for the dummy-half.

Owns:
  - DH role assignment (DefenderRole.DUMMY_HALF)
  - DH target updates (prep -> exec -> pickup)
  - DH freeze/unfreeze
  - Ball transfer from BC to DH on pickup

OPE remains the single FSM; PTBManager is a delegate called *from*
OPE's PTB phase handlers.  No parallel FSM.

PATCHES APPLIED (Chunk 2 — DH Positioning Layer):
  P1: start_prep targets bc.x - 1.0 instead of stale ruck_x - 0.8
  P2: start_exec targets bc.x - 1.0 instead of ruck_x - 0.3 (no scoop approach)
  P5: update_prep returns ready only when timer AND distance-to-target are met
  P6: update_exec returns ready only when timer AND distance-to-target are met
  P3/P4: update_ptb_ready() and update_dh_position() are DEPRECATED (raise RuntimeError)
"""

import math

from player.player import DefenderRole


# ── DH movement speed for PTB phases (self-contained, avoids circular imports) ──
_DH_MOVE_SPEED_MPS: float = 8.5

# ── CHUNK 2: DH positioning constants ──
_DH_BEHIND_DISTANCE_M: float = 1.0          # DH must be exactly 1m behind BC
_DH_POSITION_TOLERANCE_M: float = 0.55      # how close DH must be to 1m target
_DH_PREP_MIN_TIME_S: float = 0.18           # minimum prep timer before transition
_DH_EXEC_MIN_TIME_S: float = 0.15           # minimum exec timer before transition

# ── PHASE WINDOW CAPS (2026-09-17) ──
# Explicit watchdogs for the two PTB positioning phases.  The engine used
# to force-transition on a 300-frame (5.0 s) override countdown, so a
# failing DH readiness gate could hold PTB_PREP and then PTB_EXEC for up
# to five seconds EACH -- the 13.72 s tackle-chain tail recorded in
# PHASE_TIMER_BUDGET_REPORT.md.  These caps bound each window regardless
# of the readiness gate.  They are WATCHDOGS, not flow control: once the
# convergence-first RELEASE exit and the DH preposition writer are in
# place both phases resolve in ~0.2 s, so a cap only fires on a genuine
# outlier.
PTB_PREP_MAX_S: float = 1.00
PTB_EXEC_MAX_S: float = 0.60

# ── P2: PTB_PREP Gate stability constants ──
_DH_VELOCITY_TOLERANCE_MPS: float = 1.8     # max DH speed for "stable" during prep
_DH_EXEC_VELOCITY_TOLERANCE_MPS: float = 1.1  # slightly looser exec gate so PTB resolves faster
_DH_RETREAT_VX_MIN: float = -0.5            # DH must not be retreating faster than this

# ── DH SINGLE-AUTHORITY CONTRACT (2026-08-02) ──
# The DH ruck jiggle was caused by a same-frame DUAL-WRITER: PTBManager
# moved the DH toward (bc.x - 1.0) while OPE's PTB role pipeline pinned the
# DH to (ruck_x, ruck_y) and integrated d.update() in the SAME frame.  The
# fix is enforced in world/open_play_engine.py, which now skips DUMMY_HALF
# in the role pipeline during PTB_PREP/PTB_EXEC/PTB so PTBManager (prep/exec)
# and the PTB handler own the DH exclusively.  No DH-side changes needed.





class PTBManager:
    """Controls DH movement and pickup through PTB_PREP -> PTB_EXEC -> PTB.

    State machine (internal): idle -> prep -> exec -> ptb -> complete.

    Usage::

        ptb_manager = PTBManager()
        ptb_manager.start_prep(dh, bc, ruck_y)
        # each frame during prep:
        ready = ptb_manager.update_prep(dt, dh, bc, ruck_y)
        if ready:
            ptb_manager.start_exec(dh, bc, ruck_y)
        # each frame during exec:
        ready = ptb_manager.update_exec(dt, dh, bc, ruck_y)
        if ready:
            ptb_manager.complete(dh, bc)
    """

    def __init__(self):
        self.state: str = "idle"        # idle -> prep -> exec -> ptb -> complete
        self.timer: float = 0.0

    # ── Public API ──────────────────────────────────────────

    def start_prep(self, dh, bc, ruck_y: float) -> None:
        """Enter PTB_PREP: assign DUMMY_HALF role, unfreeze, set target 1m behind BC.

        NOTE: ``bc`` parameter may be None (during PTB when ball_carrier has been
        cleared).  When None, use ruck_y as anchor and target dh.x (hold position).
        DH target is computed from bc.x when available, otherwise ruck_x is used.
        """
        if dh is None:
            return
        if bc is None:
            # PTB phase — ball_carrier cleared, DH holds at current position
            dh.target_x = dh.x
            dh.target_y = ruck_y
            self.state = "prep"
            self.timer = 0.0
            return
        dh.attack_role = "dummy_half"  # Use attack_role, NOT defender_role
        # Frozen state managed by apply_phase_invariants() — not set here
        dh.tackle_locked = False
        # P1: Target is 1m behind the CURRENT BC position (not stale ruck_x)
        dh.target_x = bc.x - _DH_BEHIND_DISTANCE_M
        dh.target_y = ruck_y
        self.state = "prep"
        self.timer = 0.0

    def update_prep(self, dt: float, dh, bc, ruck_y: float) -> bool:
        """Advance prep timer.  Move DH toward 1m behind BC.

        Directly drives DH movement toward bc.x - 1.0 at _DH_MOVE_SPEED_MPS.
        Returns True when timer AND distance thresholds are BOTH met.

        NOTE: The target is recomputed each frame from bc.x, so it follows
        any BC step-back movement during prep.
        """
        if dh is None or bc is None:
            return False
        self.timer += dt

        # P1/P5: Target is 1m behind CURRENT BC position, computed fresh each frame
        target_x = bc.x - _DH_BEHIND_DISTANCE_M
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

        # P5: Gate on BOTH timer AND distance to target
        dist_to_target = math.hypot(dh.x - target_x, dh.y - target_y)

        # P2: Additional DH readiness checks — velocity stability, lateral offset,
        # behind-BC, and no retreating.
        dh_speed = math.hypot(dh.vx, dh.vy)
        lateral_offset = abs(dh.y - target_y)
        behind_bc = dh.x < bc.x
        not_retreating = dh.vx >= _DH_RETREAT_VX_MIN

        pos_ready = (dist_to_target < _DH_POSITION_TOLERANCE_M
                     and lateral_offset < _DH_POSITION_TOLERANCE_M)
        stable_ready = (dh_speed < _DH_VELOCITY_TOLERANCE_MPS
                        and behind_bc
                        and not_retreating)

        return (self.timer >= _DH_PREP_MIN_TIME_S
                and pos_ready
                and stable_ready)

    def start_exec(self, dh, bc, ruck_y: float) -> None:
        """Enter PTB_EXEC: keep DH at 1m behind BC (no scoop approach).

        P2: Targets bc.x - 1.0 throughout exec, NOT a scoop position (0.3m).
        """
        if dh is None or bc is None:
            return
        # P2: Target stays at 1m behind BC, NOT 0.3m scoop position
        dh.target_x = bc.x - _DH_BEHIND_DISTANCE_M
        dh.target_y = ruck_y
        self.state = "exec"
        self.timer = 0.0

    def update_exec(self, dt: float, dh, bc, ruck_y: float) -> bool:
        """Advance exec timer.  Keep DH at 1m behind BC.

        Directly drives DH movement toward bc.x - 1.0 at _DH_MOVE_SPEED_MPS.
        Returns True when timer AND distance thresholds are BOTH met.
        """
        if dh is None or bc is None:
            return False
        self.timer += dt

        # P2/P6: Target stays at 1m behind BC, computed fresh each frame
        target_x = bc.x - _DH_BEHIND_DISTANCE_M
        target_y = ruck_y
        dh.target_x = target_x
        dh.target_y = target_y

        # Direct movement to keep DH at 1m position
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

        # P6: Gate on BOTH timer AND distance to target
        dist_to_target = math.hypot(dh.x - target_x, dh.y - target_y)

        # P5: Additional DH readiness checks for PTB_EXEC — stricter velocity,
        # lateral offset, behind-BC, and no retreating.
        dh_speed = math.hypot(dh.vx, dh.vy)
        lateral_offset = abs(dh.y - target_y)
        behind_bc = dh.x < bc.x
        not_retreating = dh.vx >= _DH_RETREAT_VX_MIN

        pos_ready = (dist_to_target < _DH_POSITION_TOLERANCE_M
                     and lateral_offset < _DH_POSITION_TOLERANCE_M)
        stable_ready = (dh_speed < _DH_EXEC_VELOCITY_TOLERANCE_MPS
                        and behind_bc
                        and not_retreating)

        return (self.timer >= _DH_EXEC_MIN_TIME_S
                and pos_ready
                and stable_ready)

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
        """Return to idle for the NEXT tackle cycle.

        CONTRACT (2026-09-17): this MUST be called once per tackle cycle,
        BEFORE ``start_prep()``.

        The state machine carries ``state`` and ``timer`` across cycles,
        and ``OpenPlayEngine._enter_ptb_prep()`` used to gate
        ``start_prep()`` on ``state == "idle"``.  From the second tackle
        onward that guard therefore SKIPPED the call: prep inherited the
        stale ``"complete"`` state and a leftover 0.15 s timer, so
        PTB_PREP's designed >= 0.18 s window collapsed to ~0.03 s (19 of
        22 measured cycles) and the manager carried cross-cycle state.

        The engine now resets at RELEASE entry AND starts prep
        unconditionally, so an inherited state can never be read.
        """
        self.state = "idle"
        self.timer = 0.0

    def update_ptb_ready(self, world, bc, dh, dt):
        """
        DEPRECATED — DO NOT CALL.  This method competes with update_prep()
        for DH positioning, violating the single-authority rule.
        PTBManager.update_prep() is the sole authority for DH movement
        during PTB_PREP.  Raises RuntimeError on invocation.
        """
        raise RuntimeError(
            "PTBManager.update_ptb_ready() is DEPRECATED — it competes with "
            "update_prep() for DH positioning.  Use update_prep() only."
        )

    def update_dh_position(self, world, dh, dt):
        """
        DEPRECATED — DO NOT CALL.  This method uses stale ptb_anchor_x
        (frozen at DOMINANCE->RELEASE) which does not track BC movement.
        Raises RuntimeError on invocation.
        """
        raise RuntimeError(
            "PTBManager.update_dh_position() is DEPRECATED — it uses stale "
            "ptb_anchor_x and competes with update_prep()/update_exec().  "
            "Use update_prep() or update_exec() only."
        )
