"""Verify pursuit mode clearing: trace in_pursuit_mode across a complete simulation."""
import sys, os
sys.path.insert(0, 'c:/Users/User/Desktop/RLM')
os.environ['SDL_VIDEODRIVER'] = 'dummy'

# Monkey-patch OpenPlayEngine BEFORE imports
import world.open_play_engine as ope_mod
_original_update = ope_mod.OpenPlayEngine.update
_trace = []
_on_ball_secured_calls = []
_on_tackle_complete_calls = []
_on_ptb_begin_calls = []

# Wrap on_ball_secured, on_tackle_complete, on_ptb_begin
_orig_on_ball = ope_mod.OpenPlayEngine.on_ball_secured
_orig_on_tackle = ope_mod.OpenPlayEngine.on_tackle_complete
_orig_on_ptb = ope_mod.OpenPlayEngine.on_ptb_begin

def _traced_on_ball(self):
    _on_ball_secured_calls.append(self.world.frame_count)
    return _orig_on_ball(self)

def _traced_on_tackle(self):
    _on_tackle_complete_calls.append(self.world.frame_count)
    return _orig_on_tackle(self)

def _traced_on_ptb(self):
    _on_ptb_begin_calls.append(self.world.frame_count)
    return _orig_on_ptb(self)

ope_mod.OpenPlayEngine.on_ball_secured = _traced_on_ball
ope_mod.OpenPlayEngine.on_tackle_complete = _traced_on_tackle
ope_mod.OpenPlayEngine.on_ptb_begin = _traced_on_ptb

def _traced_update(self, dt):
    _original_update(self, dt)
    w = self.world
    bc = w.ball_carrier
    bc_vx = bc.vx if bc is not None else 0.0
    bc_x = bc.x if bc is not None else 0.0
    closest_dist = 9999.0
    if bc is not None:
        for d in w.defending_team:
            dd = (d.x - bc.x)**2 + (d.y - bc.y)**2
            if dd < closest_dist:
                closest_dist = dd
        closest_dist = closest_dist ** 0.5
    tes_phase = self._phase
    tackling = tes_phase not in ('OPEN_PLAY','PTB_READY','PTB_PREP','PTB_EXEC','PTB','POST_PTB_RESET')
    _trace.append({
        'frame': w.frame_count, 'pursuit': w.in_pursuit_mode,
        'first_not_made': w.first_tackle_not_made,
        'phase': str(w.phase), 'ope_phase': tes_phase,
        'bc_x': bc_x, 'bc_vx': bc_vx,
        'closest_def_dist': closest_dist,
        'tackling': tackling,
        'has_primary': self.primary is not None,
        'writer': getattr(w,'current_target_writer',''),
    })

ope_mod.OpenPlayEngine.update = _traced_update

import pygame
pygame.display.set_mode((1,1))

from world.field import Field
from world.world_state import *  # noqa
from world.world_state import WorldState, GamePhase, set_ball_carrier, get_defence_target
from world.open_play_engine import OpenPlayEngine
from kick_off.kickoff_formations import apply_receiving_kickoff_formation, apply_kicking_team_formation
from game.kickoff_engine import KickoffEngine
from ball.ball import Ball
from world.catch_system import check_for_catch

def main():
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

    MAX = 2400
    for frame in range(MAX):
        dt = 1.0/60.0
        ball.update(dt)
        if world.phase == GamePhase.KICKOFF:
            kickoff_engine.update(dt)
            touched = check_for_catch(world, ball, dt)
            if touched:
                print(f'CATCH at frame {frame}')
        elif world.phase == GamePhase.CATCH:
            if world.ball_carrier:
                open_play.start(world.ball_carrier)
            world.phase = GamePhase.OPEN_PLAY
            print(f'OPEN_PLAY at frame {frame}')
        elif world.phase == GamePhase.OPEN_PLAY:
            open_play.update(dt)

    print('\n=== RESULTS ===')
    pursuit_frames = sum(1 for t in _trace if t['pursuit'])
    total = len(_trace)
    print(f'Frames: {total}, pursuit={pursuit_frames}, structured={total-pursuit_frames}')

    transitions = []
    for i, t in enumerate(_trace):
        if i>0 and _trace[i-1]['pursuit'] != t['pursuit']:
            transitions.append((t['frame'], t['pursuit']))
    print(f'Transitions: {len(transitions)}')
    for f,v in transitions:
        print(f'  Frame {f}: pursuit -> {v}')

    # Find first tackle
    for t in _trace:
        if t['tackling']:
            print(f'\nFirst tackling frame {t["frame"]}: ope_phase={t["ope_phase"]}, pursuit={t["pursuit"]}')
            break

    # Clearing method calls
    print(f'\non_ball_secured() calls: {len(_on_ball_secured_calls)} times at frames {_on_ball_secured_calls}')
    print(f'on_tackle_complete() calls: {len(_on_tackle_complete_calls)} times at frames {_on_tackle_complete_calls}')
    print(f'on_ptb_begin() calls: {len(_on_ptb_begin_calls)} times at frames {_on_ptb_begin_calls}')

    structured_ever = any(not t['pursuit'] for t in _trace)
    print(f'\nStructured defence ever activated: {structured_ever}')

if __name__ == '__main__':
    main()
