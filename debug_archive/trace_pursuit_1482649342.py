"""Runtime diagnostic: trace in_pursuit_mode, phase, BC position, and defender distance."""

import sys
import os

# Set headless pygame BEFORE any imports
os.environ['SDL_VIDEODRIVER'] = 'dummy'

sys.path.insert(0, 'c:/Users/User/Desktop/RLM')

# Monkey-patch OpenPlayEngine BEFORE imports
import world.open_play_engine as ope_mod
_original_update = ope_mod.OpenPlayEngine.update
_trace_log = []

def _traced_update(self, dt):
    _original_update(self, dt)
    w = self.world
    bc = w.ball_carrier
    bc_x = bc.x if bc is not None else 0.0
    bc_vx = bc.vx if bc is not None else 0.0
    closest_dist = 9999.0
    if bc is not None:
        for d in w.defending_team:
            dist = (d.x - bc.x)**2 + (d.y - bc.y)**2
            if dist < closest_dist:
                closest_dist = dist
        closest_dist = closest_dist ** 0.5
    tackling = self._phase not in ('OPEN_PLAY', 'PTB_READY', 'PTB_PREP', 'PTB_EXEC', 'PTB', 'POST_PTB_RESET')
    _trace_log.append({
        'frame': w.frame_count,
        'pursuit': w.in_pursuit_mode,
        'first_not_made': w.first_tackle_not_made,
        'phase': str(w.phase),
        'ope_phase': self._phase,
        'bc_x': bc_x,
        'bc_vx': bc_vx,
        'closest_def_dist': closest_dist,
        'tackling': tackling,
        'has_primary': self.primary is not None,
        'writer': getattr(w, 'current_target_writer', ''),
        'enabled': getattr(w, 'defence_enabled', True),
    })

ope_mod.OpenPlayEngine.update = _traced_update

# Now import everything
import pygame
# Initialise display early so Field can load textures
pygame.display.set_mode((1, 1))

from world.field import Field
from world.world_state import WorldState, GameWorldState, GameSubState, GamePhase
from world.open_play_engine import OpenPlayEngine
from kick_off.kickoff_formations import apply_receiving_kickoff_formation, apply_kicking_team_formation
from game.kickoff_engine import KickoffEngine
from ball.ball import Ball
from world.catch_system import check_for_catch

def main():
    log_path = 'c:/Users/User/Desktop/RLM/tmp/pursuit_trace.txt'
    field = Field(1920, 1080)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball
    open_play = OpenPlayEngine(world)
    world.open_play = open_play
    world.open_play_engine = open_play
    kickoff_engine = KickoffEngine(world, ball, field)

    kickoff_engine.reset_for_kickoff()
    apply_receiving_kickoff_formation(world, field)
    apply_kicking_team_formation(world, field)
    world.ball_owner = 'NONE'
    world.ball_carrier = None
    world.ball.possessed_by = None
    world.attacking_team = world.receiving_team
    world.defending_team = world.kicking_team
    kickoff_engine.kickoff()

    MAX_FRAMES = 1800
    for frame in range(MAX_FRAMES):
        dt = 1.0 / 60.0
        ball.update(dt)
        if world.phase == GamePhase.KICKOFF:
            kickoff_engine.update(dt)
            touched = check_for_catch(world, ball, dt)
            if touched:
                print(f'[TRACE] Frame {frame}: CATCH occurred, phase -> CATCH')
        elif world.phase == GamePhase.CATCH:
            if world.ball_carrier is not None:
                ope = world.open_play
                if ope is not None:
                    ope.start(world.ball_carrier)
            world.phase = GamePhase.OPEN_PLAY
            print(f'[TRACE] Frame {frame}: OPEN_PLAY started')
        elif world.phase == GamePhase.OPEN_PLAY:
            open_play.update(dt)

    # Analysis
    pursuit_frames = sum(1 for t in _trace_log if t['pursuit'])
    structured_frames = sum(1 for t in _trace_log if not t['pursuit'])
    transitions = []
    for i, t in enumerate(_trace_log):
        if i > 0 and _trace_log[i-1]['pursuit'] != t['pursuit']:
            transitions.append((t['frame'], t['pursuit']))

    with open(log_path, 'w') as f:
        f.write('=== PURSUIT MODE TRACE ===\n')
        f.write(f'Total frames traced: {len(_trace_log)}\n')
        f.write(f'Pursuit frames: {pursuit_frames}, Structured frames: {structured_frames}\n')
        f.write(f'Pursuit mode transitions: {len(transitions)}\n')
        for fr, val in transitions:
            f.write(f'  Frame {fr}: pursuit -> {val}\n')

        first_tackle = None
        for t in _trace_log:
            if t['tackling']:
                first_tackle = t
                break
        if first_tackle:
            f.write(f'First tackle at frame {first_tackle["frame"]}: phase={first_tackle["ope_phase"]}, pursuit={first_tackle["pursuit"]}\n')
        else:
            f.write('NO TACKLE EVER INITIATED\n')

        f.write('\nFrame | Pursuit | Phase | OPE Phase | BC.x | BC.vx | ClosestDef | Tackling | Writer | first_not_made\n')
        f.write('-' * 110 + '\n')
        for t in _trace_log:
            f.write(f'{t["frame"]:5d} | {str(t["pursuit"]):7s} | {str(t["phase"]):12s} | '
                    f'{t["ope_phase"]:15s} | {t["bc_x"]:5.1f} | {t["bc_vx"]:5.2f} | '
                    f'{t["closest_def_dist"]:8.2f} | {str(t["tackling"]):8s} | {t["writer"]:15s} | {str(t["first_not_made"]):5s}\n')

        structured_ever = any(not t['pursuit'] for t in _trace_log)
        f.write(f'\nStructured defence ever activated: {structured_ever}\n')

    # Also print summary
    print(f'Total frames: {len(_trace_log)}')
    print(f'Pursuit frames: {pursuit_frames}, Structured frames: {structured_frames}')
    print(f'Pursuit mode transitions: {len(transitions)}')
    for fr, val in transitions:
        print(f'  Frame {fr}: pursuit -> {val}')
    if first_tackle:
        print(f'First tackle at frame {first_tackle["frame"]}: phase={first_tackle["ope_phase"]}, pursuit={first_tackle["pursuit"]}')
    else:
        print('NO TACKLE EVER INITIATED')
    print(f'Structured defence ever activated: {structured_ever}')
    print(f'Trace written to {log_path}')

if __name__ == '__main__':
    main()
