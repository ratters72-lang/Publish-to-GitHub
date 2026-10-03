"""Headless END-TO-END verifier for the fullback RECOVERY locomotion (2026-09-21).

``verify_fullback_recover_contract.py`` proves the *contract* (state tables,
predicate matrix, writer behaviour) with stubs.  This verifier proves the
*feature actually runs in the real engine* and that its documented rollback
lever really switches it off:

  PASS 1 — lever ON (``RLM_FULLBACK_RECOVER`` unset): boot the real engine the
           same way ``verify_fullback_continuity.py`` does, run the seeded
           rollout, and assert
             A. ``fullback_recovery_active`` was consulted at least once, and
             B. it returned True at least once,
             C. the tagged actor's POST-PIPELINE ``movement_state`` is
                ``MovementState.RECOVER`` on at least one frame — i.e. no later
                writer (MMDL / line demotion / pursuit) clobbers the stamp, and
             D. the anchor debt genuinely exceeded ``FB_RECOVER_DEBT_M`` at
                least once, so the run is not vacuously passing, and
             E. at least one RECOVER frame ran FASTER than
                ``FB_MAX_APPROACH_SPEED_MPS`` — proving the sweeper approach
                cap was bypassed live, not just in the stub tests, and
             F. NO RECOVER frame ran faster than the ALIGNED pace ceiling
                (``FB_RECOVER_SPEED_SCALE`` x the actor's own
                ``max_speed_mps``, which is the base the shared movement-state
                model itself scales) and none exceeded the unified defensive
                ceiling either, so the recovery is genuinely quick but never a
                sprint.  (2026-09-27: the pace is read from the shared model's
                integrated ``vx``/``vy``.  The legacy ``move_speed`` field is
                no longer written by any fullback authority and does not govern
                motion, so policing it measured the wrong number.)

  PASS 2 — lever OFF (``RLM_FULLBACK_RECOVER=0``): the same seeded rollout
           must produce ZERO RECOVER frames while still consulting the
           predicate (so the A/B is not vacuous).

Both passes are in-process: ``fullback_recovery_armed`` reads the environment
on every call, so the lever can be flipped between passes without a restart.
"""
import io
import math
import os

os.environ["SDL_VIDEODRIVER"] = "dummy"

import sys

sys.path.insert(0, ".")

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
from player.movement_states import MovementState
from game.defence.defence_lvl_1 import fullback_role as fb

SEED = 20260827
MAX_FRAMES = 6000
DT = 1.0 / 60.0

# ── POST-FRAME CONTACT IMPULSES (2026-09-27) ──
# The shared movement-state model clamps a body to
# ``max_speed_mps * params["max_speed_scale"]`` at INTEGRATION time, but this
# verifier samples AFTER ``collision_system.resolve_all`` has run for the frame
# -- and that system applies separation impulses DIRECTLY to ``body.vx`` /
# ``body.vy`` (game/physics/collision_system.py).  A RECOVER frame that ends the
# frame in contact therefore reads a fraction of a per-cent above the aligned
# row without the recovery PACE POLICY having leaked at all (observed live:
# 4.79 m/s against a 4.75 m/s row, +0.8%).  This band admits that impulse while
# staying far below any genuine regression: a RECOVER that had been wrongly
# granted the CHASE row would land near 2x the aligned pace, and one granted the
# unified ceiling would land at ~1.75x.
RECOVER_PACE_TOLERANCE = 0.05

# The engine's per-frame debug chatter (freeze audit, tackle gate, ruck pin)
# is enormous; swallow it during the rollouts and restore the real stdout for
# the result lines the CI parser reads.
_REAL_STDOUT = sys.stdout


def _quiet():
    sys.stdout = io.TextIOWrapper(
        open(os.devnull, "wb"), encoding="utf-8", errors="replace"
    )


def _loud():
    sys.stdout = _REAL_STDOUT


