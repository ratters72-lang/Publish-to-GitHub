import sys
import types

sys.path.insert(0, ".")

from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_split_spacing import side_widths
from game.defence.defence_lvl_1.defensive_depth_roles import (
    _get_role_anchors,
    _bootstrap_side_roles,
    _maintain_side,
)


def mk(i, y, role=None):
    d = types.SimpleNamespace()
    d.player_id = i
    d.y = float(y)
    d.x = 68.0
    d.depth_role = role
    d.depth_side = -1
    d.fullback_role_active = False
    d.defender_role = None
    d.tackle_state_active = False
    d._depth_seeded = True if role is not None else False
    d._force_innermost_seed = False
    return d


# ── SHORT-SIDE NUMBERS-BASED LADDER (2026-08-31) ──
# ruck_y=12 → LEFT is short.  The W/C ladder collapses by count so the
# ruck-adjacent A pivot always exists FIRST:
#   n=1 -> A                     (single ruck-adjacent pivot, no WINGER)
#   n=2 -> WINGER + A            (skip CENTRE; the pivot wins)
#   n>=3 -> WINGER + CENTRE + A  (+ B, C, D by lane order)

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
tuners = DefenceTuners()
left_width, right_width = side_widths(world, tuners)
anchors = _get_role_anchors(
    world.ruck_x_m, world.ruck_y_m, left_width, right_width, tuners
)

# ── BOOTSTRAP ──
# n=1 short side -> A only
d = mk(0, 10.5)
_bootstrap_side_roles([d], -1, 12.0, anchors, is_short_side=True)
assert d.depth_role == "A", f"bootstrap n=1 expected A, got {d.depth_role}"

# n=2 short side -> WINGER + A (no CENTRE)
d0, d1 = mk(0, 6.0), mk(1, 10.5)
_bootstrap_side_roles([d0, d1], -1, 12.0, anchors, is_short_side=True)
assert d0.depth_role == "WINGER", f"bootstrap n=2 winger expected WINGER, got {d0.depth_role}"
assert d1.depth_role == "A", f"bootstrap n=2 A expected A, got {d1.depth_role}"

# n=3 short side -> WINGER + CENTRE + A
d0, d1, d2 = mk(0, 6.0), mk(1, 8.0), mk(2, 10.5)
_bootstrap_side_roles([d0, d1, d2], -1, 12.0, anchors, is_short_side=True)
assert d0.depth_role == "WINGER", f"bootstrap n=3 winger {d0.depth_role}"
assert d1.depth_role == "CENTRE", f"bootstrap n=3 centre {d1.depth_role}"
assert d2.depth_role == "A", f"bootstrap n=3 A {d2.depth_role}"

# n=4 short side -> WINGER + CENTRE + A + B (closest-to-ruck is A)
d0, d1, d2, d3 = mk(0, 6.0), mk(1, 8.0), mk(2, 10.5), mk(3, 11.0)
_bootstrap_side_roles([d0, d1, d2, d3], -1, 12.0, anchors, is_short_side=True)
assert d0.depth_role == "WINGER", f"bootstrap n=4 winger {d0.depth_role}"
assert d1.depth_role == "CENTRE", f"bootstrap n=4 centre {d1.depth_role}"
assert d3.depth_role == "A", f"bootstrap n=4 A expected d3 (11.0, closest), got {d3.depth_role}"
assert d2.depth_role == "B", f"bootstrap n=4 B expected d2 (10.5), got {d2.depth_role}"

# ── OPEN-SIDE REGRESSION: is_short_side=False keeps the full canonical ladder ──
d0, d1, d2 = mk(0, 6.0), mk(1, 8.0), mk(2, 10.5)
_bootstrap_side_roles([d0, d1, d2], -1, 12.0, anchors, is_short_side=False)
assert d0.depth_role == "WINGER", f"open n=3 winger {d0.depth_role}"
assert d1.depth_role == "CENTRE", f"open n=3 centre {d1.depth_role}"
assert d2.depth_role == "A", f"open n=3 A {d2.depth_role}"

# ── MAINTENANCE ──
# n=2 short side, pre-seeded [WINGER, CENTRE] -> CENTRE demoted, A promoted
d0, d1 = mk(0, 6.0, "WINGER"), mk(1, 10.5, "CENTRE")
_maintain_side([d0, d1], -1, anchors, 12.0, is_short_side=True)
assert d0.depth_role == "WINGER", f"maintain n=2 winger {d0.depth_role}"
assert d1.depth_role == "A", f"maintain n=2 A {d1.depth_role}"

# n=1 short side, pre-seeded [WINGER] -> WINGER demoted, A promoted
d0 = mk(0, 10.5, "WINGER")
_maintain_side([d0], -1, anchors, 12.0, is_short_side=True)
assert d0.depth_role == "A", f"maintain n=1 expected A, got {d0.depth_role}"

# Open-side maintain regression: full ladder preserved, no demotion
d0, d1, d2, d3 = (
    mk(0, 6.0, "WINGER"),
    mk(1, 8.0, "CENTRE"),
    mk(2, 10.5, "A"),
    mk(3, 11.0, "B"),
)
_maintain_side([d0, d1, d2, d3], -1, anchors, 12.0, is_short_side=False)
assert d0.depth_role == "WINGER", f"open maintain winger {d0.depth_role}"
assert d1.depth_role == "CENTRE", f"open maintain centre {d1.depth_role}"
assert d2.depth_role == "A", f"open maintain A {d2.depth_role}"
assert d3.depth_role == "B", f"open maintain B {d3.depth_role}"

print("ALL SHORT-SIDE LADDER CHECKS PASSED")
