import sys
import math
import pygame

# ── Force stdout to UTF-8 and line-buffered — critical for auditor pipelines ──
sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
sys.stderr.reconfigure(encoding='utf-8', line_buffering=True)

SAFE_RED = (200, 0, 0)
SAFE_GREEN = (0, 200, 0)

from world.field import Field
from world.world_state import WorldState, GameWorldState, GameSubState, GamePhase
from world.open_play_engine import OpenPlayEngine

from kick_off.kickoff_formations import (
    apply_receiving_kickoff_formation,
    apply_kicking_team_formation,
)
from game.kickoff_engine import KickoffEngine

from ball.ball import Ball
from game.physics.collision_system import CollisionSystem
from world.catch_system import check_for_catch


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

# ── Audit flags ──
enable_audit = "--audit" in sys.argv
enable_sdea_audit = "--sdeaaudit" in sys.argv

open_play = OpenPlayEngine(world)
world.open_play = open_play
world.open_play_engine = open_play

kickoff_engine = KickoffEngine(world, ball, field)

# ── Kick-type demonstration (2026-09-20) ────────────────────────────────
# Keys 1-4 select a ball kick type and K launches one.  This exists so all
# four modelled kicks can be watched in the running match without touching
# the audited kickoff / open-play systems: it only ever writes the ball.
from ball import KickType as BallKickType

_DEMO_KICK_KEYS = {
    pygame.K_1: BallKickType.SPIRAL_PUNT,
    pygame.K_2: BallKickType.BOMB,
    pygame.K_3: BallKickType.GRUBBER,
    pygame.K_4: BallKickType.FLOATER,
}
demo_state = {"kick_type": BallKickType.SPIRAL_PUNT}


def demo_kick():
    """Launch a demonstration kick of the currently selected type.

    If the ball is in hand it is released first, so the kick always starts
    from a clean ball on the halfway line.  Purely a presentation aid - no
    world ownership, phase or player state is touched beyond the ball itself.
    """
    kick_type = demo_state["kick_type"]

    world.ball_carrier = None
    world.ball_owner = "NONE"
    ball.possessed_by = None
    ball.spawn_kickoff()
    ball.x_m = 60.0
    ball.y_m = 35.0

    if kick_type is BallKickType.GRUBBER:
        power, angle_deg = 24.0, 4.0
    else:
        power, angle_deg = 26.0, 34.0

    ball.kick(power, angle_deg, 180.0, kick_type=kick_type)
    print(
        f"[BALL_DEMO] kick_type={kick_type.value} "
        f"landing=({ball.landing_x_m:.1f}, {ball.landing_y_m:.1f}) "
        f"flight={ball.flight_time:.2f}s"
    )

# ── Wire TES auditor if --audit flag is set ──
if enable_audit:
    from game.audit.tes_audit import TESPhaseAuditor
    auditor = TESPhaseAuditor(open_play)
    open_play._auditor = auditor
    print("[AUDIT] TES audit enabled — writing audit_tes_frames_*.csv and audit_tes_issues_*.txt")

# ── Wire SDEA Movement Audit if --sdeaaudit flag is set ──
sdea_auditor = None
if enable_sdea_audit:
    from game.audit.sdea_movement_audit import SDEAMovementAudit
    sdea_auditor = SDEAMovementAudit()
    print("[SDEA AUDIT] Movement contract audit enabled — writing audit_sdea_frames_*.csv and audit_sdea_issues_*.txt")

# ── Defence reactivity agent (bridges PTB-to-OPEN_PLAY defence blind window) ──
from ai_agents.defence_reactivity_agent import DefenceReactivityAgent
reactivity_agent = DefenceReactivityAgent(world)

print(f"[BOOT] OpenPlayEngine starts in phase: {open_play.tes_phase.value}")


def reset_ball_and_world():
    kickoff_engine.reset_for_kickoff()


def kickoff():
    kickoff_engine.kickoff()


def draw_hud(surface, show_role_tags=True):
    font = pygame.font.SysFont("consolas", 22)
    label_state = "ON" if show_role_tags else "OFF"
    text = f"[SPACE] Kickoff   [R] Reset   [L] Labels {label_state}   [ESC] Quit"
    img = font.render(text, True, (255, 255, 255))
    surface.blit(img, (20, SCREEN_HEIGHT - 35))

    ball_line = (
        f"[1-4] Ball kick type: {demo_state['kick_type'].value:<11}   [K] Kick it"
    )
    ball_img = font.render(ball_line, True, (255, 214, 140))
    surface.blit(ball_img, (20, SCREEN_HEIGHT - 64))


