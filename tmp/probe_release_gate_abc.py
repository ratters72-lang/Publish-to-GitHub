"""tmp/probe_release_gate_abc.py - causal A/B/C test of the RELEASE stall.

Baseline showed condition 6 (DH velocity > 1.2 m/s) is the only condition
still failing once the DH has converged on bc.x - 1.0.  This probe proves
causality by changing ONE thing at a time, in memory only (no source edits):

  A  baseline            - nothing patched
  B  threshold raised    - DH_VELOCITY_STABLE_THRESHOLD_MPS := 5.0
                           (velocity test effectively disabled)
  C  writer slowed       - DH pre-position move_speed clamped to 1.0
                           (below the 1.2 threshold it must satisfy)

If B collapses the stall, condition 6 is the binding constraint.
If C also collapses it, the 4.0 m/s pre-position writer is the cause.

Run:  python tmp/probe_release_gate_abc.py
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import contextlib
import io
import math
import sys

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
from player.player import Player, DefenderRole

MAX_FRAMES = 3000
ROLLOUTS = 3
DH_CLAMP_SPEED = 1.0

BLOCKING_ROLES = (
    DefenderRole.TACKLER_1,
    DefenderRole.TACKLER_2,
    DefenderRole.TACKLER_3,
    DefenderRole.MARKER_A,
    DefenderRole.MARKER_B,
)
POS_CONDITIONS = ("3_ahead_of_bc", "4_pos_err", "5_lateral")
VEL_CONDITION = "6_velocity"

_ORIG_PLAYER_UPDATE = Player.update


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


def _clamped_update(self, dt, attacking_direction=1):
    """Variant C: cap the DUMMY_HALF's move_speed below the gate threshold."""
    if getattr(self, "defender_role", None) == DefenderRole.DUMMY_HALF:
        if getattr(self, "move_speed", 0.0) > DH_CLAMP_SPEED:
            self.move_speed = DH_CLAMP_SPEED
    return _ORIG_PLAYER_UPDATE(self, dt, attacking_direction)


def failing_conditions(ope, world):
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
    if abs(dh.x - (bc.x - ope_mod.DH_BEHIND_DISTANCE_M)) > \
            ope_mod.DH_POSITION_TOLERANCE_M + 0.1:
        fails.append("4_pos_err")
    if ruck_y is not None and \
            abs(dh.y - ruck_y) > ope_mod.DH_LATERAL_TOLERANCE_M + 0.3:
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
                    "dh_speed": (math.hypot(dh.vx, dh.vy)
                                 if dh is not None else None),
                    "ptb_time_s": (float(open_play.tes_outcome.ptb_time_s)
                                   if open_play.tes_outcome else None),
                })
            elif entered and prev == "RELEASE":
                break
            prev = phase
    return rows


def summarise(rows, idx):
    if not rows:
        return {"idx": idx, "ok": False}
    ptb_time = rows[0]["ptb_time_s"] or 1.54
    ptb_t = max(1.54, ptb_time)
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
        "ptb_t": ptb_t,
        "frames": len(rows),
        "t_exit": rows[-1]["t"],
        "t_pos_ok": t_pos_ok,
        "t_vel_ok": t_vel_ok,
        "last_fails": list(rows[-1]["fails"]),
        "vel_fail_frames": sum(1 for r in rows if VEL_CONDITION in r["fails"]),
        "pos_fail_frames": sum(
            1 for r in rows if any(f in r["fails"] for f in POS_CONDITIONS)),
        "dh_travel": (abs(rows[-1]["dh_x"] - rows[0]["dh_x"])
                      if rows[0]["dh_x"] is not None else 0.0),
        "dh_end_speed": rows[-1]["dh_speed"],
    }


def run_variant(mode):
    """mode: 'A' baseline, 'B' threshold raised, 'C' writer slowed."""
    saved_threshold = ope_mod.DH_VELOCITY_STABLE_THRESHOLD_MPS
    if mode == "B":
        ope_mod.DH_VELOCITY_STABLE_THRESHOLD_MPS = 5.0
    if mode == "C":
        Player.update = _clamped_update
    try:
        return [summarise(run_rollout(), i) for i in range(ROLLOUTS)]
    finally:
        ope_mod.DH_VELOCITY_STABLE_THRESHOLD_MPS = saved_threshold
        Player.update = _ORIG_PLAYER_UPDATE


VARIANTS = [
    ("A", "baseline (threshold 1.20 m/s, writer 4.0 m/s)"),
    ("B", "threshold raised to 5.00 m/s (velocity test disabled)"),
    ("C", "DH pre-position writer clamped to 1.00 m/s"),
]


def main():
    all_results = {}
    for mode, label in VARIANTS:
        all_results[mode] = run_variant(mode)

    print("=" * 92)
    print("RELEASE -> PTB_PREP CAUSAL A/B/C TEST  (%d rollouts per variant)" % ROLLOUTS)
    print("=" * 92)
    print()
    for mode, label in VARIANTS:
        print("-" * 92)
        print("VARIANT %s : %s" % (mode, label))
        print("-" * 92)
        rows = all_results[mode]
        print("  #  ptb_t  exit_t  t_pos_ok  t_vel_ok  vel_fail  pos_fail  "
              "dh_travel  end_spd  last blocking set")
        for r in rows:
            if not r["ok"]:
                print("  %-2d (no RELEASE observed)" % r["idx"])
                continue
            print("  %-2d %6.2f %7.2f %9s %9s %9d %9d %10.2f %8.2f  %s" % (
                r["idx"], r["ptb_t"], r["t_exit"],
                ("%.2f" % r["t_pos_ok"]) if r["t_pos_ok"] is not None else "-",
                ("%.2f" % r["t_vel_ok"]) if r["t_vel_ok"] is not None else "-",
                r["vel_fail_frames"], r["pos_fail_frames"], r["dh_travel"],
                r["dh_end_speed"], ",".join(r["last_fails"]) or "PASS"))
        ok = [r for r in rows if r["ok"]]
        if ok:
            exits = [r["t_exit"] for r in ok]
            stalls = [r["t_exit"] - r["t_pos_ok"] for r in ok
                      if r["t_pos_ok"] is not None]
            print()
            print("     RELEASE duration : %.2f .. %.2f s  (mean %.2f)" % (
                min(exits), max(exits), sum(exits) / len(exits)))
            if stalls:
                print("     tail stall after position ok : %.2f .. %.2f s "
                      "(mean %.2f)" % (min(stalls), max(stalls),
                                       sum(stalls) / len(stalls)))
            print("     frames failing velocity      : %d / %d" % (
                sum(r["vel_fail_frames"] for r in ok),
                sum(r["frames"] for r in ok)))
        print()

    print("-" * 92)
    print("CONCLUSION")
    print("-" * 92)
    a = [r for r in all_results["A"] if r["ok"]]
    b = [r for r in all_results["B"] if r["ok"]]
    c = [r for r in all_results["C"] if r["ok"]]
    if a and b:
        print("  baseline mean RELEASE : %.2f s" % (
            sum(r["t_exit"] for r in a) / len(a)))
        print("  threshold=5.0 mean    : %.2f s" % (
            sum(r["t_exit"] for r in b) / len(b)))
    if c:
        print("  writer=1.0 mean       : %.2f s" % (
            sum(r["t_exit"] for r in c) / len(c)))
    print("  (a large drop in B/C vs A proves condition 6 is the binding "
          "constraint)")
    return 0


if __name__ == "__main__":
    code = 2
    try:
        code = main()
    finally:
        pygame.quit()
    sys.exit(code)
