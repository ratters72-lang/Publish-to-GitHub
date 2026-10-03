import sys
import types

sys.path.insert(0, ".")

from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_split_spacing import (
    compute_targets,
    side_widths,
    _clamped_winger_y,
    _ruck_a_offset_m,
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


def run(label, ruck_y, ys, roles):
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

    left_w, right_w = side_widths(world, tuners)
    wl = _clamped_winger_y(left_w, ruck_y, -1, tuners)
    wr = _clamped_winger_y(right_w, ruck_y, +1, tuners)
    a_off = _ruck_a_offset_m(left_w, tuners)
    al = ruck_y - a_off
    ar = ruck_y + a_off
    print(f"=== {label}: ruck_y={ruck_y}  leftA={al:.1f} leftW={wl:.1f} | rightA={ar:.1f} rightW={wr:.1f}")

    defs = [mk(i, y, role) for i, (y, role) in enumerate(zip(ys, roles))]
    targets = compute_targets(world, defs, tuners)
    for d in defs:
        ty = targets[id(d)]
        print(
            f"  id={d.player_id} role={d.depth_role:7s} depth_side={d.depth_side:+d} "
            f"phys_y={d.y:5.1f} frame_side={d.frame_side:+d} -> ty={ty:6.2f}"
        )
    print()


ROLES = ("A", "B", "C", "CENTRE", "D", "D", "WINGER")

# Scenario 1: ruck near LEFT touchline; all 7 defenders physically RIGHT of ruck_y.
run("short-side ruck, all defs on open side", 12.0, [14.0, 18.0, 22.0, 26.0, 30.0, 34.0, 38.0], ROLES)

# Scenario 2: centre-line ruck, symmetric line (control).
run("centre ruck, symmetric", 35.0, [15.0, 19.0, 23.0, 35.0, 47.0, 51.0, 55.0], ROLES)

# Scenario 3: short-side ruck with 2 defs genuinely left, 5 right.
run("short-side ruck, 2 left defs", 12.0, [7.0, 10.0, 16.0, 22.0, 28.0, 34.0, 40.0], ROLES)

# Scenario 4: ruck very close to right touchline; all 7 defenders physically LEFT.
run("open-side ruck at right, all defs on open side", 58.0, [20.0, 25.0, 30.0, 40.0, 45.0, 50.0, 54.0], ROLES)
