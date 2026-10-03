"""Focused probe: run simulation for ~5 seconds and capture FSM behaviour."""

import os, sys, math, random
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import pygame
pygame.display.set_mode((1,1), pygame.NOFRAME)

from world.field import Field
from world.world_state import WorldState, GameWorldState
from world.open_play_engine import OpenPlayEngine
from player.player import Player, PlayerState, Team
from kick_off.controller import KickoffController
from kick_off.kickoff_formations import (
    apply_receiving_kickoff_formation,
    apply_kicking_team_formation,
)
from ball.ball import Ball


def main():
    field = Field(1920, 1080)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball

    ko = KickoffController()
    open_play = OpenPlayEngine(world)

    # ── Create teams ──
    kickers = []
    receivers = []

    for i in range(13):
        y = 5 + i * (field.width_m - 10) / 12
        p = Player(field.halfway_m, y, player_id=200 + i)
        p.team = Team.KICKING
        p.facing = math.pi
        p.state = PlayerState.IDLE
        p.defensive_anchor_x = field.halfway_m
        kickers.append(p)

    for i in range(13):
        y = 5 + i * (field.width_m - 10) / 12
        p = Player(field.halfway_m, y, player_id=100 + i)
        p.team = Team.RECEIVING
        p.facing = 0
        p.state = PlayerState.IDLE
        receivers.append(p)

    world.kicking_team = kickers
    world.receiving_team = receivers
    world.attacking_team = receivers
    world.defending_team = kickers

    apply_kicking_team_formation(world, field)
    apply_receiving_kickoff_formation(world, field)

    # ── Kickoff ──
    ball.x_m = field.halfway_m
    ball.y_m = field.centre_m
    ball.z_m = 1.0

    # Pick a landing point ON the field (x=15-30, inside tryline at x=10).
    kick_distance = random.uniform(30.0, 45.0)
    target_x = ball.x_m + (-1) * kick_distance
    # Keep y well inside the sidelines (0-70m).
    centre = field.centre_m
    target_y = random.uniform(10.0, field.width_m - 10.0)

    dx = target_x - ball.x_m
    dy = target_y - ball.y_m
    distance = math.hypot(dx, dy)
    direction_deg = math.degrees(math.atan2(dy, dx))

    g = 9.8
    T = 2.8
    horizontal_speed = distance / T
    vertical_speed = 0.5 * g * T
    power = math.hypot(horizontal_speed, vertical_speed)
    angle_rad = math.atan2(vertical_speed, horizontal_speed)
    angle_deg = math.degrees(angle_rad)
    power = min(power, 28.0)

    ball.kick(power, angle_deg, direction_deg)

    world.kickoff_landing_x = ball.landing_x_m
    world.kickoff_landing_y = ball.landing_y_m

    ko.start_kickoff()
    ko._kicking_team_players = world.kicking_team
    ko._receiving_team_players = world.receiving_team
    ko.on_ball_kicked(world.kicking_team, world.receiving_team)

    primary = min(
        world.receiving_team,
        key=lambda p: (p.x - world.kickoff_landing_x)**2 + (p.y - world.kickoff_landing_y)**2
    )
    world.primary_catcher = primary
    primary.set_state(PlayerState.CHASE_BALL)
    for p in world.receiving_team:
        if p is primary:
            continue
        p.set_state(PlayerState.SUPPORT_RUN, support_leader=primary)
    for p in world.kicking_team:
        p.set_state(PlayerState.CHASE_BALL)

    # ── Simulate frames ──
    dt = 1.0 / 60.0
    open_play_was_initialized = False
    tackle1_frame = -1
    max_frames = 600

    for frame in range(1, max_frames + 1):
        ball.update(dt)

        if world.game_state == GameWorldState.KICKOFF:
            for p in world.receiving_team:
                p.apply_state(ball, dt, world)
            for p in world.kicking_team:
                p.apply_state(ball, dt, world)
            ko.update(ball.x_m, ball.y_m, ball.z_m, ball.grounded)

        elif world.game_state == GameWorldState.OPEN_PLAY:
            for p in world.receiving_team:
                p.apply_state(ball, dt, world)
            for p in world.kicking_team:
                p.apply_state(ball, dt, world)
            open_play.update(dt)

        # Auto-init open play after catch
        if world.game_state == GameWorldState.OPEN_PLAY and world.ball_carrier:
            need_init = open_play.band is None or open_play.ball_carrier is not world.ball_carrier
            if need_init:
                if not open_play_was_initialized:
                    print("=== OPEN PLAY INIT ===")
                    open_play_was_initialized = True
                open_play.start_open_play(
                    world.ball_carrier,
                    world.receiving_team,
                    world.kicking_team,
                )

        # Track when first tackle completes
        if world.tackle_count >= 1 and tackle1_frame < 0:
            tackle1_frame = frame
            print(f"=== TACKLE 1 COMPLETED at frame {frame} ===")

        # Log summary every 30 frames after tackle 1
        if tackle1_frame > 0 and frame % 30 == 0:
            bc = world.ball_carrier
            bc_x = bc.x if bc else 0
            bc_vx = bc.vx if bc else 0
            print(f"[FRAME {frame}] tackle_count={world.tackle_count} "
                  f"ruck_state={world.ruck_state.name} "
                  f"dc_state={open_play.defence_ctx.state.name} "
                  f"bc=({'#'+str(bc.player_id) if bc else 'None'}) "
                  f"bc_x={bc_x:.1f} bc_vx={bc_vx:.2f} "
                  f"frozen={bc.frozen if bc else 'N/A'}")

        if tackle1_frame > 0 and (frame - tackle1_frame) > 300:
            print(f"\n=== DONE at frame {frame} (300 frames after tackle 1) ===")
            break

    print(f"\nFinal: tackle_count={world.tackle_count} "
          f"ruck_state={world.ruck_state.name} "
          f"dc_state={open_play.defence_ctx.state.name}")


if __name__ == "__main__":
    main()
