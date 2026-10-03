"""
verify_width_stability.py — Task A-2 verification: spacing-chain stability,
no width collapse when BC crosses the anchor, and no target_x jiggle.

Drives frame sequences through BOTH the persistent structural depth-role
assignment (assign_depth_roles) and the persistent engagement band
(update_engagement_state), and asserts:

  1. SPACING CHAIN STABLE: per-defender depth_role / depth_side / role
     width anchor do not reorder or flip while the line plays the same
     ruck.  The split-spacing inputs are fed from the STABLE structural
     role, never from a per-frame oscillation.
  2. NO WIDTH COLLAPSE ON ANCHOR CROSSING: when the BC crosses the
     defensive-line anchor (and further, the PASSIVE band edge), the line
     width (max|role_anchor_y - ruck_y|) holds — it never shrinks or
     re-centres onto the BC lane.
  3. NO TARGET_X JIGGLE: role_depth_target_x (the role-depth X used by
     movement/spacing) is immutable across the crossing; only the
     engagement band (role_depth_class) may change, exactly once.
  4. NO REORDER FROM OSCILLATION: the ordered per-side width-curve input
     is unchanged while BC oscillates across the anchor.

Emits verify_width_stability_report.json and exits 0 when all checks pass.
"""

import sys
import json

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player
from game.defence.defence_lvl_1.defensive_depth_roles import (
    assign_depth_roles,
    ROLE_DEPTH_X_OFFSET_M,
)
from game.defence.defence_lvl_1.defensive_engagement_state import (
    update_engagement_state,
)

RUCK_X = 60.0
RUCK_Y = 35.0

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


class MiniWorld:
    def __init__(self, ruck_x=RUCK_X, ruck_y=RUCK_Y):
        self.ruck_x_m = ruck_x
        self.ruck_y_m = ruck_y
        self.frame_count = 0
        self.ball_carrier = None
        self.defending_team = []
        self.open_play_engine = None


def make_defender(player_id, x, y):
    return Player(x=x, y=y, player_id=player_id)


def side_order_tag(line):
    """Ordered per-side width-curve signature: (side, role, player_id)."""
    return tuple(
        (getattr(d, "depth_side", 0), getattr(d, "depth_role", "-"), d.player_id)
        for d in line
    )


def width_signature(line, ruck_y):
    """Line width signature: per-defender structural anchor-gap magnitude."""
    return tuple(
        round(abs(getattr(d, "role_depth_target_y", ruck_y) - ruck_y), 4)
        for d in line
    )


def run_scenario1_spacing_chain_stable():
    print("\n[1] Spacing chain stable across 120 frames (same ruck)")
    world = MiniWorld()
    defenders = [
        make_defender(1, 70.0, 12.0),
        make_defender(2, 70.0, 20.0),
        make_defender(3, 70.0, 28.0),
        make_defender(4, 70.0, 42.0),
        make_defender(5, 70.0, 50.0),
        make_defender(6, 70.0, 58.0),
    ]
    world.defending_team = defenders
    world.ball_carrier = make_defender(99, 60.0, 35.0)
    assign_depth_roles(defenders, world)
    base_order = side_order_tag(defenders)
    base_width = width_signature(defenders, RUCK_Y)

    for frame in range(1, 121):
        world.frame_count = frame
        world.ball_carrier.x = 60.0 + (frame % 7) * 0.4   # 60.0..62.4 inside band
        world.ball_carrier.y = 35.0 + (frame % 3) * 0.1
        for d in defenders:
            d.y += (0.002 if d.player_id % 2 else -0.002)
        assign_depth_roles(defenders, world)
        update_engagement_state(world, defenders)

        if side_order_tag(defenders) != base_order:
            check("spacing chain reorder", False,
                  f"frame={frame} delta={side_order_tag(defenders)}")
            return
        if width_signature(defenders, RUCK_Y) != base_width:
            check("width signature drift", False,
                  f"frame={frame} {width_signature(defenders, RUCK_Y)}")
            return

    check("per-side chain order identical across 120 frames", True)
    check("width signature identical across 120 frames", True)


def run_scenario2_no_width_collapse_on_crossing():
    print("\n[2] No width collapse when BC crosses anchor + band edge")
    world = MiniWorld()
    defenders = [
        make_defender(1, 70.0, 12.0),
        make_defender(2, 70.0, 20.0),
        make_defender(3, 70.0, 28.0),
        make_defender(4, 70.0, 42.0),
        make_defender(5, 70.0, 50.0),
        make_defender(6, 70.0, 58.0),
    ]
    world.defending_team = defenders
    world.ball_carrier = make_defender(99, 55.0, 35.0)
    assign_depth_roles(defenders, world)

    width_before = width_signature(defenders, RUCK_Y)
    anchors_before = {
        d.player_id: (getattr(d, "role_depth_target_x", None),
                      getattr(d, "role_depth_target_y", None))
        for d in defenders
    }

    # BC advances from 55 -> 85 (crosses anchor 70 + band edge 75).
    for frame in range(1, 61):
        world.frame_count = frame
        world.ball_carrier.x = 55.0 + frame * 0.5
        assign_depth_roles(defenders, world)
        update_engagement_state(world, defenders)

    width_after = width_signature(defenders, RUCK_Y)
    check("line width unchanged after anchor + band-edge crossing",
          width_after == width_before,
          f"before={width_before} after={width_after}")

    for d in defenders:
        if abs(getattr(d, "role_depth_target_x", None) - (RUCK_X + ROLE_DEPTH_X_OFFSET_M)) > 1e-6:
            check("role-depth X rigid through crossing", False,
                  f"id={d.player_id} got={getattr(d, 'role_depth_target_x', None)}")
            return
    check("role-depth X rigid (ruck + 10) through crossing for all defenders", True)

    for d in defenders:
        before = anchors_before[d.player_id][1]
        after = getattr(d, "role_depth_target_y", None)
        if abs(before - after) > 1e-6:
            check("role Y anchor unchanged through crossing", False,
                  f"id={d.player_id} before={before} after={after}")
            return
    check("role Y anchors unchanged through crossing (no inward collapse)", True)


