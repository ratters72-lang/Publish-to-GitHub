"""
defensive_geometry.py — STEP‑1 defensive line geometry.

DEFENSIVE GEOMETRY IS DRIVEN BY DEPTH ROLES ONLY (2026-08-09).

Every defender's (target_x, target_y) derives from its depth role
(A/B/C/D/CENTRE/WINGER), which is assigned by the ruck-driven role
system in ``defensive_depth_roles.assign_depth_roles``:

  - X depth:  target_x = base_x + WIDTH_OFFSETS[depth_role],
              where base_x = bc.x during the drive (OPEN_PLAY with an
              active ball carrier) and ruck_x otherwise (post-drive).
  - Y width:  target_y = ruck_y + side_sign * half_width
                                  * role_width_anchor[role]
                                  * width_curve_factor[role]

There is NO Y-ordering, NO lane-index ordering, NO compression, and NO
proportional-gain geometry.  The role-slot width-curve input is exactly:

  LEFT:  [WINGER_L, CENTRE_L, A_L, B_L, C_L] + D_L
  RIGHT: [WINGER_R, CENTRE_R, A_R, B_R, C_R] + D_R

PHASE GATE
----------
Geometry movement is enabled ONLY during OPEN_PLAY.  It is DISABLED in
RELEASE, PTB_PREP, PTB_EXEC, PTB, POST_RUCK_OPEN_PLAY, TACKLE_DRIVE and
TACKLE_DOMINANCE (the TES drive period never projects OPEN_PLAY geometry).

Callers should use ``geometry_active(world)`` to gate movement.  The
``compute_*`` methods themselves remain pure target generators so
verification scripts and the debug/render path can call them directly.
"""

from player.player import Player
from world.world_state import WorldState
from game.debug.target_write_log import stamp_target_write
from game.defence.defence_lvl_1.defensive_depth_roles import assign_depth_roles
from game.defence.defence_lvl_1.defensive_width import (
    depth_side_of,
    role_anchor_y,
    role_anchor_y_for,
    governed_width_m,
)

# Phases in which geometry-driven defensive movement is DISABLED.
# Geometry runs ONLY in OPEN_PLAY.
GEOMETRY_DISABLED_PHASES = frozenset({
    "RELEASE",
    "PTB_PREP",
    "PTB_EXEC",
    "PTB",
    "POST_RUCK",
    "POST_RUCK_OPEN_PLAY",
    "TACKLE_INIT",
    "TACKLE_CONTACT",
    "TACKLE_DRIVE",
    "TACKLE_DOMINANCE",
})


def geometry_active(world) -> bool:
    """Return True when geometry-driven defensive movement is enabled.

    Geometry is enabled ONLY during OPEN_PLAY.  The world's OPE phase is
    read from ``world.phase`` / ``world.sub_state`` (the engine drives
    ``sub_state.value`` separately); verification stubs may omit both,
    which defaults to enabled so unit tests call the role target
    generator directly.

    Sub-state gate (2026-08-09): the sub-state branch is EXHAUSTIVE — any
    sub-state other than OPEN_PLAY is geometry-DISABLED.  This includes
    every TES phase (TACKLE_INIT/CONTACT/DRIVE/DOMINANCE/RELEASE) and
    every PTB phase (PTB_READY/PTB/POST_PTB_RESET/RESET_LINE), whose
    lowercase values (`tackle_*`, `ptb*`, `post_ptb_reset`, `reset_line`)
    are NOT in the legacy GEOMETRY_DISABLED_PHASES set.  Falling through
    to the default-True would silently re-arm geometry movement during
    RELEASE/PTB/POST_RUCK — exactly the phases the task contract says
    geometry movement must be DISABLED.
    """
    phase = getattr(world, "phase", None)
    sub = getattr(world, "sub_state", None)
    if phase is not None:
        name = getattr(phase, "value", phase)
        if name in GEOMETRY_DISABLED_PHASES:
            return False
        if name == "OPEN_PLAY":
            return True
    if sub is not None:
        name = getattr(sub, "value", sub)
        if name in GEOMETRY_DISABLED_PHASES:
            return False
        # Exhaustive sub-state gate: only OPEN_PLAY enables geometry.
        return name == "open_play"
    # Verification stubs / no-phase worlds: allow direct computation.
    return True


