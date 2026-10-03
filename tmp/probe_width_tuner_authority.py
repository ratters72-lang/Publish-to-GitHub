"""
probe_width_tuner_authority.py — measure how much realised defensive width
responds to the 3-point width tuners (width_left/mid/right_tryline_m) versus
the count-lane geometry parameters (defender_gap_m, min_defender_gap_m).

READ-ONLY DIAGNOSTIC.  No engine files are modified.
"""

import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_split_spacing import (
    compute_targets,
    side_lanes,
    LEFT,
    RIGHT,
)
from game.defence.defence_lvl_1.defensive_line_governor import (
    DefensiveLineGovernor,
)
from game.defence.defence_lvl_1.defensive_width import (
    interpolated_defensive_width,
)
from world.world_state import GameSubState

RUCK_X = 60.0
RUCK_Y = 35.0


class MiniWorld:
    def __init__(self, ruck_y=RUCK_Y):
        self.ruck_x_m = RUCK_X
        self.ruck_y_m = ruck_y
        self.def_line_width_m = 40.0
        self.governor_width_active = True
        self.governor_line_width_m = 40.0
        self.frame_count = 1
        self.dt = 1.0 / 60.0
        self.ball_carrier = None
        self.defending_team = []
        self.in_pursuit_mode = False
        self.sub_state = GameSubState.OPEN_PLAY
        self.defensive_line_anchor = 70.0
        self.anchor_locked = False
        self.frame_geometry_ready = False
        self.frame_geometry_frame = None
        self.frame_spacing_targets = None
        self.frame_width_targets = None
        self.frame_side_frame = None


def role_defender(role, side, player_id, ruck_y=RUCK_Y):
    d = Player(x=70.0, y=ruck_y + side * 10.0, player_id=player_id)
    d.depth_role = role
    d.depth_side = side
    d.is_defender = True
    return d


_ROLES = ["WINGER", "CENTRE", "D", "C", "B", "A"]


def side_population(n_left, n_right, ruck_y=RUCK_Y):
    line = []
    pid = 100
    for i in range(n_left):
        pid += 1
        line.append(role_defender(_ROLES[min(i, 5)], -1, pid, ruck_y))
    for i in range(n_right):
        pid += 1
        line.append(role_defender(_ROLES[min(i, 5)], 1, pid, ruck_y))
    return line


def realised_span(world, line, tuners, governor):
    targets = compute_targets(world, line, tuners, governor=governor)
    if not targets:
        return 0.0, 0.0
    ys = list(targets.values())
    span = max(ys) - min(ys)
    winger_ys = [
        targets[id(d)] for d in line
        if getattr(d, "depth_role", None) == "WINGER" and id(d) in targets
    ]
    wspan = (max(winger_ys) - min(winger_ys)) if len(winger_ys) >= 2 else 0.0
    return span, wspan


def count_lane_span(n_left, n_right, ruck_y, tuners):
    lanes = side_lanes(n_left, ruck_y, LEFT, tuners) + side_lanes(
        n_right, ruck_y, RIGHT, tuners
    )
    return max(lanes) - min(lanes) if lanes else 0.0


def main():
    print("=" * 78)
    print("WIDTH-TUNER AUTHORITY PROBE")
    print("=" * 78)

    print("\n--- A. What the width tuners FEED (interpolation only) ---")
    for label, vals in (
        ("default", (50.0, 55.0, 60.0)),
        ("narrow", (30.0, 32.0, 34.0)),
        ("wide", (65.0, 68.0, 70.0)),
    ):
        t = DefenceTuners()
        t.width_left_tryline_m, t.width_halfway_m, t.width_right_tryline_m = vals
        w = interpolated_defensive_width(t, 60.0)
        print(f"  tuners {label:8s} {vals} -> interpolated @halfway = {w:.2f} m")

    print("\n--- B. Realised span vs width tuner (governor clamp ON) ---")
    n_left, n_right = 4, 4
    line = side_population(n_left, n_right)
    for label, vals in (
        ("default", (50.0, 55.0, 60.0)),
        ("+20%", (60.0, 66.0, 72.0)),
        ("+60%", (80.0, 88.0, 96.0)),
        ("-25%", (37.5, 41.25, 45.0)),
        ("-50%", (25.0, 27.5, 30.0)),
    ):
        t = DefenceTuners()
        t.width_left_tryline_m, t.width_halfway_m, t.width_right_tryline_m = vals
        w = MiniWorld()
        w.def_line_width_m = interpolated_defensive_width(t, 60.0)
        w.governor_line_width_m = w.def_line_width_m
        gov = DefensiveLineGovernor(t, None)
        span, wspan = realised_span(w, line, t, gov)
        print(
            f"  {label:8s} {str(vals):24s} governed={w.def_line_width_m:6.2f} "
            f"realised={span:6.2f} winger={wspan:6.2f}"
        )

    print("\n--- C. Realised span vs defender_gap_m (the actual lane driver) ---")
    for gap in (3.0, 3.5, 4.0, 5.0, 6.0, 8.0):
        t = DefenceTuners()
        t.defender_gap_m = gap
        w = MiniWorld()
        w.def_line_width_m = interpolated_defensive_width(t, 60.0)
        w.governor_line_width_m = w.def_line_width_m
        gov = DefensiveLineGovernor(t, None)
        span, wspan = realised_span(w, line, t, gov)
        cls = count_lane_span(n_left, n_right, RUCK_Y, t)
        print(
            f"  defender_gap_m={gap:4.1f} count-lane={cls:6.2f} "
            f"realised={span:6.2f} winger={wspan:6.2f}"
        )

    print("\n--- D. Governor clamp OFF vs ON, default tuners ---")
    t = DefenceTuners()
    w = MiniWorld()
    w.def_line_width_m = 40.0
    gov = DefensiveLineGovernor(t, None)
    so, wo = realised_span(w, line, t, None)
    sn, wn = realised_span(w, line, t, gov)
    print(f"  governor=None        span={so:6.2f} winger={wo:6.2f}")
    print(f"  governor=<instance>  span={sn:6.2f} winger={wn:6.2f}")

    print("\n--- E. Span vs ruck_y (field position), default tuners ---")
    t = DefenceTuners()
    for ruck_y in (10.0, 20.0, 35.0, 50.0, 60.0):
        line_r = side_population(n_left, n_right, ruck_y)
        w = MiniWorld(ruck_y)
        w.def_line_width_m = interpolated_defensive_width(t, 60.0)
        w.governor_line_width_m = w.def_line_width_m
        gov = DefensiveLineGovernor(t, None)
        span, wspan = realised_span(w, line_r, t, gov)
        print(f"  ruck_y={ruck_y:5.1f} realised={span:6.2f} winger={wspan:6.2f}")

    print("\n--- F. Does the per-side COUNT change realised width? ---")
    for (nl, nr) in ((3, 3), (4, 4), (5, 5), (6, 4)):
        line_c = side_population(nl, nr)
        t = DefenceTuners()
        w = MiniWorld()
        w.def_line_width_m = interpolated_defensive_width(t, 60.0)
        w.governor_line_width_m = w.def_line_width_m
        gov = DefensiveLineGovernor(t, None)
        span, wspan = realised_span(w, line_c, t, gov)
        print(
            f"  split {nl}-{nr} (n={nl+nr}) realised={span:6.2f} winger={wspan:6.2f}"
        )


if __name__ == "__main__":
    main()
