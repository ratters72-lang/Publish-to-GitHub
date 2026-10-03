"""Intercept lane-tackler verifier (2026-08-15, spec §1/§3).

Checks the per-side lane ownership system end-to-end:

  §1  Per side of the BC exactly one PRIMARY lane tackler (min |d.x-bc.x|);
      every other candidate on that side is LINE.
  §1  Candidates may be behind OR slightly AHEAD of the carrier
      (ahead-engagement band — the middle of the line straddles the BC
      at catch time; spec §1 has no X-direction gating).  Defenders
      beyond the band are genuinely beaten and excluded.
  §1  Excluded roles (markers/DH/tacklers/fullback/frozen) never become
      lane tacklers.
  §3  PRIMARY lane tacklers inside the stance radius stop moving, pin
      targets, set HOLD, and lock facing to the BC (planted pre-contact).
  §3  PRIMARY outside the radius are NOT planted (and exit clears the
      stance).
  §2  Direction-aware commit: an ahead PRIMARY targets bc.x - 1.0
      (converges TOWARD the BC), a behind PRIMARY targets bc.x + 1.0.
  Tuners  DefensiveBehaviourUnified.select_lane_tacklers stamps
      pre_tackle_stance_radius_m / lane_tackler_switch_hysteresis_m from
      DefenceTuners onto the world (module reads them from world).
"""
import sys
import math

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from types import SimpleNamespace

from player.player import Player, Team
from player.movement_states import MovementState
from world.world_state import WorldState, GamePhase, GameSubState
from game.defence.defence_lvl_1.intercept_tacklers import (
    select_lane_tacklers,
    apply_pre_tackle_stance,
    is_primary_lane_tackler,
    is_line_defender,
)
from game.defence.defence_lvl_1.defensive_behaviour_unified import (
    DefensiveBehaviourUnified,
    _apply_tackler_commit,
)
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

failures = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def make_defender(pid, x, y):
    d = Player(x=x, y=y, player_id=pid)
    d.team = Team.RECEIVING
    d.is_defender = True
    d.max_defensive_speed_mps = 8.3
    d.defender_role = None
    d.depth_role = "A"
    d.depth_side = 1
    d.fullback_role_active = False
    return d


def make_world(**overrides):
    w = WorldState()
    w.ruck_x_m = 55.0
    w.ruck_y_m = 35.0
    w.def_line_width_m = 40.0
    w.defensive_line_anchor = 65.0
    w.anchor_locked = True
    w.govvernor_width_active = False
    w.first_tackle_not_made = False
    w.in_pursuit_mode = False
    w.use_structured_open_play = True
    w.defence_enabled = True
    w.tes_active = False
    w.phase = GamePhase.OPEN_PLAY
    w.sub_state = GameSubState.OPEN_PLAY
    w.ball_owner = "BC"
    w.role_governs = False
    w.ctx = None
    w.frame_count = 0
    w.sim_time_s = 0.0
    w.dt = 1.0 / 60.0
    for k, v in overrides.items():
        setattr(w, k, v)
    return w


def part1_lane_selection():
    print("\n[PART 1] Per-side PRIMARY / LINE assignment (§1, incl. ahead band)")
    bc = make_defender(99, 60.0, 35.0)

    # ── LEFT side (y < 35): both candidates BEHIND the carrier. ──
    left_far = make_defender(1, 58.0, 33.0)    # |dx| = 2.0
    left_near = make_defender(2, 59.0, 34.0)   # |dx| = 1.0 -> PRIMARY

    # ── RIGHT side (y >= 35): one candidate BEHIND, one AHEAD in band,
    #    and one beyond the band (genuinely beaten -> excluded). ──
    right_far = make_defender(3, 58.2, 36.0)   # |dx| = 1.8
    right_near = make_defender(4, 59.6, 37.0)  # |dx| = 0.4
    ahead_in = make_defender(5, 60.1, 36.5)    # |dx| = 0.1 ahead, in band -> PRIMARY
    beaten = make_defender(6, 67.0, 36.8)      # |dx| = 7.0, beyond 6.0m band -> excluded
    frozen = make_defender(7, 59.9, 34.2)      # |dx| = 0.1, frozen -> excluded

    frozen.frozen = True

    defs = [left_far, left_near, right_far, right_near, ahead_in, beaten, frozen]
    world = make_world(
        lane_tackler_switch_hysteresis_m=0.5,
        pre_tackle_stance_radius_m=0.8,
    )

    primaries = select_lane_tacklers(world, defs, bc)

    check("exactly two PRIMARY lane tacklers (one per side)",
          len(primaries) == 2,
          f"got {len(primaries)}")
    check("left PRIMARY is the min-|X| left-side candidate",
          left_near.tackle_role == "PRIMARY" and is_primary_lane_tackler(left_near),
          f"left_near={left_near.tackle_role}")
    check("ahead-in-band defender can own its side as PRIMARY (min-|X|)",
          ahead_in.tackle_role == "PRIMARY" and is_primary_lane_tackler(ahead_in),
          f"ahead_in={ahead_in.tackle_role}")
    check("other candidates on each side are LINE",
          left_far.tackle_role == "LINE" and is_line_defender(left_far)
          and right_near.tackle_role == "LINE" and is_line_defender(right_near)
          and right_far.tackle_role == "LINE" and is_line_defender(right_far),
          f"left_far={left_far.tackle_role} right_near={right_near.tackle_role} "
          f"right_far={right_far.tackle_role}")
    check("defender beyond the ahead-engagement band is not a candidate",
          getattr(beaten, "tackle_role", "PASSIVE") == "PASSIVE",
          f"beaten={getattr(beaten, 'tackle_role', None)}")
    check("frozen defender is never a lane tackler",
          getattr(frozen, "tackle_role", "PASSIVE") == "PASSIVE",
          f"frozen={getattr(frozen, 'tackle_role', None)}")


