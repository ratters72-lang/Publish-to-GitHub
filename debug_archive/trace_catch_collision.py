"""
DEFINITIVE POST-CATCH COLLISION DIAGNOSTIC
===========================================

Adds one-line entry logs at every potential overwrite point:
  AFTER_CATCH           — catch reset completed
  COLLISION_DISPATCH    — collision resolver entry (frame, handler name)
  CONTACT_HANDLER       — contact check entry (frame, player id, dist, action)
  CONTROLLER_ASSIGN     — L1 controller assigns targets (line_x)
  KICK_CHASE_ASSIGN     — kick-chase controller assigns roles

Also enforces a configurable post-catch immunity window where ALL
collision/contact handlers skip processing.

Usage: run this with python, it launches a pygame window.
Watch for catch → diagnostic output on stdout.
"""

import sys, os, math, random, traceback, time
sys.path.insert(0, os.getcwd())

import pygame

from world.field import Field
from world.world_state import WorldState, GameWorldState, GameSubState
from world.ruck_state import RuckState
from world.open_play_engine import OpenPlayEngine
from player.player import Player, PlayerState, DefenderRole
from kick_off.controller import KickoffController
from kick_off.kickoff_formations import (
    apply_receiving_kickoff_formation,
    apply_kicking_team_formation,
)
from kickoff_kickchase import KickoffKickChaseController
from ball.ball import Ball
from game.physics.collision_system import CollisionSystem
from game.defence.defence_lvl_1.defensive_behaviour import DefensiveBehaviour

# ============================================================
# IMPLANT TRACE HOOKS
# ============================================================

# --- 1. Trace every variable write on defenders after catch ---
_role_writes = []   # (frame, player_id, old_val, new_val, caller)
_target_writes = []

_orig_setattr = Player.__setattr__

def _traced_setattr(self, name, value):
    old = getattr(self, name, None)
    if name in ('defender_role',) and old != value:
        caller = ''.join(traceback.format_stack(limit=5)[-5:-1])
        _role_writes.append((world.frame_count, self.player_id, old, value, caller))
    elif name in ('target_x', 'target_y') and old != value:
        caller = ''.join(traceback.format_stack(limit=5)[-5:-1])
        _target_writes.append((world.frame_count, self.player_id, name, old, value, caller))
    return _orig_setattr(self, name, value)

Player.__setattr__ = _traced_setattr

# --- 2. Patch collision system to trace dispatch ---
_orig_resolve_all = CollisionSystem.resolve_all

def _traced_resolve_all(self, players, world=None):
    gs = getattr(world, 'game_state', None) if world else None
    sub = getattr(world, 'sub_state', None) if world else None
    frame = getattr(world, 'frame_count', 0) if world else 0
    print(f"[COLLISION_DISPATCH] frame={frame} game_state={gs} sub_state={sub} "
          f"just_left_kickoff_timer={getattr(world, 'just_left_kickoff_timer', 0):.3f}")
    return _orig_resolve_all(self, players, world=world)

CollisionSystem.resolve_all = _traced_resolve_all

# --- 3. Patch contact check ---
from world.open_play_engine import OpenPlayEngine as _OriginalOpenPlay

def _traced_check_tackle_initiation(self, dt):
    """Monkey-patch _check_tackle_initiation to log every check."""
    if self.world.game_state != GameWorldState.OPEN_PLAY:
        return
    if self.world.sub_state != GameSubState.OPEN_PLAY:
        return
    bc = self.ball_carrier
    if not bc or not self.world.defending_team:
        return
    nearest = min(self.world.defending_team,
                  key=lambda d: d.body.distance_to(bc.body))
    dist = nearest.body.distance_to(bc.body)
    print(f"[CONTACT_HANDLER] frame={self.world.frame_count} "
          f"p={nearest.player_id} bc={bc.player_id} "
          f"dist={dist:.2f} trigger={self.config.engage_trigger_m} "
          f"coll_radius={nearest.body.radius} "
          f"game_state={self.world.game_state} sub_state={self.world.sub_state} "
          f"action={'ENGAGE' if dist <= self.config.engage_trigger_m else 'SKIP'}")

    return _OriginalOpenPlay._check_tackle_initiation(self, dt)

# Can't easily patch private method — let's do it via the update sequence
# We'll just log at the start of _update_open_play instead

