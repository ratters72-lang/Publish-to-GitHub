"""
defensive_behaviour_unified.py — Unified defensive behaviour dispatcher.

Wraps Lvl‑1 (freeze/markers/step-up/chase) and STEP‑2 (containment/scramble/retreat)
and dispatches based on game sub-state, ball position, and per-defender depth
classification.

ARCHITECTURE CHANGE — Role-Depth Defensive Line
------------------------------------------------
No hardcoded line_x for all defenders. Instead:

    1. ball_x + ROLE_DEPTH_M[role] determines each defender's target_x
       (ball-relative depth, not ruck-anchored).
    2. classify_defender_depth() tags each defender as PRIMARY, SECONDARY,
       or PASSIVE based on |d.x - (ball_x + role_offset)|.
    3. Only PRIMARY/SECONDARY run step-up/chase; PASSIVE only retreat.

The `base_line_x` parameter is retained as a geometry anchor for lane_y
computation, but is NOT used for target_x — target_x is always ball-relative.
"""

import math

from world.world_state import GameWorldState, GameSubState, is_tackle_phase, is_in_ruck_bubble
from game.defence.defence_lvl_1.defensive_behaviour_lvl1 import DefensiveBehaviourLvl1
from game.defence.defence_lvl_2.defensive_behaviour_lvl2 import DefensiveBehaviour
from game.defence.defence_lvl_1.defensive_geometry import DefensiveGeometry
from player.player import DefenderRole

__all__ = ["DefensiveBehaviourUnified"]


