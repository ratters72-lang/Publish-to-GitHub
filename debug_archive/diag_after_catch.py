"""
Run one kickoff cycle headless, capturing AFTER_CATCH diagnostic output.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(message)s")

# Also print to ensure we see it
import builtins
original_print = builtins.print

def dual_print(*args, **kwargs):
    original_print(*args, **kwargs)

builtins.print = dual_print

import math
import random
from world.field import Field
from world.world_state import WorldState, GameWorldState, GameSubState
from world.ruck_state import RuckState
from world.open_play_engine import OpenPlayEngine

from player.player import Player, PlayerState, DefenderRole, Team as PlayerTeam

from kick_off.controller import KickoffController
from kick_off.kickoff_formations import (
    apply_receiving_kickoff_formation,
    apply_kicking_team_formation,
)

from kickoff_kickchase import KickoffKickChaseController

from ball.ball import Ball

# ------------------------------------------------------------
# SETUP (no pygame)
# ------------------------------------------------------------

field = Field(1920, 1080)
world = WorldState()
ball = Ball(field)
world.ball = ball

ko = KickoffController()
open_play = OpenPlayEngine(world)

world.open_play = open_play
world.kickoff_controller = ko
world.kickoff_kickchase = KickoffKickChaseController()

# Create teams
kickers = []
receivers = []

world.attacking_direction = 1
world.defending_left = False

for i in range(13):
    y = 5 + i * (field.width_m - 10) / 12
    p = Player(field.halfway_m, y, player_id=200 + i)
    p.team = PlayerTeam.KICKING
    p.facing = math.pi
    p.state = PlayerState.IDLE
    kickers.append(p)

for i in range(13):
    y = 5 + i * (field.width_m - 10) / 12
    p = Player(field.halfway_m, y, player_id=100 + i)
    p.team = PlayerTeam.RECEIVING
    p.facing = 0
    p.state = PlayerState.IDLE
    receivers.append(p)

world.kicking_team = kickers
world.receiving_team = receivers
world.attacking_team = receivers
world.defending_team = kickers
world.players = kickers + receivers

# Reset ball
ball.x_m = field.halfway_m
ball.y_m = field.centre_m
ball.z_m = 1.0
ball.vx = ball.vy = ball.vz = 0.0
ball.grounded = False
ball.kicked = False
ball.possessed_by = None

world.game_state = GameWorldState.KICKOFF
world.sub_state = GameSubState.KICKOFF_IN_AIR
world.ball_live = False
world.ball_in_play = False
world.ball_carrier = None
world.frame_count = 0
world.just_left_kickoff_timer = 0.0

# Reset players
for p in world.players:
    p.reset_for_kickoff(world=world)

apply_receiving_kickoff_formation(world, field)
apply_kicking_team_formation(world, field)

for p in world.receiving_team:
    p.team = PlayerTeam.RECEIVING
for p in world.kicking_team:
    p.team = PlayerTeam.KICKING

# ------------------------------------------------------------
# SIMULATE KICKOFF
# ------------------------------------------------------------

forward_dir = -1
kick_distance = random.uniform(50.0, 60.0)
target_x = ball.x_m + forward_dir * kick_distance
target_y = random.uniform(10.0, field.width_m - 10.0)

sx = ball.x_m
sy = ball.y_m
dx = target_x - sx
dy = target_y - sy
distance = math.hypot(dx, dy)
direction_deg = math.degrees(math.atan2(dy, dx))

g = 9.8
T = 2.8
horizontal_speed = distance / T
vertical_speed = 0.5 * g * T
power = math.hypot(horizontal_speed, vertical_speed)
angle_rad = math.atan2(vertical_speed, horizontal_speed)
angle_deg = math.degrees(angle_rad)

angle_deg += random.uniform(-2.0, 2.0)
power *= random.uniform(0.95, 1.05)
power = min(power, 28.0)

ball.kick(power, angle_deg, direction_deg)
world.kickoff_landing_x = ball.landing_x_m
world.kickoff_landing_y = ball.landing_y_m

# Find nearest receiver
landing_x = world.kickoff_landing_x
landing_y = world.kickoff_landing_y
best_p = None
best_d2 = 999999.0
for p in world.receiving_team:
    ddx = p.x - landing_x
    ddy = p.y - landing_y
    d2 = ddx*ddx + ddy*ddy
    if d2 < best_d2:
        best_d2 = d2
        best_p = p
primary = best_p
primary.catch_x = landing_x
primary.catch_y = landing_y
primary.catch_time = None

world.primary_catcher = primary
primary.set_state(PlayerState.CHASE_BALL)
for p in world.receiving_team:
    if p is primary:
        continue
    p.set_state(PlayerState.SUPPORT_RUN, support_leader=primary)

kicker = min(world.kicking_team, key=lambda p: p.distance_to_point(ball.x_m, ball.y_m))
kicker.set_state(PlayerState.KICKER)
kicker.defender_role = DefenderRole.KICK_CHASE
kicker.state = PlayerState.CHASE_BALL
kicker.frozen = False
for p in world.kicking_team:
    if p is kicker:
        continue
    p.set_state(PlayerState.CHASE_BALL)

ko.start_kickoff()
ko._kicking_team_players = world.kicking_team
ko._receiving_team_players = world.receiving_team
ko.on_ball_kicked(world.kicking_team, world.receiving_team)

if world.kickoff_kickchase:
    world.kickoff_kickchase.start(world)

# ------------------------------------------------------------
# FRAME LOOP
# ------------------------------------------------------------

dt = 1.0 / 60.0
MAX_FRAMES = 600
caught = False

for frame in range(MAX_FRAMES):
    world.frame_count = frame
    ball.update(dt)

    # KICKOFF phase
    if world.game_state == GameWorldState.KICKOFF:
        touched_by = None
        for p in world.players:
            if p.try_catch(ball, world):
                touched_by = "RCVR" if p.team.name == "RECEIVING" else "KICKER"
                caught = True
                break

        for p in world.receiving_team:
            p.apply_state(ball, dt, world)
        for p in world.kicking_team:
            p.apply_state(ball, dt, world)

        if world.kickoff_kickchase and world.kickoff_kickchase.active:
            world.kickoff_kickchase.update(world, dt)

        ko.update(ball.x_m, ball.y_m, ball.z_m, ball.grounded, touched_by=touched_by)

    elif world.game_state == GameWorldState.OPEN_PLAY:
        open_play.update(dt)
        if caught and frame > 10:
            break

print(f"\n=== SIMULATION DONE at frame {frame} ===")
if caught:
    print("Catch occurred. Check AFTER_CATCH log above for defender state.")
else:
    print("No catch occurred.")
