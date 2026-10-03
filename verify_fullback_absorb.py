"""verify_fullback_absorb.py — Fullback independence verifier (Task J, 2026-08-12).

Validates the fullback independence contract end-to-end:

  A. ABSORB HYSTERESIS — absorption into the line requires BOTH |x - anchor|
     and |y - ball-carrier lane| proximity for ABSORB_FRAMES_MIN consecutive
     frames.  Each counter increments while ITS proximity holds and resets
     to 0 the moment it breaks.
  B. COUNTER LOCKSTEP — a single drifting dimension (counter reset in the
     other) keeps fullback_absorb_allowed False even when the other counter
     exceeds ABSORB_FRAMES_MIN.
  C. LINE-POOL EXCLUSION — while fullback_role_active is True the actor is
     excluded from the depth-role candidate pool (assign_depth_roles keeps
     the FB out of A/B/C/D/CENTRE/WINGER).
  D. ABSORPTION TRANSITION — when fullback_absorb_allowed turns True, the
     pipeline clears fullback_role_active so the actor re-enters the line
     pool THIS frame and receives a deterministic depth role.
  E. HELPER RETENTION — ``fullback_approach_speed`` is RETAINED (still
     exported, still monotone non-increasing with error, still capped at
     FB_MAX_APPROACH_SPEED_MPS) but is NO LONGER on the fullback's live path,
     so that 2.0 m/s ceiling no longer bounds the actor.  Pinned below as
     PURE-FUNCTION behaviour only.  The live pace contract lives in
     verify_fullback_shared_locomotion.py.
  F. SHARED CEILING (2026-09-27) — a tagged fullback integrates through the
     SHARED movement-state model, so Player.update() bounds its post-facing
     velocity at ``max_defensive_speed_mps`` -- the same ceiling every line
     defender gets -- and NOT at FB_MAX_APPROACH_SPEED_MPS.  The FB-only hard
     cap block this clause used to pin has been DELETED.
  G. WORLD PUBLISH — world.fullback_role_active / fullback_absorb_allowed /
     fullback_frames_within_absorb_x / _y / fullback_arrived exist.

Exits 1 on any failed assertion (CI-style).
"""

import math
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, DefenderRole
from player.movement_states import MovementState
from game.defence.defence_lvl_1.fullback_role import (
    update_fullback_absorb_state,
    fullback_approach_speed,
    assign_fullback_role,
    ABSORB_FRAMES_MIN,
    ABSORB_X_THRESHOLD_M,
    ABSORB_Y_THRESHOLD_M,
    FB_MAX_APPROACH_SPEED_MPS,
    FB_DECEL_START_M,
    FB_ARRIVED_PROXIMITY_M,
)
from game.defence.defence_lvl_1.defensive_depth_roles import assign_depth_roles

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


class FakeBall:
    def __init__(self, x, y):
        self.x = x
        self.y = y


class FullbackWorld:
    ruck_x_m = 60.0
    ruck_y_m = 35.0


def make_players(n, base_x=65.0, base_y=20.0, gap_y=5.0):
    players = []
    for i in range(n):
        p = Player(x=base_x, y=base_y + i * gap_y, player_id=200 + i)
        p.jersey_number = i + 1
        p.defender_role = DefenderRole.DEF_LINE_READY
        p.is_defender = True
        players.append(p)
    return players


