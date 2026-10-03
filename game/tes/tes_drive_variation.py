"""game/tes/tes_drive_variation.py -- TES DRIVE + DOMINANCE VARIATION.

Adds controlled forward BC drive during TACKLE_DRIVE (extra metres,
wrestle) and backward pushback during TACKLE_DOMINANCE (dominance
pushback), with natural per-frame variation, defender strength
resistance, momentum decay, and safe clamps.

This module contains ONLY pure computation + tunable constants.
Position integration happens in OpenPlayEngine so ordering against
_follow_attached_tacklers() is guaranteed (BC moves first, tacklers
follow after -- contract F).
"""

import math
import random

from .tes_context import TESContext

# ------------------------------------------------------------------
# A. CONFIGURATION CONSTANTS (contract A)
# ------------------------------------------------------------------
# Baseline BC forward force (m/s) applied per DRIVE frame, before decay.
# BOOST TACKLE MOMENTUM (2026-08-05): 1.0 -> 1.6 so the attacker
# generates more forward pressure per frame.
BASE_DRIVE_FORCE: float = 1.6
# +/- per-frame random variation (m/s).
DRIVE_VARIATION: float = 0.35
# BOOST FORWARD METRES (2026-08-05): attacker-win (WEAK/NEUTRAL) DRIVE
# frames get x1.8 on the per-frame metres variation so the attacker
# gains more forward metres per frame.  DOMINANT (defender win) is
# deliberately NOT boosted.
DRIVE_VARIATION_ATTACKER_BOOST: float = 1.8
# Momentum decay applied to the persistent drive momentum each frame.
DRIVE_DECAY: float = 0.98
# Defender strength -> resistance scale (sum of defender strengths).
DEF_RESISTANCE_SCALE: float = 0.05
# Baseline backward push (m/s) applied per DOMINANCE frame.
DOMINANCE_PUSH: float = 0.7
# +/- per-frame random variation (m/s) on the dominance push.
DOMINANCE_VARIATION: float = 0.25

# Safe clamps (contract A / F).
# BOOST TACKLE MOMENTUM (2026-08-05): the upper bound is raised 2.0 -> 2.6
# so the boosted BASE_DRIVE_FORCE (1.6) + variation can pass through without
# binding on the old clamp.
DRIVE_FORCE_CLAMP: tuple[float, float] = (-0.5, 2.6)
# Persistent drive-momentum ceiling for the per-frame BC accumulator.
# BOOST TACKLE MOMENTUM (2026-08-05): 2.0 -> 2.6 so momentum can
# accumulate higher before clamping.
DRIVE_MOMENTUM_CLAMP: tuple[float, float] = (-0.5, 2.6)
DOMINANCE_PUSH_CLAMP: tuple[float, float] = (0.0, 2.0)

# ------------------------------------------------------------------
# B. NRL DRIVE MOVEMENT — MOMENTUM CURVE + ATTACKER BIAS (2026-08-05)
# ------------------------------------------------------------------
# Replaces the flat momentum-variation write with a momentum-weighted
# Gaussian curve, attacker-win bias, defender-win suppression, and a
# geometric correction factor.  Target distribution after a full DRIVE:
# ~90% of tackles total BC.x movement in [-2, +6] metres.
#
#   BASE_SIGMA:          standard deviation of the raw Gaussian draw (m)
#   MOMENTUM_SCALE:      how much persistent drive momentum widens the curve
#   ATTACKER_BIAS:       WEAK/NEUTRAL (attacker win) multiplier on the step
#   DEFENDER_BIAS:       DOMINANT (defender win) multiplier on the step
#   MAX_VARIATION:       hard clamp on any single per-frame variation step (m)
#   DRIVE_DETERMINISTIC_BOOST: attacker-win multiplier on the deterministic
#                        dx_step (spec Step 5: x1.8 for WEAK/NEUTRAL).
#   DOMINANT_DET_SUPPRESS: defender-win multiplier on the deterministic
#                        back-drive step (spec: defender-win suppression).
#                        Tuned so a full DOMINANT DRIVE stays above -2 m —
#                        the raw drive_strength (clamped at 2.0) produces
#                        ~3.5 m/s of backward step, which over a 0.85-1.1 s
#                        DOMINANT duration would exceed the [-2, +6] target.
BASE_SIGMA: float = 0.12
MOMENTUM_SCALE: float = 0.45
ATTACKER_BIAS: float = 1.35
DEFENDER_BIAS: float = 0.75
MAX_VARIATION: float = 0.40
DRIVE_DETERMINISTIC_BOOST: float = 1.8
DOMINANT_DET_SUPPRESS: float = 0.55

