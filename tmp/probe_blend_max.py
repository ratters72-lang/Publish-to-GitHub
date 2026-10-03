"""Probes the actual post-hold defender speeds in the verify_speed_blend
scenario.  Answers: does the defence pipeline COMMAND speeds above the
kickoff-chase ceiling (7.38) during the reaction-governor blend window,
or does the governor line-speed keep defenders below it?

This distinguishes a governor regression (speeds capped by the reaction
clock forever) from a stale test expectation (the engine intentionally
keeps line speed ~4-5 m/s during the post-catch window, so no sample
ever crosses ceiling + 0.05).
"""
import os
import sys
import math

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
sys.path.insert(0, os.getcwd())

import pygame
if not pygame.display.get_init():
    pygame.display.set_mode((64, 64))

from world.field import Field
from world.world_state import WorldState, GamePhase
from world.open_play_engine import OpenPlayEngine
from game.kickoff_engine import KickoffEngine
from ball.ball import Ball
from world.catch_system import check_for_catch
from player.player import Player

DT = 1.0 / 60.0
BLEND = Player.DEFENCE_REACTION_BLEND_S  # 0.6


def main():
    field = Field(1920, 1080)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball
    ope = OpenPlayEngine(world)
    world.open_play = ope
    world.open_play_engine = ope
    ko = KickoffEngine(world, ball, field)
    world.kickoff_controller = ko
    ko.reset_for_kickoff()
    ko.kickoff()

    catch_frame = None
    for frame in range(900):
        ball.update(DT)
        if world.phase == GamePhase.KICKOFF:
            ko.update(DT)
            check_for_catch(world, ball, DT)
            if world.phase == GamePhase.CATCH:
                catch_frame = frame
                break

    if catch_frame is None:
        print("no catch")
        sys.exit(1)

    ope = world.open_play
    ope.start(world.ball_carrier)
    world.phase = GamePhase.OPEN_PLAY

    ceilings = {d.player_id: d.kickoff_chase_speed for d in world.defending_team}
    # max actual speed per phase bucket
    max_hold = 0.0   # reaction remaining >= BLEND (pure hold)
    max_blend = 0.0  # reaction remaining < BLEND and > 0
    max_after = 0.0  # reaction exhausted
    n_blend_samples = 0

    sim_t = 0.0
    while sim_t <= 3.0 and world.phase == GamePhase.OPEN_PLAY:
        if ope._phase != "OPEN_PLAY":
            print(f"[stop] tackle began at t={sim_t:.2f}s (phase={ope._phase})")
            break
        ope.update(DT)
        for d in world.defending_team:
            spd = math.hypot(d.vx, d.vy)
            rem = d.defence_reaction_remaining_s
            if rem >= BLEND:
                max_hold = max(max_hold, spd)
            elif rem > 0.0:
                max_blend = max(max_blend, spd)
                n_blend_samples += 1
            else:
                max_after = max(max_after, spd)
        sim_t += DT

    ceiling = min(ceilings.values())
    print(f"kickoff_chase_speed ceiling = {ceiling:.3f}")
    print(f"max speed during PURE HOLD  = {max_hold:.3f}  (<= ceiling expected)")
    print(f"max speed during BLEND      = {max_blend:.3f}  ({n_blend_samples} blend frames sampled)")
    print(f"max speed AFTER reaction    = {max_after:.3f}")
    print("=> blend_crossed expectation (speed > ceiling + 0.05 = "
          f"{ceiling + 0.05:.3f}) is {'ACHIEVED' if max_blend > ceiling + 0.05 else 'NOT ACHIEVED'} "
          "by the actual commanded defence pipeline")


if __name__ == "__main__":
    main()
