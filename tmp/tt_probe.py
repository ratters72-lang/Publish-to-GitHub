"""tmp/tt_probe.py -- who moves the fullback after RELEASE?

Minimal, read-only. Boots the shipped engine (dummy SDL, seed 20260827,
dt=1/60), wraps every writer-looking method on the live OpenPlayEngine plus
Player.update and CollisionSystem.resolve_all, and records EVERY change to
the watched fullback -- including writers that run AFTER Player.update in
the same frame (the blind spot of the innermost-only tracer).

Prints, for the first tackle in which the FB is TES-attached during the
drive, the ordered writer chain for every RELEASE/PTB frame.

Writes tmp/tt_probe.txt.
"""

import io
import math
import os
import re
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
sys.path.insert(0, ".")

_REAL = sys.stdout


def _quiet():
    sys.stdout = io.TextIOWrapper(
        open(os.devnull, "wb"), encoding="utf-8", errors="replace"
    )


def _loud():
    sys.stdout = _REAL


_quiet()
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
from player.player import Player

_loud()

SEED = int(os.environ.get("RLM_TRACE_SEED", "20260827"))
MAX_FRAMES = int(os.environ.get("RLM_PROBE_FRAMES", "1400"))
DT = 1.0 / 60.0

_WATCH = ("x", "y", "vx", "vy", "target_x", "target_y",
          "movement_state", "tes_attached", "is_tackler",
          "_tackle_commit_active")
_WRAP_RE = re.compile(
    r"^_(update|prepare|apply|clear|detach|start|handle|resolve|integrate|"
    r"glide|pin|move|arm|assign|stamp|drive)"
)

FB = None
EVENTS = []
LAST = []


def _num(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def snap():
    if FB is None:
        return None
    return tuple(getattr(FB, a, None) for a in _WATCH)


def diff(b, a):
    if b is None or a is None:
        return ()
    return tuple(_WATCH[i] for i in range(len(_WATCH)) if b[i] != a[i])


def wrap(owner, name):
    orig = getattr(owner, name, None)
    if orig is None or not callable(orig):
        return

    def wrapper(*args, **kwargs):
        b = snap()
        r = orig(*args, **kwargs)
        a = snap()
        if b != a:
            f = diff(b, a)
            EVENTS.append((name, f))
            LAST.append(name)
        return r

    try:
        setattr(owner, name, wrapper)
    except (AttributeError, TypeError):
        pass


def wrap_player_update():
    orig = Player.update

    def wrapper(self, *a, **k):
        if self is not FB:
            return orig(self, *a, **k)
        b = snap()
        r = orig(self, *a, **k)
        after = snap()
        if b != after:
            f = diff(b, after)
            EVENTS.append(("Player.update", f))
            LAST.append("Player.update")
        return r

    Player.update = wrapper


def main():
    global FB

    _quiet()
    import random

    random.seed(SEED)
    collision = CollisionSystem()
    field = Field(1920, 1080)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball
    open_play = OpenPlayEngine(world, seed=SEED)
    world.open_play = open_play
    world.open_play_engine = open_play
    kickoff = KickoffEngine(world, ball, field)

    names = set()
    for cls in type(open_play).__mro__:
        for attr, val in vars(cls).items():
            if _WRAP_RE.match(attr) and callable(val):
                names.add(attr)
    for n in sorted(names):
        wrap(open_play, n)
    wrap(collision, "resolve_all")
    wrap_player_update()

    kickoff.reset_for_kickoff()
    apply_receiving_kickoff_formation(world, field)
    apply_kicking_team_formation(world, field)
    world.ball_owner = "NONE"
    world.ball_carrier = None
    world.ball.possessed_by = None
    kickoff.kickoff()

    frames = []          # (frame, phase, fb_pid, attached, chain, vals)
    for frame in range(1, MAX_FRAMES + 1):
        world.frame_count = frame
        EVENTS.clear()
        LAST.clear()
        ball.update(DT)
        if world.phase == GamePhase.KICKOFF:
            kickoff.update(DT)
            check_for_catch(world, ball, DT)
        elif world.phase == GamePhase.CATCH:
            if world.ball_carrier is not None:
                world.open_play.start(world.ball_carrier)
            world.phase = GamePhase.OPEN_PLAY
        elif world.phase == GamePhase.OPEN_PLAY:
            open_play.update(DT)
            if not world.tes_active:
                collision.resolve_all(world.players, world=world)
        else:
            continue

        if FB is None:
            for p in getattr(world, "defending_team", []):
                if getattr(p, "fullback_role_active", False):
                    FB = p
                    break
        if FB is None:
            continue
        phase = getattr(open_play, "_phase", None)
        frames.append((
            frame, phase, getattr(FB, "player_id", None),
            bool(getattr(FB, "tes_attached", False)),
            list(EVENTS), snap(),
        ))

    _loud()

    # Find the first frame where the FB is attached during a tackle phase.
    start = None
    for i, (f, ph, pid, att, ch, v) in enumerate(frames):
        if att and ph in ("TACKLE_CONTACT", "TACKLE_DRIVE"):
            start = i
            break

    out = []
    out.append("wrapped: %d methods" % len(names))
    out.append("FB pid: %s" % getattr(FB, "player_id", None))
    if start is None:
        out.append("no attached tackle frame found")
    else:
        out.append("attached from frame %s (idx %d)" % (frames[start][0], start))
        end = min(len(frames), start + 200)
        for (f, ph, pid, att, ch, v) in frames[start:end]:
            chain = "; ".join("%s[%s]" % (n, ",".join(fs)) for n, fs in ch)
            x = _num(v[0]) if v else float("nan")
            tx = _num(v[4]) if v else float("nan")
            out.append(
                "f=%-5s %-16s x=%8.3f tx=%8.3f vx=%7.3f att=%d st=%s | %s"
                % (f, ph, x, tx, _num(v[3]) if v else 0.0, int(att),
                   getattr(v[6], "name", "-") if v else "-", chain)
            )

    text = "\n".join(out) + "\n"
    with open("tmp/tt_probe.txt", "w", encoding="utf-8") as fh:
        fh.write(text)
    _REAL.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
