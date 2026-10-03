"""
verify_secondary_width_grace.py — Stage 2 SECONDARY width-eligibility grace.

Verifies ``secondary_width_grace`` + the grace-aware ``width_authority``
predicate + ``recompute_width_x`` integration:

  1. FRESH PROMOTION: a ruck-adjacent PASSIVE->SECONDARY transition arms
     the grace (timer == secondary_grace_duration_s), telemetry
     ``world.secondary_grace_applied_count`` increments.
  2. WIDTH ELIGIBILITY DURING GRACE: ``is_width_eligible(d, world, tuners)``
     returns True for the graced SECONDARY defender.
  3. WIDTH ELIGIBILITY AFTER GRACE: once the timer expires (advance past
     the window), the base predicate resumes — SECONDARY is again exempt.
  4. RUCK DISPLACEMENT INVALIDATION: moving the ruck beyond
     secondary_grace_invalid_ruck_move_m clears the grace.
  5. EXPLICIT RELEASE INVALIDATION: RELEASE phase clears the grace.
  6. FLAG-OFF: secondary_grace_enabled=False produces NO arm and the base
     predicate (SECONDARY exempt) is unchanged.
  7. PERSISTENT TELEMETRY: when a grace expires while the defender is
     still SECONDARY, ``world.secondary_grace_persistent_count``
     increments and the grace is NOT re-armed.
  8. WIDTH RECOMPUTE: with the grace active, ``recompute_width_x`` emits a
     lane X target for the graced SECONDARY defender (the micro-gap fix);
     after expiry it emits none.

Exit 0 = PASS, exit 1 = FAIL.
"""

import sys
import types

from player.player import Player, DefenderRole
from world.world_state import GameSubState
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.secondary_width_grace import (
    arm_secondary_grace,
    advance_secondary_grace,
    secondary_grace_active,
    is_grace_phase,
    is_explicit_release,
    is_ruck_adjacent,
)
from game.defence.defence_lvl_1.width_authority import is_width_eligible
from game.defence.defence_lvl_1.defensive_width import recompute_width_x

failures = []


# ─────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────

def make_defender(x, y, pid, world, depth_role="A", depth_side=-1,
                  defender_role=DefenderRole.LINE, depth_class="PASSIVE"):
    """Build a Player carrying the depth-class / depth-role used by the grace."""
    d = Player(x, y, pid)
    d._world_ref = world
    d.vx = 0.0
    d.vy = 0.0
    d.max_speed_mps = 9.2
    d.move_speed = 9.2
    d.frozen = False
    d.freeze_timer = 0.0
    d.target_x = x
    d.target_y = y
    d.depth_role = depth_role
    d.depth_side = depth_side
    d.frame_side = depth_side
    d.defender_role = defender_role
    d.fullback_role_active = False
    d.tackle_state_active = False
    d.frame_spacing_y = None
    d.depth_class = depth_class
    return d


def make_world(sub_state=GameSubState.OPEN_PLAY, ruck_y=35.0, ruck_x=60.0,
               ruck_established=True):
    """Minimal world namespace carrying the ruck + sub-state + dt."""
    return types.SimpleNamespace(
        ball_carrier=None,
        defending_team=[],
        attacking_team=[],
        sub_state=sub_state,
        frame_count=0,
        ruck_x_m=ruck_x,
        ruck_y_m=ruck_y,
        defensive_line_anchor=ruck_x + 10.0,
        def_line_width_m=40.0,
        governor_width_active=False,
        tes_active=False,
        ruck_established=ruck_established,
        dt=1.0 / 60.0,
    )


def approx(a, b, tol=1e-6):
    return abs(float(a) - float(b)) <= tol


def report(msg):
    failures.append(msg)


# ─────────────────────────────────────────────────────────────────────
# TEST 1 — FRESH PROMOTION ARMS THE GRACE
# ─────────────────────────────────────────────────────────────────────

