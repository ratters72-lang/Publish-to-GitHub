# game/tes/tes_dominance.py
import random
import math

from .tes_context import TESContext
from .tes_outcomes import TESOutcome
from .tes_state import TackleResult
from . import tes_multi_body as _multi_body

from player.movement_constants import (
    ANGLE_DELTA_MAX,
    VELOCITY_DELTA_MAX,
    EMA_SMOOTHING_FACTOR,
    MASTER_MOVEMENT_SCALER,
    COLLAPSE_THRESHOLD,
)


# ------------------------------------------------------------
# PHASE 6 — Continuous Tackle Score (Waveform)
# ------------------------------------------------------------

def update_waveform(ctx: TESContext, rng: random.Random) -> None:
    """TES PARTICIPANT FILTER APPLIED

    Continuous tackle score update during CONTACT/STRUGGLE.
    Runs every frame until TES enters DOMINANCE_SETTLING.

    Safely handles missing ctx.tackle_score by starting from
    ctx.initial_impact_score.

    ZERO-DELTA GUARD: If all computed deltas are zero for more than
    10 consecutive frames, inject small random noise to prevent
    waveform lockup.
    """
    # Get previous score (or start from initial impact if first frame)
    prev_score = getattr(ctx, "tackle_score", None)
    if prev_score is None:
        prev_score = ctx.initial_impact_score

    # ------------------------------------------------------------
    # Gather physics
    # ------------------------------------------------------------
    att = ctx.attacker
    deff = ctx.defender

    # Speeds
    att_spd = math.hypot(att.vx, att.vy)
    deff_spd = math.hypot(deff.vx, deff.vy)

    # Directions
    def_dir = _safe_norm(deff.vx, deff.vy, getattr(deff, 'facing', 0.0))
    att_dir = _safe_norm(att.vx, att.vy, getattr(att, 'facing', 0.0))

    # Angle delta
    dot = max(-1.0, min(1.0, def_dir[0] * att_dir[0] + def_dir[1] * att_dir[1]))
    angle = math.degrees(math.acos(dot))
    angle_delta = _compute_angle_delta(angle)

    # Velocity delta
    rel_speed = deff_spd - att_spd
    max_spd = max(att_spd, deff_spd, 0.1)
    vel_delta = _clamp(rel_speed * (VELOCITY_DELTA_MAX / max_spd), -VELOCITY_DELTA_MAX, VELOCITY_DELTA_MAX)

    # Stability delta (lateral jitter)
    att_lat = abs(att.vy)
    deff_lat = abs(deff.vy)
    stab_delta = _clamp((att_lat - deff_lat) * 0.02, -0.03, 0.03)

    # Twist delta (rotation difference)
    twist_delta = _clamp((att.rot - deff.rot) * 0.01, -0.01, 0.01)

    # Wrap delta (simple proximity + facing) + the COLLABORATIVE term.
    #
    # ``_multibody_wrap_delta`` is exactly 0.0 when the contest carried no
    # multi-body model, so a solo tackle's waveform is untouched; when the
    # declaration did carry one, the bodies wrapping the carrier add a
    # BOUNDED, always-positive term of the same order the legacy pair term
    # already uses.  It is folded in BEFORE the all-zero guard below, so a
    # genuine multi-body wrap can never be mistaken for a stuck waveform.
    wrap_delta = _compute_wrap_delta(att, deff) + _multibody_wrap_delta(ctx)

    # Random noise
    rand_delta = rng.uniform(-0.01, 0.01)

    # ------------------------------------------------------------
    # ZERO-DELTA GUARD (Patch 6)
    # Track consecutive frames where all computed deltas are zero.
    # If counter exceeds 10 frames, inject small random noise to
    # prevent waveform lockup.
    # ------------------------------------------------------------
    all_zero = (
        abs(angle_delta) < 0.0001 and
        abs(vel_delta) < 0.0001 and
        abs(stab_delta) < 0.0001 and
        abs(twist_delta) < 0.0001 and
        abs(wrap_delta) < 0.0001
    )
    zero_delta_counter = getattr(ctx, "_zero_delta_counter", 0)
    if all_zero:
        zero_delta_counter += 1
        if zero_delta_counter > 10:
            # Inject noise to break the lock
            rand_delta = rng.uniform(-0.015, 0.015)
            print(f"[WAVEFORM] Zero-delta guard fired at frame {zero_delta_counter}, "
                  f"injecting noise={rand_delta:.4f}")
    else:
        zero_delta_counter = 0
    ctx._zero_delta_counter = zero_delta_counter

    # ------------------------------------------------------------
    # Phase 2B — Stability update
    # ------------------------------------------------------------
    # Only decays during an active tackle engagement (started_at > 0).
    # During KO chase or any pre-tackle phase, stability stays at 1.0
    # to prevent micro-jitter before contact (Fix 3).
    if ctx.engagement.started_at > 0:
        # Each delta decays stability proportionally to its magnitude.
        # Good alignment (positive angle/wrap deltas) recovers it.
        decay = (abs(angle_delta) + abs(vel_delta) + abs(stab_delta) +
                 abs(twist_delta) + abs(wrap_delta)) * 0.5
        recovery = max(0.0, angle_delta + wrap_delta) * 2.0
        stability = getattr(ctx, "stability", 1.0)
        stability = stability - decay + recovery * (1.0 - stability)
        stability = max(0.0, min(1.0, stability))
        ctx.stability = stability
        if stability < COLLAPSE_THRESHOLD:
            ctx.collapsed = True

    # ------------------------------------------------------------
    # Master movement scaler (global weight for combined delta)
    # ------------------------------------------------------------
    master = MASTER_MOVEMENT_SCALER

    # ------------------------------------------------------------
    # Combine deltas
    # ------------------------------------------------------------
    raw_delta = (
        angle_delta +
        vel_delta +
        stab_delta +
        twist_delta +
        wrap_delta +
        rand_delta
    )

    score = prev_score + raw_delta * master

    # Clamp
    score = _clamp(score, 0.0, 3.0)

    # Smooth (EMA) — using imported smoothing factor
    score = score * EMA_SMOOTHING_FACTOR + prev_score * (1.0 - EMA_SMOOTHING_FACTOR)

    setattr(ctx, "tackle_score", score)


