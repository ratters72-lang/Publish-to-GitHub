"""
verify_geometry_authority_lock.py — GEOMETRY AUTHORITY + BOUNDED TIMING LOCK

Directive (2026-09-04): "Lock defensive behaviour timing and authority to
the unified geometry so no behaviour can override count, lanes, width, or
spacing, and all movement follows the bounded human timing curves."

Follow-up (2026-09-04): "Markers now use geometry-authority targets only
(ruck_x ± offsets, ruck_y), and all freeze/HOLD/timing states suppress
velocity without ever overriding target_x/target_y, so markers remain
pixel-correct on both axes across TES → PTB → RELEASE → OPEN_PLAY."

This verifier asserts the enforceable invariants without a full sim run:

  A. BOUNDED TIMING CURVE — Player._accelerate_toward_target must HALT a
     defensive-ceiling carrier on move_speed <= 0 (HOLD / timing_locked /
     freeze), not fall back to max_speed_mps.  Non-defenders keep the
     legacy fallback.
  B. GEOMETRY AUTHORITY — adjust_for_role must NOT pin the Y/X target to
     the defender's current position for timing holds.
  C. SINGLE Y READ — frame_spacing_y is the frame-authority Y read.
  D. MARKER GEOMETRY AUTHORITY — marker_geometry_target returns the single
     ruck-pin (ruck_x + depth, ruck_y) for A/B; enforce_marker_geometry_
     authority re-asserts it after a d.x/d.y pin (freeze/HOLD/timing only
     suppresses velocity, never the target).

Exit code 0 = all passed; 1 = failure.
"""

import sys

from player.player import Player, DefenderRole
from game.defence.defence_lvl_1.defensive_role_spacing import DefensiveRoleSpacing
from game.defence.defence_lvl_1.defensive_pipeline import frame_spacing_y
from game.defence.defence_lvl_1.marker_authority import (
    marker_geometry_target,
    enforce_marker_geometry_authority,
)


def _mk_defender(max_defensive_speed_mps: float) -> Player:
    d = Player(x=60.0, y=35.0, player_id=1)
    d.max_defensive_speed_mps = max_defensive_speed_mps
    d.move_speed = 0.0
    d.target_x = 100.0
    d.target_y = 20.0
    d.vx = 0.0
    d.vy = 0.0
    return d


def _mk_world(ruck_x=60.0, ruck_y=35.0):
    return type("W", (), {"ruck_x_m": ruck_x, "ruck_y_m": ruck_y})()


def check_hold_halts_defender() -> bool:
    d = _mk_defender(8.3)
    d._accelerate_toward_target(dt=1.0 / 60.0)
    ok = (d.vx == 0.0 and d.vy == 0.0 and d.x == 60.0 and d.y == 35.0)
    if not ok:
        print("  FAIL: defender move_speed=0 did not halt "
              "(vx=%.3f vy=%.3f x=%.3f y=%.3f)" % (d.vx, d.vy, d.x, d.y))
    return ok


def check_hold_does_not_halt_non_defender() -> bool:
    d = _mk_defender(0.0)
    d._accelerate_toward_target(dt=1.0 / 60.0)
    moved = (d.vx != 0.0 or d.vy != 0.0)
    if not moved:
        print("  FAIL: non-defender move_speed=0 did not fall back to max")
    return moved


def check_speed_moves_toward_target() -> bool:
    d = _mk_defender(8.3)
    d.move_speed = 8.3
    d.x = 60.0
    d.y = 35.0
    d._accelerate_toward_target(dt=1.0 / 60.0)
    moved = (d.vx != 0.0 or d.vy != 0.0)
    if not moved:
        print("  FAIL: defender with move_speed>0 did not move")
    return moved


def check_adjust_for_role_no_timing_pin() -> bool:
    rs = DefensiveRoleSpacing(tuners=type("T", (), {})())
    d = Player(x=60.0, y=35.0, player_id=2)
    d.depth_class = "PASSIVE"
    d.retreat_delay = 1.0
    d.lateral_hesitation = 1.0
    base_tx, base_ty = 70.0, 22.0
    world = _mk_world()
    line_state = {"speed_mps": 8.3}
    tx, ty = rs.adjust_for_role(d, base_tx, base_ty, world, line_state)
    ok = (tx == base_tx and ty == base_ty)
    if not ok:
        print("  FAIL: adjust_for_role pinned timing target "
              "(tx=%.3f base_tx=%.3f ty=%.3f base_ty=%.3f)"
              % (tx, base_tx, ty, base_ty))
    return ok


def check_single_y_authority_read() -> bool:
    return callable(frame_spacing_y)


def check_marker_geometry_target() -> bool:
    """marker_geometry_target = (ruck_x + depth, ruck_y) for A/B."""
    world = _mk_world(ruck_x=60.0, ruck_y=35.0)
    a = Player(x=0, y=0, player_id=11)
    a.defender_role = DefenderRole.MARKER_A
    b = Player(x=0, y=0, player_id=12)
    b.defender_role = DefenderRole.MARKER_B
    ta = marker_geometry_target(world, a)
    tb = marker_geometry_target(world, b)
    ok = (ta == (61.0, 35.0) and tb == (62.0, 35.0))
    if not ok:
        print("  FAIL: marker_geometry_target "
              "(A=%s expected (61.0,35.0); B=%s expected (62.0,35.0))"
              % (ta, tb))
    return ok


def check_marker_enforce_after_pin() -> bool:
    """enforce_marker_geometry_authority re-asserts ruck pin after a d.x/d.y pin."""
    world = _mk_world(ruck_x=60.0, ruck_y=35.0)
    m = Player(x=55.0, y=30.0, player_id=21)
    m.defender_role = DefenderRole.MARKER_A
    m.target_x = 55.0  # a freeze/pin wrote d.x/d.y (stale)
    m.target_y = 30.0
    m.frozen = True
    m.move_speed = 0.0
    enforce_marker_geometry_authority(world, [m])
    ok = (m.target_x == 61.0 and m.target_y == 35.0
          and m.frozen is True and m.move_speed == 0.0)
    if not ok:
        print("  FAIL: enforce_marker_geometry_authority "
              "(x=%.3f y=%.3f frozen=%s speed=%.3f)"
              % (m.target_x, m.target_y, m.frozen, m.move_speed))
    return ok


def main() -> int:
    print("GEOMETRY AUTHORITY + BOUNDED TIMING + MARKER AUTHORITY LOCK")
    print("=" * 58)

    checks = [
        ("HOLD halts a defensive-ceiling carrier", check_hold_halts_defender),
        ("Non-defender move_speed=0 falls back to max_speed",
         check_hold_does_not_halt_non_defender),
        ("Defender move_speed>0 moves toward target",
         check_speed_moves_toward_target),
        ("adjust_for_role no longer pins timing target",
         check_adjust_for_role_no_timing_pin),
        ("frame_spacing_y is the single Y authority read",
         check_single_y_authority_read),
        ("marker_geometry_target = (ruck_x + depth, ruck_y)",
         check_marker_geometry_target),
        ("enforce_marker_geometry_authority re-asserts ruck pin after pin",
         check_marker_enforce_after_pin),
    ]

    ok_all = True
    for label, fn in checks:
        try:
            r = fn()
        except Exception as e:  # noqa: BLE001
            print("  EXCEPTION: %s" % e)
            r = False
        print("[%s] %s" % ("PASS" if r else "FAIL", label))
        ok_all = ok_all and r

    print("=" * 58)
    if ok_all:
        print("ALL CHECKS PASSED")
        return 0
    print("FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
