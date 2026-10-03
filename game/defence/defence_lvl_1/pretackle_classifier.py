"""pretackle_classifier.py — MovementState-driven pre-tackle engage classifier.

Replaces the binary distance fast-paths in ``try_pre_tackle`` with a single
posture-driven classification.  The classifier is the SOLE consumer of
``MovementState`` for the TES-engage decision and enforces, in order:

  1. Role eligibility (one shared predicate with the call sites).
  2. Lane-slot alignment (``is_slot_tackle_eligible``) as a HARD engage
     conjunct — a slot-OUT body is never a valid engage candidate.
  3. Real planted posture: a genuine stationary square-up (zero velocity),
     NOT the overloaded ``pre_tackle_stance`` / ``footplanting`` flags which
     ``_apply_flatfoot_step_in`` stamps on a full-speed CHASE.
  4. Closing posture via ``MovementState`` (CHASE / SHOOT).
  5. Distance as a NECESSARY (not sufficient) conjunct — there is no
     distance-only fast-path.
  6. Approach angle measured from the tackler's facing/geometry plane, not
     velocity direction.
  7. Closing-speed / planted gate, then the full evasion stack (which a
     closing CHASE must satisfy entirely).

DESIGN NOTES
------------
The ORIGINAL gate had two distance-only fast-paths (``collision_priority``
HIGH and "planted/committed") that returned True on distance alone, bypassing
the evasion stack.  This allowed ``_apply_flatfoot_step_in`` — which stamps
``pre_tackle_stance=True`` / ``footplanting=True`` on a full-speed, non-frozen
CHASE defender — to auto-engage TES on distance.  The classifier removes both
fast-paths.  A genuinely stationary square-up (zero velocity) is the ONLY
"planted" posture and it is still gated on slot + angle + random + wrong-foot;
any moving CHASE (committed tackler OR flat-foot step-in) must satisfy the
full evasion stack, so it can never auto-engage on distance alone.

WRONG-FOOT BASELINE
-------------------
The classifier reads ``bc.prev_facing`` at entry (falling back to the current
facing on the first frame) and writes it back to ``bc.facing`` at exit on
EVERY call, so the facing delta is a per-frame derivative rather than a
"since the last successful tackle" snapshot.  In the unified path (one call
per frame per primary) this is exactly the per-frame delta; the legacy
multi-defender chase loop returns on the first True, so only the first call's
delta is meaningful there.
"""

import math

from player.movement_states import MovementState
from game.defence.defence_lvl_1.slot_arbitration import (
    is_slot_tackle_eligible,
)


# ── Tunable constants (defaults mirror the original gate) ──────────────
ENGAGE_DISTANCE_M: float = 2.0
MAX_APPROACH_ANGLE_DEG: float = 120.0
BC_OUTRUN_MARGIN_MPS: float = 3.0
DEF_OVERSHOOT_MARGIN_MPS: float = 2.0
MOMENTUM_RATIO_FLOOR: float = 0.5
BASE_RANDOM_FAIL: float = 0.05
WRONG_FOOT_FACING_DELTA: float = 0.3   # radians (~17 deg)
PLANTED_VELOCITY_EPS: float = 0.01
# Angle-quality slip bands (degrees).
HEAD_ON_MAX_DEG: float = 30.0
SIDE_ON_MAX_DEG: float = 60.0
WRONG_FOOT_BASE_CHANCE: float = 0.4
WRONG_FOOT_SKILL_SCALE: float = 0.15


# ── Shared role-eligibility predicate (Finding 7) ───────────────────────
def is_engage_role(defender) -> bool:
    """True when *defender* may start a tackle (PRIMARY / SECONDARY).

    This is the SINGLE role-eligibility predicate shared by the classifier
    and the call sites' primary-selection loops, so the caller's candidate
    set and the gate's role filter can never drift.  THIRD is deliberately
    excluded — it is a joiner-only concept, not a tackle-initiator role.
    """
    if getattr(defender, "fullback_role_active", False):
        from game.defence.defence_lvl_1.fullback_role import (
            FB_EMERGENCY_ENGAGE_AUTHORITY_ATTR,
        )
        return bool(getattr(defender, FB_EMERGENCY_ENGAGE_AUTHORITY_ATTR, False))
    return getattr(defender, "tackle_role", None) in ("PRIMARY", "SECONDARY")


