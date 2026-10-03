"""pretackle_machine.py — the 12-state pre-TES physics machine.

PURPOSE
-------
A unified pre-TES physics block that runs AFTER anticipation and jitter
removal (the Stage-9 lean and the finalise/target-stabiliser pass) and
replaces the binary ``try_pre_tackle`` engage decision with a single
MovementState-driven posture classification.  The machine reads ONE
posture per (defender, BC) pair, resolves it into exactly one of 12
states, and maps that state onto one of 5 outcomes:

    BOUNCE_BACK      — defender recoils off the contact.
    FALL_OVER        — defender overcommits / rear-angle / forced step-in.
    STUNNED_UPRIGHT  — defender is wrong-footed but stays on its feet.
    WRONG_FOOT       — defender is laterally misaligned / wrong posture.
    CLEAN_ENGAGE     — the ONLY outcome that feeds TES.

TES is fed ONLY when the four conjuncts below ALL resolve into a valid
engage window:

    1. CONTACT GEOMETRY  — lane-slot alignment (``is_slot_tackle_eligible``)
                           + distance window + approach angle from the
                           tackler's FACING plane (Finding 5 — not velocity).
    2. CLOSING POSTURE   — MovementState is CHASE/SHOOT, OR a genuine
                           stationary square-up (Finding 3 — MovementState
                           is the posture source of truth).
    3. PLANTED PREDICATE — a genuine stationary square-up: zero velocity +
                           HOLD + ``_mmdl_moved``.  The overloaded
                           ``pre_tackle_stance`` / ``footplanting`` flags are
                           NEVER trusted (Finding 2 — ``_apply_flatfoot_step_in``
                           stamps them on a full-speed CHASE).
    4. WRONG-FOOT DELTA  — the per-frame ``bc.prev_facing`` derivative
                           (Finding 4 — NOT a "since the last tackle"
                           snapshot).

RELATION TO ``pretackle_classifier``
-----------------------------------
``pretackle_classifier.classify_pretackle`` is the LEGACY boolean version
of this decision.  It is retained verbatim so ``verify_pretackle_classifier``
keeps passing and so the two views can be diffed.  The machine imports its
shared role-eligibility predicate (``is_engage_role``) from that module so
the caller's candidate set and the machine's role filter can never drift
(Finding 7).  ``try_pre_tackle`` now delegates to ``should_engage``.

WRONG-FOOT BASELINE
-------------------
The machine reads ``bc.prev_facing`` at entry (falling back to the current
facing on the first frame) and writes it back to ``bc.prev_facing`` on
EVERY call — before any early-return — so the facing delta is a per-frame
derivative rather than a stale snapshot.
"""

from __future__ import annotations

import math
from enum import Enum

from player.movement_states import MovementState
from player.player import DefenderRole
from game.defence.defence_lvl_1.slot_arbitration import (
    is_slot_tackle_eligible,
)
from game.defence.defence_lvl_1.pretackle_classifier import (
    is_engage_role,
)
from game.defence.defence_lvl_1.pre_tackle_authority import (
    PreTackleState,
)
# ── RETREAT-OBLIGATION VETO (2026-09-17) ─────────────────────────────
# THE 10 m retreat contract.  The machine is the LIVE engage gate
# (`try_pre_tackle` delegates here via run_machine + should_engage), so a
# non-marker defender that has not completed its retreat must not be a
# tackle candidate at all.  The veto lands in `_invalid_candidate`, which
# every classification path passes through first.
from game.defence.defence_lvl_1.retreat_obligation import (
    is_retreat_obligated,
)


# ── Tunable constants (defaults mirror the legacy gate) ───────────────
ENGAGE_DISTANCE_M: float = 2.0
HEAD_ON_MAX_DEG: float = 30.0
SIDE_ON_MAX_DEG: float = 60.0
MAX_APPROACH_ANGLE_DEG: float = 120.0
PLANTED_VELOCITY_EPS: float = 0.01
WRONG_FOOT_FACING_DELTA: float = 0.3   # radians (~17 deg)
WRONG_FOOT_BASE_CHANCE: float = 0.4
WRONG_FOOT_SKILL_SCALE: float = 0.15
BASE_RANDOM_FAIL: float = 0.05
BOUNCE_BACK_PUSH_M: float = 0.25
FALL_OVER_STUN_S: float = 0.5
STUNNED_UPRIGHT_STUN_S: float = 0.2
WRONG_FOOT_RECOIL_M: float = 0.15
BC_OUTRUN_MARGIN_MPS: float = 3.0
DEF_OVERSHOOT_MARGIN_MPS: float = 2.0
MOMENTUM_RATIO_FLOOR: float = 0.5


