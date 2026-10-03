"""tmp/probe_dh_open_play_support.py - 180-frame DH trace after BROKEN_TACKLE.

For every BROKEN_TACKLE (a TES -> OPEN_PLAY transition) this records the DH's
target_source / target_x / target_y / off_x / off_y / movement_state / speed /
attack_role / defender_role for up to 180 frames, then reports the first frame,
the last frame, the target_source counts and the DH's total X travel.

Two arms in ONE process, toggling RLM_DH_OPEN_PLAY_SUPPORT (read at
consumption time): ON = dh_open_play_support live, OFF = pre-change engine.

Run:  python tmp/probe_dh_open_play_support.py
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import contextlib
import io
import math
import sys
from collections import Counter

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

MAX_FRAMES = 9000
WINDOW = 180
TES_PHASES = ("TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE",
              "TACKLE_DOMINANCE")


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


def snapshot(open_play, world):
    dh = open_play.dh
    bc = world.ball_carrier
    if dh is None:
        return None
    off_x = None
    off_y = None
    if getattr(dh, "target_x", None) is not None:
        off_x = float(dh.target_x) - float(dh.x)
    if getattr(dh, "target_y", None) is not None:
        off_y = float(dh.target_y) - float(dh.y)
    return {
        "frame": world.frame_count,
        "phase": open_play._phase,
        "dh_id": getattr(dh, "player_id", None),
        "src": getattr(dh, "target_source", None),
        "tx": getattr(dh, "target_x", None),
        "ty": getattr(dh, "target_y", None),
        "x": float(dh.x),
        "y": float(dh.y),
        "off_x": off_x,
        "off_y": off_y,
        "mstate": getattr(getattr(dh, "movement_state", None), "name", None),
        "speed": math.hypot(float(getattr(dh, "vx", 0.0)),
                            float(getattr(dh, "vy", 0.0))),
        "move_speed": float(getattr(dh, "move_speed", 0.0) or 0.0),
        "attack_role": getattr(dh, "attack_role", None),
        "def_role": getattr(getattr(dh, "defender_role", None), "name", None),
        "bc_x": float(bc.x) if bc is not None else None,
        "bc_y": float(bc.y) if bc is not None else None,
    }


def run_rollout(lever_on):
    os.environ["RLM_DH_OPEN_PLAY_SUPPORT"] = "1" if lever_on else "0"
    traces = []
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        world, ball, open_play, kickoff_engine, collision_system = boot_world()
        dt = 1.0 / 60.0
        prev_phase = None
        active = None
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

            phase = open_play._phase
            if prev_phase in TES_PHASES and phase == "OPEN_PLAY":
                snap = snapshot(open_play, world)
                if snap is not None and open_play.dh is not None:
                    active = {
                        "row0": snap,
                        "last": snap,
                        "n": 1,
                        "srcs": Counter([str(snap["src"])]),
                        "moved": 0.0,
                        "last_x": snap["x"],
                    }
                    traces.append(active)
            if active is not None and phase == "OPEN_PLAY":
                snap = snapshot(open_play, world)
                if snap is not None:
                    active["n"] += 1
                    active["srcs"][str(snap["src"])] += 1
                    active["moved"] += abs(snap["x"] - active["last_x"])
                    active["last_x"] = snap["x"]
                    active["last"] = snap
                    if active["n"] >= WINDOW:
                        active = None
            prev_phase = phase
    return traces


def fmt(v):
    if isinstance(v, float):
        return "%.2f" % v
    return str(v)


def main():
    print("=" * 104)
    print("DH OPEN_PLAY SUPPORT - 180-FRAME TRACE AFTER BROKEN_TACKLE")
    print("=" * 104)
    for label, lever_on in (("ON  (dh_open_play_support live)", True),
                            ("OFF (RLM_DH_OPEN_PLAY_SUPPORT=0)", False)):
        traces = run_rollout(lever_on)
        print("")
        print("#" * 104)
        print("ARM %s -- %d broken-tackle traces" % (label, len(traces)))
        print("#" * 104)
        if not traces:
            print("  (no BROKEN_TACKLE observed)")
            continue
        for i, t in enumerate(traces[:6]):
            r0 = t["row0"]
            last = t["last"]
            print("  trace %d: dh_id=%s frames=%d dh_x_travel=%.2fm" % (
                i, r0["dh_id"], t["n"], t["moved"]))
            print("    first frame %d: src=%s off_x=%s off_y=%s mstate=%s "
                  "speed=%s attack_role=%s def_role=%s" % (
                      r0["frame"], r0["src"], fmt(r0["off_x"]),
                      fmt(r0["off_y"]), r0["mstate"], fmt(r0["speed"]),
                      r0["attack_role"], r0["def_role"]))
            print("    last  frame %d: src=%s off_x=%s off_y=%s mstate=%s "
                  "speed=%s" % (
                      last["frame"], last["src"], fmt(last["off_x"]),
                      fmt(last["off_y"]), last["mstate"], fmt(last["speed"])))
            print("    target_source counts: %s" % dict(t["srcs"]))
        first_src = Counter(str(t["row0"]["src"]) for t in traces)
        all_src = Counter()
        for t in traces:
            all_src.update(t["srcs"])
        travel = [t["moved"] for t in traces]
        print("  ---- AGGREGATE ----")
        print("    traces                    : %d" % len(traces))
        print("    first-frame target_source : %s" % dict(first_src))
        print("    all-frame  target_source  : %s" % dict(all_src))
        print("    dh x-travel over windows  : min %.2f max %.2f mean %.2f m" % (
            min(travel), max(travel), sum(travel) / len(travel)))
    return 0


if __name__ == "__main__":
    code = 2
    try:
        code = main()
    finally:
        pygame.quit()
    sys.exit(code)
