"""
DIAGNOSTIC: trace what overwrites defender_role/target_x after catch reset.

Now with fix verification. Run this to:
1. Confirm the overwrite exists (before fix)
2. Apply the fix
3. Confirm the fix works

When you run this script, it will launch a pygame window.
Wait for the ball to be caught (typically within 200 frames).
Diagnostic output will print to console.
"""

import sys
import os
sys.path.insert(0, os.getcwd())

import random
import math
import pygame
import traceback

from world.field import Field
from world.world_state import WorldState, GameWorldState, GameSubState
from world.ruck_state import RuckState
from world.open_play_engine import OpenPlayEngine
from player.player import Player, PlayerState, DefenderRole
from player.receiver_selector import ReceiverSelector
from kick_off.controller import KickoffController
from kick_off.kickoff_formations import apply_receiving_kickoff_formation, apply_kicking_team_formation
from kickoff_kickchase import KickoffKickChaseController
from ball.ball import Ball
from game.physics.collision_system import CollisionSystem

# ============================================================
# MONKEY-PATCH PLAYER TO TRACE DEFENDER_ROLE / TARGET WRITES
# ============================================================
_role_writes = []
_target_writes = []
_state_writes = []
_orig_setattr = Player.__setattr__

def _traced_setattr(self, name, value):
    if name == 'defender_role':
        old = getattr(self, 'defender_role', None)
        if old != value:
            stack = ''.join(traceback.format_stack(limit=6)[-6:-1])
            _role_writes.append((self, old, value, stack, len(_role_writes)))
    elif name in ('target_x', 'target_y'):
        old = getattr(self, name, None)
        if old != value:
            stack = ''.join(traceback.format_stack(limit=6)[-6:-1])
            _target_writes.append((self, name, old, value, stack, len(_target_writes)))
    return _orig_setattr(self, name, value)

Player.__setattr__ = _traced_setattr



# ------------------------------------------------------------
# SETUP (mirrors run.py)
# ------------------------------------------------------------

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

open_play = OpenPlayEngine(world)
world.open_play = open_play

ko = KickoffController()
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
    global _role_writes, _target_writes, _state_writes
    _role_writes.clear(); _target_writes.clear(); _state_writes.clear()
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
    ko.start_kickoff()
    ko._kicking_team_players = world.kicking_team
    ko._receiving_team_players = world.receiving_team
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
# MAIN LOOP — runs simulation until catch + 10 frames
# ============================================================