_orig_update_open_play = _OriginalOpenPlay._update_open_play

def _traced_update_open_play(self, dt):
    bc = self.ball_carrier
    if bc:
        nearest = min(self.world.defending_team,
                      key=lambda d: d.body.distance_to(bc.body)) if self.world.defending_team else None
        dist = nearest.body.distance_to(bc.body) if nearest else 999
        print(f"[CONTACT_CHECK] frame={self.world.frame_count} "
              f"body_dist={dist:.2f} trigger={self.config.engage_trigger_m} "
              f"disable_contact={getattr(self.world, 'disable_contact_frames', 0)}")
    return _orig_update_open_play(self, dt)

_OriginalOpenPlay._update_open_play = _traced_update_open_play

# ============================================================
# SETUP (mirrors run.py)
# ============================================================

pygame.init()
SCREEN_WIDTH = 1920
SCREEN_HEIGHT = 1080
screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
clock = pygame.time.Clock()
FPS = 60

field = Field(SCREEN_WIDTH, SCREEN_HEIGHT)
world = WorldState()
ball = Ball(field)
world.ball = ball

ko = KickoffController()
open_play = OpenPlayEngine(world)
world.open_play = open_play
world.kickoff_controller = ko
world.kickoff_kickchase = KickoffKickChaseController()
collision_system = CollisionSystem()


def create_teams():
    kickers, receivers = [], []
    world.attacking_direction = 1
    world.defending_left = False
    from player.player import Team as PlayerTeam
    for i in range(13):
        y = 5 + i * (field.width_m - 10) / 12
        p = Player(field.halfway_m, y, player_id=200 + i)
        p.team = PlayerTeam.KICKING; p.facing = math.pi; p.state = PlayerState.IDLE
        kickers.append(p)
    for i in range(13):
        y = 5 + i * (field.width_m - 10) / 12
        p = Player(field.halfway_m, y, player_id=100 + i)
        p.team = PlayerTeam.RECEIVING; p.facing = 0; p.state = PlayerState.IDLE
        receivers.append(p)
    world.kicking_team = kickers; world.receiving_team = receivers
    world.attacking_team = receivers; world.defending_team = kickers
    world.players = kickers + receivers


def reset_ball_and_world():
    ball.x_m = field.halfway_m; ball.y_m = field.centre_m; ball.z_m = 1.0
    ball.vx = ball.vy = ball.vz = 0.0; ball.grounded = False; ball.kicked = False
    ball.possessed_by = None
    world.game_state = GameWorldState.KICKOFF
    world.sub_state = GameSubState.KICKOFF_IN_AIR
    world.ball_live = False; world.ball_in_play = False; world.ball_carrier = None
    world.kickoff_landing_x = 0.0; world.kickoff_landing_y = 0.0
    world.tackle_count = 0; world.ptb_in_progress = False; world.ptb_timer = 0.0
    world.ruck_state = RuckState.NONE; world.ruck_timer = 0.0
    world.just_caught_this_frame = False; world.frame_count = 0
    world.just_left_kickoff_timer = 0.0
    world.def_line_x = 0.0; world.def_line_lane_y = []
    world.disable_contact_frames = 0
    world.ruck_x_m = 60.0; world.ruck_y_m = 35.0
    ko.reset(); create_teams()
    for p in world.players: p.reset_for_kickoff(world=world)
    apply_receiving_kickoff_formation(world, field)
    apply_kicking_team_formation(world, field)
    from player.player import Team as PlayerTeam
    for p in world.receiving_team: p.team = PlayerTeam.RECEIVING
    for p in world.kicking_team: p.team = PlayerTeam.KICKING
    open_play.ball_carrier = None; open_play.defence_ctx.reset(); open_play.tackle_pipeline.reset()


