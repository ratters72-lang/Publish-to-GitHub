"""tmp_probe_target_x_writer.py — name the exact writer of `bc.target_x`.

During RELEASE the engine's ONLY write of `bc.target_x` is the (now clamped)
stand-up target in `_update_release`.  The verifier still saw 28 frames with
`bc.target_x = 120.44` (== unclamped `ruck_x - 0.15`), so some other writer
re-stamps it every frame.  This intercepts `Player.__setattr__` and records the
call site whenever the BALL CARRIER's `target_x` is set OUTSIDE the field.
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import random
import sys
import traceback

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import pygame

pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

import world.open_play_engine as ope_mod

ope_mod.DEBUG_SDEA_PHYSICS = False
ope_mod.DEBUG_OPE_TRACE = False

from world.field import Field
from world.world_state import WorldState, GamePhase
from world.open_play_engine import OpenPlayEngine
from kick_off.kickoff_formations import (
    apply_receiving_kickoff_formation,
    apply_kicking_team_formation,
)
from game.kickoff_engine import KickoffEngine
from ball.ball import Ball
from game.physics.collision_system import CollisionSystem
from world.catch_system import check_for_catch
from player.player import Player, FIELD_MIN_X, FIELD_MAX_X

FRAMES = 6000
SEED = 20260827
TOL = 1e-6

_SPY_BC = None
_SPY_OPE = None
_SITES = {}
_SITE_VALS = {}
_RECORDS = []


def install_spy():
    """Record every out-of-field `target_x` write on the ball carrier."""
    orig = Player.__setattr__

    def spy(self, name, value):
        if name == "target_x" and self is _SPY_BC:
            try:
                v = float(value)
            except (TypeError, ValueError):
                v = None
            if v is not None and (v > FIELD_MAX_X + TOL
                                  or v < FIELD_MIN_X - TOL):
                stack = traceback.extract_stack()[:-1]
                site = None
                for fr in reversed(stack):
                    base = os.path.basename(fr.filename)
                    if base in ("player.py", "target_stabiliser.py",
                                "open_play_engine.py",
                                "release_controller.py"):
                        site = "%s:%d  %s" % (
                            base, fr.lineno, (fr.line or "").strip())
                        break
                if site is None and stack:
                    fr = stack[-1]
                    site = "%s:%d  %s" % (
                        os.path.basename(fr.filename), fr.lineno,
                        (fr.line or "").strip())
                # Key by the phase AT WRITE TIME: the RELEASE entry frame is
                # the one where `_phase` flips INTO RELEASE during the very
                # update call that performs the write, so a phase test taken
                # before the call misses it.
                ph = getattr(_SPY_OPE, "_phase", None)
                key = "%s | %s" % (ph, site)
                _SITES[key] = _SITES.get(key, 0) + 1
                _lo, _hi = _SITE_VALS.get(key, (v, v))
                _SITE_VALS[key] = (min(_lo, v), max(_hi, v))
                if len(_RECORDS) < 20:
                    _RECORDS.append((v, key))
        orig(self, name, value)

    Player.__setattr__ = spy
    return orig


def main():
    global _SPY_BC, _SPY_OPE
    real = sys.stdout
    sys.stdout = open(os.devnull, "w", encoding="utf-8")

    install_spy()

    collision_system = CollisionSystem()
    field = Field(1920, 1080)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball

    open_play = OpenPlayEngine(world)
    world.open_play = open_play
    world.open_play_engine = open_play
    _SPY_OPE = open_play

    kickoff_engine = KickoffEngine(world, ball, field)

    random.seed(SEED)
    kickoff_engine.reset_for_kickoff()
    apply_receiving_kickoff_formation(world, field)
    apply_kicking_team_formation(world, field)
    world.ball_owner = "NONE"
    world.ball_carrier = None
    world.ball.possessed_by = None
    kickoff_engine.kickoff()

    release_frames = 0
    dt = 1.0 / 60.0
    for frame in range(1, FRAMES + 1):
        world.frame_count = frame
        ball.update(dt)
        if world.phase == GamePhase.KICKOFF:
            kickoff_engine.update(dt)
            check_for_catch(world, ball, dt)
        elif world.phase == GamePhase.CATCH:
            if world.ball_carrier is not None:
                world.open_play.start(world.ball_carrier)
            world.phase = GamePhase.OPEN_PLAY
        elif world.phase == GamePhase.OPEN_PLAY:
            # Spy on EVERY open-play frame and let the recorded phase decide.
            _SPY_BC = getattr(world, "ball_carrier", None)
            open_play.update(dt)
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)
        else:
            _SPY_BC = None
            continue
        if getattr(open_play, "_phase", None) == "RELEASE":
            release_frames += 1
        _SPY_BC = None

    sys.stdout = real
    print("=" * 96)
    print("TARGET_X WRITER PROBE — who writes an out-of-field carrier target?")
    print("=" * 96)
    print("  frames         = %d   seed = %d" % (FRAMES, SEED))
    print("  RELEASE frames = %d" % release_frames)
    print("  field bounds   = [%.3f, %.3f]" % (FIELD_MIN_X, FIELD_MAX_X))
    print("  out-of-field carrier-target writes (all phases, keyed by the "
          "phase at write time) = %d" % sum(_SITES.values()))
    if _RECORDS:
        vals = [v for v, _s in _RECORDS]
        print("  min / max value seen = %.3f / %.3f" % (min(vals), max(vals)))
    print("")
    print("-" * 96)
    print("CALL SITES (count  site)")
    print("-" * 96)
    if not _SITES:
        print("  (none — no out-of-field carrier target was written)")
    for site, n in sorted(_SITES.items(), key=lambda kv: -kv[1]):
        _lo, _hi = _SITE_VALS.get(site, (0.0, 0.0))
        print("  %6d  [%.3f..%.3f]  %s" % (n, _lo, _hi, site))
    print("=" * 96)
    return 0


if __name__ == "__main__":
    sys.exit(main())
