"""
ruck_adjacent_pin.py — FINAL-AUTHORITY ruck-adjacent first-defender pin
plus slot-2 min/max-gap constraint (side-sign symmetric).

PURPOSE
-------
Engine law (2026-08-22): the FIRST defender in the defensive line on EACH
side of the ruck sits EXACTLY 2.0 m from the ruck's Y-axis — no exceptions.

Engine law (2026-08-23, revised 2026-09-07): SLOT-2 — the first free
defender after the A-pin on each side — is held in a SIDE-SIGN SYMMETRIC
band between ``RUCK_PIN_SLOT2_MIN_GAP_M`` and ``RUCK_PIN_SLOT2_MAX_GAP_M``
from the A-lane.  This is a SOFT constraint, not a hard pin: within the band
the defender's organic spacing is untouched; beyond it (drift OUTWARD past
the max gap, or collapse INWARD onto the pivot / across the ruck past the
min gap) the drift (retreat noise, band-pull, inside-out sorting,
structured-close, MMDL slide/hold, finalise blend — all of which still
rewrite non-A defenders' Y every frame) is clamped back.  The band uses
``anchor + side * gap`` (side = -1 LEFT, +1 RIGHT) so it always lives on the
OUTWARD side of the A-pivot — a LEFT slot-2 is never clamped to the RIGHT
half of the field and vice-versa.

The split-spacing chain (defensive_split_spacing.compute_targets) already
pins its innermost per-side slot to ``ruck_adjacent_anchor(ruck_y, side)``,
but the live count-driven side split can assign that slot to a defender
physically on the OTHER side of the ruck (cross-ruck reassignment), while
the physically-nearest defender on a side receives an interior lane —
leaving NOBODY targeted at ruck_y +/- 2.0 m on that side (measured live:
one side at 2 m, the other at 5-8 m).

This module is the LAST WRITER of the frame.  Called once per
OpenPlayEngine.update() AFTER every phase handler, the SDM, and the
freeze-compliance pass, it:

  1. SELECTS the first TWO defenders per side BY CURRENT PHYSICAL
     POSITION (nearest eligible line defenders with y strictly on that
     side of ruck_y), each with its own hysteresis so neither slot
     flip-flops between defenders mid-transit.
  2. PINS slot-1 ``target_y`` to ``ruck_adjacent_anchor(ruck_y, side)``
     — but ONLY when slot-1 is a genuine A-pivot (depth_role == "A").
     During the reform window the true A-pivot is often occupied as a
     marker/tackler (excluded via _pin_eligible), so the pin falls back
     to the physically-nearest body.  With a non-A fallback the pin does
     NOT hold the A-lane at all — the marker/tackler owns the ruck — and
     the fallback body keeps its organic target so it returns to its
     proper lane.  No tuner, width factor, band pull, governor clamp,
     or sideline clamp may move the genuine A-pivot.
  3. PLACES slot-1's actual ``y`` exactly on the anchor EVERY frame —
     no exceptions, but only for a genuine A-pivot.  (Live evidence:
     during TES phases the arrival controller can park a defender short
     of its own target indefinitely, so convergence is not a guarantee.)
  4. CLAMPS slot-2 into the side-sign band ``[anchor + side*MIN_GAP,
     anchor + side*MAX_GAP]`` (side-sign symmetric): whenever slot-2's
     position or target exits that band (drift outward or collapse
     inward / cross-ruck) it is pulled back to the nearest bound and
     outward velocity is zeroed.  Inside the band slot-2 is completely
     untouched — organic geometry preserved.  A WINGER slot-2 whose
     spacing-chain target is intentionally wide beyond the band is
     YIELDED (role-gated) so the wide anchor keeps its half-width slot.
  5. PRINTS one audit line EVERY frame with the MEASURED Y-distance
     ``|d.y - ruck_y|`` for both slot-1s and the MEASURED GAP from the
     A-lane for both slot-2s — the compliance record.

Exclusions (their own writers own them): markers, dummy-half, TES
tacklers/joiners, kick-chase, chase-target owners, the dynamic fullback,
frozen players, and TACKLE_INIT / TACKLE_CONTACT / TACKLE_DRIVE /
TACKLE_DOMINANCE target-only locking (TES owns the drive window; the pin
locks Y targets but never snaps position).
"""

from __future__ import annotations

import os

from player.player import DefenderRole
from player.movement_states import MovementState
from game.defence.defence_lvl_1.defensive_split_spacing import (
    ruck_adjacent_anchor,
    LEFT,
    RIGHT,
)
from game.defence.defence_lvl_1.defensive_tuners import post_tes_settle_active
from game.defence.defence_lvl_1.slot_arbitration import (
    update_slot_target,
    lock_slot,
    claim_slot_exclusive,
    PRIORITY_PIN,
)
from game.defence.defence_lvl_1.tes_drive_structure import (
    is_tes_structure_hold,
)

# A defender within this distance of ruck_y belongs to NEITHER side
# (standing on the ruck line) — never selected.
RUCK_PIN_SIDE_EPS_M: float = 0.05