def select_primary_candidate(world, defenders, bc):
    """Return the closest PRIMARY/SECONDARY engage candidate, preferring a
    slot-eligible one.

    Shared by the call sites' primary-selection loops so the candidate set
    and the classifier's role filter use ONE predicate (Finding 7 — the old
    call sites included THIRD and could select a THIRD nearest body the gate
    then rejected).  THIRD is joiner-only and excluded from primary
    selection.  Among engage-role candidates the closest slot-eligible body
    is preferred (Finding 1 follow-through): a slot-OUT nearest body can no
    longer starve a valid slot-IN tackle.  If no candidate is slot-eligible,
    the closest engage-role body is still returned — the classifier will
    reject it, but the frame selects a real candidate instead of silently
    passing ``None``.
    """
    if bc is None:
        return None
    # Live engage tuners (the guard's radius / facing band).  The live call
    # sites keep tuners on the world or the behaviour object; fall back to
    # the guard's own defaults when neither is present.
    _tun = (getattr(world, "defence_tuners", None)
            or getattr(world, "tuners", None))
    from game.defence.defence_lvl_1.tackle_engage_guard import (
        is_tackle_engage_guard as _is_engage_guard,
    )
    primary = None
    primary_dist = math.inf
    slot_primary = None
    slot_primary_dist = math.inf
    for d in (defenders or ()):
        # RADIAL+FACING TACKLE ENGAGE GUARD (2026-09-10) — CANDIDACY.
        # Admission is ``is_engage_role(d)`` OR the guard.  A DEF_LINE_READY
        # line body carries ``tackle_role = None``, so the role filter ALONE
        # excluded every radial-in, facing line body from the candidate set:
        # the body was never handed to ``try_pre_tackle``, so the tackle
        # pipeline could not fire at all — the FINAL chokepoint of
        # "no tackles ever".  The guard's own predicate (genuine line body +
        # radial-in + facing at the carrier) is the admission ticket; once
        # admitted, ``pretackle_machine.run_machine`` STAMPS the TES engage
        # role on the body (``stamp_tes_tackler_role``).  Scope stays narrow:
        # markers / dummy-half / fullback / kick-chase / TES tacklers / frozen
        # / freeze-timer bodies are all rejected by the guard itself.
        guarded = _is_engage_guard(d, bc, world, _tun)
        if not (is_engage_role(d) or guarded):
            continue
        dist = math.hypot(
            float(getattr(d, "x", 0.0) or 0.0) - float(bc.x),
            float(getattr(d, "y", 0.0) or 0.0) - float(bc.y),
        )
        if dist < primary_dist:
            primary_dist = dist
            primary = d
        # A guarded body counts as slot-eligible for the preference too, so
        # a radial-in, facing body can never be starved by a slot-eligible
        # one at the live call sites (which hand exactly ONE candidate to
        # ``try_pre_tackle`` per frame).
        if (dist < slot_primary_dist
                and (is_slot_tackle_eligible(bc, d, world) or guarded)):
            slot_primary_dist = dist
            slot_primary = d
    return slot_primary if slot_primary is not None else primary


# ── Posture predicates ────────────────────────────────────────────────────
def is_real_planted_posture(defender, eps: float = PLANTED_VELOCITY_EPS) -> bool:
    """True when *defender* is genuinely stationary (a square-up hold).

    REAL PLANTED = zero total velocity.  This distinguishes a genuine
    planted stance (``apply_pre_tackle_stance`` / the pre-tackle authority's
    PLANTED freeze — both zero the velocity) from a flat-foot forced step-in
    (``_apply_flatfoot_step_in``) which stamps ``pre_tackle_stance=True`` /
    ``footplanting=True`` while running at FULL SPEED in CHASE.  The
    ``pre_tackle_stance`` / ``footplanting`` flags are deliberately NOT used:
    they are overloaded by the flat-foot writer.
    """
    if defender is None:
        return False
    spd = math.hypot(
        float(getattr(defender, "vx", 0.0) or 0.0),
        float(getattr(defender, "vy", 0.0) or 0.0),
    )
    return spd < eps


def is_closing_posture(defender) -> bool:
    """True when *defender*'s MovementState is a forward-closing posture.

    CHASE / SHOOT are the only tackle-closing movement states.  RETREAT,
    DRIFT, SLIDE and HOLD-with-velocity are not valid engage postures.
    """
    state = getattr(defender, "movement_state", None)
    return state in (MovementState.CHASE, MovementState.SHOOT)


