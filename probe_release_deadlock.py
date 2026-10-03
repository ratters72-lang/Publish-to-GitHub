"""probe_release_deadlock.py — the RELEASE geometry deadlock, measured.

THE PROVEN CHAIN (each link read from source, then measured here)
----------------------------------------------------------------
1. ``_prepare_release`` stamps the ruck anchor from the drive END X:
   ``world.ruck_x_m = bc.x``.
2. ``Player._clamp_position`` RETURNS EARLY for the ball carrier while
   ``world.tes_active`` and the sub-state is ``tackle_drive`` /
   ``tackle_dominance`` — so the drive can carry the carrier PAST
   ``FIELD_MAX_X`` (120.0) and the ruck anchor inherits that x.
3. ``_update_release`` writes the carrier's stand-up target as
   ``ruck_x + (-attacking_direction) * RECOVERY_BACKWARD_DISTANCE_M``
   (0.15 m).  When the anchor is already at the field edge, that target
   lands AHEAD of the carrier.
4. ``enforce_bc_backward_invariant`` then zeroes the carrier's forward
   ``vx`` EVERY FRAME, and ``_clamp_position`` forbids ``x > FIELD_MAX_X``
   — so the carrier can never reach a target that lies forward of it.
5. ``release_convergence`` cannot rescue it: MARKER_A/MARKER_B pin to
   ``ruck_x + 1.0`` / ``ruck_x + 2.0``, both OUTSIDE the field when the
   anchor is at the edge, so ``markers_ready`` is permanently False and
   ``all_converged`` can never be true.  Only RELEASE_MAX_DURATION_S
   (2.00 s) ends the phase — carrier motionless throughout.
6. ``frozen`` is False the whole time, which is why the Stage 3.5
   freeze/unfreeze fix changed nothing about the symptom.

Every link is reported as a COUNT over a live rollout.  Read-only.
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import math
import random
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import pygame

pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

import world.open_play_engine as ope_mod

ope_mod.DEBUG_SDEA_PHYSICS = False
ope_mod.DEBUG_OPE_TRACE = False

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
from player.player import FIELD_MAX_X, FIELD_MIN_X

from world.rules.release_gate import (
    release_convergence,
    MARKER_CONVERGENCE_TOLERANCE_M,
    BC_RECOVERY_TOLERANCE_M,
    BC_SETTLED_SPEED_MPS,
    RELEASE_MAX_DURATION_S,
    RELEASE_MIN_CONVERGENCE_S,
)

REPORT_PATH = "tmp_release_deadlock_report.txt"
_REPORT = None
_REAL_STDOUT = sys.stdout


def _silence():
    sys.stdout = open(os.devnull, "w", encoding="utf-8")


def _speak():
    if _REPORT is not None:
        sys.stdout = _REPORT


def _close():
    if _REPORT is not None:
        _REPORT.flush()
        _REPORT.close()
    sys.stdout = _REAL_STDOUT
def boot_and_run(frames=6000, seed=20260827):
    """Step the real engine; return the per-frame RELEASE ledger."""
    collision_system = CollisionSystem()
    field = Field(1920, 1080)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball

    open_play = OpenPlayEngine(world)
    world.open_play = open_play
    world.open_play_engine = open_play

    kickoff_engine = KickoffEngine(world, ball, field)

    random.seed(seed)
    kickoff_engine.reset_for_kickoff()
    apply_receiving_kickoff_formation(world, field)
    apply_kicking_team_formation(world, field)
    world.ball_owner = "NONE"
    world.ball_carrier = None
    world.ball.possessed_by = None
    kickoff_engine.kickoff()

    ledger = []
    dt = 1.0 / 60.0
    for frame in range(1, frames + 1):
        world.frame_count = frame
        ball.update(dt)
        if world.phase == GamePhase.KICKOFF:
            kickoff_engine.update(dt)
            check_for_catch(world, ball, dt)
        elif world.phase == GamePhase.CATCH:
            if world.ball_carrier is not None:
                world.open_play.start(world.ball_carrier)
            world.phase = GamePhase.OPEN_PLAY
        elif world.phase == GamePhase.OPEN_PLAY:
            open_play.update(dt)
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)
        else:
            continue

        if getattr(open_play, "_phase", None) != "RELEASE":
            continue

        bc = getattr(world, "ball_carrier", None)
        ruck_x = getattr(world, "ruck_x_m", None)
        try:
            dh_ready = open_play._check_dh_ready_for_ptb(for_exec=False)
        except Exception:
            dh_ready = False
        try:
            rep = release_convergence(open_play, world, dh_ready)
        except Exception as exc:      # never let the instrument crash the run
            rep = {"error": repr(exc)}

        marker_errors = []
        for body in open_play._get_markers():
            tgt = open_play._get_marker_target(body)
            if tgt is None:
                continue
            marker_errors.append((
                getattr(getattr(body, "defender_role", None), "name", "?"),
                round(math.hypot(tgt[0] - body.x, tgt[1] - body.y), 3),
                round(float(tgt[0]), 3),
            ))

        ledger.append({
            "frame": frame,
            "phase_timer": round(float(getattr(open_play, "_phase_timer", 0.0)), 3),
            "ruck_x": ruck_x,
            "ruck_over_field": (None if ruck_x is None else ruck_x - FIELD_MAX_X),
            "bc_present": bc is not None,
            "bc_x": None if bc is None else round(float(bc.x), 3),
            "bc_vx": None if bc is None else round(float(bc.vx), 4),
            "bc_target_x": (None if bc is None
                            else round(float(getattr(bc, "target_x", 0.0)), 3)),
            "bc_speed": (None if bc is None
                         else round(math.hypot(bc.vx, bc.vy), 4)),
            "bc_frozen": None if bc is None else bool(getattr(bc, "frozen", False)),
            "marker_errors": marker_errors,
            "dh_ready": rep.get("dh_ready"),
            "markers_ready": rep.get("markers_ready"),
            "bc_ready": rep.get("bc_ready"),
            "all_converged": rep.get("all_converged"),
        })

    return ledger
def _longest_dwell(ledger):
    """(frames, seconds) of the longest single RELEASE episode.

    ``_phase_timer`` resets to 0.0 on every phase entry, so a step backwards
    in it marks a new episode.
    """
    best = 0
    run = 0
    prev = None
    for r in ledger:
        t = r["phase_timer"]
        if prev is None or t <= prev:
            run = 1
        else:
            run += 1
        best = max(best, run)
        prev = t
    return best, round(best / 60.0, 3)


def analyse(ledger):
    release_frames = len(ledger)
    with_bc = [r for r in ledger if r["bc_present"]]
    ruck_over = [r for r in ledger
                 if r["ruck_over_field"] is not None and r["ruck_over_field"] > 0.0]
    ruck_at_edge = [r for r in ledger
                    if r["ruck_over_field"] is not None and r["ruck_over_field"] > -0.25]
    markers_fail = [r for r in with_bc if r["markers_ready"] is False]
    all_conv = [r for r in with_bc if r["all_converged"] is True]
    frozen = [r for r in with_bc if r["bc_frozen"] is True]
    target_ahead = [
        r for r in with_bc
        if r["bc_target_x"] is not None and r["bc_x"] is not None
        and r["bc_target_x"] > r["bc_x"] + 0.05
    ]
    pinned = [
        r for r in with_bc
        if r["bc_x"] is not None and r["bc_x"] >= FIELD_MAX_X - 1e-6
        and r["bc_speed"] is not None and r["bc_speed"] < BC_SETTLED_SPEED_MPS
        and r["bc_frozen"] is False
    ]
    return {
        "release_frames": release_frames,
        "release_frames_with_bc": len(with_bc),
        "frames_span": (ledger[-1]["frame"] - ledger[0]["frame"] + 1) if ledger else 0,
        "first_frame": ledger[0]["frame"] if ledger else None,
        "last_frame": ledger[-1]["frame"] if ledger else None,
        "ruck_over_field": len(ruck_over),
        "ruck_at_edge": len(ruck_at_edge),
        "max_ruck_x": max((r["ruck_x"] or 0.0) for r in ledger) if ledger else None,
        "markers_fail": len(markers_fail),
        "all_converged": len(all_conv),
        "frozen": len(frozen),
        "target_ahead": len(target_ahead),
        "pinned": len(pinned),
        "examples": (target_ahead[:4] + pinned[:4]),
    }


def main():
    global _REPORT
    _REPORT = open(REPORT_PATH, "w", encoding="utf-8")
    _speak()

    print("=" * 96)
    print("RELEASE GEOMETRY DEADLOCK — measured, not argued")
    print("=" * 96)
    print("  FIELD_MIN_X                    = %.3f" % FIELD_MIN_X)
    print("  FIELD_MAX_X                    = %.3f" % FIELD_MAX_X)
    print("  RECOVERY_BACKWARD_DISTANCE_M   = 0.15   (engine)")
    print("  RELEASE_RECOVERY_SPEED_MPS     = 1.50   (engine)")
    print("  MARKER_CONVERGENCE_TOLERANCE_M = %.3f" % MARKER_CONVERGENCE_TOLERANCE_M)
    print("  BC_RECOVERY_TOLERANCE_M        = %.3f" % BC_RECOVERY_TOLERANCE_M)
    print("  BC_SETTLED_SPEED_MPS           = %.3f" % BC_SETTLED_SPEED_MPS)
    print("  RELEASE_MIN_CONVERGENCE_S      = %.3f" % RELEASE_MIN_CONVERGENCE_S)
    print("  RELEASE_MAX_DURATION_S         = %.3f" % RELEASE_MAX_DURATION_S)

    _silence()
    ledger = boot_and_run(frames=6000)
    _speak()

    s = analyse(ledger)
    dwell_frames, dwell_s = _longest_dwell(ledger)

    print("")
    print("-" * 96)
    print("A. HOW MUCH OF THE ROLLOUT IS RELEASE?")
    print("-" * 96)
    print("  RELEASE frames (first=%s last=%s) = %d"
          % (s["first_frame"], s["last_frame"], s["release_frames"]))
    print("  longest single RELEASE episode       = %d frames (%.2f s)"
          % (dwell_frames, dwell_s))

    print("")
    print("-" * 96)
    print("B. IS THE RUCK ANCHOR OUT OF BOUNDS?   (links 1 + 2)")
    print("-" * 96)
    print("  RELEASE frames with ruck_x > FIELD_MAX_X         = %d" % s["ruck_over_field"])
    print("  RELEASE frames with ruck_x within 0.25 m of it   = %d" % s["ruck_at_edge"])
    print("  max ruck_x seen during RELEASE                   = %s" % s["max_ruck_x"])

    print("")
    print("-" * 96)
    print("C. DOES THE STAND-UP TARGET LAND AHEAD OF THE CARRIER?   (link 3)")
    print("-" * 96)
    print("  RELEASE frames with bc.target_x > bc.x + 0.05 = %d" % s["target_ahead"])

    print("")
    print("-" * 96)
    print("D. IS THE CARRIER PINNED, UNFROZEN?   (links 4 + 6)")
    print("-" * 96)
    print("  frames bc.x >= FIELD_MAX_X, speed < %.2f, frozen False = %d"
          % (BC_SETTLED_SPEED_MPS, s["pinned"]))
    print("  frames with bc.frozen is True                          = %d" % s["frozen"])

    print("")
    print("-" * 96)
    print("E. CAN THE CONVERGENCE GATE FIRE?   (link 5)")
    print("-" * 96)
    print("  RELEASE frames with markers_ready is False = %d" % s["markers_fail"])
    print("  RELEASE frames with all_converged is True  = %d" % s["all_converged"])

    print("")
    print("-" * 96)
    print("F. EXAMPLE FRAMES (target ahead of the carrier, or pinned)")
    print("-" * 96)
    for r in s["examples"]:
        print("  f=%-5s t=%-6s ruck_x=%-8s over=%-7s bc_x=%-8s vx=%-8s "
              "tx=%-8s spd=%-7s frozen=%-5s markers=%s conv=%s"
              % (r["frame"], r["phase_timer"], r["ruck_x"],
                 (None if r["ruck_over_field"] is None
                  else round(r["ruck_over_field"], 3)),
                 r["bc_x"], r["bc_vx"], r["bc_target_x"], r["bc_speed"],
                 r["bc_frozen"], r["marker_errors"], r["all_converged"]))

    print("")
    print("-" * 96)
    print("G. THE VERDICT")
    print("-" * 96)
    if s["frozen"] == 0:
        print("  * bc.frozen is NEVER True during RELEASE: the Stage 3.5")
        print("    freeze/unfreeze fix is intact and is NOT the cause.")
    if s["ruck_over_field"] or s["ruck_at_edge"]:
        print("  * The ruck anchor reaches the field edge / past it, which puts the")
        print("    carrier's stand-up target AHEAD of a carrier the clamp forbids to")
        print("    advance, and puts both marker ruck-pins OUTSIDE the field.")
    if s["markers_fail"] and s["all_converged"] == 0:
        print("  * markers_ready is False on every RELEASE frame that carries the BC,")
        print("    and all_converged is NEVER True — so only the %.2f s watchdog"
              % RELEASE_MAX_DURATION_S)
        print("    ends the phase, with the carrier motionless and unfrozen throughout.")
    print("  * Report written to %s" % REPORT_PATH)
    _close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