# ── 12 MACHINE STATES ─────────────────────────────────────────────────
class PretackleMachineState(Enum):
    """The 12 pre-tackle physics states.

    Partitioned on posture (MovementState read) x contact geometry (slot /
    angle band) x planted / wrong-foot:

      STATIONARY (HOLD):
        SQUARE_UP_ENGAGE        head-on + slot-in + genuinely planted
                                + clean BC      -> CLEAN_ENGAGE
        SQUARE_UP_OFF_SLOT      slot-OUT (or poor angle) -> WRONG_FOOT
        SQUARE_UP_WRONG_FOOTED  wrong-footed BC -> STUNNED_UPRIGHT

      CLOSING (CHASE/SHOOT):
        CLOSING_HEAD_ON         head-on + slot-in + clean -> CLEAN_ENGAGE
        CLOSING_SIDE_ON         side-on angle -> BOUNCE_BACK
        CLOSING_REAR            rear angle -> FALL_OVER
        CLOSING_OFF_SLOT        slot-OUT -> WRONG_FOOT
        CLOSING_WRONG_FOOTED    wrong-footed BC -> STUNNED_UPRIGHT

      LATERAL (SLIDE/DRIFT):
        LATERAL_SLIDE           weak closing -> WRONG_FOOT

      RETREATING (RETREAT) / non-candidate:
        RETREATING              no tackle forms -> BOUNCE_BACK

      OVERLOADED FLAG (Finding 2):
        FLATFOOT_FORCED         pre_tackle_stance/footplanting set on a
                                full-speed, non-planted CHASE -> FALL_OVER

      AUTHORITY-OWNED (pre_tackle_state COMMIT/PLANTED):
        COMMIT_PLANTED          head-on + slot-in + clean -> CLEAN_ENGAGE
    """
    SQUARE_UP_ENGAGE = "SQUARE_UP_ENGAGE"
    SQUARE_UP_OFF_SLOT = "SQUARE_UP_OFF_SLOT"
    SQUARE_UP_WRONG_FOOTED = "SQUARE_UP_WRONG_FOOTED"
    CLOSING_HEAD_ON = "CLOSING_HEAD_ON"
    CLOSING_SIDE_ON = "CLOSING_SIDE_ON"
    CLOSING_REAR = "CLOSING_REAR"
    CLOSING_OFF_SLOT = "CLOSING_OFF_SLOT"
    CLOSING_WRONG_FOOTED = "CLOSING_WRONG_FOOTED"
    LATERAL_SLIDE = "LATERAL_SLIDE"
    RETREATING = "RETREATING"
    FLATFOOT_FORCED = "FLATFOOT_FORCED"
    COMMIT_PLANTED = "COMMIT_PLANTED"


# ── 5 OUTCOMES ────────────────────────────────────────────────────────
class PretackleOutcome(Enum):
    """The terminal outcome the machine produces for a (defender, BC)."""
    BOUNCE_BACK = "BOUNCE_BACK"
    FALL_OVER = "FALL_OVER"
    STUNNED_UPRIGHT = "STUNNED_UPRIGHT"
    WRONG_FOOT = "WRONG_FOOT"
    CLEAN_ENGAGE = "CLEAN_ENGAGE"