# ------------------------------------------------------------
# Helper functions
# ------------------------------------------------------------

def _safe_norm(vx, vy, facing_rad):
    mag = math.hypot(vx, vy)
    if mag > 0.001:
        return (vx / mag, vy / mag)
    return (math.cos(facing_rad), math.sin(facing_rad))


def _compute_angle_delta(angle_deg: float) -> float:
    """
    Positive delta if defender rotates into BC,
    negative if defender loses alignment.

    Capped by ANGLE_DELTA_MAX (0.018) to prevent
    unstable 90° snaps at high speed.
    """
    if angle_deg <= 30:
        return +ANGLE_DELTA_MAX
    elif angle_deg <= 60:
        return +ANGLE_DELTA_MAX
    elif angle_deg <= 120:
        return -ANGLE_DELTA_MAX * 0.5
    else:
        return -ANGLE_DELTA_MAX


def _compute_wrap_delta(att, deff):
    """
    Simple wrap heuristic:
    defender close + facing attacker → positive
    defender drifting → negative
    """
    dx = att.x_m - deff.x_m
    dy = att.y_m - deff.y_m
    dist = math.hypot(dx, dy)

    if dist < 1.0:
        return +0.01
    elif dist < 2.0:
        return +0.01
    else:
        return -0.01


def _wrap_model(ctx):
    """The contest's multi-body wrap model, or None.

    ONE reader for every multi-body consumer in this module, so the "no model"
    case — a solo tackle, an unresolvable declaration, or the stage's lever
    switched off — is handled in exactly one place.
    """
    return getattr(ctx, "wrap_model", None)


def _multibody_wrap_delta(ctx) -> float:
    """The COLLABORATIVE wrap term for the waveform.

    Exactly 0.0 with no model (or a model carrying no assist), so the legacy
    single-pair wrap heuristic stands alone and a solo tackle's waveform is
    bit-identical to the stage before this one.
    """
    return _multi_body.wrap_delta_for(_wrap_model(ctx))


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ------------------------------------------------------------
# RAW INPUT CONSTANTS — momentum normalisation factor
# ------------------------------------------------------------
MOMENTUM_NORM: float = 100.0  # bc_momentum ~540 → 5.4

