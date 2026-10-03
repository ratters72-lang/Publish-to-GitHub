"""tmp/probe_release_gate_sweep.py - multi-rollout decomposition of the
RELEASE -> PTB_PREP stall.

For each rollout it records:
  * ptb_t                  - the time gate threshold (max(1.54, ptb_time_s))
  * t_time_ok              - first frame the time gate passes
  * t_pos_ok               - first frame conditions 3/4/5/7/8 ALL pass
                             (DH positionally ready; only velocity can block)
  * t_vel_ok               - first frame condition 6 passes
  * t_exit                 - the frame RELEASE actually ended
  * last_fails             - the failing set on the final RELEASE frame

The decisive question: which condition is the LAST one to clear?

Run:  python tmp/probe_release_gate_sweep.py
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

MAX_FRAMES = 2400
ROLLOUTS = 4

BLOCKING_ROLES = (
    DefenderRole.TACKLER_1,
    DefenderRole.TACKLER_2,
    DefenderRole.TACKLER_3,
    DefenderRole.MARKER_A,
    DefenderRole.MARKER_B,
)

# Condition groups.
POS_CONDITIONS = ("3_ahead_of_bc", "4_pos_err", "5_lateral")
VEL_CONDITION = "6_velocity"


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


def failing_conditions(ope, world):
    """Mirror _check_dh_ready_for_ptb(for_exec=False)."""
    dh = ope.dh
    bc = world.ball_carrier
    fails = []
    if dh is None:
        return ["0_dh_none"], None, None
    if bc is None:
        return ["0_bc_none"], dh, None
    ruck_y = getattr(world, "ruck_y_m", bc.y)
    if dh.x >= bc.x + 0.3:
        fails.append("3_ahead_of_bc")
    target_x = bc.x - ope_mod.DH_BEHIND_DISTANCE_M
    if abs(dh.x - target_x) > ope_mod.DH_POSITION_TOLERANCE_M + 0.1:
        fails.append("4_pos_err")
    if ruck_y is not None and abs(dh.y - ruck_y) > ope_mod.DH_LATERAL_TOLERANCE_M + 0.3:
        fails.append("5_lateral")
    if math.hypot(dh.vx, dh.vy) > ope_mod.DH_VELOCITY_STABLE_THRESHOLD_MPS:
        fails.append(VEL_CONDITION)
    if dh.frozen:
        fails.append("7_frozen")
    if dh.defender_role in BLOCKING_ROLES:
        fails.append("8_role")
    return fails, dh, bc


def run_rollout():
    rows = []
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        world, ball, open_play, kickoff_engine, collision_system = boot_world()
        dt = 1.0 / 60.0
        entered = False
        prev = None
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
                entered = True
                fails, dh, bc = failing_conditions(open_play, world)
                rows.append({
                    "t": float(open_play._phase_timer),
                    "fails": fails,
                    "dh_x": dh.x if dh is not None else None,
                    "dh_y": dh.y if dh is not None else None,
                    "dh_speed": (math.hypot(dh.vx, dh.vy)
                                 if dh is not None else None),
                    "bc_x": bc.x if bc is not None else None,
                    "ptb_time_s": (float(open_play.tes_outcome.ptb_time_s)
                                   if open_play.tes_outcome else None),
                    "role": (getattr(getattr(dh, "defender_role", None),
                                     "name", None) if dh is not None else None),
                })
            elif entered and prev == "RELEASE":
                break
            prev = phase
    return rows


def summarise(rows, idx):
    if not rows:
        return {"idx": idx, "ok": False}
    ptb_time = rows[0]["ptb_time_s"]
    ptb_t = max(1.54, ptb_time if ptb_time is not None else 1.54)
    t_time_ok = next((r["t"] for r in rows if r["t"] >= ptb_t), rows[-1]["t"])
    t_pos_ok = next(
        (r["t"] for r in rows
         if not any(f in r["fails"] for f in POS_CONDITIONS)
         and "7_frozen" not in r["fails"] and "8_role" not in r["fails"]),
        None)
    t_vel_ok = next(
        (r["t"] for r in rows if VEL_CONDITION not in r["fails"]), None)
    return {
        "idx": idx,
        "ok": True,
        "ptb_time": ptb_time,
        "ptb_t": ptb_t,
        "release_frames": len(rows),
        "t_exit": rows[-1]["t"],
        "t_time_ok": t_time_ok,
        "t_pos_ok": t_pos_ok,
        "t_vel_ok": t_vel_ok,
        "last_fails": list(rows[-1]["fails"]),
        "dh_start_x": rows[0]["dh_x"],
        "dh_end_x": rows[-1]["dh_x"],
        "dh_end_speed": rows[-1]["dh_speed"],
        "role": rows[0]["role"],
        "vel_fail_frames": sum(1 for r in rows if VEL_CONDITION in r["fails"]),
        "pos_fail_frames": sum(
            1 for r in rows if any(f in r["fails"] for f in POS_CONDITIONS)),
    }


def main():
    results = []
    for i in range(ROLLOUTS):
        results.append(summarise(run_rollout(), i))

    print("=" * 96)
    print("RELEASE -> PTB_PREP STALL DECOMPOSITION  (%d rollouts)" % ROLLOUTS)
    print("=" * 96)
    print()
    print("per-rollout")
    print("-" * 96)
    hdr = ("#  ptb_time  ptb_t  exit_t  t_pos_ok  t_vel_ok  "
           "vel_fail  pos_fail  dh_travel  last blocking set")
    print(hdr)
    for r in results:
        if not r["ok"]:
            print("%-2d  (no RELEASE phase observed)" % r["idx"])
            continue
        travel = (abs(r["dh_end_x"] - r["dh_start_x"])
                  if r["dh_start_x"] is not None else 0.0)
        print("%-2d %8.2f %6.2f %7.2f %9s %9s %9d %9d %10.2f  %s" % (
            r["idx"], r["ptb_time"], r["ptb_t"], r["t_exit"],
            ("%.2f" % r["t_pos_ok"]) if r["t_pos_ok"] is not None else "-",
            ("%.2f" % r["t_vel_ok"]) if r["t_vel_ok"] is not None else "-",
            r["vel_fail_frames"], r["pos_fail_frames"], travel,
            ",".join(r["last_fails"]) or "PASS"))
    print()

    ok = [r for r in results if r["ok"]]
    if not ok:
        print("no rollouts completed RELEASE")
        return 2

    print("-" * 96)
    print("AGGREGATE")
    print("-" * 96)
    n = len(ok)
    print("  rollouts analysed                        : %d" % n)
    print("  ptb_time_s range                         : %.2f .. %.2f" % (
        min(r["ptb_time"] for r in ok), max(r["ptb_time"] for r in ok)))
    print("  ptb_t (time gate) range                  : %.2f .. %.2f" % (
        min(r["ptb_t"] for r in ok), max(r["ptb_t"] for r in ok)))
    print("  RELEASE duration range                   : %.2f .. %.2f s" % (
        min(r["t_exit"] for r in ok), max(r["t_exit"] for r in ok)))
    print("  frames failing velocity condition        : %d / %d (%.1f%%)" % (
        sum(r["vel_fail_frames"] for r in ok),
        sum(r["release_frames"] for r in ok),
        100.0 * sum(r["vel_fail_frames"] for r in ok)
        / max(1, sum(r["release_frames"] for r in ok))))
    print("  frames failing position conditions        : %d / %d (%.1f%%)" % (
        sum(r["pos_fail_frames"] for r in ok),
        sum(r["release_frames"] for r in ok),
        100.0 * sum(r["pos_fail_frames"] for r in ok)
        / max(1, sum(r["release_frames"] for r in ok))))
    print()

    last_cond = Counter()
    for r in ok:
        if not r["last_fails"]:
            last_cond["(none - exited cleanly)"] += 1
        else:
            for f in r["last_fails"]:
                last_cond[f] += 1
    print("  condition failing on the FINAL RELEASE frame (the stall driver):")
    for k, v in last_cond.most_common():
        print("     %-22s %d / %d" % (k, v, n))
    print()

    # The decisive comparison: which clears last, position or velocity?
    both = [r for r in ok if r["t_pos_ok"] is not None
            and r["t_vel_ok"] is not None]
    if both:
        vel_last = sum(1 for r in both if r["t_vel_ok"] > r["t_pos_ok"])
        pos_last = sum(1 for r in both if r["t_pos_ok"] > r["t_vel_ok"])
        same = len(both) - vel_last - pos_last
        print("  ordering of the final two conditions:")
        print("     velocity clears AFTER position  : %d / %d" % (vel_last, len(both)))
        print("     position clears AFTER velocity  : %d / %d" % (pos_last, len(both)))
        print("     same frame                      : %d / %d" % (same, len(both)))
        print()
        stalls = [r["t_exit"] - r["t_pos_ok"] for r in both]
        total = [r["t_exit"] for r in both]
        print("  tail stall (exit - position-ready): %.2f .. %.2f s" % (
            min(stalls), max(stalls)))
        print("  share of RELEASE spent with position ALREADY ok:")
        for r in both:
            if r["t_exit"] > 0:
                print("     rollout %-2d  %.2fs of %.2fs (%.0f%%)" % (
                    r["idx"], r["t_exit"] - r["t_pos_ok"], r["t_exit"],
                    100.0 * (r["t_exit"] - r["t_pos_ok"]) / r["t_exit"]))
    print()
    print("  DH role observed                         : %s" % set(
        r["role"] for r in ok))
    print("  DH end speed range                       : %.2f .. %.2f m/s" % (
        min(r["dh_end_speed"] for r in ok),
        max(r["dh_end_speed"] for r in ok)))
    print("  DH_VELOCITY_STABLE_THRESHOLD_MPS         : %.2f m/s" % (
        ope_mod.DH_VELOCITY_STABLE_THRESHOLD_MPS,))
    return 0


if __name__ == "__main__":
    code = 2
    try:
        code = main()
    finally:
        pygame.quit()
    sys.exit(code)
