"""
game/debug/defender_state_audit.py — Defensive depth / target_y / kickoff-lane
investigation (2026-08-12).

Frame-accurate diagnostic that reports, against the REAL engine fields:

  - DEPTH-ROLE CORRECTNESS — depth_role / depth_side vs the structural
    role anchors (role_depth_target_x == anchor, role_depth_target_y ==
    the role's ruck-relative width anchor).
  - TARGET_Y DRIFT — the written target_y vs the split-spacing role
    anchor (the single geometry Y source, defensive_split_spacing.
    spacing_y).  The offending target_source is printed so the writer
    that went off the spacing chain is identifiable.
  - RUCK-SIDE CHECK — the PERSISTENT locked depth_side (-1/+1) must
    match the side of target_y relative to ruck_y_m.  A side-locked
    defender with a cross-side target is the real wrong-side-of-ruck
    invariant violation.
  - KICKOFF LANE CHECK — during GamePhase.KICKOFF (defence AI does NOT
    run; KMC owns movement) the fullback actor must hold the flat-line
    anchor (target_source == "fullback", pinning world.fullback_anchor_*)
    and every chaser must keep its inertial lane (target_y ≈ own y).
    There is NO BC-lane chase during kickoff (bc is None in flight).
  - INVARIANT SHEET 8.3 FLAGS — explicit per-defender violation booleans:
    stale_depth_role / stale_target_y / cross_side_target_y /
    missing_target_source / kickoff_lane_violation /
    wrong_side_of_ruck / invalid_target_y_movement.

ENGINE-CORRECTED vs ORIGINAL SPEC
---------------------------------
The original investigate_defender_state() spec assumed:

    - d.role holds the depth role         -> real: d.depth_role
    - depth A/B/C are bc_y-distance bands -> real: structural,
      ruck-relative, hysteresis-protected anchors (defensive_depth_roles)
    - target_y should equal bc_y          -> real: role-anchor spacing Y
    - ruck side inferred from d.y/target  -> real: persistent d.depth_side
    - KICKOFF runs tackler lanes          -> real: KMC inertial lanes +
      a pinned fullback anchor

This module checks the REAL invariants so every printed line is
meaningful — no false positives from the old fictional fields.

WIRING (both call sites required for a complete picture):
  1. KickoffEngine.update()  — every KICKOFF frame (ball in flight).
  2. OpenPlayEngine.update() — every frame; gates internally to OPEN_PLAY.

SELF-LIMITING: prints at most AUDIT_PER_PHASE_FRAMES frames per game
phase (KICKOFF, OPEN_PLAY) across AUDIT_MAX_PHASES distinct phases, then
goes silent.  Call rearm_defender_state_audit() to re-arm for another
capture run.
"""

from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_split_spacing import spacing_y
from game.defence.defence_lvl_1.defensive_pipeline import (
    frame_spacing_y as _frame_spacing_y,
    frame_geometry_is_fresh,
)

# ── Audit gates ──────────────────────────────────────────────────────
DEBUG_DEFENDER_STATE_AUDIT: bool = True
AUDIT_PER_PHASE_FRAMES: int = 5
AUDIT_MAX_PHASES: int = 4

# Tolerances (metres).  The target-y drift check compares the written
# target against the spacing chain; spacing clamps (governor / sideline)
# may move the chain by up to ~2-3 m, so the tolerance is deliberately
# generous — this audit flags WRITER BUGS, not clamp arithmetic.
TARGET_Y_DRIFT_TOLERANCE_M: float = 3.0
RUCK_SIDE_TOLERANCE_M: float = 0.5
KICKOFF_LANE_TOLERANCE_M: float = 0.5
ANCHOR_TOLERANCE_M: float = 1.0

_printed_per_phase: dict = {}
_phases_printed: int = 0


def rearm_defender_state_audit() -> None:
    """Reset the per-phase print budget for another capture run."""
    global _printed_per_phase, _phases_printed
    _printed_per_phase = {}
    _phases_printed = 0


