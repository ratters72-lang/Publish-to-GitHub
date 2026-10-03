"""Monte-Carlo tuning harness for the NRL DRIVE movement curve.

Replicates _update_tackle_drive()'s per-frame math at 60 Hz using the real
TES constants/functions, then measures the total BC.x movement distribution
over a full DRIVE (deterministic accumulator + momentum accumulator +
Gaussian variation + geometry).  Tracks whether the spec gate is met:

    ~90% of tackles: total movement in [-2, +6] metres
    WEAK/NEUTRAL   -> forward-biased (mean > 0)
    DOMINANT       -> backward-biased (mean < 0)

Constants are read live from game/tes/tes_drive_variation.py so edits to
those module constants are picked up automatically.
"""
import math
import random
import sys

sys.path.insert(0, ".")

from game.tes.tes_drive_variation import (
    BASE_SIGMA,
    MOMENTUM_SCALE,
    ATTACKER_BIAS,
    DEFENDER_BIAS,
    MAX_VARIATION,
    DRIVE_DETERMINISTIC_BOOST,
    DOMINANT_DET_SUPPRESS,
    DRIVE_DECAY,
    DRIVE_MOMENTUM_CLAMP,
    DRIVE_FORCE_CLAMP,
    BASE_DRIVE_FORCE,
    DRIVE_VARIATION,
    DEF_RESISTANCE_SCALE,
    compute_geometric_factor,
)

DT = 1.0 / 60.0

# Realistic per-dominance input distributions (mirror compute_drive_distance
# / compute_tackle_duration output ranges and drive_strength calculation).
DOMINANCE_W = 0.35   # rough real-world share of WEAK (attacker wins)
DOMINANCE_N = 0.40   # NEUTRAL
DOMINANCE_D = 0.25   # DOMINANT


def make_drive_inputs(rng, dom):
    """Return (drive_step_mps, duration_s, angle_deg, lean, alignment,
    strength_sum, team_dir)."""
    # drive_step_mps mirrors: (|drive_distance| / dur) * drive_strength * 1.5
    if dom == "WEAK":
        drive_mag = rng.uniform(0.0, 2.4)          # WEAK small + drive
        dur0 = rng.uniform(1.30, 1.95)
        strength = rng.uniform(0.7, 1.1)
    elif dom == "DOMINANT":
        drive_mag = rng.uniform(0.0, 1.0)          # defender drives BC back
        dur0 = rng.uniform(0.78, 1.30)
        strength = rng.uniform(1.4, 1.8)
    else:  # NEUTRAL
        drive_mag = rng.uniform(0.0, 1.6)
        dur0 = rng.uniform(1.04, 1.95)
        strength = rng.uniform(1.1, 1.5)
    drive_step_mps = (drive_mag / max(0.1, dur0)) * strength * 1.5
    duration_s = max(0.4, min(1.95, dur0))
    angle = rng.uniform(0, 80)
    lean = rng.uniform(0.3, 1.0)
    alignment = rng.uniform(0.0, 0.6) if rng.random() < 0.5 else 0.0
    strength_sum = rng.uniform(0.4, 1.6)
    team_dir = 1
    return drive_step_mps, duration_s, angle, lean, alignment, strength_sum, team_dir