# Keep the previous frame's pick unless a newcomer is closer by more
# than this (prevents slot flip-flop between two near-equidistant
# defenders while the line settles).  Applied independently to slot-1
# and slot-2.
RUCK_PIN_HYSTERESIS_M: float = 0.5

# SLOT-2 SOFT CONSTRAINT: maximum allowed gap between the second
# defender and the A-lane (the ruck-adjacent anchor).  Within this
# bound slot-2 is untouched; beyond it, position and target are
# clamped back to exactly this distance from the anchor.
RUCK_PIN_SLOT2_MAX_GAP_M: float = 3.5

# SLOT-2 MIN-GAP FLOOR (2026-09-07): the slot-2 must stay at least this
# far OUTWARD of the A-lane (in the side-sign direction).  A slot-2 that
# drifts INWARD toward the A-pivot (gap -> 0) — collapsing/stacking onto
# it or sitting between the pivot and the ruck — is pushed back out to
# this minimum.  Combined with the MAX_GAP the slot-2 is held in the
# side-sign band [anchor + side*MIN, anchor + side*MAX]; this is the
# guard against the "open-side outer defenders collapsing" defect.
RUCK_PIN_SLOT2_MIN_GAP_M: float = 1.0

# MAX per-frame convergence for the reform-window slot-1 snap / slot-2 gap
# clamp (2026-09-07).  The raw position writes (slot1.y = anchor /
# slot2.y = clamped) teleported a far body onto its lane in ONE frame
# (RUCK-PIN f=182 gap 12.93->3.50, f=212 slot-1 25.73->29.38).  A body
# farther than RUCK_PIN_GLIDE_THRESHOLD_M from its lane now converges at
# most RUCK_PIN_GLIDE_SPEED_MPS * dt each frame; a body within the
# threshold still snaps so the pin owns convergence when close.
RUCK_PIN_GLIDE_THRESHOLD_M: float = 0.5
RUCK_PIN_GLIDE_SPEED_MPS: float = 6.0

# Roles that play tight to the ruck / are engaged in the tackle — their
# own writers own their targets; they can never hold a line slot.
_PIN_EXCLUDED_ROLES = frozenset({
    DefenderRole.MARKER_A,
    DefenderRole.MARKER_B,
    DefenderRole.DUMMY_HALF,
    DefenderRole.TACKLER_1,
    DefenderRole.TACKLER_2,
    DefenderRole.TACKLER_3,
    DefenderRole.DEF_CHASE,
    # STAGE 4B (2026-09-19) — RETREAT OBLIGATION WINS OVER THE PIN.
    # A body that has flipped to DEF_RETREAT_10 owes the 10 m retreat; its
    # owner is the retreat writer.  This set did not list it, so the pin
    # was eligible for retreating bodies and held them in the ruck-adjacent
    # band, fighting the retreat in the SAME frame.  Measured by
    # tmp/probe_stage4b_exit_ownership.py: 838 "retreat_manager -> ruck_pin"
    # write pairs, plus 831 UNDECLARED ``ruck_pin`` writes on
    # class=DEF_RETREAT_10 bodies during PTB_PREP / PTB_EXEC / PTB.
    # The deferred-retreat design is unaffected: bodies held FOR the pin
    # through RELEASE -> PTB carry DEF_LINE_READY and only flip to
    # DEF_RETREAT_10 in _clear_marker_roles at POST_RUCK entry.
    DefenderRole.DEF_RETREAT_10,
})

# TES phases where the pin must LOCK the slot Y TARGETS
# (target-only, no position mutation — TES owns position/velocity).
# TACKLE_INIT/TACKLE_CONTACT were historically fully SKIPPED, leaving the
# slot-1 (A-pivot) and slot-2 Y targets free to be drifted by TES-phase
# writers / the integrator.  TACKLE_DRIVE/TACKLE_DOMINANCE were added to
# the full position snap set, but that re-enabled the ruck-pin positional
# teleport of the outside defenders during the drive (2026-09-07); they
# are now target-only too so the depth-locked line GLIDES onto its lane
# instead of being snapped.  The Y slot lock keeps them locked to the
# structural values across the whole TES window.
_TES_TARGET_LOCK_PHASES = frozenset({
    "TACKLE_INIT", "TACKLE_CONTACT",
    "TACKLE_DRIVE", "TACKLE_DOMINANCE",
})


def _tes_y_lock_enabled(world) -> bool:
    """True when the TES target-only Y slot lock is active (default ON).

    Feature flag (2026-09-07 rollout): the Y-lock was always-on during
    TACKLE_INIT / TACKLE_CONTACT.  ``RLM_TES_Y_LOCK=0`` (or
    ``world.tes_y_lock_enabled is False``) disables it, reverting the pin to
    the pre-lock full-skip in the TES attach window so playtesters can compare
    with / without the lock.  Mirrors the diagnostics_on / audit_on
    env-gate pattern (explicit world override wins; env string "0" = off).
    """
    w_override = getattr(world, "tes_y_lock_enabled", None)
    if w_override is not None:
        return bool(w_override)
    return os.environ.get("RLM_TES_Y_LOCK", "1") != "0"