def test_fresh_promotion_arms():
    tuners = DefenceTuners()
    tuners.secondary_grace_enabled = True
    world = make_world(GameSubState.OPEN_PLAY)  # grace phase
    d = make_defender(70.0, 33.0, 1, world, "A", -1)
    d.depth_class = "PASSIVE"

    # Frame 1 (first sight): record snapshot, NO arm.
    arm_secondary_grace(world, [d], tuners)
    if getattr(d, "_secondary_grace_remaining_s", 0.0) != 0.0:
        report("FRESH-PROMOTION: first-sight must NOT arm (no history)")
    if getattr(world, "secondary_grace_applied_count", 0) != 0:
        report("FRESH-PROMOTION: first-sight must NOT increment applied_count")
    if getattr(d, "_secondary_grace_prev_class", None) != "PASSIVE":
        report("FRESH-PROMOTION: first-sight must record prev_class=PASSIVE")

    # Frame 2 (fresh promotion while ruck-adjacent): arm.
    d.depth_class = "SECONDARY"
    arm_secondary_grace(world, [d], tuners)
    rem = getattr(d, "_secondary_grace_remaining_s", 0.0)
    if not approx(rem, float(tuners.secondary_grace_duration_s)):
        report(
            f"FRESH-PROMOTION: grace remaining={rem} != "
            f"duration={tuners.secondary_grace_duration_s}"
        )
    if getattr(world, "secondary_grace_applied_count", 0) != 1:
        report("FRESH-PROMOTION: applied_count must be 1 after one arm")
    if not secondary_grace_active(d, world, tuners):
        report("FRESH-PROMOTION: secondary_grace_active False after arm")
    if not is_ruck_adjacent(d, world, tuners):
        report("FRESH-PROMOTION: ruck-adjacent predicate False for y=33 ruck=35")


# ─────────────────────────────────────────────────────────────────────
# TEST 2 — WIDTH ELIGIBILITY DURING GRACE
# ─────────────────────────────────────────────────────────────────────

def test_width_eligibility_during_grace():
    tuners = DefenceTuners()
    tuners.secondary_grace_enabled = True
    world = make_world(GameSubState.OPEN_PLAY)
    d = make_defender(70.0, 33.0, 2, world, "A", -1, depth_class="PASSIVE")
    arm_secondary_grace(world, [d], tuners)  # first sight
    d.depth_class = "SECONDARY"
    arm_secondary_grace(world, [d], tuners)  # arms

    # Base predicate (no world/tuners) -> SECONDARY exempt -> False.
    if is_width_eligible(d):
        report("WIDTH-ELIG: base is_width_eligible(d) must be False for SECONDARY")
    # Grace-aware predicate -> True.
    if not is_width_eligible(d, world, tuners):
        report("WIDTH-ELIG: grace-aware is_width_eligible(d, world, tuners) must be True")


# ─────────────────────────────────────────────────────────────────────
# TEST 3 — WIDTH ELIGIBILITY REVERTS AFTER GRACE EXPIRY
# ─────────────────────────────────────────────────────────────────────

def test_grace_expiry_reverts():
    tuners = DefenceTuners()
    tuners.secondary_grace_enabled = True
    world = make_world(GameSubState.OPEN_PLAY)
    d = make_defender(70.0, 33.0, 3, world, "A", -1, depth_class="PASSIVE")
    arm_secondary_grace(world, [d], tuners)
    d.depth_class = "SECONDARY"
    arm_secondary_grace(world, [d], tuners)

    # Advance ~1.0 s (60 frames at dt=1/60) -> grace expires.
    for _ in range(61):
        advance_secondary_grace(world, [d], tuners)
        d._secondary_grace_prev_class = "SECONDARY"  # stay engaged
    rem = getattr(d, "_secondary_grace_remaining_s", 0.0)
    if rem > 0.0:
        report(f"GRACE-EXPIRY: remaining={rem} must be <= 0 after 61 frames")
    if secondary_grace_active(d, world, tuners):
        report("GRACE-EXPIRY: secondary_grace_active must be False after expiry")
    if is_width_eligible(d, world, tuners):
        report("GRACE-EXPIRY: grace-aware eligibility must revert to False after expiry")


