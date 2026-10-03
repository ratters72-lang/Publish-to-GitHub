"""probe_stage2_depth_delivery.py — is the authority's command DELIVERED?

THE STAGE-2 DELIVERY PROBE.  The companion to
``probe_stage2_depth_discharge.py``: that probe measures the OUTCOME (debt
discharge); this one measures the MECHANISM (does the body actually move at
the speed the authority commanded?).

It exists because the first Stage-2 wiring was measured as NOMINAL: the
authority stamped 8.29 m/s and the body delivered 0.67 m/s -- 8.1% of
command -- because it commanded through the smooth-damp ``DRIFT`` port.  A
convergence law cannot discharge anything through an integrator that
throttles its command to a twelfth, and the outcome A/B alone could not say
which of the two was at fault.  This probe makes that distinction directly.
It is the regression guard for the integrator path: if a future change puts
the authority back on a smooth-damped state, the delivery ratio collapses
and this probe says so in one number.

In-run measurement (no arm comparison): boots with the authority ON and, for
every authority-granted body-frame, records

    debt_m            the Stage-1 debt at measurement time
    commanded_mps     the move_speed the authority stamped
    achieved_vx       the body's live vx after Player.update integrated
    dx_frame          the X the body actually gained this frame / dt

If `achieved` is a small fraction of `commanded`, the command is being
throttled by the integrator (the known smooth-damp trap) and the convergence
law cannot bite no matter how large the gain.

MEASURED (this probe, 2026-09-18):
    commanded through DRIFT (smooth damp) : 8.287 -> 0.673 m/s =  8.1%
    commanded through the accel-lerp state: 8.188 -> 6.332 m/s = 77.3%
with debt p50 falling 2.77 m -> 1.39 m once the command was delivered, i.e.
the population moves from "parked in debt" to "discharging".

Run:  python probe_stage2_depth_delivery.py
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import contextlib
import io
import sys

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

from game.defence.defence_lvl_1 import tes_depth_obligation as tdo
from game.defence.defence_lvl_1 import tes_depth_authority as tda

DT = 1.0 / 60.0
MAX_FRAMES = 3000


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
    return world, ball, open_play, kickoff_engine, collision_system, field


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def main():
    buf = io.StringIO()
    prev_x = {}
    rows = []
    states = {}

    with contextlib.redirect_stdout(buf):
        (world, ball, open_play, kickoff_engine,
         collision_system, field) = boot_world()
        open_play.def_tuners.tes_depth_authority_enabled = True

        for _ in range(MAX_FRAMES):
            world.frame_count += 1
            ball.update(DT)

            if world.phase == GamePhase.KICKOFF:
                kickoff_engine.update(DT)
                check_for_catch(world, ball, DT)
                continue
            if world.phase == GamePhase.CATCH:
                if world.ball_carrier is not None:
                    open_play.start(world.ball_carrier)
                world.phase = GamePhase.OPEN_PLAY
                continue
            if world.phase != GamePhase.OPEN_PLAY:
                continue

            open_play.update(DT)
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)

            if not tdo.in_tes_depth_chamber(world):
                prev_x.clear()
                continue

            m = getattr(open_play, "defence_manager", None)
            cur_list = getattr(m, "_tes_depth_authority_last", None)
            tuners = tdo._resolve_tuners(world)

            # Only trust the manager list on a frame where the TES drive
            # writer actually ran (the drive sub-states).
            drive_ran = str(getattr(open_play, "_phase", "")).upper() in (
                "TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE",
            )
            granted = list(cur_list or []) if drive_ran else []

            for d in granted:
                if d is None:
                    continue
                target = tdo.tes_depth_target_x(world, d, tuners)
                if target is None:
                    continue
                debt = tdo.tes_depth_debt_m(world, d, target_x=target)
                cmd = _f(getattr(d, "move_speed", 0.0))
                vx = _f(getattr(d, "vx", 0.0))
                x = _f(getattr(d, "x", 0.0))
                px = prev_x.get(id(d))
                dx_rate = ((x - px) / DT) if px is not None else None
                ms = getattr(d, "movement_state", None)
                states[getattr(ms, "name", str(ms))] = (
                    states.get(getattr(ms, "name", str(ms)), 0) + 1
                )
                rows.append({
                    "debt": debt, "cmd": cmd, "vx": vx,
                    "dx_rate": dx_rate,
                    "sign_debt": (x - float(target)),
                })
                prev_x[id(d)] = x

    print("=" * 74)
    print("STAGE-2 DELIVERY DIAGNOSTIC (authority ON, in-run)")
    print("=" * 74)
    if not rows:
        print("no granted body-frames observed.")
        return

    n = len(rows)
    mean_cmd = sum(r["cmd"] for r in rows) / n
    mean_vx = sum(abs(r["vx"]) for r in rows) / n
    dx_rows = [r for r in rows if r["dx_rate"] is not None]
    mean_dx = (sum(abs(r["dx_rate"]) for r in dx_rows) / len(dx_rows)
               if dx_rows else 0.0)
    thin = [r for r in rows if r["cmd"] > 0.01 and abs(r["vx"]) < 0.5 * r["cmd"]]
    tiny = [r for r in rows if r["cmd"] > 0.01 and abs(r["vx"]) < 0.1 * r["cmd"]]

    print(f"\n  granted body-frames           : {n}")
    print(f"  mean COMMANDED speed (m/s)    : {mean_cmd:.3f}")
    print(f"  mean |vx| ACHIEVED (m/s)      : {mean_vx:.3f}")
    print(f"  mean |dx|/dt ACROSS FRAME     : {mean_dx:.3f}")
    print(f"  delivery ratio (achieved/cmd) : "
          f"{(mean_vx / mean_cmd) if mean_cmd else 0.0:.3f}")
    print(f"  frames < 50% of command       : {len(thin)} "
          f"({100.0 * len(thin) / n:.1f}%)")
    print(f"  frames < 10% of command       : {len(tiny)} "
          f"({100.0 * len(tiny) / n:.1f}%)")
    print(f"\n  movement_state histogram      : {states}")

    debts = sorted(r["debt"] for r in rows)
    print(f"  debt p50 / p90 / max (m)      : "
          f"{debts[n // 2]:.2f} / {debts[int(n * 0.9)]:.2f} / {debts[-1]:.2f}")

    # Signed-debt composition: ahead vs behind the role depth.
    ahead = sum(1 for r in rows if r["sign_debt"] > 0.0)
    behind = n - ahead
    print(f"  ahead of role depth           : {ahead} "
          f"({100.0 * ahead / n:.1f}%)")
    print(f"  behind role depth             : {behind} "
          f"({100.0 * behind / n:.1f}%)")

    # Sub-50% frames: what do they look like?
    if thin:
        tc = sum(r["cmd"] for r in thin) / len(thin)
        tv = sum(abs(r["vx"]) for r in thin) / len(thin)
        td = sum(r["debt"] for r in thin) / len(thin)
        print(f"\n  [under-delivered frames] cmd={tc:.3f} vx={tv:.3f} "
              f"debt={td:.2f}")


if __name__ == "__main__":
    try:
        main()
    finally:
        pygame.quit()
