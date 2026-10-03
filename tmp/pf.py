"""pf.py - who owns the final 70-pin on interior lane holders?"""
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from verify_width_lanes import (
    MiniWorld, make_lane_line, make_defender, reset_per_frame,
)
from player.player import Player
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_behaviour_unified import (
    DefensiveBehaviourUnified,
)
from game.defence.defence_lvl_1.defensive_depth_roles import assign_depth_roles
from game.defence.defence_lvl_1.defensive_width import lane_x_of

world = MiniWorld()
line = make_lane_line()
world.defending_team = line
world.ball_carrier = make_defender(99, 40.0, 35.0)
tuners = DefenceTuners()
dbu = DefensiveBehaviourUnified(tuners)
assign_depth_roles(line, world)

print("roles after assign:",
      [(d.player_id, d.depth_role, d.depth_side) for d in line])

world.frame_count = 1
reset_per_frame(line)
dbu.apply(world, line, 0.0, [])

for d in line:
    print(
        f"id={d.player_id} x={d.x:.1f} y={d.y:.1f} "
        f"role={d.depth_role} class={d.depth_class} "
        f"tx={d.target_x:.2f} src={d.target_source} "
        f"ms={d.movement_state} lane={lane_x_of(d, world, tuners)}"
    )

# Also dump the frame width dict
wd = getattr(world, "frame_width_targets", None)
print("frame_width_targets:",
      {k.__class__.__name__ if not hasattr(k, 'player_id') else v
       for k, v in []} if not wd else {
           next((d2.player_id for d2 in line if id(d2) == k), k): round(v, 2)
           for k, v in wd.items()})
print("governor_width_active:", world.governor_width_active)