# Geometric correction factor tunables (compute_geometric_factor).
ANGLE_WEIGHT: float = 0.55          # how strongly angle reduces drive
LEAN_WEIGHT: float = 0.35           # how strongly BC lean affects drive
MULTI_TACKLER_WEIGHT: float = 0.25  # alignment penalty for multiple tacklers
MIN_FACTOR: float = 0.10            # never fully cancel drive
MAX_FACTOR: float = 1.00            # never exceed full effectiveness

# ------------------------------------------------------------------
# JIGGLE-KILL / MEDIAN-3m TUNINGS (2026-08-06)
# ------------------------------------------------------------------
# The old per-frame Gaussian was zero-mean and clamped at +/-0.40 m/frame
# (~24 m/s of apparent velocity) -- a shudder that contributed ~0 net
# forward metres.  BASE_SIGMA is scaled down and MAX_VARIATION set to
# 0.05 m/frame (~3 m/s) so the variation channel is a smooth, directional
# accumulator instead of a vibration.
BASE_SIGMA_POST: float = 0.015
MAX_VARIATION_POST: float = 0.05
# Momentum-factor curve: BC pre-contact speed -> drive discount in [0,1].
#   0.35 at flat-footed (<= 2.0 m/s) -> 1.0 at full sprint (>= 7.0 m/s).
MOMENTUM_FACTOR_FF: float = 0.35
MOMENTUM_FACTOR_FULL: float = 1.0
MOMENTUM_FACTOR_FF_SPEED: float = 2.0
MOMENTUM_FACTOR_FULL_SPEED: float = 7.0
# Result-aware DOMINANCE pushback: when False (WEAK/NEUTRAL) the
# backward push is replaced by a tiny settle; only DOMINANT pushes back.
DOMINANCE_PUSH_WEAK: float = 0.0
DOMINANCE_PUSH_NEUTRAL: float = 0.05
DOMINANCE_PUSH_DOMINANT: float = 0.7
# Flat-footed BC extra drive reduction: DOMINANT push also muting.
FF_DOMINANCE_MUTE_SCALE: float = 0.6

# ------------------------------------------------------------------
# FORWARD-DRIVE REBALANCE (2026-08-06) — sideways fold + backward cap
# ------------------------------------------------------------------
# Goal: more forward drive, less linear backward shove.  DOMINANT
# (defender-win) pushes are split so only half the impulse goes
# backward while ~0.9x pushes sideways ("fold" the tackle), and every
# tackle gets at least a small sideways component proportional to BC
# pre-contact momentum.  A per-tackle cap on actual backward metres
# then damps any residual shove once the BC has already been driven
# back ~2 m.
#
#   DOMINANT_X_REBALANCE:    multiplier on the backward X step during
#                            DRIVE + DOMINANCE (0.5 = halve backward).
#   DOMINANT_SIDE_FOLD:      sideways impulse as a fraction of the
#                            pre-rebalance backward magnitude (0.9 =
#                            almost all of it sideways).
#   SIDE_MIN_WEAK_NEUTRAL:   minimum sideways fold (m) on EVERY
#                            attacker-win (WEAK/NEUTRAL) drive, scaled
#                            by bc_momentum_factor so flat-footed BCs
#                            still fold a little.  Keeps tackles from
#                            looking like pure linear shoves.
#   NET_BACKWARD_CAP_M:      per-tackle backward-metre cap (m).  Once
#                            ctx.net_backward_m exceeds this, further
#                            backward X is damped by NET_BACKWARD_DAMP.
#                            The sideways fold is NOT damped — after the
#                            cap the wrestle turns mostly sideways.
DOMINANT_X_REBALANCE: float = 0.5
DOMINANT_SIDE_FOLD: float = 0.9
SIDE_MIN_WEAK_NEUTRAL: float = 0.3
NET_BACKWARD_CAP_M: float = 2.0
NET_BACKWARD_DAMP: float = 0.3

