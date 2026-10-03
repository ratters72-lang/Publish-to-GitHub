"""
Full chain diagnostic - trace EVERY substate transition with timestamps.
"""
import sys, math, random
sys.path.insert(0, 'c:/Users/User/Desktop/rlm')

from world.world_state import WorldState, GameWorldState, GameSubState
from world.open_play_engine import OpenPlayEngine, OpenPlayConfig
from player.player import Player, PlayerState, DefenderRole

world = WorldState()
world.game_state = GameWorldState.OPEN_PLAY
world.sub_state = GameSubState.OPEN_PLAY
world.attacking_direction = 1
world.defending_left = True

# Players
bc = Player(30, 35, player_id=101)
bc.state = PlayerState.BALL_CARRIER
bc.max_speed_mps = 0.5
bc.mass = 90
bc.target_x = 130; bc.target_y = 35

defender = Player(28.5, 35, player_id=201)
defender.state = PlayerState.TACKLING
defender.mass = 90
defender.defender_role = DefenderRole.LINE

# Extra defenders (for retreat testing)
extra = []
for i in range(3):
    d = Player(30, 10 + i*20, player_id=210+i)
    d.defender_role = DefenderRole.LINE
    extra.append(d)

world.ball_carrier = bc
world.attacking_team = [bc, Player(30, 40, player_id=102)]
world.defending_team = [defender] + extra
world.players = world.attacking_team + world.defending_team
world.set_ruck(bc.x, bc.y, "INIT")
world.ruck_cycle.reset()

engine = OpenPlayEngine(world)
engine.ball_carrier = bc
dt = 1.0/60.0

# Use shorter ruck settle + no PTB timeout
config = OpenPlayConfig()
config.ruck_settle_s = 0.1
config.ptb_timeout_s = 0.5
engine.config = config

# Track states and when they change
last_sub = None
last_pipe = None
last_tes = None

for frame in range(600):
    engine.update(dt)
    sub = world.sub_state
    pipe_phase = engine.tackle_pipeline.phase
    tes_phase = engine.tackle_pipeline.tes.phase.value if engine.tackle_pipeline.tes else 'N/A'
    
    if sub != last_sub or pipe_phase != last_pipe or tes_phase != last_tes or frame < 5:
        print(f"[Frame {frame:4d}] sub={sub.value:25s} pipe={pipe_phase:12s} tes={tes_phase:22s} ruck_timer={world.ruck_cycle.elapsed:.3f}")
        last_sub = sub
        last_pipe = pipe_phase
        last_tes = tes_phase
    
    if sub == GameSubState.OPEN_PLAY and frame > 0:
        print(f"\n=== FULL CYCLE COMPLETE at frame {frame}! ===")
        break
else:
    print(f"\n=== STUCK at {world.sub_state} after 600 frames ===")
    print(f"  ruck_cycle.elapsed={world.ruck_cycle.elapsed:.3f}")
    print(f"  ruck_cycle.phase={world.ruck_cycle.current_phase}")
    print(f"  _ruck_settle_timer={engine._ruck_settle_timer:.3f}")
    print(f"  _ptb_timer={engine._ptb_timer:.3f}")
    print(f"  _ptb_started={engine._ptb_started}")
    print(f"  DH controller:")
    print(f"    dh={engine.dh_controller.dh}")
    print(f"    at_ruck={engine.dh_controller.at_ruck}")
    print(f"    pickup_complete={engine.dh_controller.pickup_complete}")
    print(f"    ball_playable={engine.dh_controller.ball_playable}")
    print(f"    pickup_timer={engine.dh_controller.pickup_timer:.3f}")
    print(f"    pickup_duration={engine.dh_controller.pickup_duration:.3f}")
    print(f"  _post_ruck_timer={engine._post_ruck_timer:.3f}")
