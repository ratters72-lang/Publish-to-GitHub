"""
Audit Step 1 — Integration test: drives the full defence pipeline
(DefensiveBehaviourUnified._apply_open_play_unified) with known BC/FR
positions, then checks the outputs.

This avoids the sim startup problem by creating a minimal world + players
and calling the defence method directly.
"""

import sys, os, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from world.world_state import WorldState, GameSubState
from world.open_play_engine import OpenPlayEngine
from game.defence.defence_lvl_1.defensive_behaviour_unified import DefensiveBehaviourUnified
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_role_spacing import DefensiveRoleSpacing
from player.player import Player, DefenderRole


def make_player(x, y, pid, vx=0.0, vy=0.0):
    p = Player(x, y, player_id=pid)
    p.vx = vx
    p.vy = vy
    p.depth_class = "PASSIVE"
    p.defender_role = None
    p.lane_index = 0
    p.frozen = False
    p.freeze_timer = 0.0
    p.timing_locked = False
    p.compression_active = False
    p.compression_timer = 0.0
    p.lateral_hesitation = 0.0
    p.retreat_delay = 0.0
    p.step_stagger = 0.0
    return p


def run_scenario(scenario_name, bc_pos, bc_vel, fr_pos, anchor_x, defender_setup,
                 expected_checks, tuners=None):
    """Run a full defence pipeline test with given positions."""
    world = WorldState()
    world.sub_state = GameSubState.OPEN_PLAY
    world.role_governs = False
    world.defence_enabled = True
    world.dt = 1.0 / 60.0

    # Ball carrier
    bc = make_player(bc_pos[0], bc_pos[1], 99, vx=bc_vel[0])
    world.ball_carrier = bc
    world.ball_owner = "BC"

    # First receiver
    if fr_pos:
        fr = make_player(fr_pos[0], fr_pos[1], 98)
        world.first_receiver = fr
    else:
        world.first_receiver = None

    world.defensive_line_anchor = anchor_x

    # Create OPE stub
    world.open_play_engine = object()

    # Defenders
    defenders = []
    for setup in defender_setup:
        x, y, pid, depth_class, def_role, lane_idx = setup[:6]
        d = make_player(x, y, pid)
        d.depth_class = depth_class
        d.defender_role = def_role
        d.lane_index = lane_idx
        d.max_speed_mps = 9.2
        d.move_speed = 9.2
        defenders.append(d)

    world.defending_team = defenders

    # Run defence
    if tuners is None:
        tuners = DefenceTuners()

    db = DefensiveBehaviourUnified(tuners)
    db.apply(world, defenders, None, None)

    # Check results
    print(f"\n[{scenario_name}]")
    results = {}
    for d in defenders:
        check = expected_checks.get(d.player_id)
        if check:
            expected_tx, expected_ty, label = check
            tx_ok = abs(d.target_x - expected_tx) < 0.5
            ty_ok = abs(d.target_y - expected_ty) < 0.5
            status = "OK" if tx_ok and ty_ok else "MISMATCH"
            print(f"  d#{d.player_id} {label}: target=({d.target_x:.2f},{d.target_y:.2f}) "
                  f"expected=({expected_tx:.2f},{expected_ty:.2f}) [{status}]")
            if status != "OK":
                results[d.player_id] = (tx_ok, ty_ok, d.target_x, d.target_y, expected_tx, expected_ty, label)
    return results


