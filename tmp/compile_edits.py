"""Compile-check + geometry smoke: even per-side ruck spacing."""
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import py_compile

from player.player import Player
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_depth_roles import assign_depth_roles
from game.defence.defence_lvl_1.defensive_geometry import DefensiveGeometry
from game.defence.defence_lvl_1.defensive_split_spacing import compute_targets

files = [
    "game/defence/defence_lvl_1/defensive_split_spacing.py",
    "game/defence/defence_lvl_1/defensive_geometry.py",
    "game/defence/defence_lvl_1/defensive_tuners.py",
]
for f in files:
    py_compile.compile(f, doraise=True)
    print(f"COMPILE OK {f}")


def make_defender(player_id, x, y):
    d = Player(x=x, y=y, player_id=player_id)
    d.is_defender = True
    return d


def make_world():
    class _W:
        ball_carrier = None
        ball = None
        ruck_x_m = 60.0
        ruck_y_m = 35.0
        def_line_width_m = 40.0
        defensive_line_anchor = 70.0
        anchor_locked = True
        in_pursuit_mode = False
        first_tackle_not_made = False
        governor_width_active = True
        governor_line_width_m = 40.0
        short_side_width_m = None
        open_side_width_m = None
        sim_time_s = 0.0
        fullback_default_x = 60.0
        fullback_default_y = 35.0
        defending_team = []
        frame_count = 0
        depth_roles_frame = 0
        frame_geometry_ready = False
        frame_geometry_frame = None
        frame_spacing_targets = None
        frame_width_targets = None
        role_depth_state = {}
        depth_role_state = {}
        sub_state = None
        phase = "OPEN_PLAY"

    return _W()


def main():
    tuners = DefenceTuners()
    world = make_world()
    line = []
    for i in range(6):
        line.append(make_defender(i + 1, 63.0, 38.0 + i * 1.0))
    for i in range(4):
        line.append(make_defender(20 + i, 63.0, 31.0 - i * 1.0))
    world.defending_team = line
    assign_depth_roles(line, world)

    geometry = DefensiveGeometry(tuners)
    role_targets = geometry.compute_role_targets(world, line)
    chain = compute_targets(world, line, tuners)

    # A/WINGER anchors agree between geometry and chain.
    for d in line:
        role = getattr(d, "depth_role", None)
        if role not in ("A", "WINGER", "B"):
            continue
        g_y = role_targets[id(d)][1]
        c_y = chain.get(id(d))
        assert abs(g_y - c_y) < 1e-6, (
            f"id={d.player_id} role={role} geom_y={g_y:.3f} chain_y={c_y:.3f}"
        )
        print(
            f"id={d.player_id} side={getattr(d, 'depth_side', None)} "
            f"role={role} geom_y={g_y:.3f}"
        )

    a = [d for d in line if getattr(d, "depth_role", None) == "A"]
    w = [d for d in line if getattr(d, "depth_role", None) == "WINGER"]
    assert a and w, "no A/WINGER roles assigned"
    for d in a:
        assert abs(chain[id(d)] - (world.ruck_y_m + getattr(d, "depth_side", 0) * 2.0)) < 1e-6, chain[id(d)]
    for d in w:
        assert abs(chain[id(d)] - (world.ruck_y_m + getattr(d, "depth_side", 0) * 20.0)) < 1e-6, chain[id(d)]
    print("GEOMETRY + CHAIN AGREE, A/WINGER ANCHORS CORRECT")


if __name__ == "__main__":
    main()