# ─────────────────────────────────────────────────────────────────────
# TEST 4 — RUCK DISPLACEMENT INVALIDATION
# ─────────────────────────────────────────────────────────────────────

def test_ruck_displacement_invalidation():
    tuners = DefenceTuners()
    tuners.secondary_grace_enabled = True
    world = make_world(GameSubState.OPEN_PLAY, ruck_y=35.0, ruck_x=60.0)
    d = make_defender(70.0, 33.0, 4, world, "A", -1, depth_class="PASSIVE")
    arm_secondary_grace(world, [d], tuners)
    d.depth_class = "SECONDARY"
    arm_secondary_grace(world, [d], tuners)
    # Grace active.
    if not secondary_grace_active(d, world, tuners):
        report("RUCK-DISPLACE: grace not active before ruck move")

    # Move ruck_y beyond invalid threshold (invalid=2.0).
    world.ruck_y_m = 35.0 + 3.0
    advance_secondary_grace(world, [d], tuners)
    rem = getattr(d, "_secondary_grace_remaining_s", 0.0)
    if rem > 0.0:
        report(f"RUCK-DISPLACE: grace not cleared (remaining={rem})")
    if secondary_grace_active(d, world, tuners):
        report("RUCK-DISPLACE: secondary_grace_active must be False after ruck move")
    if is_width_eligible(d, world, tuners):
        report("RUCK-DISPLACE: grace-aware eligibility must be False after ruck move")


# ─────────────────────────────────────────────────────────────────────
# TEST 5 — EXPLICIT RELEASE INVALIDATION
# ─────────────────────────────────────────────────────────────────────

def test_explicit_release_invalidation():
    tuners = DefenceTuners()
    tuners.secondary_grace_enabled = True
    world = make_world(GameSubState.OPEN_PLAY, ruck_y=35.0)
    d = make_defender(70.0, 33.0, 5, world, "A", -1, depth_class="PASSIVE")
    arm_secondary_grace(world, [d], tuners)
    d.depth_class = "SECONDARY"
    arm_secondary_grace(world, [d], tuners)
    if not secondary_grace_active(d, world, tuners):
        report("RELEASE-INVALIDATE: grace not active before RELEASE")

    # Transition to RELEASE (TACKLE_RELEASE sub-state).
    world.sub_state = GameSubState.TACKLE_RELEASE
    if not is_explicit_release(world):
        report("RELEASE-INVALIDATE: is_explicit_release must be True for TACKLE_RELEASE")
    advance_secondary_grace(world, [d], tuners)
    rem = getattr(d, "_secondary_grace_remaining_s", 0.0)
    if rem > 0.0:
        report(f"RELEASE-INVALIDATE: grace not cleared (remaining={rem})")


# ─────────────────────────────────────────────────────────────────────
# TEST 6 — FLAG OFF
# ─────────────────────────────────────────────────────────────────────

def test_flag_off():
    tuners = DefenceTuners()
    tuners.secondary_grace_enabled = False  # canary off
    world = make_world(GameSubState.OPEN_PLAY)
    d = make_defender(70.0, 33.0, 6, world, "A", -1, depth_class="PASSIVE")
    arm_secondary_grace(world, [d], tuners)
    d.depth_class = "SECONDARY"
    arm_secondary_grace(world, [d], tuners)
    if getattr(world, "secondary_grace_applied_count", 0) != 0:
        report("FLAG-OFF: applied_count must be 0 when secondary_grace_enabled=False")
    if getattr(d, "_secondary_grace_remaining_s", 0.0) != 0.0:
        report("FLAG-OFF: grace remaining must be 0 when flag off")
    if secondary_grace_active(d, world, tuners):
        report("FLAG-OFF: secondary_grace_active must be False when flag off")
    if is_width_eligible(d, world, tuners):
        report("FLAG-OFF: grace-aware eligibility must be False when flag off (SECONDARY exempt)")

    # advance with flag off clears any stale timer.
    d._secondary_grace_remaining_s = 0.5
    d.depth_class = "SECONDARY"
    advance_secondary_grace(world, [d], tuners)
    if getattr(d, "_secondary_grace_remaining_s", 0.0) != 0.0:
        report("FLAG-OFF: advance must clear stale timer when flag off")