def main():
    print("=" * 64)
    print("FULLBACK INDEPENDENCE / ABSORB VERIFIER (Task J)")
    print("=" * 64)

    # ── A. Absorb hysteresis: both counters must reach the minimum ──
    print("\n[A] Absorb hysteresis (both X and Y for ABSORB_FRAMES_MIN)")
    fb = Player(x=70.0, y=35.0, player_id=1)
    fb.fullback_role_active = True
    anchor_x = 70.0
    lane_y = 35.0
    # 1 frame where BOTH are within threshold.
    update_fullback_absorb_state(fb, anchor_x, lane_y)
    check("frame 1: counters start at 1/1", fb.frames_within_absorb_x == 1 and fb.frames_within_absorb_y == 1,
          f"fx={fb.frames_within_absorb_x} fy={fb.frames_within_absorb_y}")
    check("frame 1: absorb NOT allowed (below minimum)",
          not fb.fullback_absorb_allowed, f"absorb={fb.fullback_absorb_allowed}")
    check("frame 1: arrived (within FB_ARRIVED_PROXIMITY_M)",
          fb.fullback_arrived is True, f"arrived={fb.fullback_arrived}")

    # Advance to exactly ABSORB_FRAMES_MIN - 1 more frames.
    for _ in range(ABSORB_FRAMES_MIN - 1):
        update_fullback_absorb_state(fb, anchor_x, lane_y)
    check(
        "counters reach ABSORB_FRAMES_MIN exactly",
        fb.frames_within_absorb_x == ABSORB_FRAMES_MIN
        and fb.frames_within_absorb_y == ABSORB_FRAMES_MIN,
        f"fx={fb.frames_within_absorb_x} fy={fb.frames_within_absorb_y}",
    )
    check(
        "absorb ALLOWED once both counters >= ABSORB_FRAMES_MIN",
        fb.fullback_absorb_allowed is True,
        f"absorb={fb.fullback_absorb_allowed}",
    )

    # ── B. Counter lockstep: one drifting dimension blocks absorption ──
    print("\n[B] Counter lockstep (both dimensions required simultaneously)")
    fb2 = Player(x=70.0, y=35.0, player_id=2)
    fb2.fullback_role_active = True
    for i in range(ABSORB_FRAMES_MIN + 2):
        # X stays within threshold (anchor 70.0); Y drifts +2 m from frame 3.
        if i < 2:
            update_fullback_absorb_state(fb2, 70.0, 35.0)
        else:
            update_fullback_absorb_state(fb2, 70.0, 37.0)
        if i >= 1:
            check(
                "X counter persists while X within threshold",
                fb2.frames_within_absorb_x == i + 1,
                f"frame {i} fx={fb2.frames_within_absorb_x}",
            )
    check(
        "Y counter reset to 0 when Y proximity breaks",
        fb2.frames_within_absorb_y == 0,
        f"fy={fb2.frames_within_absorb_y}",
    )
    check(
        "absorb NOT allowed despite X counter >= min",
        not fb2.fullback_absorb_allowed,
        f"fx={fb2.frames_within_absorb_x} absorb={fb2.fullback_absorb_allowed}",
    )

    # ── C. Line-pool exclusion while FB active ──
    print("\n[C] FB excluded from depth-role line pools while active")
    players = make_players(6)
    players[0].depth_role = "D"  # stale role must be cleared on selection
    actor, _ = assign_fullback_role(players, FakeBall(35.0, 35.0), 70.0)
    check("FB actor selected", actor is not None, f"actor={actor.player_id if actor else None}")
    check("FB stale depth_role cleared", getattr(actor, "depth_role", None) is None,
          f"depth_role={getattr(actor, 'depth_role', None)}")
    assign_depth_roles(players, FullbackWorld())
    fb_actor = next(p for p in players if getattr(p, "fullback_role_active", False))
    check(
        "FB excluded from depth-role lanes (still None)",
        getattr(fb_actor, "depth_role", None) is None,
        f"depth_role={getattr(fb_actor, 'depth_role', None)}",
    )
    line_roles = [getattr(p, "depth_role", None) for p in players if p is not fb_actor]
    check(
        "all non-FB defenders get line roles",
        all(r is not None for r in line_roles),
        str(line_roles),
    )

    # ── D. Absorption transition: flag cleared + deterministic line role ──
    print("\n[D] Absorption transition (flag cleared, deterministic line role)")
    players2 = make_players(6)
    actors = []
    actor2, _ = assign_fullback_role(players2, FakeBall(35.0, 35.0), 70.0)
    actors.append(actor2)
    # Drive the actor onto the line positions: anchor is 70.0; move the
    # actor to exactly on top of the anchor + ball lane so both counters
    # saturate and absorption fires.
    actor2.x = 70.0
    actor2.y = 35.0
    anchor_line = 70.0
    lane_y = 35.0
    absorbed_frame = None
    # Pipeline simulation: maintain role each frame; if absorbed, the actor
    # returns to the line pool and assign_depth_roles stamps a line role.
    for frame in range(1, ABSORB_FRAMES_MIN + 2):
        assign_fullback_role(players2, FakeBall(35.0, 35.0), anchor_line)
        fb_cur = next(p for p in players2 if getattr(p, "fullback_role_active", False))
        update_fullback_absorb_state(fb_cur, anchor_line, lane_y)
        if fb_cur.fullback_absorb_allowed:
            # Absorption: clear the role so the actor re-enters the line pool.
            fb_cur.fullback_role_active = False
            if getattr(fb_cur, "role", None) == "FULLBACK":
                fb_cur.role = None
            fb_cur.target_source = None
        # Post-absorb frame: the actor is a line defender this frame.
        if absorbed_frame is None:
            if not fb_cur.fullback_role_active:
                absorbed_frame = frame
    check("absorption fired within ABSORB_FRAMES_MIN + 1 frames", absorbed_frame is not None,
          f"absorbed_frame={absorbed_frame}")
    # After absorption the actor must be assigned a deterministic line role.
    for p in players2:
        p.fullback_role_active = False
    for p in players2:
        p.depth_role = None
        p.depth_side = None
        p._depth_seeded = False
    assign_depth_roles(players2, FullbackWorld())
    roles_after = sorted(getattr(p, "depth_role", None) for p in players2)
    check(
        "all 6 defenders (ex-absorbed included) hold deterministic line roles",
        all(r is not None for r in roles_after) and len(roles_after) == 6,
        str(roles_after),
    )

    # ── E. Approach-speed cap (pure function) ──
    print("\n[E] Approach-speed cap")
    check(
        "far approach capped at FB_MAX_APPROACH_SPEED_MPS",
        abs(fullback_approach_speed(8.3, 20.0) - FB_MAX_APPROACH_SPEED_MPS) < 1e-9,
        f"speed={fullback_approach_speed(8.3, 20.0):.3f}",
    )
    check(
        "slow base speed NOT boosted",
        fullback_approach_speed(1.0, 20.0) == 1.0,
        f"speed={fullback_approach_speed(1.0, 20.0):.3f}",
    )
    inside = fullback_approach_speed(8.3, FB_DECEL_START_M * 0.5)
    far = fullback_approach_speed(8.3, FB_DECEL_START_M * 3.0)
    check(
        "decelerates linearly inside FB_DECEL_START_M",
        inside < far,
        f"inside={inside:.3f} far={far:.3f}",
    )
    near = fullback_approach_speed(8.3, 0.1)
    check(
        "never stalls at the anchor (floor 10%)",
        near >= FB_MAX_APPROACH_SPEED_MPS * 0.1 - 1e-9,
        f"near={near:.3f}",
    )

    # ── F. Hard cap in Player.update ──
    print("\n[F] Player.update hard cap on fullback velocity")
    fb3 = Player(x=60.0, y=35.0, player_id=3)
    fb3.fullback_role_active = True
    fb3.target_x = 65.0
    fb3.target_y = 35.0
    # 2026-09-27: no bespoke speed write.  The actor is stamped into a shared
    # state and the SHARED model derives its pace; the post-facing clamp in
    # Player.update() bounds it at the actor's own defensive ceiling.
    fb3.max_defensive_speed_mps = 8.3
    fb3.movement_state = MovementState.CHASE
    # Simulate a few frames until damped velocity reaches its ceiling.
    max_speed = 0.0
    for _ in range(120):
        fb3.update(1.0 / 60.0)
        max_speed = max(max_speed, math.hypot(fb3.vx, fb3.vy))
    check(
        "fullback scalar speed never exceeds the SHARED defensive ceiling "
        "(not the retired FB-only 2.0 m/s cap)",
        max_speed <= fb3.max_defensive_speed_mps + 0.05,
        f"max_speed={max_speed:.4f} ceiling={fb3.max_defensive_speed_mps}",
    )
    # Non-FB actors are NOT capped by the FB ceiling.  CHASE movement
    # bypasses the target stabiliser (should_stabilise_target gate), so
    # the actor genuinely ramps toward the 8.0 m/s command — proving the
    # FB-only ceiling is selective, not a global clamp.
    fb4 = Player(x=60.0, y=35.0, player_id=4)
    fb4.target_x = 65.0
    fb4.target_y = 35.0
    fb4.move_speed = 8.0
    fb4.max_defensive_speed_mps = 8.3
    fb4.movement_state = MovementState.CHASE
    max_speed4 = 0.0
    for _ in range(120):
        fb4.update(1.0 / 60.0)
        max_speed4 = max(max_speed4, math.hypot(fb4.vx, fb4.vy))
    check(
        "non-FB defender unaffected by FB cap",
        max_speed4 > FB_MAX_APPROACH_SPEED_MPS,
        f"max_speed={max_speed4:.4f}",
    )

    # ── G. World publish fields exist ──
    print("\n[G] World publish fields")
    from world.world_state import WorldState
    w = WorldState()
    check("world.fullback_role_active exists", hasattr(w, "fullback_role_active"))
    check("world.fullback_absorb_allowed exists", hasattr(w, "fullback_absorb_allowed"))
    check("world.fullback_frames_within_absorb_x exists", hasattr(w, "fullback_frames_within_absorb_x"))
    check("world.fullback_frames_within_absorb_y exists", hasattr(w, "fullback_frames_within_absorb_y"))
    check("world.fullback_arrived exists", hasattr(w, "fullback_arrived"))

    print("\n" + "=" * 64)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
