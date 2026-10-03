"""Smoke test: even per-side ruck spacing at 6/4 split."""
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_split_spacing import compute_targets
from game.defence.defence_lvl_1.defensive_depth_roles import assign_depth_roles


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
        depth_roles_frame = None
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

    # 10 defenders: 6 right of ruck (y > 35), 4 left (y < 35).
    line = []
    for i in range(6):
        line.append(make_defender(i + 1, 63.0, 38.0 + i * 1.0))
    for i in range(4):
        line.append(make_defender(20 + i, 63.0, 31.0 - i * 1.0))
    world.defending_team = line
    assign_depth_roles(line, world)

    targets = compute_targets(world, line, tuners)

    print("Per-defender even spacing:")
    for d in sorted(line, key=lambda o: o.y):
        ty = targets.get(id(d))
        print(f"  id={d.player_id} side={getattr(d, 'depth_side', None)} "
              f"role={getattr(d, 'depth_role', None)} y={d.y:.2f} -> target_y={ty:.2f}")

    right = [d for d in line if getattr(d, "depth_side", 0) == 1]
    left = [d for d in line if getattr(d, "depth_side", 0) == -1]
    print(f"\nside counts: right={len(right)} left={len(left)}")

    a_right = [d for d in right if getattr(d, "depth_role", None) == "A"]
    w_right = [d for d in right if getattr(d, "depth_role", None) == "WINGER"]
    if a_right and w_right:
        print(f"RIGHT A y={targets[id(a_right[0])]:.2f} (expect 37.0) "
              f"WINGER y={targets[id(w_right[0])]:.2f} (expect 55.0)")
    a_left = [d for d in left if getattr(d, "depth_role", None) == "A"]
    w_left = [d for d in left if getattr(d, "depth_role", None) == "WINGER"]
    if a_left and w_left:
        print(f"LEFT  A y={targets[id(a_left[0])]:.2f} (expect 33.0) "
              f"WINGER y={targets[id(w_left[0])]:.2f} (expect 15.0)")

    right_ys = sorted({round(targets[id(d)], 4) for d in right if id(d) in targets})
    print(f"RIGHT target ys={right_ys}")
    gaps = [round(right_ys[i] - right_ys[i - 1], 4) for i in range(1, len(right_ys))]
    print(f"RIGHT gaps={gaps}")

    left_ys = sorted({round(targets[id(d)], 4) for d in left if id(d) in targets})
    print(f"LEFT target ys={left_ys}")
    lgaps = [round(left_ys[i] - left_ys[i - 1], 4) for i in range(1, len(left_ys))]
    print(f"LEFT gaps={lgaps}")

    assert len(right_ys) == 6, right_ys
    assert len(left_ys) == 4, left_ys
    if a_right and w_right:
        assert abs(targets[id(a_right[0])] - 37.0) < 1e-6, targets[id(a_right[0])]
        assert abs(targets[id(w_right[0])] - 55.0) < 1e-6, targets[id(w_right[0])]
    if a_left and w_left:
        assert abs(targets[id(a_left[0])] - 33.0) < 1e-6, targets[id(a_left[0])]
        assert abs(targets[id(w_left[0])] - 15.0) < 1e-6, targets[id(w_left[0])]
    print("\nSMOKE OK")


if __name__ == "__main__":
    main()
