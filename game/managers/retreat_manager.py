"""retreat_manager.py -- RetreatManager: sole authority for forced retreat.

STAGE 3B -- RETREAT + SPACING AUTHORITY MIGRATION
=================================================

Owns EVERY forced-retreat write in the engine:

  1. apply_forced_retreat()      -- THE unified forced-retreat writer.
                                    Builds ``retreat_spacing_targets``
                                    from the SAME split-spacing chain
                                    every other writer consumes (single
                                    Y source), splits compliant /
                                    non-compliant against the anchor
                                    compliance line, and writes:
                                      X  <- ANCHOR AUTHORITY (ruck_x + 10)
                                      Y  <- retreat_spacing_targets with
                                            the frame-authoritative
                                            ``_frame_spacing_y`` fallback
                                    NO current-Y fallback exists anywhere
                                    on the retreat path.

  2. apply_tes_support_retreat() -- the TES-support fold-back writer
                                    (structured-close TES branch).

TES FREEZE COMPLIANCE (contract 3)
----------------------------------
A defender carrying ``frozen`` or ``freeze_timer > 0`` NEVER receives a
movement order from this module.  Its position is owned by the freeze
authority: the writer applies the freeze-pin only (target = current
position) and leaves velocity/state untouched otherwise.  No retreat
write can violate a TES freeze mask.

RUCK-PIVOT TIMING (contract 4)
------------------------------
This module never forms bands and never pivots the ruck.  Band formation
is owned by ``DefenceManager.assign_bands`` and fires only once TES has
entered TACKLE_INIT (``world.ruck_established``).  On CATCH / OPEN_PLAY
the retreat writer positions defenders purely from spacing + anchor.

CLEAN EXECUTION LAYER (contract 5)
----------------------------------
Players execute movement only.  Spacing, retreat, width, and band logic
live under the manager layer (this module + DefenceManager).
"""

import math

from player.player import DefenderRole, Player
from player.movement_states import MovementState
from game.defence.defence_lvl_1.defensive_pipeline import (
    frame_spacing_y as _frame_spacing_y,
)
from game.defence.defence_lvl_1.defensive_split_spacing import (
    compute_targets as _split_compute_targets,
)
from game.defence.defence_lvl_1.anchor_authority import (
    defensive_line_anchor_m as _anchor_m,
)
from game.debug.target_write_log import stamp_target_write

from game.defence.defence_lvl_1.tackle_state_gate import (
    is_tes_drive_hold as _is_tes_drive_hold,
)
# ── DEBT-AWARE RETREAT LOCOMOTION (Retreat Geometry Phase 1) ──
# This writer is the SOLE stamping authority for the retreat gait: it issues
# a profile on every body it retreats and CLEARS it on every body it does
# not, so a profile's lifetime is exactly the retreat write.  The locomotion
# module carries no project-scope imports, so importing it here is cycle
# free in any load order.
from game.defence.defence_lvl_1.retreat_locomotion import (
    apply_retreat_locomotion,
    clear_retreat_locomotion,
)

__all__ = ["RetreatManager", "apply_forced_retreat", "apply_tes_support_retreat"]

_MARKER_ROLES = (DefenderRole.MARKER_A, DefenderRole.MARKER_B)


def _is_retreat_eligible(defender, ruck_x: float, compliance_dist: float) -> bool:
    """True when *defender* has retreated behind the compliance line.

    Markers are always exempt (they play tight to the ruck).  Mirrors the
    long-standing compliance predicate formerly inlined in the unified
    behaviour dispatcher.
    """
    if getattr(defender, "defender_role", None) in _MARKER_ROLES:
        return True
    return defender.x >= ruck_x + compliance_dist


def _is_frozen(defender) -> bool:
    """TES freeze-contract read: frozen OR holding a freeze timer."""
    if bool(getattr(defender, "frozen", False)):
        return True
    return float(getattr(defender, "freeze_timer", 0.0) or 0.0) > 0.0


def _split_retreat_targets(world, defenders, tuners, governor=None) -> dict:
    """Build the retreat Y dict from the SAME split-spacing chain.

    Single Y source: identical inputs produce identical Y for the
    retreat writer, the structured pipeline, and the frame-authoritative
    fallback reader.  No retreat-specific width curve exists.
    """
    return _split_compute_targets(world, defenders, tuners, governor=governor)


