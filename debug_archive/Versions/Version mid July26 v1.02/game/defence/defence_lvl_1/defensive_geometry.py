"""
defensive_geometry.py — STEP‑1 defensive line geometry.

Computes lane_y positions for defenders based on:
  - Compression/expansion (ball X relative to field centre)
  - Dynamic spacing (base spacing × compression factor)
  - Per-role width curve (depth role multipliers on lane offset)
  - Sideline margins (clamp outermost lanes)

This is the foundation of Phase‑E — all defender Y targets flow from here.
"""

import math
from player.player import Player
from world.world_state import WorldState


class DefensiveGeometry:
    """Computes lane_y targets for a defensive line."""

    def __init__(self, tuners):
        """Store a reference to the DefenceTuners singleton."""
        self.tuners = tuners

    # ─────────────────────────────────────────────────────────────
    # PUBLIC ENTRY POINT
    # ─────────────────────────────────────────────────────────────

    def compute_line_shape(
        self,
        world: WorldState,
        defenders: list[Player],
        line_x: float,
    ) -> list[float]:
        """
        Compute target_y positions for each defender in order (sorted by Y).

        Pipeline:
            1. Compression factor from ball X
            2. Dynamic spacing from compression
            3. Width curve per defender depth role
            4. Lane targets (evenly spaced around central anchor)
            5. Sideline margin clamping

        Returns a lane_y list of same length as defenders (sorted by current Y).
        """
        sorted_defenders = sorted(defenders, key=lambda d: d.y)

        # 1. Compression based on ball X
        compression = self._compute_compression(world)

        # 2. Dynamic spacing
        spacing = self._compute_spacing(compression)

        # 3. Width curve per defender
        width_curve = self._compute_width_curve(sorted_defenders)

        # 4. Lane targets
        lane_targets = self._compute_lane_targets(
            world, sorted_defenders, spacing, width_curve
        )

        # 5. Sideline margins
        lane_targets = [self._apply_sideline_margin(y) for y in lane_targets]

        return lane_targets

    # ─────────────────────────────────────────────────────────────
    # STEP 1 — COMPRESSION
    # ─────────────────────────────────────────────────────────────

    def _compute_compression(self, world: WorldState) -> float:
        """
        Compression factor based on ball X distance from field centre.

        When ball is near our own tryline (behind us) → compress (factor < 1).
        When ball is near opposition tryline (ahead) → expand (factor > 1).

        Uses the tuners.compression_factor for scaling strength.
        """
        # Use ball carrier's TRUE position (not eased ball.x_m)
        ball_x: float = 60.0
        if world.ball_carrier is not None:
            ball_x = world.ball_carrier.x
        elif world.ball is not None:
            ball_obj = world.ball
            if getattr(ball_obj, "possessed_by", None) is not None:
                ball_x = ball_obj.possessed_by.x
            else:
                ball_x = getattr(ball_obj, "true_x_m", getattr(ball_obj, "x", 60.0))
        else:
            ball_x = getattr(world, "ball_x_m", 60.0)
        bc_x = ball_x

        field_centre = 60.0  # midpoint of 10..110 field
        dist_from_centre = abs(bc_x - field_centre)
        norm_dist = dist_from_centre / field_centre  # 0..1

        # Ball behind centre → compress (<1), ahead → expand (>1)
        direction = -1.0 if bc_x < field_centre else 1.0
        factor = 1.0 + direction * norm_dist * (self.tuners.compression_factor * 0.5)

        return max(0.6, min(1.2, factor))

    # ─────────────────────────────────────────────────────────────
    # STEP 2 — SPACING
    # ─────────────────────────────────────────────────────────────

    def _compute_spacing(self, compression: float) -> float:
        """Base spacing scaled by compression factor."""
        return self.tuners.spacing_m * compression * 0.7

    # ─────────────────────────────────────────────────────────────
    # STEP 3 — WIDTH CURVE PER DEFENDER
    # ─────────────────────────────────────────────────────────────

    def _compute_width_curve(self, defenders: list[Player]) -> list[float]:
        """
        Per-defender width multiplier based on depth_role.

        Inner defenders (A/B) get tighter spacing → factor < 1.
        Outer defenders (C/D/WINGER) get wider spacing → factor > 1.
        """
        curve = []
        for d in defenders:
            role = getattr(d, "depth_role", "C")
            factor = self.tuners.width_curve_factor.get(role, 1.0) * 0.8
            curve.append(factor)
        return curve

    # ─────────────────────────────────────────────────────────────
    # STEP 4 — LANE TARGETS
    # ─────────────────────────────────────────────────────────────

    def _compute_lane_targets(
        self,
        world: WorldState,
        defenders: list[Player],
        spacing: float,
        width_curve: list[float],
    ) -> list[float]:
        """
        Compute Y targets evenly spaced around ball_y anchor.

        Uses defenders list order (sorted by Y) to maintain relative
        positions.  The centre of the line is determined by the middle
        defender(s).
        """
        lane_targets = []
        n = len(defenders)
        if n == 0:
            return lane_targets

        # Anchor lane centre on ball Y instead of fixed field centre
        ball_y = 35.0
        if world.ball_carrier is not None:
            ball_y = world.ball_carrier.y
        elif world.ball is not None:
            ball_obj = world.ball
            if getattr(ball_obj, "possessed_by", None) is not None:
                ball_y = ball_obj.possessed_by.y
            else:
                ball_y = getattr(ball_obj, "true_y_m", getattr(ball_obj, "y", 35.0))
        base_y = ball_y

        mid = n // 2
        for i in range(n):
            offset = (i - mid) * spacing
            lane_y = base_y + offset * width_curve[i]
            lane_targets.append(lane_y)

        return lane_targets

    # ─────────────────────────────────────────────────────────────
    # STEP 5 — SIDELINE MARGIN
    # ─────────────────────────────────────────────────────────────

    def _apply_sideline_margin(self, y: float) -> float:
        """Clamp Y coordinate to stay within sideline margins."""
        margin = self.tuners.sideline_margin_m
        field_min = margin
        field_max = self.tuners.field_width_m - margin
        return max(field_min, min(field_max, y))
