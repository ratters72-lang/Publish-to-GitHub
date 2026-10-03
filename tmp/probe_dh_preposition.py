"""tmp/probe_dh_preposition.py - does the DH close the gap during the tackle?

For every TES frame (TACKLE_INIT/CONTACT/DRIVE/DOMINANCE) and the first
RELEASE frame this records the DH's id/x/y/speed/frozen/move_speed/
target_x/target_source and its gap to (bc.x - 1.0, bc.y), then prints a
per-rollout summary.  That isolates whether a lingering ~25 m approach is
caused by a missing DH assignment, a frozen body, or a body never driven.

Run:  python tmp/probe_dh_preposition.py
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

MAX_FRAMES = 3000
ROLLOUTS = 5
TES_PHASES = ("TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE",
              "TACKLE_DOMINANCE")
PTB_PHASES = ("PTB_PREP", "PTB_EXEC", "PTB")


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
    row = {
        "dh_id": getattr(dh, "player_id", None),
        "dh_x": dh.x if dh is not None else None,
        "spd": math.hypot(dh.vx, dh.vy) if dh is not None else None,
        "frozen": bool(getattr(dh, "frozen", False)) if dh is not None else None,
        "move_speed": (float(getattr(dh, "move_speed", 0.0) or 0.0)
                       if dh is not None else None),
        "target_x": getattr(dh, "target_x", None) if dh is not None else None,
        "src": getattr(dh, "target_source", None) if dh is not None else None,
        "game_role": getattr(getattr(dh, "defender_role", None), "name", None),
        "gap": None,
    }
    if dh is not None and bc is not None:
        row["gap"] = math.hypot(
            (bc.x - ope_mod.DH_BEHIND_DISTANCE_M) - dh.x,
            bc.y - dh.y,
        )
    return row


def run_rollout():
    tes_rows = []
    transitions = []
    release_first = None
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        world, ball, open_play, kickoff_engine, collision_system = boot_world()
        dt = 1.0 / 60.0
        prev_phase = None
        prev_x = None
        seen_release = False
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
            if phase != prev_phase:
                transitions.append("%d:%s" % (int(world.frame_count), phase))
                prev_phase = phase

            if phase in TES_PHASES or phase == "RELEASE":
                row = snapshot(open_play, world)
                row["phase"] = phase
                row["t"] = float(open_play._phase_timer)
                row["dx"] = (row["dh_x"] - prev_x
                             if (row["dh_x"] is not None
                                 and prev_x is not None) else None)
                if phase in TES_PHASES:
                    tes_rows.append(row)
                elif release_first is None:
                    release_first = row
                prev_x = row["dh_x"]

            if phase == "RELEASE":
                seen_release = True
            if seen_release and phase in PTB_PHASES:
                break
    return tes_rows, release_first, transitions


def summarise(idx, tes_rows, release_first, transitions):
    out = {"idx": idx, "ok": bool(tes_rows)}
    if not tes_rows:
        return out
    first = tes_rows[0]
    out["dh_id"] = first["dh_id"]
    out["game_role"] = first["game_role"]
    out["tes_frames"] = len(tes_rows)
    out["gap_first"] = first["gap"]
    out["x_first"] = first["dh_x"]
    out["gap_release"] = release_first["gap"] if release_first else None
    out["x_release"] = release_first["dh_x"] if release_first else None
    out["tes_moved_frames"] = sum(
        1 for r in tes_rows if r["dx"] is not None and abs(r["dx"]) > 1e-4)
    out["tes_frozen_frames"] = sum(1 for r in tes_rows if r["frozen"])
    out["tes_null_dh_frames"] = sum(
        1 for r in tes_rows if r["dh_id"] is None)
    out["tes_speeds"] = sorted(
        set(round(r["move_speed"] or 0.0, 2) for r in tes_rows))
    out["tes_sources"] = dict(
        Counter(str(r["src"]) for r in tes_rows).most_common(4))
    out["tes_travel"] = abs(
        (release_first["dh_x"] if release_first else first["dh_x"])
        - first["dh_x"])
    entries = {}
    for r in tes_rows:
        entries.setdefault(r["phase"], r)
    out["gap_by_phase"] = {p: entries[p]["gap"] for p in entries}
    out["transitions"] = transitions[:16]
    return out


def fmt(value):
    if isinstance(value, (int, float)):
        return "%.2f" % value
    return "-"


def main():
    results = [summarise(i, *run_rollout()) for i in range(ROLLOUTS)]

    print("=" * 100)
    print("DH PRE-POSITION TRACE  (%d rollouts)" % ROLLOUTS)
    print("=" * 100)
    print()
    print("#  dh_id role        TESfrm moved frozen nulldh x_first x_release "
          "gap@INIT gap@RELEASE TES_travel")
    print("-" * 100)
    for r in results:
        if not r["ok"]:
            print("%-2d  (no TES frames observed)" % r["idx"])
            continue
        print("%-2d %5s %-11s %6d %5d %6d %6d %7s %9s %8s %11s %10s" % (
            r["idx"], r["dh_id"], str(r["game_role"]), r["tes_frames"],
            r["tes_moved_frames"], r["tes_frozen_frames"],
            r["tes_null_dh_frames"], fmt(r["x_first"]), fmt(r["x_release"]),
            fmt(r["gap_first"]), fmt(r["gap_release"]), fmt(r["tes_travel"])))
    print()
    for r in results:
        if not r["ok"]:
            continue
        print("rollout %d" % r["idx"])
        print("   gap at TES phase entry : %s" % {
            k: (round(v, 2) if isinstance(v, (int, float)) else None)
            for k, v in r["gap_by_phase"].items()})
        print("   move_speed values seen : %s" % (r["tes_speeds"],))
        print("   target_source counts   : %s" % (r["tes_sources"],))
        print("   transitions            : %s" % (r["transitions"],))
        print()
    return 0


if __name__ == "__main__":
    code = 2
    try:
        code = main()
    finally:
        pygame.quit()
    sys.exit(code)