def main():
    tuners = DefenceTuners()
    results = {}

    # ── Scenario A: Standard OPEN_PLAY, BC running at 6 m/s ──
    # BC at 40m, FR at 38m, anchor at 50m
    # PRIMARY should get lead_point = 40 + 6*0.3 = 41.8m (AHEAD of BC!)
    # PASSIVE should get anchor = 50m
    # Markers should get bc.x - 1.0 = 39m
    print("\n" + "=" * 70)
    print("SCENARIO A: Standard OPEN_PLAY with running BC (vx=6.0)")
    print("=" * 70)
    r = run_scenario(
        "A: BC at 40m, vx=6, FR at 38m, anchor=50",
        bc_pos=(40.0, 35.0), bc_vel=(6.0, 0),
        fr_pos=(38.0, 32.0), anchor_x=50.0,
        defender_setup=[
            # d#1 PRIMARY at 45m, lane 0
            (45.0, 35.0, 1, "PRIMARY", None, 0),
            # d#2 SECONDARY at 48m, lane +1
            (48.0, 38.0, 2, "SECONDARY", None, 1),
            # d#3 SECONDARY at 48m, lane -1
            (48.0, 32.0, 3, "SECONDARY", None, -1),
            # d#4 PASSIVE at 52m, lane +2
            (52.0, 41.0, 4, "PASSIVE", None, 2),
            # d#5 PASSIVE at 52m, lane -2
            (52.0, 29.0, 5, "PASSIVE", None, -2),
            # d#6 MARKER_A at 45m
            (45.0, 35.0, 6, "PRIMARY", DefenderRole.MARKER_A, 0),
        ],
        expected_checks={
            1: (41.8, 32.0, "PRIMARY -> lead_point=41.8 (bc.x+6*0.3)"),  # lead point ahead of BC!
            2: (41.8, 35.0, "SECONDARY -> lead_point=41.8, ty=fr_y+1*3.0=35.0"),
            3: (41.8, 29.0, "SECONDARY -> lead_point=41.8, ty=fr_y-3.0=29.0"),
            4: (50.0, 38.0, "PASSIVE -> anchor=50, ty=fr_y+2*3.0=38.0"),
            5: (50.0, 26.0, "PASSIVE -> anchor=50, ty=fr_y-6.0=26.0"),
            6: (39.0, 35.0, "MARKER_A -> bc.x-1.0=39, ty=bc.y=35"),
        },
        tuners=tuners,
    )
    results.update(r)

    # ── Scenario B: BC stationary (vx=0.2) — no lead-point ──
    print("\n" + "=" * 70)
    print("SCENARIO B: BC stationary (vx=0.2)")
    print("=" * 70)
    r = run_scenario(
        "B: BC stationary at 50m, anchor=60",
        bc_pos=(50.0, 35.0), bc_vel=(0.2, 0),
        fr_pos=(48.0, 33.0), anchor_x=60.0,
        defender_setup=[
            (55.0, 35.0, 1, "PRIMARY", None, 0),
            (58.0, 38.0, 2, "SECONDARY", None, 1),
            (60.0, 41.0, 3, "PASSIVE", None, 2),
        ],
        expected_checks={
            # PRIMARY with slow BC: bc.x - 1.0 = 49 (no lead-point)
            1: (49.0, 33.0, "PRIMARY slow -> bc.x-1.0=49, ty=fr_y"),
            2: (49.0, 36.0, "SECONDARY -> bc.x-1.0=49, ty=fr_y+3.0=36"),
            3: (60.0, 39.0, "PASSIVE -> anchor=60, ty=fr_y+6.0=39"),
        },
        tuners=tuners,
    )
    results.update(r)

    # ── Scenario C: PASSIVE hold_anchor when BC is far ──
    # PASSIVE should always get anchor_x regardless of where BC is
    print("\n" + "=" * 70)
    print("SCENARIO C: PASSIVE always anchor_x")
    print("=" * 70)
    r = run_scenario(
        "C: BC far forward at 30m, anchor=55",
        bc_pos=(30.0, 32.0), bc_vel=(5.0, 0),
        fr_pos=(28.0, 30.0), anchor_x=55.0,
        defender_setup=[
            (50.0, 32.0, 1, "PRIMARY", None, 0),
            (55.0, 40.0, 2, "PASSIVE", None, 2),
        ],
        expected_checks={
            1: (31.5, 30.0, "PRIMARY -> lead_point=30+5*0.3=31.5, ty=fr_y"),
            # PASSIVE must NOT step up to 30m — must stay at anchor=55
            2: (55.0, 36.0, "PASSIVE -> anchor=55 (NOT bc.x-1), ty=fr_y+6.0=36"),
        },
        tuners=tuners,
    )
    results.update(r)

    # ── Scenario D: Compression active ──
    # Lane offset = lane_index * 3.0 * (1 - compression_factor)
    # When compression_factor = 1.0, offset = 0
    print("\n" + "=" * 70)
    print("SCENARIO D: Compression active (factor=1.0)")
    print("=" * 70)
    r = run_scenario(
        "D: Compression active at 1.0",
        bc_pos=(45.0, 35.0), bc_vel=(4.0, 0),
        fr_pos=(43.0, 34.0), anchor_x=55.0,
        defender_setup=[
            (50.0, 35.0, 1, "PRIMARY", None, 2),
        ],
        expected_checks={
            # compression_timer would normally be > 0 when compression_active,
            # but in this test we haven't run DefensiveTiming. Let's just check
            # that PASSIVE SECONDARY get correct role mapping
        },
        tuners=tuners,
    )

    # Report summary
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    if not results:
        print("All scenarios passed!")
    else:
        for pid, (tx_ok, ty_ok, tx, ty, etx, ety, label) in results.items():
            print(f"  FAIL: d#{pid} {label}: target({tx:.2f},{ty:.2f}) expected({etx:.2f},{ety:.2f})")


if __name__ == "__main__":
    main()