# ══════════════════════════════════════════════════════════════════
# DOMINANCE SETTLE WINDOW (2026-09-17) -- the DOMINANCE HOLD is gone.
# ══════════════════════════════════════════════════════════════════
# The phase used to be a per-result clock (WEAK 0.30 / NEUTRAL 0.40 /
# DOMINANT 0.50 s) whose exit test was
#
#     ctx.dominance_elapsed_s >= ctx.dominance_hold_s
#
# and NOTHING else -- no arrival condition, no position test.  Measured
# in PHASE_TIMER_BUDGET_REPORT.md, the phase's ENTIRE movement content
# was 1.6 cm of ball-carrier displacement over 0.30 s (0.054 m/s): the
# clock WAS the phase, and it put 0.25-0.45 s of dead time on every
# tackle-to-PTB chain for that 1.6 cm.
#
# DOMINANCE now runs a short SETTLE window and exits.  The wrestle
# IMPULSE is preserved rather than discarded: the pushes below stay
# calibrated to the REFERENCE holds, so dominance_push_scale_for()
# multiplies the push rate by reference/settle and the total backward +
# lateral displacement a tackle delivers is conserved while the time is
# not spent.  On-field positioning therefore does not change.
#
# Why a shorter window cannot change the tackle OUTCOME events: the
# per-frame checks in game/tes/tes_checks.py OVERWRITE their flags every
# frame, and compute_dominance() unconditionally re-derives
# outcome.tackle_result later in the SAME frame -- so only the LAST
# DOMINANCE frame's draws survive.  A shorter window still has exactly
# one last frame, so per-tackle event probabilities are unchanged.
DOMINANCE_SETTLE_S: float = 0.05

# The holds the wrestle impulse is CALIBRATED to (seconds).  These no
# longer gate the phase -- they define the displacement the phase must
# deliver, which dominance_push_scale_for() compresses into the settle.
DOMINANCE_HOLD_WEAK: float = 0.30
DOMINANCE_HOLD_NEUTRAL: float = 0.40
DOMINANCE_HOLD_DOMINANT: float = 0.50
DOMINANCE_HOLD_DEFAULT: float = 0.25

# Ceiling on the compression ratio.  Keeps the worst observed single-
# frame step at ~0.12 m (no teleport / pop) while DOMINANT retains 8/10
# of its reference wrestle displacement.
DOMINANCE_PUSH_SCALE_MAX: float = 8.0


def dominance_hold_for(result: str) -> float:
    """Return the DOMINANCE EXIT window (s) -- the settle window.

    Kept as a function (same signature) so every caller keeps working;
    the value is now the settle window rather than a per-result clock.
    """
    return DOMINANCE_SETTLE_S


def dominance_hold_reference_for(result: str) -> float:
    """Return the REFERENCE hold (s) the wrestle displacement is
    calibrated to for *result* ("WEAK" / "NEUTRAL" / "DOMINANT")."""
    return {
        "WEAK": DOMINANCE_HOLD_WEAK,
        "NEUTRAL": DOMINANCE_HOLD_NEUTRAL,
        "DOMINANT": DOMINANCE_HOLD_DOMINANT,
    }.get(result, DOMINANCE_HOLD_DEFAULT)


def dominance_push_scale_for(result: str) -> float:
    """Compression ratio that conserves the wrestle displacement.

    reference_hold / settle, capped at DOMINANCE_PUSH_SCALE_MAX so the
    per-frame step stays physically sane.  Above the cap the residual
    displacement is discarded (DOMINANT keeps 8/10 of it).
    """
    reference = dominance_hold_reference_for(result)
    if DOMINANCE_SETTLE_S <= 0.0:
        return 1.0
    return min(DOMINANCE_PUSH_SCALE_MAX, reference / DOMINANCE_SETTLE_S)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def defender_strength_sum(ctx: TESContext) -> float:
    """Sum of strength (0..1) over ALL engaged tacklers.

    Contract B.2: primary + secondary + third.  Falls back to the
    defender context strength if the engagement registry is empty.
    """
    tacklers = ctx.engagement.tacklers
    if tacklers:
        return sum(pc.strength for pc in tacklers.values())
    return getattr(ctx.defender, "strength", 0.5)


def compute_drive_force(rng: random.Random, strength_sum: float) -> float:
    """Compute one DRIVE-frame BC forward force (m/s).

    Contract B:
      1. base + random.uniform(-DRIVE_VARIATION, DRIVE_VARIATION)
      2. - defender_strength_sum * DEF_RESISTANCE_SCALE
      3. momentum decay is applied by the engine accumulator
      4. clamped to [-0.5, 2.0] m/s
    """
    drive_force = BASE_DRIVE_FORCE + rng.uniform(-DRIVE_VARIATION, DRIVE_VARIATION)
    drive_force -= strength_sum * DEF_RESISTANCE_SCALE
    return _clamp(drive_force, *DRIVE_FORCE_CLAMP)