def _run_rollout():
    """One seeded rollout; returns the recovery-locomotion telemetry."""
    import random

    random.seed(SEED)

    # Some engine paths tear the display surface down at the end of a rollout
    # (``Field.__init__`` does ``pygame.image.load(...).convert()``, which needs
    # an initialised display), so the surface is re-created for every pass.
    if not pygame.display.get_init():
        pygame.display.init()
    pygame.display.set_mode((1, 1), pygame.NOFRAME)

    collision_system = CollisionSystem()
    field = Field(1920, 1080)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball
    open_play = OpenPlayEngine(world)
    world.open_play = open_play
    world.open_play_engine = open_play
    kickoff_engine = KickoffEngine(world, ball, field)

    telemetry = {
        "predicate_calls": 0,
        "predicate_true": 0,
        "recover_frames": 0,
        "recover_over_cap_frames": 0,
        "recover_over_aligned_frames": 0,
        "recover_over_unified_frames": 0,
        "recover_max_speed": 0.0,
        "recover_max_aligned_ceiling": 0.0,
        "max_debt_m": 0.0,
        "armed_frames": 0,
        "active_frames": 0,
    }

    original_predicate = fb.fullback_recovery_active

    def _spy(world, defender, anchor_x, anchor_y):
        telemetry["predicate_calls"] += 1
        debt = math.hypot(
            float(getattr(defender, "x", 0.0)) - float(anchor_x),
            float(getattr(defender, "y", 0.0)) - float(anchor_y),
        )
        if debt > telemetry["max_debt_m"]:
            telemetry["max_debt_m"] = debt
        verdict = original_predicate(world, defender, anchor_x, anchor_y)
        if verdict:
            telemetry["predicate_true"] += 1
        return verdict

    fb.fullback_recovery_active = _spy
    try:
        kickoff_engine.reset_for_kickoff()
        apply_receiving_kickoff_formation(world, field)
        apply_kicking_team_formation(world, field)
        world.ball_owner = "NONE"
        world.ball_carrier = None
        world.ball.possessed_by = None
        kickoff_engine.kickoff()

        for frame in range(1, MAX_FRAMES + 1):
            world.frame_count = frame
            ball.update(DT)
            if world.phase == GamePhase.KICKOFF:
                kickoff_engine.update(DT)
                check_for_catch(world, ball, DT)
            elif world.phase == GamePhase.CATCH:
                if world.ball_carrier is not None:
                    world.open_play.start(world.ball_carrier)
                world.phase = GamePhase.OPEN_PLAY
            elif world.phase == GamePhase.OPEN_PLAY:
                open_play.update(DT)
                if not world.tes_active:
                    collision_system.resolve_all(world.players, world=world)
            else:
                continue

            active = [p for p in world.defending_team
                      if getattr(p, "fullback_role_active", False)]
            if len(active) != 1:
                continue
            actor = active[0]
            if getattr(actor, "tes_attached", False):
                continue
            telemetry["active_frames"] += 1
            if getattr(world, "fullback_recover_allowed", False):
                telemetry["armed_frames"] += 1
            if getattr(actor, "movement_state", None) == MovementState.RECOVER:
                telemetry["recover_frames"] += 1
                # ── PACE MEASURED OFF THE SHARED MODEL'S OWN OUTPUT ──
                # (2026-09-27) This used to read ``actor.move_speed``.  That
                # field is a LEGACY of the retired bespoke writers: the shared
                # movement-state module mentions it only in comments and neither
                # reads nor writes it, so on a RECOVER frame it held whatever an
                # unrelated writer last left there (observed live: the actor's
                # raw ``max_speed_mps``, ~9.5 m/s).  Since the re-basing, the
                # fullback's REAL pace is the shared state machine's own
                # velocity -- ``apply_fullback_locomotion`` ->
                # ``apply_movement_state``, which clamps ``hypot(vx, vy)`` to
                # ``max_speed_mps * params["max_speed_scale"]``.  That is the
                # number this verifier must police.
                _vx = float(getattr(actor, "vx", 0.0) or 0.0)
                _vy = float(getattr(actor, "vy", 0.0) or 0.0)
                speed = math.hypot(_vx, _vy)
                if speed > fb.FB_MAX_APPROACH_SPEED_MPS:
                    telemetry["recover_over_cap_frames"] += 1
                # ALIGNED PACE (2026-09-22, re-based 2026-09-27): the shared
                # model scales the actor's OWN ``max_speed_mps`` by the RECOVER
                # row, so THAT -- not the post-facing defensive ceiling -- is the
                # state's authored pace.  The unified defensive ceiling is
                # asserted separately, as the outer bound, below.
                _shared_base = float(getattr(actor, "max_speed_mps", 0.0) or 0.0)
                _aligned_ceiling = _shared_base * fb.FB_RECOVER_SPEED_SCALE
                if speed > telemetry["recover_max_speed"]:
                    telemetry["recover_max_speed"] = speed
                if _aligned_ceiling > telemetry["recover_max_aligned_ceiling"]:
                    telemetry["recover_max_aligned_ceiling"] = _aligned_ceiling
                if (
                    _aligned_ceiling > 0.0
                    and speed > _aligned_ceiling * (1.0 + RECOVER_PACE_TOLERANCE)
                ):
                    telemetry["recover_over_aligned_frames"] += 1
                _unified = float(
                    getattr(actor, "max_defensive_speed_mps", 0.0) or 0.0
                )
                if _unified > 0.0 and speed > _unified + 1e-6:
                    telemetry["recover_over_unified_frames"] += 1
    finally:
        fb.fullback_recovery_active = original_predicate

    # NOTE: pygame is intentionally NOT torn down here — this function is run
    # twice in-process for the A/B, and pygame.quit() after the first pass
    # leaves the second pass without a display surface.
    return telemetry