# Phases in the post-tackle line-reforming window where the pin must OWN
# the ruck-adjacent bodies (A-pivot + markers): force-snap position (not
# just target) so they converge on their lanes instead of being parked
# short by the movement integrator.  This is the recovery span from
# TES hand-over through to the first OPEN_PLAY frame.
#
# NOTE (2026-09-07): TACKLE_DRIVE / TACKLE_DOMINANCE were removed from this
# set — they are now TARGET-ONLY (see _TES_TARGET_LOCK_PHASES) so the pin
# never snaps the outside defenders' position during the drive.  The
# reform-snap window now begins at RELEASE.
_REFORM_WINDOW_PHASES = frozenset({
    "RELEASE", "TACKLE_RELEASE",
    "PTB_READY", "PTB_PREP", "PTB_EXEC", "PTB",
    "POST_PTB_RESET", "POST_RUCK_OPEN_PLAY",
})


def _pin_eligible(d, world) -> bool:
    """True when *d* may hold a ruck-adjacent pin slot."""
    if d is None:
        return False
    if getattr(d, "fullback_role_active", False):
        return False
    # TACKLE-STATE GATE (2026-08-23): holders inside the tackle-radius
    # never hold an A-pin / slot-2 -- their own writers own their targets
    # until the gate releases them on radius exit.
    if getattr(d, "tackle_state_active", False):
        return False
    # STAGE 4B (2026-09-19) — FREEZE AGREEMENT, NOT A CONJUNCTION.
    # The freeze-compliance writer (movement_contracts + the update() tail)
    # pins on ``frozen`` ALONE, but this test required ``freeze_timer > 0``
    # as well, so a body with frozen=True and freeze_timer==0 was pinned by
    # the freeze pass and then position-snapped by this pin in the SAME
    # frame.  Measured: 1,371 "freeze_pin -> ruck_pin" write pairs on one
    # body-frame (probe_stage4b_exit_ownership).  The pin only ADDS geometry
    # authority, so it must never outrank a freeze — a frozen body is never
    # pin-eligible.  (C6 of verify_exit_boundary_ownership_contract reports
    # the asymmetry; freeze_timer is not even reachable in the pipeline.)
    if getattr(d, "frozen", False):
        return False
    if getattr(d, "defender_role", None) in _PIN_EXCLUDED_ROLES:
        return False
    # ── TES DRIVE STRUCTURE-HOLD EXEMPTION (2026-09-11) ──
    # A line body held on its TES drive structure cell is owned by
    # tes_drive_structure.py — the declared "ONLY line writer for the
    # chamber".  The pin's Y writes are BC-independent but LANE-UNTRUE:
    # they retarget a held body to ruck_y ± 2.0.  Measured live on a held
    # body whose locked lane was 40.95 m with ruck_y 44.45: the pin wrote
    # target_y = 42.45 (ruck_y - 2.0), the body read DRIFT at 2.5 m/s and
    # left its lane by 1.5 m — the residual "drift" the lane-true contract
    # forbids.  Excluding held bodies keeps exactly ONE authority on the
    # drive line; the pin still owns markers, the DH, tacklers and every
    # UNHELD body, and re-owns held bodies the moment the chamber closes
    # (``clear_tes_drive_structure`` drops the hold at RELEASE).
    if is_tes_structure_hold(d):
        return False
    # TES participants (primary + joiners) are owned by TES physics.
    tackler_ids = getattr(world, "tackler_ids", None) or set()
    if getattr(d, "player_id", None) in tackler_ids:
        return False
    # Chase-owned defenders carry live chase target fields.
    if hasattr(d, "chase_target_x") or hasattr(d, "chase_target_y"):
        return False
    return True


def _pick_with_hysteresis(residents, ruck_y, prev_id):
    """Nearest resident, honouring hysteresis against *prev_id*."""

    def _dist(o):
        return abs(float(o.y) - ruck_y)

    nearest = min(
        residents, key=lambda o: (_dist(o), getattr(o, "player_id", 0))
    )
    holder = next(
        (o for o in residents if getattr(o, "player_id", None) == prev_id),
        None,
    )
    if (
        holder is not None
        and holder is not nearest
        and _dist(holder) <= _dist(nearest) + RUCK_PIN_HYSTERESIS_M
    ):
        return holder
    return nearest