def simulate_one(rng, dom):
    """Run one full DRIVE at 60 Hz and return total BC.x movement."""
    (drive_step_mps, duration_s, angle, lean, alignment,
     strength_sum, team_dir) = make_drive_inputs(rng, dom)

    # Geometry factor (pure function reads the 3 context fields).
    from game.tes.tes_context import TESContext
    ctx = TESContext()
    ctx.contact_angle = angle
    ctx.bc_lean_forward = lean
    ctx.tackler_alignment = alignment
    ctx.dominance_result = dom
    ctx.team_attack_dir = team_dir
    ctx.drive_momentum = 0.0
    ctx.drive_elapsed_s = 0.0
    ctx.drive_det_x = 0.0
    ctx.drive_variation_x = 0.0
    ctx.drive_start_x = 50.0

    geometric_factor = compute_geometric_factor(ctx)

    # Deterministic step per frame: sign-flipped for DOMINANT, boosted for
    # WEAK/NEUTRAL, geometry-scaled.  Mirrors the engine accumulator.
    if dom == "DOMINANT":
        det_step_mps = -drive_step_mps * DOMINANT_DET_SUPPRESS
    else:
        det_step_mps = drive_step_mps * DRIVE_DETERMINISTIC_BOOST
    det_step_mps *= geometric_factor

    duration = max(0.1, duration_s)
    elapsed = 0.0
    while True:
        elapsed += DT
        ctx.drive_det_x += det_step_mps * DT
        # Variation (spec Step 3 + Step 7).
        raw_force = BASE_DRIVE_FORCE + rng.uniform(-DRIVE_VARIATION, DRIVE_VARIATION)
        raw_force -= strength_sum * DEF_RESISTANCE_SCALE
        raw_force = max(DRIVE_FORCE_CLAMP[0], min(DRIVE_FORCE_CLAMP[1], raw_force))
        ctx.drive_momentum = max(
            DRIVE_MOMENTUM_CLAMP[0],
            min(DRIVE_MOMENTUM_CLAMP[1],
                ctx.drive_momentum * DRIVE_DECAY + raw_force))
        variation_step = rng.gauss(0.0, BASE_SIGMA)
        variation_step *= (1.0 + ctx.drive_momentum * MOMENTUM_SCALE)
        if dom in ("WEAK", "NEUTRAL"):
            variation_step *= ATTACKER_BIAS
        else:
            variation_step *= DEFENDER_BIAS
        variation_step = max(-MAX_VARIATION, min(MAX_VARIATION, variation_step))
        variation_step *= geometric_factor
        ctx.drive_variation_x += variation_step
        if elapsed / duration >= 1.0:
            break
    return ctx.drive_det_x + ctx.drive_variation_x


def main():
    rng = random.Random(20260805)
    N = 50000
    totals = []
    per_dom = {"WEAK": [], "NEUTRAL": [], "DOMINANT": []}
    for _ in range(N):
        r = rng.random()
        if r < DOMINANCE_W:
            dom = "WEAK"
        elif r < DOMINANCE_W + DOMINANCE_N:
            dom = "NEUTRAL"
        else:
            dom = "DOMINANT"
        total = simulate_one(rng, dom)
        totals.append(total)
        per_dom[dom].append(total)

    totals.sort()
    n = len(totals)
    in_range = sum(1 for v in totals if -2.0 <= v <= 6.0)
    pct = 100.0 * in_range / n
    mean = sum(totals) / n
    p05 = totals[int(n * 0.05)]
    p90 = totals[int(n * 0.90)]
    p95 = totals[int(n * 0.95)]

    print(f"N={n}")
    print(f"P(total in [-2, +6]) = {pct:.2f}%  (target >= 90%)")
    print(f"mean = {mean:.3f}")
    print(f"p05 = {p05:.3f}  p90 = {p90:.3f}  p95 = {p95:.3f}")
    print(f"min = {totals[0]:.3f}  max = {totals[-1]:.3f}")
    for dom in ("WEAK", "NEUTRAL", "DOMINANT"):
        arr = per_dom[dom]
        m = sum(arr) / len(arr)
        inside = sum(1 for v in arr if -2.0 <= v <= 6.0)
        print(f"  {dom:8s} n={len(arr):5d} mean={m:7.3f} "
              f"in-range={100.0*inside/len(arr):5.1f}%")

    ok = in_range / n >= 0.90
    weak_neutral_fwd = (sum(per_dom["WEAK"]) + sum(per_dom["NEUTRAL"])) > 0
    dom_back = sum(per_dom["DOMINANT"]) / len(per_dom["DOMINANT"]) < 0
    print(f"\nGATE: 90% in-range={ok} "
          f"WEAK/NEUTRAL forward={weak_neutral_fwd} "
          f"DOMINANT backward={dom_back}")
    sys.exit(0 if (ok and weak_neutral_fwd and dom_back) else 1)


if __name__ == "__main__":
    main()
