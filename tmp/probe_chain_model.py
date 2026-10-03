"""Decisive probe: is the live spacing chain role-anchored or even-lane?

Replicates verify_role_width_order's synthetic line (make_balanced_line(6)),
runs assign_depth_roles + compute_targets, and prints for every defender:
    depth_role | chain_y | role_anchor_y_for | even_lane_expect
"""
import sys
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_width import role_anchor_y_for
from game.defence.defence_lvl_1.defensive_split_spacing import compute_targets
from game.defence.defence_lvl_1.defensive_depth_roles import assign_depth_roles

RUCK_X, RUCK_Y, WIDTH = 60.0, 35.0, 40.0


class MiniWorld:
    def __init__(self):
        self.ruck_x_m = RUCK_X
        self.ruck_y_m = RUCK_Y
        self.def_line_width_m = WIDTH
        self.short_side_width_m = None
        self.open_side_width_m = None
        self.governor_width_active = True
        self.governor_line_width_m = WIDTH
        self.ball_carrier = None
        self.open_play_engine = None
        self.sub_state = None


def make_defender(player_id, x, y):
    return Player(x=x, y=y, player_id=player_id)


def make_balanced_line(n_per_side):
    defenders = []
    pid = 1
    left_ys = [RUCK_Y - 24.0, RUCK_Y - 18.0]
    interior_left = [RUCK_Y - 3.0, RUCK_Y - 9.0, RUCK_Y - 15.0, RUCK_Y - 21.0]
    right_ys = [RUCK_Y + 18.0, RUCK_Y + 24.0]
    interior_right = [RUCK_Y + 3.0, RUCK_Y + 9.0, RUCK_Y + 15.0, RUCK_Y + 21.0]
    while (len([d for d in defenders if d.y < RUCK_Y]) < n_per_side
           or len([d for d in defenders if d.y > RUCK_Y]) < n_per_side):
        if len([d for d in defenders if d.y < RUCK_Y]) < n_per_side:
            slot = len([d for d in defenders if d.y < RUCK_Y])
            y = left_ys[slot] if slot < len(left_ys) else interior_left[slot - len(left_ys)]
            defenders.append(make_defender(pid, RUCK_X, y)); pid += 1
        if len([d for d in defenders if d.y > RUCK_Y]) < n_per_side:
            slot = len([d for d in defenders if d.y > RUCK_Y])
            y = right_ys[slot] if slot < len(right_ys) else interior_right[slot - len(right_ys)]
            defenders.append(make_defender(pid, RUCK_X, y)); pid += 1
    return defenders


tuners = DefenceTuners()
line = make_balanced_line(6)
world = MiniWorld()
assign_depth_roles(line, world)
targets = compute_targets(world, line, tuners)

print(f"role_width_anchor = {tuners.role_width_anchor}")
print(f"RUCK_ADJACENT_A_Y_OFFSET_M = {getattr(tuners, 'RUCK_ADJACENT_A_Y_OFFSET_M', 2.0)}")
print()
print(f"{'id':>3} {'side':>4} {'role':>7} {'chain_y':>9} {'role_anchor':>11} {'diff':>8}")

# Even-lane expectation per side, between A-anchor (2.0) and WINGER (half-width).
for side in (-1, 1):
    side_defs = [d for d in line if d.depth_side == side]
    if not side_defs:
        continue
    ys = sorted(targets[id(d)] for d in side_defs) if side == 1 else sorted(
        (targets[id(d)] for d in side_defs), reverse=True)
    n = len(side_defs)
    a_y = RUCK_Y + side * 2.0
    w_y = RUCK_Y + side * WIDTH * 0.5
    even = [a_y + (w_y - a_y) * i / (n - 1) for i in range(n)]
    print(f"\n-- side {side}: n={n} even-model=[{', '.join(f'{v:.3f}' for v in even)}]")
    for d in sorted(side_defs, key=lambda d: side * targets[id(d)]):
        cy = targets[id(d)]
        ra = role_anchor_y_for(d.depth_role, d.depth_side, RUCK_Y, WIDTH, tuners)
        print(f"{d.player_id:>3} {side:>4} {d.depth_role:>7} {cy:>9.3f} {ra:>11.3f} {cy-ra:>8.3f}")

# Also show the exact even-progression vs role-anchor progression for one full side.
print("\n== ROLE-ANCHOR progression (6 roles) ==")
for r in ("A", "B", "C", "D", "CENTRE", "WINGER"):
    ra = role_anchor_y_for(r, 1, RUCK_Y, WIDTH, tuners)
    print(f"  {r:>7} = {ra:.3f} (offset from ruck: {abs(ra-RUCK_Y):.3f})")
