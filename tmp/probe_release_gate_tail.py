"""tmp/probe_release_gate_tail.py - what keeps RELEASE alive PAST the time gate.

Facts already established:
  * time gate  ptb_t = max(1.54, tes_outcome.ptb_time_s); ptb_time_s is drawn
    from uniform(1.0,1.4) / uniform(1.6,2.4) / uniform(2.2,3.2), so
    ptb_t <= 3.2 s ALWAYS.  Past ~3.2 s the time gate cannot be the blocker.
  * force-override escape hatch fires 300 frames after RELEASE_MIN_DURATION_S
    (1.54 s) -> 6.54 s, i.e. _ptb_gate_override_frames.

So any RELEASE alive between 3.2 s and 6.54 s is held open PURELY by
_check_dh_ready_for_ptb().  This probe measures, over that population:

  * which of the 8 readiness conditions fail,
  * whether the DH's reported vx/vy is genuine motion or a stale value
    (by comparing vx*dt against the actual per-frame x displacement),
  * whether the exit was gate-driven or override-driven.

Run:  python tmp/probe_release_gate_tail.py
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

MAX_FRAMES = 3000
ROLLOUTS = 5

# Past this timer value the time gate can never be the blocker.
TIME_GATE_CEILING = 3.20

BLOCKING_ROLES = (
    DefenderRole.TACKLER_1,
    DefenderRole.TACKLER_2,
    DefenderRole.TACKLER_3,
    DefenderRole.MARKER_A,
    DefenderRole.MARKER_B,
)
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
        prev_x = None
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
                real_dx = (dh.x - prev_x) if (dh is not None
                                              and prev_x is not None) else None
                rows.append({
                    "t": float(open_play._phase_timer),
                    "fails": fails,
                    "dh_x": dh.x if dh is not None else None,
                    "dh_y": dh.y if dh is not None else None,
                    "dh_vx": dh.vx if dh is not None else None,
                    "dh_vy": dh.vy if dh is not None else None,
                    "dh_speed": (math.hypot(dh.vx, dh.vy)
                                 if dh is not None else None),
                    "real_dx": real_dx,
                    "bc_x": bc.x if bc is not None else None,
                    "ptb_time_s": (float(open_play.tes_outcome.ptb_time_s)
                                   if open_play.tes_outcome else None),
                    "override": int(open_play._ptb_gate_override_frames),
                })
                prev_x = dh.x if dh is not None else None
            elif entered and prev == "RELEASE":
                break
            prev = phase
    return rows


def main():
    all_rows = []
    per_rollout = []
    for i in range(ROLLOUTS):
        rows = run_rollout()
        if rows:
            for r in rows:
                r["rollout"] = i
            all_rows.extend(rows)
            ok = [r for r in rows if r["dh_x"] is not None]
            per_rollout.append({
                "idx": i,
                "ptb_t": max(1.54, rows[0]["ptb_time_s"] or 1.54),
                "t_exit": rows[-1]["t"],
                "frames": len(rows),
                "last_fails": list(rows[-1]["fails"]),
                "override_at_last": rows[-1]["override"],
                "last_speed": rows[-1]["dh_speed"],
                "travel": (abs(rows[-1]["dh_x"] - rows[0]["dh_x"])
                           if rows[0]["dh_x"] is not None else 0.0),
            })

    print("=" * 92)
    print("WHAT HOLDS RELEASE PAST THE TIME GATE  (%d rollouts)" % ROLLOUTS)
    print("=" * 92)
    print()
    print("per-rollout summary")
    print("-" * 92)
    print("  #  ptb_t  exit_t  frames  dh_travel  end_spd  override  "
          "last blocking set")
    for r in per_rollout:
        print("  %-2d %6.2f %7.2f %7d %10.2f %8.2f %9d  %s" % (
            r["idx"], r["ptb_t"], r["t_exit"], r["frames"], r["travel"],
            r["last_speed"], r["override_at_last"],
            ",".join(r["last_fails"]) or "PASS"))
    print()

    # ── the population that CANNOT be explained by the time gate ──
    tail = [r for r in all_rows if r["t"] >= TIME_GATE_CEILING]
    print("-" * 92)
    print("POPULATION: RELEASE frames with timer >= %.2fs "
          "(time gate provably cannot block)" % TIME_GATE_CEILING)
    print("-" * 92)
    print("  frames in population : %d / %d (%.1f%% of all RELEASE frames)" % (
        len(tail), len(all_rows),
        100.0 * len(tail) / max(1, len(all_rows))))
    if tail:
        counts = Counter()
        for r in tail:
            for f in r["fails"]:
                counts[f] += 1
        print("  readiness conditions failing in that population:")
        for name, n in counts.most_common():
            print("     %-18s %6d  (%.1f%% of population)" % (
                name, n, 100.0 * n / len(tail)))
        pure = sum(1 for r in tail if r["fails"] == [VEL_CONDITION])
        print()
        print("  frames where condition 6 is the ONLY remaining failure: "
              "%d (%.1f%%)" % (pure, 100.0 * pure / len(tail)))
        print()
        print("  readiness conditions by 0.5s timer bucket:")
        buckets = {}
        for r in tail:
            b = int(r["t"] * 2) / 2.0
            buckets.setdefault(b, Counter())
            for f in r["fails"]:
                buckets[b][f] += 1
        for b in sorted(buckets):
            items = ", ".join("%s=%d" % (k, v)
                              for k, v in buckets[b].most_common())
            print("     t>=%.1fs  %s" % (b, items))
    print()

    # ── is the DH velocity genuine motion, or a stale value? ──
    moving = [r for r in all_rows
              if r["dh_x"] is not None and r["real_dx"] is not None]
    if moving:
        errs = [abs(r["real_dx"] * 60.0 - r["dh_vx"]) for r in moving]
        errs.sort()
        med = errs[len(errs) // 2]
        print("-" * 92)
        print("IS dh.vx GENUINE MOTION?  (compare vx against the actual "
              "per-frame displacement x60)")
        print("-" * 92)
        print("  frames compared            : %d" % len(moving))
        print("  median |vx - dx*60|        : %.4f m/s" % med)
        print("  p95    |vx - dx*60|        : %.4f m/s" % errs[int(0.95 * len(errs))])
        print("  -> a near-zero error means the reported DH speed is REAL "
              "movement,")
        print("     not a stale velocity left behind by a writer.")
        print()

    # ── velocity sweep across the whole RELEASE ──
    speeds = [r["dh_speed"] for r in all_rows if r["dh_speed"] is not None]
    if speeds:
        speeds_sorted = sorted(speeds)
        sp50 = speeds_sorted[len(speeds_sorted) // 2]
        print("-" * 92)
        print("DH SPEED DISTRIBUTION ACROSS ALL RELEASE FRAMES")
        print("-" * 92)
        print("  frames              : %d" % len(speeds))
        print("  min / p50 / p95 / max : %.2f / %.2f / %.2f / %.2f m/s" % (
            speeds_sorted[0], sp50,
            speeds_sorted[int(0.95 * len(speeds_sorted))], speeds_sorted[-1]))
        print("  threshold (DH_VELOCITY_STABLE_THRESHOLD_MPS) : %.2f m/s" % (
            ope_mod.DH_VELOCITY_STABLE_THRESHOLD_MPS,))
        print("  frames ABOVE threshold : %d (%.1f%%)" % (
            sum(1 for s in speeds
                if s > ope_mod.DH_VELOCITY_STABLE_THRESHOLD_MPS),
            100.0 * sum(1 for s in speeds
                        if s > ope_mod.DH_VELOCITY_STABLE_THRESHOLD_MPS)
            / len(speeds)))
        print("  RELEASE writer sets dh.move_speed = 4.00 m/s "
              "(open_play_engine.py P7 block)")
        print()
    return 0


if __name__ == "__main__":
    code = 2
    try:
        code = main()
    finally:
        pygame.quit()
    sys.exit(code)
