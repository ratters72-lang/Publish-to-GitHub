"""
Diagnose why TACKLE_CONTACT never transitions to TACKLE_COMPLETE.
This script creates a minimal setup and traces every call.
"""
import sys, math, random

# Add project root
sys.path.insert(0, 'c:/Users/User/Desktop/rlm')

from world.world_state import WorldState, GameWorldState, GameSubState
from world.ruck_state import RuckCyclePhase, RuckCycleState
from world.open_play_engine import OpenPlayEngine
from player.player import Player, PlayerState, DefenderRole
from game.tackle.tackle_pipeline import TacklePipeline
from game.tes.tes_state import TESPhase, TackleResult

# Setup minimal world
world = WorldState()
world.game_state = GameWorldState.OPEN_PLAY
world.sub_state = GameSubState.OPEN_PLAY
world.attacking_direction = 1
world.defending_left = True

# Create BC and defender
bc = Player(30, 35, player_id=101)
bc.state = PlayerState.BALL_CARRIER
bc.vx = 5.0
bc.vy = 0.0
bc.mass = 90

defender = Player(27, 35, player_id=201)  # 3m from BC
defender.state = PlayerState.TACKLING
defender.vx = 3.0
defender.vy = 0.0
defender.mass = 90

# Add other defenders
extra_defs = []
for i in range(5):
    d = Player(30, 5 + i*14, player_id=210+i)
    d.state = PlayerState.IDLE
    d.defender_role = DefenderRole.LINE
    extra_defs.append(d)

world.ball_carrier = bc
world.attacking_team = [bc] + [Player(30, 40, player_id=102)]
world.defending_team = [defender] + extra_defs
world.players = world.attacking_team + world.defending_team
world.set_ruck(bc.x, bc.y, "DIAG_INIT")
world.ruck_cycle.reset()

# Create engine
engine = OpenPlayEngine(world)
engine.ball_carrier = bc
engine.defence_ctx.primary = defender

# Give BC a ball target
bc.target_x = bc.x + 100
bc.target_y = bc.y

dt = 1.0 / 60.0
frame_count = 0

print("=" * 80)
print("DIAGNOSIS: Sub-state transitions")
print("=" * 80)

while engine.world.sub_state == GameSubState.OPEN_PLAY and frame_count < 300:
    engine.update(dt)
    frame_count += 1
    if frame_count == 1:
        print(f"\nAfter frame {frame_count}: game={world.game_state} sub={world.sub_state}")
        print(f"  Pipeline phase={engine.tackle_pipeline.phase}")
        print(f"  TES phase={engine.tackle_pipeline.tes.phase}")

# Now trace TACKLE_CONTACT frame by frame
max_frames = 600
tes_phase_at_start = None
while frame_count < max_frames:
    engine.update(dt)
    frame_count += 1
    
    pipeline = engine.tackle_pipeline
    tes = pipeline.tes
    phases = {
        'pipe': pipeline.phase,
        'tes': tes.phase if tes else 'N/A',
        'sub': world.sub_state.value,
    }
    
    # Every frame show details
    if pipeline._outcome:
        outcome = pipeline._outcome
        print(f"\n[Frame {frame_count}] pipe={pipeline.phase} tes={tes.phase} sub={world.sub_state.value}")
        print(f"  outcome: result={outcome.tackle_result} dur={outcome.tackle_duration_s:.3f} ptb={outcome.ptb_time_s:.1f}")
    
    if world.sub_state == GameSubState.OPEN_PLAY:
        print(f"\n=== REACHED OPEN_PLAY at frame {frame_count}! ===")
        break

if world.sub_state not in (GameSubState.OPEN_PLAY,):
    print(f"\nFAILED: Stuck at {world.sub_state} after {frame_count} frames")
    print(f"  Pipeline phase: {engine.tackle_pipeline.phase}")
    print(f"  TES phase: {engine.tackle_pipeline.tes.phase}")
    tes_obj = engine.tackle_pipeline.tes
    print(f"  TES timer: {tes_obj._timer:.4f}")
    print(f"  TES outcome: {tes_obj._outcome}")
    if tes_obj._outcome:
        o = tes_obj._outcome
        print(f"    tackle_duration_s={o.tackle_duration_s}, ptb_time_s={o.ptb_time_s}")
