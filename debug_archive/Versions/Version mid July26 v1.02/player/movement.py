# player/movement.py
# NEW FILE — Movement Foundation (Phase 1)

import math

from player import player

# Phase 2D — Unified Facing Physics
ROTATION_SMOOTHING = 0.15

def update_facing_unified(player, dt):
    """
    Unified facing physics:
      - smooth rotation toward velocity direction
      - rotation inertia
      - movement/facing coupling
    """
    vx = player.vx
    vy = player.vy
    speed = math.hypot(vx, vy)

    # No rotation if nearly stationary
    if speed < 0.05:
        return

    target = math.atan2(vy, vx)
    current = player.facing

    # shortest-path angle diff
    diff = (target - current + math.pi) % (2 * math.pi) - math.pi

    # smooth rotation
    player.facing = current + diff * ROTATION_SMOOTHING

    # light movement → facing coupling
    # (keeps movement direction aligned with facing)
    player.vx += math.cos(player.facing) * 0.02
    player.vy += math.sin(player.facing) * 0.02


TUNERS_TACKLE_SOLIDIFICATION = {
    "defender_rotation_lock_strength": 0.85,
    "bc_rotation_smoothing": 0.20,
    "lateral_velocity_clamp": 0.10,
    "jitter_smoothing": 0.05,
    "footwork_deadzone": 0.20,
}


def smooth_input(player, raw_x, raw_y):
    """
    Low-pass filter to stabilise input and prevent jitter.
    Updates player.input_smooth_x and player.input_smooth_y.
    """
    # smoothing factor — adjust later if needed
    s = 0.25

    player.input_smooth_x += (raw_x - player.input_smooth_x) * s
    player.input_smooth_y += (raw_y - player.input_smooth_y) * s


def update_velocity(player, dt):
    """
    Accelerate/decelerate toward desired velocity based on smoothed input.
    Updates player.vel_x and player.vel_y.
    """

    desired_x = player.input_smooth_x * player.max_speed
    desired_y = player.input_smooth_y * player.max_speed

    # accelerate toward desired velocity
    player.vel_x += (desired_x - player.vel_x) * player.accel * dt
    player.vel_y += (desired_y - player.vel_y) * player.accel * dt


def update_position(player, dt):
    """
    Apply velocity to position.
    Updates player.x and player.y.
    """
    player.x += player.vel_x * dt
    player.y += player.vel_y * dt

    player.target_x = max(0.0, min(120.0, player.target_x))
    player.target_y = max(0.0, min(70.0, player.target_y))


def apply_tackle_solidification(bc, primary, dt):
    """Apply tackle solidification tuners to BC and primary defender.

    Called each frame during TACKLE_CONTACT while TES is still in ENGAGE
    (pre-contact chase).  Stabilises both players to prevent jitter and
    erratic rotation just before contact.
    """
    t = TUNERS_TACKLE_SOLIDIFICATION

    # ── 1. Lateral velocity clamp ──
    # (defender facing lock removed — unified facing handles rotation)
    if primary is not None and bc is not None:
        dx = bc.x - primary.x
        dy = bc.y - primary.y
        dist = math.hypot(dx, dy)
        if dist > 0.01:
            primary.vy *= 1.0 - t["lateral_velocity_clamp"]
            primary.vel_y *= 1.0 - t["lateral_velocity_clamp"]

    # ── 2. (BC rotation smoothing removed — unified facing handles rotation) ──

    # ── 3. Jitter smoothing ──
    # Apply low-pass to velocity deltas to dampen frame-to-frame jitter
    for p in (bc, primary):
        if p is None:
            continue
        prev_vx = getattr(p, "_prev_vx", p.vx)
        prev_vy = getattr(p, "_prev_vy", p.vy)

        # Exponential smooth toward previous velocity
        p.vx = prev_vx + (p.vx - prev_vx) * (1.0 - t["jitter_smoothing"])
        p.vy = prev_vy + (p.vy - prev_vy) * (1.0 - t["jitter_smoothing"])

        # Store for next frame
        p._prev_vx = p.vx
        p._prev_vy = p.vy

    # ── 4. Footwork deadzone ──
    # Zero out tiny velocity components that cause foot-shuffle jitter
    for p in (bc, primary):
        if p is None:
            continue
        speed = math.hypot(p.vx, p.vy)
        if speed < t["footwork_deadzone"]:
            p.vx = 0.0
            p.vy = 0.0
            p.vel_x = 0.0
            p.vel_y = 0.0

    # ── 5. Hard clamp forward velocity during ENGAGE chase ──
    # Prevents the movement model from accelerating into contact,
    # which caused a speed spike right before TES takeover.
    max_engage_speed = 2.0  # m/s — tune to match your movement resolution
    if bc is not None:
        bc.vx = max(-max_engage_speed, min(max_engage_speed, bc.vx))
    if primary is not None:
        primary.vx = max(-max_engage_speed, min(max_engage_speed, primary.vx))
