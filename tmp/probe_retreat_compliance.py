"""READ-ONLY live-rollout probe for the role-based retreat-obligation gate.

CONTRACT UNDER TEST (2026-09-18)
--------------------------------
A defender is retreat-obligated iff its LIVE ROLE is DEF_RETREAT_10, and it
may not hold tackle authority while that is true.  The engine's own arrival
test (``d.x >= target_x``) is the sole writer of retreat-completion.

Measures, over N live rollouts:
  1. IS THE GATE LIVE?      frames with >=1 obligated body; gates issued;
                            audit violations (a writer bypassing the veto).
  2. IS IT DISCHARGED?      per-body maximal run length as DEF_RETREAT_10.
  3. IS THE BUDGET SAFE?    DRIVE_END -> PTB_ENTER vs the 1.289 s baseline.

Nothing is patched and no production file is written.
"""

import contextlib
import io
import os
import statistics
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
from game.defence.defence_lvl_1.anchor_authority import (
    defensive_line_anchor_m as _anchor_m,
)

DT = 1.0 / 60.0
# RLM_PROBE_FRAMES / RLM_PROBE_ROLLOUTS allow a SHORT smoke run that still
# proves the contract (discharge, veto, violations) without the full
# regression budget.  Defaults = the full run (60 s of play, 4 rollouts).
FRAMES = int(os.environ.get("RLM_PROBE_FRAMES", "3600"))
ROLLOUTS = int(os.environ.get("RLM_PROBE_ROLLOUTS", "4"))
BASELINE_PTB_S = 1.289

# Phase names as reported by ope._phase.
DRIVE_END_PHASES = ("TACKLE_DOMINANCE",)
PTB_ENTRY_PHASES = ("PTB_PREP", "PTB_EXEC", "PTB")


def boot_world(seed):
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


def role_name(d):
    role = getattr(d, "defender_role", None)
    return getattr(role, "name", None) or str(role)


def run_rollout(seed):
    """Run one rollout read-only; return the per-rollout measurement dict."""
    buf = io.StringIO()
    result = {
        "open_play_frames": 0,
        "obligated_frames": 0,
        "obligated_body_frames": 0,
        "gates": 0,
        "violations": 0,
        "runs": {},          # pid -> list of run lengths (frames)
        "current": {},       # pid -> frames in the open run
        "ptb_s": [],
        "cycles": 0,
        "samples": [],
        "violations_detail": [],
        "revoked": 0,
    }
    with contextlib.redirect_stdout(buf):
        world, ball, ope, kickoff_engine, collision_system = boot_world(seed)
        dm = getattr(ope, "defence_manager", None)
        drive_end_frame = None
        prev_phase = None
        for _ in range(FRAMES):
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

            ope.update(DT)
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)

            # ── PHASE-BUDGET TRACKING (read-only) ──
            cur_phase = ope._phase
            if cur_phase in DRIVE_END_PHASES:
                drive_end_frame = int(world.frame_count)
            elif drive_end_frame is not None and cur_phase in PTB_ENTRY_PHASES \
                    and prev_phase not in PTB_ENTRY_PHASES:
                result["ptb_s"].append(
                    (int(world.frame_count) - drive_end_frame) * DT
                )
                result["cycles"] += 1
                drive_end_frame = None
            prev_phase = cur_phase

            team = list(getattr(world, "defending_team", None) or ())
            obligated_now = []
            for d in team:
                if ro.is_retreat_obligated(d, world):
                    obligated_now.append(d)
                    pid = getattr(d, "player_id", "?")
                    result["current"][pid] = \
                        result["current"].get(pid, 0) + 1
                else:
                    pid = getattr(d, "player_id", "?")
                    if result["current"].get(pid):
                        result["runs"].setdefault(pid, []).append(
                            result["current"][pid]
                        )
                        result["current"][pid] = 0

            result["open_play_frames"] += 1
            if obligated_now:
                result["obligated_frames"] += 1
            result["obligated_body_frames"] += len(obligated_now)
            # ── WHY IT DOES NOT DISCHARGE (diagnostic) ──
            # Geometry of an obligated body: position vs the anchor, the
            # target it was handed this frame, and which writer handed it.
            if obligated_now and len(result["samples"]) < 12 and (
                len(result["samples"]) < 3
                or int(world.frame_count) % 300 == 0
            ):
                _d = obligated_now[0]
                result["samples"].append({
                    "frame": int(world.frame_count),
                    "pid": getattr(_d, "player_id", "?"),
                    "x": round(float(getattr(_d, "x", 0.0) or 0.0), 2),
                    "ruck_x": round(float(
                        getattr(world, "ruck_x_m", 0.0) or 0.0), 2),
                    "anchor": round(float(_anchor_m(world)), 2),
                    "target_x": round(float(
                        getattr(_d, "target_x", 0.0) or 0.0), 2),
                    "src": str(getattr(_d, "target_source", "") or ""),
                    "state": str(getattr(
                        getattr(_d, "movement_state", None), "name", "")),
                    "n_obligated": len(obligated_now),
                })
            if dm is not None:
                result["gates"] += int(
                    getattr(dm, "_retreat_gated_last_frame", 0) or 0
                )
            _vs = ro.audit_retreat_obligation_compliance(world, team)
            result["violations"] += len(_vs)
            for _v in _vs:
                if len(result["violations_detail"]) < 8:
                    result["violations_detail"].append(
                        f"f{int(world.frame_count)} {_v}"
                    )

        # close any run still open at the end of the rollout
        for pid, frames in result["current"].items():
            if frames:
                result["runs"].setdefault(pid, []).append(frames)
        result["revoked"] = int(
            getattr(dm, "_retreat_revoked_total", 0) or 0
        )
    return result