# ── Approach angle from the FACING / geometry plane (Finding 5) ────────
def approach_angle_from_facing(defender, bc) -> float:
    """Return the approach angle (deg) between *defender*'s facing plane and
    the defender→BC line.

    Uses the tackler's FACING (the geometry plane it presents to the BC),
    NOT its instantaneous velocity direction.  The velocity direction can
    point along a stale / smoothed / lateral vector for a SLIDE or arcing
    CHASE and mis-bracket the angle.
    """
    to_bc = math.atan2(float(bc.y) - float(defender.y),
                       float(bc.x) - float(defender.x))
    facing = float(getattr(defender, "facing", 0.0) or 0.0)
    delta = abs(to_bc - facing)
    delta = min(delta, math.pi * 2 - delta)
    return math.degrees(delta)


# ── Whole-classifier entry point ─────────────────────────────────────────
def classify_pretackle(world, defender, bc, tuners) -> bool:
    """Return True when TES should start for (*defender*, *bc*).

    MovementState-driven pre-tackle classifier replacing the binary gate's
    distance fast-paths.  This is the single engage decision; the callers
    call it exactly as they called ``try_pre_tackle``.
    """
    # ── 0. Ownership / presence / TES-drive suppress (unchanged) ──
    if getattr(world, "ball_owner", None) != "BC":
        return False
    if bc is None:
        return False
    ope = getattr(world, "open_play_engine", None)
    if ope is not None and getattr(ope, "_suppress_pre_tackle", False):
        return False

    # ── 1. Invalid candidates: frozen / marker / DH never initiate ──
    if getattr(defender, "frozen", False):
        return False
    if float(getattr(defender, "freeze_timer", 0.0) or 0.0) > 0.0:
        return False
    from player.player import DefenderRole
    _role = getattr(defender, "defender_role", None)
    if _role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B,
                 DefenderRole.DUMMY_HALF):
        return False

    # ── RADIAL+FACING TACKLE ENGAGE GUARD (2026-09-10) ──
    # Computed ONCE, before the role gate, exactly as the machine does it.
    # ORDER IS LOAD-BEARING: ``is_engage_role`` admits only
    # ``tackle_role in ("PRIMARY", "SECONDARY")`` and a DEF_LINE_READY line
    # body carries ``None``, so a role gate evaluated first rejected every
    # radial-in, facing line body BEFORE the engage window was ever examined
    # — the FINAL chokepoint of "no tackles ever".  The guard is therefore the
    # ADMISSION TICKET: when it fires the body is admitted and stamped with
    # the TES engage role, and the lane-Y slot veto is bypassed for it.
    from game.defence.defence_lvl_1.tackle_engage_guard import (
        is_tackle_engage_guard as _is_engage_guard,
        stamp_tes_tackler_role as _stamp_tes_tackler_role,
    )
    guarded = _is_engage_guard(defender, bc, world, tuners)
    if guarded:
        _stamp_tes_tackler_role(defender)

    # ── 2. Role eligibility (shared predicate; THIRD excluded) ──
    if not (is_engage_role(defender) or guarded):
        return False

    # ── 3. LANE-SLOT ALIGNMENT — hard engage conjunct (Finding 1) ──
    # Bypassed for a guarded body (radial-in AND facing the carrier): that is
    # a live, physical tackle attempt, so the lane-Y veto must not starve it.
    if not (is_slot_tackle_eligible(bc, defender, world) or guarded):
        return False

    dx = float(bc.x) - float(defender.x)
    dy = float(bc.y) - float(defender.y)
    dist = math.hypot(dx, dy)

    # ── 4. Distance window — NECESSARY, not sufficient (no fast-path) ──
    if dist > ENGAGE_DISTANCE_M:
        return False

    # ── 5. POSTURE — MovementState-driven (Findings 2 + 3) ──
    planted = is_real_planted_posture(defender)
    closing = is_closing_posture(defender)
    if not (planted or closing):
        return False

    # ── 6. Onside compliance (retreat-line) — keep, planted/commit exempt ──
    from world.world_state import is_tackle_phase as _is_tackle_phase
    if not _is_tackle_phase(world):
        ruck_x = getattr(world, "ruck_x_m", 60.0)
        compliance_dist = float(
            getattr(tuners, "retreat_compliance_distance_m", 10.0) or 10.0
        )
        committed_lane_tackler = bool(
            getattr(defender, "_tackle_commit_active", False)
        )
        if (float(defender.x) < float(ruck_x) + compliance_dist
                and not committed_lane_tackler
                and not planted):
            return False

    # ── 7. Approach angle from FACING (Finding 5) ──
    angle = approach_angle_from_facing(defender, bc)
    if angle >= MAX_APPROACH_ANGLE_DEG:
        return False

    # ── 8. Closing-speed / planted gate (Finding 6) ──
    #    For a genuine stationary square-up the BC runs INTO the tackler, so
    #    a ~0 closing speed is legitimate; for a mover it must close.
    if dist > 0.01:
        to_bc_dx = dx / dist
        to_bc_dy = dy / dist
    else:
        to_bc_dx, to_bc_dy = 1.0, 0.0
    closing_speed = (
        to_bc_dx * float(getattr(defender, "vx", 0.0) or 0.0)
        + to_bc_dy * float(getattr(defender, "vy", 0.0) or 0.0)
    )
    if closing_speed <= 0.0 and not planted:
        return False

    # ── 9. EVASION STACK (no distance-only fast-path) ──
    #    The original Gate 4's planted/commit fast-path is REPLACED by the
    #    posture split below: a genuinely stationary square-up skips the
    #    closing-vector checks (outrun / overshoot / momentum), which are
    #    meaningless for a zero-velocity tackler the runner will crash into.
    #    A moving CHASE (incl. the flat-foot step-in) MUST satisfy all of
    #    them, so it can never auto-engage on distance alone.
    def_spd = math.hypot(
        float(getattr(defender, "vx", 0.0) or 0.0),
        float(getattr(defender, "vy", 0.0) or 0.0),
    )
    bc_spd = math.hypot(
        float(getattr(bc, "vx", 0.0) or 0.0),
        float(getattr(bc, "vy", 0.0) or 0.0),
    )

    if not planted:
        # BC outruns / defender overshoot (closing-vector checks).
        if bc_spd > def_spd + BC_OUTRUN_MARGIN_MPS:
            return False
        if def_spd > bc_spd + DEF_OVERSHOOT_MARGIN_MPS:
            return False

    # Angle-quality slip bands (rebased on the FACING angle).
    if angle < HEAD_ON_MAX_DEG:
        pass  # head-on — no penalty
    elif angle < SIDE_ON_MAX_DEG:
        rng = _resolve_rng(tuners)
        slip_chance = 0.50 - getattr(defender, "tackle_skill", 0.5) * 0.15
        if rng.random() < slip_chance:
            return False
    else:
        rng = _resolve_rng(tuners)
        miss_chance = 0.75 - getattr(defender, "tackle_skill", 0.5) * 0.20
        if rng.random() < miss_chance:
            return False

    # Momentum comparison (skipped for a genuinely stationary square-up —
    # a zero-velocity tackler has no momentum to compare; the runner crashes
    # into it instead of driving through a closing body).
    if not planted:
        bc_momentum = float(getattr(bc, "mass", 90.0) or 90.0) * bc_spd
        def_momentum = float(getattr(defender, "mass", 90.0) or 90.0) * def_spd
        momentum_ratio = def_momentum / max(0.1, bc_momentum)
        if momentum_ratio < MOMENTUM_RATIO_FLOOR:
            return False

    # Base random failure (5% — design intent, tunable not hard-coded).
    rng = _resolve_rng(tuners)
    if rng.random() < BASE_RANDOM_FAIL:
        return False

    # ── 10. BC wrong-foot (per-frame baseline, Finding 4) ──
    bc_facing = float(getattr(bc, "facing", 0.0) or 0.0)
    bc_prev_facing = float(
        getattr(bc, "prev_facing",
                getattr(bc, "_prev_facing", bc_facing))
        or bc_facing
    )
    facing_delta = abs(bc_facing - bc_prev_facing)
    facing_delta = min(facing_delta, math.pi * 2 - facing_delta)
    if facing_delta > WRONG_FOOT_FACING_DELTA:
        wrong_foot_chance = (
            WRONG_FOOT_BASE_CHANCE
            - getattr(defender, "tackle_skill", 0.5) * WRONG_FOOT_SKILL_SCALE
        )
        if rng.random() < wrong_foot_chance:
            return False

    # ── Terminal: advance the per-frame wrong-foot baseline (always) ──
    bc.prev_facing = bc_facing

    return True


def _resolve_rng(tuners):
    """Return the tuners' rng or a fresh random.Random fallback."""
    rng = getattr(tuners, "rng", None)
    if rng is not None:
        return rng
    import random
    return random.Random()


# ── Backwards-compatible alias so existing imports keep working ─────────
try_pre_tackle_classify = classify_pretackle