# ── State -> outcome mapping ──────────────────────────────────────────
OUTCOME_BY_STATE = {
    PretackleMachineState.SQUARE_UP_ENGAGE: PretackleOutcome.CLEAN_ENGAGE,
    PretackleMachineState.SQUARE_UP_OFF_SLOT: PretackleOutcome.WRONG_FOOT,
    PretackleMachineState.SQUARE_UP_WRONG_FOOTED: PretackleOutcome.STUNNED_UPRIGHT,
    PretackleMachineState.CLOSING_HEAD_ON: PretackleOutcome.CLEAN_ENGAGE,
    PretackleMachineState.CLOSING_SIDE_ON: PretackleOutcome.BOUNCE_BACK,
    PretackleMachineState.CLOSING_REAR: PretackleOutcome.FALL_OVER,
    PretackleMachineState.CLOSING_OFF_SLOT: PretackleOutcome.WRONG_FOOT,
    PretackleMachineState.CLOSING_WRONG_FOOTED: PretackleOutcome.STUNNED_UPRIGHT,
    PretackleMachineState.LATERAL_SLIDE: PretackleOutcome.WRONG_FOOT,
    PretackleMachineState.RETREATING: PretackleOutcome.BOUNCE_BACK,
    PretackleMachineState.FLATFOOT_FORCED: PretackleOutcome.FALL_OVER,
    PretackleMachineState.COMMIT_PLANTED: PretackleOutcome.CLEAN_ENGAGE,
}

# The states that may feed TES (the ONLY clean-engage states).
ENGAGE_STATES = frozenset({
    PretackleMachineState.SQUARE_UP_ENGAGE,
    PretackleMachineState.CLOSING_HEAD_ON,
    PretackleMachineState.COMMIT_PLANTED,
})
# ─────────────────────────────────────────────────────────────────────
# TUNABLE ACCESSORS
# ─────────────────────────────────────────────────────────────────────

def _tun(tuners, name, default):
    """Read *tuners* attribute *name* (falling back to *default*)."""
    return getattr(tuners, name, default)


def machine_enabled(tuners) -> bool:
    """Master gate for the machine (classification always runs)."""
    return bool(_tun(tuners, "pretackle_machine_enabled", True))


def engage_distance_m(tuners) -> float:
    return float(_tun(tuners, "machine_engage_distance_m", ENGAGE_DISTANCE_M))


def head_on_max_deg(tuners) -> float:
    return float(_tun(tuners, "machine_head_on_max_deg", HEAD_ON_MAX_DEG))


def side_on_max_deg(tuners) -> float:
    return float(_tun(tuners, "machine_side_on_max_deg", SIDE_ON_MAX_DEG))


def max_approach_angle_deg(tuners) -> float:
    return float(_tun(tuners, "machine_max_approach_angle_deg", MAX_APPROACH_ANGLE_DEG))


def planted_velocity_eps(tuners) -> float:
    return float(_tun(tuners, "machine_planted_velocity_eps", PLANTED_VELOCITY_EPS))


def wrong_foot_facing_delta(tuners) -> float:
    return float(_tun(tuners, "machine_wrong_foot_facing_delta", WRONG_FOOT_FACING_DELTA))


def wrong_foot_base_chance(tuners) -> float:
    return float(_tun(tuners, "machine_wrong_foot_base_chance", WRONG_FOOT_BASE_CHANCE))


def wrong_foot_skill_scale(tuners) -> float:
    return float(_tun(tuners, "machine_wrong_foot_skill_scale", WRONG_FOOT_SKILL_SCALE))


def base_random_fail(tuners) -> float:
    return float(_tun(tuners, "machine_base_random_fail", BASE_RANDOM_FAIL))


def _resolve_rng(tuners):
    """Return the tuners' rng or a fresh random.Random fallback."""
    rng = getattr(tuners, "rng", None)
    if rng is not None:
        return rng
    import random
    return random.Random()


# ─────────────────────────────────────────────────────────────────────
# PREDICATES
# ─────────────────────────────────────────────────────────────────────

def is_real_planted_posture(defender, eps: float | None = None) -> bool:
    """True when *defender* is a GENUINE stationary square-up.

    REAL PLANTED = zero total velocity AND MovementState == HOLD AND
    ``_mmdl_moved`` is True (the movement layer consumed it).  This
    distinguishes a genuine planted stance from the OVERLOADED flag: a
    flat-foot forced step-in (``_apply_flatfoot_step_in`` / ``apply_pre_tackle_stance``
    stamp ``pre_tackle_stance=True`` / ``footplanting=True`` on a full-speed,
    non-frozen CHASE.  The flags are deliberately NOT used here (Finding 2).
    """
    if defender is None:
        return False
    if getattr(defender, "movement_state", None) is not MovementState.HOLD:
        return False
    if not bool(getattr(defender, "_mmdl_moved", False)):
        return False
    if eps is None:
        eps = PLANTED_VELOCITY_EPS
    spd = math.hypot(
        float(getattr(defender, "vx", 0.0) or 0.0),
        float(getattr(defender, "vy", 0.0) or 0.0),
    )
    return spd < eps


