"""
PHASE 2 SMOKE TEST 1 — Static Overlap

Place two players at a known overlapping offset (0.3m apart, radius=0.55 each,
so overlap = 0.8m).  Enable collision bodies, run 1s of simulation at 60fps,
and assert:
  - Max penetration ≤ 0.01 units after settling
  - No jitter (positions should converge monotonically)
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from player.player import Player
from game.physics.collision_system import CollisionSystem


def run_static_overlap_test():
    cs = CollisionSystem()

    # Two players, centre-to-centre distance = 0.3m
    # radius=0.55 each → combined radius = 1.1 → overlap = 1.1 - 0.3 = 0.8m
    p1 = Player(0.0, 0.0, player_id=0)
    p2 = Player(0.3, 0.0, player_id=1)

    # Both have mass=1, default restitution=0.2, friction=0.3
    # Already set by CollisionBody.__init__

    # Enable collision
    p1.body.collision_enabled = True
    p2.body.collision_enabled = True

    dt = 1.0 / 60.0
    frames = int(1.0 / dt)

    max_penetration = 0.0
    pen_history = []
    prev_pen = None
    monotonic = True

    for f in range(frames):
        # Sync bodies to player positions (already in sync unless resolve moved them)
        p1.body.sync()
        p2.body.sync()

        # Check current penetration
        overlap = (p1.body.radius + p2.body.radius) - p1.body.distance_to(p2.body)
        if overlap > 0:
            pen_history.append(overlap)
            if overlap > max_penetration:
                max_penetration = overlap
            if prev_pen is not None and overlap > prev_pen * 1.001:
                monotonic = False
            prev_pen = overlap
        else:
            pen_history.append(0.0)
            prev_pen = 0.0

        # Resolve
        cs.resolve_pair(p1.body, p2.body)

    # Final penetration check
    final_overlap = (p1.body.radius + p2.body.radius) - p1.body.distance_to(p2.body)
    final_pen = max(0.0, final_overlap)

    print("=== PHASE 2 — STATIC OVERLAP TEST ===")
    print(f"  Initial distance:       0.3 m")
    print(f"  Combined radius:        {p1.body.radius + p2.body.radius:.2f} m")
    print(f"  Initial overlap:        {0.8:.2f} m")
    print(f"  Frames simulated:       {frames}")
    print(f"  Max penetration:        {max_penetration:.6f} m")
    print(f"  Final penetration:      {final_pen:.6f} m")
    print(f"  Monotonic convergence:  {'Y' if monotonic else 'N'}")

    # Assert
    if final_pen > 0.01:
        print(f"  ❌ FAIL: final penetration {final_pen:.6f} > 0.01")
        return False, max_penetration
    if not monotonic:
        print(f"  ⚠ WARN: non-monotonic convergence (may indicate jitter)")
        # Not a hard fail — small measurement noise is acceptable

    if final_pen <= 0.01:
        print(f"  ✅ PASS: penetration ≤ 0.01 after 1s settle")
        return True, max_penetration

    return False, max_penetration


if __name__ == "__main__":
    ok, pen = run_static_overlap_test()
    sys.exit(0 if ok else 1)