def _pick_a_pivot(residents, ruck_y, prev_id):
    """Prefer the genuine A-pivot (depth_role == 'A'); fall back to nearest.

    RUCK-PIN ROLE-AWARE (2026-09-02): the pin must hold the A-LANE on the
    spacing chain's ACTUAL A-pivot, not just whoever happens to be
    physically nearest.  During the line-reforming window (RELEASE/PTB)
    the nearest body can be a WINGER/CENTRE (the chain wants it wide);
    pinning it to the A-lane drags the line inward and teleports the
    wrong defender.  When an A-stamped pivot exists on the side it is the
    slot-1 holder; otherwise fall back to physical-nearest (pre-A-role
    bootstrap, all-D sides).  NOTE (2026-09-07): a non-A fallback is a
    CANDIDATE ONLY — the enforcer role-gates the A-lane snap to a genuine
    A-pivot, so a fallback outer-role body is never dragged onto the lane.
    """
    a_pivots = [
        d for d in residents
        if getattr(d, "depth_role", None) == "A"
    ]
    if a_pivots:
        return _pick_with_hysteresis(a_pivots, ruck_y, prev_id)
    return _pick_with_hysteresis(residents, ruck_y, prev_id)


def _pick_interior_slot2(slot1, residents, ruck_y, prev_id):
    """Prefer an interior chain body over a wide anchor for slot-2.

    RUCK-PIN ROLE-AWARE (2026-09-02): slot-2 is the "first free defender
    after the A-pin".  It must be an INTERIOR body (B/C/D) that genuinely
    sits tight to the pivot — never a WINGER/CENTRE outer anchor, whose
    correct chain slot is the wide half-width (and which the 3.5 m clamp
    would otherwise drag inward).  Falls back to nearest when no interior
    body exists on the side (sparse / all-outer line).
    """
    rest = [d for d in residents if d is not slot1]
    if not rest:
        return None
    interior = [
        d for d in rest
        if getattr(d, "depth_role", None) in ("B", "C", "D")
    ]
    if interior:
        return _pick_with_hysteresis(interior, ruck_y, prev_id)
    return _pick_with_hysteresis(rest, ruck_y, prev_id)


def _clamp_to_band(value, band_lo, band_hi):
    """Clamp *value* into [band_lo, band_hi] (inclusive, order-agnostic).

    band_lo <= band_hi is guaranteed by the caller (the side-sign slot-2
    band is built with min/max).  Values outside the band snap to the
    nearest bound.  Used by enforce_ruck_adjacent_pin for the slot-2
    position AND target clamps.
    """
    if value < band_lo:
        return band_lo
    if value > band_hi:
        return band_hi
    return value


def _ptb_lateral_deadband_active(phase_name: str) -> bool:
    """True during the last PTB approach when micro-lateral writes are noise.

    A defender already sitting on the correct ruck-adjacent lane should not be
    re-targeted by a tiny final-authority write in the last PTB frames.  This is
    a deadband guard for the final write path, not a general lane-authority
    bypass.
    """
    return phase_name in {"PTB_PREP", "PTB_EXEC", "PTB"}


def _skip_micro_lateral_write(slot, target_y: float, phase_name: str, deadband_m: float = 0.25) -> bool:
    """Ignore final target writes that only nudge a defender by a tiny amount.

    This prevents one final PTB-frame write from yanking a defender by a few
    cm just before the handoff when the lane is already effectively correct.
    """
    if not _ptb_lateral_deadband_active(phase_name):
        return False
    if slot is None:
        return False
    current_y = float(getattr(slot, "target_y", getattr(slot, "y", target_y)))
    return abs(current_y - target_y) <= deadband_m


def select_first_two_defenders(world) -> dict:
    """Return {LEFT: (slot1, slot2), RIGHT: (slot1, slot2)}.

    Slot-1 is the nearest eligible line defender by CURRENT physical y
    on each side of the ruck; slot-2 is the nearest eligible resident
    excluding slot-1.  Each slot keeps its own hysteresis state under
    ``world._ruck_pin_state`` (keys ``L``/``R`` and ``L2``/``R2``).
    """
    ruck_y = getattr(world, "ruck_y_m", None)
    if ruck_y is None:
        return {}
    ruck_y = float(ruck_y)

    pool = [
        d for d in (getattr(world, "defending_team", None) or [])
        if _pin_eligible(d, world)
    ]

    prev = getattr(world, "_ruck_pin_state", None)
    if not isinstance(prev, dict):
        prev = {}

    result = {}
    state = {}
    for side, key in ((LEFT, "L"), (RIGHT, "R")):
        if side == LEFT:
            residents = [
                d for d in pool
                if float(d.y) < ruck_y - RUCK_PIN_SIDE_EPS_M
            ]
        else:
            residents = [
                d for d in pool
                if float(d.y) > ruck_y + RUCK_PIN_SIDE_EPS_M
            ]
        if not residents:
            result[side] = (None, None)
            state[key] = None
            state[key + "2"] = None
            continue

        slot1 = _pick_a_pivot(residents, ruck_y, prev.get(key))
        slot2 = _pick_interior_slot2(
            slot1, residents, ruck_y, prev.get(key + "2")
        )
        result[side] = (slot1, slot2)
        state[key] = getattr(slot1, "player_id", None)
        state[key + "2"] = getattr(slot2, "player_id", None)

    world._ruck_pin_state = state
    return result


def select_first_defenders(world) -> dict:
    """Backward-compatible view: {LEFT: slot1, RIGHT: slot1}."""
    two = select_first_two_defenders(world)
    return {side: pair[0] for side, pair in two.items()}