# ─────────────────────────────────────────────────────────────────────
# TEST 7 — PERSISTENT TELEMETRY (no re-arm after expiry)
# ─────────────────────────────────────────────────────────────────────

def test_persistent_telemetry():
    tuners = DefenceTuners()
    tuners.secondary_grace_enabled = True
    world = make_world(GameSubState.OPEN_PLAY)
    d = make_defender(70.0, 33.0, 7, world, "A", -1, depth_class="PASSIVE")
    arm_secondary_grace(world, [d], tuners)
    d.depth_class = "SECONDARY"
    arm_secondary_grace(world, [d], tuners)
    applied_after_arm = getattr(world, "secondary_grace_applied_count", 0)

    # Expire the grace while staying SECONDARY.
    for _ in range(31):
        advance_secondary_grace(world, [d], tuners)
        d._secondary_grace_prev_class = "SECONDARY"
    if getattr(world, "secondary_grace_persistent_count", 0) != 1:
        report(
            "PERSISTENT: persistent_count must be 1 after grace expiry while "
            f"still SECONDARY (got {getattr(world, 'secondary_grace_persistent_count', 0)})"
        )
    if not getattr(d, "_secondary_grace_expired", False):
        report("PERSISTENT: _secondary_grace_expired must be True after expiry")

    # Attempt re-arm while still SECONDARY + expired -> must NOT re-arm.
    arm_secondary_grace(world, [d], tuners)
    if getattr(world, "secondary_grace_applied_count", 0) != applied_after_arm:
        report("PERSISTENT: grace must NOT re-arm after expiry (persistence suppression)")


# ─────────────────────────────────────────────────────────────────────
# TEST 8 — WIDTH RECOMPUTE EMITS LANE X DURING GRACE
# ─────────────────────────────────────────────────────────────────────

def test_width_recompute():
    tuners = DefenceTuners()
    tuners.secondary_grace_enabled = True
    world = make_world(GameSubState.OPEN_PLAY, ruck_x=60.0, ruck_y=35.0)
    d = make_defender(62.0, 33.0, 8, world, "A", -1, depth_class="PASSIVE")
    world.defending_team = [d]
    # assign a depth_role so lane X is defined (A offset 0.0 -> lane=ruck_x).
    d.depth_role = "A"

    # First sight, then fresh promotion (arms).
    arm_secondary_grace(world, [d], tuners)
    d.depth_class = "SECONDARY"
    arm_secondary_grace(world, [d], tuners)
    if not secondary_grace_active(d, world, tuners):
        report("WIDTH-RECOMPUTE: grace not active before recompute")

    # With grace active, recompute emits a lane X target.
    targets = recompute_width_x(world, [d], tuners)
    if id(d) not in targets:
        report("WIDTH-RECOMPUTE: no lane X target for graced SECONDARY (micro-gap fix)")
    else:
        exp = 60.0 + float(tuners.WIDTH_OFFSETS["A"])  # ruck_x + 0.0
        if not approx(targets[id(d)], exp):
            report(
                f"WIDTH-RECOMPUTE: lane X={targets[id(d)]} != expected={exp}"
            )

    # Expire the grace -> no target.
    for _ in range(31):
        advance_secondary_grace(world, [d], tuners)
    targets2 = recompute_width_x(world, [d], tuners)
    if id(d) in targets2:
        report("WIDTH-RECOMPUTE: lane X target emitted after grace expiry (must be exempt)")


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────

def main():
    test_fresh_promotion_arms()
    test_width_eligibility_during_grace()
    test_grace_expiry_reverts()
    test_ruck_displacement_invalidation()
    test_explicit_release_invalidation()
    test_flag_off()
    test_persistent_telemetry()
    test_width_recompute()

    if failures:
        print("VERIFY_SECONDARY_WIDTH_GRACE: FAIL")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("VERIFY_SECONDARY_WIDTH_GRACE: PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
