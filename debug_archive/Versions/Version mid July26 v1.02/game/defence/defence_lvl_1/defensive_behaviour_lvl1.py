import math

__all__ = ["DefensiveBehaviourLvl1"]

from world.world_state import GameSubState, is_tackle_phase, is_in_ruck_bubble
from player.player import DefenderRole


class DefensiveBehaviourLvl1:

    def __init__(self, tuners=None, geometry=None):
        self.tuners = tuners
        self.def_geometry = geometry

    # ═══════════════════════════════════════════════════════════════
    # D3 — Split methods (called from DefensiveBehaviourUnified)
    # ═══════════════════════════════════════════════════════════════

    def apply_contact(self, world, defenders):
        """
        TACKLE_CONTACT: freeze all non-chase defenders at current position.
        """
        for i, d in enumerate(defenders):
            if d.defender_role == DefenderRole.KICK_CHASE:
                continue
            d.frozen = True
            d.target_x = d.x
            d.target_y = d.y

    def apply_markers(self, world, defenders):
        """
        TACKLE_DOMINANCE: sort by distance to ruck → assign MARKER_A/B with freeze.
        """
        sorted_by_ruck = sorted(
            defenders,
            key=lambda d: (d.x - world.ruck_x_m) ** 2 + (d.y - world.ruck_y_m) ** 2
        )

        # Filter KICK_CHASE out of the sorted list for assignment
        sorted_non_chase = [d for d in sorted_by_ruck if d.defender_role != DefenderRole.KICK_CHASE]

        if len(sorted_non_chase) > 0:
            m1 = sorted_non_chase[0]
            m1.defender_role = DefenderRole.MARKER_A
            m1.freeze_timer = self.tuners.MARKER_A_FREEZE_AFTER_PTB
            m1.frozen = True
            m1.target_x = world.ruck_x_m + 1.0
            m1.target_y = world.ruck_y_m
        if len(sorted_non_chase) > 1:
            m2 = sorted_non_chase[1]
            m2.defender_role = DefenderRole.MARKER_B
            m2.freeze_timer = self.tuners.MARKER_B_FREEZE_AFTER_PTB
            m2.frozen = True
            m2.target_x = world.ruck_x_m + 2.0
            m2.target_y = world.ruck_y_m

    def apply_step_up(self, world, defenders, line_x, lane_y):
        """
        STEP UP: non-frozen, non-marker defenders → target_x = line_x,
        target_y = geometry lane with 80/20 blend to prevent jitter.
        """
        # ═══════════════════════════════════════════════════════════════
        # STEP‑03A‑06C — TES active guard: no step-up during tackle phase
        # ═══════════════════════════════════════════════════════════════
        if is_tackle_phase(world):
            return

        sorted_defs = sorted(defenders, key=lambda d: d.y)
        # Compute geometry lane targets for this line
        lane_targets = self.def_geometry.compute_line_shape(world, sorted_defs, line_x)

        for i, d in enumerate(sorted_defs):
            if d.defender_role == DefenderRole.KICK_CHASE:
                continue
            if getattr(d, "freeze_timer", 0.0) > 0:
                d.target_x = d.x
                d.target_y = d.y
                continue
            if self._is_marker(d):
                d.target_x = world.ruck_x_m + (1.0 if d.defender_role == DefenderRole.MARKER_A else 2.0)
                d.target_y = world.ruck_y_m
                d.frozen = True
                continue
            d.target_x = line_x
            if i < len(lane_targets):
                # 80% geometry, 20% current Y to prevent jitter
                d.target_y = lane_targets[i] * 0.8 + d.y * 0.2

    def apply_chase(self, world, defenders, line_x, lane_y):
        """
        CHASE: engagement steering toward ball carrier with geometry blend.

        Defenders compute angle to BC, blend BC y with lane y (70/30),
        apply role-based steering factor, and set target with engagement
        offset to create a curved approach.

        Geometry lane targets are blended 40/60 with steering to preserve
        spacing without fighting the chase direction.
        """
        # ═══════════════════════════════════════════════════════════════
        # STEP‑03A‑06C — TES active guard: no chase during tackle phase
        # ═══════════════════════════════════════════════════════════════
        if is_tackle_phase(world):
            return

        # Use ball carrier's TRUE position (not eased ball.x_m) — critical for chase steering
        if world.ball_carrier is not None:
            bc_x = world.ball_carrier.x
            bc_y = world.ball_carrier.y
        else:
            ball = world.ball
            bc_x = getattr(ball, "true_x_m", getattr(ball, "x", 60.0))
            bc_y = getattr(ball, "true_y_m", getattr(ball, "y", 35.0))

        sorted_defs = sorted(defenders, key=lambda d: d.y)
        # Compute geometry lane targets for spacing preservation
        lane_targets = self.def_geometry.compute_line_shape(world, sorted_defs, line_x)

        for i, d in enumerate(sorted_defs):
            # ───────────────────────────────────────────────
            # STEP‑02A‑06 — Missed tackle recovery
            # ───────────────────────────────────────────────
            if getattr(d, "recovery_timer", 0.0) > 0.0:
                d.recovery_timer -= world.dt

                # ─────────────────────────────────────────────
                # STEP‑02A‑08 — Stumble movement
                # ─────────────────────────────────────────────
                # Push defender backward along facing direction
                stumble_speed = 1.0  # m/s, tune later
                d.x -= math.cos(d.facing) * stumble_speed * world.dt
                d.y -= math.sin(d.facing) * stumble_speed * world.dt

                d.target_x = d.x
                d.target_y = d.y
                continue

            # ───────────────────────────────────────────────
            # STEP‑02A‑11 — Auto‑rotate toward BC after recovery
            # ───────────────────────────────────────────────
            if getattr(d, "recovery_timer", 0.0) <= 0.0 and d.state == "MISSED_TACKLE":
                dx = bc_x - d.x
                dy = bc_y - d.y
                d.facing = math.atan2(dy, dx)
                d.state = "CHASE"  # re‑engage

            if d.defender_role == DefenderRole.KICK_CHASE:
                continue
            if getattr(d, "freeze_timer", 0.0) > 0:
                d.target_x = d.x
                d.target_y = d.y
                continue
            if self._is_marker(d):
                d.target_x = world.ruck_x_m + (1.0 if d.defender_role == DefenderRole.MARKER_A else 2.0)
                d.target_y = world.ruck_y_m
                d.frozen = True
                continue

            # 1. Compute lateral steering toward BC
            dx = bc_x - d.x
            dy = bc_y - d.y

            # 2. Compute angle toward BC
            angle = math.atan2(dy, dx)

            # ───────────────────────────────────────────────
            # STEP‑02A‑02 — Pre‑tackle attempt hook
            # ───────────────────────────────────────────────

            dist = math.hypot(dx, dy)

            tackle_radius = self.tuners.tackle_radius_m
            dive_radius   = self.tuners.dive_radius_m

            # Normal tackle window
            if dist <= tackle_radius:
                d._pre_tackle_window = "NORMAL"

            # Diving tackle window
            elif dist <= dive_radius:
                d._pre_tackle_window = "DIVE"

            else:
                d._pre_tackle_window = None

            # ───────────────────────────────────────────────
            # STEP‑02A‑04 — Trigger TES on successful pre‑tackle
            # ───────────────────────────────────────────────

            if d._pre_tackle_window is not None:
                if try_pre_tackle(world, d, world.ball_carrier, self.tuners):
                    # Successful tackle → start TES
                    ope = world.open_play_engine
                    ope._start_tackle(world.ball_carrier, d)
                    return  # stop chase for this defender

            # 3. Compute steering strength based on depth role
            role = getattr(d, "depth_role", "C")
            role_factor = self.tuners.ROLE_STEER_FACTOR.get(role, 1.0)

            # 4. Apply curved steering (slight bias toward lane_y)
            lane_y_target = lane_targets[i] if i < len(lane_targets) else bc_y
            target_y = (bc_y * 0.7) + (lane_y_target * 0.3)

            # 5. Compute final target with engagement offset
            d.target_x = bc_x - (self.tuners.engagement_offset_m * math.cos(angle))
            d.target_y = target_y + (self.tuners.engagement_offset_m * math.sin(angle)) * role_factor

            # 6. Blend geometry with steering: 40% geometry, 60% steering
            if i < len(lane_targets):
                geom_y = lane_targets[i]
                d.target_y = geom_y * 0.4 + d.target_y * 0.6

            # 7. Mark defender as "engaging"
            d.is_engaging = True

    # ═══════════════════════════════════════════════════════════════
    # Original apply() — kept for backward compatibility
    # ═══════════════════════════════════════════════════════════════

    def apply(self, world, defenders, line_x, lane_y):
        # ═══════════════════════════════════════════════════════════════
        # STEP‑03A‑06C — TES tackle-phase guard
        #
        # During TES, freeze defenders inside the ruck bubble and
        # do nothing more.  Defenders outside the bubble are unfrozen
        # and get no movement override.
        # ═══════════════════════════════════════════════════════════════
        bc = world.ball_carrier
        if is_tackle_phase(world):
            for d in defenders:
                if bc is not None and is_in_ruck_bubble(d, bc):
                    d.frozen = True
                    d.target_x = d.x
                    d.target_y = d.y
                else:
                    d.frozen = False
            return

        sub = world.sub_state

        non_chase_defs = [d for d in defenders if d.defender_role != DefenderRole.KICK_CHASE]
        if not non_chase_defs:
            return

        if sub == GameSubState.TACKLE_CONTACT:
            for d in non_chase_defs:
                d.frozen = True
                d.target_x = d.x
                d.target_y = d.y
            return

        if sub == GameSubState.TACKLE_DOMINANCE:
            sorted_by_ruck = sorted(
                non_chase_defs,
                key=lambda d: (d.x - world.ruck_x_m) ** 2 + (d.y - world.ruck_y_m) ** 2
            )
            if len(sorted_by_ruck) > 0:
                m1 = sorted_by_ruck[0]
                m1.defender_role = DefenderRole.MARKER_A
                m1.freeze_timer = self.tuners.MARKER_A_FREEZE_AFTER_PTB
                m1.frozen = True
                m1.target_x = world.ruck_x_m + 1.0
                m1.target_y = world.ruck_y_m
            if len(sorted_by_ruck) > 1:
                m2 = sorted_by_ruck[1]
                m2.defender_role = DefenderRole.MARKER_B
                m2.freeze_timer = self.tuners.MARKER_B_FREEZE_AFTER_PTB
                m2.frozen = True
                m2.target_x = world.ruck_x_m + 2.0
                m2.target_y = world.ruck_y_m
            return

        sorted_defs = sorted(non_chase_defs, key=lambda d: d.y)

        for i, d in enumerate(sorted_defs):
            if getattr(d, "freeze_timer", 0.0) > 0:
                d.target_x = d.x
                d.target_y = d.y
                continue

            if self._is_marker(d):
                offset = 1.0 if d.defender_role == DefenderRole.MARKER_A else 2.0
                d.target_x = world.ruck_x_m + offset
                d.target_y = world.ruck_y_m
                d.frozen = True
                continue

            if sub in (GameSubState.RUCK_FORMING, GameSubState.PTB, GameSubState.POST_RUCK_OPEN_PLAY):
                self._apply_retreat(d, line_x, lane_y, i)
                continue

            if sub == GameSubState.OPEN_PLAY:
                self._apply_open_play(d, world, line_x, lane_y, i)
                continue

            d.target_x = line_x
            if i < len(lane_y):
                d.target_y = lane_y[i]

    @staticmethod
    def _is_marker(d):
        return getattr(d, "defender_role", None) in (
            DefenderRole.MARKER_A,
            DefenderRole.MARKER_B,
        )

    def _apply_retreat(self, d, line_x, lane_y, idx):
        if self._is_marker(d):
            return
        retreat_line = line_x + (self.tuners.def_line_depth_m * 0.05)
        d.target_x = retreat_line if d.x < retreat_line else line_x
        if idx < len(lane_y):
            d.target_y = lane_y[idx]

    @staticmethod
    def _get_ball_x(world):
        """Extract ball X position, preferring ball carrier's true position."""
        if world.ball_carrier is not None:
            return world.ball_carrier.x
        ball = getattr(world, "ball", None)
        if ball is not None:
            if getattr(ball, "possessed_by", None) is not None:
                return ball.possessed_by.x
            return getattr(ball, "true_x_m", getattr(ball, "x", 60.0))
        return getattr(world, "ball_x_m", 60.0)

    @staticmethod
    def _get_ball_y(world):
        """Extract ball Y position, preferring ball carrier's true position."""
        if world.ball_carrier is not None:
            return world.ball_carrier.y
        ball = getattr(world, "ball", None)
        if ball is not None:
            if getattr(ball, "possessed_by", None) is not None:
                return ball.possessed_by.y
            return getattr(ball, "true_y_m", getattr(ball, "y", 35.0))
        return getattr(world, "ball_y_m", 35.0)

    def _apply_open_play(self, d, world, line_x, lane_y, idx):
        ball_x = self._get_ball_x(world)
        ball_y = self._get_ball_y(world)

        step_up = self.tuners.defender_activation_radius_m * 0.15
        chase = self.tuners.defender_activation_radius_m * 0.30

        if ball_x < line_x + step_up:
            d.target_x = line_x
        elif ball_x < line_x + chase:
            d.target_x = line_x - 1.0
        elif ball_x > line_x + chase:
            d.target_x = ball_x
            d.target_y = ball_y
            return
        else:
            d.target_x = line_x

        if idx < len(lane_y):
            d.target_y = lane_y[idx]


