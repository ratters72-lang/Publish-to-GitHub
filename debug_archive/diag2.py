"""
Diagnose TACKLE_CONTACT -> TACKLE_COMPLETE transition.
Forces immediate engage, then traces every frame.
"""
import sys, math, random
sys.path.insert(0, 'c:/Users/User/Desktop/rlm')

from world.world_state import WorldState, GameWorldState, GameSubState
from world.open_play_engine import OpenPlayEngine
from player.player import Player, PlayerState, DefenderRole


def draw_collision_debug(players, screen):
    import pygame

    for p in players:
        body = p.body
        px, py = body.x, body.y

        if isinstance(px, (int, float)) and isinstance(py, (int, float)):
            if not math.isnan(px) and not math.isnan(py):
                pygame.draw.circle(screen, (255, 255, 255), (int(px), int(py)), body.radius, 1)

    for i in range(len(players)):
        for j in range(i + 1, len(players)):
            a = players[i].body
            b = players[j].body

            if a.is_overlapping(b):
                pygame.draw.circle(screen, (255, 0, 0), (int(a.x), int(a.y)), a.radius, 2)
                pygame.draw.circle(screen, (255, 0, 0), (int(b.x), int(b.y)), b.radius, 2)

                px, py = a.penetration_vector(b)
                if px != 0 or py != 0:
                    ax, ay = int(a.x), int(a.y)
                    pygame.draw.line(
                        screen,
                        (255, 255, 0),
                        (ax, ay),
                        (ax + int(px * 2), ay + int(py * 2)),
                        2,
                    )


def draw_debug_arrows(player, field, screen):
    import pygame
    scale = field.scale
    px, py = field.metres_to_px(player.x, player.y)

    ix = player.input_smooth_x
    iy = player.input_smooth_y
    imag = math.hypot(ix, iy)
    if imag > 0.01:
        arrow_len = min(imag * scale * 2, 80)
        ex = int(px + (ix / imag) * arrow_len)
        ey = int(py + (iy / imag) * arrow_len)
        pygame.draw.line(screen, (0, 255, 255), (px, py), (ex, ey), 2)

    vmag = math.hypot(player.vel_x, player.vel_y)
    if vmag > 0.01:
        arrow_len = min(vmag * scale * 2, 80)
        ex = int(px + (player.vel_x / vmag) * arrow_len)
        ey = int(py + (player.vel_y / vmag) * arrow_len)
        pygame.draw.line(screen, (0, 255, 0), (px, py), (ex, ey), 3)

    fx, fy = player.facing_vec
    arrow_len = 50
    ex = int(px + fx * arrow_len)
    ey = int(py + fy * arrow_len)
    pygame.draw.line(screen, (255, 0, 0), (px, py), (ex, ey), 2)


if __name__ == "__main__":
    world = WorldState()
    world.game_state = GameWorldState.OPEN_PLAY
    world.sub_state = GameSubState.OPEN_PLAY
    world.attacking_direction = 1
    world.defending_left = True

    bc = Player(30, 35, player_id=101)
    bc.state = PlayerState.BALL_CARRIER
    bc.max_speed_mps = 0.1
    bc.vx = 0.1
    bc.vy = 0.0
    bc.mass = 90
    bc.strength = 0.5
    bc.aggression = 0.5
    bc.tackle_skill = 0.3
    bc.offload_skill = 0.5
    bc.target_x = 130
    bc.target_y = 35

    defender = Player(28.5, 35, player_id=201)
    defender.state = PlayerState.TACKLING
    defender.max_speed_mps = 0.1
    defender.vx = 0.1
    defender.vy = 0.0
    defender.mass = 90
    defender.strength = 0.5
    defender.aggression = 0.5
    defender.tackle_skill = 0.5
    defender.offload_skill = 0.1
    defender.target_x = bc.x
    defender.target_y = bc.y
    defender.defender_role = DefenderRole.LINE

    world.ball_carrier = bc
    world.attacking_team = [bc, Player(30, 40, player_id=102)]
    world.defending_team = [defender, Player(30, 45, player_id=202), Player(30, 50, player_id=203)]
    world.players = world.attacking_team + world.defending_team
    world.set_ruck(bc.x, bc.y, "INIT")

    engine = OpenPlayEngine(world)
    engine.ball_carrier = bc
    world.freeze_bc("TEST_START")

    dt = 1.0 / 60.0

    print("=" * 80)
    print("TRACING TACKLE TRANSITIONS")
    print("=" * 80)

    for frame in range(900):
        engine.update(dt)

        pipe = engine.tackle_pipeline
        tes = pipe.tes

        if frame < 5 or frame % 10 == 0 or world.sub_state != GameSubState.TACKLE_CONTACT:
            print(
                f"[Frame {frame:4d}] sub={world.sub_state.value:20s} pipe={pipe.phase:12s} tes={tes.phase.value:22s} tes_timer={tes._timer:.4f}"
            )

        if world.sub_state == GameSubState.RUCK_FORMING:
            print(f"\n=== REACHED RUCK_FORMING at frame {frame}! ===")
            for frame2 in range(frame + 1, 900):
                engine.update(dt)
                pipe = engine.tackle_pipeline
                tes = pipe.tes
                if frame2 % 20 == 0 or world.sub_state in (GameSubState.PTB, GameSubState.POST_RUCK_OPEN_PLAY):
                    print(f"[Frame {frame2:4d}] sub={world.sub_state.value:20s} pipe={pipe.phase:12s} tes={tes.phase.value:22s}")
                if world.sub_state == GameSubState.OPEN_PLAY:
                    print(f"\n=== REACHED OPEN_PLAY at frame {frame2}! FULL CYCLE COMPLETE ===")
                    break
            break
    else:
        print(f"\nFAILED: Stuck at {world.sub_state} after 900 frames")
        print(f"  Pipeline phase: {engine.tackle_pipeline.phase}")
        print(f"  TES phase: {engine.tackle_pipeline.tes.phase}")
        tes = engine.tackle_pipeline.tes
        print(f"  TES timer: {tes._timer:.4f}")
        outcome = tes._outcome
        if outcome:
            print(f"  outcome.tackle_duration_s={outcome.tackle_duration_s:.4f}")
            print(f"  outcome.tackle_result={outcome.tackle_result}")
