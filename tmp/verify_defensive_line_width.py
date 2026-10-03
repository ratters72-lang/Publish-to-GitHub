"""
DEFENSIVE LINE WIDTH VERIFICATION TEST

Validates that:
  1. get_defence_target() distributes Y across def_line_width_m
  2. DefensiveRoleSpacing uses def_line_width_m not hardcoded values
  3. DefensiveGeometry uses def_line_width_m not hardcoded values
  4. Effective width = max(Y) - min(Y) stays close to def_line_width_m
"""

import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from world.world_state import get_defence_target
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_role_spacing import DefensiveRoleSpacing
from game.defence.defence_lvl_1.defensive_geometry import DefensiveGeometry


class MockWorld:
    """Minimal world stub that provides the attributes get_defence_target needs."""
    def __init__(self, def_line_width_m=40.0, ruck_y_m=35.0, ruck_x_m=60.0,
                 defensive_line_anchor=70.0):
        self.def_line_width_m = def_line_width_m
        self.ruck_y_m = ruck_y_m
        self.ruck_x_m = ruck_x_m
        self.defensive_line_anchor = defensive_line_anchor
        self.ball_carrier = None
        self.phase = None  # for geometry phase check


class MockPlayer:
    """Minimal player stub for spacing tests."""
    def __init__(self, y=35.0, x=60.0, player_id=0, depth_role="C",
                 defender_role=None, depth_class="PASSIVE"):
        self.y = y
        self.x = x
        self.player_id = player_id
        self.depth_role = depth_role
        self.defender_role = defender_role
        self.depth_class = depth_class
        self.lane_index = 0


# ── TEST 1: get_defence_target with authoritative width ──

def test_get_defence_target_width():
    """Verify get_defence_target distributes Y across def_line_width_m."""
    passes = 0
    fails = 0

    # Test: 40m width centred on ruck_y=35
    # With 7 line defenders (indices 3-9), they should span ~±20m around 35
    world = MockWorld(def_line_width_m=40.0, ruck_y_m=35.0)
    ys = []
    for i in range(3, 10):
        tx, ty = get_defence_target(world, i)
        ys.append(ty)

    effective_width = max(ys) - min(ys)
    # Should be close to 40m
    if abs(effective_width - 40.0) < 1.0:
        print(f"[PASS] get_defence_target width={effective_width:.1f}m for 40m config")
        passes += 1
    else:
        print(f"[FAIL] get_defence_target width={effective_width:.1f}m expected ~40m")
        fails += 1

    # Test: 20m width centred on ruck_y=20
    world2 = MockWorld(def_line_width_m=20.0, ruck_y_m=20.0)
    ys2 = []
    for i in range(3, 10):
        tx, ty = get_defence_target(world2, i)
        ys2.append(ty)

    effective_width2 = max(ys2) - min(ys2)
    if abs(effective_width2 - 20.0) < 1.0:
        print(f"[PASS] get_defence_target width={effective_width2:.1f}m for 20m config")
        passes += 1
    else:
        print(f"[FAIL] get_defence_target width={effective_width2:.1f}m expected ~20m")
        fails += 1

    # Test: 60m width centred on ruck_y=35
    world3 = MockWorld(def_line_width_m=60.0, ruck_y_m=35.0)
    ys3 = []
    for i in range(3, 10):
        tx, ty = get_defence_target(world3, i)
        ys3.append(ty)

    effective_width3 = max(ys3) - min(ys3)
    if abs(effective_width3 - 60.0) < 2.0:
        print(f"[PASS] get_defence_target width={effective_width3:.1f}m for 60m config")
        passes += 1
    else:
        print(f"[FAIL] get_defence_target width={effective_width3:.1f}m expected ~60m")
        fails += 1

    return passes, fails


# ── TEST 2: DefensiveRoleSpacing uses def_line_width_m ──