# ═══════════════════════════════════════════════════════════════════
# Step‑03A‑06 — Pre‑tackle trigger (realistic contact conditions)
# ═══════════════════════════════════════════════════════════════════

def forward_projection_hits_bc(defender, bc):
    """Return True if defender's forward-facing ray intersects BC's bounding circle.

    Casts a ray of length 2.0m from defender along defender.facing.
    Uses the CollisionBody radius for BC (default 0.55m).

    This is a line-segment-vs-circle test.
    """
    ray_length = 2.0
    defender_radius = getattr(defender, 'radius', 0.55)
    bc_radius = getattr(bc, 'radius', 0.55)
    combined_radius = bc_radius  # treat defender as point for ray

    # Ray endpoint
    ex = defender.x + math.cos(defender.facing) * ray_length
    ey = defender.y + math.sin(defender.facing) * ray_length

    # Vector from ray start to BC centre
    dx = bc.x - defender.x
    dy = bc.y - defender.y

    # Project BC centre onto ray direction (clamped 0..1)
    ray_dx = ex - defender.x
    ray_dy = ey - defender.y
    ray_len_sq = ray_dx * ray_dx + ray_dy * ray_dy
    if ray_len_sq < 0.0001:
        return False

    t = (dx * ray_dx + dy * ray_dy) / ray_len_sq
    t = max(0.0, min(1.0, t))

    # Closest point on ray to BC centre
    closest_x = defender.x + t * ray_dx
    closest_y = defender.y + t * ray_dy

    # Distance from closest point to BC centre
    dist_to_center = math.hypot(bc.x - closest_x, bc.y - closest_y)

    return dist_to_center < combined_radius