def compute_dominance_push(rng: random.Random, strength_sum: float) -> float:
    """Compute one DOMINANCE-frame backward push (m/s).

    Contract D:
      1. DOMINANCE_PUSH + random.uniform(-DOMINANCE_VARIATION, DOMINANCE_VARIATION)
      2. + defender_strength_sum * DEF_RESISTANCE_SCALE
      3. clamped to [0.0, 2.0] m/s
    """
    dom_push = DOMINANCE_PUSH + rng.uniform(-DOMINANCE_VARIATION, DOMINANCE_VARIATION)
    dom_push += strength_sum * DEF_RESISTANCE_SCALE
    return _clamp(dom_push, *DOMINANCE_PUSH_CLAMP)


def compute_geometric_factor(ctx: TESContext) -> float:
    """Simple, tunable geometric correction factor for DRIVE.

    Returns a multiplier between 0.0 and 1.0 representing how
    "effective" the ball carrier's forward drive is, given the
    geometry of the contact:

      1. Tackler angle:       wider approach angles reduce drive.
      2. BC lean direction:   leaning into the drive keeps it effective.
      3. Multi-tackler alignment: opposing tackler angles reduce drive.

    PURE FUNCTION — reads only ctx (contact_angle, bc_lean_forward,
    tackler_alignment), never mutates TES state, never touches clamp
    logic or tackler-follow logic.

    Factor = angle_factor * lean_factor * multi_factor, clamped to
    [MIN_FACTOR, MAX_FACTOR].  1.0 = fully effective forward movement,
    0.0 = fully cancelled by geometry.
    """
    # === 1. Tackler angle penalty ===
    # ctx.contact_angle in degrees (0 = perfect front-on).
    # Large angles (side-on / chasing) reduce the effective drive.
    angle_penalty = abs(getattr(ctx, "contact_angle", 0.0)) / 90.0
    angle_factor = 1.0 - (min(1.0, angle_penalty) * ANGLE_WEIGHT)

    # === 2. BC lean direction ===
    # ctx.bc_lean_forward in 0..1 (1 = fully leaning into the drive).
    lean_factor = 1.0 - ((1.0 - _clamp(getattr(ctx, "bc_lean_forward", 1.0), 0.0, 1.0)) * LEAN_WEIGHT)

    # === 3. Multi-tackler alignment penalty ===
    # ctx.tackler_alignment in 0..1 (0 = perfectly aligned, 1 = opposing).
    multi_factor = 1.0 - (_clamp(getattr(ctx, "tackler_alignment", 0.0), 0.0, 1.0) * MULTI_TACKLER_WEIGHT)

    # === Combine factors ===
    geometric_factor = angle_factor * lean_factor * multi_factor

    # === Clamp ===
    geometric_factor = max(MIN_FACTOR, min(MAX_FACTOR, geometric_factor))

    return geometric_factor


def compute_bc_momentum_factor(bc) -> float:
    """Pre-contact BC speed -> drive discount multiplier in [0, 1].

    ``bc`` is the live Player at TES entry (real velocity still intact).
    1.0 = full sprint (>= MOMENTUM_FACTOR_FULL_SPEED), 0.35 = flat-footed
    (<= MOMENTUM_FACTOR_FF_SPEED), linear ramp between.
    """
    bc_spd = math.hypot(getattr(bc, "vx", 0.0), getattr(bc, "vy", 0.0))
    lo = MOMENTUM_FACTOR_FF_SPEED
    hi = MOMENTUM_FACTOR_FULL_SPEED
    if bc_spd >= hi:
        return MOMENTUM_FACTOR_FULL
    if bc_spd <= lo:
        return MOMENTUM_FACTOR_FF
    t = (bc_spd - lo) / (hi - lo)
    return MOMENTUM_FACTOR_FF + (MOMENTUM_FACTOR_FULL - MOMENTUM_FACTOR_FF) * t


def compute_drive_variation_step(rng: random.Random, ctx: TESContext) -> float:
    """Directional DRIVE variation step (metres, this frame).

    JIGGLE-KILL (2026-08-06): replaces the zero-mean Gaussian with a
    momentum-weighted DRIFT so every WEAK/NEUTRAL frame contributes >= 0
    forward metres and every DOMINANT frame <= 0.  Scaled down to
    MAX_VARIATION_POST = 0.05 m/frame (~3 m/s) so the BC never shudders.
    """
    momentum = ctx.drive_momentum
    scale = (1.0 + momentum * MOMENTUM_SCALE)

    if ctx.dominance_result in ("WEAK", "NEUTRAL"):
        half = rng.uniform(0.0, BASE_SIGMA_POST) * scale * ATTACKER_BIAS
    else:  # DOMINANT
        half = -rng.uniform(0.0, BASE_SIGMA_POST) * scale * DEFENDER_BIAS

    step = max(-MAX_VARIATION_POST, min(MAX_VARIATION_POST, half))
    step *= compute_geometric_factor(ctx)
    return step