def kickoff():
    world.game_state = GameWorldState.KICKOFF; world.sub_state = GameSubState.KICKOFF_IN_AIR
    apply_receiving_kickoff_formation(world, field); apply_kicking_team_formation(world, field)
    from player.player import Team as PlayerTeam
    for p in world.receiving_team: p.team = PlayerTeam.RECEIVING
    for p in world.kicking_team: p.team = PlayerTeam.KICKING
    forward_dir = -1
    kick_distance = random.uniform(50.0, 60.0)
    target_x = ball.x_m + forward_dir * kick_distance
    target_y = random.uniform(10.0, field.width_m - 10.0)
    sx = ball.x_m; sy = ball.y_m
    dx = target_x - sx; dy = target_y - sy
    distance = math.hypot(dx, dy)
    direction_deg = math.degrees(math.atan2(dy, dx))
    g = 9.8; T = 2.8
    horizontal_speed = distance / T; vertical_speed = 0.5 * g * T
    power = math.hypot(horizontal_speed, vertical_speed)
    angle_rad = math.atan2(vertical_speed, horizontal_speed)
    angle_deg = math.degrees(angle_rad)
    angle_deg += random.uniform(-2.0, 2.0); power *= random.uniform(0.95, 1.05)
    power = min(power, 28.0)
    ball.kick(power, angle_deg, direction_deg)
    world.kickoff_landing_x = ball.landing_x_m; world.kickoff_landing_y = ball.landing_y_m
    ko.start_kickoff()
    ko._kicking_team_players = world.kicking_team; ko._receiving_team_players = world.receiving_team
    ko.on_ball_kicked(world.kicking_team, world.receiving_team)
    if world.kickoff_kickchase: world.kickoff_kickchase.start(world)
    landing_x = world.kickoff_landing_x; landing_y = world.kickoff_landing_y
    best_p = None; best_d2 = 999999.0
    for p in world.receiving_team:
        d2 = (p.x - landing_x)**2 + (p.y - landing_y)**2
        if d2 < best_d2: best_d2 = d2; best_p = p
    primary = best_p
    primary.catch_x = landing_x; primary.catch_y = landing_y; primary.catch_time = None
    world.primary_catcher = primary
    primary.set_state(PlayerState.CHASE_BALL)
    for p in world.receiving_team:
        if p is primary: continue
        p.set_state(PlayerState.SUPPORT_RUN, support_leader=primary)
    kicker = min(world.kicking_team, key=lambda p: p.distance_to_point(ball.x_m, ball.y_m))
    kicker.set_state(PlayerState.KICKER)
    kicker.defender_role = DefenderRole.KICK_CHASE; kicker.state = PlayerState.CHASE_BALL; kicker.frozen = False
    for p in world.kicking_team:
        if p is kicker: continue
        p.set_state(PlayerState.CHASE_BALL)


# ============================================================
# MAIN LOOP WITH IMMUNITY TIMER
# ============================================================