def try_pre_tackle(world, defender, bc, tuners):
    """
    Return True if tackle should start (TES entry).
    Return False if no valid tackle opportunity.

    Uses realistic rugby league geometry — no probability scoring,
    no speed/skill checks.  TES handles all contact outcomes.
    Slow defenders still tackle if close enough.

    Conditions (any one is sufficient):
      1. Physical overlap: bounding circles touch.
      2. Forward projection: defender's facing ray intersects BC.
      3. Close enough (dist < 1.8m) AND approach angle < 110°.

    Called from Lvl‑1 apply_chase() and unified _apply_chase_to_active().
    The caller is responsible for starting TES on True.
    """
    # Vector from defender → BC (approach vector)
    dx = bc.x - defender.x
    dy = bc.y - defender.y
    dist = math.hypot(dx, dy)

    # ── 1. Physical overlap detection ──
    # If bounding circles overlap, tackle fires regardless of angle.
    defender_radius = getattr(defender, 'radius', getattr(defender, 'collision_radius', 0.55))
    bc_radius = getattr(bc, 'radius', getattr(bc, 'collision_radius', 0.55))
    if dist < (bc_radius + defender_radius):
        return True

    # ── 2. Distance gate (too far to reach) ──
    if dist > 1.8:
        return False

    # ── 3. Approach angle ──
    # Angle between defender.facing and the defender→BC direction
    approach_angle = math.atan2(dy, dx)
    angle_diff = abs(approach_angle - defender.facing)
    if angle_diff > math.pi:
        angle_diff = 2 * math.pi - angle_diff
    angle_deg = angle_diff * (180.0 / math.pi)

    # ── 4. Forward projection touch detection ──
    # If defender's forward-facing ray intersects BC, tackle fires.
    if forward_projection_hits_bc(defender, bc):
        return True

    # ── 5. Angle gate (defender facing away from BC) ──
    if angle_deg > 110.0:
        return False

    # ── 6. Final simplified trigger ──
    # Close enough AND angle reasonable → tackle.
    return True
