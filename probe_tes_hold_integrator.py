"""probe_tes_hold_integrator.py — WHY does a held out-of-lane body close so
slowly?

Prints, for the worst-held out-of-lane body of each drive chamber, the full
integrator state each drive frame:

    y/x, target_y/target_x, the hold cell, vx/vy, the smooth-damp velocity
    state (_sd_vx/_sd_vy), move_speed, movement_state, arrival_mode,
    max_defensive_speed_mps, defended_cap_remaining_s,
    defence_reaction_remaining_s and the MMDL skip stamp.

One run pins the authority that throttles the lane correction (smooth-damp
ramp vs defensive governor vs an MMDL skip), so the lane-true verifier's
clause B can grade the REAL convergence law instead of a guessed one.
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import contextlib
import io
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

import pygame
pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

import world.open_play_engine as ope_mod
ope_mod.DEBUG_SDEA_PHYSICS = False

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
from game.defence.defence_lvl_1 import tes_drive_structure as tds

DRIVE_PHASES = ("TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE")
LINE_ROLES = ("DEF_LINE_READY", "DEF_CHASE", "DEF_RETREAT_10")
MAX_FRAMES = 3000
MAX_ROWS = 60


def _role_name(p):
    role = getattr(p, "defender_role", None)
    return getattr(role, "name", None) or (str(role) if role is not None else None)


def _mstate(p):
    ms = getattr(p, "movement_state", None)
    return getattr(ms, "name", None) or (str(ms) if ms is not None else None)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def boot_world():
    collision_system = CollisionSystem()
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
    world.ball_owner = "NONE"
    world.ball_carrier = None
    world.ball.possessed_by = None
    kickoff_engine.kickoff()
    return world, ball, open_play, kickoff_engine, collision_system


def main():
    buf = io.StringIO()
    rows = []
    with contextlib.redirect_stdout(buf):
        world, ball, open_play, kickoff_engine, collision_system = boot_world()
        dt = 1.0 / 60.0
        prev_cid = None
        tracked = None
        for _ in range(MAX_FRAMES):
            world.frame_count += 1
            ball.update(dt)
            if world.phase == GamePhase.KICKOFF:
                kickoff_engine.update(dt)
                check_for_catch(world, ball, dt)
                continue
            if world.phase == GamePhase.CATCH:
                if world.ball_carrier is not None:
                    open_play.start(world.ball_carrier)
                world.phase = GamePhase.OPEN_PLAY
                continue
            if world.phase != GamePhase.OPEN_PLAY:
                continue
            open_play.update(dt)
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)
            if open_play._phase not in DRIVE_PHASES:
                continue
            cid = getattr(world, tds.CHAMBER_ID_ATTR, None)
            if cid != prev_cid:
                prev_cid = cid
                tracked = None
                # Pick the body whose held lane is farthest OUTSIDE its
                # effective cell at chamber entry (the strongest
                # out-of-lane case).
                worst = None
                for d in (getattr(world, "defending_team", None) or []):
                    if _role_name(d) not in LINE_ROLES:
                        continue
                    if getattr(d, "fullback_role_active", False):
                        continue
                    if not bool(getattr(d, tds.HOLD_ACTIVE_ATTR, False)):
                        continue
                    err = abs(_f(getattr(d, "y", 0.0))
                              - _f(getattr(d, tds.HOLD_Y_ATTR, 0.0)))
                    if err > 0.5 and (worst is None or err > worst[1]):
                        worst = (d.player_id, err)
                if worst is not None:
                    tracked = worst[0]
                    rows.append(
                        f"CHAMBER cid={cid} frame={world.frame_count} "
                        f"tracked pid={tracked} entry_err={worst[1]:.2f}"
                    )
            if tracked is None:
                continue
            if len(rows) >= MAX_ROWS + 12:
                continue
            d = None
            for p in (getattr(world, "defending_team", None) or []):
                if getattr(p, "player_id", None) == tracked:
                    d = p
                    break
            if d is None or not bool(getattr(d, tds.HOLD_ACTIVE_ATTR, False)):
                continue
            rows.append(
                "  f={f} {sub:<17} pid={pid} y={y:7.2f} ty={ty:7.2f} "
                "hy={hy:7.2f} | x={x:7.2f} tx={tx:7.2f} hx={hx:7.2f} | "
                "vx={vx:6.2f} vy={vy:6.2f} sdvx={sx:6.2f} sdvy={sy:6.2f} | "
                "spd={spd:5.2f} {ms:<6} arr={arr} mmdl={mmdl} | "
                "maxdef={md:5.2f} dcap={dc:5.2f} rcap={rc:5.2f} "
                "du={du} src={src}".format(
                    f=world.frame_count,
                    sub=str(getattr(world, "sub_state", "?")).split(".")[-1][:17],
                    pid=tracked,
                    y=_f(getattr(d, "y", 0.0)),
                    ty=_f(getattr(d, "target_y", 0.0)),
                    hy=_f(getattr(d, tds.HOLD_Y_ATTR, 0.0)),
                    x=_f(getattr(d, "x", 0.0)),
                    tx=_f(getattr(d, "target_x", 0.0)),
                    hx=_f(getattr(d, tds.HOLD_X_ATTR, 0.0)),
                    vx=_f(getattr(d, "vx", 0.0)),
                    vy=_f(getattr(d, "vy", 0.0)),
                    sx=_f(getattr(d, "_sd_vx", 0.0)),
                    sy=_f(getattr(d, "_sd_vy", 0.0)),
                    spd=_f(getattr(d, "move_speed", 0.0)),
                    ms=str(_mstate(d))[:6],
                    arr=bool(getattr(d, "arrival_mode", False)),
                    mmdl=bool(getattr(d, "_mmdl_moved", False)),
                    md=_f(getattr(d, "max_defensive_speed_mps", 0.0)),
                    dc=_f(getattr(d, "defended_cap_remaining_s", 0.0)),
                    rc=_f(getattr(d, "defence_reaction_remaining_s", 0.0)),
                    du=bool(getattr(d, "depth_locked", False)),
                    src=str(getattr(d, "target_source", "-"))[:16],
                )
            )

    for line in rows:
        print(line)


if __name__ == "__main__":
    try:
        main()
    finally:
        pygame.quit()