def is_closing_posture(defender) -> bool:
    """True when *defender*'s MovementState is a forward-closing posture.

    CHASE / SHOOT are the only tackle-closing movement states.  RETREAT,
    DRIFT, SLIDE and HOLD-with-velocity are not valid engage postures
    (Finding 3 — MovementState is the posture source of truth).
    """
    state = getattr(defender, "movement_state", None)
    return state in (MovementState.CHASE, MovementState.SHOOT)


def approach_angle_from_facing(defender, bc, world=None, tuners=None) -> float:
    """Return the approach angle (deg) between *defender*'s facing plane and
    the defender->BC line.

    Uses the tackler's FACING (the geometry plane it presents to the BC),
    NOT its instantaneous velocity direction (Finding 5).  The velocity
    direction can point along a stale / smoothed / lateral vector for a
    SLIDE or arcing CHASE and mis-bracket the angle.

    ANTICIPATION-ALIGNED REFERENCE (2026-09-09): while the defender is under
    the anticipation authority window, the line LEANS toward the predicted
    threat lane and the facing is locked to that predicted point.  Measure
    the to-BC ray against the SAME predicted Y (``anticipation_target_y``)
    so the angle band through the lean is stable frame-to-frame — the live
    carrier's lateral jink would otherwise move the reference and wobble the
    head-on/side/rear classification.  Falls back to the live BC when no
    anticipation reference is present.
    """
    if bc is None or defender is None:
        return 180.0
    ref_x = float(getattr(bc, "x", 0.0) or 0.0)
    ref_y = float(getattr(bc, "y", 0.0) or 0.0)
    if world is not None:
        try:
            from game.defence.defence_lvl_1.anticipation_layer import (
                anticipation_facing_y as _afy,
            )
            _ref_y = _afy(world, defender)
            if _ref_y is not None:
                ref_y = float(_ref_y)
        except Exception:
            pass
    to_bc = math.atan2(ref_y - float(defender.y),
                       ref_x - float(defender.x))
    facing = float(getattr(defender, "facing", 0.0) or 0.0)
    delta = abs(to_bc - facing)
    delta = min(delta, math.pi * 2 - delta)
    return math.degrees(delta)


def wrong_foot_delta(bc, tuners=None) -> float:
    """Return the per-frame BC wrong-foot delta (radians), advancing the
    baseline.  ``bc.prev_facing`` is always written (Finding 4).

    Falls back to the FIRST-frame behaviour (prev == current) when the BC
    carries no baseline yet.
    """
    if bc is None:
        return 0.0
    bc_facing = float(getattr(bc, "facing", 0.0) or 0.0)
    bc_prev = getattr(bc, "prev_facing", None)
    if bc_prev is None:
        bc_prev = getattr(bc, "_prev_facing", None)
    if bc_prev is None:
        bc_prev = bc_facing
    bc_prev = float(bc_prev if bc_prev is not None else bc_facing)
    delta = abs(bc_facing - bc_prev)
    delta = min(delta, math.pi * 2 - delta)
    # Advance the per-frame baseline on EVERY call (before any return).
    bc.prev_facing = bc_facing
    return delta


# ─────────────────────────────────────────────────────────────────────
# POSTURE READ — the single MovementState-driven posture read
# ─────────────────────────────────────────────────────────────────────

def read_posture(defender, tuners=None):
    """Return ``(posture_class, planted, closing)`` from ONE posture read.

    posture_class is one of:
      "STATIONARY"  — HOLD (genuine square-up candidate)
      "CLOSING"     — CHASE / SHOOT
      "LATERAL"     — SLIDE / DRIFT
      "RETREATING"  — RETREAT (or unknown / non-engage)
    ``planted`` is the genuine-zero-velocity square-up predicate; ``closing``
    is the CHASE/SHOOT predicate.
    """
    state = getattr(defender, "movement_state", None)
    planted = is_real_planted_posture(
        defender, eps=planted_velocity_eps(tuners) if tuners is not None else None,
    )
    closing = is_closing_posture(defender)
    if state is MovementState.HOLD:
        posture = "STATIONARY"
    elif closing:
        posture = "CLOSING"
    elif state in (MovementState.SLIDE, MovementState.DRIFT):
        posture = "LATERAL"
    else:
        posture = "RETREATING"
    return posture, planted, closing


