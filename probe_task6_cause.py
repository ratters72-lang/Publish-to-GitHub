"""probe_task6_cause.py — why verify_task6_chase.py returns HOLD instead of CHASE.

One-shot diagnosis.  Nothing here is production code.

The verifier builds a BARE fixture (a Player with only depth_class and
movement_state set, inside a SimpleNamespace world with no seed fields) and
expects decide_movement_state -> CHASE for an engaged defender ahead of a
carrier that is past the line.  It gets HOLD.

This script (a) prints each gate's read on that fixture, and (b) re-runs the
same call with the seed-staleness predicate forced False, to show the seed gate
is the sole thing between the fixture and CHASE.
"""

import sys
import types

sys.path.insert(0, ".")

from player.player import Player
from player.movement_states import MovementState
from game.defence.defence_lvl_1 import movement_mode_decision as mmd
from game.defence.defence_lvl_1 import seed_hold
from game.defence.defence_lvl_1.anchor_authority import defensive_line_anchor_m


def make_world(ruck_x=40.0, bc_x=52.0, frame=0):
    """Exactly the verifier's world."""
    world = types.SimpleNamespace(
        ruck_x_m=ruck_x,
        tes_active=False,
        frame_count=frame,
        defensive_line_anchor=ruck_x + 10.0,
        anchor_locked=False,
        ball_carrier=None,
        sub_state=None,
        dt=1.0 / 60.0,
    )
    bc = Player(bc_x, 35.0, player_id=999)
    world.ball_carrier = bc
    return world, bc


def make_defender(x, y, depth_class, movement_state, player_id):
    """Exactly the verifier's defender."""
    d = Player(x, y, player_id=player_id)
    d.is_defender = True
    d.max_defensive_speed_mps = 8.3
    d.depth_class = depth_class
    d.movement_state = movement_state
    d._mmdl_state_until_frame = -1
    return d


def main():
    world, bc = make_world()
    ahead = make_defender(55.0, 31.0, "PRIMARY", MovementState.SLIDE, 1)

    print("=== the verifier's fixture, read gate by gate ===")
    try:
        print("anchor_x (engine helper)      :", defensive_line_anchor_m(world))
    except Exception as exc:
        print("anchor_x (engine helper) raised:", type(exc).__name__, exc)
    print("carrier_past_defensive_line   :",
          mmd.carrier_past_defensive_line(world, bc))
    print("is_in_pursuit_commit_range    :",
          mmd.is_in_pursuit_commit_range(ahead, bc, 12.0))
    print("depth_role on the fixture     :", getattr(ahead, "depth_role", None))
    print("ruck_established on the world :",
          getattr(world, "ruck_established", "<absent -> defaults True>"))
    print("is_seed_stale(fixture)        :", seed_hold.is_seed_stale(ahead, world))
    print("decide_movement_state(fixture):",
          mmd.decide_movement_state(world, ahead, bc, tuners=None))

    print("")
    print("=== same call, seed-staleness predicate forced False ===")
    original = seed_hold.is_seed_stale
    seed_hold.is_seed_stale = lambda *a, **kw: False
    try:
        state = mmd.decide_movement_state(world, ahead, bc, tuners=None)
    finally:
        seed_hold.is_seed_stale = original
    print("decide_movement_state         :", state)
    print("")
    if state == MovementState.CHASE:
        print("CONCLUSION: the STALE-SEED FORCE-HOLD gate is the sole cause.")
        print("The fixture carries no depth_role / depth_side / _depth_seeded /")
        print("role_depth_target_* / frame_spacing_targets, so is_seed_stale")
        print("returns True and MMDL returns HOLD before the TASK VI")
        print("carrier-past-line CHASE override is ever reached.")
    else:
        print("CONCLUSION: NOT the seed gate — another branch also holds it:",
              state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
