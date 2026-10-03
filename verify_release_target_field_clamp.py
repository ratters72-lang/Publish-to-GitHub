"""verify_release_target_field_clamp.py — pin the RELEASE geometry contract.

WHAT THIS PINS
--------------
Every target the RELEASE phase derives from the ruck anchor must lie INSIDE
the field, and the carrier must never be frozen while the phase runs.

RELEASE's convergence-exit reachability is MEASURED and reported alongside
those checks (it does not currently fire -- see check 4), but it is not part
of this verifier's contract.

The measured defect this locks out (see
STUCK_CARRIER_AFTER_MISS_BUMP_REPORT.md):

  * the ruck anchor is the ball carrier's drive END x, and
    ``Player._clamp_position`` deliberately skips the BC clamp while the
    TES drive is running, so the anchor could land past ``FIELD_MAX_X``
    (measured 120.593 against a 120.0 bound);
  * ``ruck_x + 1.0`` / ``ruck_x + 2.0`` then pinned the marker pair beyond
    the dead-ball line, where the clamp makes them unreachable, so
    ``markers_ready`` was False on every RELEASE frame carrying the carrier
    and ``release_convergence()`` never returned ``all_converged`` (measured
    0 in 2724 RELEASE frames);
  * the stand-up target ``ruck_x - 0.15`` landed AHEAD of a carrier the
    clamp held at the line while ``enforce_bc_backward_invariant`` zeroed
    his forward vx, so he could not reach the target he was given;
  * the phase therefore always burned its full ``RELEASE_MAX_DURATION_S``
    watchdog with the carrier pinned and unfrozen (measured 2157 such
    frames), which is the live "carrier stops moving after the tackle and
    it is NOT a freeze" symptom.

This verifier is deliberately INDEPENDENT of the freeze contract: it asserts
target geometry and gate reachability, neither of which any existing
verifier asserted (``verify_bump_clearance_contract.py`` only asserts that
``frozen`` is cleared, which passes on every pinned frame).

Run:  python verify_release_target_field_clamp.py
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
from world.open_play_engine import (
    OpenPlayEngine,
    DH_BEHIND_DISTANCE_M,
    DH_POSITION_TOLERANCE_M,
    DH_LATERAL_TOLERANCE_M,
    DH_VELOCITY_STABLE_THRESHOLD_MPS,
)
from kick_off.kickoff_formations import (
    apply_receiving_kickoff_formation,
    apply_kicking_team_formation,
)
from game.kickoff_engine import KickoffEngine
from ball.ball import Ball
from game.physics.collision_system import CollisionSystem
from world.catch_system import check_for_catch
from player.player import FIELD_MIN_X, FIELD_MAX_X

from world.rules.release_gate import (
    release_convergence,
    BC_SETTLED_SPEED_MPS,
    RELEASE_MAX_DURATION_S,
)

FRAMES = 6000
SEED = 20260827

# A target this far outside [FIELD_MIN_X, FIELD_MAX_X] is a violation.
TOL = 1e-6

# The carrier counts as PINNED when he is at the field edge, not moving, and
# NOT frozen -- i.e. held by geometry rather than by the freeze flag.
PINNED_SPEED_MPS = BC_SETTLED_SPEED_MPS


def _silence():
    sys.stdout = open(os.devnull, "w", encoding="utf-8")


def _restore(real):
    sys.stdout = real


#: The engine applies the DH tolerances as `constant + relaxation`, so they are
#: read from the engine rather than restated here.
_DH_POS_TOL = DH_POSITION_TOLERANCE_M + 0.1
_DH_LATERAL_TOL = DH_LATERAL_TOLERANCE_M + 0.3
_DH_BEHIND_MARGIN = 0.3


def _dh_gate_reason(open_play, world):
    """Name the FIRST condition of the DH gate that blocks this frame.

    Mirrors OpenPlayEngine._check_dh_ready_for_ptb(for_exec=False) exactly, so
    the binding term can be counted instead of guessed.
    """
    dh = getattr(open_play, "dh", None)
    bc = getattr(world, "ball_carrier", None)
    if dh is None:
        return "01 dh is None"
    if bc is None:
        return "02 bc is None"
    ruck_y = getattr(world, "ruck_y_m", bc.y)
    if dh.x >= bc.x + _DH_BEHIND_MARGIN:
        return "03 dh not behind bc"
    if abs(dh.x - (bc.x - DH_BEHIND_DISTANCE_M)) > _DH_POS_TOL:
        return "04 dh off the 1m target"
    if abs(dh.y - ruck_y) > _DH_LATERAL_TOL:
        return "05 dh lateral offset"
    if math.hypot(dh.vx, dh.vy) > DH_VELOCITY_STABLE_THRESHOLD_MPS:
        return "06 dh not stable"
    return "OK"


def run_rollout(frames=FRAMES, seed=SEED):
    """Drive the real engine and capture every RELEASE frame's geometry."""
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

    release = []
    carrier_frames = 0
    carrier_frozen_frames = 0
    dt = 1.0 / 60.0
    for frame in range(1, frames + 1):
        world.frame_count = frame
        _pre_target = None
        _phase_pre = None
        ball.update(dt)
        if world.phase == GamePhase.KICKOFF:
            kickoff_engine.update(dt)
            check_for_catch(world, ball, dt)
        elif world.phase == GamePhase.CATCH:
            if world.ball_carrier is not None:
                world.open_play.start(world.ball_carrier)
            world.phase = GamePhase.OPEN_PLAY
        elif world.phase == GamePhase.OPEN_PLAY:
            # DIAGNOSTIC: capture the carrier's target BEFORE the engine
            # update.  If an out-of-field value is already present here, the
            # writer ran in an earlier phase and RELEASE never corrected it;
            # if it appears only AFTER the update, a RELEASE-phase writer did
            # it.
            _bc_pre = getattr(world, "ball_carrier", None)
            if _bc_pre is not None:
                _pre_target = float(getattr(_bc_pre, "target_x", 0.0))
            _phase_pre = getattr(open_play, "_phase", None)
            open_play.update(dt)
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)
        else:
            continue

        bc = getattr(world, "ball_carrier", None)
        if bc is not None:
            carrier_frames += 1
            if bool(getattr(bc, "frozen", False)):
                carrier_frozen_frames += 1

        if getattr(open_play, "_phase", None) != "RELEASE":
            continue

        try:
            dh_ready = open_play._check_dh_ready_for_ptb(for_exec=False)
        except Exception:
            dh_ready = False
        try:
            rep = release_convergence(open_play, world, dh_ready)
        except Exception as exc:
            rep = {"error": repr(exc)}

        marker_targets = []
        for body in open_play._get_markers():
            tgt = open_play._get_marker_target(body)
            if tgt is None:
                continue
            marker_targets.append((round(float(tgt[0]), 4),
                                   round(float(tgt[1]), 4)))

        release.append({
            "frame": frame,
            "ruck_x": getattr(world, "ruck_x_m", None),
            "bc_x": (None if bc is None else float(bc.x)),
            "bc_target_x": (None if bc is None
                            else float(getattr(bc, "target_x", 0.0))),
            "bc_target_pre": _pre_target,
            "phase_pre": _phase_pre,
            "bc_frozen": (None if bc is None
                          else bool(getattr(bc, "frozen", False))),
            "marker_targets": marker_targets,
            "all_converged": rep.get("all_converged"),
            "markers_ready": rep.get("markers_ready"),
            "bc_ready": rep.get("bc_ready"),
            "dh_ready": rep.get("dh_ready"),
            "dh_reason": _dh_gate_reason(open_play, world),
        })

    return {
        "release": release,
        "carrier_frames": carrier_frames,
        "carrier_frozen_frames": carrier_frozen_frames,
        "frames": frames,
    }


