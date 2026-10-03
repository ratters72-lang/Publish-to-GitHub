"""Measure the DEF line geometry RELATIVE TO THE CARRIER during the TES drive.

Writes a UTF-8 report to tmp/drive_geom_<tag>.txt so the cp1252 console cannot
mangle it.  Per drive frame, for every non-participant line body:
    depth  = x - bc.x      (should settle near WIDTH_OFFSETS[depth_role])
    lateral= y - bc.y
plus the seeded structure cell (_tes_hold_x/_tes_hold_y) when present.
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"

import contextlib
import io
import sys

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

TAG = os.environ.get("RLM_GEOM_TAG", "out")
OUT = "tmp/drive_geom_" + TAG + ".txt"

DRIVE_PHASES = ("TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE")
LINE_ROLES = ("DEF_LINE_READY", "DEF_CHASE", "DEF_RETREAT_10")
MAX_FRAMES = 2500


def role_name(p):
    r = getattr(p, "defender_role", None)
    return getattr(r, "name", None) or (str(r) if r is not None else None)


def boot_world():
    cs = CollisionSystem()
    field = Field(1920, 1080)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball
    op = OpenPlayEngine(world)
    world.open_play = op
    world.open_play_engine = op
    ke = KickoffEngine(world, ball, field)
    ke.reset_for_kickoff()
    apply_receiving_kickoff_formation(world, field)
    apply_kicking_team_formation(world, field)
    world.ball_owner = "NONE"
    world.ball_carrier = None
    world.ball.possessed_by = None
    ke.kickoff()
    return world, ball, op, ke, cs, field


def main():
    lines = []
    cap = io.StringIO()
    with contextlib.redirect_stdout(cap):
        world, ball, op, ke, cs, field = boot_world()
        tuners = getattr(op, "def_tuners", None)
        offsets = getattr(tuners, "WIDTH_OFFSETS", None) or {}
        lines.append("WIDTH_OFFSETS: " + str(sorted(offsets.items())))
        dt = 1.0 / 60.0
        drive_frames = 0
        for _ in range(MAX_FRAMES):
            world.frame_count += 1
            ball.update(dt)
            if world.phase == GamePhase.KICKOFF:
                ke.update(dt)
                check_for_catch(world, ball, dt)
                continue
            if world.phase == GamePhase.CATCH:
                if world.ball_carrier is not None:
                    op.start(world.ball_carrier)
                world.phase = GamePhase.OPEN_PLAY
                continue
            if world.phase != GamePhase.OPEN_PLAY:
                continue

            op.update(dt)
            if not world.tes_active:
                cs.resolve_all(world.players, world=world)

            phase = op._phase
            if phase not in DRIVE_PHASES:
                continue

            bc = world.ball_carrier
            if bc is None:
                continue
            drive_frames += 1
            bcx, bcy = float(bc.x), float(bc.y)
            ruck_x = getattr(world, "ruck_x_m", None)
            entry_ruck = getattr(world, "_tes_drive_entry_ruck_x", None)
            fst = getattr(world, "frame_spacing_targets", None) or {}

            tes_ids = set()
            if op.primary is not None:
                tes_ids.add(op.primary.player_id)
            for j in op.joiners:
                tes_ids.add(j.player_id)
            for pid in getattr(op, "_attached_player_slots", {}):
                tes_ids.add(pid)

            # only report the first 4 and last 4 drive frames (keep it small)
            if drive_frames > 4 and drive_frames % 25 != 0:
                continue

            lines.append("")
            lines.append(
                "f=%d phase=%s bc=(%.2f,%.2f) ruck_x=%s entry_ruck=%s "
                "fst_n=%d" % (
                    world.frame_count, phase, bcx, bcy,
                    ("%.2f" % ruck_x) if ruck_x is not None else "None",
                    ("%.2f" % entry_ruck) if entry_ruck is not None else "None",
                    len(fst),
                )
            )
            for d in world.defending_team:
                if d is bc:
                    continue
                if getattr(d, "fullback_role_active", False):
                    continue
                if d.player_id in tes_ids:
                    continue
                if role_name(d) not in LINE_ROLES:
                    continue
                role = getattr(d, "depth_role", None) or "-"
                off = offsets.get(role, offsets.get("D", 5.0))
                hx = getattr(d, "_tes_hold_x", None)
                hy = getattr(d, "_tes_hold_y", None)
                lines.append(
                    "   id=%s role=%s dr=%s x=%.2f y=%.2f depth=%+.2f "
                    "lat=%+.2f tgt=(%.2f,%.2f) hold=(%s,%s) "
                    "fst=%s fsy=%s src=%s" % (
                        d.player_id, role_name(d), role,
                        float(d.x), float(d.y),
                        float(d.x) - bcx, float(d.y) - bcy,
                        float(getattr(d, "target_x", 0.0)),
                        float(getattr(d, "target_y", 0.0)),
                        ("%.2f" % hx) if hx is not None else "-",
                        ("%.2f" % hy) if hy is not None else "-",
                        ("%.2f" % fst[id(d)]) if id(d) in fst else "-",
                        ("%.2f" % d.frame_spacing_y)
                        if getattr(d, "frame_spacing_y", None) is not None else "-",
                        getattr(d, "target_source", "-"),
                    )
                )
        lines.append("")
        lines.append("DRIVE FRAMES OBSERVED: %d" % drive_frames)

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("wrote", OUT)
    pygame.quit()


if __name__ == "__main__":
    main()
