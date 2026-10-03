"""
ai_agents/defence_reactivity_agent.py — Minimal POST_RUCK retreat agent.

HARD PHASE GATE: only runs when world.phase == OPEN_PLAY AND
world.sub_state == POST_RUCK_OPEN_PLAY.  Never runs during any
TES phase (TACKLE_INIT/CONTACT/DRIVE/DOMINANCE), RELEASE, PTB,
or early OPEN_PLAY.

HARD ROLE GATE: only processes defenders with role DEF_RETREAT_10.

Never touches player.frozen — that's owned by apply_phase_invariants().
Never calls player.update(dt) — the normal physics loop resolves targets.
Never writes vx/vy directly.
"""

import math

from world.world_state import GamePhase, GameSubState
from player.player import DefenderRole
from player.movement_states import MovementState


# ── Retreat distance ──
RETREAT_DISTANCE_M: float = 10.0


class DefenceReactivityAgent:
    """
    Sole movement owner for DEF_RETREAT_10 defenders during
    POST_RUCK_OPEN_PLAY.

    Sets retreat targets (BC.x + 10) each frame — the normal
    Player.update(dt) in the physics loop resolves targets into
    actual velocity.
    """

    def __init__(self, world):
        self.world = world

    def update(self):
        """
        Call once per frame, AFTER OPE.update() and BEFORE collision.

        Phase gate: only runs when world.phase == OPEN_PLAY AND
        world.sub_state == POST_RUCK_OPEN_PLAY.  Returns immediately
        in all other phases (TES, RELEASE, PTB, early OPEN_PLAY).
        """
        # ── Phase gate: only POST_RUCK_OPEN_PLAY ──
        phase = getattr(self.world, "phase", None)
        sub = getattr(self.world, "sub_state", None)
        if phase != GamePhase.OPEN_PLAY or sub != GameSubState.POST_RUCK_OPEN_PLAY:
            return

        # ── Must have a ball carrier to compute retreat line ──
        bc = getattr(self.world, "ball_carrier", None)
        if bc is None:
            return

        retreat_line_x = bc.x + RETREAT_DISTANCE_M

        # ── Roles that must NEVER be touched ──
        SKIP_ROLES = frozenset({
            DefenderRole.MARKER_A,
            DefenderRole.MARKER_B,
            DefenderRole.DUMMY_HALF,
            DefenderRole.TACKLER_1,
            DefenderRole.TACKLER_2,
            DefenderRole.TACKLER_3,
        })

        # ── Per-defender retreat ──
        for d in getattr(self.world, "defending_team", []):
            role = getattr(d, "defender_role", None)

            # Role gate: only DEF_RETREAT_10
            if role != DefenderRole.DEF_RETREAT_10:
                continue

            # Skip markers, DH, tacklers (belt + suspenders)
            if role in SKIP_ROLES:
                continue

            # Already past retreat line → do nothing
            if d.x >= retreat_line_x:
                continue

            # ── Set movement intent ONLY ──
            d.target_x = retreat_line_x
            d.target_y = d.y
            d.move_speed = d.max_speed_mps * 0.85  # retreat speed
            d.movement_state = MovementState.RETREAT
            d.target_source = "reactivity_agent_retreat"

            # DO NOT:
            # - touch d.frozen
            # - call d.update(dt)
            # - write vx/vy directly
