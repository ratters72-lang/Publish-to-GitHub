"""Dummy-Half (DH) controller — the single most important attacker in the ruck cycle.

The DH:
  1. Is the closest attacker to the ruck who is NOT the ball carrier (BC).
  2. Moves to the ruck after TES completes (POST_RUCK_LOCK).
  3. Waits for the "ball playable" window (end of PTB WIND-UP).
  4. Picks up the ball with animation delay (0.25-0.40s).
  5. Decides next action: pass left, pass right, run, scoot, kick.
  6. Anchors the ruck position (ruck_y_m, band centre, short‑side/open‑side widths).
  7. Triggers defensive line SET → READY → ADVANCE once ball is picked up.
  8. NEVER leaves the field (clamped to Field API).
"""

from typing import Any, Optional

from world.world_state import is_tackle_phase


# ── Config ──────────────────────────────────────────────
DEFAULT_PICKUP_TIME_MIN: float = 0.25
DEFAULT_PICKUP_TIME_MAX: float = 0.40
MOVE_SPEED_MPS: float = 6.0  # how fast DH moves to ruck position


class DummyHalfAction:
    """Possible actions the DH can take after picking up the ball."""
    PASS_LEFT = "pass_left"
    PASS_RIGHT = "pass_right"
    RUN = "run"
    SCOOT = "scoot"
    KICK = "kick"  # reserved for later