# ------------------------------------------------------------
# PHASE 6 → PHASE 7 BRIDGE (Dominance + Drive + Duration)
# ------------------------------------------------------------

def compute_dominance(ctx: TESContext, outcome: TESOutcome, rng: random.Random) -> None:
    """TES PARTICIPANT FILTER APPLIED

    Compute attacker vs defender dominance using raw physics inputs.

    Attacker raw inputs::
        bc_momentum (mass × speed at contact), bc_speed, bc_angle_score
        (how directly forward the hit is), bc_balance (stability), fatigue.

    Defender raw inputs::
        tackler_momentum_sum (sum of all engaged tacklers' momentum),
        tackler_count, tackle_angle_avg (wrap quality), compression, fatigue.

    A fixed attacker_bias of 0.8 ensures the BC can win even with a single
    front‑on tackler.  No defender_bias is added.

    The raw dominance values (before clamping) are stored in
    outcome.attacker_dominance / outcome.defender_dominance for the
    caller to use in drive-distance computation.
    """
    att = ctx.attacker
    deff = ctx.defender

    # ── Attacker raw inputs ──────────────────────────────────────
    # ── THE CARRIER'S EFFECTIVE MOMENTUM (Stage 3.2) ──────────────
    # The carrier's ACTIVE KINETIC MOMENTUM SCORE is not ``mass x speed``: it
    # is ``mass x speed x fend_trait``, the fend multiplier read off the
    # declaration's own carrier slot through the multi-body model.  This is the
    # collision's own equation, and this is where it is checked against the
    # defenders' resistance — the raw scores below are banded at +/-1.0 into
    # WEAK / NEUTRAL / DOMINANT, so a harder-fending carrier needs more
    # defensive resistance to be held.
    #
    # The multiplier is EXACTLY 1.0 with no model (a solo tackle, an
    # unresolvable declaration, the stage's lever off) and exactly 1.0 for a
    # neutral record, so ``x * 1.0`` leaves the legacy momentum score
    # bit-identical in every frame that carried no non-neutral trait.
    bc_momentum = att.mass * math.hypot(att.vx, att.vy)
    bc_momentum *= _multi_body.carrier_momentum_multiplier(_wrap_model(ctx))
    bc_speed = math.hypot(att.vx, att.vy)
    bc_angle_score = max(-1.0, min(1.0, _approach_angle_score(ctx)))
    bc_balance = getattr(ctx, "stability", 1.0)
    bc_fatigue = getattr(ctx, "fatigue", 1.0)

    attacker_raw = (
        (bc_momentum / MOMENTUM_NORM) * 1.2
        + bc_speed * 1.0
        + bc_angle_score * 0.8
        + bc_balance * 0.6
        - bc_fatigue * 0.4
    )

    # ── Defender raw inputs ──────────────────────────────────────
    tackler_count = ctx.engagement.num_tacklers
    if tackler_count < 1:
        tackler_count = 1  # at least the primary tackler

    # Sum momentum of all engaged tacklers
    tackler_momentum_sum = sum(
        p.mass * math.hypot(p.vx, p.vy)
        for p in ctx.engagement.tacklers.values()
    )

    tackle_angle_avg = max(-1.0, min(1.0, _approach_angle_score(ctx)))
    tackle_compression = 0.5  # simplified — could be computed from spacing
    defender_fatigue = 1.0 - (1.0 - getattr(ctx, "fatigue", 1.0)) * 0.5

    # ── MULTI-BODY ASSISTS (2026-09-23) ──────────────────────────────
    # The declaration's WRAP ASSISTS are bodies in the contact radius that
    # ``ctx.engagement`` never registered as tacklers — the engine joins
    # secondaries dynamically, and a body that wrapped without being evaluated
    # is never joined at all.  Their momentum enters the defender's raw score
    # at a weight the model declares, and each counts as a declared fraction
    # of a tackler.  Both terms are exactly 0.0 with no model, so a solo
    # tackle's dominance is bit-identical to the stage before this one.
    _model = _wrap_model(ctx)
    assist_momentum = _multi_body.assist_momentum_kgmps(_model)
    assist_count = int(getattr(_model, "assist_count", 0) or 0)

    defender_raw = (
        (tackler_momentum_sum / MOMENTUM_NORM) * 1.0
        + tackler_count * 0.7
        + tackle_angle_avg * 0.5
        + tackle_compression * 0.4
        - defender_fatigue * 0.4
        + (assist_momentum / MOMENTUM_NORM)
        * _multi_body.DOMINANCE_ASSIST_MOMENTUM_WEIGHT
        + assist_count * _multi_body.DOMINANCE_ASSIST_COUNT_WEIGHT
    )

    # ── Apply biases ─────────────────────────────────────────────
    ATTACKER_BIAS: float = 0.8
    DEFENDER_BIAS: float = 0.0

    attacker_dom = attacker_raw + ATTACKER_BIAS
    defender_dom = defender_raw + DEFENDER_BIAS

    outcome.attacker_dominance = attacker_dom
    outcome.defender_dominance = defender_dom

    delta = attacker_dom - defender_dom
    if delta > 0:
        outcome.tackle_result = TackleResult.WEAK if delta > 1.0 else TackleResult.NEUTRAL
    else:
        outcome.tackle_result = TackleResult.DOMINANT if delta < -1.0 else TackleResult.NEUTRAL

    # ── DOMINANT-FROM-BEHIND GATE (2026-08-06) ──────────────────────
    # A defender may only earn DOMINANT (defender-wins drive, backward
    # pushback) when they hit the BC from in front or level.  The
    # geometric eligibility was snapshotted once at TES entry
    # (ctx.defender_can_dominate, see game/tes/tes.py::can_be_dominant).
    # When the gate fails, cap the result at NEUTRAL -- a pure chase-down
    # tackle can still stop the BC, but never drives them backward.
    # Default True keeps legacy behaviour for any context that was not
    # built through tes_start_tackle().
    if (
        outcome.tackle_result == TackleResult.DOMINANT
        and not getattr(ctx, "defender_can_dominate", True)
    ):
        outcome.tackle_result = TackleResult.NEUTRAL