def draw_player_number(screen, player, number):
    """
    Draw a stable, non-rotating jersey number centered inside the player's circle.
    """
    px, py = field.metres_to_px(player.x, player.y)
    font_size = int(0.55 * field.scale * 1.2)
    font = pygame.font.SysFont("Arial", font_size, bold=True)
    img = font.render(str(number), True, (255, 255, 255))
    rect = img.get_rect(center=(int(px), int(py)))
    screen.blit(img, rect)


def render_defender_role_tag(screen, player):
    """
    Debug overlay: draw a short role tag (T1 / MA / DLR / DR10 / DH ...)
    above the player so role transitions during TES / RELEASE / retreat-10 /
    joiner logic are visible at a glance.  Reads Player.role_tag each frame,
    so it always reflects the current defender_role.

    TACKLER-COMMIT LABEL (2026-08-13): when the OPEN_PLAY commit writer
    assigned this defender tackle_role = PRIMARY/SECONDARY, a cyan "P"/"S"
    tag is drawn above the role tag so we can see when a defender switches
    into a tackler role.  Purely for debugging — no gameplay effect.
    """
    # ── TACKLER-COMMIT LABEL (P/S) — drawn above the role tag ──
    tackle_role = getattr(player, "tackle_role", None)
    if tackle_role in ("PRIMARY", "SECONDARY") and getattr(player, "_tackle_commit_active", False):
        px, py = field.metres_to_px(player.x, player.y)
        if px is not None and py is not None:
            commit_label = "P" if tackle_role == "PRIMARY" else "S"
            commit_font = pygame.font.SysFont("consolas", 18, bold=True)
            commit_img = commit_font.render(commit_label, True, (0, 255, 255))
            commit_rect = commit_img.get_rect(
                midbottom=(int(px), int(py) - int(0.75 * field.scale) - 4)
            )
            commit_backing = commit_img.get_rect(center=commit_rect.center)
            pad = 2
            pygame.draw.rect(
                screen,
                (20, 20, 20),
                (
                    commit_backing.x - pad,
                    commit_backing.y - pad,
                    commit_backing.w + pad * 2,
                    commit_backing.h + pad * 2,
                ),
            )
            screen.blit(commit_img, commit_rect)

    # DEPTH ROLE WIRE (2026-08-07): the depth_role tag (A/B/C/D/CENTRE/WINGER)
    # is drawn in white just below the yellow engagement-role tag so depth
    # identity is visible per defender.  Without this overlay, the depth roles
    # driving the geometry width curve are invisible in the simulation.
    if not hasattr(player, "defender_role"):
        return
    role = getattr(player, "defender_role", None)
    if role is None:
        tes_role = getattr(player, "tes_tackler_role", None)
        if tes_role is None:
            return  # no role yet — nothing to draw
    tag = getattr(player, "role_tag", None)
    if not tag:
        return
    px, py = field.metres_to_px(player.x, player.y)
    if px is None or py is None:
        return
    # Small, high-contrast tag just above the player circle.
    font = pygame.font.SysFont("consolas", 16, bold=True)
    img = font.render(tag, True, (255, 255, 0))
    rect = img.get_rect(midbottom=(int(px), int(py) - int(0.75 * field.scale)))
    # Dark backing for readability on grass.
    backing = img.get_rect(center=rect.center)
    pad = 2
    pygame.draw.rect(
        screen,
        (20, 20, 20),
        (backing.x - pad, backing.y - pad, backing.w + pad * 2, backing.h + pad * 2),
    )
    screen.blit(img, rect)

    # ── Depth role tag (white) just below the yellow role tag ──
    depth_tag = getattr(player, "depth_role_tag", None)
    if not depth_tag or depth_tag == "-":
        return
    depth_img = font.render(depth_tag, True, (255, 255, 255))
    # midtop at the bottom of the role tag so the two stack cleanly.
    depth_rect = depth_img.get_rect(
        midtop=(int(px), rect.bottom + 1)
    )
    depth_backing = depth_img.get_rect(center=depth_rect.center)
    pygame.draw.rect(
        screen,
        (20, 20, 20),
        (
            depth_backing.x - pad,
            depth_backing.y - pad,
            depth_backing.w + pad * 2,
            depth_backing.h + pad * 2,
        ),
    )
    screen.blit(depth_img, depth_rect)


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

    # Ball ownership: clear any stale possessed_by — ball is at kickoff position,
    # no carrier exists yet.  OPE will set BC ownership on first catch.
    world.ball_owner = "NONE"
    world.ball_carrier = None
    world.ball.possessed_by = None
    # game_state is already OPEN_PLAY (set by reset_for_kickoff)
    # sub_state is already OPEN_PLAY
    # phase is already KICKOFF (set by reset_for_kickoff)
    kickoff()

    # ── 'L' toggles player role tags (jersey numbers stay on) ──
    show_role_tags = True

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
                elif event.key == pygame.K_l:
                    show_role_tags = not show_role_tags
                elif event.key == pygame.K_k:
                    demo_kick()
                elif event.key in _DEMO_KICK_KEYS:
                    demo_state["kick_type"] = _DEMO_KICK_KEYS[event.key]
                    print(f"[BALL_DEMO] selected {demo_state['kick_type'].value}")

        # ═══════════════════════════════════════════════════════════
        # BALL UPDATE — MUST run every frame, independent of any FSM
        # ═══════════════════════════════════════════════════════════
        ball.update(dt)

        # ═══════════════════════════════════════════════════════════
        # PHASE-BASED DISPATCH — single source of truth
        #
        # KICKOFF: only kickoff engine + catch system. No OPE, no defence/attack.
        # CATCH:   one-frame transition to hand control to OPE.
        # OPEN_PLAY: OPE runs (defence, attack, TES, PTB all inside).
        # ═══════════════════════════════════════════════════════════

        world.frame_count += 1

        if world.phase == GamePhase.KICKOFF:
            # ── KICKOFF: ball flight + chase + catch detection ──
            # KickoffEngine handles ball physics during flight (is_active guards).
            # check_for_catch detects the catch and sets world.phase = CATCH.
            kickoff_engine.update(dt)
            check_for_catch(world, ball, dt)

        elif world.phase == GamePhase.CATCH:
            # ── CATCH: one-frame transition to open play ──
            # The catch is already confirmed (phase set by _perform_catch).
            # No OPE runs this frame — just transition to OPEN_PLAY.
            # If a ball carrier was set by the catch system, start OPE.
            if world.ball_carrier is not None:
                ope = world.open_play
                if ope is not None:
                    ope.start(world.ball_carrier)
            world.phase = GamePhase.OPEN_PLAY

        elif world.phase == GamePhase.OPEN_PLAY:
            # ── OPEN_PLAY: full simulation ──
            # OpenPlayEngine runs all sub-systems (defence, attack, TES, PTB)
            # and handles its own internal sub-state machine.
            open_play.update(dt)

            # ── SDEA movement audit (logs every OPEN_PLAY frame) ──
            if sdea_auditor is not None:
                sdea_auditor.log_frame(world, open_play._phase)

            # ── Defence reactivity agent (bridges PTB-to-OPEN_PLAY gap) ──
            # Runs AFTER OPE so it can observe the latest phase.
            # Runs BEFORE collision so targets are set before movement resolution.
            reactivity_agent.update()

            # Collision only when no TES engagement is active
            # (TES locks BC/tackler physics internally)
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)

        screen.fill((0, 0, 0))
        field.draw(screen)

        # ── FACING OVERRIDE: Front indicator line (MANDATORY) ──
        # Drawn FIRST so the player circle (drawn AFTER, below) covers the
        # inner part — the dash starts at the circle edge instead of
        # overlapping the circle.
        L = 8.0
        for p in world.players:
            if not _is_valid_position(p):
                continue
            px, py = field.metres_to_px(p.x, p.y)
            if px is None or py is None:
                continue
            fx = px + math.cos(p.facing) * L
            fy = py + math.sin(p.facing) * L
            pygame.draw.line(screen, (255, 50, 50), (int(px), int(py)), (int(fx), int(fy)), 3)

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

        for i, p in enumerate(world.receiving_team, start=1):
            safe_draw(p, SAFE_GREEN)
            draw_player_number(screen, p, i)
            if show_role_tags:
                render_defender_role_tag(screen, p)

        for i, p in enumerate(world.kicking_team, start=1):
            safe_draw(p, SAFE_RED)
            draw_player_number(screen, p, i)
            if show_role_tags:
                render_defender_role_tag(screen, p)

        ball.draw(screen)
        draw_hud(screen, show_role_tags)
        pygame.display.flip()

    # ── TES audit finalize ──
    if enable_audit:
        ope_auditor = getattr(open_play, "_auditor", None)
        if ope_auditor is not None:
            ope_auditor.finalize()

    # ── SDEA movement audit finalize ──
    if sdea_auditor is not None:
        sdea_auditor.finalize()

    pygame.quit()
    sys.exit()


if __name__ == "__main__":
    main()
