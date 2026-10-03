"""Quick headless sim test v2 - proper stdout capture."""
import sys
import os
sys.path.insert(0, '.')

# Force headless
os.environ['SDL_VIDEODRIVER'] = 'dummy'
os.environ['PGGAME'] = '0'

# Redirect stdout to capture
from io import StringIO

from world.world_state import WorldState, GameWorldState, GameSubState, GamePhase
from game.defence.defence_lvl_1.defensive_behaviour_unified import DefensiveBehaviourUnified
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from player.player import Player, DefenderRole
from player.movement_states import MovementState

tuners = DefenceTuners()
beh = DefensiveBehaviourUnified(tuners)

# Create minimal world
w = WorldState()
w.ball_carrier = None
w.defensive_line_anchor = 60.0
w.ruck_x_m = 50.0
w.ruck_y_m = 35.0
w.phase = GamePhase.OPEN_PLAY
w.sub_state = GameSubState.OPEN_PLAY
w.defence_enabled = True
w.in_pursuit_mode = False
w.first_tackle_not_made = False
w.current_target_writer = ""
w.use_structured_open_play = True
w.dt = 1.0/60.0
w.defending_team = []
w.attacking_team = []
w.in_kickoff = False
w.role_governs = False
w.just_caught_this_frame = False
w.game_state = GameWorldState.OPEN_PLAY

# Create 7 defenders
defenders = []
for i in range(7):
    p = Player(x=55.0 + i * 1.0, y=10.0 + i * 8.0, player_id=100+i)
    p.max_speed_mps = 8.0
    p.move_speed = 0.0
    p.frozen = False
    p.freeze_timer = 0.0
    p.timing_locked = False
    p.depth_class = "PASSIVE"
    p.lane_index = 0
    p.is_tackler = False
    p.in_contact = False
    p.tes_locked = False
    p.defender_role = None
    p.target_source = ""
    p.reaction_timer = 0.0
    p.retreat_delay = 0.0
    p.lateral_hesitation = 0.0
    p.step_stagger = 0.0
    p.step_stagger_active = False
    p.compression_active = False
    p.compression_timer = 0.0
    p.dh_defender_delay = 0.0
    p.dh_defender_ptb_active = False
    p.movement_state = None
    defenders.append(p)

w.defending_team = defenders

# Create BC
bc = Player(x=55.0, y=35.0, player_id=200)
bc.vx = 2.0
bc.vy = 0.0
w.ball_carrier = bc

# Test 1: structured open play with non-compliant PASSIVE defenders
print("=" * 60)
print("TEST 1: Structured OPEN_PLAY (use_structured_open_play=True)")
print("=" * 60)
beh._apply_structured_open_play(w, defenders)

retreat_count = 0
for d in defenders:
    dc = getattr(d, "depth_class", "")
    ms = d.movement_state
    src = getattr(d, "target_source", "")
    if ms == MovementState.RETREAT and src == "retreat_non_compliant":
        retreat_count += 1
        print(f"  PASSIVE #{d.player_id}: movement_state=RETREAT, target_source={src} (CORRECT)")
    elif dc == "PASSIVE":
        print(f"  PASSIVE #{d.player_id}: movement_state={ms}, target_source={src}")
    elif dc in ("PRIMARY", "SECONDARY"):
        print(f"  {dc} #{d.player_id}: movement_state={ms}, target_source={src}")

print(f"  -> PASSIVE defenders with RETREAT: {retreat_count}")
assert retreat_count > 0, "No PASSIVE defenders got RETREAT in structured mode!"

# Test 2: open_play_unified path (used by PTB phases)
print("\n" + "=" * 60)
print("TEST 2: POST_PTB_RESET (use_structured_open_play=False)")
print("=" * 60)
w.sub_state = GameSubState.POST_PTB_RESET
for d in defenders:
    d.x = 52.0 + defenders.index(d) * 1.0  # not yet at compliance
w.defensive_line_anchor = 65.0
w.ruck_x_m = 50.0
w.use_structured_open_play = False
beh._apply_open_play_unified(w, defenders)

retreat_count2 = 0
for d in defenders:
    ts = d.target_source
    ms = getattr(d, "movement_state", None)
    dc = getattr(d, "depth_class", "")
    if ts == "retreat_non_compliant":
        if ms == MovementState.RETREAT:
            retreat_count2 += 1
            print(f"  PASSIVE #{d.player_id}: target_source={ts}, movement_state=RETREAT (CORRECT)")
        else:
            print(f"  PASSIVE #{d.player_id}: target_source={ts}, movement_state={ms} (SHOULD BE RETREAT)")
    elif dc == "PASSIVE":
        print(f"  PASSIVE #{d.player_id}: target_source={ts}, movement_state={ms}")

print(f"  -> PASSIVE defenders with RETREAT: {retreat_count2}")
assert retreat_count2 > 0, "No PASSIVE defenders got RETREAT in unified mode!"

print("\n" + "=" * 60)
print("ALL TESTS PASSED!")
print("=" * 60)