def outcome_for_state(state: PretackleMachineState) -> PretackleOutcome:
    """Return the outcome mapped to *state*."""
    return OUTCOME_BY_STATE.get(state, PretackleOutcome.BOUNCE_BACK)


def is_engage_state(state: PretackleMachineState) -> bool:
    """True when *state* may feed TES."""
    return state in ENGAGE_STATES
# ─────────────────────────────────────────────────────────────────────
# STATE MACHINE — classify one (defender, BC) into exactly one state
# ─────────────────────────────────────────────────────────────────────

def _invalid_candidate(world, defender, bc) -> str | None:
    """Return a reason string when *defender* cannot be a tackle candidate,
    else None (the candidate passes the pre-condition gate).

    Mirrors the legacy classifier's pre-condition checks: ownership, BC
    presence, TES-drive suppress, frozen / freeze-timer, marker / DH role.

    RETREAT-OBLIGATION VETO (2026-09-17): the 10 m retreat contract is a
    pre-condition of being a tackle candidate at all.  It is checked here
    rather than in ``should_engage``'s conjunct list so it covers EVERY
    path through the machine — ``run_machine``'s classification, the
    ``COMMIT_PLANTED`` authority branch, the radial+facing engage guard
    bypass, and ``should_engage``'s geometry/posture conjunts.  The
    legacy ``pretackle_classifier`` carried this check as its step 6, but
    that function is dead on the live path (``try_pre_tackle`` delegates
    to this machine), and even there it was skipped under
    ``is_tackle_phase`` and bypassed for planted / committed bodies —
    which is precisely how the obligation came to be unenforced.
    """
    if getattr(world, "ball_owner", None) != "BC":
        return "not_bc_owner"
    if bc is None:
        return "no_bc"
    ope = getattr(world, "open_play_engine", None)
    if ope is not None and getattr(ope, "_suppress_pre_tackle", False):
        return "suppressed"
    if getattr(defender, "frozen", False):
        return "frozen"
    if float(getattr(defender, "freeze_timer", 0.0) or 0.0) > 0.0:
        return "freeze_timer"
    _role = getattr(defender, "defender_role", None)
    if _role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B,
                 DefenderRole.DUMMY_HALF):
        return "marker_or_dh"
    # ── THE 10 m RETREAT OBLIGATION ──
    # Non-marker defender still short of its retreat line: not a candidate,
    # so the machine classifies RETREATING (a non-engage state) and no
    # tackle forms.  Markers / DH / fullback are exempt by construction
    # inside the predicate itself.
    if is_retreat_obligated(defender, world):
        return "retreat_obligated"
    return None