class DefensiveGeometry:
    """Computes role-driven (target_x, target_y) for a defensive line."""

    def __init__(self, tuners):
        """Store a reference to the DefenceTuners singleton."""
        self.tuners = tuners

    # ─────────────────────────────────────────────────────────────
    # PUBLIC ENTRY POINTS
    # ─────────────────────────────────────────────────────────────

    def compute_role_targets(self, world, defenders) -> dict:
        """Assign depth roles and compute per-defender role targets.

        Returns ``{id(d): (target_x, target_y)}`` for every eligible
        defender.  Depth roles are assigned in-place on each defender
        (idempotent).  Targets flow ONLY from the depth role tables:

            target_x = base_x + WIDTH_OFFSETS[depth_role],
                       where base_x = bc.x during the drive (OPEN_PLAY
                       with an active ball carrier) and ruck_x otherwise.
            target_y = role_anchor_y(d)   (spec Section 3)

        where ``role_anchor_y`` is the canonical width formula from
        defensive_width.py:

            role_anchor_y = ruck_y + depth_side * half_width
                            * ROLE_WIDTH_OFFSETS[depth_role]

        ``depth_side`` is the PERSISTENT role-locked side (Task A) — the
        live ``d.y < ruck_y`` sign is NEVER consulted for seeded
        defenders.  Width is the governed width (Task D) when the line
        governor is active; otherwise the raw ``world.def_line_width_m``.

        No Y-sorting, no lane-index ordering, no compression scaling.

        TASK 2 (2026-08-14) — SINGLE-ASSIGN-PER-FRAME: the un-gated
        preamble (OpenPlayEngine.update() top) already assigned depth
        roles and published ``frame_spacing_targets`` from those SAME
        roles this frame.  Re-assigning here would mutate the bands
        mid-frame and leave the published spacing dict stale relative to
        the post-mutation roles (the stale_depth_role / stale_target_y
        drift).  Guarded: role assignment is owned by the preamble; this
        generator only consumes the assigned roles.
        """
        from game.defence.defence_lvl_1.defensive_pipeline import (
            depth_roles_assigned_this_frame as _assigned_this_frame,
        )
        if not _assigned_this_frame(world):
            assign_depth_roles(defenders, world)

        ruck_x = getattr(world, "ruck_x_m", 60.0)
        ruck_y = getattr(world, "ruck_y_m", 35.0)
        width = governed_width_m(world, self.tuners)

        offsets = getattr(self.tuners, "WIDTH_OFFSETS", None) or {}

        # RUCK-ADJACENT EVEN SPACING (2026-08-18, user verdict): the
        # role-target Y consumes the SAME even side-lane spacing the
        # split-spacing chain produces (A anchors ruck_y ± 2 m via
        # tuners.RUCK_ADJACENT_A_Y_OFFSET_M, WINGER anchors ± half-width,
        # interior evenly between).  The split-spacing module remains the
        # SINGLE Y authority; this generator only produces X (role depth)
        # and the spacing-owned Y so step-7 geometry and the chain can
        # never drift.
        #
        # TASK 8 (2026-08-21) — RECOMPUTE SKIP: when the un-gated frame
        # preamble's dict already covers this defender set (the normal
        # engine case — the preamble runs before every consumer), the Y
        # is read via ``frame_spacing_y`` from the published dict and the
        # even side-lane chain is NOT recomputed here.  The structural
        # recompute below is the fallback for verifier stubs / direct
        # calls with no fresh frame dict.
        from game.defence.defence_lvl_1.defensive_pipeline import (
            frame_spacing_covers as _covers,
            frame_spacing_y as _frame_ty,
        )
        covered = _covers(world, defenders)
        if not covered:
            from game.defence.defence_lvl_1.defensive_split_spacing import (
                spacing_y as _count_lane_spacing_y,
            )

        # ── DRIVE DEPTH REFERENCE (2026-09-05, BC-anchored drive) ──
        # During OPEN_PLAY with an active ball carrier (the drive) the
        # defensive-line depth base is the BALL CARRIER, NOT the ruck:
        #
        #     target_x = bc.x + WIDTH_OFFSETS[role]
        #
        # This makes every line defender's depth track the ball carrier
        # while the drive is alive.  When the drive ends (no active BC,
        # or geometry is disabled for a non-OPEN_PLAY phase) the base
        # reverts to the ruck so the line retreats to the 10 m anchor
        # (ruck_x + 10) via the retreat/phase machinery.
        base_x = ruck_x
        bc_drive = getattr(world, "ball_carrier", None)
        if bc_drive is not None and geometry_active(world):
            base_x = float(bc_drive.x)

        targets = {}
        for d in defenders:
            role = getattr(d, "depth_role", "D") or "D"
            # ── X depth — role-driven, BC-anchored during the drive ──
            tx = base_x + offsets.get(role, offsets.get("D", 5.0))

            # ── Y width — ruck-adjacent even side spacing (single Y
            #    authority), locked side ──
            if covered:
                ty = _frame_ty(world, d, self.tuners)
            else:
                # STAGE 3 COUNT-LANE FALLBACK (2026-09-03): spacing_y is the
                # single Y authority (count → lanes → spacing), replacing the
                # legacy width-driven _even_spacing_target_y fallback.
                ty = _count_lane_spacing_y(world, d, self.tuners)

            targets[id(d)] = (tx, ty)

        return targets

    def compute_line_shape(self, world, defenders, line_x: float) -> list:
        """Compatibility entry: return target_y per defender in INPUT order.

        Assigns depth roles (side-effect) and returns the role-slot Y
        target for each defender in the same order as ``defenders``.
        ``line_x`` is accepted for backward compatibility and ignored —
        X targets are role-driven (WIDTH_OFFSETS), never line_x.

        NOTE (2026-08-09): results are in INPUT order, NOT Y-sorted, and
        NOT lane-index ordered.  Callers that need X should use
        ``compute_role_targets``.
        """
        assign_depth_roles(defenders, world)
        targets = self.compute_role_targets(world, defenders)
        return [targets[id(d)][1] for d in defenders]

    def apply_role_targets(self, world, defenders) -> None:
        """Write target_x/target_y onto each defender from role geometry.

        Used by the structured OPEN_PLAY pipeline after all per-defender
        gates (frozen / marker / fullback) have been applied.
        """
        targets = self.compute_role_targets(world, defenders)
        for d in defenders:
            if id(d) in targets:
                d.target_x, d.target_y = targets[id(d)]
                # INSTRUMENT (2026-09-02): this geometry/spacing application
                # wrote target_x/target_y without a writer tag, so the jitter
                # audit attributed every jump here to "unknown".  Stamp it as
                # "spacing" so the audit can attribute precisely.
                stamp_target_write(world, d, "spacing")

    # ─────────────────────────────────────────────────────────────
    # LEGACY HELPERS (kept for external callers / verification)
    # ─────────────────────────────────────────────────────────────

    def _compute_compression(self, world: WorldState) -> float:
        """Legacy compression factor — REMOVED (2026-08-09).

        Width is pure role-slot width with NO compression scaling.
        Returns 1.0 (neutral) so legacy callers remain valid.
        """
        return 1.0

    def _compute_spacing(self, compression: float) -> float:
        """Legacy spacing — REMOVED (2026-08-09).

        Spacing no longer drives geometry; role width anchors do.
        Returns the neutral base spacing for legacy callers.
        """
        return getattr(self.tuners, "spacing_m", 5.0)

    def _compute_width_curve(self, defenders: list[Player]) -> list:
        """Per-defender width multiplier based on depth_role.

        Kept for verification/diagnostics.  Mirrors the canonical
        ``role_width_offset`` multiplier — ``width_curve_factor[role]``
        with NO hidden 0.8 (2026-08-17; the global width compression is
        gone).  This helper returns the curve factor per defender (role
        fallback "D" only).
        """
        curve = []
        for d in defenders:
            role = getattr(d, "depth_role", "D") or "D"
            factor = self.tuners.width_curve_factor.get(role, 1.0)
            curve.append(factor)
        return curve

    def _apply_sideline_margin(self, y: float) -> float:
        """Clamp Y coordinate to stay within sideline margins."""
        margin = getattr(self.tuners, "sideline_margin_m", 3.0)
        field_max = getattr(self.tuners, "field_width_m", 70.0) - margin
        return max(margin, min(field_max, y))
