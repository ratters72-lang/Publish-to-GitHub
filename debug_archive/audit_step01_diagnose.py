"""
Diagnose the integration failures by tracing each step.
"""
import sys, os, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from world.world_state import WorldState, GameSubState
from game.defence.defence_lvl_1.defensive_behaviour_unified import (
    DefensiveBehaviourUnified, _is_marker
)
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from player.player import Player, DefenderRole

tuners = DefenceTuners()
db = DefensiveBehaviourUnified(tuners)

# Create world + players like the integration test
world = WorldState()
world.sub_state = GameSubState.OPEN_PLAY
world.role_governs = False
world.defence_enabled = True
world.dt = 1.0/60.0

bc = Player(40.0, 35.0, 99)
bc.vx = 6.0
world.ball_carrier = bc
world.ball_owner = "BC"

fr = Player(38.0, 32.0, 98)
world.first_receiver = fr
world.defensive_line_anchor = 50.0
world.open_play_engine = object()

# Defenders with known setup
defenders = []
for x, y, pid, dc, dr, li in [
    (45.0, 35.0, 1, "PASSIVE", None, 0),
    (52.0, 41.0, 4, "PASSIVE", None, 2),
    (45.0, 35.0, 6, "PASSIVE", DefenderRole.MARKER_A, 0),  # manually set MARKER_A
]:
    d = Player(x, y, pid)
    d.depth_class = dc
    d.defender_role = dr
    d.lane_index = li
    d.frozen = False
    d.freeze_timer = 0.0
    d.timing_locked = False
    d.compression_active = False
    d.compression_timer = 0.0
    d.lateral_hesitation = 0.0
    d.retreat_delay = 0.0
    d.step_stagger = 0.0
    d.max_speed_mps = 9.2
    d.move_speed = 9.2
    defenders.append(d)

print("=== BEFORE calling apply() ===")
for d in defenders:
    print(f"  d#{d.player_id}: depth_class={d.depth_class} defender_role={d.defender_role} "
          f"frozen={d.frozen} timing_locked={getattr(d,'timing_locked',False)}")
    print(f"    _is_marker={_is_marker(d)}")

# Run through the steps in _apply_open_play_unified manually
print("\n=== Step 1: classify_defender_depth ===")
for d in defenders:
    dc = db.classify_defender_depth(world, d)
    d.depth_class = dc
    print(f"  d#{d.player_id}: new depth_class={dc}")
    # y distance to bc
    dy = abs(d.y - world.ball_carrier.y)
    print(f"    dy to bc = {dy:.2f} (threshold = {tuners.PRIMARY_LATERAL_THRESHOLD})")

print("\n=== Step 2: compute_lane_indices ===")
db._compute_lane_indices(defenders)
for d in sorted(defenders, key=lambda x: x.y):
    print(f"  d#{d.player_id} y={d.y:.1f} lane_index={d.lane_index}")

print("\n=== Step 3: Priority gates (simulated) ===")
for d in defenders:
    print(f"  d#{d.player_id}: defender_role={d.defender_role} freeze_timer={d.freeze_timer}")
    print(f"    _is_marker={_is_marker(d)}")
    if _is_marker(d) and getattr(d, "freeze_timer", 0.0) > 0:
        print(f"    -> Gate 1 (marker freeze)")
    elif getattr(d, "freeze_timer", 0.0) > 0:
        print(f"    -> Gate 2 (freeze_timer)")
    elif getattr(d, "timing_locked", False):
        print(f"    -> Gate 3 (timing_locked)")
    else:
        print(f"    -> None (proceeds to role spacing)")

print("\n=== Step 4: Role Spacing ===")
db.def_role_spacing.prepare(world)
for d in defenders:
    if d.frozen or getattr(d, "timing_locked", False):
        print(f"  d#{d.player_id}: SKIPPED (frozen/timing_locked)")
        continue
    tx, ty = db.def_role_spacing.compute_role_spacing(
        defender=d,
        ball_carrier=world.ball_carrier,
        role=d.role,
        lane_index=getattr(d, "lane_index", 0),
        compression_factor=db.def_timing.get_compression_factor(d),
        lateral_state=getattr(d, "lateral_hesitation", 0.0),
        retreat_state=getattr(d, "retreat_delay", 0.0),
    )
    print(f"  d#{d.player_id} depth_class={d.depth_class} role={d.defender_role}: "
          f"target=({tx:.2f},{ty:.2f})")
    # Show which branch of compute_role_spacing was taken
    depth_class = getattr(d, "depth_class", "PASSIVE")
    def_role = getattr(d, "defender_role", None)
    is_marker = def_role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B)
    
    if is_marker or def_role == DefenderRole.DUMMY_HALF:
        print(f"    Branch: MARKER/DH -> bc.x - 1.0")
    elif depth_class in ("PRIMARY", "SECONDARY"):
        bcvx = db.def_role_spacing._bcvx
        if bcvx > 0.5:
            lead = db.def_role_spacing._bcx + bcvx * 0.3
            print(f"    Branch: PRIMARY/SECONDARY with lead-point -> lead={lead:.2f}")
        else:
            print(f"    Branch: PRIMARY/SECONDARY without lead-point -> bc.x-1.0")
    else:
        print(f"    Branch: PASSIVE -> anchor={db.def_role_spacing._anchor_x}")
