# game/tes/tes_dominance.py
import random
import math

from .tes_context import TESContext
from .tes_outcomes import TESOutcome
from .tes_state import TackleResult

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
    """
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

    # Wrap delta (simple proximity + facing)
    wrap_delta = _compute_wrap_delta(att, deff)

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


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ------------------------------------------------------------
# PHASE 6 → PHASE 7 BRIDGE (Dominance + Drive + Duration)
# ------------------------------------------------------------

def compute_dominance(ctx: TESContext, outcome: TESOutcome, rng: random.Random) -> None:
    """
    Dominance now uses the continuous tackle_score instead of raw momentum.
    Falls back to ctx.initial_impact_score if tackle_score hasn't been set.

    Phase 2B: score is multiplied by attacker stability so an unstable
    attacker (collapsed, off-balance) gives the defender a dominance advantage.
    """
    score = getattr(ctx, "tackle_score", ctx.initial_impact_score)
    score *= getattr(ctx, "stability", 1.0)  # Phase 2B

    # Defender dominance increases as score decreases
    defender_dom = _clamp(1.0 - (score / 3.0), 0.0, 1.0)
    attacker_dom = 1.0 - defender_dom

    outcome.attacker_dominance = attacker_dom
    outcome.defender_dominance = defender_dom

    if defender_dom > 0.60:
        outcome.tackle_result = TackleResult.DOMINANT
    elif attacker_dom > 0.60:
        outcome.tackle_result = TackleResult.WEAK
    else:
        outcome.tackle_result = TackleResult.NEUTRAL


def compute_drive_distance(ctx: TESContext, outcome: TESOutcome, rng: random.Random) -> None:
    """
    Drive distance now based on continuous dominance.
    """
    dom = outcome.attacker_dominance

    if outcome.tackle_result == TackleResult.DOMINANT:
        outcome.drive_distance_m = -4.0 * (1.0 - dom)
    elif outcome.tackle_result == TackleResult.WEAK:
        outcome.drive_distance_m = 3.0 * dom
    else:
        outcome.drive_distance_m = rng.uniform(-0.4, 0.4)


def compute_tackle_duration(ctx: TESContext, outcome: TESOutcome, rng: random.Random) -> None:
    """
    Duration unchanged.
    """
    if outcome.tackle_result == TackleResult.DOMINANT:
        outcome.tackle_duration_s = rng.uniform(0.6, 1.2)
    elif outcome.tackle_result == TackleResult.WEAK:
        outcome.tackle_duration_s = rng.uniform(1.8, 2.8)
    else:
        outcome.tackle_duration_s = rng.uniform(1.2, 2.0)


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