def _approach_angle_score(ctx: TESContext) -> float:
    """
    Score representing how directly the defender is hitting the BC.

    1.0 = perfect head-on (angle ≈ 0°)
    0.0 = side-on (angle ≈ 90°)
    -1.0 = chasing from behind (angle ≈ 180°)

    Computed from the dot product of defender's approach direction
    and the vector from defender to BC.
    """
    deff = ctx.defender
    att = ctx.attacker
    dx = att.x_m - deff.x_m
    dy = att.y_m - deff.y_m
    dist = math.hypot(dx, dy)
    if dist < 0.01:
        return 1.0  # on top of each other — assume head-on

    # Defender's movement direction (or facing if stationary)
    deff_spd = math.hypot(deff.vx, deff.vy)
    if deff_spd > 0.01:
        def_dx = deff.vx / deff_spd
        def_dy = deff.vy / deff_spd
    else:
        def_dx = math.cos(getattr(deff, 'facing', 0.0))
        def_dy = math.sin(getattr(deff, 'facing', 0.0))

    # Direction from defender to BC
    to_bc_dx = dx / dist
    to_bc_dy = dy / dist

    # Dot product: 1.0 = heading straight at BC, -1.0 = heading away
    dot = def_dx * to_bc_dx + def_dy * to_bc_dy
    return _clamp(dot, -1.0, 1.0)


