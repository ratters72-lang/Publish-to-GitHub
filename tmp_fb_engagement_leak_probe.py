"""tmp_fb_engagement_leak_probe.py -- THE BEATEN FULLBACK MUST NOT MARK THE SPOT.

THE DEFECT (proved here, then fixed behind RLM_FB_EMERGENCY_RELEASE_ON_MISS)
---------------------------------------------------------------------------
``OpenPlayEngine._arm_tackler_role`` sets ``_fb_emergency_engaged`` on the
tagged fullback and stamps ``defender_role = TACKLER_1``.  The only site that
ever cleared that pair was ``reset_fullback_emergency_engagement``, called
from the RELEASE-entry loop inside ``_clear_tes_artefacts``.

A BROKEN TACKLE NEVER ENTERS RELEASE.  ``_handle_broken_tackle`` ends with
``self._set_phase("OPEN_PLAY", ..., "BROKEN_TACKLE")``, and its two
normalisation sweeps BOTH ``continue`` on a tagged fullback (the actor is
deliberately excluded from the line-role AND the chase-reset writers).  So a
fullback the tackle armed kept the engagement marker and the TACKLER role for
the rest of the match -- and that leaked pair is exactly what the geometry
authority yields to:

    fullback_tackle_authority_active(fb) is True    (the marker)
    fullback_tackle_side_owner_live(fb)  is True    (the SAME marker)
      -> establish_fullback_live_break_chase() declines
      -> clear_static_pursuit_target() declines (the target is already a
         live chase point, not a static pin)
      -> apply_fullback_geometry() falls to a bare ``return``
      -> NO target write, NO state write, NO speed write

Because ``fullback_approach_cap_applies`` is the exact complement of the
authority flag, the governed ceiling is never restored either, so the body
coasts on whatever ``move_speed`` it last inherited.  The observable symptom
is the one predicted: the beaten fullback MARKS THE SPOT where the carrier
beat it and then makes a delayed, throttled pursuit, because
``fullback_tackle_authority_active`` keeps it trapped in the yield path and
prevents its transition into the normal live-break chase locomotion.

The engagement is TACKLE-CYCLE state, so the teardown that ends the cycle
must release it.  ``release_fullback_emergency_on_miss(world)`` does that for
the whole defending team, gated on the lever.

WHAT THIS PROBE ASSERTS (all read-only; no engine code is edited here)
---------------------------------------------------------------------
 1. baseline      -- marker absent, authority inactive, cap applies.
 2. armed         -- the real ``_arm_tackler_role`` sets the marker and role.
 3. post-miss     -- the teardown clears marker + role, KEEPS the tag, lands
                     in OPEN_PLAY without RELEASE, and releases the commit
                     latches.
 4. consequence   -- on the released body the authority is inactive, the cap
                     applies, and the LIVE-BREAK CHASE ESTABLISHMENT SUCCEEDS.
 5. geometry      -- END-TO-END: ``apply_fullback_geometry`` on a live DEPTH
                     break writes CHASE, the goal-side pursuit point, and the
                     governed ceiling (8.3) -- the chase locomotion the leak
                     was blocking.
 6. lever A/B     -- with ``RLM_FB_EMERGENCY_RELEASE_ON_MISS=0`` the marker
                     SURVIVES the teardown, the establishment declines, and
                     the geometry writer bare-returns leaving the target and
                     state untouched: the pre-change engine, restored.

Run:  python tmp_fb_engagement_leak_probe.py
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

import pygame

pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

from player.player import Player, DefenderRole
from player.movement_states import MovementState
from world.world_state import WorldState
from world.open_play_engine import OpenPlayEngine
from game.defence.defence_lvl_1 import fullback_role as fb

ANCHOR = 60.0        # defensive line anchor (ruck_x + 10)
RUCK_X = 50.0
CEILING = 8.3        # unified defensive ceiling
TRAIL = 1.0          # FB_CHASE_TRAIL_M

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("  [%s] %-60s %s" % ("PASS" if ok else "FAIL", name, detail))


def build_world():
    world = WorldState()
    world.frame_count = 1000
    world.ruck_x_m = RUCK_X
    world.ruck_y_m = 35.0
    world.defensive_line_anchor = ANCHOR
    world.anchor_locked = True
    world.tes_active = False
    world.in_kickoff = False
    world.defending_team = []
    world.attacking_team = []
    world.players = []
    try:
        world.tackler_ids = set()
    except Exception:
        pass
    return world


def make_fb(pid):
    d = Player(ANCHOR + 2.0, 35.0, pid)
    d.fullback_role_active = True
    d.role = "FULLBACK"
    d.can_acquire_tackle = True
    d.defender_role = DefenderRole.DEF_LINE_READY
    d.max_defensive_speed_mps = CEILING
    d.max_speed_mps = 9.2
    d.target_x = ANCHOR
    d.target_y = 35.0
    d.frozen = False
    d.freeze_timer = 0.0
    d.depth_role = None
    d.depth_side = None
    d.tackle_role = None
    return d


def make_bc():
    bc = Player(ANCHOR + 6.0, 35.0, 900)
    bc.is_ball_carrier = True
    bc.vx = 6.0
    return bc


def arm_then_beat(ope, world, d, bc):
    """Drive the REAL arming site, then the REAL miss teardown."""
    ctx = SimpleNamespace(tackler_roles={})
    ope._arm_tackler_role(d, DefenderRole.TACKLER_1, "TACKLER_1", ctx)
    ope.primary = d
    ope.joiners = []
    ope.tes_ctx = None
    ope.ball_carrier = bc
    ope._phase = "TACKLE_CONTACT"
    ope._broken_checked_this_contact = False
    ope._handle_broken_tackle()


def score_released_body(world, d, bc):
    """The post-fix contract on a body the teardown released."""
    d.frozen = False
    d.freeze_timer = 0.0
    check("released: authority_active is FALSE",
          fb.fullback_tackle_authority_active(d) is False)
    check("released: tackle_side_owner_live is FALSE",
          fb.fullback_tackle_side_owner_live(d) is False)
    check("released: sweeper cap APPLIES again",
          fb.fullback_approach_cap_applies(d) is True)
    check("released: live-break chase establishment SUCCEEDS",
          fb.establish_fullback_live_break_chase(world, d) is True)

    # ── END-TO-END: the geometry authority writes the chase locomotion ──
    d.target_x = ANCHOR
    d.target_y = 35.0
    d.movement_state = MovementState.DRIFT
    d.move_speed = 0.0
    fb.apply_fullback_geometry(world, d)
    check("geometry: state is CHASE (not the anchor DRIFT)",
          fb.movement_state_name(d) == "CHASE",
          "state=%r" % (fb.movement_state_name(d),))
    check("geometry: target_x is the goal-side pursuit point",
          abs(float(d.target_x) - (float(bc.x) - TRAIL)) < 1e-6,
          "target_x=%.4f expected=%.4f" % (d.target_x, bc.x - TRAIL))
    check("geometry: target_y is the carrier's lane",
          abs(float(d.target_y) - float(bc.y)) < 1e-6)
    check("geometry: target_source is fullback_geometry",
          getattr(d, "target_source", None) == "fullback_geometry",
          "source=%r" % (getattr(d, "target_source", None),))
    # The CHASE contract = the governed ceiling x the arrival taper, with the
    # FB_MAX_APPROACH_SPEED_MPS (2.0) SWEEPER cap NOT applied.  At the 3.0 m
    # error above the taper is 3.0 / FB_DECEL_START_M(6.0) = 0.5, so the
    # commanded speed must be 4.15 -- not the 1.00 the 2.0 cap produced.
    _expect_near = CEILING * (3.0 / 6.0)
    check("geometry: NEAR chase is the tapered ceiling, not the 2.0 cap",
          abs(float(d.move_speed) - _expect_near) < 1e-6
          and float(d.move_speed) > fb.FB_MAX_APPROACH_SPEED_MPS,
          "move_speed=%.4f expected=%.4f (2.0-cap gives %.2f)"
          % (d.move_speed, _expect_near,
             min(CEILING, fb.FB_MAX_APPROACH_SPEED_MPS) * 0.5))

    # FAR chase: outside FB_DECEL_START_M the taper is inert, so the body
    # must command the FULL governed ceiling.
    d.x = float(bc.x) - TRAIL - 8.0          # 8.0 m of error
    d.target_x = ANCHOR
    d.target_y = 35.0
    d.movement_state = MovementState.DRIFT
    d.move_speed = 0.0
    fb.apply_fullback_geometry(world, d)
    check("geometry: FAR chase commands the FULL ceiling (no 2.0 cap)",
          abs(float(d.move_speed) - CEILING) < 1e-6
          and fb.movement_state_name(d) == "CHASE",
          "move_speed=%.4f ceiling=%.1f" % (d.move_speed, CEILING))
    d.x = ANCHOR + 2.0                        # restore for later sections
    d.frozen = False
    d.freeze_timer = 0.0


def main():
    print("=" * 88)
    print("FULLBACK MISS-TEARDOWN ENGAGEMENT RELEASE - CONTRACT PROBE")
    print("=" * 88)

    bc = make_bc()
    d1 = make_fb(200)
    world = build_world()
    world.ball_carrier = bc
    world.defending_team = [d1]
    world.players = [d1, bc]
    world.attacking_team = [bc]
    ope = OpenPlayEngine(world)
    ope.ball_carrier = bc

    print("")
    print("  -- 1. baseline (tagged sweeper, before arming) --")
    check("baseline: engagement marker absent",
          fb.is_fullback_emergency_engaged(d1) is False)
    check("baseline: tackle authority inactive",
          fb.fullback_tackle_authority_active(d1) is False)
    check("baseline: sweeper cap applies",
          fb.fullback_approach_cap_applies(d1) is True)

    print("")
    print("  -- 2. arm through the engine's single arming site --")
    arm_then_beat(ope, world, d1, bc)
    # (arm+teardown both ran; assert arming really happened via the teardown's
    #  observable effect below, plus the direct arming check on a second body.)

    print("")
    print("  -- 3. post-miss teardown contract --")
    check("post-miss: phase is OPEN_PLAY (RELEASE never ran)",
          ope._phase == "OPEN_PLAY", "phase=%r" % (ope._phase,))
    check("post-miss: the FULLBACK TAG is kept",
          d1.fullback_role_active is True)
    check("post-miss: frozen (the 1.0 s stun still runs)",
          d1.frozen is True)
    check("post-miss: commit latches released",
          getattr(d1, "_tackle_commit_active", False) is False
          and getattr(d1, "_pursuit_commit_active", False) is False)
    check("post-miss: _fb_emergency_engaged CLEARED",
          fb.is_fullback_emergency_engaged(d1) is False,
          "marker=%r" % (fb.is_fullback_emergency_engaged(d1),))
    check("post-miss: defender_role restored to DEF_LINE_READY",
          d1.defender_role is DefenderRole.DEF_LINE_READY,
          "role=%r" % (d1.defender_role,))

    print("")
    print("  -- 4. consequence chain on the released body --")
    score_released_body(world, d1, bc)

    print("")
    print("  -- 5. lever A/B: RLM_FB_EMERGENCY_RELEASE_ON_MISS=0 --")
    os.environ["RLM_FB_EMERGENCY_RELEASE_ON_MISS"] = "0"
    try:
        bc2 = make_bc()
        d2 = make_fb(201)
        world2 = build_world()
        world2.ball_carrier = bc2
        world2.defending_team = [d2]
        world2.players = [d2, bc2]
        world2.attacking_team = [bc2]
        ope2 = OpenPlayEngine(world2)
        ope2.ball_carrier = bc2

        ctx = SimpleNamespace(tackler_roles={})
        ope2._arm_tackler_role(d2, DefenderRole.TACKLER_1, "TACKLER_1", ctx)
        check("lever off: arming sets the marker",
              fb.is_fullback_emergency_engaged(d2) is True)
        check("lever off: authority is active while armed",
              fb.fullback_tackle_authority_active(d2) is True)

        ope2.primary = d2
        ope2.joiners = []
        ope2.tes_ctx = None
        ope2._phase = "TACKLE_CONTACT"
        ope2._broken_checked_this_contact = False
        ope2._handle_broken_tackle()

        check("lever off: marker SURVIVES the teardown (pre-change engine)",
              fb.is_fullback_emergency_engaged(d2) is True,
              "marker=%r" % (fb.is_fullback_emergency_engaged(d2),))
        check("lever off: defender_role stays TACKLER_1",
              d2.defender_role is DefenderRole.TACKLER_1,
              "role=%r" % (d2.defender_role,))
        check("lever off: authority stays ACTIVE (trapped in the yield)",
              fb.fullback_tackle_authority_active(d2) is True)
        check("lever off: establishment DECLINES",
              fb.establish_fullback_live_break_chase(world2, d2) is False)

        # The bare return: target and state are left exactly as they were.
        d2.frozen = False
        d2.freeze_timer = 0.0
        pinned_tx = float(bc2.x) - TRAIL          # a live chase point
        d2.target_x = pinned_tx
        d2.target_y = float(bc2.y)
        d2.movement_state = MovementState.DRIFT
        d2.move_speed = 1.234                     # a sentinel stale speed
        fb.apply_fullback_geometry(world2, d2)
        check("lever off: geometry BARE-RETURNS (target untouched)",
              abs(float(d2.target_x) - pinned_tx) < 1e-9,
              "target_x=%.4f pinned=%.4f" % (d2.target_x, pinned_tx))
        check("lever off: geometry writes NO movement state",
              fb.movement_state_name(d2) == "DRIFT",
              "state=%r" % (fb.movement_state_name(d2),))
        check("lever off: geometry writes NO speed (stale value survives)",
              abs(float(d2.move_speed) - 1.234) < 1e-9,
              "move_speed=%r" % (d2.move_speed,))
    finally:
        os.environ.pop("RLM_FB_EMERGENCY_RELEASE_ON_MISS", None)

    print("")
    print("  -- 6. restored: same body, lever back ON --")
    check("restored: release is enabled by default",
          fb.fullback_emergency_release_on_miss_enabled() is True)
    released = fb.release_fullback_emergency_on_miss(world2)
    check("restored: the release helper frees the leaked body",
          len(released) == 1 and fb.is_fullback_emergency_engaged(d2) is False,
          "released=%d" % len(released))
    check("restored: it is idempotent on a released body",
          fb.release_fullback_emergency_on_miss(world2) == [])

    failures = [n for n, ok, _d in RESULTS if not ok]
    print("")
    print("-" * 88)
    if failures:
        print("FAILED - %d/%d checks:" % (len(failures), len(RESULTS)))
        for n in failures:
            print("  - %s" % n)
        return 1
    print("ALL %d CHECKS PASSED" % len(RESULTS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
