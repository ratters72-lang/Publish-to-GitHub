import sys
import types

sys.path.insert(0, ".")

from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_depth_roles import assign_depth_roles


def mk(i, y):
    d = types.SimpleNamespace()
    d.player_id = i
    d.y = float(y)
    d.x = 68.0
    d.depth_role = None
    d.depth_side = None
    d.frame_side = 0
    d.fullback_role_active = False
    d.defender_role = None
    d.tackle_state_active = False
    d._depth_seeded = False
    d._force_innermost_seed = False
    d.role_depth_target_x = 0.0
    d.role_depth_target_y = 0.0
    return d


# ruck_y=12 -> LEFT is short (dist_to_left=9 < dist_to_right=55).
# n_total=10 with dist 9 -> n_short=round(10*0.1533)=2 -> LEFT gets 2.
world = types.SimpleNamespace()
world.ruck_y_m = 12.0
world.ruck_x_m = 60.0
world.def_line_width_m = 55.0
world.ruck_established = True
world.governor_width_active = False
world.ball_carrier = None
world.sub_state = "open_play"
world.just_left_kickoff_timer = 0.0
world.kickoff_side_sign = None
world.open_play_engine = None

defs = [
    mk(0, 6.0),   # left band (closest to A/WINGER band centre ~8)
    mk(1, 8.0),   # left band
    mk(2, 10.5),  # near ruck
    mk(3, 11.0),  # near ruck
    mk(4, 14.0),  # right
    mk(5, 18.0),
    mk(6, 22.0),
    mk(7, 26.0),
    mk(8, 30.0),
    mk(9, 34.0),
]

result = assign_depth_roles(defs, world)

left = result["LEFT"]
right = result["RIGHT"]

# LEFT (short side) should have n=2 -> WINGER + A (no CENTRE).
left_roles = sorted([(d.player_id, d.depth_role) for d in left], key=lambda t: t[0])
assert len(left) == 2, f"expected 2 LEFT defenders, got {len(left)}: {left_roles}"
assert [d.depth_role for d in left] == ["WINGER", "A"], f"short-side ladder expected [WINGER, A], got {[d.depth_role for d in left]}"

# RIGHT (open side) should keep the full canonical single-capacity ladder.
right_roles = [d.depth_role for d in right]
assert "WINGER" in right_roles, f"open side missing WINGER: {right_roles}"
assert "CENTRE" in right_roles, f"open side missing CENTRE: {right_roles}"
assert "A" in right_roles, f"open side missing A: {right_roles}"
assert len(right) == 8, f"expected 8 RIGHT defenders, got {len(right)}"

# Verify every defender got a valid depth_role.
for d in defs:
    assert d.depth_role in ("A", "B", "C", "D", "CENTRE", "WINGER"), (
        f"defender {d.player_id} has invalid/incomplete depth_role {d.depth_role}"
    )

print("ALL SHORT-SIDE E2E WIRING CHECKS PASSED")