class DummyHalfController:
    """Controls the dummy-half during the ruck cycle.

    The DH is the attacker who picks up the ball from the PTB and
    distributes it.  This controller is reset each tackle cycle.

    Usage::

        dh_controller = DummyHalfController()
        dh_controller.assign(attacking_team, ruck_x, ruck_y, ball_carrier)
        # each frame during the ruck cycle:
        dh_controller.update(dt, ruck_x, ruck_y, phase)
        # after pickup:
        if dh_controller.ball_playable:
            # read dh_controller.dh, dh_controller.pickup_complete
    """

    def __init__(self):
        self.dh: Any = None                     # the player assigned as DH
        self.pickup_timer: float = 0.0
        self.pickup_duration: float = DEFAULT_PICKUP_TIME_MAX
        self.pickup_complete: bool = False
        self.ball_playable: bool = False
        self.action: Optional[str] = None
        self.at_ruck: bool = False
        self._arrival_time: float = 0.0         # sim-time spent at ruck

    # ── Public API ────────────────────────────────────

    def assign(self, attacking_team: list, ruck_x: float, ruck_y: float,
               ball_carrier: Any, field: Any = None) -> None:
        """Find and assign the closest attacker to the ruck who is NOT the BC.

        This is the temporary assignment logic until we have real positional
        roles: closest attacker to the ruck (excluding BC) = DH.
        """
        self.dh = self._find_dh(attacking_team, ruck_x, ruck_y, ball_carrier)
        self.pickup_timer = 0.0
        self.pickup_duration = DEFAULT_PICKUP_TIME_MAX  # randomised on pickup
        self.pickup_complete = False
        self.ball_playable = False
        self.action = None
        self.at_ruck = False
        self._arrival_time = 0.0

        if self.dh is not None:
            self.dh.role = "dummy_half"

    def move_to_ruck(self, dt: float, ruck_x: float, ruck_y: float,
                     field: Any = None, arrival_radius: float = 1.5) -> None:
        """Move the DH toward the ruck position each frame.

        Call every frame during PTB. Once within arrival_radius
        (default 1.5m, from config radius hierarchy) of the ruck,
        the DH is "at ruck" and stops.
        
        The DH always moves at MOVE_SPEED_MPS (6.0) regardless of
        any other speed modifiers.

        NOTE: This method is only called during PTB phases (already
        gated by OPE phase machine).  The TES active guard is handled
        by the defence system's ruck-bubble freeze.
        """
        if self.dh is None:
            return

        dx = ruck_x - self.dh.x
        dy = ruck_y - self.dh.y
        dist = (dx * dx + dy * dy) ** 0.5

        # DH is considered "at ruck" when within arrival_radius (config threshold)
        if dist <= arrival_radius:
            self.at_ruck = True
            self._arrival_time += dt
            self.dh.vx = 0.0
            self.dh.vy = 0.0
            return

        # Move toward ruck at MOVE_SPEED_MPS (6.0)
        if dist > 0.01:
            step = MOVE_SPEED_MPS * dt
            factor = min(step / dist, 1.0)
            self.dh.x += dx * factor
            self.dh.y += dy * factor
            self.dh.vx = (dx / dist) * MOVE_SPEED_MPS
            self.dh.vy = (dy / dist) * MOVE_SPEED_MPS

        # Clamp to field
        if field is not None:
            clamped_x, clamped_y = field.clamp_point(self.dh.x, self.dh.y)
            self.dh.x = clamped_x
            self.dh.y = clamped_y

    def start_pickup(self) -> None:
        """Begin the pickup animation.

        Called at the start of PTB WIND-UP (end of Phase 3).
        The pickup takes ``pickup_duration`` seconds.
        """
        if self.dh is None:
            return
        self.pickup_timer = 0.0
        self.pickup_complete = False
        self.ball_playable = False
        # Slight randomisation of pickup duration within [0.25, 0.40]
        import random
        self.pickup_duration = DEFAULT_PICKUP_TIME_MIN + (
            DEFAULT_PICKUP_TIME_MAX - DEFAULT_PICKUP_TIME_MIN
        ) * random.random()

    def update_pickup(self, dt: float) -> None:
        """Advance the pickup animation timer.

        Call every frame from Phase 4 (PTB WIND-UP) onward.
        ``ball_playable`` becomes True at the end of Phase 4.
        ``pickup_complete`` becomes True after the pickup animation finishes.

        NOTE: This method is only called during PTB phases (already
        gated by OPE phase machine).  The TES active guard is handled
        by the defence system's ruck-bubble freeze.
        """
        if self.dh is None:
            return
        if self.pickup_complete:
            return

        self.pickup_timer += dt

        # Ball becomes playable at the end of PTB WIND-UP (Phase 4 end).
        # pickup_timer >= 0.4s marks that point (Phase 4 is 0.4s long).
        if self.pickup_timer >= 0.4 and not self.ball_playable:
            self.ball_playable = True

        # Pickup animation completes after pickup_duration
        if self.pickup_timer >= self.pickup_duration and not self.pickup_complete:
            self.pickup_complete = True
            self.dh.possessed = True
            self.dh.role = "dummy_half"

    def decide_action(self, receivers: list, ruck_y: float) -> str:
        """Decide what the DH does after picking up the ball.

        For now: pick the nearest available receiver and pass to them.
        Returns the action string.

        TODO: later add run/scoot/kick decisions based on game state.
        """
        if self.dh is None or not self.pickup_complete:
            return DummyHalfAction.RUN

        if not receivers:
            self.action = DummyHalfAction.RUN
            return self.action

        # Find nearest receiver
        nearest = min(
            receivers,
            key=lambda r: (
                (r.x - self.dh.x) ** 2 + (r.y - self.dh.y) ** 2
            )
        )

        if nearest.y < ruck_y:
            self.action = DummyHalfAction.PASS_LEFT
        else:
            self.action = DummyHalfAction.PASS_RIGHT

        # Set receiver target
        if self.action in (DummyHalfAction.PASS_LEFT, DummyHalfAction.PASS_RIGHT):
            nearest.target_x = self.dh.x + 5.0
            nearest.target_y = nearest.y
            nearest.role = "receiver"

        return self.action

    def hold_at_ruck(self) -> None:
        """Zero the DH's velocity while waiting for the ball to become playable."""
        if self.dh is None:
            return
        self.dh.vx = 0.0
        self.dh.vy = 0.0

    def clear(self) -> None:
        """Reset the controller for the next tackle cycle."""
        self.dh = None
        self.pickup_timer = 0.0
        self.pickup_complete = False
        self.ball_playable = False
        self.action = None
        self.at_ruck = False
        self._arrival_time = 0.0

    # ── Helpers ────────────────────────────────────────

    @staticmethod
    def _find_dh(attacking_team: list, ruck_x: float, ruck_y: float,
                 ball_carrier: Any) -> Any:
        """Return the closest attacker to the ruck who is NOT the BC.

        If no attacker other than the BC exists, returns the BC as fallback
        (the DH can't be empty).
        """
        candidates = [
            p for p in attacking_team
            if p is not ball_carrier
        ]

        if not candidates:
            # Fallback: BC is the only attacker
            return ball_carrier

        return min(
            candidates,
            key=lambda p: (
                (p.x - ruck_x) ** 2 + (p.y - ruck_y) ** 2
            ),
        )

    @property
    def is_assigned(self) -> bool:
        """Whether a DH has been assigned for this cycle."""
        return self.dh is not None
