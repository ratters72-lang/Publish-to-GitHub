"""tmp/diag_goal_line.py — parameterised goal-line width diagnostic.

Usage: python tmp/diag_goal_line.py <c10> <ctry> <bc_x>
Prints the published side widths, each line body's y / target_y / spacing
target / target_source / depth_role at the pinned bc.x, so we can see which
writer owns the OUTER (winger) bodies and whether their Y follows the
four-anchor compression.
"""
import os
import sys
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, ".")
import contextlib
import io
import pygame
pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)
import world.open_play_engine as ope_mod
ope_mod.DEBUG_SDEA_PHYSICS = False

from world.field import Field
from world.world_state import WorldState, GamePhase
from world.open_play_engine import OpenPlayEngine
from kick_off.kickoff_formations import (
    apply_receiving_kickoff_formation, apply_kicking_team_formation)
from game.kickoff_engine import KickoffEngine
from ball.ball import Ball
from game.physics.collision_system import CollisionSystem
from world.catch_system import check_for_catch
from game.defence.defence_lvl_1.defensive_width import governed_width_m
from game.defence.defence_lvl_1.anchor_authority import reset_anchor_authority
from game.defence.defence_lvl_1.formation_width_authority import (
    published_side_widths, published_formation_width)
from game.defence.defence_lvl_1.defensive_split_spacing import side_widths
from game.defence.defence_lvl_1 import population_authority as pa

C10 = float(sys.argv[1]) if len(sys.argv) > 1 else 0.95
CTRY = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
TARGET = float(sys.argv[3]) if len(sys.argv) > 3 else 100.0

DT = 1.0 / 60.0
collision = CollisionSystem()
field = Field(1920, 1080)
world = WorldState()
ball = Ball(field); world.ball = ball
ope = OpenPlayEngine(world)
world.open_play = ope; world.open_play_engine = ope
ke = KickoffEngine(world, ball, field)
ke.reset_for_kickoff()
apply_receiving_kickoff_formation(world, field)
apply_kicking_team_formation(world, field)
world.ball_owner = "NONE"; world.ball_carrier = None; world.ball.possessed_by = None
ke.kickoff()

ope.def_tuners.defending_10m_compression = C10
ope.def_tuners.defending_tryline_compression = CTRY


def step():
    world.frame_count += 1
    ball.update(DT)
    if world.phase == GamePhase.KICKOFF:
        ke.update(DT); check_for_catch(world, ball, DT); return
    if world.phase == GamePhase.CATCH:
        if world.ball_carrier is not None:
            ope.start(world.ball_carrier)
        world.phase = GamePhase.OPEN_PLAY; return
    if world.phase != GamePhase.OPEN_PLAY:
        return
    ope.update(DT)
    if not world.tes_active:
        collision.resolve_all(world.players, world=world)


def pin(x):
    bc = world.ball_carrier
    if bc is None:
        return
    bc.x = x; bc.vx = 0.0
    world.ruck_x_m = x
    world.ruck_y_m = float(getattr(bc, "y", 35.0))
    try:
        reset_anchor_authority(world)
    except Exception:
        pass


_captured = None
with contextlib.redirect_stdout(io.StringIO()):
    for _ in range(1200):
        step()
        if world.phase == GamePhase.OPEN_PLAY and world.ball_carrier is not None:
            break
    for _ in range(120):
        pin(TARGET); step()
    bc = world.ball_carrier
    line = list(pa.line_population(world.defending_team or []))
    fst = getattr(world, "frame_spacing_targets", None) or {}
    rows = []
    for d in sorted(world.defending_team or [], key=lambda z: -float(z.y)):
        rows.append((
            getattr(d, "player_id", "?"),
            str(getattr(d, "depth_role", "-")),
            float(d.y),
            getattr(d, "target_y", None),
            fst.get(id(d)),
            str(getattr(d, "target_source", "-")),
            d in line,
        ))
    _ys = [float(x.y) for x in line]
    sw = None
    try:
        sw = side_widths(world, ope.def_tuners)
    except Exception as exc:
        sw = "err:%s" % exc
    _captured = {
        "gov": governed_width_m(world, ope.def_tuners),
        "pub": dict(published_formation_width(world) or {}),
        "sw": sw,
        "ruck": (world.ruck_x_m, world.ruck_y_m, world.defensive_line_anchor),
        "sub": str(getattr(world.sub_state, "value", world.sub_state)),
        "span": (max(_ys) - min(_ys)) if len(_ys) >= 2 else 0.0,
        "n_line": len(line),
        "bc": (float(bc.x), float(bc.y)) if bc else None,
        "rows": rows,
    }

print("C10=%s CTRY=%s bc.x=%s" % (C10, CTRY, TARGET))
print("  governed=%.2f  span_y=%.2f  n_line=%d  ruck=(%.1f,%.1f) anchor=%.2f sub=%s"
      % (_captured["gov"], _captured["span"], _captured["n_line"],
         _captured["ruck"][0], _captured["ruck"][1], _captured["ruck"][2],
         _captured["sub"]))
_pub = _captured["pub"]
print("  published=%s" % {k: _pub.get(k) for k in ("left_m", "right_m", "frame")})
try:
    sw = _captured["sw"]
    if isinstance(sw, (list, tuple)):
        print("  side_widths=%s" % [round(float(v), 2) for v in sw])
    else:
        print("  side_widths=%s" % sw)
except Exception:
    pass
print("  %-5s %-9s %8s %9s %9s %-18s %s" % (
    "pid", "depth", "y", "target_y", "fst_y", "source", "in_line"))
for (pid, dep, y, ty, fty, src, inl) in _captured["rows"]:
    print("  %-5s %-9s %8.2f %9s %9s %-18s %s" % (
        pid, dep, y,
        ("%.2f" % ty) if ty is not None else "-",
        ("%.2f" % fty) if fty is not None else "-",
        src, inl))