def compute_drive_distance(ctx: TESContext, outcome: TESOutcome, rng: random.Random) -> None:
    """TES PARTICIPANT FILTER APPLIED

    Compute physics-based drive distance from momentum delta.

    Attacker momentum (mass × speed) vs defender momentum determines
    how far the BC is driven.  Negative = driven back (defender wins),
    positive = drives forward (attacker wins).

    Stability, collapse, and tackle_result act as modifiers.

    Final output clamped to [-0.5, 3.0] metres to prevent extremes.
    """
    att = ctx.attacker
    deff = ctx.defender

    # Momentum delta: positive = defender stronger (BC driven back)
    #
    # NOT SCALED BY THE FEND CURVE (Stage 3.2), DELIBERATELY.  This is a
    # displacement, not a collision comparison: it runs through a delta clamp,
    # a 1.5 m forward floor for a winning carrier and a post-result x0.75/x1.8
    # multiplier, and that floor exists to guarantee an attacker-win drive a
    # real forward gain.  Scaling its momentum term would fight the contract
    # the floor encodes, so the fend curve is consumed ONLY where the carrier's
    # momentum is weighed against the defenders' resistance — the dominance
    # equation above, whose own +/-1.0 bands are that comparison's thresholds.
    att_mom = att.mass * att.speed
    deff_mom = deff.mass * deff.speed
    momentum_delta = deff_mom - att_mom

    # Clamp momentum delta to sane range
    momentum_delta = max(-200.0, min(400.0, momentum_delta))

    # Convert to metres
    # MEDIAN-3m (2026-08-06): 0.040 -> 0.055 so fast-BC attacker-wins
    # reach ~3 m median with the other drive raises.
    DRIVE_SCALE: float = 0.055  # tunable
    # FORWARD FLOOR (2026-08-06): attacker-win drives (WEAK/NEUTRAL) always
    # net at least this many forward metres (scaled by the BC's pre-contact
    # momentum factor) so a stopped-and-stood tackle still produces a real
    # forward gain instead of a sub-metre shuffle.
    MIN_ATTACKER_WIN_DRIVE_M: float = 1.5
    physics_drive_m = momentum_delta * DRIVE_SCALE

    # Stability modifier: low stability = easier to drive back
    physics_drive_m *= (1.0 + (1.0 - getattr(ctx, "stability", 1.0)))

    # Collapse modifier: collapsed attacker gets driven further
    if getattr(ctx, "collapsed", False):
        physics_drive_m *= 1.25

    # MOMENTUM GATE (2026-08-06): discount the ENTIRE drive magnitude
    # (forward AND backward) by the BC's pre-contact momentum factor.
    # A flat-footed BC (factor 0.35) drives ~0.35x as far, and is also
    # easier to push back (defender dominance less muted).
    physics_drive_m *= getattr(ctx, "bc_momentum_factor", 1.0)

    # Tackle result modifier
    # MEDIAN-3m (2026-08-06): WEAK 0.4 -> 0.75 so attacker-wins retain
    # a meaningful forward drive instead of a ~0.3 m shuffle.
    if outcome.tackle_result == TackleResult.DOMINANT:
        physics_drive_m *= 1.8
    elif outcome.tackle_result == TackleResult.WEAK:
        physics_drive_m *= 0.75

    # Final clamp — widened range to accommodate stronger dominance separation
    physics_drive_m = max(-1.5, min(5.0, physics_drive_m))

    # FORWARD FLOOR (2026-08-06): attacker-wins (WEAK/NEUTRAL) always net
    # at least MIN_ATTACKER_WIN_DRIVE_M forward metres, scaled by the
    # BC's pre-contact momentum factor (already applied above).  The
    # engine consumes |drive_distance_m| as the forward magnitude (the
    # DOMINANT back-drive direction comes from ctx.dominance_result
    # downstream, not the sign), so enforce the floor on the MAGNITUDE:
    # never shrink a drive physics already made larger — only lift
    # sub-floor (or zero/backward-in-sign) attacker-win drives up to the
    # minimum.  DOMINANT (defender-wins) is never floored; it only keeps
    # its backward drive through the legitimate front-on path — a
    # defender chasing from behind was already capped to NEUTRAL in
    # compute_dominance(), so it can never reach this branch.
    if outcome.tackle_result in (TackleResult.WEAK, TackleResult.NEUTRAL):
        floor_m = MIN_ATTACKER_WIN_DRIVE_M * getattr(ctx, "bc_momentum_factor", 1.0)
        if abs(physics_drive_m) < floor_m:
            physics_drive_m = -floor_m if physics_drive_m < 0.0 else floor_m

    # Apply team direction so drive goes toward the attacking tryline
    outcome.drive_distance_m = physics_drive_m * ctx.team_attack_dir


