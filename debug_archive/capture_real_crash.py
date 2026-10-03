"""
Mimics the real game path through OpenPlayEngine.
Sets up players close together with CORRECT velocities so alignment passes.
"""
import sys, os, math, random, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ['SDL_VIDEODRIVER'] = 'dummy'
import pygame
pygame.display.set_mode((1, 1))

from world.field import Field
from world.world_state import WorldState, GameWorldState, GameSubState
from world.open_play_engine import OpenPlayEngine
from player.player import Player, PlayerState, Team as PlayerTeam
from ball.ball import Ball

field = Field(1920, 1080)
world = WorldState()
ball = Ball(field)
world.ball = ball

# Two players at position ~50, with both velocities pointing forward (same direction)
world.attacking_direction = 1
world.defending_left = False

att = Player(50, 35, player_id=100)
att.team = PlayerTeam.RECEIVING
att.facing = 0
att.state = PlayerState.IDLE
att.frozen = False
att.vx = 2.0   # moving forward
att.vy = 0.0
world.receiving_team.append(att)

deff = Player(50.6, 35, player_id=200)  # 0.6m apart — within CONTACT_RADIUS
deff.team = PlayerTeam.KICKING
deff.facing = 0
deff.state = PlayerState.IDLE
deff.frozen = False
deff.vx = 2.5   # also moving forward (alignment > 0.65)
deff.vy = 0.0
world.kicking_team.append(deff)

world.attacking_team = world.receiving_team
world.defending_team = world.kicking_team
world.players = world.receiving_team + world.kicking_team
world.ball_carrier = att
world.ruck_x_m = 50
world.ruck_y_m = 35

open_play = OpenPlayEngine(world)
world.open_play = open_play
open_play.start_open_play(att, world.receiving_team, world.kicking_team)

open_play._engage_cooldown = 0.0
open_play.defence_ctx.primary = deff
open_play.defence_ctx.just_caught_this_frame = False

dt = 1.0 / 60.0
print("Starting...")
att_spd = math.hypot(att.vx, att.vy)
deff_spd = math.hypot(deff.vx, deff.vy)
alignment = (att.vx * deff.vx + att.vy * deff.vy) / (att_spd * deff_spd) if att_spd > 0.001 and deff_spd > 0.001 else 1.0
print(f"distance={math.hypot(att.x - deff.x, att.y - deff.y):.3f} alignment={alignment:.3f}")
print()

for frame in range(500):
    try:
        open_play.update(dt)
    except Exception as e:
        print(f"\n!!! CRASH at frame {frame} !!!")
        print(f"Exception: {type(e).__name__}: {e}")
        traceback.print_exc()
        sys.exit(1)
    
    sub = world.sub_state
    if sub == GameSubState.TACKLE_CONTACT:
        fsm_state = open_play.tackle_pipeline.tes._fsm.state
        phase = open_play.tackle_pipeline.tes.phase
        if frame % 5 == 0 or fsm_state in ('contact', 'drive', 'dominance_settling', 'tackle_complete', 'ground'):
            dist = math.hypot(att.x - deff.x, att.y - deff.y)
            print(f"  [{frame}] sub={sub} fsm={fsm_state} phase={phase} dist={dist:.3f}")
        if fsm_state == 'tackle_complete':
            print(f"  [{frame}] TACKLE COMPLETE!")
            break
    elif sub != GameSubState.OPEN_PLAY:
        print(f"  [{frame}] sub={sub}")
        break

print(f"\nFinal: sub={world.sub_state} fsm={open_play.tackle_pipeline.tes._fsm.state}")
outcome = open_play.tackle_pipeline.tes.outcome
print(f"Outcome: {outcome}")
