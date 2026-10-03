"""AUTHORITATIVE flow + contract diagnostic.

Runs one rollout and reports, every 60 frames, the raw role census (no
predicate), plus the contract measurements (obligated count, gates,
violations) computed the same way the probe computes them.  Any exception
inside ope.update is printed with its traceback instead of being swallowed.

Ends with a summary: discharge runs per body, cycle count, phase budget.
"""

import os
import statistics
import sys
import traceback

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
from game.defence.defence_lvl_1.anchor_authority import (
    defensive_line_anchor_m as _anchor_m,
)

DT = 1.0 / 60.0
FRAMES = int(os.environ.get("RLM_DIAG_FRAMES", "1200"))
CENSUS_EVERY = int(os.environ.get("RLM_DIAG_CENSUS", "120"))
DRIVE_END_PHASES = ("TACKLE_DOMINANCE",)
PTB_ENTRY_PHASES = ("PTB_PREP", "PTB_EXEC", "PTB")


def boot(seed):
    collision_system = CollisionSystem()
    field = Field(1920, 1080)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball
    open_play = OpenPlayEngine(world, seed=seed)
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


def census(team):
    counts = {}
    for d in team:
        role = getattr(d, "defender_role", None)
        name = getattr(role, "name", None) or str(role)
        counts[name] = counts.get(name, 0) + 1
    return counts


