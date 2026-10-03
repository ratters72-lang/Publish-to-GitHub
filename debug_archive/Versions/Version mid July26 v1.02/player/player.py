"""
player/player.py — Simplified.

The OpenPlayEngine is the sole authority for all movement, freeze, and state.
Player.update() only checks the `frozen` flag (set by OPE). No self-managed
state machines, no apply_state(), no tackle_locked/in_contact/is_tackler
branches — those are all replaced by `frozen` and OPE's phase logic.

The `try_catch()` method has been removed — OPE's _handle_catch() does
all catch logic directly.
"""

import math
from enum import Enum, auto
from typing import Optional

from player.collision_body import CollisionBody


class Team(Enum):
    KICKING = "KICKING"
    RECEIVING = "RECEIVING"


class PlayerState(Enum):
    IDLE = "IDLE"
    KICKER = "KICKER"
    CHASE_BALL = "CHASE_BALL"
    BALL_CARRIER = "BALL_CARRIER"
    SUPPORT_RUN = "SUPPORT_RUN"
    TACKLING = "TACKLING"
    TACKLE_APPROACH = "TACKLE_APPROACH"
    TACKLED = "TACKLED"
    PTB_PLAYER = "PTB_PLAYER"


class DefenderRole(Enum):
    MARKER_A = auto()
    MARKER_B = auto()
    LINE = auto()
    KICK_CHASE = auto()
    DUMMY_HALF = auto()


FIELD_MIN_X = 0.0
FIELD_MAX_X = 120.0
FIELD_MIN_Y = 0.0
FIELD_MAX_Y = 70.0