class DefensiveBehaviourUnified:
    """Wrapper that selects Lvl‑1 or STEP‑2 behaviour based on game sub-state and ball position."""

    def __init__(self, tuners):
        self.tuners = tuners
        self.def_geometry = DefensiveGeometry(tuners)
        self.lvl1 = DefensiveBehaviourLvl1(tuners, geometry=self.def_geometry)
        self.step2 = DefensiveBehaviour(tuners, geometry=self.def_geometry)

    @staticmethod
    def _get_ball_x(world):
        """Extract ball X position from world, normalising between Ball and Player objects.

        When the ball is carried, use the carrier's TRUE position (not the eased ball.x_m).
        This is critical — ball.x_m lags behind the carrier by 65% each frame due to
        the easing in Ball.update(), causing defenders to target stale positions.
        """
        # Ball carrier is the authority when the ball is held
        if world.ball_carrier is not None:
            return world.ball_carrier.x
        ball = getattr(world, "ball", None)
        if ball is not None:
            # Check if ball has a possessed_by reference
            if getattr(ball, "possessed_by", None) is not None:
                return ball.possessed_by.x
            return getattr(ball, "true_x_m", getattr(ball, "x", 60.0))
        return getattr(world, "ball_x_m", 60.0)

    @staticmethod
    def _get_ball_y(world):
        """Extract ball Y position from world.

        When the ball is carried, use the carrier's TRUE position (not the eased ball.y_m).
        """
        if world.ball_carrier is not None:
            return world.ball_carrier.y
        ball = getattr(world, "ball", None)
        if ball is not None:
            if getattr(ball, "possessed_by", None) is not None:
                return ball.possessed_by.y
            return getattr(ball, "true_y_m", getattr(ball, "y", 35.0))
        return getattr(world, "ball_y_m", 35.0)

    # ─────────────────────────────────────────────────────────────────
    # DEPTH CLASSIFICATION
    # ─────────────────────────────────────────────────────────────────

    def classify_defender_depth(self, world, d):
        """Classify a defender as PRIMARY, SECONDARY, or PASSIVE based on
        how far their current X is from their expected role-depth line position.

        expected_x = ball_x + ROLE_DEPTH_M[role]

        PRIMARY:   |d.x - expected_x| <= primary_depth_threshold_m
        SECONDARY: |d.x - expected_x| <= secondary_depth_threshold_m
        PASSIVE:   otherwise

        Thresholds are defined in DefenceTuners and MUST be set to values
        appropriate for the field scale (100m).  The old hardcoded 5m/10m
        values locked all defenders in PASSIVE during kickoff (dx ≈ 40-50m).
        """
        ball_x = self._get_ball_x(world)
        role = getattr(d, "depth_role", "C")
        role_offset = self.tuners.ROLE_DEPTH_M.get(role, 5.0)
        expected_x = ball_x + role_offset
        dx = abs(d.x - expected_x)

        if dx <= self.tuners.primary_depth_threshold_m:
            return "PRIMARY"
        elif dx <= self.tuners.secondary_depth_threshold_m:
            return "SECONDARY"
        else:
            return "PASSIVE"

    # ─────────────────────────────────────────────────────────────────
    # MAIN ENTRY
    # ─────────────────────────────────────────────────────────────────

    def apply(self, world, defenders, base_line_x, lane_y):
        """
        Mode selection — dispatch to the correct sub-behaviour.

        TACKLE_CONTACT         → Lvl‑1: freeze all non-chase defenders
        TACKLE_DOMINANCE       → Lvl‑1: assign markers
        RUCK_FORMING / PTB
            / POST_RUCK        → STEP‑2: retreat behind ball
        OPEN_PLAY              → classify each defender by depth role:
            PRIMARY/SECONDARY  → Lvl‑1 step-up/chase (ball-relative depth)
            PASSIVE            → STEP‑2 retreat only
        KICKOFF                → Lvl‑1 step-up for all defenders

        NOTE: `base_line_x` is used as a geometry reference for lane_y
        only.  target_x is always ball_x + ROLE_DEPTH_M[role], so the
        line tracks the ball dynamically, not a fixed ruck anchor.
        """
        # -- Kickoff chase: defenders advance toward ball's aerial position --
        # During kickoff flight (ball in air, no ball carrier yet), defenders
        # must chase forward into the receiving half to apply pressure.
        # ball_x during flight is ~15-25m (ball.true_x_m), so defenders
        # starting at ~62m chase toward ~20-30m.
        # After catch, world.ball_carrier is set → guard fails →
        # normal ball-relative depth takes over seamlessly.
        if world.in_kickoff and world.ball_carrier is None:
            ball_x = self._get_ball_x(world)
            for d in defenders:
                d.target_x = ball_x + 5.0   # chase forward, 5m in front of ball
                d.target_y = d.y             # maintain lane
            return

        # ═══════════════════════════════════════════════════════════════
        # STEP‑03A‑06C — TES tackle-phase guard
        #
        # During TES (TACKLE_INIT through TACKLE_DOMINANCE), defenders
        # inside the ruck bubble (~3.5m from BC) must freeze so they
        # don't interfere with contact physics / TES ownership.
        #
        # Defenders OUTSIDE the bubble continue normal TES-safe
        # behaviour: spacing and shape are maintained, but chase/step-up
        # and pre-tackle are blocked by guards in the dispatch helpers.
        # ═══════════════════════════════════════════════════════════════
        bc = world.ball_carrier
        if is_tackle_phase(world):
            for d in defenders:
                if bc is not None and is_in_ruck_bubble(d, bc):
                    d.frozen = True
                    d.target_x = d.x
                    d.target_y = d.y
                else:
                    # Players outside the bubble should NOT hold
                    # target_x/y — they'd override the geometry/spacing
                    # system.  Instead we just ensure they're unfrozen
                    # and let the dispatch below handle positioning.
                    d.frozen = False
            # Fall through to normal sub-state dispatch so that
            # TACKLE_CONTACT freezes non-bubble players too (for
            # the marker-assignment case), and non-bubble players
            # in TACKLE_DRIVE/DOMINANCE get correct spacing from
            # the existing handler below.
            # NOTE: chase/step-up helpers have early-return guards
            # for tackle phase, so they won't fire.

        sub = world.sub_state

        # -- TACKLE_CONTACT: freeze all non-chase defenders --
        if sub == GameSubState.TACKLE_CONTACT:
            return self.lvl1.apply_contact(world, defenders)

        # -- TACKLE_DOMINANCE: assign markers --
        if sub == GameSubState.TACKLE_DOMINANCE:
            return self.lvl1.apply_markers(world, defenders)

        # -- PTB / post-ruck: retreat --
        if sub in (GameSubState.RUCK_FORMING, GameSubState.PTB, GameSubState.POST_RUCK_OPEN_PLAY):
            return self.step2.apply_retreat(world, defenders, base_line_x, lane_y)

        # -- OPEN_PLAY: classify each defender FIRST, THEN dispatch --
        ball_x = self._get_ball_x(world)
        ball_y = self._get_ball_y(world)

        # STEP 1 — classify FIRST (NO movement before this)
        for d in defenders:
            d.depth_class = self.classify_defender_depth(world, d)

        # ── DIAGNOSTIC: defence debug print ──
        print("\n=== DEFENCE DEBUG ===")
        for i, d in enumerate(defenders):
            role = getattr(d, "depth_role", "C")
            role_offset = self.tuners.ROLE_DEPTH_M.get(role, 5.0)
            expected_x = ball_x + role_offset
            dx = abs(d.x - expected_x)
            print(
                f"DEF {i:02d} | role={role:<6} | "
                f"x={d.x:6.2f} → expected={expected_x:6.2f} | dx={dx:5.2f} | "
                f"class={d.depth_class:<9} | "
                f"target_x={d.target_x:6.2f} | "
                f"state={world.sub_state.name}"
            )

        # Split defenders by depth class
        active_defenders = [d for d in defenders if d.depth_class in ("PRIMARY", "SECONDARY")]
        passive_defenders = [d for d in defenders if d.depth_class == "PASSIVE"]

        # STEP 2 — active defenders step-up/chase (ball-relative depth)
        if active_defenders:
            step_up_threshold = self.tuners.def_line_depth_m * 0.15
            chase_threshold = self.tuners.def_line_depth_m * 0.30

            if ball_x < base_line_x + step_up_threshold:
                self._apply_step_up_to_active(world, active_defenders, base_line_x, lane_y)
            elif ball_x < base_line_x + chase_threshold:
                self._apply_chase_to_active(world, active_defenders, base_line_x, lane_y)
            else:
                self._apply_chase_to_active(world, active_defenders, base_line_x, lane_y)

        # STEP 3 — passive defenders retreat (ball-relative depth, correct +X direction)
        for d in passive_defenders:
            if d.defender_role == DefenderRole.KICK_CHASE:
                continue
            if getattr(d, "freeze_timer", 0.0) > 0:
                d.target_x = d.x
                d.target_y = d.y
                continue
            if _is_marker(d):
                d.target_x = world.ruck_x_m + _marker_offset(d)
                d.target_y = world.ruck_y_m
                d.frozen = True
                continue

            d.retreating = True
            role = getattr(d, "depth_role", "C")
            role_offset = self.tuners.ROLE_DEPTH_M.get(role, 5.0)
            lane_y_target = getattr(d, "_lane_y", ball_y)
            self._apply_passive_retreat(d, ball_x, ball_y, role_offset, lane_y_target)

        # STEP 4 — fallback: step-up ALL when no active defenders exist
        if not active_defenders:
            self._apply_step_up_to_active(world, defenders, base_line_x, lane_y)

    # ─────────────────────────────────────────────────────────────────
    # SUB-DISPATCH HELPERS
    # ─────────────────────────────────────────────────────────────────

    def _apply_step_up_to_all(self, world, defenders, base_line_x, lane_y):
        """Push ALL defenders into their ball-relative role-depth bands.

        target_x = ball_x + ROLE_DEPTH_M[role] so defenders stay
        behind the ball carrier. This MUST be called before
        classify_defender_depth() so that defenders are close enough to
        their expected_x for dx to be small (0-5m), producing PRIMARY/
        SECONDARY classifications instead of PASSIVE for all.

        Stores d._lane_y for each defender so that _apply_passive_retreat()
        has access to the geometry lane target when blending Y.
        """
        # ═══════════════════════════════════════════════════════════════
        # STEP‑03A‑06C — TES active guard: no step-up during tackle phase
        # ═══════════════════════════════════════════════════════════════
        if is_tackle_phase(world):
            return

        ball_x = self._get_ball_x(world)
        ball_y = self._get_ball_y(world)
        for i, d in enumerate(defenders):
            role = getattr(d, "depth_role", "C")
            role_offset = self.tuners.ROLE_DEPTH_M.get(role, 5.0)
            d._lane_y = lane_y[i]
            expected_x = ball_x + role_offset
            dx = expected_x - d.x
            max_stepup_step = 3.0
            if dx > max_stepup_step:
                d.target_x = d.x + max_stepup_step
            else:
                d.target_x = expected_x
            d.target_y = lane_y[i] * 0.3 + ball_y * 0.7

    def _apply_step_up_to_active(self, world, defenders, base_line_x, lane_y):
        """Apply step-up to active defenders using ball-relative role depth."""
        # ═══════════════════════════════════════════════════════════════
        # STEP‑03A‑06C — TES active guard: no step-up during tackle phase
        # ═══════════════════════════════════════════════════════════════
        if is_tackle_phase(world):
            return

        sorted_defs = sorted(defenders, key=lambda d: d.y)
        lane_targets = self.def_geometry.compute_line_shape(world, sorted_defs, base_line_x)

        ball_x = self._get_ball_x(world)
        ball_y = self._get_ball_y(world)
        for i, d in enumerate(sorted_defs):
            if d.defender_role == DefenderRole.KICK_CHASE:
                continue
            if getattr(d, "freeze_timer", 0.0) > 0:
                d.target_x = d.x
                d.target_y = d.y
                continue
            if _is_marker(d):
                d.target_x = world.ruck_x_m + _marker_offset(d)
                d.target_y = world.ruck_y_m
                d.frozen = True
                continue

            role = getattr(d, "depth_role", "C")
            role_offset = self.tuners.ROLE_DEPTH_M.get(role, 5.0)
            expected_x = ball_x + role_offset
            dx = expected_x - d.x
            max_stepup_step = 3.0
            if dx > max_stepup_step:
                d.target_x = d.x + max_stepup_step
            else:
                d.target_x = expected_x

            if i < len(lane_targets):
                d.target_y = lane_targets[i] * 0.3 + ball_y * 0.7

    def _apply_chase_to_active(self, world, defenders, base_line_x, lane_y):
        """Apply chase to active defenders using engagement steering with depth blending."""
        # ═══════════════════════════════════════════════════════════════
        # STEP‑03A‑06C — TES active guard: no chase during tackle phase
        # ═══════════════════════════════════════════════════════════════
        if is_tackle_phase(world):
            return

        # Use ball carrier's TRUE position (not eased ball.x_m) — critical for chase steering
        bc = world.ball_carrier
        if bc is not None:
            bc_x = bc.x
            bc_y = bc.y
        else:
            ball = world.ball
            bc_x = getattr(ball, "true_x_m", getattr(ball, "x", 60.0))
            bc_y = getattr(ball, "true_y_m", getattr(ball, "y", 35.0))

        sorted_defs = sorted(defenders, key=lambda d: d.y)
        lane_targets = self.def_geometry.compute_line_shape(world, sorted_defs, base_line_x)

        for i, d in enumerate(sorted_defs):
            if d.defender_role == DefenderRole.KICK_CHASE:
                continue
            if getattr(d, "freeze_timer", 0.0) > 0:
                d.target_x = d.x
                d.target_y = d.y
                continue
            if _is_marker(d):
                d.target_x = world.ruck_x_m + _marker_offset(d)
                d.target_y = world.ruck_y_m
                d.frozen = True
                continue

            role = getattr(d, "depth_role", "C")
            role_offset = self.tuners.ROLE_DEPTH_M.get(role, 5.0)

            # 1. Compute lateral steering toward BC
            dx = bc_x - d.x
            dy = bc_y - d.y

            # 2. Compute angle toward BC
            angle = math.atan2(dy, dx)

            # ───────────────────────────────────────────────
            # STEP‑03A‑06 — Pre‑tackle attempt (tackle initiation)
            # ───────────────────────────────────────────────
            dist = math.hypot(dx, dy)
            tackle_radius = self.tuners.tackle_radius_m
            dive_radius = self.tuners.dive_radius_m

            # Set window for apply_chase compatibility (not used by unified path
            # but avoids None errors if the old path reads it)
            if dist <= tackle_radius:
                d._pre_tackle_window = "NORMAL"
            elif dist <= dive_radius:
                d._pre_tackle_window = "DIVE"
            else:
                d._pre_tackle_window = None

            if bc is not None and d._pre_tackle_window is not None:
                # Import here to avoid circular import
                from game.defence.defence_lvl_1.defensive_behaviour_lvl1 import try_pre_tackle
                if try_pre_tackle(world, d, bc, self.tuners):
                    # Successful tackle → start TES
                    ope = world.open_play_engine
                    ope._start_tackle(bc, d)
                    return  # stop chase for this defender

            # 3. Role-based steering factor
            role_factor = self.tuners.ROLE_STEER_FACTOR.get(role, 1.0)

            # 4. Blend BC y with lane y (70/30)
            lane_y_target = lane_targets[i] if i < len(lane_targets) else bc_y
            target_y = (bc_y * 0.7) + (lane_y_target * 0.3)

            # 5. Ball-relative depth anchor blended with BC-chase steering
            expected_x = bc_x + role_offset
            chase_target_x = bc_x + (self.tuners.engagement_offset_m * math.cos(angle))

            # Blend: 30% ball-relative depth, 70% chase steering
            d.target_x = expected_x * 0.3 + chase_target_x * 0.7

            # 6. Y with engagement steering and geometry blend (40/60)
            steer_y = target_y + (self.tuners.engagement_offset_m * math.sin(angle)) * role_factor
            if i < len(lane_targets):
                geom_y = lane_targets[i]
                d.target_y = geom_y * 0.4 + steer_y * 0.6
            else:
                d.target_y = steer_y

            # 7. Mark defender as engaging
            d.is_engaging = True

    # ─────────────────────────────────────────────────────────────────
    # PASSIVE RETREAT — speed-clamped, ball-anchored Y
    # ─────────────────────────────────────────────────────────────────

    def _apply_passive_retreat(self, d, ball_x, ball_y, role_offset, lane_y_target):
        """Apply speed-clamped retreat with Y blending.

        Direction is ALWAYS RIGHT (+X).  No sign flips, no negative hacks.
        X-axis is clamped to max_retreat_step (3.0 m/tick) so defenders
        cannot teleport across the field in one frame.

        Y-axis blend: 30% lane_y, 70% ball_y — same blend as Step-01C
        step-up, ensuring vertical alignment stays anchored on the ball
        while lane_y provides lateral spacing.
        """
        # ═══════════════════════════════════════════════════════════════
        # STEP‑03A‑06C — TES guard: this is called per-defender inside a
        # loop in apply(), so we can't return early with world.  The
        # caller should have already guarded against tackle-phase.
        # This is a safety net — if somehow called during TES, no-op.
        # ═══════════════════════════════════════════════════════════════
        expected_x = ball_x + role_offset + 2.0  # depth buffer
        max_retreat_step = 3.0                    # metres per tick, positive
        dx = expected_x - d.x
        if dx > max_retreat_step:
            d.target_x = d.x + max_retreat_step
        else:
            d.target_x = expected_x

        d.target_y = lane_y_target * 0.3 + ball_y * 0.7


# ─────────────────────────────────────────────────────────────────────
# MODULE-LEVEL HELPERS (no self needed)
# ─────────────────────────────────────────────────────────────────────

def _is_marker(d):
    return getattr(d, "defender_role", None) in (
        DefenderRole.MARKER_A,
        DefenderRole.MARKER_B,
    )


def _marker_offset(d):
    role = getattr(d, "defender_role", None)
    if role == DefenderRole.MARKER_A:
        return 1.0
    elif role == DefenderRole.MARKER_B:
        return 2.0
    return 1.5