def main():
    world, ball, ope, kickoff_engine, collision_system = boot(1)
    dm = getattr(ope, "defence_manager", None)

    errors = 0
    open_play_frames = 0
    obligated_frames = 0
    obligated_body_frames = 0
    raw_retreat_body_frames = 0
    gates = 0
    violations = 0
    current = {}
    runs = {}
    ptb_s = []
    drive_end_frame = None
    prev_phase = None
    samples = []
    open_samples = []
    violation_samples = []
    open_ticks = 0

    for i in range(FRAMES):
        world.frame_count += 1
        ball.update(DT)
        phase = world.phase
        if phase == GamePhase.KICKOFF:
            kickoff_engine.update(DT)
            check_for_catch(world, ball, DT)
            prev_phase = ope._phase
            continue
        if phase == GamePhase.CATCH:
            if world.ball_carrier is not None:
                ope.start(world.ball_carrier)
            world.phase = GamePhase.OPEN_PLAY
            prev_phase = ope._phase
            continue
        if phase != GamePhase.OPEN_PLAY:
            prev_phase = ope._phase
            continue

        try:
            ope.update(DT)
        except Exception:
            errors += 1
            if errors <= 3:
                print("EXCEPTION in ope.update at frame", world.frame_count)
                traceback.print_exc()
        if not world.tes_active:
            collision_system.resolve_all(world.players, world=world)

        # ---- phase budget ----
        cur_phase = ope._phase
        if cur_phase in DRIVE_END_PHASES:
            drive_end_frame = int(world.frame_count)
        elif (drive_end_frame is not None and cur_phase in PTB_ENTRY_PHASES
                and prev_phase not in PTB_ENTRY_PHASES):
            ptb_s.append((int(world.frame_count) - drive_end_frame) * DT)
            drive_end_frame = None
        prev_phase = cur_phase

        # ---- raw census (no predicate) ----
        team = list(getattr(world, "defending_team", None) or ())
        counts = census(team)
        raw_retreat_body_frames += counts.get("DEF_RETREAT_10", 0)

        # ---- contract (predicate) ----
        obligated_now = []
        for d in team:
            pid = getattr(d, "player_id", "?")
            if ro.is_retreat_obligated(d, world):
                obligated_now.append(d)
                current[pid] = current.get(pid, 0) + 1
            elif current.get(pid):
                runs.setdefault(pid, []).append(current[pid])
                current[pid] = 0

        open_play_frames += 1
        if obligated_now:
            obligated_frames += 1
        obligated_body_frames += len(obligated_now)
        if dm is not None:
            gates += int(getattr(dm, "_retreat_gated_last_frame", 0) or 0)
        _vs = ro.audit_retreat_obligation_compliance(world, team)
        violations += len(_vs)
        if _vs and len(violation_samples) < 6:
            violation_samples.append(f"f{int(world.frame_count)} {_vs[0]}")
        if obligated_now and len(samples) < 10:
            _d = obligated_now[0]
            samples.append(
                f"f{int(world.frame_count)} ope={cur_phase} id={_d.player_id} "
                f"x={float(_d.x):.2f} ruck={float(getattr(world, 'ruck_x_m', 0.0) or 0.0):.2f} "
                f"anchor={float(_anchor_m(world)):.2f} "
                f"tx={float(getattr(_d, 'target_x', 0.0) or 0.0):.2f} "
                f"src={getattr(_d, 'target_source', '')!r} "
                f"state={getattr(getattr(_d, 'movement_state', None), 'name', '')} "
                f"n={len(obligated_now)}"
            )
        # ── THE ERA THAT MATTERS: true OPEN_PLAY ──
        # The stall lives here, not in RELEASE (where the retreat writer is
        # demonstrably driving the line at ~5 m/s toward the anchor).
        # Count only the frames this era actually has (``world.frame_count``
        # advances ~2 per iteration, so a frame-count modulo never aligns).
        if obligated_now and cur_phase == "OPEN_PLAY":
            open_ticks += 1
        if (obligated_now and cur_phase == "OPEN_PLAY"
                and len(open_samples) < 10
                and open_ticks % 150 == 1):
            _d = obligated_now[0]
            _anon = [
                float(getattr(x, "x", 0.0) or 0.0)
                for x in obligated_now
            ]
            open_samples.append(
                f"f{int(world.frame_count)} id={_d.player_id} "
                f"x={float(_d.x):.2f} anchor={float(_anchor_m(world)):.2f} "
                f"tx={float(getattr(_d, 'target_x', 0.0) or 0.0):.2f} "
                f"src={getattr(_d, 'target_source', '')!r} "
                f"state={getattr(getattr(_d, 'movement_state', None), 'name', '')!r} "
                f"rawmstate={getattr(_d, 'movement_state', None)!r} "
                f"ms={float(getattr(_d, 'move_speed', 0.0) or 0.0):.2f} "
                f"mmdlmoved={bool(getattr(_d, '_mmdl_moved', False))} "
                f"frozen={bool(getattr(_d, 'frozen', False))} "
                f"ft={float(getattr(_d, 'freeze_timer', 0.0) or 0.0):.2f} "
                f"v=({float(getattr(_d, 'vx', 0.0) or 0.0):.2f},"
                f"{float(getattr(_d, 'vy', 0.0) or 0.0):.2f}) "
                f"n={len(obligated_now)} xrange="
                f"{min(_anon):.1f}..{max(_anon):.1f}"
            )

        if (i + 1) % CENSUS_EVERY == 0:
            bc = world.ball_carrier
            print(
                f"f{world.frame_count:5d} ope={cur_phase:<16} tes={world.tes_active} "
                f"bc={'-' if bc is None else round(bc.x, 1)} "
                f"obl={len(obligated_now)} raw={counts.get('DEF_RETREAT_10', 0)} "
                f"{counts}"
            )

    for pid, frames in current.items():
        if frames:
            runs.setdefault(pid, []).append(frames)

    print()
    print("=" * 92)
    print("SUMMARY")
    print("=" * 92)
    print(f"  errors in ope.update            : {errors}")
    print(f"  OPEN_PLAY frames                : {open_play_frames}")
    print(f"  frames with >=1 obligated body  : {obligated_frames}")
    print(f"  obligated body-frames           : {obligated_body_frames}")
    print(f"  RAW DEF_RETREAT_10 body-frames  : {raw_retreat_body_frames}")
    print(f"  defender-gates issued by sync   : {gates}")
    print(f"  audit violations                : {violations}")
    for _v in violation_samples:
        print(f"    {_v}")
    flat = [f for rr in runs.values() for f in rr]
    if flat:
        secs = [f * DT for f in flat]
        print(f"  obligation runs                 : n={len(secs)} "
              f"min={min(secs):.3f} median={statistics.median(secs):.3f} "
              f"max={max(secs):.3f}")
        worst = sorted(runs.items(), key=lambda kv: max(kv[1]),
                       reverse=True)[:6]
        for pid, rr in worst:
            print(f"    id={pid} runs={len(rr)} worst={max(rr)} "
                  f"({max(rr) * DT:.3f} s)")
    else:
        print("  obligation runs                 : none")
    if ptb_s:
        print(f"  DRIVE_END->PTB_ENTER            : n={len(ptb_s)} "
              f"mean={statistics.mean(ptb_s):.3f} max={max(ptb_s):.3f}")
    print("  first obligated samples:")
    for s in samples:
        print(f"    {s}")
    print("  OPEN_PLAY-era obligated samples (the era that stalls):")
    for s in open_samples:
        print(f"    {s}")


if __name__ == "__main__":
    try:
        main()
    finally:
        pygame.quit()