def apply_forced_retreat(world, defenders, tuners, governor=None,
                         spacing_targets=None, tes_retreat_priority=False,
                         force_anchor_x=False):
    """THE unified forced-retreat writer (sole retreat authority).

    Splits *defenders* into compliant / non-compliant against the anchor
    compliance line, then drives every non-compliant defender back with:

        target_x <- ANCHOR AUTHORITY (ruck_x + 10)
        target_y <- retreat_spacing_targets (split-spacing chain),
                    falling back to the frame-authoritative spacing read

    TES FREEZE CONTRACT: a frozen defender (or freeze_timer > 0) is
    NEVER moved -- the freeze-pin (target = current position) is applied
    and the defender is excluded from both lists.  The dynamic fullback
    actor never retreats (geometry-driven only) and is passed through as
    compliant.

    TES-RETREAT PRIORITY (2026-08-29): when *tes_retreat_priority* is
    True (RELEASE / PTB with a live tackle-escape), every non-compliant
    eligible defender is routed through ``apply_tes_support_retreat`` --
    the TES-support fold-back writer -- so the TES retreat target (anchor X
    + spacing Y, ``target_source="tes_support_retreat"``, 0.5x ceiling
    speed) wins over the generic spacing/lane write.  This rebalances the
    authority arbitration so a tackle-escape retreat is never overridden
    by a spacing lane during RELEASE/PTB.

    FORCE-ANCHOR X (DRIVE-RELEASE RETREAT, 2026-08-30): when
    *force_anchor_x* is True, EVERY non-compliant eligible defender is
    pinned to the anchor compliance X (``target_x = ruck_x + 10``)
    regardless of any width-lane entry.  This is the post-tackle retreat
    mode used at the DRIVE->RELEASE boundary: the whole line drops TES
    isolation and retreats to the 10 m line immediately, exactly like the
    POST_RUCK_OPEN_PLAY completion loop.  The width-lane override
    (``retreat_to_lane``) is skipped so interior A/B/C/D/CENTRE/WINGER
    holders all retreat to the full 10 m rather than their shallower lane
    X.

    Returns ``(compliant, non_compliant)`` for the caller pipeline.
    """
    # ── RUCK-ORIGIN SYNC READ (2026-08-29) ──
    # The ruck origin (world.ruck_x_m) is published on catch/DH-pickup by
    # set_ball_carrier() and at each tackle/release transition.  If it is
    # still None (post-kickoff reset, pre-first-tackle), fall back to the
    # BC position so retreat geometry never targets a stale/default ruck —
    # otherwise mid-line actors converge to the previous ruck before
    # correcting.  This is the frame-consistent ruck read the retreat
    # authority requires.
    ruck_x = getattr(world, "ruck_x_m", None)
    if ruck_x is None:
        bc = getattr(world, "ball_carrier", None)
        ruck_x = float(bc.x) if bc is not None else 60.0
    compliance_dist = float(
        getattr(tuners, "retreat_compliance_distance_m", 10.0)
    )
    compliance_x = _anchor_m(world)
    bc = getattr(world, "ball_carrier", None)

    # STAGE 3C — PURE CONSUMER: NO SECOND COMPUTE.  The retreat writer
    # reads the frame-authoritative dicts published by the SpacingAuthority
    # (run_defensive_pipeline) earlier in this frame:
    #   Y  <- world.frame_spacing_targets  (spacing authority, sole source)
    #   X  <- world.frame_width_targets    (width authority, lane holders)
    # A caller may pass ``spacing_targets`` explicitly (the structured
    # pipeline's in-frame dict); otherwise the fresh frame dict is used.
    # There is no structural recompute and no per-writer Y invention.
    retreat_spacing_targets = {}
    if spacing_targets:
        retreat_spacing_targets = dict(spacing_targets)
    elif getattr(world, "frame_geometry_ready", False):
        published = getattr(world, "frame_spacing_targets", None)
        if published:
            retreat_spacing_targets = dict(published)

    # Width dict: consumed for width-eligible lane holders only.
    width_targets = getattr(world, "frame_width_targets", None) or {}
    line_defs = [
        d for d in defenders
        if isinstance(d, Player)
        and not getattr(d, "fullback_role_active", False)
    ]

    # ── POST-MISS PURSUIT EXEMPTION (2026-09-26) ──
    # A defender the carrier beat OUTRIGHT is entitled to pursue for a
    # bounded window after the miss (``post_miss_pursuit``).  Driving it back
    # to the anchor here is exactly the measured post-miss defect: on the
    # freeze-expiry frame the hand-back authored a live pursuit point, and the
    # very NEXT frame this writer re-seized the body and drove it AWAY from
    # the carrier -- measured ``dist_to_bc`` 1.51 -> 3.13 m and climbing,
    # ``target_source = "retreat_non_compliant"``, ``chase=-`` throughout.
    #
    # The entitlement is handed to the pursuit authority (the dispatch's FINAL
    # target write) and the body is reported as COMPLIANT, exactly like the
    # fullback exemption below: it is not retreated, and it is not driven.
    #
    # REACHABILITY: ``owns_post_miss_pursuit`` requires the canonical genuine
    # line break (``bc.x > anchor_x + 1.0``).  At RELEASE / PTB the anchor is
    # ``ruck_x + 10`` and the carrier sits on the ruck, so the predicate is
    # unsatisfiable there by construction and no entitlement can fire outside
    # OPEN_PLAY -- the TES-era retreat paths are untouched.
    #
    # Import is FUNCTION-LOCAL: ``post_miss_pursuit`` carries no project-scope
    # imports, so no load order can produce a cycle.
    from game.defence.defence_lvl_1.post_miss_pursuit import (
        owns_post_miss_pursuit as _owns_post_miss_pursuit,
    )

    compliant = []
    non_compliant = []
    for d in defenders:
        if not isinstance(d, Player):
            continue
        # FULLBACK EXEMPTION: the last man never enters RETREAT.
        if getattr(d, "fullback_role_active", False):
            clear_retreat_locomotion(d)
            compliant.append(d)
            continue
        # POST-MISS PURSUIT EXEMPTION (see the note above): the beaten body
        # chases; the retreat authority writes NOTHING for it.
        if _owns_post_miss_pursuit(world, d):
            clear_retreat_locomotion(d)
            compliant.append(d)
            continue
        if _is_retreat_eligible(d, ruck_x, compliance_dist):
            # ── FORCE-ANCHOR COMPLIANT RE-DRIVE (2026-09-01) ──
            # DRIVE-RELEASE whole-line retreat (force_anchor_x=True): a
            # compliant DEF_RETREAT_10 defender that is AHEAD of the anchor
            # (post-drive ruck moved backward, or it sat on a TES-era target
            # with vx=0) must be driven back to the anchor.  Route it through
            # the non-compliant writer so the caller integrates it (otherwise
            # it keeps its TES-era target and vx=0 — STUCK_NOT_FROZEN, unable
            # to join the defensive geometry).  Only for force_anchor_x
            # (whole-line retreat); normal width-lane retreat keeps the
            # compliant list unchanged.
            if force_anchor_x and d.x > compliance_x and not _is_frozen(d):
                non_compliant.append(d)
            else:
                clear_retreat_locomotion(d)
                compliant.append(d)
            continue
        # TES FREEZE COMPLIANCE: a frozen non-compliant defender must not
        # move -- position owned by the freeze authority (freeze-pin only);
        # excluded from retreat processing.
        if _is_frozen(d):
            clear_retreat_locomotion(d)
            d.target_x = d.x
            d.target_y = d.y
            d.frozen = True
            d.target_source = "retreat_freeze_pin"
            # MOVEMENT-STATE CONSISTENCY (2026-09-01): a frozen body must
            # never carry a stale CHASE/SHOOT from the pre-tackle MMDL frame
            # — pin it to HOLD so the freeze-pin is state-consistent with
            # the rest of the TES→RELEASE→PTB transition window.
            d.movement_state = MovementState.HOLD
            stamp_target_write(world, d, "retreat_manager",
                               detail="retreat_freeze_pin")
            continue
        non_compliant.append(d)

    for d in non_compliant:
        # TES-RETREAT PRIORITY (2026-08-29): when a tackle-escape is
        # active (RELEASE / PTB), the TES-support fold-back writer owns
        # the retreat target so the TES retreat geometry (anchor X +
        # spacing Y, target_source="tes_support_retreat", 0.5x ceiling
        # speed) wins over the generic spacing/lane write.  This is the
        # authority-rebalance: TES retreat targets are never overridden
        # by a spacing lane during RELEASE/PTB.
        if tes_retreat_priority:
            clear_retreat_locomotion(d)
            apply_tes_support_retreat(world, d, tuners, governor=governor)
            continue
        ty = retreat_spacing_targets.get(id(d))
        if ty is None:
            # Dict-only read (no structural recompute); non-covered
            # stubs get the documented ruck_y pin.
            ty = _frame_spacing_y(world, d, tuners, governor=governor)
        # WIDTH CONSUMER (Stage 3C): a defender holding an entry in the
        # published width dict keeps its authoritative lane X — retreat
        # NEVER derives or overrides width-X.  Non-width defenders fall
        # back to the anchor-compliance pin.
        #
        # FORCE-ANCHOR X (DRIVE-RELEASE RETREAT, 2026-08-30): when
        # *force_anchor_x* is True the width-lane override is SKIPPED —
        # every non-compliant defender is pinned to the anchor compliance
        # X (ruck_x + 10) so the whole line retreats to the 10 m line at
        # the DRIVE->RELEASE boundary, exactly like the POST_RUCK_OPEN_PLAY
        # completion loop.  Interior A/B/C/D/CENTRE/WINGER holders are not
        # held at their shallower lane X.
        lane = None
        if not force_anchor_x:
            lane = width_targets.get(id(d))
        if lane is not None:
            d.target_x = float(lane)
            d.target_source = "retreat_to_lane"
        else:
            d.target_x = compliance_x
            d.target_source = "retreat_non_compliant"
        d.target_y = ty
        d.movement_state = MovementState.RETREAT
        stamp_target_write(
            world, d, "retreat_manager",
            detail=("retreat_to_lane" if lane is not None
                    else "retreat_non_compliant"),
        )
        ceiling = float(getattr(tuners, "max_defensive_speed_mps", 8.3))
        base = min(float(getattr(d, "max_speed_mps", ceiling)), ceiling)
        # ── DEBT-AWARE LOCOMOTION (Retreat Geometry Phase 1) ──
        # The gait is chosen from the debt this write has just created
        # (target_x - x), and the commanded speed is the gait's speed scale
        # times the arrival taper.  The locomotion layer also owns FACING
        # for the frame: ``set_facing`` snaps the facing to the carrier in
        # ONE frame and pins ``facing_override`` (which makes
        # ``update_facing_unified`` early-return), so that snap is the
        # spin-turn mechanism this phase removes.  The override is
        # therefore cleared and the bounded rotation is left to the
        # integrator.
        #
        # With the locomotion layer disabled (tuner or RLM_RETREAT_LOCOMOTION=0)
        # the legacy flat ``0.85 x ceiling`` command AND the one-frame
        # ``set_facing`` snap are preserved exactly, so the A/B arms differ
        # only by the gait law.
        profile = apply_retreat_locomotion(
            world, d, tuners, base_speed=base, target_x=d.target_x,
        )
        if profile is not None:
            d.clear_facing_override()
        else:
            d.move_speed = base * 0.85
            # freeze authority is apply_phase_invariants() — do NOT write
            # frozen here; a suppressed body must stay frozen.
            if bc is not None:
                angle_to_bc = math.atan2(bc.y - d.y, bc.x - d.x)
                d.set_facing(angle_to_bc)

    # Now process only compliant defenders (caller pipeline continues).
    return compliant, non_compliant


