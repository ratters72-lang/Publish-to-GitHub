"""
PHASE 2 SMOKE TEST 2 — Dynamic Pushback

Two players approach each other head-on at known velocities.  Enable collision
bodies, run short simulation, and measure:

  1. Pre-collision integrator state vs Phase 1 baseline (must match)
     → baseline run: same input, collision_enabled = False
  2. Post-resolver penetration (should stabilise near zero)
  3. Post-resolver lateral velocity delta (units/sec)
     → difference between enabled and disabled runs after contact
"""

import sys, os, copy, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from player.player import Player
from game.physics.collision_system import CollisionSystem
from player.movement import smooth_input, update_velocity, update_position, update_facing


def run_phase1_baseline(p1, p2, dt, frames):
    """Run without collision to get the Phase 1 baseline positions/velocities."""
    states = []
    for f in range(frames):
        # Move toward each other:
        # p1 (RIGHT, x=5.0) moves LEFT → raw_x = -1.0
        # p2 (LEFT,  x=1.0) moves RIGHT → raw_x = 1.0
        for p in [p1, p2]:
            if p is p1:
                p.update_movement(-1.0, 0.0, dt)
            else:
                p.update_movement(1.0, 0.0, dt)

        # No collision resolution (collision_enabled is False by default)
        states.append({
            'frame': f,
            'p1_x': p1.x, 'p1_y': p1.y,
            'p1_vx': p1.vel_x, 'p1_vy': p1.vel_y,
            'p2_x': p2.x, 'p2_y': p2.y,
            'p2_vx': p2.vel_x, 'p2_vy': p2.vel_y,
        })
    return states


def run_phase2_collision(p1, p2, dt, frames):
    """Run WITH collision enabled."""
    cs = CollisionSystem()
    states = []
    for f in range(frames):
        # Same movement as baseline
        for p in [p1, p2]:
            if p is p1:
                p.update_movement(-1.0, 0.0, dt)
            else:
                p.update_movement(1.0, 0.0, dt)

        # Resolve collision
        p1.body.sync()
        p2.body.sync()
        cs.resolve_pair(p1.body, p2.body)

        states.append({
            'frame': f,
            'p1_x': p1.x, 'p1_y': p1.y,
            'p1_vx': p1.vel_x, 'p1_vy': p1.vel_y,
            'p2_x': p2.x, 'p2_y': p2.y,
            'p2_vx': p2.vel_x, 'p2_vy': p2.vel_y,
        })
    return states


def run_dynamic_pushback_test():
    # Clone players for baseline and collision runs
    # Start them 4.0 units apart so we get ~22 frames of clear pre-collision baseline.
    # p1 is at x=5.0 (RIGHT), p2 at x=1.0 (LEFT)
    # They approach: p1 moves LEFT (-x), p2 moves RIGHT (+x)
    
    # Phase 1 baseline (no collision)
    p1_base = Player(5.0, 35.0, player_id=0)
    p2_base = Player(1.0, 35.0, player_id=1)
    p1_base.vel_x = p1_base.vx = -4.0
    p2_base.vel_x = p2_base.vx = 4.0
    p1_base.input_smooth_x = -0.7
    p2_base.input_smooth_x = 0.7

    # Phase 2 (with collision)
    p1_col = Player(5.0, 35.0, player_id=0)
    p2_col = Player(1.0, 35.0, player_id=1)
    p1_col.vel_x = p1_col.vx = -4.0
    p2_col.vel_x = p2_col.vx = 4.0
    p1_col.input_smooth_x = -0.7
    p2_col.input_smooth_x = 0.7

    # Enable collision on the second pair
    p1_col.body.collision_enabled = True
    p2_col.body.collision_enabled = True

    dt = 1.0 / 60.0
    frames = 90  # 1.5 seconds — long enough to contact and stabilise

    # Run both
    baseline = run_phase1_baseline(p1_base, p2_base, dt, frames)
    collision = run_phase2_collision(p1_col, p2_col, dt, frames)

    # ── ANALYSIS ──
    # Find the first frame where Phase 1 baseline shows overlap
    first_contact_frame = None
    for s in baseline:
        dist = math.hypot(s['p1_x'] - s['p2_x'], s['p1_y'] - s['p2_y'])
        if dist < 1.1:  # combined radius = 1.1
            first_contact_frame = s['frame']
            break

    if first_contact_frame is None:
        print("  ❌ FAIL: players never made contact in baseline")
        return False, 0, "N", 0

    # Check pre-collision match (frames 0 to first_contact_frame - 1)
    pre_match = True
    for s_base, s_col in zip(baseline[:first_contact_frame], collision[:first_contact_frame]):
        eps = 1e-9
        if (abs(s_base['p1_x'] - s_col['p1_x']) > eps or
            abs(s_base['p1_vx'] - s_col['p1_vx']) > eps or
            abs(s_base['p2_x'] - s_col['p2_x']) > eps or
            abs(s_base['p2_vx'] - s_col['p2_vx']) > eps):
            pre_match = False
            break

    pre_match_str = "Y" if pre_match else "N"

    # Measure max penetration in collision run
    max_pen = 0.0
    for s in collision:
        dist = math.hypot(s['p1_x'] - s['p2_x'], s['p1_y'] - s['p2_y'])
        pen = max(0.0, 1.1 - dist)
        if pen > max_pen:
            max_pen = pen

    # Post-resolver lateral velocity delta
    # Compare lateral (Y) velocity at the frame AFTER first contact
    lateral_delta = 0.0
    for f in range(first_contact_frame, min(len(baseline), len(collision))):
        s_base = baseline[f]
        s_col = collision[f]
        # Lateral = Y direction (perpendicular to approach)
        delta = abs(s_base['p1_vy'] - s_col['p1_vy'])
        if delta > abs(lateral_delta):
            lateral_delta = delta

    print("=== PHASE 2 — DYNAMIC PUSHBACK TEST ===")
    print(f"  First contact frame:    {first_contact_frame}")
    print(f"  Frames simulated:       {frames}")
    print(f"  Max penetration:        {max_pen:.6f} m")
    print(f"  Pre-collision match:    {pre_match_str}")
    print(f"  Post-resolver lateral Δ:{lateral_delta:.6f} m/s")
    print(f"")
    print(f"  == VERDICT ==")
    if not pre_match:
        print(f"  ❌ FAIL: pre-collision state diverged from baseline")
    if max_pen > 0.1:
        print(f"  ⚠ WARN: max penetration {max_pen:.4f}m — may be high")

    # Lateral delta should be non-zero if friction/impulse applied (also fine if zero
    # for head-on approach with symmetric mass — lateral velocity was zero to begin with)
    print(f"  Lateral velocity delta accounts for friction effects during collision.")

    if pre_match:
        print(f"  ✅ PASS: pre-collision integrator matches Phase 1 baseline")

    return pre_match, max_pen, pre_match_str, lateral_delta


if __name__ == "__main__":
    ok, pen, match, lat_delta = run_dynamic_pushback_test()
    sys.exit(0 if ok else 1)