class Player:
    """
    A rugby league player.

    Movement: OPE sets target_x/target_y.  Player.update() accelerates
    toward that target if not frozen.

    Freeze: ONLY OPE may set frozen=True/False.  No other subsystem
    touches this flag.

    State: PlayerState is informational (for rendering/debug).  OPE
    sets ball_carrier.state = BALL_CARRIER during start_open_play.
    """

    def __init__(self, x: float, y: float, player_id: int = -1):
        self.x = x
        self.y = y
        self.target_x = x
        self.target_y = y

        self.body = CollisionBody(self)
        self.body.player_id = player_id

        self.kickoff_kicked = False
        self._kickoff_internal_state = "init"

        self.vel_x = 0.0
        self.vel_y = 0.0
        self.max_speed = 6.5
        self.accel = 0.45
        self.decel = 0.35
        self.input_smooth_x = 0.0
        self.input_smooth_y = 0.0
        # facing_angle/facing_vec removed — use player.facing (Phase 2D)

        self.vx = 0.0
        self.vy = 0.0
        self.max_speed_mps = 9.2
        self.move_speed = self.max_speed_mps
        self.facing = 0.0
        self.facing_len = 0.8

        self.player_id = player_id
        self.team: Optional[Team] = None
        self.role = None

        # ── FREEZE — ONLY OPE may set this ──
        self.frozen = False
        self.freeze_timer: float = 0.0

        self.can_be_primary: bool = True
        self.can_be_secondary: bool = True
        self.possessed = False

        self.kick_lane = 0
        self.kick_lane_offset_y = 0.0
        self.kick_stagger_x = 0.0

        self.state = PlayerState.IDLE

        self.support_leader: Optional[Player] = None
        self.support_offset = 5.0

        self.defender_role: Optional[DefenderRole] = None
        self.retreating = False

        self.mass = 1.0
        self.momentum = 0.0

        # ── Contact flags (informational, set by OPE during tackle phases) ──
        self.in_contact: bool = False
        self.is_tackler: bool = False
        self.tackle_locked: bool = False
        self.pre_tackle_event: str | None = None
        # ── STEP‑02A‑13 — Visual reach indicator ──
        self.reach_event: str | None = None
        # ── STEP‑02A‑14 — Defender wrap event (visual-only) ──
        self.wrap_event: str | None = None
        self.apply_physics_forces: bool = False

        # Phase 4: BC tuners (personality parameters)
        self.tuners: "AttackTuners" = None  # set externally by OpenPlayEngine

        # Phase 3: BC intent (RUN / PASS / KICK)
        self.intent: str | None = None

        self.solid: bool = True

        # ── STEP‑03A‑01 — TES entry flags ──
        self.anim_state: str = ""          # "TES_CONTACT" during tackle engagement
        self.tes_locked: bool = False      # True while TES owns this player

    def update(self, dt: float, attacking_direction: int = 1) -> None:
        """Simple movement: if frozen, stop; else accelerate toward target.

        ONLY OPE may freeze/unfreeze this player.
        """
        if self.frozen:
            self.vx = self.vy = 0.0
            self.vel_x = self.vel_y = 0.0
            from player.movement import update_facing_unified
            update_facing_unified(self, dt)
            self._update_physics(dt)
            self.body.sync()
            return

        self.move_speed = self.max_speed_mps
        self._accelerate_toward_target(dt)
        from player.movement import update_facing_unified
        update_facing_unified(self, dt)
        self._clamp_position()
        self.body.sync()

    def set_state(self, state: PlayerState, **kwargs) -> None:
        """Set player state (informational only)."""
        self.state = state
        for k, v in kwargs.items():
            setattr(self, k, v)

    def reset_for_kickoff(self, world=None) -> None:
        """Reset player state for a new kickoff."""
        self.state = PlayerState.IDLE
        self.retreating = False
        self.frozen = True

        self.target_x = self.x
        self.target_y = self.y

        self.vx *= 0.35
        self.vy *= 0.35
        self.vel_x *= 0.35
        self.vel_y *= 0.35

        self.body.reset_collision_state()

        if world is not None:
            self._world_ref = world
            self.body.apply_kickoff_properties()

        self.possessed = False
        self.freeze_timer = 0.0
        self.can_be_primary = True
        self.can_be_secondary = True
        self.defender_role = None
        self.support_leader = None
        self.kick_lane = 0
        self.kick_lane_offset_y = 0.0
        self.kick_stagger_x = 0.0

        self.in_contact = False
        self.is_tackler = False
        self.tackle_locked = False
        self.pre_tackle_event = None
        self.reach_event = None
        self.wrap_event = None

        self.solid = True

        # ── STEP‑03A‑01 — Reset TES entry flags ──
        self.anim_state = ""
        self.tes_locked = False

    def update_movement(self, raw_input_x: float, raw_input_y: float, dt: float) -> None:
        """Direct input-based movement (not used during gameplay — OPE sets targets)."""
        self._accelerate_toward_target(dt)
        self._clamp_position()
        self.body.sync()

    def set_pos(self, x: float, y: float, tag: str = "") -> None:
        self.x = x
        self.y = y
        self.vx = 0.0
        self.vy = 0.0
        self._clamp_position()

    def distance_to(self, other) -> float:
        return math.hypot(self.x - other.x, self.y - other.y)

    def distance_to_point(self, x: float, y: float) -> float:
        return math.hypot(self.x - x, self.y - y)

    # ── Internals ─────────────────────────────────────

    def _accelerate_toward_target(self, dt: float) -> None:
        """Smoothly accelerate toward target_x/target_y using vx/vy."""
        dx = self.target_x - self.x
        dy = self.target_y - self.y
        dist = math.hypot(dx, dy)

        if dist < 0.05:
            factor = min(self.decel * dt * 60.0, 1.0)
            self.vx *= (1.0 - factor)
            self.vy *= (1.0 - factor)
            if math.hypot(self.vx, self.vy) < 0.01:
                self.vx = 0.0
                self.vy = 0.0
        else:
            nx = dx / dist
            ny = dy / dist
            desired_vx = nx * self.move_speed
            desired_vy = ny * self.move_speed
            factor = min(self.accel * dt * 60.0, 1.0)
            self.vx += (desired_vx - self.vx) * factor
            self.vy += (desired_vy - self.vy) * factor
            speed = math.hypot(self.vx, self.vy)
            if speed > self.move_speed:
                self.vx *= self.move_speed / speed
                self.vy *= self.move_speed / speed

        self.x += self.vx * dt
        self.y += self.vy * dt

    def _update_facing(self) -> None:
        # Phase 2D: facing handled by unified facing system
        return

    def _update_physics(self, dt: float) -> None:
        speed = math.hypot(self.vx, self.vy)
        self.momentum = self.mass * speed

    def _clamp_position(self) -> None:
        self.x = max(FIELD_MIN_X, min(FIELD_MAX_X, self.x))
        self.y = max(FIELD_MIN_Y, min(FIELD_MAX_Y, self.y))