def test_role_spacing_width():
    """Verify DefensiveRoleSpacing.compute_role_spacing uses def_line_width_m."""
    passes = 0
    fails = 0

    tuners = DefenceTuners()
    spacing = DefensiveRoleSpacing(tuners)

    # Prepare with a 50m width
    world = MockWorld(def_line_width_m=50.0, ruck_y_m=35.0, ruck_x_m=60.0,
                      defensive_line_anchor=70.0)
    spacing.prepare(world)

    # Check stored width
    if spacing._def_line_width_m == 50.0:
        print(f"[PASS] DefensiveRoleSpacing._def_line_width_m = {spacing._def_line_width_m}")
        passes += 1
    else:
        print(f"[FAIL] DefensiveRoleSpacing._def_line_width_m = {spacing._def_line_width_m}, expected 50.0")
        fails += 1

    # Compute Y for several line defenders (lane_index -1, 0, 1, 2)
    # Note: compute_role_spacing maps lane_index to def_index = 3+base_idx,
    # where base_idx = abs(lane_index)+1.  So:
    #   lane_index=0 → base_idx=1 → def_index=4 → lane_idx_internal=1
    #   lane_index=1 → base_idx=2 → def_index=5 → lane_idx_internal=2
    #   lane_index=2 → base_idx=3 → def_index=6 → lane_idx_internal=3
    #   lane_index=-1 → base_idx=2 → def_index=5 → lane_idx_internal=2
    # This means Y values are spread from slot 1..3 of 7 slots across 50m.
    defender0 = MockPlayer(y=35, player_id=0)
    defender1 = MockPlayer(y=35, player_id=1)
    defender2 = MockPlayer(y=35, player_id=2)
    defender_minus1 = MockPlayer(y=35, player_id=-1)

    ty0 = spacing.compute_role_spacing(defender0, None, None, lane_index=0)[1]
    ty1 = spacing.compute_role_spacing(defender1, None, None, lane_index=1)[1]
    ty2 = spacing.compute_role_spacing(defender2, None, None, lane_index=2)[1]
    ty_minus1 = spacing.compute_role_spacing(defender_minus1, None, None, lane_index=-1)[1]

    print(f"  lane_idx=0 → ty={ty0:.1f}, lane_idx=1 → ty={ty1:.1f}, lane_idx=2 → ty={ty2:.1f}, lane_idx=-1 → ty={ty_minus1:.1f}")

    # The Y values should all be different from each other (spread)
    unique_ys = len(set([round(ty0,1), round(ty1,1), round(ty2,1), round(ty_minus1,1)]))
    if unique_ys >= 3:
        print(f"[PASS] role_spacing Y diversity = {unique_ys} unique values out of 4")
        passes += 1
    else:
        print(f"[FAIL] role_spacing Y diversity = {unique_ys} unique values out of 4 (too clustered)")
        fails += 1

    # Verify that the computed width between extremes is meaningful
    all_ys = [ty0, ty1, ty2, ty_minus1]
    computed_width = max(all_ys) - min(all_ys)
    # With 50m width and 7 slots: min spread between these is at least ~8.3m
    if computed_width > 5.0:
        print(f"[PASS] role_spacing computed width = {computed_width:.1f}m (good spread)")
        passes += 1
    else:
        print(f"[FAIL] role_spacing computed width = {computed_width:.1f}m (too narrow)")
        fails += 1

    return passes, fails


# ── TEST 3: DefensiveGeometry uses def_line_width_m ──

def test_geometry_width():
    """Verify DefensiveGeometry._compute_lane_targets uses def_line_width_m."""
    passes = 0
    fails = 0

    tuners = DefenceTuners()
    geometry = DefensiveGeometry(tuners)

    # Test with 30m width
    world = MockWorld(def_line_width_m=30.0, ruck_y_m=35.0)
    defenders = [MockPlayer(y=10), MockPlayer(y=20), MockPlayer(y=30),
                 MockPlayer(y=40), MockPlayer(y=50)]
    lane_y = geometry._compute_lane_targets(world, defenders, spacing=5.0,
                                            width_curve=[1.0]*5)

    effective_width = max(lane_y) - min(lane_y)
    if abs(effective_width - 30.0) < 2.0:
        print(f"[PASS] geometry lane width = {effective_width:.1f}m for 30m config")
        passes += 1
    else:
        print(f"[FAIL] geometry lane width = {effective_width:.1f}m, expected ~30m")
        print(f"  lane_y = {[f'{y:.1f}' for y in lane_y]}")
        fails += 1

    # Test with 60m width
    world2 = MockWorld(def_line_width_m=60.0, ruck_y_m=35.0)
    lane_y2 = geometry._compute_lane_targets(world2, defenders, spacing=5.0,
                                             width_curve=[1.0]*5)

    effective_width2 = max(lane_y2) - min(lane_y2)
    if abs(effective_width2 - 60.0) < 3.0:
        print(f"[PASS] geometry lane width = {effective_width2:.1f}m for 60m config")
        passes += 1
    else:
        print(f"[FAIL] geometry lane width = {effective_width2:.1f}m, expected ~60m")
        print(f"  lane_y = {[f'{y:.1f}' for y in lane_y2]}")
        fails += 1

    return passes, fails


# ── MAIN ──

def main():
    total_passes = 0
    total_fails = 0

    print("=" * 60)
    print("DEFENSIVE LINE WIDTH VERIFICATION")
    print("=" * 60)

    print("\n── Test 1: get_defence_target() ──")
    p, f = test_get_defence_target_width()
    total_passes += p
    total_fails += f

    print("\n── Test 2: DefensiveRoleSpacing ──")
    p, f = test_role_spacing_width()
    total_passes += p
    total_fails += f

    print("\n── Test 3: DefensiveGeometry ──")
    p, f = test_geometry_width()
    total_passes += p
    total_fails += f

    print("\n" + "=" * 60)
    print(f"RESULTS: {total_passes} passed, {total_fails} failed")
    if total_fails > 0:
        print("❌ SOME TESTS FAILED")
        sys.exit(1)
    else:
        print("✅ ALL TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