def _phase_key(value) -> str:
    """Normalise an Enum phase/sub-state to its short string name."""
    return getattr(value, "name", None) or getattr(value, "value", None) or str(value)


def _resolve_tuners(world) -> DefenceTuners:
    """Return the live DefenceTuners (engine singleton when available)."""
    ope = getattr(world, "open_play_engine", None)
    tuners = getattr(ope, "def_tuners", None) if ope is not None else None
    if tuners is None:
        tuners = DefenceTuners()
    return tuners


def _format_player_id(player) -> str:
    pid = getattr(player, "player_id", None)
    return f"#{pid}" if pid is not None else "#?"


# ─────────────────────────────────────────────────────────────────────
# MAIN ENTRY
# ─────────────────────────────────────────────────────────────────────

def investigate_defender_state(world, defenders, bc, phase, subphase):
    """Inspect defenders and print depth-role correctness, ruck-side
    correctness, kickoff-lane correctness, and target_y drift.

    Args:
        world: engine world object (WorldState or stub).
        defenders: list of defender actors.
        bc: ball-carrier actor (may be None — always None during KICKOFF
            flight).
        phase/subphase: current phase / sub-state (Enums from
            world.phase / world.sub_state).
    """
    global _phases_printed

    if not DEBUG_DEFENDER_STATE_AUDIT:
        return
    phase_key = _phase_key(phase)
    if _phases_printed >= AUDIT_MAX_PHASES:
        return
    if _printed_per_phase.get(phase_key, 0) >= AUDIT_PER_PHASE_FRAMES:
        return
    _printed_per_phase[phase_key] = _printed_per_phase.get(phase_key, 0) + 1
    if _printed_per_phase[phase_key] == 1:
        _phases_printed += 1

    bc_y = getattr(bc, "y", None)
    ruck_y = getattr(world, "ruck_y_m", None)
    anchor_x = getattr(world, "defensive_line_anchor", None)
    tuners = _resolve_tuners(world)
    subphase_key = _phase_key(subphase)
    structured = bool(getattr(world, "use_structured_open_play", True))
    kickoff = phase_key == "KICKOFF"

    print("\n=== DEFENSIVE INVESTIGATION REPORT ===")
    print(f"phase={phase_key} subphase={subphase_key} "
          f"bc_y={bc_y if bc_y is None else f'{bc_y:.2f}'} "
          f"ruck_y={ruck_y if ruck_y is None else f'{ruck_y:.2f}'} "
          f"anchor_x={anchor_x}")

    for d in defenders:
        rid = getattr(d, "player_id", "?")
        depth_role = getattr(d, "depth_role", None)
        depth_side = getattr(d, "depth_side", None)
        target_source = getattr(d, "target_source", None)
        target_y = getattr(d, "target_y", None)
        target_x = getattr(d, "target_x", None)
        dy = getattr(d, "y", 0.0)
        dx = getattr(d, "x", 0.0)
        fb_active = bool(getattr(d, "fullback_role_active", False))
        frozen = bool(getattr(d, "frozen", False))
        role = getattr(d, "defender_role", None)
        role_name = getattr(role, "name", None) or str(role) if role is not None else None

        # -------------------------------------------
        # 1. DEPTH-ROLE CORRECTNESS vs STRUCTURAL ANCHORS
        # -------------------------------------------
        depth_ok = True
        anchor_drift = None
        if depth_role is not None and not fb_active:
            # role_depth_target_y is the structural ruck-relative anchor
            # stamped by assign_depth_roles (defensive_depth_roles.py).
            structural_y = getattr(d, "role_depth_target_y", None)
            if structural_y is not None:
                anchor_drift = abs(dy - structural_y)
                # A line defender may be mid-glide to its anchor; this is
                # informational drift, NOT a role mismatch (roles are
                # correct by construction).
                depth_ok = anchor_drift <= 6.0
        elif fb_active:
            # Fullback is excluded from the depth-role system on purpose.
            depth_ok = True

        # -------------------------------------------
        # 2. TARGET_Y SHOULD MATCH THE SPACING CHAIN (structured OPEN_PLAY)
        # -------------------------------------------
        target_ok = True
        expected_spacing_y = None
        target_drift = None
        if (structured and not kickoff and depth_role in
                ("A", "B", "C", "D", "CENTRE", "WINGER")
                and not fb_active and not frozen):
            expected_spacing_y = spacing_y(world, d, tuners)
            if target_y is not None:
                target_drift = abs(target_y - expected_spacing_y)
                target_ok = target_drift <= TARGET_Y_DRIFT_TOLERANCE_M

        # -------------------------------------------
        # 3. RUCK-SIDE CHECK — locked depth_side vs target side
        # -------------------------------------------
        ruck_side_ok = True
        if (ruck_y is not None and depth_side in (-1, 1)
                and not fb_active and target_y is not None):
            target_side = 0
            if target_y < ruck_y - RUCK_SIDE_TOLERANCE_M:
                target_side = -1
            elif target_y > ruck_y + RUCK_SIDE_TOLERANCE_M:
                target_side = 1
            if target_side != 0 and target_side != depth_side:
                ruck_side_ok = False

        # -------------------------------------------
        # 4. KICKOFF LANE CHECK (only in KICKOFF)
        # -------------------------------------------
        kickoff_ok = True
        if kickoff:
            if fb_active:
                # Fullback holds the flat-line anchor; the anchor is
                # published to world.fullback_anchor_x/y each kickoff frame.
                fb_tx = getattr(world, "fullback_anchor_x", None)
                fb_ty = getattr(world, "fullback_anchor_y", None)
                if target_source != "fullback":
                    kickoff_ok = False
                if (fb_tx is not None and fb_ty is not None
                        and target_x is not None and target_y is not None):
                    if (abs(target_x - fb_tx) > ANCHOR_TOLERANCE_M
                            or abs(target_y - fb_ty) > ANCHOR_TOLERANCE_M):
                        kickoff_ok = False
            else:
                # KMC chasers keep their inertial lane: target_y ≈ own y.
                # There is NO BC-lane targeting during kickoff (bc is None).
                if target_y is not None:
                    if abs(target_y - dy) > KICKOFF_LANE_TOLERANCE_M:
                        kickoff_ok = False

        # -------------------------------------------
        # 5. INVARIANT SHEET 8.3 FLAGS (2026-08-12) — explicit per-defender
        #    violation booleans, checked against the un-gated frame preamble
        #    (defensive_pipeline.run_defensive_pipeline publishes
        #    world.frame_spacing_targets every frame):
        #
        #   stale_depth_role          — role_depth_target_y (stamped by
        #                               assign_depth_roles) disagrees with the
        #                               LIVE frame spacing Y > 2.0 m while the
        #                               frame dict is fresh → the role seed was
        #                               NOT re-stamped for the current ruck.
        #   stale_target_y            — target_y written by a spacing consumer
        #                               disagrees with the FRESH frame spacing Y
        #                               (> 3.0 m) → writer consumed stale
        #                               (previous-frame / old-ruck) geometry.
        #   cross_side_target_y       — target_y sits on the opposite side of
        #                               ruck_y from the LOCKED depth_side.
        #   missing_target_source     — non-frozen, non-fullback line defender
        #                               with a depth role has no target_source
        #                               stamp (writer-audit contract).
        #   kickoff_lane_violation    — KICKOFF: FB off its flat-line anchor
        #                               or chaser off its inertial lane.
        #   wrong_side_of_ruck        — same signal as cross_side_target_y,
        #                               named per the invariant sheet.
        #   invalid_target_y_movement — non-frozen defender with depth_role
        #                               has target_y None / NaN / outside the
        #                               legal 0..70 band.
        #
        #   stale_geometry            — the PUBLISHED frame dict is NOT fresh
        #                               for the current frame (ready False or
        #                               frame stamp != frame_count).  Because
        #                               run_defensive_pipeline republishes
        #                               every OPE frame BEFORE any writer, a
        #                               stale dict at audit time (end of the
        #                               same frame) proves a writer ran
        #                               WITHOUT fresh geometry — the §4.2
        #                               "stale spacing/width" failure.  A
        #                               legitimately non-OPE phase (KICKOFF,
        #                               CATCH) must therefore ALSO not see a
        #                               fresh dict.
        #   stale_target_x            — target_x written by a width consumer
        #                               disagrees with the FRESH frame width X
        #                               (world.frame_width_targets) > 3.0 m →
        #                               writer consumed stale/other X geometry.
        #   residual_frame_geometry   — KICKOFF: frame_geometry_ready is still
        #                               True.  The preamble never runs during
        #                               KICKOFF flight (run.py dispatches only
        #                               KickoffEngine), so any residual dict
        #                               must have leaked from a previous
        #                               sequence (reset bug).  This is the
        #                               audit that makes §8 'kickoff geometry
        #                               refresh' verifiable.
        # -------------------------------------------
        stale_depth_role = False
        stale_target_y = False
        cross_side_target_y = False
        missing_target_source = False
        kickoff_lane_violation = False
        wrong_side_of_ruck = False
        invalid_target_y_movement = False
        stale_geometry = False
        stale_target_x = False
        residual_frame_geometry = False

        fresh_geometry = frame_geometry_is_fresh(world)

        # -- 5h. STALE GEOMETRY (frame dict not fresh at audit time) --
        # The preamble republishes on every OPE frame before ANY writer, so
        # a dict-not-fresh frame at audit (end of the same frame) means a
        # writer consumed geometry WITHOUT this frame's preamble — the §4.2
        # stale-spacing/width failure.  Non-OPE phases (KICKOFF flight) must
        # also NOT see a fresh dict.
        if (not kickoff and not frozen and not fb_active
                and depth_role in ("A", "B", "C", "D", "CENTRE", "WINGER")
                and getattr(world, "frame_geometry_ready", False)
                and not fresh_geometry):
            stale_geometry = True

        # -- 5i. RESIDUAL FRAME GEOMETRY (KICKOFF-only) --
        # The preamble never runs during KICKOFF flight (run.py dispatches
        # only KickoffEngine), so any leftover ready-stamp is a reset leak
        # from the previous sequence — the kickoff-reset staleness bug.
        if kickoff and getattr(world, "frame_geometry_ready", False):
            residual_frame_geometry = True

        # -- 5j. STALE TARGET_X (width-X drift vs the fresh frame width dict) --
        # Mirror of 5b for the width side: target_x written by the geometry
        # LINE-HOLD writer must agree with the FRESH frame width X.  The
        # check fires ONLY when target_source == "structured_open_play" —
        # the one writer that consumes the width dict (Passive line holders,
        # Task I).  Every other writer (mmdl_*, structured_close, retreat_*,
        # line_break_collapse, tes_support, unified_close, decisions)
        # legitimately scripts BC-relative / compliance / anchor X and must
        # never be flagged against the pure line-hold width.
        width_fresh = (
            fresh_geometry
            and depth_role in ("A", "B", "C", "D", "CENTRE", "WINGER")
            and not fb_active and not frozen and not kickoff
            and target_x is not None
            and target_source == "structured_open_play"
        )
        if width_fresh:
            width_dict = getattr(world, "frame_width_targets", None) or {}
            expected_fresh_x = width_dict.get(id(d))
            if expected_fresh_x is not None:
                if abs(target_x - expected_fresh_x) > TARGET_Y_DRIFT_TOLERANCE_M:
                    stale_target_x = True

        # -- 5a. STALE DEPTH ROLE --
        if (depth_role in ("A", "B", "C", "D", "CENTRE", "WINGER")
                and not fb_active and not kickoff and fresh_geometry):
            frame_y = _frame_spacing_y(world, d, tuners)
            stamped_y = getattr(d, "role_depth_target_y", None)
            if stamped_y is not None and frame_y is not None:
                if abs(stamped_y - frame_y) > RUCK_SIDE_TOLERANCE_M * 4.0:
                    stale_depth_role = True

        # -- 5b. STALE TARGET_Y --
        # TASK 2 (2026-08-14) — the committed tackler writer is a legal
        # spacing override: a PRIMARY/SECONDARY tackler's target_y must be
        # bc.y (the collision point) to make contact, NOT its spacing lane.
        # The audit therefore never flags `tackler_commit` against the
        # spacing chain — mirroring the stale_target_x gate that only fires
        # on `structured_open_play` (the one pure line-hold writer).
        if (structured and not kickoff
                and depth_role in ("A", "B", "C", "D", "CENTRE", "WINGER")
                and not fb_active and not frozen and target_y is not None
                and fresh_geometry
                and target_source != "tackler_commit"):
            expected_fresh = _frame_spacing_y(world, d, tuners)
            if expected_fresh is not None:
                if abs(target_y - expected_fresh) > TARGET_Y_DRIFT_TOLERANCE_M:
                    stale_target_y = True

        # -- 5c / 5f. CROSS-SIDE TARGET_Y + WRONG-SIDE-OF-RUCK --
        if (ruck_y is not None and depth_side in (-1, 1)
                and not fb_active and target_y is not None):
            target_signed = 0
            if target_y < ruck_y - RUCK_SIDE_TOLERANCE_M:
                target_signed = -1
            elif target_y > ruck_y + RUCK_SIDE_TOLERANCE_M:
                target_signed = 1
            if target_signed != 0 and target_signed != depth_side:
                cross_side_target_y = True
                wrong_side_of_ruck = True

        # -- 5d. MISSING TARGET_SOURCE --
        if (not frozen and not fb_active and not kickoff
                and depth_role in ("A", "B", "C", "D", "CENTRE", "WINGER")
                and target_source is None):
            missing_target_source = True

        # -- 5e. KICKOFF LANE VIOLATION --
        if kickoff and not kickoff_ok:
            kickoff_lane_violation = True

        # -- 5g. MOVEMENT WITH INVALID TARGET_Y --
        if not frozen and not fb_active and depth_role is not None:
            if target_y is None:
                invalid_target_y_movement = True
            else:
                try:
                    target_y_num = float(target_y)
                except (TypeError, ValueError):
                    target_y_num = float("nan")
                import math as _math
                if _math.isnan(target_y_num):
                    invalid_target_y_movement = True
                elif not (0.0 <= target_y_num <= 70.0):
                    invalid_target_y_movement = True

        # -------------------------------------------
        # PRINT REPORT FOR THIS DEFENDER
        # -------------------------------------------
        side_str = {-1: "L", 1: "R"}.get(depth_side, "-")
        print(f"\nDef {rid}: x={dx:.2f} y={dy:.2f} "
              f"id={rid} fb={fb_active}")
        print(f"  role={role_name} depth_role={depth_role} depth_side={side_str} "
              f"depth_anchor_drift={anchor_drift if anchor_drift is not None else '-'} "
              f"depth_ok={depth_ok}")
        print(f"  target_source={target_source} "
              f"target=({target_x if target_x is None else f'{target_x:.2f}'},"
              f"{target_y if target_y is None else f'{target_y:.2f}'})")
        if expected_spacing_y is not None:
            print(f"  expected_spacing_y={expected_spacing_y:.2f} "
                  f"target_drift={target_drift:.2f} "
                  f"(tol {TARGET_Y_DRIFT_TOLERANCE_M:.1f}) target_ok={target_ok}")
        print(f"  ruck_side_ok={ruck_side_ok}")
        if kickoff:
            print(f"  kickoff_lane_ok={kickoff_ok}")

        # Invariant sheet 8.3 flag summary line.
        print(f"  INVARIANT-8.3: stale_depth_role={stale_depth_role} "
              f"stale_target_y={stale_target_y} "
              f"cross_side_target_y={cross_side_target_y} "
              f"missing_target_source={missing_target_source} "
              f"kickoff_lane_violation={kickoff_lane_violation} "
              f"wrong_side_of_ruck={wrong_side_of_ruck} "
              f"invalid_target_y_movement={invalid_target_y_movement} "
              f"stale_geometry={stale_geometry} "
              f"stale_target_x={stale_target_x} "
              f"residual_frame_geometry={residual_frame_geometry}")

        # -------------------------------------------
        # FLAG PROBLEMS
        # -------------------------------------------
        if not depth_ok:
            print(f"  !!! DEPTH ROLE DISTANCE — {depth_role} anchor "
                  f"drift {anchor_drift:.2f} m > 6.0 (mid-glide ok, "
                  f"check if persistent)")

        if not target_ok:
            print(f"  !!! TARGET_Y DRIFT — target_y={target_y:.2f} "
                  f"spacing_y={expected_spacing_y:.2f} drift={target_drift:.2f} m "
                  f"writer={target_source}")

        if not ruck_side_ok:
            print(f"  !!! WRONG SIDE OF RUCK — locked depth_side={side_str} "
                  f"target_y={target_y:.2f} ruck_y={ruck_y:.2f} "
                  f"writer={target_source}")

        if kickoff and not kickoff_ok:
            print(f"  !!! KICKOFF LANE ERROR — "
                  f"{'fullback off its flat-line anchor' if fb_active else 'chaser not holding inertial lane'} "
                  f"target=({target_x if target_x is None else f'{target_x:.2f}'},"
                  f"{target_y if target_y is None else f'{target_y:.2f}'})")

        if stale_depth_role:
            print(f"  !!! STALE DEPTH ROLE — role_depth_target_y "
                  f"{getattr(d, 'role_depth_target_y', None)} vs live frame spacing Y "
                  f"({_frame_spacing_y(world, d, tuners):.2f}) — seed not re-stamped "
                  f"for the current ruck")

        if stale_target_y:
            print(f"  !!! STALE TARGET_Y — target_y={target_y:.2f} vs fresh "
                  f"frame spacing Y {_frame_spacing_y(world, d, tuners):.2f} "
                  f"(frame dict fresh this frame) writer={target_source}")

        if missing_target_source:
            print(f"  !!! MISSING TARGET_SOURCE — depth_role={depth_role} "
                  f"unfrozen with no writer stamp")

        if kickoff_lane_violation and not kickoff_ok:
            print(f"  !!! KICKOFF LANE VIOLATION — "
                  f"{'fullback off its flat-line anchor' if fb_active else 'chaser not holding inertial lane'}")

        if invalid_target_y_movement:
            print(f"  !!! INVALID TARGET_Y MOVEMENT — target_y={target_y!r} "
                  f"outside legal 0..70 band")

        if stale_geometry:
            print(f"  !!! STALE GEOMETRY — frame_geometry_ready="
                  f"{getattr(world, 'frame_geometry_ready', False)} "
                  f"frame stamp {getattr(world, 'frame_geometry_frame', None)} "
                  f"vs frame_count {getattr(world, 'frame_count', None)} — a writer "
                  f"consumed geometry WITHOUT this frame's preamble (writer={target_source})")

        if stale_target_x:
            print(f"  !!! STALE TARGET_X — target_x={target_x:.2f} vs fresh "
                  f"frame width X from frame_width_targets — writer consumed "
                  f"stale/other X geometry (writer={target_source})")

        if residual_frame_geometry:
            print(f"  !!! RESIDUAL FRAME GEOMETRY (KICKOFF) — "
                  f"frame_geometry_ready still True during kickoff flight: "
                  f"a prior sequence's dict was not cleared by "
                  f"reset_for_kickoff's clear_frame_geometry()")

    print("=== END REPORT ===\n")


def run_defender_state_audit(world) -> None:
    """One-argument engine entry point.

    Reads world.defending_team / world.ball_carrier / world.phase /
    world.sub_state and dispatches to investigate_defender_state().
    """
    defenders = getattr(world, "defending_team", None) or []
    bc = getattr(world, "ball_carrier", None)
    investigate_defender_state(
        world,
        defenders,
        bc,
        getattr(world, "phase", None),
        getattr(world, "sub_state", None),
    )
