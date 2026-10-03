"""
verify_task6_chase.py — TASK VI: carrier-past-line diagonal chase verifier.

Reproduces the reported symptom head-on:
    Defenders AHEAD of bc.x (d.x > bc.x) slide laterally (vy) but stop
    forward (vx ~ 0) until alignment completes.

Asserts the full fix chain once bc.x > anchored_line + 1.0:
  1. MMDL decide_movement_state -> CHASE (never SLIDE) for engaged
     PRIMARY/SECONDARY defenders ahead of the carrier.
  2. The decision LOCK cannot suppress CHASE (locked SLIDE is overridden).
  3. PASSIVE depth still does NOT get the override (line holders unchanged).
  4. CHASE is excluded from the target stabiliser (raw targets reach
     apply_movement_state -> CHASE lateral_priority 0.3 -> non-zero vx+vy).
  5. CHASE bypasses the post-catch defensive-line clamp at the Player level
     (a SLIDE defender is still clamped; a CHASE defender is not).
  6. CHASE uses the accel-lerp path, not smooth-damp (vx > 0 after one step).

Run:  python verify_task6_chase.py
Exit 0 on success; prints a PASS/FAIL line per assertion.
"""
import math
import sys
import types

from player.player import Player
from player.movement_states import MovementState
from game.defence.defence_lvl_1.movement_mode_decision import (
    decide_movement_state,
)


def make_world(ruck_x=40.0, bc_x=52.0, frame=0):
    """Bare world: ruck_x=40 -> anchor=ruck_x+10=50.  bc_x=52 > 51 = past."""
    w = types.SimpleNamespace(
        ruck_x_m=ruck_x,
        tes_active=False,
        frame_count=frame,
        defensive_line_anchor=ruck_x + 10.0,
        anchor_locked=False,
        ball_carrier=None,
        sub_state=None,
        dt=1.0 / 60.0,
    )
    bc = Player(bc_x, 35.0, player_id=999)
    w.ball_carrier = bc
    return w, bc


def make_defender(x, y, depth_class, movement_state, player_id):
    d = Player(x, y, player_id=player_id)
    d.is_defender = True
    d.max_defensive_speed_mps = 8.3
    d.depth_class = depth_class
    d.movement_state = movement_state
    d._mmdl_state_until_frame = -1
    return d