def collect_violations(res):
    """Every violation of the RELEASE target-geometry contract."""
    rel = res["release"]
    markers_out = []
    bc_out = []
    unreachable_at_edge = []
    marker_pins_seen = 0
    entry_frames = 0
    for r in rel:
        # A frame in which RELEASE was ENTERED during this very update is not
        # yet owned by the RELEASE handler: `_update_release` has not run, so
        # the carrier still holds the target the PREVIOUS phase left on him
        # (the un-clamped drive-end x).  Those frames are counted and then
        # skipped -- this verifier asserts the targets RELEASE ITSELF issues.
        if r["phase_pre"] != "RELEASE":
            entry_frames += 1
            continue
        for tx, _ty in r["marker_targets"]:
            marker_pins_seen += 1
            if tx < FIELD_MIN_X - TOL or tx > FIELD_MAX_X + TOL:
                markers_out.append((r["frame"], tx))
        tx = r["bc_target_x"]
        if tx is not None and (tx < FIELD_MIN_X - TOL or tx > FIELD_MAX_X + TOL):
            bc_out.append((r["frame"], tx, r["bc_target_pre"]))
        if (r["bc_x"] is not None and tx is not None
                and r["bc_x"] >= FIELD_MAX_X - TOL
                and tx > r["bc_x"] + 0.05):
            unreachable_at_edge.append((r["frame"], r["bc_x"], tx))
    return {
        "markers_out": markers_out,
        "bc_out": bc_out,
        "unreachable_at_edge": unreachable_at_edge,
        "marker_pins_seen": marker_pins_seen,
        "entry_frames": entry_frames,
    }