def main():
    print("=" * 104)
    print("RETREAT-OBLIGATION LIVE-ROLLOUT PROBE (role-based contract)")
    print("=" * 104)
    print(f"  frames per rollout     : {FRAMES}   rollouts : {ROLLOUTS}   "
          f"dt : {DT:.6f}")
    print(f"  obligation             : defender_role is DEF_RETREAT_10")
    print(f"  baseline budget        : DRIVE_END -> PTB_ENTER "
          f"{BASELINE_PTB_S:.3f} s")
    print()

    totals = {
        "open_play_frames": 0,
        "obligated_frames": 0,
        "obligated_body_frames": 0,
        "gates": 0,
        "violations": 0,
    }
    all_runs = {}
    all_ptb = []
    cycles = 0
    diag = []
    diag_v = []
    revoked_total = 0
    for i in range(1, ROLLOUTS + 1):
        print(f"  rollout {i} starting ...")
        r = run_rollout(seed=i)
        for key in totals:
            totals[key] += r[key]
        for pid, runs in r["runs"].items():
            all_runs.setdefault(pid, []).extend(runs)
        all_ptb.extend(r["ptb_s"])
        cycles += r["cycles"]
        diag.extend(r["samples"])
        diag_v.extend(r["violations_detail"])
        revoked_total += r["revoked"]
        print(f"  rollout {i}: {r['open_play_frames']:5d} OPEN_PLAY frames  "
              f"{r['obligated_frames']:5d} obligated frames  "
              f"{r['gates']:5d} gates  {r['violations']} violations")

    print()
    print("-" * 104)
    print("1. IS THE GATE LIVE?  (a gate that never fires == no gate)")
    print("-" * 104)
    share = (100.0 * totals["obligated_frames"] / totals["open_play_frames"]
             if totals["open_play_frames"] else 0.0)
    print(f"  OPEN_PLAY frames sampled        : {totals['open_play_frames']}")
    print(f"  frames with >=1 obligated body  : {totals['obligated_frames']}"
          f"  ({share:.1f}%)")
    print(f"  obligated body-frames           : "
          f"{totals['obligated_body_frames']}")
    print(f"  defender-gates issued by sync   : {totals['gates']}")
    print(f"  gate share of OPEN_PLAY frames  : {share:.1f}% "
          f"(informational - a correct gate is narrow)")
    if totals["obligated_frames"] > 0:
        print("  VERDICT: gate is LIVE (the retreat role reaches OPEN_PLAY "
              "and the veto sees it)")
    else:
        print("  VERDICT: gate is DEAD - the retreat role never reached the "
              "veto")

    print()
    print("-" * 104)
    print("2. DID ANY AUTHORITY WRITER BYPASS THE VETO?")
    print("-" * 104)
    print(f"  audit violations across every sampled frame : "
          f"{totals['violations']}")
    if totals["violations"] == 0:
        print("  VERDICT: NO WRITER GRANTED AUTHORITY TO AN OBLIGATED BODY")
    else:
        print("  VERDICT: WRITERS BYPASSED THE VETO (see the audit above)")
    print(f"  post-writer revocations by the manager : {revoked_total}")
    print("  first violations (which markers survive):")
    for _v in diag_v:
        print(f"    {_v}")

    print()
    print("-" * 104)
    print("3. IS THE OBLIGATION DISCHARGED (not a permanent stall)?")
    print("-" * 104)
    flat = [f for runs in all_runs.values() for f in runs]
    if flat:
        secs = [f * DT for f in flat]
        print(f"  obligation runs observed        : {len(secs)}")
        print(f"  run length (s)                  : n={len(secs)} "
              f"min={min(secs):.3f} median={statistics.median(secs):.3f} "
              f"mean={statistics.mean(secs):.3f} max={max(secs):.3f}")
        print(f"  distinct defenders obligated    : {len(all_runs)}")
        worst = sorted(all_runs.items(),
                       key=lambda kv: max(kv[1]), reverse=True)[:6]
        print("  worst runs by defender (frames -> s):")
        for pid, runs in worst:
            w = max(runs)
            print(f"    id={pid:<5} runs={len(runs):3d} worst={w:5d} "
                  f"({w * DT:.3f} s)")
        over = [s for s in secs if s > 5.0]
        if over:
            print(f"  runs longer than 5.0 s          : {len(over)} "
                  f"(worst {max(over):.3f} s)")
        if max(secs) <= 5.0:
            print("  VERDICT: the obligation DISCHARGES "
                  "(every run clears well inside a tackle cycle)")
        else:
            print("  VERDICT: at least one obligation run exceeds 5.0 s - "
                  "inspect the worst defenders above")
    else:
        print("  obligation runs observed        : 0")
        print("  VERDICT: no obligation was ever observed (see section 1)")

    print()
    print("-" * 104)
    print("3b. WHY DOESN'T IT DISCHARGE?  (obligated-body geometry)")
    print("-" * 104)
    print("   frame    id        x   ruck_x   anchor target_x  "
          "state            target_source")
    for s in diag:
        print(f"  {s['frame']:6d} {str(s['pid']):>4} {s['x']:8.2f} "
              f"{s['ruck_x']:8.2f} {s['anchor']:8.2f} "
              f"{s['target_x']:8.2f}  {s['state']:<15}  "
              f"{s['src']}  (n={s['n_obligated']})")
    print()

    print("-" * 104)
    print("4. IS THE PHASE BUDGET UNTOUCHED?")
    print("-" * 104)
    if all_ptb:
        print(f"  cycles completed         : {cycles}")
        print(f"  DRIVE_END -> PTB_ENTER (s): n={len(all_ptb)} "
              f"min={min(all_ptb):.3f} median={statistics.median(all_ptb):.3f} "
              f"mean={statistics.mean(all_ptb):.3f} max={max(all_ptb):.3f}")
        delta = statistics.mean(all_ptb) - BASELINE_PTB_S
        print(f"  mean vs recorded baseline : {delta:+.3f} s")
        if abs(delta) <= 0.35:
            print("  VERDICT: phase budget is PRESERVED "
                  "(no RELEASE/PTB timing was added)")
        else:
            print("  VERDICT: phase budget MOVED beyond tolerance")
    else:
        print("  no DRIVE_END -> PTB_ENTER transitions captured")

    print()
    print("=" * 104)
    live = totals["obligated_frames"] > 0
    clean = totals["violations"] == 0
    if flat:
        discharged = max(f * DT for f in flat) <= 5.0
    else:
        discharged = False
    budget_ok = bool(all_ptb) and \
        abs(statistics.mean(all_ptb) - BASELINE_PTB_S) <= 0.35
    ok = live and clean and discharged and budget_ok
    print(f"RESULT: {'PASSED' if ok else 'FAILED'} -- live={live} "
          f"clean={clean} discharged={discharged} budget_ok={budget_ok}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    try:
        main()
    finally:
        pygame.quit()