def apply_tes_support_retreat(world, d, tuners, governor=None,
                              track_bc_x=False) -> None:
    """TES-support fold-back writer (structured-close TES branch).

    During an active TES engagement every non-tackler folds back behind
    the ruck line at controlled speed.  Y comes from the frame-authority
    spacing read.  X comes from the LIVE-BC depth source: ``role_depth_target_x``
    is stamped by the depth-role authority (``bc.x + WIDTH_OFFSETS[depth_role]``
    during the drive, ``ruck_x + 10`` after), so the line tracks the driven
    carrier through TES.  The legacy ``track_bc_x`` offset math remains only
    as the fallback for a depth-stamp-less body.  This keeps ONE X writer per
    defender per frame (the conductor model).  Frozen defenders are never
    touched (TES freeze contract).
    """
    # DEBT-AWARE LOCOMOTION: this fold-back writer is NOT the compliance
    # retreat path — its X is a role depth, not the compliance line — so it
    # never issues a gait.  It clears any profile an earlier write stamped
    # so no stale gait can survive onto this body.
    clear_retreat_locomotion(d)
    if _is_frozen(d):
        return
    # ── OUT-OF-RADIUS HOLD GATE (2026-09-07) ──
    # During the TES drive a defender BEYOND R_tackle that is NOT a
    # registered TES tackler must be treated as retreat/hold-only.  The
    # BC-tracked depth X (role_depth_target_x = bc.x + offset) is FORWARD
    # of a body sitting behind the carrier, and the frame-spacing fallback
    # (role_anchor_y_side) can be OUTWARD for an outer role — both are the
    # out-of-band leak.  Hold the body at its current position (monotone-
    # back / stationary) and tag it as a retreat write so the N/A-pivot
    # write-audit attributes it legally.
    if _is_tes_drive_hold(world, d):
        d.target_x = float(d.x)
        d.target_y = float(d.y)
        d.movement_state = MovementState.RETREAT
        d.target_source = "tes_support_retreat"
        stamp_target_write(
            world, d, "tes_support_retreat",
            detail="tes_out_of_radius_hold",
        )
        ceiling = float(getattr(tuners, "max_defensive_speed_mps", 8.3))
        base = min(float(getattr(d, "max_speed_mps", ceiling)), ceiling)
        d.move_speed = base * 0.5
        return
    # DRIVE-AWARE X SOURCE (2026-09-06): the authoritative role-depth X
    # (bc.x + WIDTH_OFFSETS[depth_role] during the drive, ruck_x + 10
    # after) is the single depth base.  Consume it directly so the line
    # tracks the driven carrier through TES.
    _rtx = getattr(d, "role_depth_target_x", None)
    if _rtx is not None:
        try:
            _rtxf = float(_rtx)
            if math.isfinite(_rtxf):
                d.target_x = _rtxf
                d.target_y = _frame_spacing_y(world, d, tuners, governor=governor)
                d.movement_state = MovementState.RETREAT
                d.target_source = "tes_support_retreat"
                stamp_target_write(world, d, "tes_support_retreat",
                                   detail="tes_support_retreat")
                ceiling = float(getattr(tuners, "max_defensive_speed_mps", 8.3))
                base = min(float(getattr(d, "max_speed_mps", ceiling)), ceiling)
                d.move_speed = base * 0.5
                return
        except (TypeError, ValueError):
            pass
    if track_bc_x:
        bc = getattr(world, "ball_carrier", None)
        role = getattr(d, "depth_role", None)
        offsets = getattr(tuners, "WIDTH_OFFSETS", None) or {}
        if bc is not None and role is not None and role in offsets:
            d.target_x = bc.x + offsets[role]
        else:
            d.target_x = _anchor_m(world)
    else:
        d.target_x = _anchor_m(world)
    d.target_y = _frame_spacing_y(world, d, tuners, governor=governor)
    d.movement_state = MovementState.RETREAT
    d.target_source = "tes_support_retreat"
    stamp_target_write(world, d, "tes_support_retreat",
                       detail="tes_support_retreat")
    ceiling = float(getattr(tuners, "max_defensive_speed_mps", 8.3))
    base = min(float(getattr(d, "max_speed_mps", ceiling)), ceiling)
    d.move_speed = base * 0.5
    # freeze authority is apply_phase_invariants() — do NOT write
    # frozen here; a suppressed body must stay frozen.