def run_machine(world, defender, bc, tuners) -> PretackleMachineState:
    """Resolve *defender* vs *bc* into exactly one of the 12 states.

    Reads the single MovementState-driven posture, then evaluates contact
    geometry (slot + facing angle band), the planted predicate, and the
    wrong-foot delta, and returns the ONE state that classifies the pair.
    This is the machine's primary classification entry point.

    Notes:
      - The wrong-foot baseline is advanced on EVERY call (in
        ``wrong_foot_delta``), so the delta here is always a per-frame
        derivative — a stale "since the last tackle" snapshot is impossible.
      - Pre-condition failures (frozen / marker / wrong role / no BC /
        suppress) resolve to ``RETREATING`` (a non-engage state), so the
        machine is total: it always returns exactly one of the 12 states.
    """
    if not machine_enabled(tuners):
        # Disabled -> never feed TES; classify the conservative non-engage.
        return PretackleMachineState.RETREATING

    # Pre-condition gate.
    if _invalid_candidate(world, defender, bc) is not None:
        return PretackleMachineState.RETREATING

    # Single MovementState-driven posture read.
    posture, planted, closing = read_posture(defender, tuners)

    # Per-frame wrong-foot delta (advances the baseline internally).
    wf_delta = wrong_foot_delta(bc, tuners)
    wf_threshold = wrong_foot_facing_delta(tuners)
    wf = wf_delta > wf_threshold

    # ── RADIAL+FACING TACKLE ENGAGE GUARD (2026-09-10) ──
    # The slot-Y gate (``|bc.y - defender.y| < tackle_slot_threshold_m``,
    # default 0.3 m) vetoed EVERY engage window whenever the carrier's lane Y
    # sat off the defender's live Y: ``try_pre_tackle`` returned False
    # forever and no tackle ever formed ("no tackles happen ever").
    # A GENUINE LINE DEFENDER that is radial-in AND FACING the carrier has a
    # live, physical tackle attempt.  For those bodies the guard's own
    # predicate IS the contact-geometry criterion, so the slot gate is
    # bypassed and the pair classifies straight to the clean-engage state.
    #
    # ORDER IS LOAD-BEARING: this block runs BEFORE the ``is_engage_role``
    # gate below.  That shared predicate admits ONLY
    # ``tackle_role in ("PRIMARY", "SECONDARY")`` and a DEF_LINE_READY body
    # carries ``None``, so the role gate was rejecting every radial+facing
    # line body BEFORE the engage window was ever evaluated — the FINAL
    # chokepoint of "no tackles ever".  The guard therefore stamps the TES
    # engage ROLE itself (what ``pre_tackle_authority`` does for its COMMIT
    # owner) and then returns the clean-engage state directly.
    from game.defence.defence_lvl_1.tackle_engage_guard import (
        is_tackle_engage_guard as _is_engage_guard,
        stamp_tackle_engage_guard as _stamp_guard,
        stamp_tes_tackler_role as _stamp_tes_role,
    )
    guard = _is_engage_guard(defender, bc, world, tuners)
    if guard:
        _stamp_tes_role(defender)
        if closing or planted:
            _stamp_guard(defender, world=world)
            if posture == "STATIONARY" and planted:
                return PretackleMachineState.SQUARE_UP_ENGAGE
            return PretackleMachineState.CLOSING_HEAD_ON

    # Shared role-eligibility predicate (Finding 7 — THIRD is joiner-only).
    if not is_engage_role(defender):
        return PretackleMachineState.RETREATING

    # Contact geometry: slot-in (lateral lane alignment).
    slot_in = is_slot_tackle_eligible(bc, defender, world)

    # Contact geometry: approach angle from the FACING plane (Finding 5).
    # World is passed so an anticipation-leaning defender measures the
    # angle against the SAME predicted lane it faces (angle lock).
    angle = approach_angle_from_facing(defender, bc, world=world, tuners=tuners)
    head_max = head_on_max_deg(tuners)
    side_max = side_on_max_deg(tuners)

    # Authority-owned COMMIT / PLANTED body (pre_tackle_authority).
    authority_state = getattr(defender, "pre_tackle_state", None)
    authority_commit = (
        authority_state in (PreTackleState.COMMIT, PreTackleState.PLANTED)
    )

    # OVERLOADED FLAG (Finding 2): `pre_tackle_stance` / `footplanting` set
    # on a full-speed, NON-planted CHASE is a forced flat-foot step-in —
    # the opposite of a genuine planted stance.  Detect and classify it
    # separately.
    overloaded_stance = (
        not planted
        and closing
        and (
            bool(getattr(defender, "pre_tackle_stance", False))
            or bool(getattr(defender, "footplanting", False))
        )
    )

    # ── Classify ──
    if authority_commit:
        # Authority-owned body: engage only when geometry is clean.
        if slot_in and angle < head_max and not wf:
            return PretackleMachineState.COMMIT_PLANTED
        if not slot_in:
            return PretackleMachineState.CLOSING_OFF_SLOT
        if wf:
            return PretackleMachineState.CLOSING_WRONG_FOOTED
        if angle < side_max:
            return PretackleMachineState.CLOSING_SIDE_ON
        return PretackleMachineState.CLOSING_REAR

    if overloaded_stance:
        return PretackleMachineState.FLATFOOT_FORCED

    if posture == "STATIONARY":
        if not slot_in:
            return PretackleMachineState.SQUARE_UP_OFF_SLOT
        if wf:
            return PretackleMachineState.SQUARE_UP_WRONG_FOOTED
        if angle < head_max:
            return PretackleMachineState.SQUARE_UP_ENGAGE
        # Stationary but the BC is at a poor angle relative to the facing
        # plane -> cannot clean-engage from that posture.
        return PretackleMachineState.SQUARE_UP_OFF_SLOT

    if posture == "CLOSING":
        if not slot_in:
            return PretackleMachineState.CLOSING_OFF_SLOT
        if wf:
            return PretackleMachineState.CLOSING_WRONG_FOOTED
        if angle < head_max:
            return PretackleMachineState.CLOSING_HEAD_ON
        if angle < side_max:
            return PretackleMachineState.CLOSING_SIDE_ON
        return PretackleMachineState.CLOSING_REAR

    if posture == "LATERAL":
        return PretackleMachineState.LATERAL_SLIDE

    # RETREATING (or unknown posture) — no tackle forms.
    return PretackleMachineState.RETREATING