def part2_pre_tackle_stance():
    print("\n[PART 2] Close-range planted stance (§3)")
    bc = make_defender(99, 60.0, 35.0)

    inside = make_defender(1, 59.7, 35.0)      # dist 0.3 < 0.8
    inside.tackle_role = "PRIMARY"
    outside = make_defender(2, 62.0, 35.0)     # dist 2.0 >= 0.8
    outside.tackle_role = "PRIMARY"
    line_d = make_defender(3, 59.8, 34.0)      # LINE — never plants
    line_d.tackle_role = "LINE"

    defs = [inside, outside, line_d]
    world = make_world(pre_tackle_stance_radius_m=0.8)

    apply_pre_tackle_stance(world, defs, bc)

    check("PRIMARY inside radius is planted (pre_tackle_stance flag)",
          getattr(inside, "pre_tackle_stance", False),
          f"flag={getattr(inside, 'pre_tackle_stance', False)}")
    check("planted defender stops (vx=vy=0, speed 0, HOLD)",
          inside.vx == 0.0 and inside.vy == 0.0
          and inside.move_speed == 0.0
          and getattr(inside, "movement_state", None) == MovementState.HOLD,
          f"vx={inside.vx} vy={inside.vy} move_speed={inside.move_speed}")
    check("planted defender pins targets to current position",
          inside.target_x == inside.x and inside.target_y == inside.y,
          f"target=({inside.target_x:.1f},{inside.target_y:.1f}) pos=({inside.x:.1f},{inside.y:.1f})")
    check("planted defender squares up to the BC",
          math.isclose(
              inside.facing,
              math.atan2(bc.y - inside.y, bc.x - inside.x),
              abs_tol=1e-9,
          ),
          f"facing={inside.facing:.4f}")
    check("PRIMARY outside radius is NOT planted",
          not getattr(outside, "pre_tackle_stance", False),
          f"flag={getattr(outside, 'pre_tackle_stance', False)}")
    check("LINE defender never plants",
          not getattr(line_d, "pre_tackle_stance", False),
          f"flag={getattr(line_d, 'pre_tackle_stance', False)}")

    # Exit clears the stance (BC moves past radius).
    inside.pre_tackle_stance = True
    inside.vx = 1.0
    bc2 = make_defender(98, 64.0, 35.0)         # dist 4.3 — far away
    apply_pre_tackle_stance(world, [inside], bc2)
    check("stance clears when the carrier exits the radius",
          not getattr(inside, "pre_tackle_stance", False),
          f"flag={getattr(inside, 'pre_tackle_stance', False)}")


def part3_tuner_stamping():
    print("\n[PART 3] Wrapper stamps tuner constants onto the world")
    tuners = DefenceTuners()
    unified = DefensiveBehaviourUnified(tuners)

    world = SimpleNamespace()
    unified.select_lane_tacklers(world, [], None)

    check("world.pre_tackle_stance_radius_m stamped from tuners",
          getattr(world, "pre_tackle_stance_radius_m", None)
          == tuners.pre_tackle_stance_radius_m,
          f"world={getattr(world, 'pre_tackle_stance_radius_m', None)} "
          f"tuner={tuners.pre_tackle_stance_radius_m}")
    check("world.lane_tackler_switch_hysteresis_m stamped from tuners",
          getattr(world, "lane_tackler_switch_hysteresis_m", None)
          == tuners.lane_tackler_switch_hysteresis_m,
          f"world={getattr(world, 'lane_tackler_switch_hysteresis_m', None)} "
          f"tuner={tuners.lane_tackler_switch_hysteresis_m}")


def part4_direction_aware_commit():
    print("\n[PART 4] Direction-aware tackle-commit targets (§2, catch fix)")
    bc = make_defender(99, 60.0, 35.0)

    ahead_d = make_defender(1, 60.5, 35.0)     # d.x > bc.x — middle of line at catch
    ahead_d.tackle_role = "PRIMARY"
    behind_d = make_defender(2, 58.0, 35.0)    # d.x < bc.x — classic chaser
    behind_d.tackle_role = "PRIMARY"

    tuners = DefenceTuners()
    # This assertion isolates the commit-target writer; corridor speed/target
    # ownership has its own verifier and intentionally takes precedence.
    tuners.pretackle_corridor_owns_speed = False
    world = make_world()
    world.ball_carrier = bc

    _apply_tackler_commit(world, [ahead_d, behind_d], tuners)

    check("ahead PRIMARY targets bc.x - 1.0 (converges TOWARD the carrier)",
          math.isclose(ahead_d.tackle_target_x, bc.x - 1.0, abs_tol=1e-9)
          and math.isclose(ahead_d.target_x, bc.x - 1.0, abs_tol=1e-9),
          f"target_x={ahead_d.target_x:.2f} expected={bc.x - 1.0:.2f}")
    check("ahead PRIMARY keeps BC Y (tight lane band)",
          math.isclose(ahead_d.tackle_target_y, bc.y, abs_tol=1e-9),
          f"target_y={ahead_d.tackle_target_y:.2f}")
    check("behind PRIMARY targets bc.x + 1.0 (forward deflection preserved)",
          math.isclose(behind_d.tackle_target_x, bc.x + 1.0, abs_tol=1e-9),
          f"target_x={behind_d.target_x:.2f} expected={bc.x + 1.0:.2f}")


def main():
    print("=" * 64)
    print("INTERCEPT LANE-TACKLER VERIFIER (spec §1/§2/§3)")
    print("=" * 64)

    part1_lane_selection()
    part2_pre_tackle_stance()
    part3_tuner_stamping()
    part4_direction_aware_commit()

    print("\n" + "=" * 64)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