def compute_tackle_duration(ctx: TESContext, outcome: TESOutcome, rng: random.Random) -> None:
    """
    Compute the DRIVE-LERP duration (seconds), clamped so the drive
    resolves quickly.

    DRIVE CRAWL CLAMP (2026-08-04): the WEAK range (1.8-2.8 s) made the
    DRIVE LERP crawl — probe showed BC + tacklers reaching drive_target
    at ~1.0 s while the phase held for ~2.6 s, freezing everyone static
    for the tail.  DRIVE duration is now capped at 1.5 s so a completed
    tackle exits DRIVE promptly; relative ordering (DOMINANT fastest,
    WEAK slowest) is preserved.

    BOOST TACKLE MOMENTUM (2026-08-05): durations x1.3 (longer DRIVE =
    more frames = more accumulated drive variation), and the hard ceiling
    is raised 1.5 s -> 1.95 s so the extended range is not cut short.
    """
    if outcome.tackle_result == TackleResult.DOMINANT:
        outcome.tackle_duration_s = rng.uniform(0.78, 1.30)
    elif outcome.tackle_result == TackleResult.WEAK:
        outcome.tackle_duration_s = rng.uniform(1.30, 1.95)
    else:
        outcome.tackle_duration_s = rng.uniform(1.04, 1.95)
    # ── MULTI-BODY COLLABORATIVE DURATION (2026-09-23) ──────────────
    # More bodies wrapping the carrier resolve the contest SOONER, so the
    # drawn duration is scaled by the contest's own collaborative scale —
    # computed once at TES entry from the declaration.  The scale is EXACTLY
    # 1.0 with no model, so the legacy draw passes through untouched, and the
    # result is clamped back into the same envelope the draw was clamped into:
    # this can only SHORTEN a duration, and only within the band this function
    # already declared.
    outcome.tackle_duration_s = min(
        1.95,
        max(
            0.4,
            outcome.tackle_duration_s
            * float(getattr(ctx, "multibody_duration_scale", 1.0) or 1.0),
        ),
    )


def compute_ptb_time(ctx: TESContext, outcome: TESOutcome, rng: random.Random) -> None:
    """
    PTB time unchanged.
    """
    if outcome.tackle_result == TackleResult.DOMINANT:
        outcome.ptb_time_s = rng.uniform(1.0, 1.4)
    elif outcome.tackle_result == TackleResult.WEAK:
        outcome.ptb_time_s = rng.uniform(2.2, 3.2)
    else:
        outcome.ptb_time_s = rng.uniform(1.6, 2.4)


# ------------------------------------------------------------
# TACKLE DRIVE VECTOR PATCH — pure forward-drive target (SDEA)
# ------------------------------------------------------------
# Old (buggy) behaviour: drive_target_x was derived by re-applying
# team_attack_dir on top of an already-signed drive_distance_m in
# OpenPlayEngine._update_tackle_drive(), which double-flipped the
# sign and produced a backwards drive target whenever the vector
# components cancelled.  This function is the single source of
# truth: BC is always driven FORWARD along attack_dir.

def compute_drive_target_x(drive_start_x: float,
                           attack_dir: int,
                           drive_magnitude: float) -> float:
    """Compute the X-target for the TACKLE_DRIVE LERP.

    SPEC:
      - attack_dir ∈ {+1, -1}   (+1 = toward attacking tryline)
      - drive_magnitude ≥ 0     (forward distance, metres)
      - Result: BC is driven forward along attack_dir by
        drive_magnitude metres from drive_start_x.

    Pure computation — reads only its arguments, returns a value.
    Never writes to world_state, players, OPE, TES, markers, or
    freeze state.  Same inputs → same output.
    """
    # Enforce valid attack_dir (defensive guard, no silent flip)
    if attack_dir not in (+1, -1):
        raise ValueError(f"invalid attack_dir={attack_dir}, expected +1 or -1")

    # Enforce non-negative magnitude (no accidental backward drive)
    if drive_magnitude < 0.0:
        raise ValueError(f"drive_magnitude must be >= 0, got {drive_magnitude}")

    drive_target_x = drive_start_x + attack_dir * drive_magnitude
    return drive_target_x
