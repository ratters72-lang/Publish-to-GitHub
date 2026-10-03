import sys
import types

sys.path.insert(0, ".")

from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_split_spacing import (
    compute_targets,
    side_widths,
    _clamped_winger_y,
    LEFT,
    RIGHT,
)


def mk(i, y, role="D", side=1):
    d = types.SimpleNamespace()
    d.player_id = i
    d.y = float(y)
    d.x = 68.0
    d.depth_role = role
    d.depth_side = side
    d.fullback_role_active = False
    d.defender_role = None
    d.frame_side = 0
    return d


ROLES = ("A", "B", "C", "CENTRE", "D", "D", "WINGER")


def check(label, ruck_y, ys, roles, margin=0.05, seed_side=True):
    """Assert the physical-side + even-spacing contracts for one ruck.

    Production invariant: assign_depth_roles seeds the PERSISTENT
    depth_side from the physical position BEFORE spacing runs, so a
    seeded defender's side always matches its standing side.  With
    seed_side=False the defender is left unseeded (depth_side None) to
    exercise the live-sign fallback inside depth_side_of().
    """
    def build(i, y, role):
        side = RIGHT if y >= ruck_y else LEFT
        return mk(i, y, role, side=side if seed_side else None)

    world = types.SimpleNamespace()
    world.ruck_y_m = ruck_y
    world.ruck_x_m = 60.0
    world.short_side_width_m = None
    world.open_side_width_m = None
    world.governor_width_active = False
    world.def_line_width_m = 55.0
    world.ball_carrier = None
    world.sub_state = "open_play"
    tuners = DefenceTuners()

    defs = [build(i, y, role) for i, (y, role) in enumerate(zip(ys, roles))]
    targets = compute_targets(world, defs, tuners)

    # 1. Physical-side integrity: frame_side must match the physical side.
    for d in defs:
        expect_side = RIGHT if d.y >= ruck_y else LEFT
        if d.frame_side != expect_side:
            raise AssertionError(
                f"{label}: frame_side={d.frame_side} expected {expect_side} "
                f"for phys_y={d.y} ruck_y={ruck_y}"
            )

    # 2. No cross-ruck target: a defender physically RIGHT of ruck_y must
    #    not receive a Y target LEFT of ruck_y (and vice versa).
    for d in defs:
        ty = targets[id(d)]
        if d.y >= ruck_y and ty < ruck_y - margin:
            raise AssertionError(
                f"{label}: right-side defender phys_y={d.y} got target y={ty} "
                f"across the ruck (ruck_y={ruck_y})"
            )
        if d.y < ruck_y and ty > ruck_y + margin:
            raise AssertionError(
                f"{label}: left-side defender phys_y={d.y} got target y={ty} "
                f"across the ruck (ruck_y={ruck_y})"
            )

    # 3. Even spacing per physical side: n >= 3 sides have equal gaps.
    left = sorted([(d.y, targets[id(d)]) for d in defs if d.frame_side == LEFT])
    right = sorted([(d.y, targets[id(d)]) for d in defs if d.frame_side == RIGHT])

    for name, side in (("left", left), ("right", right)):
        n = len(side)
        if n >= 3:
            gaps = [round(abs(side[i + 1][1] - side[i][1]), 4) for i in range(n - 1)]
            if max(gaps) - min(gaps) > 0.01:
                raise AssertionError(
                    f"{label}: {name} side not evenly spaced: gaps={gaps}"
                )


# ruck near left touchline, all defenders physically right -> ALL on right
check("short-side ruck, all defs on open side", 12.0,
      [14.0, 18.0, 22.0, 26.0, 30.0, 34.0, 38.0], ROLES)

# centre-line ruck, symmetric line (control)
check("centre ruck, symmetric", 35.0,
      [15.0, 19.0, 23.0, 35.0, 47.0, 51.0, 55.0], ROLES)

# short-side ruck with 2 defenders genuinely left, 5 right
check("short-side ruck, 2 left defs", 12.0,
      [7.0, 10.0, 16.0, 22.0, 28.0, 34.0, 40.0], ROLES)

# ruck near right touchline, all defenders physically left -> ALL on left
check("open-side ruck at right, all defs on open side", 58.0,
      [20.0, 25.0, 30.0, 40.0, 45.0, 50.0, 54.0], ROLES)

# unseeded fallback: no depth_side stamp -> live-sign fallback must
# produce the same physical-side partition
check("unseeded defenders, centre ruck", 35.0,
      [15.0, 19.0, 23.0, 35.0, 47.0, 51.0, 55.0], ROLES, seed_side=False)

# mirror check: right-hand chain order-preserving with physical Y; the
# innermost right defender must land at the A anchor near ruck_y, and no
# right defender may cross the ruck.
world = types.SimpleNamespace()
world.ruck_y_m = 35.0
world.ruck_x_m = 60.0
world.short_side_width_m = None
world.open_side_width_m = None
world.governor_width_active = False
world.def_line_width_m = 55.0
world.ball_carrier = None
world.sub_state = "open_play"
tuners = DefenceTuners()
defs = [mk(i, y, role, side=RIGHT) for i, (y, role) in enumerate(zip(
    [37.0, 41.0, 45.0, 49.0, 53.0, 57.0, 61.0], ROLES))]
targets = compute_targets(world, defs, tuners)
for d in defs:
    ty = targets[id(d)]
    if ty < 35.0 - 0.05:
        raise AssertionError(f"mirror: right defender phys_y={d.y} crossed ruck to {ty}")
right_ys = sorted([targets[id(d)] for d in defs if d.y >= 35.0])
if abs(right_ys[0] - 37.0) > 0.05:  # innermost right defender at A anchor
    raise AssertionError(f"mirror: innermost right defender not at A anchor: {right_ys[0]}")
if abs(right_ys[-1] - 62.5) > 0.05:  # outermost right defender at WINGER anchor (ruck 35 + half-width 27.5)
    raise AssertionError(f"mirror: outermost right defender not at WINGER anchor: {right_ys[-1]}")

print("ALL SHORT-SIDE SPACING CHECKS PASSED")