failures = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def main():
    # ── 1. Engaged defender AHEAD of bc.x, BC past anchor -> CHASE ──
    world, bc = make_world()
    ahead = make_defender(x=55.0, y=33.0, depth_class="PRIMARY",
                          movement_state=MovementState.SLIDE, player_id=1)
    # Arranged to hit the SLIDE branch *without* the fix:
    #   lateral = |33-35| = 2 < threshold(3) -> would SLIDE only via
    #   history; instead ensure lateral > threshold to prove the override
    #   beats the pure SLIDE branch, and the override (bc past line) wins.
    ahead.y = 31.0  # lateral = 4 > 3.0, dist < engage (20) -> SLIDE branch
    state = decide_movement_state(world, ahead, bc, tuners=None)
    check("engaged ahead-dominant defender forced to CHASE (not SLIDE)",
          state == MovementState.CHASE, f"got {state}")

    # ── 2. Locked SLIDE cannot suppress the carrier-past override ──
    world2, bc2 = make_world(bc_x=52.0)
    locked = make_defender(x=56.0, y=31.0, depth_class="SECONDARY",
                           movement_state=MovementState.SLIDE, player_id=2)
    locked._mmdl_state_until_frame = world2.frame_count + 100  # active lock
    state2 = decide_movement_state(world2, locked, bc2, tuners=None)
    check("decision lock does not suppress CHASE once BC is past the line",
          state2 == MovementState.CHASE, f"got {state2}")

    # ── 3. PASSIVE is never overridden ──
    passive = make_defender(x=55.0, y=31.0, depth_class="PASSIVE",
                            movement_state=MovementState.DRIFT, player_id=3)
    state3 = decide_movement_state(world, passive, bc, tuners=None)
    check("PASSIVE line holder is NOT forced to CHASE",
          state3 != MovementState.CHASE, f"got {state3}")

    # ── 4. Stabiliser excludes CHASE (raw targets reach the accelerator) ──
    from player.target_stabiliser import should_stabilise_target
    chaser = make_defender(x=55.0, y=31.0, depth_class="PRIMARY",
                           movement_state=MovementState.CHASE, player_id=4)
    chaser.is_defender = True
    check("CHASE is excluded from the target stabiliser",
          should_stabilise_target(chaser) is False)

    # ── 5. CHASE bypasses the post-catch defensive-line clamp ──
    dt = 1.0 / 60.0
    # SLIDE defender: clamped.
    slide_d = make_defender(x=55.0, y=31.0, depth_class="SECONDARY",
                            movement_state=MovementState.SLIDE, player_id=5)
    slide_d.max_defensive_speed_mps = 8.3
    slide_d.defended_cap_remaining_s = 1.0
    slide_d.defended_speed_cap_mps = 4.0
    slide_d.move_speed = 8.3
    slide_d.target_x = 55.0
    slide_d.target_y = 28.0  # pure lateral target
    slide_d._accelerate_toward_target(dt)
    slide_speed = math.hypot(slide_d.vx, slide_d.vy)
    check("SLIDE defender still clamped by the defensive-line clamp",
          slide_speed <= 4.0 + 1e-9,
          f"speed={slide_speed:.3f} (cap 4.0)")

    # CHASE defender AHEAD of the BC (d.x=55 > bc.x=52) converging on the
    # carrier: X target between the BC and the line (bc.x-1 ~ 51), Y toward
    # the carrier lane (33).  One accel-lerp step only reaches
    # accel_scale * speed, so run enough frames for the speed to converge —
    # only then can the clamp bypass (> 4.0) vs clamp applied (<= 4.0) be
    # distinguished.
    chase_d = make_defender(x=55.0, y=31.0, depth_class="PRIMARY",
                            movement_state=MovementState.CHASE, player_id=6)
    chase_d.max_defensive_speed_mps = 8.3
    chase_d.defended_cap_remaining_s = 1.0
    chase_d.defended_speed_cap_mps = 4.0
    chase_d.move_speed = 8.3
    chase_d.target_x = 50.0   # forward BC-relative point (BC past the line at 52)
    chase_d.target_y = 33.0   # lateral correction toward BC lane
    # Just a few frames: the accel-lerp factor is 0.45/frame, so 8 frames
    # reach ~8.3*(1-0.55^8) ~ 7.9 m/s while the player is still mid-pursuit
    # (moves only ~0.5 m toward the target — never "arrived", which would
    # snap velocity to zero and make the clamp-bypass indistinguishable).
    for _ in range(8):
        chase_d._accelerate_toward_target(dt)
    chase_speed = math.hypot(chase_d.vx, chase_d.vy)
    check("CHASE defender bypasses the clamp (pursuit speed kept)",
          chase_speed > 4.0,
          f"speed={chase_speed:.3f} (cap 4.0)")

    # ── 6. CHASE produces non-zero vx + vy (diagonal pursuit) ──
    # The sign of vx follows geometry: an ahead-defender converges TOWARD
    # the carrier (lower X), a behind-defender chases +X.  Diagonal chase
    # means BOTH axes are non-zero — SLIDE (the bug) suppresses vx entirely.
    check("CHASE acceleration produces non-zero vx (toward target)",
          abs(chase_d.vx) > 0.0, f"vx={chase_d.vx:.4f}")
    # The spread between the chaser's lane (31) and target lane (33) gives vy.
    check("CHASE acceleration produces lateral vy != 0",
          abs(chase_d.vy) > 0.0, f"vy={chase_d.vy:.4f}")

    # ── 7. CHASE uses the accel-lerp path, not smooth_damp ──
    check("CHASE takes the accel-lerp path (no smooth_damp)",
          chase_d._uses_smooth_damp() is False)

    if failures:
        print(f"\n{len(failures)} assertion(s) FAILED: {failures}")
        sys.exit(1)
    print("\nAll TASK VI chase-override assertions PASSED.")
    sys.exit(0)


if __name__ == "__main__":
    main()