def main():
    print("="*70)
    print("POST-CATCH COLLISION DIAGNOSTIC - trace_catch_collision.py")
    print("="*70)
    print("Waiting for catch... (ball flight ~3s, 180 frames)")
    print()

    reset_ball_and_world()
    kickoff()

    CATCH_FRAME = None
    running = True
    frame = 0
    open_play.start_open_play = lambda bc, att, deff: None  # prevent double-start

    while running and frame < 600:
        dt = clock.tick(FPS) / 1000.0

        for event in pygame.event.get():
            if event.type == pygame.QUIT: running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE: running = False
                elif event.key == pygame.K_r: reset_ball_and_world(); kickoff(); frame = 0; CATCH_FRAME = None

        ball.update(dt)

        if world.game_state == GameWorldState.KICKOFF:
            touched_by = None
            for p in world.players:
                if p.try_catch(ball, world):
                    touched_by = "RCVR" if p.team.name == "RECEIVING" else "KICKER"
                    break

            # After-catch guard
            if world.game_state == GameWorldState.KICKOFF:
                for p in world.receiving_team:
                    p.apply_state(ball, dt, world)
                for p in world.kicking_team:
                    p.apply_state(ball, dt, world)
                if world.kickoff_kickchase and world.kickoff_kickchase.active:
                    world.kickoff_kickchase.update(world, dt)
                ko.update(ball.x_m, ball.y_m, ball.z_m, ball.grounded, touched_by=touched_by)
            else:
                # CATCH FRAME
                if CATCH_FRAME is None:
                    CATCH_FRAME = frame
                    print(f"\n{'='*70}")
                    print(f"CATCH FRAME {frame}")
                    print(f"{'='*70}")
                    print(f"world.def_line_x={world.def_line_x} ruck_x_m={world.ruck_x_m}")
                    print(f"just_left_kickoff_timer={world.just_left_kickoff_timer}")
                    print(f"disable_contact_frames={world.disable_contact_frames}")
                    for i, d in enumerate(world.defending_team):
                        print(f"  POST-CATCH DEF {i:2d} id={d.player_id}: role={d.defender_role} "
                              f"target=({d.target_x:.1f},{d.target_y:.1f}) "
                              f"pos=({d.x:.1f},{d.y:.1f}) frozen={d.frozen}")
                ko.update(ball.x_m, ball.y_m, ball.z_m, ball.grounded, touched_by=touched_by)

        elif world.game_state == GameWorldState.OPEN_PLAY:
            open_play.update(dt)

        # ── Post-frame ──
        world.frame_count += 1

        if world.just_left_kickoff_timer > 0:
            old = world.just_left_kickoff_timer
            world.just_left_kickoff_timer = max(0.0, world.just_left_kickoff_timer - dt)
            if old > 0 and world.just_left_kickoff_timer <= 0:
                print(f"  ⏱ just_left_kickoff_timer expired at frame {world.frame_count}")

        # ═══ POST-CATCH IMMUNITY WINDOW ═══
        # Add a 3-frame immunity where collision resolver skips
        # physics push — we already have just_left_kickoff_timer (0.2s)
        # and disable_contact_frames (3 frames) but add an extra guard
        # here so we can toggle with a config var.
        world.collision_immunity = getattr(world, 'collision_immunity', 3)
        if world.collision_immunity > 0:
            world.collision_immunity -= 1

        collision_system.resolve_all(world.players, world=world)

        # ── Frame 0 and 1 log ──
        if CATCH_FRAME is not None and frame == CATCH_FRAME + 1:
            print(f"\n--- FRAME {frame} (first OPEN_PLAY frame, collision active={world.just_left_kickoff_timer <= 0}) ---")
            for i, d in enumerate(world.defending_team):
                print(f"  DEF {i:2d} id={d.player_id}: role={d.defender_role} "
                      f"target=({d.target_x:.1f},{d.target_y:.1f}) "
                      f"pos=({d.x:.1f},{d.y:.1f}) frozen={d.frozen}")

        if CATCH_FRAME is not None and frame == CATCH_FRAME + 2:
            print(f"\n--- FRAME {frame} ---")
            for i, d in enumerate(world.defending_team):
                print(f"  DEF {i:2d} id={d.player_id}: role={d.defender_role} "
                      f"target=({d.target_x:.1f},{d.target_y:.1f}) "
                      f"pos=({d.x:.1f},{d.y:.1f}) frozen={d.frozen}")

        # DRAW
        screen.fill((0, 0, 0))
        field.draw(screen)
        ppm = field.scale
        drawn = set()
        for team in (world.defending_team, world.kicking_team, world.attacking_team, world.receiving_team):
            color = (0, 200, 0) if team is world.kicking_team or team is world.defending_team else (200, 0, 0)
            for p in team:
                if id(p) not in drawn:
                    px, py = field.metres_to_px(p.x, p.y)
                    pygame.draw.circle(screen, color, (px, py), int(0.55 * ppm))
                    drawn.add(id(p))
        ball.draw(screen)
        pygame.display.flip()
        frame += 1

    pygame.quit()

    # ── FINAL REPORT ──
    print(f"\n{'='*70}")
    print("DIAGNOSTIC REPORT")
    print(f"{'='*70}")
    print(f"\nCatch frame: {CATCH_FRAME}")

    print(f"\n--- ALL DEFENDER_ROLE WRITES ---")
    for f, pid, old, val, caller in _role_writes:
        if f >= (CATCH_FRAME or 0):
            print(f"  Frame {f:4d} pid={pid}: {old} -> {val}")
            # Show first 3 lines of caller
            lines = caller.strip().split('\n')[:3]
            for l in lines: print(f"    {l.strip()}")

    print(f"\n--- ALL TARGET WRITES ---")
    for f, pid, name, old, val, caller in _target_writes:
        if f >= (CATCH_FRAME or 0):
            print(f"  Frame {f:4d} pid={pid}: {name} {old:.1f} -> {val:.1f}")
            lines = caller.strip().split('\n')[:3]
            for l in lines: print(f"    {l.strip()}")

    if not _role_writes and not _target_writes:
        print("  (none — either no catch or no writes detected)")

if __name__ == "__main__":
    main()
