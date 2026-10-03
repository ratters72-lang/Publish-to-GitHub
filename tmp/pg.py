"""pg.py - trace recompute_width_x inputs/outputs inside dbu.apply."""
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import game.defence.defence_lvl_1.defensive_behaviour_unified as dbu_mod
from game.defence.defence_lvl_1 import defensive_width as dw
from verify_width_lanes import (
    MiniWorld, make_lane_line, make_defender, reset_per_frame,
)
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_behaviour_unified import (
    DefensiveBehaviourUnified,
)
from game.defence.defence_lvl_1.defensive_depth_roles import assign_depth_roles

orig = dw.recompute_width_x


def traced(world, defs, tuners, **kw):
    out = orig(world, defs, tuners, **kw)
    print("recompute_width_x ->", len(out), "entries;",
          {getattr(d, "player_id", "?"): round(v, 2) for d, v in []} if not out else {
              getattr(k, "player_id", hex(id(k))): round(float(val), 2)
              for k, val in out.items()})
    # show why each defender was refused
    from game.defence.defence_lvl_1.width_authority import is_width_eligible
    for d in defs:
        ok = is_width_eligible(d)
        print(f"   id={d.player_id} role={d.depth_role} "
              f"class={d.depth_class} eligible={ok}")
    return out


dw.recompute_width_x = traced
# also patch the name inside dbu module namespace if imported directly
if hasattr(dbu_mod, "recompute_width_x"):
    dbu_mod.recompute_width_x = traced

world = MiniWorld()
line = make_lane_line()
world.defending_team = line
world.ball_carrier = make_defender(99, 40.0, 35.0)
tuners = DefenceTuners()
dbu = DefensiveBehaviourUnified(tuners)
assign_depth_roles(line, world)

world.frame_count = 1
reset_per_frame(line)
dbu.apply(world, line, 0.0, [])
