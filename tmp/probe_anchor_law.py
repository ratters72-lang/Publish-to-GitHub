"""Tuner-immunity probe for the ruck_adjacent_anchor ANCHOR LAW (2026-08-22).

Proves:
  1. ruck_adjacent_anchor(ruck_y, side) returns EXACTLY ruck_y - 2.0 (LEFT)
     and ruck_y + 2.0 (RIGHT).
  2. Setting tuners.RUCK_ADJACENT_A_Y_OFFSET_M = 5.0 (corrupted tuner)
     does NOT move the A anchor: compute_targets / required_side_counts /
     side_band_centre / spacing_target_y all still land the innermost
     defender at exactly ruck_y ± 2.0 m.
  3. Narrow side widths (degenerate clamp path) also do NOT move it.
"""
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_depth_roles import assign_depth_roles
from game.defence.defence_lvl_1.defensive_split_spacing import (
    compute_targets,
    required_side_counts,
    side_band_centre,
    spacing_target_y,
    ruck_adjacent_anchor,
    LEFT,
    RIGHT,
)

failures = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        failures.append(name)


class MiniWorld:
    phase = "OPEN_PLAY"
    sub_state = "OPEN_PLAY"
    ball_carrier = None
    ball = None
    ruck_x_m = 60.0
    ruck_y_m = 35.0
    def_line_width_m = 40.0
    ball_x_m = 60.0
    ball_y_m = 35.0
    defensive_line_anchor = 70.0
    in_pursuit_mode = False
    first_tackle_not_made = False
    governor_width_active = True
    governor_line_width_m = 40.0
    sim_time_s = 0.0
    frame_count = 0
    short_side_width_m = None
    open_side_width_m = None
    fullback_default_x = 60.0
    fullback_default_y = 35.0
    defending_team = []


def make_line(world):
    line = []
    for i in range(6):
        d = Player(x=63.0, y=35.0 + 2.0 + i * 1.5, player_id=i + 1)
        d.is_defender = True
        line.append(d)
    for i in range(4):
        d = Player(x=63.0, y=35.0 - 1.0 - i * 1.5, player_id=20 + i)
        d.is_defender = True
        line.append(d)
    world.defending_team = line
    assign_depth_roles(line, world)
    return line


def main():
    print("=" * 68)
    print("RUCK-ADJACENT ANCHOR LAW — TUNER IMMUNITY PROBE")
    print("=" * 68)

    print("\n[1] ruck_adjacent_anchor is EXACT")
    check("LEFT anchor == ruck_y - 2.0 exactly",
          ruck_adjacent_anchor(35.0, LEFT) == 33.0,
          f"got {ruck_adjacent_anchor(35.0, LEFT)}")
    check("RIGHT anchor == ruck_y + 2.0 exactly",
          ruck_adjacent_anchor(35.0, RIGHT) == 37.0,
          f"got {ruck_adjacent_anchor(35.0, RIGHT)}")

    tuners = DefenceTuners()
    tuners.RUCK_ADJACENT_A_Y_OFFSET_M = 5.0  # CORRUPTED — must be ignored
    world = MiniWorld()
    ruck_y = world.ruck_y_m
    line = make_line(world)

    targets = compute_targets(world, line, tuners, governor=None)
    # The LAW clamps the NEAREST (innermost-slot) defender on each split
    # side — the count-split may cross the ruck, so the A role label can
    # land on an interior lane; the anchor slot itself is what is fixed.
    print("\n[2] compute_targets innermost slot, CORRUPTED tuner (5.0)")
    per_side_t = {}
    for d in line:
        side = getattr(d, "depth_side", 0)
        if id(d) in targets:
            per_side_t.setdefault(side, []).append(targets[id(d)])
    for side, ys in sorted(per_side_t.items()):
        innermost = min(ys) if side == RIGHT else max(ys)
        expected = ruck_y + side * 2.0
        check(f"side={side} innermost slot == ruck_y {side:+d}*2.0m (tuner IGNORED)",
              abs(innermost - expected) < 1e-9,
              f"innermost={innermost:.6f} expected={expected:.6f}")

    world.short_side_width_m = 8.0   # degenerate narrow side
    world.open_side_width_m = 60.0
    nl, nr = required_side_counts(world, tuners, 10, ruck_y=ruck_y)
    check("required_side_counts runs with narrow width + corrupted tuner",
          isinstance(nl, int) and isinstance(nr, int) and nl + nr == 10,
          f"n_left={nl} n_right={nr}")
    centre_left = side_band_centre(LEFT, ruck_y, 8.0, tuners)
    centre_right = side_band_centre(RIGHT, ruck_y, 60.0, tuners)
    # Band centre = midpoint of (fixed anchor, clamped winger).  The
    # winger honours SIDELINE_BUFFER_M, so compute the expected winger
    # from the module itself rather than assuming the raw half-width.
    from game.defence.defence_lvl_1.defensive_split_spacing import (
        _clamped_winger_y as _cw,
    )
    wl = _cw(8.0, ruck_y, LEFT, tuners)
    wr = _cw(60.0, ruck_y, RIGHT, tuners)
    check("left band centre uses the FIXED anchor (not tuner 5.0)",
          abs(centre_left - 0.5 * (33.0 + wl)) < 1e-9,
          f"centre={centre_left:.6f} expected={0.5 * (33.0 + wl):.6f}")
    check("right band centre uses the FIXED anchor (not tuner 5.0)",
          abs(centre_right - 0.5 * (37.0 + wr)) < 1e-9,
          f"centre={centre_right:.6f} expected={0.5 * (37.0 + wr):.6f}")

    a_l = next(d for d in line if getattr(d, "depth_role", None) == "A"
               and getattr(d, "depth_side", 0) == LEFT)
    a_r = next(d for d in line if getattr(d, "depth_role", None) == "A"
               and getattr(d, "depth_side", 0) == RIGHT)
    ty_l = spacing_target_y(a_l, 8.0, ruck_y, tuners)
    ty_r = spacing_target_y(a_r, 60.0, ruck_y, tuners)
    print("\n[4] spacing_target_y A-role immunity")
    check("left A == ruck_y - 2.0 exactly (narrow width, corrupted tuner)",
          abs(ty_l - 33.0) < 1e-9, f"y={ty_l:.6f}")
    check("right A == ruck_y + 2.0 exactly (corrupted tuner)",
          abs(ty_r - 37.0) < 1e-9, f"y={ty_r:.6f}")

    print("\n[5] Innermost chain lane == ruck_y ± 2.0 (per side)")
    per_side = {}
    for d in line:
        side = getattr(d, "depth_side", 0)
        if id(d) in targets:
            per_side.setdefault(side, []).append(targets[id(d)])
    for side, ys in per_side.items():
        innermost = min(ys) if side == RIGHT else max(ys)
        expected = ruck_y + side * 2.0
        check(f"side={side} innermost lane == ruck_y {side:+d}*2.0m",
              abs(innermost - expected) < 1e-9,
              f"innermost={innermost:.6f} expected={expected:.6f}")

    print("\n" + "=" * 68)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED — the anchor is a LAW, tuners are inert")
    sys.exit(0)


if __name__ == "__main__":
    main()