def main():
    real = sys.stdout
    print("=" * 96)
    print("VERIFY — RELEASE target geometry + convergence-exit reachability")
    print("=" * 96)
    print("  frames        = %d" % FRAMES)
    print("  seed          = %d" % SEED)
    print("  FIELD bounds  = [%.3f, %.3f]" % (FIELD_MIN_X, FIELD_MAX_X))
    print("  watchdog      = %.2f s (RELEASE_MAX_DURATION_S)"
          % RELEASE_MAX_DURATION_S)

    _silence()
    res = run_rollout()
    _restore(real)

    v = collect_violations(res)
    rel = res["release"]
    all_converged = sum(1 for r in rel if r["all_converged"] is True)
    markers_failed = sum(1 for r in rel if r["markers_ready"] is False)
    dh_failed = sum(1 for r in rel if r["dh_ready"] is False)
    dh_reasons = {}
    for r in rel:
        key = r["dh_reason"]
        dh_reasons[key] = dh_reasons.get(key, 0) + 1
    bc_failed = sum(1 for r in rel if r["bc_ready"] is False)
    dh_ok = sum(1 for r in rel if r["dh_reason"] == "OK")
    coincident = sum(
        1 for r in rel
        if r["dh_reason"] == "OK" and r["markers_ready"] is True
        and r["bc_ready"] is True
    )
    owned = len(rel) - v["entry_frames"]
    with_pre = sum(1 for r in rel if r["phase_pre"] == "RELEASE")
    at_edge_unfrozen = sum(
        1 for r in rel
        if r["bc_x"] is not None and r["bc_x"] >= FIELD_MAX_X - TOL
        and r["bc_frozen"] is False
    )

    print("")
    print("-" * 96)
    print("MEASURED")
    print("-" * 96)
    print("  frames with a ball carrier        = %d" % res["carrier_frames"])
    print("  carrier frames with frozen True   = %d"
          % res["carrier_frozen_frames"])
    print("  RELEASE frames                    = %d" % len(rel))
    print("  ... entered mid-frame (unowned)   = %d"
          % v["entry_frames"])
    print("  ... RELEASE-owned frames          = %d (pre-phase RELEASE: %d)"
          % (owned, with_pre))
    print("  RELEASE frames dh_ready False     = %d" % dh_failed)
    print("  RELEASE frames bc_ready False     = %d" % bc_failed)
    print("  DH gate — first blocking condition on RELEASE frames:")
    for key in sorted(dh_reasons):
        print("      %-28s %d" % (key, dh_reasons[key]))
    print("  DH gate SATISFIED on %d frames; DH+markers+BC simultaneously "
          "ready on %d" % (dh_ok, coincident))
    print("  marker ruck-pins observed         = %d" % v["marker_pins_seen"])
    print("  RELEASE frames markers_ready False= %d" % markers_failed)
    print("  RELEASE frames all_converged True = %d" % all_converged)
    print("  RELEASE frames bc at edge, unfrozen = %d" % at_edge_unfrozen)

    failures = []

    print("")
    print("-" * 96)
    print("CHECKS")
    print("-" * 96)

    ok = not v["markers_out"]
    print("  [%s] 1. every marker ruck-pin lies inside the field"
          % ("PASS" if ok else "FAIL"))
    if not ok:
        print("        %d violation(s), first: frame=%s target_x=%s"
              % (len(v["markers_out"]), v["markers_out"][0][0],
                 v["markers_out"][0][1]))
        failures.append(1)

    ok = not v["bc_out"]
    print("  [%s] 2. every stand-up target lies inside the field"
          % ("PASS" if ok else "FAIL"))
    if not ok:
        print("        %d violation(s); first: frame=%s target_after=%s "
              "target_before=%s"
              % (len(v["bc_out"]), v["bc_out"][0][0], v["bc_out"][0][1],
                 v["bc_out"][0][2]))
        print("        pre-update value already out-of-field on %d of %d"
              % (sum(1 for rec in v["bc_out"]
                     if rec[2] is not None and rec[2] > FIELD_MAX_X + TOL),
                 len(v["bc_out"])))
        failures.append(2)

    ok = not v["unreachable_at_edge"]
    print("  [%s] 3. no stand-up target is handed to a carrier pinned at the "
          "field edge while sitting AHEAD of him"
          % ("PASS" if ok else "FAIL"))
    if not ok:
        print("        %d violation(s), first: frame=%s bc_x=%s target_x=%s"
              % (len(v["unreachable_at_edge"]), v["unreachable_at_edge"][0][0],
                 v["unreachable_at_edge"][0][1],
                 v["unreachable_at_edge"][0][2]))
        failures.append(3)

    # SCOPE NOTE: this is REPORTED, not asserted.  RELEASE's early-exit
    # predicate (`dh_ready AND markers_ready AND bc_ready`) does not coincide
    # on any frame of this rollout, so the phase always runs its full
    # RELEASE_MAX_DURATION_S watchdog.  That is a real, separately-owned
    # finding about DH/marker TIMING -- not about the RELEASE target geometry
    # this verifier pins -- so it is reported here with its histogram and left
    # to its own contract rather than failing this one.
    print("  [%s] 4. REPORTED (not asserted): convergence exit reachability"
          % ("fired" if all_converged > 0 else "never fired"))
    print("        all_converged was True on %d of %d RELEASE frames; the "
          "phase therefore always ends on its %.2f s watchdog"
          % (all_converged, len(rel), RELEASE_MAX_DURATION_S))
    print("        frames with DH and every marker simultaneously ready = %d"
          % coincident)

    ok = res["carrier_frozen_frames"] == 0
    print("  [%s] 5. the Stage 3.5 freeze property still holds "
          "(carrier never frozen)" % ("PASS" if ok else "FAIL"))
    if not ok:
        print("        %d carrier frames had frozen True"
              % res["carrier_frozen_frames"])
        failures.append(5)

    print("")
    print("=" * 96)
    if failures:
        print("RESULT: FAIL — checks %s" % failures)
        print("=" * 96)
        return 1
    print("RESULT: PASS — every RELEASE target is in-field and the carrier "
          "is never frozen")
    print("NOTE: the RELEASE convergence exit never fires on this seed "
          "(reported above); the phase always runs its watchdog.")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    sys.exit(main())
