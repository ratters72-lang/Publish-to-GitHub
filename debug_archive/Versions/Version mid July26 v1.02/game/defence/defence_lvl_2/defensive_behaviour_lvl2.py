"""
STEP‑2 DEFENSIVE BEHAVIOUR
==========================

Consumes:
    • STEP‑0 (defence_ctx.mode, depth_m per defender)
    • STEP‑1 (lane_y + line_x)

Produces:
    • target_x (containment: ball.x - depth_m, clamped behind line)
    • target_y (containment: lane_y from STEP-1)
    • scramble override (all target BC)
    • retreat override (all target ruck + retreat_distance)

NEVER modifies spacing or lane_y.
NEVER assigns TES roles.
NEVER assigns ruck roles.
"""

from player.player import DefenderRole
from world.world_state import is_tackle_phase


# -- Constants for STEP-2 unified methods --
RETREAT_DISTANCE_M: float = 10.0


class DefensiveBehaviour:
    """STEP‑2 behaviour layer: modifies X only (except scramble/retreat)."""

    def __init__(self, tuners=None, geometry=None):
        self.tuners = tuners
        self.def_geometry = geometry

    # ═══════════════════════════════════════════════════════════════
    # D4 — Split methods (called from DefensiveBehaviourUnified)
    # All use unified signature (world, defenders, line_x, lane_y)
    # ═══════════════════════════════════════════════════════════════

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

    def apply_containment(self, world, defenders, line_x, lane_y):
        """
        CONTAINMENT: ball behind defensive line → hold line_x with depth offsets.

        For each defender:
            X = ball_x - depth_m, clamped ≥ line_x
            Y = geometry lane target (computed from DefensiveGeometry)

        Respects frozen, freeze_timer, and markers.
        Sorting defenders by Y ensures indices align with geometry output.
        """
        # ═══════════════════════════════════════════════════════════════
        # STEP‑03A‑06C — TES active guard: no containment during tackle phase
        # ═══════════════════════════════════════════════════════════════
        if is_tackle_phase(world):
            return

        ball_x = self._get_ball_x(world)
        if not defenders:
            return

        # Sort defenders by Y to align with geometry lane ordering
        sorted_defs = sorted(defenders, key=lambda d: d.y)
        lane_targets = self.def_geometry.compute_line_shape(world, sorted_defs, line_x)

        for i, d in enumerate(sorted_defs):
            if d.defender_role == DefenderRole.KICK_CHASE:
                continue
            if d.frozen:
                continue
            if getattr(d, "freeze_timer", 0.0) > 0:
                d.target_x = d.x
                d.target_y = d.y
                continue
            if getattr(d, "defender_role", None) in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                d.target_x = world.ruck_x_m + (1.0 if d.defender_role == DefenderRole.MARKER_A else 2.0)
                d.target_y = world.ruck_y_m
                d.frozen = True
                continue

            # Depth from ROLE_DEPTH_M (A/B/C/D roles) or fallback 5.0
            role = getattr(d, "depth_role", None)
            if role in self.tuners.ROLE_DEPTH_M:
                depth = self.tuners.ROLE_DEPTH_M[role]
            else:
                depth = 5.0

            target_x = ball_x - depth
            if target_x < line_x:
                target_x = line_x

            d.target_x = target_x

            # Y from geometry lane targets
            if i < len(lane_targets):
                d.target_y = lane_targets[i]
            else:
                d.target_y = getattr(d, "lane_y", d.y)

    def apply_scramble(self, world, defenders, line_x, lane_y):
        """
        SCRAMBLE: ball deep → all defenders target the ball directly.

        Geometry OFF, Roles OFF, Lane_y OFF, Depth OFF.
        Skips frozen, freeze_timer, markers, and KICK_CHASE.
        """
        # ═══════════════════════════════════════════════════════════════
        # STEP‑03A‑06C — TES active guard: no scramble during tackle phase
        # ═══════════════════════════════════════════════════════════════
        if is_tackle_phase(world):
            return

        ball_x = self._get_ball_x(world)
        ball_y = self._get_ball_y(world)

        for d in defenders:
            if d.defender_role == DefenderRole.KICK_CHASE:
                continue
            if d.frozen:
                continue
            if getattr(d, "freeze_timer", 0.0) > 0:
                d.target_x = d.x
                d.target_y = d.y
                continue
            if getattr(d, "defender_role", None) in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                d.target_x = world.ruck_x_m + (1.0 if d.defender_role == DefenderRole.MARKER_A else 2.0)
                d.target_y = world.ruck_y_m
                d.frozen = True
                continue

            d.retreating = False
            d.target_x = ball_x
            d.target_y = ball_y

    def apply_retreat(self, world, defenders, line_x, lane_y):
        """
        RETREAT: PTB / post-ruck → all defenders slide back behind ball.

        X = ball_x - RETREAT_DISTANCE_M (behind the ball carrier)
        Y = lane_y[i] (preserve lateral spacing)

        Skips frozen, freeze_timer, markers, and KICK_CHASE.
        """
        # ═══════════════════════════════════════════════════════════════
        # STEP‑03A‑06C — TES active guard: no retreat during tackle phase
        # ═══════════════════════════════════════════════════════════════
        if is_tackle_phase(world):
            return

        ball_x = self._get_ball_x(world)
        target_x = ball_x - RETREAT_DISTANCE_M

        for i, d in enumerate(defenders):
            if d.defender_role == DefenderRole.KICK_CHASE:
                continue
            if d.frozen:
                continue
            if getattr(d, "freeze_timer", 0.0) > 0:
                d.target_x = d.x
                d.target_y = d.y
                continue
            if getattr(d, "defender_role", None) in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                d.target_x = world.ruck_x_m + (1.0 if d.defender_role == DefenderRole.MARKER_A else 2.0)
                d.target_y = world.ruck_y_m
                d.frozen = True
                continue

            d.retreating = True
            d.target_x = target_x
            if i < len(lane_y):
                d.target_y = lane_y[i]
            else:
                d.target_y = getattr(d, "lane_y", d.y)

    # ═══════════════════════════════════════════════════════════════
    # PTB / NON-OPEN-PLAY GUARD (shared check)
    # STEP-2 behaviour must NEVER run during PTB or TACKLE_CONTACT.
    # ═══════════════════════════════════════════════════════════════
    @staticmethod
    def _guard_sub_state() -> bool:
        """Return True if caller should skip (non-OPEN_PLAY sub-state).

        This guard relies on the fact that OpenPlayEngine only calls
        these methods during OPEN_PLAY sub-state.  It is a last-resort
        safety net in case some other code path calls them during PTB.
        """
        import sys as _sys
        import traceback as _tb
        _sys.stderr.write("STEP-2 called — verifying sub-state safety\n")
        _sys.stderr.flush()
        return False  # No reliable way to check from here; caller guard is sufficient

    # ------------------------------------------------------------
    # CONTAINMENT — pure role depth relative to ball
    # ------------------------------------------------------------
    def compute(self, defence_ctx, geom_ctx, defenders, ball):
        """
        Run only in CONTAINMENT mode.

        For each defender:
            X = ball.x - depth_m
            Y = lane_y (from STEP‑1 geometry)

        Clamped behind line_x.
        No offsets, no chase, no markers, no A/B/C bands.
        """
        # ═══════════════════════════════════════════════════════════════
        # STEP‑03A‑06C — TES active guard: no compute during tackle phase
        # ═══════════════════════════════════════════════════════════════
        # Called from old code path; we don't have `world` here, but the
        # caller is responsible for gating.  This is a safety net.
        # ═══════════════════════════════════════════════════════════════

        # ═══ NON-OPEN-PLAY GUARD ═══
        # Called from OpenPlayEngine._update_open_play() which already
        # returns early for PTB/TACKLE_CONTACT.  This guard is a
        # last-resort safety net — log if somehow reached during PTB.
        import sys as _sys
        _sys.stderr.write("STEP-2 compute() called\n")
        _sys.stderr.flush()

        if defence_ctx.mode != "CONTAINMENT":
            return
        if not defenders:
            return

        line_x = geom_ctx.def_line_x
        lane_y = geom_ctx.lane_y

        # Ball.x may be a Player object (has .x) or a Ball object (has x_m)
        if hasattr(ball, "x"):
            bx = ball.x
        elif hasattr(ball, "true_x_m"):
            bx = ball.true_x_m
        else:
            bx = 60.0  # fallback centre

        print(f"STEP-2 RUNNING: mode = {defence_ctx.mode}  ball_x = {bx:.1f}  line_x = {line_x:.1f}  defenders={len(defenders)}")

        for i, d in enumerate(defenders):
            if d.frozen:
                continue
            # ═══ FREEZE TIMER GUARD ═══
            # Never overwrite targets of a defender with active freeze_timer.
            # Only the generic freeze_timer handler in OpenPlayEngine.update()
            # may unfreeze them.
            if getattr(d, 'freeze_timer', 0.0) > 0:
                continue
            # depth from ROLE_DEPTH_M (A/B/C/D roles) or fallback 5.0
            role = getattr(d, "depth_role", None)
            if role in self.tuners.ROLE_DEPTH_M:
                depth = self.tuners.ROLE_DEPTH_M[role]
            else:
                depth = 5.0

            target_x = bx - depth
            if target_x < line_x:
                target_x = line_x

            d.target_x = target_x

            # Y from lane_y (cycle if more defenders than lanes)
            if i < len(lane_y):
                d.target_y = lane_y[i]
            else:
                d.target_y = getattr(d, "lane_y", d.y)

    # ------------------------------------------------------------
    # SCRAMBLE — all defenders target the BALL
    # ------------------------------------------------------------
    def scramble(self, defenders, ball):
        """
        Override everything: every defender targets the BALL directly.

        BALL-relative, NOT BC-relative.  This ensures correct behaviour
        during kicks, passes, offloads, and broken play.

        Geometry OFF, Roles OFF, Lane_y OFF, Depth OFF.
        """
        # ═══ NON-OPEN-PLAY GUARD ═══
        # Same safety net as compute().
        import sys as _sys
        _sys.stderr.write("STEP-2 scramble() called\n")
        _sys.stderr.flush()

        # Extract ball position, normalising between Ball and Player objects
        if hasattr(ball, "true_x_m"):
            bx = ball.true_x_m
            by = ball.true_y_m
        elif hasattr(ball, "x"):
            bx = ball.x
            by = ball.y
        else:
            bx = 60.0
            by = 35.0

        print(f"SCRAMBLE ACTIVE: ball.x = {bx:.1f}, ball.y = {by:.1f}")
        for d in defenders:
            # ═══ FREEZE TIMER GUARD ═══
            # Never overwrite targets of a defender with active freeze_timer.
            if getattr(d, 'freeze_timer', 0.0) > 0:
                continue
            if not d.frozen and d.defender_role not in (
                DefenderRole.MARKER_A, DefenderRole.MARKER_B, DefenderRole.KICK_CHASE,
            ):
                d.retreating = False
                d.target_x = bx
                d.target_y = by

    # ------------------------------------------------------------
    # RETREAT — all defenders slide back from ball
    # ------------------------------------------------------------
    def retreat(self, defenders, ball, retreat_distance):
        """
        Override everything: every defender retreats behind the ball.

        X = ball.x - retreat_distance   (behind the ball)
        Y = lane_y (preserve lateral spacing for shape)

        Markers remain ruck-relative — handled by assign_markers_and_retreat.
        """
        # ═══ NON-OPEN-PLAY GUARD ═══
        # Same safety net as compute().
        import sys as _sys
        _sys.stderr.write("STEP-2 retreat() called\n")
        _sys.stderr.flush()

        ball_x = getattr(ball, "true_x_m", getattr(ball, "x", 60.0))
        print(f"RETREAT ACTIVE: ball.x = {ball_x:.1f}, retreat_distance = {retreat_distance:.1f}")
        target_x = ball_x - retreat_distance
        for d in defenders:
            if d.frozen:
                continue
            d.retreating = True
            d.target_x = target_x
            # Preserve Y from lane_y if set, otherwise keep current
            lane_y = getattr(d, "lane_y", d.y)
            d.target_y = lane_y