def compute_dominance_push_result(rng: random.Random, ctx: TESContext) -> float:
    """Result-aware DOMINANCE backward push (m/s, positive magnitude).

    Only DOMINANT (defender win) pushes the BC backward.  WEAK gets zero
    pushback and NEUTRAL a tiny settle, so attacker-wins stop going
    backwards every tackle.  Flat-footed BC (low bc_momentum_factor) is
    easier to stop, so its DOMINANT push is muted by FF_DOMINANCE_MUTE_SCALE.

    DOMINANCE SETTLE COMPRESSION (2026-09-17): the returned rate is the
    CLAMPED physical push (<= 2.0 m/s) multiplied by
    ``dominance_push_scale_for(result)``, because the phase now delivers
    the whole wrestle inside DOMINANCE_SETTLE_S instead of the reference
    hold.  The caller multiplies by dt, so the per-frame displacement is
    reference/settle times larger while the TOTAL displacement over the
    phase is unchanged (up to the scale cap).  The return value can
    therefore exceed the physical clamp -- it is a rate, not a speed
    limit, and the caller consumes it as a bounded position step.
    """
    result = getattr(ctx, "dominance_result", "NEUTRAL") or "NEUTRAL"
    base = {
        "WEAK": DOMINANCE_PUSH_WEAK,
        "NEUTRAL": DOMINANCE_PUSH_NEUTRAL,
        "DOMINANT": DOMINANCE_PUSH_DOMINANT,
    }.get(result, DOMINANCE_PUSH_NEUTRAL)

    if result == "DOMINANT":
        base += getattr(ctx, "bc_momentum_factor", 1.0) * FF_DOMINANCE_MUTE_SCALE * 0.3
    push = base + rng.uniform(-DOMINANCE_VARIATION, DOMINANCE_VARIATION)
    if result != "DOMINANT":
        push = max(0.0, push)
    # Clamp FIRST so the compressed quantity is the physical per-frame
    # push, then compress it into the settle window.
    return _clamp(push, 0.0, 2.0) * dominance_push_scale_for(result)


def investigate_tes_forward_momentum(bc, defender, tes_state, frame_log):
    """
    Investigate why the BC loses forward momentum during TES.
    This does NOT modify behaviour. It only observes and reports.

    The AI should run this once per TES frame and store the results.
    """
    # 1. Record TES state
    frame_log["tes_state"] = tes_state

    # 2. Record BC velocity before TES physics
    vx_before = getattr(bc, "vx", 0.0)
    vy_before = getattr(bc, "vy", 0.0)
    frame_log["bc_vx_before"] = vx_before
    frame_log["bc_vy_before"] = vy_before

    # 3. Record defender velocity
    frame_log["def_vx"] = getattr(defender, "vx", 0.0)
    frame_log["def_vy"] = getattr(defender, "vy", 0.0)

    # 4. Check for movement locks
    frame_log["movement_locked"] = getattr(bc, "movement_locked", False)
    frame_log["frozen"] = getattr(bc, "frozen", False)
    frame_log["override_movement"] = getattr(bc, "override_movement", False)

    # 5. Check for velocity clamps/damping
    frame_log["clamp_x"] = getattr(bc, "clamp_x", None)
    frame_log["clamp_y"] = getattr(bc, "clamp_y", None)
    frame_log["tes_damping"] = getattr(bc, "tes_damping", None)

    # 6. Check for positional corrections/snaps
    frame_log["snap_x"] = getattr(bc, "snap_x", None)
    frame_log["snap_y"] = getattr(bc, "snap_y", None)

    # 7. Record BC velocity after TES physics
    vx_after = getattr(bc, "vx", 0.0)
    vy_after = getattr(bc, "vy", 0.0)
    frame_log["bc_vx_after"] = vx_after
    frame_log["bc_vy_after"] = vy_after

    # 8. Compute forward momentum change
    frame_log["forward_delta"] = vx_after - vx_before

    # 9. Flag if TES killed forward momentum
    frame_log["momentum_killed"] = (
        vx_before > 0.1 and vx_after < vx_before * 0.5
    )
    # 10. Pre-contact context (set once at TES entry by OPE via
    #     defender._tes_ctx = ctx, read here)
    tes_ctx = getattr(defender, "_tes_ctx", None)
    frame_log["bc_momentum_factor"] = (
        getattr(tes_ctx, "bc_momentum_factor", 1.0)
        if tes_ctx is not None else 1.0
    )
    frame_log["bc_flatfooted"] = (
        getattr(tes_ctx, "bc_flatfooted", False)
        if tes_ctx is not None else False
    )
    return frame_log
