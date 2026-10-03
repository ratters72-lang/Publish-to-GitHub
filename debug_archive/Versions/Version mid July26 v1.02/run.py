import sys
import math
import pygame

SAFE_RED = (200, 0, 0)
SAFE_GREEN = (0, 200, 0)

from world.field import Field
from world.world_state import WorldState, GameWorldState, GameSubState
from world.open_play_engine import OpenPlayEngine

from kick_off.kickoff_formations import (
    apply_receiving_kickoff_formation,
    apply_kicking_team_formation,
)
from game.kickoff_engine import KickoffEngine

from ball.ball import Ball
from game.physics.collision_system import CollisionSystem


collision_system = CollisionSystem()

pygame.init()

SCREEN_WIDTH = 1920
SCREEN_HEIGHT = 1080
screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
pygame.display.set_caption("Rugby League Simulator")

clock = pygame.time.Clock()
FPS = 60

field = Field(SCREEN_WIDTH, SCREEN_HEIGHT)

world = WorldState()
ball = Ball(field)
world.ball = ball

open_play = OpenPlayEngine(world)
world.open_play = open_play
world.open_play_engine = open_play

kickoff_engine = KickoffEngine(world, ball, field)

print(f"[BOOT] OpenPlayEngine starts in phase: {open_play.tes_phase.value}")


def reset_ball_and_world():
    kickoff_engine.reset_for_kickoff()


def kickoff():
    kickoff_engine.kickoff()


def draw_hud(surface):
    font = pygame.font.SysFont("consolas", 22)
    text = "[SPACE] Kickoff   [R] Reset   [ESC] Quit"
    img = font.render(text, True, (255, 255, 255))
    surface.blit(img, (20, SCREEN_HEIGHT - 35))


def _is_valid_position(p):
    for attr in ("x", "y"):
        val = getattr(p, attr, None)
        if val is None:
            return False
        if not isinstance(val, (int, float)):
            return False
        if isinstance(val, float):
            if math.isnan(val) or math.isinf(val):
                return False
    return True


print("MAIN LOOP STARTED")


def main():
    reset_ball_and_world()
    apply_receiving_kickoff_formation(world, field)
    apply_kicking_team_formation(world, field)

    world.ball.possessed_by = None
    # game_state is already OPEN_PLAY (set by reset_for_kickoff)
    # sub_state is already OPEN_PLAY
    kickoff()

    running = True
    while running:
        dt = clock.tick(FPS) / 1000.0

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_r:
                    reset_ball_and_world()
                    kickoff()
                elif event.key == pygame.K_SPACE:
                    reset_ball_and_world()
                    kickoff()

        # ═══════════════════════════════════════════════════════════
        # BALL UPDATE — MUST run every frame, independent of any FSM
        # ═══════════════════════════════════════════════════════════
        ball.update(dt)

        # KickoffEngine — gates on in_kickoff internally
        kickoff_engine.update(dt)

        # OpenPlayEngine — runs every frame. Defence is continuous
        # during kickoff and open play. Attack pipeline only activates
        # when a ball carrier exists (handled internally).
        open_play.update(dt)

        # Collision only when ball is in play (not during ball flight)
        if not world.in_kickoff:
            collision_system.resolve_all(world.players, world=world)

        world.frame_count += 1

        screen.fill((0, 0, 0))
        field.draw(screen)

        drawn = set()

        def safe_draw(player, color):
            if id(player) in drawn:
                return
            if not _is_valid_position(player):
                return
            px, py = field.metres_to_px(player.x, player.y)
            for v in (px, py):
                if v is None or not isinstance(v, (int, float)) or math.isnan(v) or math.isinf(v):
                    return
            try:
                pygame.draw.circle(
                    screen,
                    color,
                    (int(px), int(py)),
                    int(0.55 * field.scale),
                )
            except TypeError:
                return
            drawn.add(id(player))

        for p in world.receiving_team:
            safe_draw(p, SAFE_GREEN)

        for p in world.kicking_team:
            safe_draw(p, SAFE_RED)

        ball.draw(screen)
        draw_hud(screen)
        pygame.display.flip()

    pygame.quit()
    sys.exit()


if __name__ == "__main__":
    main()