def enforce_ruck_adjacent_pin(world) -> None:
    """Final-authority A-pin + slot-2 gap clamp + per-frame audit print.

    Runs ONCE per OpenPlayEngine.update(), after every target writer.
    See module docstring for the contract.
    """
    frame = getattr(world, "frame_count", 0)
    sub = str(getattr(world, "sub_state", "?")).split(".")[-1]
    ruck_y = getattr(world, "ruck_y_m", None)

    if ruck_y is None:
        return
    ruck_y = float(ruck_y)

    phase_name = str(getattr(world, "_ope_phase_label", "") or "")
    if not phase_name:
        # Caller stamps the live phase label before calling; fall back to
        # the world sub-state value string.
        phase_name = sub.upper()

    # ── TES TARGET-ONLY Y SLOT LOCK (2026-09-07) ──
    # In the TES engagement phases (INIT/CONTACT/DRIVE/DOMINANCE) the pin
    # locks the slot-1 (A-pivot) and slot-2 Y TARGETS to their structural
    # values so no TES-phase writer / integrator drifts them; position and
    # velocity remain owned by TES stasis / drive-depth-track (target-only
    # — no snap, no HOLD clear).  DRIVE/DOMINANCE previously took the full
    # position snap + band clamp, which re-enabled the outside-def
    # teleport; they are now target-only so the line glides.
    _target_only = phase_name in _TES_TARGET_LOCK_PHASES
    # ── TES Y-LOCK FEATURE FLAG (2026-09-07 rollout; default ON) ──
    # RLM_TES_Y_LOCK=0 (or world.tes_y_lock_enabled=False) disables the
    # target-only Y slot lock, reverting TACKLE_INIT/CONTACT to the pre-lock
    # behaviour (pin fully skips the attach window).  Marker lane hold +
    # diagnostics below are independent and still run.
    _y_lock_on = _tes_y_lock_enabled(world)
    _target_only = _target_only and _y_lock_on
    # ── POST-TES DRIVE-END SETTLE WINDOW (2026-09-06; phase-aware 2026-09-07) ──
    # While the drive-end settle window is active (DOMINANCE→RELEASE through
    # PTB_* and POST_RUCK_OPEN_PLAY) the pin is TARGET-ONLY: no slot-1
    # position snap, no slot-2 position clamp.  The bodies GLIDE to the
    # ruck-adjacent lane via the movement integrator instead of teleporting
    # around the ruck (the drive-era positions were previously masked by the
    # old hard-freeze).  The window disengages on the first OPEN_PLAY frame.
    if post_tes_settle_active(world):
        _target_only = True
    _run_slot_pin = not (not _y_lock_on and phase_name in _TES_TARGET_LOCK_PHASES)

    # ── OPEN_PLAY: SPACING CHAIN IS THE SOLE Y AUTHORITY (2026-09-02) ──
    # In OPEN_PLAY the split-spacing chain (frame_spacing_targets) already
    # pins the innermost per-side slot to ruck_adjacent_anchor and even-spaces
    # the rest.  The ruck-adjacent pin must NOT write ANY Y here — no slot-1
    # target pin, no position snap, no slot-2 clamp.  Writing Y in OPEN_PLAY
    # was the residual jitter source (it re-ordered physical proximity every
    # frame and flipped slot1/slot2 identity).  The pin only remains active
    # during the line-reforming window (TACKLE_DRIVE/DOMINANCE, RELEASE,
    # PTB_*, POST_RUCK_OPEN_PLAY) where the geometry pipeline is disabled and
    # it must hold the A-lane via a target-only (FOLLOW) pin.
    if phase_name == "OPEN_PLAY":
        print(
            f"[RUCK-PIN] f={frame} sub={sub} ruck={ruck_y:.2f} "
            f"| OPEN_PLAY spacing-owns-Y (pin Y-writes disabled)"
        )
        return

    picks = select_first_two_defenders(world)
    spacing_dct = getattr(world, "frame_spacing_targets", None)
    if not isinstance(spacing_dct, dict):
        spacing_dct = None

    parts = []
    if not _run_slot_pin:
        parts.append(
            f"TES_Y_LOCK=OFF {phase_name} -- slot writes SKIPPED (pre-lock)"
        )
    _slot_sides = ((LEFT, "L"), (RIGHT, "R")) if _run_slot_pin else ()
    for side, key in _slot_sides:
        slot1, slot2 = picks.get(side, (None, None))

        # ── SLOT-1: A-pin, ROLE-GATED (2026-09-07) ──────────────────
        # The A-lane force-snap applies ONLY to a genuine A-pivot
        # (depth_role == "A").  During the reform window the true A-pivot
        # is often occupied as a marker/tackler (excluded via
        # _pin_eligible), so _pick_a_pivot falls back to the
        # physically-nearest body — which can be an outer-role
        # (B/C/WINGER/CENTRE).  Snapping that wrong body onto the A-lane
        # dragged an outer-role defender into the ruck-adjacent lane when
        # it should be spaced further out, and sometimes onto the other
        # side of the ruck (user verdict 2026-09-07).  With a non-A
        # fallback the pin does NOT hold the A-lane at all — the
        # marker/tackler owns the ruck — and the fallback body keeps its
        # organic target so it returns to its proper lane.
        if slot1 is None:
            parts.append(f"{key} none")
        else:
            is_a_pivot = getattr(slot1, "depth_role", None) == "A"
            if not is_a_pivot:
                parts.append(
                    f"{key} id={getattr(slot1, 'player_id', '?')} "
                    f"role={getattr(slot1, 'depth_role', None) or '-'} "
                    f"N/A-pivot (no snap)"
                )
            else:
                anchor = ruck_adjacent_anchor(ruck_y, side)
                dist_before = abs(float(slot1.y) - ruck_y)

                # 1. TARGET PIN — final-authority soft-locked write.
                # In the final PTB frames, a tiny micro-write can yank a defender
                # laterally even when it is already effectively on the lane.  Skip
                # the final target write unless it is meaningfully offset from the
                # lane centre; this removes the last-frame jitter without changing
                # the normal reform-window lock outside PTB.
                if _skip_micro_lateral_write(slot1, anchor, phase_name):
                    parts.append(
                        f"{key} id={getattr(slot1, 'player_id', '?')} "
                        f"role={getattr(slot1, 'depth_role', None) or '-'} "
                        f"PTB_DEADBAND_SKIP target={anchor:.2f}"
                    )
                else:
                    lock_slot(world, slot1, anchor, "ruck_pin", PRIORITY_PIN)
                    _won1 = claim_slot_exclusive(world, frame, f"{key}1", "ruck_pin")
                    if not _won1:
                        parts.append(f"{key}1 OWNERSHIP-CONFLICT")
                    # Route through the per-slot override ring (No-op when the
                    # diagnostics platform is OFF; production byte-identical).
                    from game.debug.tes_diagnostics import slot_override_ring as _ring1
                    update_slot_target(
                        world, slot1, anchor, "ruck_pin", PRIORITY_PIN,
                        record=_ring1(world, f"{key}1"),
                    )
                    if spacing_dct is not None and id(slot1) in spacing_dct:
                        spacing_dct[id(slot1)] = anchor

                # 2. POSITION PLACEMENT (REFORM-SNAP, 2026-09-03): the pin must
                #    OWN the ruck-adjacent A-pivot in the reform window by writing
                #    POSITION (not just target), so the body converges on the
                #    anchor instead of being parked short by the movement
                #    integrator.  Previously the reform phases only did FOLLOW
                #    (target-only) — the integrator could stall the pivot short
                #    of its lane indefinitely.  OPEN_PLAY is handled ABOVE (the
                #    pin returns "spacing-owns-Y" and writes no Y); TACKLE_INIT /
                #    TACKLE_CONTACT use the target-only Y lock below.  This
                #    branch therefore only sees non-OPEN_PLAY phases.  The old
                #    FOLLOW-only reform window is replaced with a hard snap; the
                #    teleport/jitter trade-off is intentional — the pin must own
                #    convergence.
                # TARGET-ONLY TES LOCK (2026-09-07): in TACKLE_INIT/CONTACT the
                #    position is owned by TES stasis, so only target_y is locked
                #    (no snap / no HOLD clear / no frozen clear).
                if not _target_only:
                    # CAP CONVERGENCE (2026-09-07): the raw snap (slot1.y =
                    # anchor) teleported a far A-pivot onto the lane in ONE
                    # frame (RUCK-PIN f=212: 25.73 -> 29.38, ~3.65 m).  When
                    # the body is farther than RUCK_PIN_GLIDE_THRESHOLD_M from
                    # the anchor, glide toward it by at most RUCK_PIN_GLIDE_SPEED_MPS
                    # * dt per frame; a body within the threshold still snaps so
                    # the pin owns convergence when close.
                    _max_step = RUCK_PIN_GLIDE_SPEED_MPS * float(
                        getattr(world, "dt", 1.0 / 60.0)
                    )
                    _cur_y = float(slot1.y)
                    _dist_to_anchor = abs(_cur_y - anchor)
                    if _dist_to_anchor > RUCK_PIN_GLIDE_THRESHOLD_M:
                        _dir = 1.0 if anchor > _cur_y else -1.0
                        slot1.y = _cur_y + _dir * min(_max_step, _dist_to_anchor)
                    else:
                        slot1.y = anchor
                    slot1.vy = 0.0
                    # CLEAR HOLD IMMEDIATELY (2026-09-03): a HOLD-bodied A-pivot
                    # sits frozen on its stale position instead of letting the pin
                    # own the lane.  Clear HOLD/frozen so the body is live-ownable
                    # by the pin.
                    if getattr(slot1, "movement_state", None) == MovementState.HOLD:
                        slot1.movement_state = MovementState.RETREAT
                    slot1.frozen = False
                    if getattr(slot1, "freeze_timer", 0.0) > 0.0:
                        slot1.freeze_timer = 0.0
                status = (
                    "TLOCK" if _target_only
                    else ("SNAP" if dist_before > 0.02 else "HOLD")
                )

                # Compliance: for the target-only lock verify the TARGET is at the
                # anchor; for the reform snap verify the POSITION.
                _compliance_ref = (
                    float(slot1.target_y) if _target_only else float(slot1.y)
                )
                dist_after = abs(_compliance_ref - ruck_y)
                ok = "OK" if abs(dist_after - 2.0) < 0.05 else "OFF"
                parts.append(
                    f"{key} id={getattr(slot1, 'player_id', '?')} "
                    f"role={getattr(slot1, 'depth_role', None) or '-'} "
                    f"tgt={anchor:.2f} pos={float(slot1.y):.2f} "
                    f"d={dist_after:.2f}m {status}/{ok}"
                )

        # ── SLOT-2: soft MIN/MAX gap clamp, side-sign symmetric ──────
        # SYMMETRIC SIDE-SIGN BAND (2026-09-07): the old
        # bound_lo = anchor - MAX / bound_hi = anchor + MAX was not
        # side-sign aware — a LEFT slot-2 clamped to the inward bound
        # (ruck_y + 1.5) landed on the RIGHT side of the ruck, and a
        # RIGHT slot-2 clamped to ruck_y - 1.5 landed LEFT (the
        # cross-ruck teleport / wrong-role stacking consequence).  The
        # band is now anchored to the side sign so it lives ONLY on the
        # outward side of the A-pivot: anchor + s*MIN .. anchor + s*MAX
        # (s = -1 LEFT, +1 RIGHT).  This mirrors LEFT/RIGHT exactly and
        # prevents slot-2 from collapsing onto the pivot (gap -> 0) or
        # crossing the ruck.
        if slot2 is None:
            parts.append(f"{key}2 none")
            continue
        anchor = ruck_adjacent_anchor(ruck_y, side)
        _s = int(side)
        _min = RUCK_PIN_SLOT2_MIN_GAP_M
        _max = RUCK_PIN_SLOT2_MAX_GAP_M
        band_lo = min(anchor + _s * _min, anchor + _s * _max)
        band_hi = max(anchor + _s * _min, anchor + _s * _max)

        pos_gap = abs(float(slot2.y) - anchor)
        tgt_gap = abs(float(getattr(slot2, "target_y", anchor)) - anchor)
        clamped = False

        # ── YIELD TO SPACING CHAIN, ROLE-GATED (2026-09-07) ──
        # The clamp yields ONLY for a genuine WINGER slot-2 (the wide
        # anchor).  Previously the yield applied to ANY slot-2 whose
        # chain target sat beyond the bound, so during TES drive an
        # interior B/C/D (or CENTRE) slot-2 with a wide sparse-side chain
        # target drifted OUT away from the TES (user verdict 2026-09-07
        # "outside defenders push out away from tes except for winger").
        # Role-gating the yield to WINGER keeps the winger's wide
        # half-width behavior intact while every other slot-2 is clamped
        # to the band.
        _slot2_spacing_y = (
            spacing_dct.get(id(slot2)) if spacing_dct is not None else None
        )
        _spacing_wants_wide = (
            _slot2_spacing_y is not None
            and abs(float(_slot2_spacing_y) - anchor) > _max
        )
        _is_winger = getattr(slot2, "depth_role", None) == "WINGER"
        _yield_wide = _spacing_wants_wide and _is_winger

        # Position clamp — fires beyond EITHER bound (drift or collapse).
        # TARGET-ONLY TES LOCK (2026-09-07): in TACKLE_INIT/CONTACT the
        # position is owned by TES stasis, so only the target clamp runs
        # (no position mutation / velocity zero).
        if not _yield_wide and not _target_only:
            _y0 = float(slot2.y)
            _clamped_y = _clamp_to_band(_y0, band_lo, band_hi)
            if abs(_clamped_y - _y0) > 1e-9:
                # CAP CONVERGENCE (2026-09-07): the raw clamp (slot2.y =
                # clamped) teleported a far slot-2 onto the band in ONE frame
                # (RUCK-PIN f=182 gap 12.93->3.50, ~9 m).  Glide toward the
                # nearest band bound by at most RUCK_PIN_GLIDE_SPEED_MPS * dt
                # per frame; the target is still written to the clamped bound
                # so the movement integrator continues the convergence.
                _max_step2 = RUCK_PIN_GLIDE_SPEED_MPS * float(
                    getattr(world, "dt", 1.0 / 60.0)
                )
                _y0_cur = float(slot2.y)
                _dist_to_bound = abs(_clamped_y - _y0_cur)
                # TES-POS-WRITE instrument (2026-09-07): loud-print a large
                # reform-window slot-2 snap so the live run flags it.
                if _dist_to_bound > 0.8:
                    print(
                        f"[TES-POS-WRITE] ruck_pin_slot2 id={getattr(slot2, 'player_id', '?')}"
                        f" f={frame} phase={phase_name} dy={_dist_to_bound:.2f}"
                    )
                if _dist_to_bound > _max_step2:
                    _dir2 = 1.0 if _clamped_y > _y0_cur else -1.0
                    slot2.y = _y0_cur + _dir2 * _max_step2
                else:
                    slot2.y = _clamped_y
                slot2.vy = 0.0
                # POSITION CLAMP TARGET SYNC (2026-09-02): mutates ACTUAL y
                # but keeps target_y / _target_writer consistent so the
                # freeze-pin never attributes the clamp to the wrong writer.
                slot2.target_y = _clamped_y
                slot2._target_writer = "ruck_pin"
                if spacing_dct is not None and id(slot2) in spacing_dct:
                    spacing_dct[id(slot2)] = _clamped_y
                clamped = True

        # Target clamp — stops writers converging back past the band.
        if not _yield_wide:
            _t0 = float(getattr(slot2, "target_y", anchor))
            _clamped_t = _clamp_to_band(_t0, band_lo, band_hi)
            if not _skip_micro_lateral_write(slot2, _clamped_t, phase_name):
                _won2 = claim_slot_exclusive(world, frame, f"{key}2", "ruck_pin")
                if not _won2:
                    parts.append(f"{key}2 OWNERSHIP-CONFLICT")
                from game.debug.tes_diagnostics import slot_override_ring as _ring2
                _written_t = update_slot_target(
                    world, slot2, _clamped_t, "ruck_pin", PRIORITY_PIN,
                    record=_ring2(world, f"{key}2"),
                )
                # Engage the soft-lock on slot-2 so a lower-priority writer cannot
                # drift the clamped target beyond +/- SOFT_LOCK_RADIUS during TES.
                lock_slot(
                    world, slot2, float(getattr(slot2, "target_y", anchor)),
                    "ruck_pin", PRIORITY_PIN,
                )
                if abs(_written_t - _t0) > 1e-9:
                    if spacing_dct is not None and id(slot2) in spacing_dct:
                        spacing_dct[id(slot2)] = _written_t
                    clamped = True
            else:
                parts.append(
                    f"{key}2 id={getattr(slot2, 'player_id', '?')} "
                    f"role={getattr(slot2, 'depth_role', None) or '-'} "
                    f"PTB_DEADBAND_SKIP target={_clamped_t:.2f}"
                )

        gap_after = abs(float(slot2.y) - anchor)
        seg_status = "CLAMP" if clamped else "OK"
        parts.append(
            f"{key}2 id={getattr(slot2, 'player_id', '?')} "
            f"role={getattr(slot2, 'depth_role', None) or '-'} "
            f"gap={pos_gap:.2f}->{gap_after:.2f} {seg_status}"
        )

    # ── MARKER LANE HOLD (2026-09-04; RUCK-PIN GEOMETRY AUTHORITY) ──
    # Re-assert every MARKER_A/B lane target to the single ruck-pin geometry
    # authority (ruck_x + depth, ruck_y) so no retreat / spacing / pin writer
    # can drift a marker off its ruck lane during the RELEASE/PTB reform
    # window.  Never BC-relative.
    #
    # NEVER FORCE-SNAP (2026-09-07): a marker's POSITION is NEVER written
    # here.  The marker GLIDES to its ruck-pin lane via the movement
    # integrator (Player.update smooth-damp / accel-lerp toward
    # target_x/target_y).  The decision lock keeps the target at the pre-flip
    # value briefly so the glide is smooth after a ruck state flip, then
    # recomputes the live ruck pin; the large-ruck-move invalidation lets the
    # marker recover to the live pin when the ruck shifts > 2.0 m.  Marker
    # role assignment + geometry finalization happen ONLY at the canonical
    # Drive→Release transition (canary-flagged by
    # marker_assign_at_release_only=True), and the RUCK-PIN target re-assert
    # below is the single authority that keeps the lane target exact.
    from game.defence.defence_lvl_1.marker_authority import marker_geometry_target as _mt
    for _d in (getattr(world, "defending_team", None) or []):
        _d_role = getattr(_d, "defender_role", None)
        if _d_role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
            _tx, _ty = _mt(world, _d)
            _d.target_x = _tx
            _d.target_y = _ty

    # ── TES DIAGNOSTICS (off-by-default, 2026-09-07) ──
    # Lightweight per-frame capture during TES phases of lane derivation,
    # marker target and slot-2 chain target for short post-deploy monitoring.
    # NO-OP unless world._tes_diagnostics exists or RLM_TES_DIAGNOSTICS=1.
    from game.debug.tes_diagnostics import diagnostics_on as _tes_on
    if _tes_on(world):
        from game.debug.tes_diagnostics import record_tes as _tes_record
        _tes_record(world, frame, phase_name, picks, spacing_dct)

    print(f"[RUCK-PIN] f={frame} sub={sub} ruck={ruck_y:.2f} | " + " | ".join(parts))