def _check(label, condition, failures):
    status = "PASS" if condition else "FAIL"
    print(f"  {status}  {label}")
    if not condition:
        failures.append(label)


def main():
    failures = []

    _quiet()
    saved_lever = os.environ.pop("RLM_FULLBACK_RECOVER", None)
    on = _run_rollout()
    os.environ["RLM_FULLBACK_RECOVER"] = "0"
    off = _run_rollout()
    os.environ.pop("RLM_FULLBACK_RECOVER", None)
    if saved_lever is not None:
        os.environ["RLM_FULLBACK_RECOVER"] = saved_lever
    pygame.quit()
    _loud()

    print("FULLBACK RECOVERY LOCOMOTION — LIVE END-TO-END (2026-09-21)")
    print("=" * 68)
    print("  lever ON : " + repr(sorted(on.items())))
    print("  lever OFF: " + repr(sorted(off.items())))
    print("-" * 68)

    _check("the rollout produced fullback-active frames", on["active_frames"] > 0,
           failures)
    _check("the recovery predicate is consulted live",
           on["predicate_calls"] > 0, failures)
    _check("the arming seam actually arms the lever",
           on["armed_frames"] > 0, failures)
    _check(
        f"the anchor debt exceeds the arm band ({fb.FB_RECOVER_DEBT_M} m) live "
        f"(max {on['max_debt_m']:.1f} m)",
        on["max_debt_m"] > fb.FB_RECOVER_DEBT_M,
        failures,
    )
    _check("the predicate returns True at least once",
           on["predicate_true"] > 0, failures)
    _check("RECOVER survives to the END of at least one frame",
           on["recover_frames"] > 0, failures)
    _check(
        "RECOVER runs above the sweeper approach cap on some frame "
        "(the cap bypass is live)",
        on["recover_over_cap_frames"] > 0,
        failures,
    )
    _check(
        f"NO RECOVER frame exceeds the ALIGNED pace ceiling live "
        f"(fastest integrated frame {on['recover_max_speed']:.2f} m/s vs the "
        f"{on['recover_max_aligned_ceiling']:.2f} m/s ceiling = "
        f"{fb.FB_RECOVER_SPEED_SCALE} x the actor's own max_speed_mps, "
        f"{RECOVER_PACE_TOLERANCE:.0%} band for post-frame contact impulses)",
        on["recover_over_aligned_frames"] == 0,
        failures,
    )
    _check(
        "NO RECOVER frame exceeds the unified defensive ceiling "
        "(the shared model's outer bound)",
        on["recover_over_unified_frames"] == 0,
        failures,
    )

    _check("the rollback lever still consults the predicate (A/B not vacuous)",
           off["predicate_calls"] > 0, failures)
    _check(
        "RLM_FULLBACK_RECOVER=0 produces ZERO RECOVER frames "
        "(rollback lever verified live)",
        off["recover_frames"] == 0,
        failures,
    )
    _check(
        "the debt range under the lever OFF is comparable (same rollout shape)",
        off["max_debt_m"] > fb.FB_RECOVER_DEBT_M,
        failures,
    )

    print("=" * 68)
    if failures:
        print(f"RESULT: FAIL ({len(failures)})")
        for label in failures:
            print(f"  - {label}")
        return 1
    print("RESULT: PASS")
    print("  - the recovery locomotion runs in the real engine and survives")
    print("    every later writer to the end of the frame")
    print("  - the documented A-B lever switches it off end to end")
    return 0


if __name__ == "__main__":
    sys.exit(main())
