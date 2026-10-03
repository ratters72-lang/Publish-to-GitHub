"""Diag: why does a gated body hold a sub-tolerance-sized debt for ~35 s?

Dumps, per gated body-frame: x, the LIVE target, the STAMPED target, the
deficit each is measured from, the latch, the movement state, vx and the
body's own target_x — so the non-closing debt can be attributed.
"""

import contextlib
import io
import os
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

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
from game.defence.defence_lvl_1 import retreat_obligation as ro

DT = 1.0 / 60.0
MAX_FRAMES = 3600
MAX_ROWS = 80


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


def role_name(d):
    role = getattr(d, "defender_role", None)
    return getattr(role, "name", None) or str(role)


def main():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        world, ball, ope, kickoff_engine, collision_system = boot_world()
        rows = []
        per_body = {}
        latched_only = {}
        for _ in range(MAX_FRAMES):
            world.frame_count += 1
            ball.update(DT)
            if world.phase == GamePhase.KICKOFF:
                kickoff_engine.update(DT)
                check_for_catch(world, ball, DT)
                continue
            if world.phase == GamePhase.CATCH:
                if world.ball_carrier is not None:
                    ope.start(world.ball_carrier)
                world.phase = GamePhase.OPEN_PLAY
                continue
            if world.phase != GamePhase.OPEN_PLAY:
                continue
            ope.update(DT)
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)

            team = list(getattr(world, "defending_team", None) or ())
            for d in team:
                latched = bool(getattr(d, ro.OBLIGATION_ATTR, False))
                obligated = ro.is_retreat_obligated(d, world)
                if not latched and not obligated:
                    continue
                live = ro.obligation_target_x(d, world)
                stamped = d.__dict__.get(ro.OBLIGATION_TARGET_ATTR)
                x = float(d.x)
                deficit = ro.retreat_deficit_m(d, world)
                movement = getattr(d, "movement_state", None)
                rec = {
                    "frame": int(world.frame_count),
                    "pid": getattr(d, "player_id", "?"),
                    "role": role_name(d),
                    "drole": getattr(d, "depth_role", None),
                    "x": round(x, 3),
                    "live": None if live is None else round(float(live), 3),
                    "stamped": (None if stamped is None
                                else round(float(stamped), 3)),
                    "deficit": round(deficit, 3),
                    "latched": latched,
                    "gated": obligated,
                    "movement": getattr(movement, "name", movement),
                    "vx": round(float(getattr(d, "vx", 0.0) or 0.0), 3),
                    "target_x": getattr(d, "target_x", None),
                }
                if rec["target_x"] is not None:
                    rec["target_x"] = round(float(rec["target_x"]), 3)
                if len(rows) < MAX_ROWS:
                    rows.append(rec)
                agg = per_body.setdefault(rec["pid"], {
                    "n": 0, "gated_n": 0, "min_def": deficit,
                    "max_def": deficit, "min_x": x, "max_x": x,
                    "first": rec["frame"], "last": rec["frame"],
                    "role": rec["role"], "drole": rec["drole"],
                    "moves": set(),
                })
                agg["n"] += 1
                if obligated:
                    agg["gated_n"] += 1
                agg["min_def"] = min(agg["min_def"], deficit)
                agg["max_def"] = max(agg["max_def"], deficit)
                agg["min_x"] = min(agg["min_x"], x)
                agg["max_x"] = max(agg["max_x"], x)
                agg["last"] = rec["frame"]
                if rec["movement"]:
                    agg["moves"].add(rec["movement"])
                # latched-but-not-gated frames are the anomaly worth seeing
                if latched and not obligated and \
                        len(latched_only) < MAX_ROWS:
                    latched_only[(rec["pid"], rec["frame"])] = rec

    print("=" * 132)
    print("GATED / LATCHED BODY-FRAMES (first %d)" % MAX_ROWS)
    print("=" * 132)
    print("%6s %5s %-15s %7s %8s %8s %8s %8s %8s %-9s %7s %8s"
          % ("frame", "pid", "role", "drole", "x", "live", "stamped",
             "deficit", "latch", "movement", "vx", "tgt_x"))
    for r in rows:
        print("%6d %5s %-15s %7s %8.3f %8s %8s %8.3f %8s %-9s %7.3f %8s"
              % (r["frame"], r["pid"], r["role"], r["drole"], r["x"],
                 "-" if r["live"] is None else "%.3f" % r["live"],
                 "-" if r["stamped"] is None else "%.3f" % r["stamped"],
                 r["deficit"], r["latched"], r["movement"], r["vx"],
                 "-" if r["target_x"] is None else "%.3f" % r["target_x"]))
    print()
    print("=" * 132)
    print("PER-BODY SUMMARY")
    print("=" * 132)
    for pid, agg in sorted(per_body.items(),
                           key=lambda kv: kv[1]["n"], reverse=True):
        print("  id=%-5s latched_frames=%-5d gated_frames=%-5d "
              "x=[%.2f..%.2f] deficit=[%.3f..%.3f] role=%s drole=%s"
              % (pid, agg["n"], agg["gated_n"], agg["min_x"], agg["max_x"],
                 agg["min_def"], agg["max_def"], agg["role"], agg["drole"]))
        print("              movements=%s window=[%d..%d]"
              % (sorted(agg["moves"]), agg["first"], agg["last"]))
    print()
    print("  distinct bodies: %d" % len(per_body))
    print()
    print("=" * 132)
    print("LATCHED BUT NOT GATED (the anomaly: debt re-opened next frame?)  "
          "count=%d" % len(latched_only))
    print("=" * 132)
    shown = 0
    for key in sorted(latched_only):
        r = latched_only[key]
        print("  frame=%d pid=%s x=%.3f live=%s stamped=%s deficit=%.3f "
              "movement=%s vx=%.3f tgt_x=%s"
              % (r["frame"], r["pid"], r["x"], r["live"], r["stamped"],
                 r["deficit"], r["movement"], r["vx"], r["target_x"]))
        shown += 1
        if shown >= 25:
            break


if __name__ == "__main__":
    try:
        main()
    finally:
        pygame.quit()