class RetreatManager:
    """Manager-layer owner of the forced-retreat writers.

    Held by ``DefenceManager`` (the macro brain).  Wraps the pure writer
    functions above so every engine entry point routes through the
    manager layer while the functions themselves stay import-safe for
    the behaviour delegate (no import cycle).
    """

    def __init__(self, tuners):
        self.tuners = tuners

    def apply_forced_retreat(self, world, defenders, governor=None,
                             tes_retreat_priority=False,
                             force_anchor_x=False):
        """Delegate to the unified forced-retreat writer.

        *force_anchor_x* pins every non-compliant defender to the anchor
        compliance X (ruck_x + 10), skipping the width-lane override
        (DRIVE-RELEASE retreat mode).
        """
        return apply_forced_retreat(
            world, defenders, self.tuners, governor=governor,
            tes_retreat_priority=tes_retreat_priority,
            force_anchor_x=force_anchor_x,
        )

    def apply_tes_support_retreat(self, world, d, governor=None,
                                  track_bc_x=False) -> None:
        """Delegate to the TES-support fold-back writer.

        *track_bc_x* routes X through the live-BC width offset when True
        (TES-drive BC-tracking shadow); otherwise the anchor authority.
        """
        apply_tes_support_retreat(
            world, d, self.tuners, governor=governor, track_bc_x=track_bc_x,
        )
