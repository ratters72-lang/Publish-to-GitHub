"""tmp/probe_release_gate.py - per-frame trace of the RELEASE -> PTB_PREP gate.

Read-only instrumentation.  Boots the same harness as
probe_tackle_state_release.py, drives the match until the OPE enters RELEASE,
then re-derives every condition of _check_dh_ready_for_ptb() plus the time
gate, recording exactly which condition blocks each frame.

Run:  python tmp/probe_release_gate.py
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
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
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
from player.player import DefenderRole

MAX_FRAMES = 6000
OUT_LOG = "tmp/_release_gate_trace.txt"

BLOCKING_ROLES = (
    DefenderRole.TACKLER_1,
    DefenderRole.TACKLER_2,
    DefenderRole.TACKLER_3,
    DefenderRole.MARKER_A,
    DefenderRole.MARKER_B,
)


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


def evaluate_gate(ope, world):
    """Mirror _check_dh_ready_for_ptb(for_exec=False) condition by condition."""
    dh = ope.dh
    bc = world.ball_carrier
    fails = []
    if dh is None:
        return ["0_dh_none"], {}
    if bc is None:
        return ["0_bc_none"], {}
    ruck_y = getattr(world, "ruck_y_m", bc.y)
    info = {
        "dh_x": dh.x,
        "dh_y": dh.y,
        "dh_vx": dh.vx,
        "dh_vy": dh.vy,
        "bc_x": bc.x,
        "bc_y": bc.y,
        "bc_vx": bc.vx,
        "ruck_y": ruck_y,
        "dh_frozen": bool(dh.frozen),
        "dh_role": getattr(getattr(dh, "defender_role", None), "name", None),
        "dh_fb_active": bool(getattr(dh, "fullback_role_active", False)),
        "dh_move_speed": float(getattr(dh, "move_speed", 0.0) or 0.0),
        "dh_target_x": getattr(dh, "target_x", None),
        "dh_target_y": getattr(dh, "target_y", None),
    }
    # 3. DH must be behind BC
    if dh.x >= bc.x + 0.3:
        fails.append("3_ahead_of_bc")
    # 4. DH within relaxed position tolerance of the 1m-behind target
    target_x = bc.x - ope_mod.DH_BEHIND_DISTANCE_M
    pos_tol = ope_mod.DH_POSITION_TOLERANCE_M + 0.1
    info["pos_err"] = abs(dh.x - target_x)
    info["pos_tol"] = pos_tol
    if info["pos_err"] > pos_tol:
        fails.append("4_pos_err")
    # 5. DH lateral offset from ruck_y
    lat_tol = ope_mod.DH_LATERAL_TOLERANCE_M + 0.3
    if ruck_y is not None:
        info["lat_err"] = abs(dh.y - ruck_y)
        info["lat_tol"] = lat_tol
        if info["lat_err"] > lat_tol:
            fails.append("5_lateral")
    # 6. DH speed below stability threshold
    info["dh_speed"] = math.hypot(dh.vx, dh.vy)
    info["vel_tol"] = ope_mod.DH_VELOCITY_STABLE_THRESHOLD_MPS
    if info["dh_speed"] > info["vel_tol"]:
        fails.append("6_velocity")
    # 7. DH must not be frozen
    if dh.frozen:
        fails.append("7_frozen")
    # 8. DH must not hold a tackler/marker role
    if dh.defender_role in BLOCKING_ROLES:
        fails.append("8_role")
    return fails, info


def main():
    rows = []
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        world, ball, open_play, kickoff_engine, collision_system = boot_world()
        dt = 1.0 / 60.0
        in_release = False
        release_start_frame = None
        prev_phase = None
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
            if phase == "RELEASE":
                if not in_release:
                    in_release = True
                    release_start_frame = int(world.frame_count)
                fails, info = evaluate_gate(open_play, world)
                row = {
                    "frame": int(world.frame_count),
                    "t": float(open_play._phase_timer),
                    "fails": fails,
                    "outcome_ptb": (
                        float(open_play.tes_outcome.ptb_time_s)
                        if open_play.tes_outcome else None),
                    "ptb_t": max(1.54, float(open_play.tes_outcome.ptb_time_s)
                                 if open_play.tes_outcome else 1.54),
                }
                row.update(info)
                rows.append(row)
            elif in_release and phase != "RELEASE":
                if prev_phase == "RELEASE":
                    break
            prev_phase = phase

    with open(OUT_LOG, "w", encoding="utf-8") as fh:
        fh.write(buf.getvalue())

    print("=" * 78)
    print("RELEASE -> PTB_PREP GATE TRACE")
    print("=" * 78)
    if not rows:
        print("RESULT: never observed a RELEASE frame in %d frames." % MAX_FRAMES)
        return 2

    print("release frames observed      : %d" % len(rows))
    print("phase_timer span             : %.2fs .. %.2fs" % (
        rows[0]["t"], rows[-1]["t"]))
    print("tes_outcome.ptb_time_s       : %s" % (rows[0]["outcome_ptb"],))
    print("time-gate ptb_t              : %.2f" % rows[0]["ptb_t"])
    print("final phase after RELEASE    : %s" % rows[-1].get("fails"))
    print()

    # Which condition blocks, and for how long?
    blocking = Counter()
    blocked_frames = 0
    time_gate_blocked = 0
    for r in rows:
        if r["t"] < r["ptb_t"]:
            time_gate_blocked += 1
        if r["fails"]:
            blocked_frames += 1
            for f in r["fails"]:
                blocking[f] += 1
    print("-" * 78)
    print("BLOCKING CONDITION FREQUENCY (frames where each test failed)")
    print("-" * 78)
    print("  frames gated by TIME (t < ptb_t)      : %d" % time_gate_blocked)
    print("  frames where readiness gate failed    : %d" % blocked_frames)
    for name, n in blocking.most_common():
        print("    %-18s %6d" % (name, n))
    print()

    # Dominant-condition timeline: coalesce consecutive identical fail sets.
    print("-" * 78)
    print("BLOCKING TIMELINE (coalesced runs of identical failing condition sets)")
    print("-" * 78)
    runs = []
    for r in rows:
        key = tuple(r["fails"])
        if runs and runs[-1]["key"] == key:
            runs[-1]["end_t"] = r["t"]
            runs[-1]["n"] += 1
        else:
            runs.append({"key": key, "start_t": r["t"], "end_t": r["t"], "n": 1})
    for run in runs:
        label = ",".join(run["key"]) if run["key"] else "PASS -> transition"
        print("  t=%6.2fs .. %6.2fs  n=%-5d  %s" % (
            run["start_t"], run["end_t"], run["n"], label))
    print()

    # Detail for the final 12 frames before resolution.
    print("-" * 78)
    print("LAST 12 RELEASE FRAMES")
    print("-" * 78)
    hdr = ("t      dh_x    dh_y   dh_vx   dh_vy   spd   poserr bc_x    "
           "target  role       frozen fb")
    print(hdr)
    for r in rows[-12:]:
        print("%6.2f %7.2f %6.2f %7.2f %7.2f %5.2f %6.2f %7.2f %7.2f  %-9s %-5s %s" % (
            r["t"], r["dh_x"], r["dh_y"], r["dh_vx"], r["dh_vy"],
            r["dh_speed"], r.get("pos_err", -1.0), r["bc_x"],
            (r["bc_x"] - ope_mod.DH_BEHIND_DISTANCE_M),
            str(r.get("dh_role")), str(r.get("dh_frozen")),
            str(r.get("dh_fb_active"))))
    print()

    # DH target vs the gate target: are they the same anchor?
    last = rows[-1]
    print("-" * 78)
    print("DH WRITER TARGET vs GATE TARGET (final frame)")
    print("-" * 78)
    print("  dh.target_x                : %.3f" % (last.get("dh_target_x") or -1.0))
    print("  bc.x - DH_BEHIND_DISTANCE_M: %.3f" % (
        last["bc_x"] - ope_mod.DH_BEHIND_DISTANCE_M))
    print("  dh.target_y                : %s" % (last.get("dh_target_y"),))
    print("  world.ruck_y_m             : %s" % (last.get("ruck_y"),))
    print("  dh.move_speed              : %.2f" % last.get("dh_move_speed", -1.0))
    print()
    print("engine log written to %s" % OUT_LOG)
    return 0


if __name__ == "__main__":
    code = 2
    try:
        code = main()
    finally:
        pygame.quit()
    sys.exit(code)
