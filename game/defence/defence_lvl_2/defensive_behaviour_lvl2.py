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

import math

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
        """AI_ENGINE_FIX: CONTAINMENT uses get_defence_target() for all positioning.
        
        This method is guarded by RuntimeError during OPEN_PLAY and should only
        be called during PTB phases where geometry is active.
        """
        # STEP‑03A‑09B: early-return when defence is disabled
        if not getattr(world, 'defence_enabled', True):
            return

        # ═══════════════════════════════════════════════════════════════
        # PHASE GUARD — defence only runs during OPEN_PLAY phase.
        # ═══════════════════════════════════════════════════════════════
        from world.world_state import GamePhase
        if world.phase != GamePhase.OPEN_PLAY:
            return

        """
        CONTAINMENT: ball behind defensive line → hold line_x with depth offsets.

        AI_ENGINE_FIX: Uses get_defence_target() with defender indices.
        Markers/DH use ruck_x, line defenders use defensive_line_anchor.
        """
        # ═══════════════════════════════════════════════════════════════
        # STEP‑03A‑06C — TES active guard: no containment during tackle phase
        # ═══════════════════════════════════════════════════════════════
        if is_tackle_phase(world):
            return
        # ── STEP‑03A‑06C: subsystem flag guard ──
        if not world.defensive_line_update_enabled:
            return

        if not defenders:
            return

        # INPUT ORDER (2026-08-09): geometry lane targets are returned in
        # input order — no Y-sort, no lane-index ordering.
        sorted_defs = list(defenders)
        lane_targets = self.def_geometry.compute_line_shape(world, sorted_defs, line_x)

        for i, d in enumerate(sorted_defs):
            if d.defender_role == DefenderRole.KICK_CHASE:
                continue
            if d.frozen:
                continue
            if getattr(d, "freeze_timer", 0.0) > 0:
                if d.defender_role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                    # MARKER AUTHORITY LOCK (2026-09-04): marker freeze is a
                    # VELOCITY-ONLY hold — target stays the ruck-pin geometry
                    # authority, never d.x/d.y.
                    from game.defence.defence_lvl_1.marker_authority import marker_geometry_target as _mt
                    d.target_x, d.target_y = _mt(world, d)
                    d.frozen = True
                else:
                    d.target_x = d.x
                    d.target_y = d.y
                continue
            if d.defender_role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                # MARKER AUTHORITY LOCK (2026-09-04): marker target = the
                # single X-depth ruck pin (ruck_x + depth, ruck_y).
                from game.defence.defence_lvl_1.marker_authority import marker_geometry_target as _mt
                d.target_x, d.target_y = _mt(world, d)
                d.frozen = True
                continue

            # AI_ENGINE_FIX: use anchor-based X via get_defence_target.
            # Task F (2026-08-10): ty is DISCARDED — legacy lane Y never
            # reaches a target.  Y comes exclusively from spacing below.
            from world.world_state import get_defence_target
            tx, _ty_ignored = get_defence_target(world, i + 3)
            d.target_x = tx

            # SPACING Y (Task F, 2026-08-10): containment drives X only;
            # Y is spacing_targets[player].y (no geometry-lane, no lane_y).
            from game.defence.defence_lvl_1.defensive_pipeline import frame_spacing_y as _frame_spacing_y
            d.target_y = _frame_spacing_y(world, d, self.tuners)

    def apply_scramble(self, world, defenders, line_x, lane_y):
        # STEP‑03A‑09B: early-return when defence is disabled
        if not getattr(world, 'defence_enabled', True):
            return

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
        # ── STEP‑03A‑06C: subsystem flag guard ──
        if not world.defensive_chase_enabled:
            return

        ball_x = self._get_ball_x(world)
        ball_y = self._get_ball_y(world)

        for d in defenders:
            if d.defender_role == DefenderRole.KICK_CHASE:
                continue
            if d.frozen:
                continue
            if getattr(d, "freeze_timer", 0.0) > 0:
                if d.defender_role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                    from game.defence.defence_lvl_1.marker_authority import marker_geometry_target as _mt
                    d.target_x, d.target_y = _mt(world, d)
                    d.frozen = True
                else:
                    d.target_x = d.x
                    d.target_y = d.y
                continue
            if d.defender_role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                from game.defence.defence_lvl_1.marker_authority import marker_geometry_target as _mt
                d.target_x, d.target_y = _mt(world, d)
                d.frozen = True
                continue

            d.retreating = False
            d.target_x = ball_x
            # SPACING Y (Task F, 2026-08-10): scramble drives X only; Y is
            # spacing_targets[player].y (never ball_y / chase convergence).
            from game.defence.defence_lvl_1.defensive_pipeline import frame_spacing_y as _frame_spacing_y
            d.target_y = _frame_spacing_y(world, d, self.tuners)

    def apply_retreat(self, world, defenders, line_x, lane_y, use_role_depths=False):
        # STEP‑03A‑09B: early-return when defence is disabled
        if not getattr(world, 'defence_enabled', True):
            return

        """
        RETREAT: PTB / post-ruck → all defenders slide back behind ruck.

        FIXED direction: defence always retreats RIGHT (+X), away from the
        ruck (toward the attacking team's tryline at x=110).

        When use_role_depths is False (default):
            X = world.ruck_x_m + RETREAT_DISTANCE_M (flat blanket retreat)
        When use_role_depths is True:
            X = world.ruck_x_m + ROLE_DEPTH_M[role] + post_ptb_depth_buffer_m
            (each defender settles into their role-depth position)

        Y = lane_y[i] in both modes (preserve lateral spacing)

        Skips frozen, freeze_timer, markers, and KICK_CHASE.

        FACING OVERRIDE (MANDATORY): defender faces BC while retreating.
        """
        # ═══════════════════════════════════════════════════════════════
        # STEP‑03A‑06C — TES active guard: no retreat during tackle phase
        # ═══════════════════════════════════════════════════════════════
        if is_tackle_phase(world):
            return
        # ── STEP‑03A‑06C: subsystem flag guard ──
        if not world.defensive_retreat_enabled:
            return

        depth_buffer = self.tuners.post_ptb_depth_buffer_m if use_role_depths else 0.0

        for i, d in enumerate(defenders):
            if d.defender_role == DefenderRole.KICK_CHASE:
                continue
            if d.frozen:
                continue
            if getattr(d, "freeze_timer", 0.0) > 0:
                if d.defender_role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                    from game.defence.defence_lvl_1.marker_authority import marker_geometry_target as _mt
                    d.target_x, d.target_y = _mt(world, d)
                    d.frozen = True
                else:
                    d.target_x = d.x
                    d.target_y = d.y
                continue
            if d.defender_role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                from game.defence.defence_lvl_1.marker_authority import marker_geometry_target as _mt
                d.target_x, d.target_y = _mt(world, d)
                d.frozen = True
                continue

            d.retreating = True

            if use_role_depths:
                # "D" is the universal bulk/fallback depth role (ruck-driven
                # role system, 2026-08-08).
                role = getattr(d, "depth_role", "D")
                role_offset = self.tuners.ROLE_DEPTH_M.get(role, 5.0)
                raw_target_x = world.ruck_x_m + role_offset + depth_buffer
            else:
                # STEP‑03A‑08: Use world.defensive_line_anchor instead of ruck_x + 10.
                # TASK VII (2026-08-20) — AUTHORITY-ROUTED: the anchor X must be
                # read through anchor_authority.defensive_line_anchor_m (ruck_x +
                # 10, locked-published fallback), never the bare world field.  The
                # bare read bypassed the authority fallback stamping and was one
                # edit away from drifting from the frame lock.
                from game.defence.defence_lvl_1.anchor_authority import (
                    defensive_line_anchor_m as _anchor_m,
                )
                raw_target_x = _anchor_m(world)

            # ── AUDIT FIX: Smooth lerp to retreat target ──
            lerp_alpha = 0.3
            d.target_x = d.x + (raw_target_x - d.x) * lerp_alpha

            # STEP-03A-09: distance-based speed scaling for retreat
            retreat_x = d.target_x
            dist = abs(d.x - retreat_x)
            if dist > 15.0:
                speed = d.max_speed_mps * 1.0
            elif dist > 8.0:
                speed = d.max_speed_mps * 0.75
            else:
                speed = d.max_speed_mps * 0.5
            d.move_speed = speed

            # SPACING Y (Task F, 2026-08-10): retreat keeps spacing Y, never
            # lane_y / current Y (uniform retreat, no lane-shape fallback).
            from game.defence.defence_lvl_1.defensive_pipeline import frame_spacing_y as _frame_spacing_y
            d.target_y = _frame_spacing_y(world, d, self.tuners)

            # Facing override: face BC while retreating
            bc = world.ball_carrier
            if bc is not None:
                angle_to_bc = math.atan2(bc.y - d.y, bc.x - d.x)
                d.set_facing(angle_to_bc)

    # ═══════════════════════════════════════════════════════════════
    # CHASE + COMPRESSION (OPEN_PLAY only)
    # ═══════════════════════════════════════════════════════════════

    def apply_chase_and_compression(self, world, bc, defenders, tuners):
        # STEP‑03A‑09B: early-return when defence is disabled
        if not getattr(world, 'defence_enabled', True):
            return

        """Apply Y-only chase/compression in OPEN_PLAY.

        GLOBAL FIELD DIRECTION: Defence always in front of BC.
        PRIMARY at BC.x + 1.0, all others at BC.x + 10.0 anchor.
        This method ONLY adjusts Y toward FR (or BC if FR absent) and sets speed.
        X target is NEVER modified — it stays at the anchor set by
        DefensiveBehaviourUnified._apply_open_play_unified().

        STEP-03A-09: FR-driven Y convergence.  Chase targets the First
        Receiver lane instead of BC lane, so defenders align to the most
        dangerous attacking player.

        PRIMARY chasers (up to chase_max_primary, within chase_primary_radius_m):
            target_y = fr_y  (converge Y on FR lane)
            move_speed = max_speed

        SECONDARY chasers (up to chase_max_secondary, within chase_secondary_radius_m):
            target_y = fr_y  (converge Y on FR lane)
            move_speed = max_speed * 0.85

        PASSIVE (all other eligible defenders):
            target_y = target_y + compression_strength * (fr_y - target_y)
            move_speed = max_speed * passive_retreat_speed_scale
        """
        bcx = bc.x
        bcy = bc.y

        # STEP-03A-09: FR-driven Y target
        fr = world.first_receiver
        if fr is not None:
            chase_y = fr.y
        else:
            chase_y = bc.y
        n_def = len(defenders)
        if n_def == 0:
            return

        # Sort eligible by distance to BC (nearest first).
        # NOTE (2026-08-08): this is EUCLIDEAN chase-engagement ranking
        # (PRIMARY/SECONDARY/PASSIVE chaser assignment) — NOT a raw-Y sort
        # and NOT depth-role assignment.  Depth roles (A/B/C/D/CENTRE/WINGER)
        # are the ruck-driven geometry system in defensive_depth_roles.py;
        # this sort stays untouched (spec decision).
        sorted_defs = sorted(defenders, key=lambda d: (d.x - bcx) ** 2 + (d.y - bcy) ** 2)

        def_to_idx = {id(d): i for i, d in enumerate(defenders)}
        assigned = [False] * n_def

        # --- PRIMARY CHASERS (Y-only converge) ---
        primary_count = 0
        primary_max = tuners.chase_max_primary
        primary_radius_sq = tuners.chase_primary_radius_m ** 2

        for d in sorted_defs:
            if primary_count >= primary_max:
                break
            dx = d.x - bcx
            dy = d.y - bcy
            dist_sq = dx * dx + dy * dy
            if dist_sq > primary_radius_sq:
                continue
            idx = def_to_idx.get(id(d))
            if idx is None:
                continue
            # SPACING Y (Task F, 2026-08-10): chase never converges Y on the
            # FR lane — Y stays spacing_targets[player].y. X remains at the
            # anchor set by the caller.
            from game.defence.defence_lvl_1.defensive_pipeline import frame_spacing_y as _frame_spacing_y
            d.target_y = _frame_spacing_y(world, d, self.tuners)
            d.move_speed = d.max_speed_mps
            assigned[idx] = True
            primary_count += 1

        # --- SECONDARY CHASERS (Y-only converge) ---
        secondary_count = 0
        secondary_max = tuners.chase_max_secondary
        secondary_radius_sq = tuners.chase_secondary_radius_m ** 2

        for d in sorted_defs:
            if secondary_count >= secondary_max:
                break
            idx = def_to_idx.get(id(d))
            if idx is None:
                continue
            if assigned[idx]:
                continue
            dx = d.x - bcx
            dy = d.y - bcy
            dist_sq = dx * dx + dy * dy
            if dist_sq > secondary_radius_sq:
                continue
            # SPACING Y (Task F, 2026-08-10): chase never converges Y on the
            # FR lane — Y stays spacing_targets[player].y.
            from game.defence.defence_lvl_1.defensive_pipeline import frame_spacing_y as _frame_spacing_y
            d.target_y = _frame_spacing_y(world, d, self.tuners)
            d.move_speed = d.max_speed_mps * 0.85
            assigned[idx] = True
            secondary_count += 1

        # --- PASSIVE COMPRESSION (Y-only) ---
        for i, d in enumerate(defenders):
            if assigned[i]:
                continue
            # SPACING Y (Task F, 2026-08-10): no FR convergence / no inline
            # compression — Y stays spacing_targets[player].y (spacing owns Y
            # in every writer; compression may affect speed only).
            from game.defence.defence_lvl_1.defensive_pipeline import frame_spacing_y as _frame_spacing_y
            d.target_y = _frame_spacing_y(world, d, self.tuners)
            d.move_speed = d.max_speed_mps * tuners.passive_retreat_speed_scale

            # FACING OVERRIDE (MANDATORY): face BC while compressing
            if bc is not None:
                angle_to_bc = math.atan2(bc.y - d.y, bc.x - d.x)
                d.set_facing(angle_to_bc)

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
        # ── OPEN_PLAY GUARD — this method is NEVER called during OPEN_PLAY ──
        # If somehow reached, it would overwrite BC-anchored target_x with
        # ball-relative depth targets, breaking defender convergence.
        raise RuntimeError(
            "STEP-2 compute() must NOT run during OPEN_PLAY. "
            "Use DefensiveBehaviourUnified._apply_open_play_unified() instead."
        )

    # ------------------------------------------------------------
    # SCRAMBLE — all defenders target the BALL
    # ------------------------------------------------------------
    def scramble(self, defenders, ball):
        """
        Override everything: every defender targets the BALL directly.

        BALL-relative, NOT BC-relative.  This ensures correct behaviour
        during kicks, passes, offloads, and broken play.

        Geometry OFF, Roles OFF, Lane_y OFF, Depth OFF.

        GUARD: This legacy method must NOT be called during OPEN_PLAY.
        """
        raise RuntimeError(
            "STEP-2 scramble() must NOT run during OPEN_PLAY. "
            "Use DefensiveBehaviourUnified._apply_open_play_unified() instead."
        )

    # ------------------------------------------------------------
    # RETREAT — all defenders slide back from ball
    # ------------------------------------------------------------
    def retreat(self, defenders, ball, retreat_distance):
        """
        Override everything: every defender retreats behind the ball.

        X = ball.x - retreat_distance   (behind the ball)
        Y = lane_y (preserve lateral spacing for shape)

        Markers remain ruck-relative — handled by assign_markers_and_retreat.

        GUARD: This legacy method must NOT be called during OPEN_PLAY.
        """
        raise RuntimeError(
            "STEP-2 retreat() must NOT run during OPEN_PLAY. "
            "Use DefensiveBehaviourUnified._apply_open_play_unified() instead."
        )