def main():
    reset_ball_and_world()
    kickoff()
    
    CATCH_FRAME = None
    running = True
    frame = 0
    FINISH_FRAME = None  # die this many frames after catch

    while running and (FINISH_FRAME is None or frame <= FINISH_FRAME):
        dt = clock.tick(FPS) / 1000.0
        prev_role_n = len(_role_writes)
        prev_target_n = len(_target_writes)

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

            # ═══════════════════════════════════════════════════════════
            # AFTER CATCH GUARD: if try_catch transitioned to OPEN_PLAY,
            # skip apply_state / kick_chase for this frame.
            # apply_state would overwrite defender targets (they'd chase
            # ball.x_m instead of forming a LINE), and kick_chase would
            # re-apply KICK_CHASE roles.
            # ═══════════════════════════════════════════════════════════
            if world.game_state == GameWorldState.KICKOFF:
                # Still in kickoff: run normal kickoff logic
                for p in world.receiving_team:
                    p.apply_state(ball, dt, world)
                for p in world.kicking_team:
                    p.apply_state(ball, dt, world)
                if world.kickoff_kickchase and world.kickoff_kickchase.active:
                    world.kickoff_kickchase.update(world, dt)
                ko.update(ball.x_m, ball.y_m, ball.z_m, ball.grounded, touched_by=touched_by)
            else:
                # Transitioned to OPEN_PLAY: catch just happened.
                # Log the state immediately after catch for diagnostic.
                if CATCH_FRAME is None:
                    CATCH_FRAME = frame
                    FINISH_FRAME = frame + 10
                    print(f"\n{'='*60}")
                    print(f"FRAME {frame}: CATCH TRANSITION TO OPEN_PLAY")
                    print(f"{'='*60}")
                    
                    # Log post-catch state (should be clean reset)
                    print("\n--- POST-CATCH STATE (guard active, apply_state skipped) ---")
                    for i, d in enumerate(world.defending_team):
                        role = getattr(d, 'defender_role', None)
                        st = getattr(d, 'state', None)
                        st_name = st.name if st else '?'
                        print(f"  DEF {i} id={d.player_id}: role={role} state={st_name} "
                              f"target=({d.target_x:.1f},{d.target_y:.1f}) "
                              f"pos=({d.x:.1f},{d.y:.1f}) frozen={d.frozen}")

                # Still call ko.update with touch info
                ko.update(ball.x_m, ball.y_m, ball.z_m, ball.grounded, touched_by=touched_by)

        elif world.game_state == GameWorldState.OPEN_PLAY:
            open_play.update(dt)

        # ── Post-frame bookkeeping ──
        world.frame_count += 1
        if world.just_left_kickoff_timer > 0:
            world.just_left_kickoff_timer = max(0.0, world.just_left_kickoff_timer - dt)
        collision_system.resolve_all(world.players, world=world)

        # ── Log trace writes after catch frame ──
        if CATCH_FRAME is not None and frame == CATCH_FRAME:
            new_roles = _role_writes[prev_role_n:]
            new_targets = _target_writes[prev_target_n:]
            
            if new_roles:
                print(f"\n--- DEFENDER_ROLE WRITES ON CATCH FRAME {frame} ---")
                for p, old, val, stack, idx in new_roles:
                    print(f"  WRITE #{idx}: Player {p.player_id}: {old} -> {val}")
                    print(f"  Stack:\n{stack}\n")
            if new_targets:
                print(f"\n--- TARGET WRITES ON CATCH FRAME {frame} ---")
                for p, name, old, val, stack, idx in new_targets:
                    print(f"  WRITE #{idx}: Player {p.player_id} {name}: {old} -> {val}")
                    # Only show first 3 lines of stack to identify caller
                    lines = stack.strip().split('\n')
                    print(f"  Caller: {lines[0] if lines else '?'}")
                    for ln in lines[1:4]:
                        print(f"    {ln.strip()}")
                    print()
            if not new_roles and not new_targets:
                print(f"\n  ✓ No defender_role or target overwrites on catch frame (fix confirmed!)")

        # ── Log post-catch frames ──
        if CATCH_FRAME is not None and CATCH_FRAME < frame <= CATCH_FRAME + 5:
            print(f"\n--- FRAME {frame} (post-catch) ---")
            for i, d in enumerate(world.defending_team):
                role = getattr(d, 'defender_role', None)
                st = getattr(d, 'state', None)
                st_name = st.name if st else '?'
                print(f"  DEF {i} id={d.player_id}: role={role} state={st_name} "
                      f"target=({d.target_x:.1f},{d.target_y:.1f}) "
                      f"pos=({d.x:.1f},{d.y:.1f}) frozen={d.frozen}")

        # ── DRAW ──
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
    
    if CATCH_FRAME is not None:
        print(f"\n{'='*60}")
        print(f"DIAGNOSTIC SUMMARY — Catch at frame {CATCH_FRAME}")
        print(f"{'='*60}")
        print(f"  Total defender_role writes tracked: {len(_role_writes)}")
        print(f"  Total target_x/target_y writes tracked: {len(_target_writes)}")
        
        # Filter to only writes on/after catch frame
        catch_roles = [w for w in _role_writes if w[0] in world.defending_team and CATCH_FRAME <= world.frame_count]
        catch_targets = [w for w in _target_writes if w[0] in world.defending_team and CATCH_FRAME <= world.frame_count]
        print(f"  Defender_role writes after catch: {len(catch_roles)}")
        print(f"  Target writes after catch: {len(catch_targets)}")
        
        # Check for any KICK_CHASE role after catch
        kick_chase_after = False
        for p in world.defending_team:
            if getattr(p, 'defender_role', None) == DefenderRole.KICK_CHASE:
                if world.game_state != GameWorldState.KICKOFF:
                    print(f"  ⚠ WARNING: Player {p.player_id} has KICK_CHASE role outside KICKOFF!")
                    kick_chase_after = True
        if not kick_chase_after:
            print(f"  ✓ No KICK_CHASE roles outside KICKOFF")
        
        # Check defender targets are halfway-line aligned
        print(f"\n  Final defender state at frame {frame-1}:")
        for i, d in enumerate(world.defending_team):
            role = getattr(d, 'defender_role', None)
            print(f"    DEF {i} id={d.player_id}: role={role} "
                  f"target=({d.target_x:.1f},{d.target_y:.1f})")
    else:
        print("No catch occurred in 600 frames. Run again.")

if __name__ == "__main__":
    main()