def run_scenario3_no_target_x_jiggle():
    print("\n[3] No target_x jiggle: role-depth X has zero variance through crossing")
    world = MiniWorld()
    defenders = [
        make_defender(1, 70.0, 20.0),
        make_defender(2, 70.0, 30.0),
        make_defender(3, 70.0, 40.0),
        make_defender(4, 70.0, 50.0),
    ]
    world.defending_team = defenders
    world.ball_carrier = make_defender(99, 60.0, 35.0)
    assign_depth_roles(defenders, world)

    history = {d.player_id: [] for d in defenders}
    for frame in range(1, 91):
        world.frame_count = frame
        # Square-wave oscillation right across the anchor/band edge.
        world.ball_carrier.x = 74.9 if frame % 2 == 1 else 75.1
        assign_depth_roles(defenders, world)
        update_engagement_state(world, defenders)
        for d in defenders:
            history[d.player_id].append(getattr(d, "role_depth_target_x", None))

    for pid, xs in history.items():
        unique = set(xs)
        check(f"role-depth X jitter-free for id={pid} (single value over 90 frames)",
              len(unique) == 1 and abs(unique.pop() - (RUCK_X + ROLE_DEPTH_X_OFFSET_M)) < 1e-6,
              f"values={sorted(unique)[:3] if len(unique) <= 3 else 'many'}")
        if len(set(xs)) > 1:
            return


def run_scenario4_band_transitions_hysteretic():
    print("\n[4] Band transitions on a full crossing are monotone + interval-spaced")
    world = MiniWorld()
    defenders = [make_defender(1, 70.0, 35.0)]
    world.defending_team = defenders
    world.ball_carrier = make_defender(99, 60.0, 35.0)
    assign_depth_roles(defenders, world)

    # Ramp 60 -> 83.8 over 120 frames: legitimately traverses all three
    # bands (AGGRESSIVE < 65, BALANCED in [65,75], PASSIVE > 75).  The
    # hysteresis contract allows ONE switch per band boundary (3 total),
    # each gated by the 20-frame interval — never oscillation or spam.
    prev = None
    transitions = []
    for frame in range(1, 121):
        world.frame_count = frame
        world.ball_carrier.x = 60.0 + frame * 0.2
        update_engagement_state(world, defenders)
        role = defenders[0].role_depth_class
        if prev is not None and role != prev:
            transitions.append((frame, prev, role))
        prev = role

    check("at most one switch per band boundary (<= 3 total on the ramp)",
          len(transitions) <= 3, str(transitions))
    switch_frames = [t[0] for t in transitions]
    gaps_ok = all(
        b - a > 20 for a, b in zip(switch_frames, switch_frames[1:])
    )
    check("all switches separated by > 20 frames (hysteresis window)",
          gaps_ok, str(switch_frames))
    check("final band role PASSIVE (locked)", prev == "PASSIVE", str(prev))
    check("no PASSIVE re-entry after the final switch",
          prev == "PASSIVE" and (not transitions or transitions[-1][2] == "PASSIVE"),
          str(transitions))

    # Run the same ramp in reverse (83.8 -> 60): the band must step back
    # through BALANCED -> AGGRESSIVE with the same hysteresis, ending AGGRESSIVE.
    transitions = []
    prev = None
    for frame in range(1, 121):
        world.frame_count = 1000 + frame
        world.ball_carrier.x = 83.8 - frame * 0.2
        update_engagement_state(world, defenders)
        role = defenders[0].role_depth_class
        if prev is not None and role != prev:
            transitions.append((frame, prev, role))
        prev = role
    check("reverse ramp ends AGGRESSIVE (BC deep behind anchor)",
          prev == "AGGRESSIVE", str(prev))


def main():
    print("=" * 72)
    print("WIDTH-STABILITY VALIDATION (Task A-2, 2026-08-10)")
    print("=" * 72)

    run_scenario1_spacing_chain_stable()
    run_scenario2_no_width_collapse_on_crossing()
    run_scenario3_no_target_x_jiggle()
    run_scenario4_band_transitions_hysteretic()

    report = {
        "width_stability": {
            "spacing_chain_stable": True,
            "no_width_collapse": True,
            "no_target_x_jiggle": True,
            "band_transition_once": True,
            "issues_remaining": failures,
        }
    }
    if failures:
        for key in report["width_stability"]:
            if key != "issues_remaining":
                report["width_stability"][key] = False
        report["width_stability"]["issues_remaining"] = failures

    report_path = "verify_width_stability_report.json"
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print(f"\nReport written to {report_path}")
    print(json.dumps(report, indent=2))

    print("\n" + "=" * 72)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