# ─────────────────────────────────────────────────────────────────────
# ENGAGE WINDOW — the four conjuncts must ALL resolve
# ─────────────────────────────────────────────────────────────────────

def should_engage(world, defender, bc, tuners,
                  state: PretackleMachineState | None = None) -> bool:
    """True only when TES should be fed for (*defender*, *bc*).

    This is the strict engage gate.  It returns True ONLY when ALL FOUR
    conjuncts resolve into a valid engage window:

      1. CONTACT GEOMETRY — ``is_slot_tackle_eligible`` AND distance <=
         ``machine_engage_distance_m`` AND approach angle (from the FACING
         plane) < ``machine_max_approach_angle_deg``.
      2. CLOSING POSTURE  — MovementState is CHASE/SHOOT (``is_closing_posture``),
         OR a genuine stationary square-up (``is_real_planted_posture``).
      3. PLANTED PREDICATE — for a SQUARE_UP_ENGAGE the body must satisfy
         the genuine-planted predicate (zero velocity + HOLD + ``_mmdl_moved``).
         A CLOSING state does not require a planted body (it is closing).
      4. WRONG-FOOT DELTA — the per-frame ``bc.prev_facing`` derivative must
         be <= ``machine_wrong_foot_facing_delta``.

    If *state* is omitted the machine classifies it (``run_machine``).
    The wrong-foot baseline is advanced on every call regardless of the
    return value (per-frame derivative, Finding 4).
    """
    if not machine_enabled(tuners):
        return False
    if state is None:
        state = run_machine(world, defender, bc, tuners)

    # Only the clean-engage states may feed TES.
    if state not in ENGAGE_STATES:
        return False

    # ── RADIAL+FACING TACKLE ENGAGE GUARD (2026-09-10) ──
    # Radial-in AND facing the carrier bypasses the SLOT-Y gate: see
    # ``tackle_engage_guard``.  Without this the strict engage window could
    # never resolve for a genuine line defender whose lane Y sat more than
    # 0.3 m off the carrier, so no tackle ever formed.
    from game.defence.defence_lvl_1.tackle_engage_guard import (
        is_tackle_engage_guard as _is_engage_guard,
    )
    guard = _is_engage_guard(defender, bc, world, tuners)

    # ── 1. CONTACT GEOMETRY ──
    if not (is_slot_tackle_eligible(bc, defender, world) or guard):
        return False
    dist = math.hypot(
        float(getattr(defender, "x", 0.0) or 0.0) - float(getattr(bc, "x", 0.0) or 0.0),
        float(getattr(defender, "y", 0.0) or 0.0) - float(getattr(bc, "y", 0.0) or 0.0),
    )
    if dist > engage_distance_m(tuners):
        return False
    angle = approach_angle_from_facing(defender, bc, world=world, tuners=tuners)
    if angle >= max_approach_angle_deg(tuners):
        return False

    # ── 2. CLOSING POSTURE ──
    planted = is_real_planted_posture(
        defender, eps=planted_velocity_eps(tuners),
    )
    closing = is_closing_posture(defender)
    if not (planted or closing):
        return False

    # ── 3. PLANTED PREDICATE ──
    # A SQUARE_UP_ENGAGE is a stationary square-up; it MUST be genuinely
    # planted.  A CLOSING_HEAD_ON / COMMIT_PLANTED is a moving closer and
    # satisfies the planted conjunct vacuously (closing is a valid posture).
    if state is PretackleMachineState.SQUARE_UP_ENGAGE and not planted:
        return False

    # ── 4. WRONG-FOOT DELTA ──
    # Reuse the delta ``run_machine`` already computed (and advanced the
    # baseline with).  If ``run_machine`` was NOT called first (should_engage
    # used standalone with an explicit state), compute it here — the baseline
    # advances exactly once per frame either way.
    wf_delta = getattr(bc, "_pretackle_wrong_foot_delta", None)
    if wf_delta is None:
        wf_delta = wrong_foot_delta(bc, tuners)
    # A guarded (radial-in + facing) body is a FORCED engage: the wrong-foot
    # delta may not cancel it, or the guard would be defeated by exactly the
    # carrier lateral jink that made the slot gate fail in the first place.
    if not guard and wf_delta > wrong_foot_facing_delta(tuners):
        return False

    return True


# ─────────────────────────────────────────────────────────────────────
# OUTCOME PHYSICS — apply the selected outcome to the defender
# ─────────────────────────────────────────────────────────────────────

def apply_outcome(world, defender, bc, tuners,
                  state: PretackleMachineState | None = None
                  ) -> PretackleOutcome:
    """Apply the physical effect for the (defender, BC) pair's outcome.

    Returns the outcome.  For non-engage outcomes the defender's posture
    is mutated so the machine *produces* the outcome, not just labels it:

      - BOUNCE_BACK      — push the defender backward along its facing,
                           zero forward velocity (recoil off the contact).
      - FALL_OVER        — zero velocity + brief stun (the defender
                           overcommitted / hit from a bad angle and goes down).
      - STUNNED_UPRIGHT  — brief stun, zero velocity (wrong-footed but up).
      - WRONG_FOOT       — small lateral recoil + zero lateral velocity.
      - CLEAN_ENGAGE     — no-op (the caller starts TES; the defender is
                           handed to the tackle chamber as-is).

    Bounded and conservative so the outcome never teleports the defender.
    """
    if state is None:
        state = run_machine(world, defender, bc, tuners)
    outcome = outcome_for_state(state)
    if outcome is PretackleOutcome.CLEAN_ENGAGE or defender is None:
        return outcome

    facing = float(getattr(defender, "facing", 0.0) or 0.0)
    if outcome is PretackleOutcome.BOUNCE_BACK:
        push = float(_tun(tuners, "machine_bounce_back_push_m", BOUNCE_BACK_PUSH_M))
        # Backward = opposite the facing direction.
        defender.x = float(defender.x) - math.cos(facing) * push
        defender.y = float(defender.y) - math.sin(facing) * push
        defender.vx = 0.0
        defender.vy = 0.0
        defender.footplanting = False
        defender.pre_tackle_stance = False
        defender._machine_outcome = outcome.value
        defender._machine_state = state.value
        return outcome

    if outcome is PretackleOutcome.FALL_OVER:
        defender.vx = 0.0
        defender.vy = 0.0
        stun = float(_tun(tuners, "machine_fall_over_stun_s", FALL_OVER_STUN_S))
        if hasattr(defender, "freeze_timer"):
            defender.freeze_timer = max(
                float(getattr(defender, "freeze_timer", 0.0) or 0.0), stun,
            )
        defender.footplanting = False
        defender.pre_tackle_stance = False
        defender.step_stagger_active = True
        defender.step_stagger = 0.05
        defender._machine_outcome = outcome.value
        defender._machine_state = state.value
        return outcome

    if outcome is PretackleOutcome.STUNNED_UPRIGHT:
        defender.vx = 0.0
        defender.vy = 0.0
        stun = float(_tun(tuners, "machine_stunned_upright_stun_s", STUNNED_UPRIGHT_STUN_S))
        if hasattr(defender, "freeze_timer"):
            defender.freeze_timer = max(
                float(getattr(defender, "freeze_timer", 0.0) or 0.0), stun,
            )
        defender._machine_outcome = outcome.value
        defender._machine_state = state.value
        return outcome

    # WRONG_FOOT
    recoil = float(_tun(tuners, "machine_wrong_foot_recoil_m", WRONG_FOOT_RECOIL_M))
    side = -1.0 if float(getattr(defender, "y", 0.0)) < float(getattr(bc, "y", 0.0)) else 1.0
    defender.y = float(getattr(defender, "y", 0.0)) - side * recoil
    defender.vy = 0.0
    defender._machine_outcome = outcome.value
    defender._machine_state = state.value
    return outcome
