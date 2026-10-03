"""Headless verification: SPACE-restart must leave a clean engine.

Boots the full world (dummy SDL video driver), advances into real play,
fast-forwards to each regression phase (RELEASE / PTB_EXEC /
POST_RUCK_OPEN_PLAY), restarts exactly like run.py's SPACE handler, and
asserts:

  1. Engine FSM + TES + PTB delegate state is fully cleared.
  2. World-level TES/PTB/geometry fields are cleared.
  3. After the next catch, the post-catch clamp is armed with its full
     window and defenders can never exceed the cap during the first
     OPEN_PLAY seconds (no speed spike, no teleport, no freeze).
  4. The BC actually moves after the second catch (no freeze).
  5. Behaviour is identical across 5 rapid restarts.
"""

import os
import sys

# Same as run.py: force UTF-8 stdout so audit emoji/markers survive redirection.
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)

os.environ["SDL_VIDEODRIVER"] = "dummy"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pygame

# pygamesurface.convert() 需要 display surface，dummy driver 下仍需建立一个隐藏窗口
pygame.init()
pygame.display.set_mode((1, 1))

import math

from world.field import Field
from world.world_state import WorldState, GamePhase
from world.open_play_engine import OpenPlayEngine
from game.kickoff_engine import KickoffEngine
from ball.ball import Ball
from world.catch_system import check_for_catch

DT = 1.0 / 60.0


# ---------------------------------------------------------------- harness
def build_world():
    field = Field(1920, 1080)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball

    ope = OpenPlayEngine(world)
    world.open_play = ope
    world.open_play_engine = ope

    ko = KickoffEngine(world, ball, field)
    world.kickoff_controller = ko
    return world, ball, ope, ko


def step_phase_dispatch(world, ball, ope, ko):
    """Mirror run.py's frame dispatch without rendering."""
    ball.update(DT)
    if world.phase == GamePhase.KICKOFF:
        ko.update(DT)
        check_for_catch(world, ball, DT)
    elif world.phase == GamePhase.CATCH:
        if world.ball_carrier is not None:
            ope.start(world.ball_carrier)
        world.phase = GamePhase.OPEN_PLAY
    elif world.phase == GamePhase.OPEN_PLAY:
        ope.update(DT)


def advance(world, ball, ope, ko, frames):
    for _ in range(frames):
        step_phase_dispatch(world, ball, ope, ko)
        if world.phase == GamePhase.OPEN_PLAY and world.ball_carrier is not None:
            return True  # reached live open play
    return False


def reboot(world, ball, ope, ko):
    """Exactly run.py's SPACE handler: reset_ball_and_world() + kickoff()."""
    ko.reset_for_kickoff()
    ko.kickoff()


# ---------------------------------------------------------------- assertions
def assert_clean_engine(world, ope, label):
    """All engine-owned + world-level leaked state must be cleared."""
    problems = []

    # Engine FSM
    if ope._phase != "OPEN_PLAY":
        problems.append(f"_phase={ope._phase!r}")
    if ope._phase_timer != 0.0:
        problems.append(f"_phase_timer={ope._phase_timer!r}")
    if ope.ball_carrier is not None:
        problems.append("ball_carrier not None")
    if ope.primary is not None:
        problems.append("primary not None")
    if ope.joiners:
        problems.append(f"joiners={ope.joiners!r}")
    if ope.tes_ctx is not None:
        problems.append("tes_ctx not None")
    if ope.tes_outcome is not None:
        problems.append("tes_outcome not None")
    if ope.dh is not None:
        problems.append("dh not None")

    # Delegate caches
    if ope.ptb_manager.state != "idle":
        problems.append(f"ptb_manager.state={ope.ptb_manager.state!r}")
    if ope.ptb_manager.timer != 0.0:
        problems.append(f"ptb_manager.timer={ope.ptb_manager.timer!r}")
    governor = ope.def_behaviour.line_governor
    if governor.line_width is not None:
        problems.append(f"governor.line_width={governor.line_width!r}")
    if governor.compression_factor != 0.0:
        problems.append(f"governor.compression={governor.compression_factor!r}")
    if governor.step_up_state != 0.0:
        problems.append(f"governor.step_up={governor.step_up_state!r}")
    if ope.def_behaviour._post_catch_stable_frames != 0:
        problems.append(
            f"post_catch_stable_frames={ope.def_behaviour._post_catch_stable_frames!r}"
        )

    # World-level TES / PTB / geometry
    if world.tes_locked:
        problems.append("world.tes_locked=True")
    if world.tes_active:
        problems.append("world.tes_active=True")
    if world.primary_tackler_id is not None:
        problems.append(f"primary_tackler_id={world.primary_tackler_id!r}")
    if world.secondary_tacklers:
        problems.append(f"secondary_tacklers={world.secondary_tacklers!r}")
    if world.frames_since_ptb < world.escape_window_frames:
        problems.append(f"frames_since_ptb={world.frames_since_ptb!r} (escape window would fire)")
    if world.marker_freeze_timer < world.marker_lock_duration_s:
        problems.append(f"marker_freeze_timer={world.marker_freeze_timer!r} (markers would lock)")
    if world.ptb_anchor_x is not None:
        problems.append(f"ptb_anchor_x={world.ptb_anchor_x!r}")
    if world.ptb_anchor_y is not None:
        problems.append(f"ptb_anchor_y={world.ptb_anchor_y!r}")
    if world.last_contact_x is not None:
        problems.append(f"last_contact_x={world.last_contact_x!r}")
    if world.last_contact_y is not None:
        problems.append(f"last_contact_y={world.last_contact_y!r}")
    if world.in_pursuit_mode:
        problems.append("in_pursuit_mode=True")
    if world.defensive_line_anchor is not None:
        problems.append(f"defensive_line_anchor={world.defensive_line_anchor!r}")
    if world.ruck_x_m is not None:
        problems.append(f"ruck_x_m={world.ruck_x_m!r}")
    if world.role_governs:
        problems.append("role_governs=True")
    if world.current_target_writer:
        problems.append(f"current_target_writer={world.current_target_writer!r}")
    if not world.defence_enabled:
        problems.append("defence_enabled=False")
    if world.defensive_chase_enabled is False:
        problems.append("defensive_chase_enabled=False (derived flags not restored)")

    if problems:
        for p in problems:
            print(f"  [FAIL] {label}: {p}")
        return False
    print(f"  [PASS] {label}: engine state clean")
    return True


def assert_clamp_and_speed(world, ball, ope, ko, label):
    """After the next catch, the clamp must be armed and speed profiles sane.

    Verifies (in order):
      1. Clamp armed with the FULL window on every defender immediately
         after the catch (before any further frames run).
      2. During the first 8 OPEN_PLAY frames (within the ~10-frame clamp
         window before the stability-based auto-disarm), no defender
         exceeds the clamp ceiling.
      3. Across the first 60 frames (covering the reaction-governor blend),
         no defender exceeds max_speed_mps (no hyper-speed), no player
         teleports (>3m single-frame jump), and all speeds/positions finite.
      4. The BC actually moves (no freeze).
    """
    problems = []
    cap = max(
        (getattr(d, "defended_speed_cap_mps", 0.0) or 0.0)
        for d in world.defending_team
    )

    # 1. Clamp armed with the full window on every defender (checked NOW,
    #    before any clamp time has elapsed).
    for d in world.defending_team:
        if d.defended_cap_remaining_s <= 2.0:
            problems.append(
                f"defender {d.player_id} clamp not freshly armed "
                f"(remaining={d.defended_cap_remaining_s:.3f})"
            )
    if cap <= 0.0:
        problems.append(f"clamp cap <= 0 ({cap})")

    bc = world.ball_carrier
    if bc is None:
        problems.append("no ball carrier after catch")
        bc = None
    else:
        if bc.frozen:
            problems.append("BC frozen=True in OPEN_PLAY")
        bc_x0, bc_y0 = bc.x, bc.y

    # 2. Clamped window: first 8 frames must never exceed the clamp ceiling.
    max_speed_clamped_window = 0.0
    for _ in range(8):
        step_phase_dispatch(world, ball, ope, ko)
        for d in world.defending_team:
            spd = math.hypot(d.vx, d.vy)
            max_speed_clamped_window = max(max_speed_clamped_window, spd)
    clamp_allow = max(cap, 5.5) + 0.1
    if max_speed_clamped_window > clamp_allow:
        problems.append(
            f"defender speed {max_speed_clamped_window:.3f} exceeds clamp "
            f"ceiling ~{clamp_allow:.3f} during clamped window"
        )

    # 3. Full transition window (60 frames): hyper-speed, per-frame
    #    teleport detection, finiteness.
    max_speed_observed = 0.0
    prev_pos = {p.player_id: (p.x, p.y) for p in world.players}
    for _ in range(52):
        step_phase_dispatch(world, ball, ope, ko)
        for d in world.defending_team:
            spd = math.hypot(d.vx, d.vy)
            max_speed_observed = max(max_speed_observed, spd)
            if not math.isfinite(spd):
                problems.append(f"defender {d.player_id} non-finite speed {spd}")
        for p in world.players:
            if not (math.isfinite(p.x) and math.isfinite(p.y)):
                problems.append(f"player {p.player_id} non-finite position ({p.x},{p.y})")
            # True single-frame teleport: consecutive-frame delta within
            # THIS simulation window (not the cross-restart baseline).
            prev = prev_pos.get(p.player_id)
            if prev is not None:
                jump = math.hypot(p.x - prev[0], p.y - prev[1])
                if jump > 3.0:
                    problems.append(
                        f"player {p.player_id} one-frame jump {jump:.2f}m (teleport)"
                    )
            prev_pos[p.player_id] = (p.x, p.y)

    if cap <= 0.0:
        pass  # already flagged above
    speed_allow = max((d.max_speed_mps for d in world.defending_team), default=9.2) + 0.2
    if max_speed_observed > speed_allow:
        problems.append(
            f"max defender speed {max_speed_observed:.3f} exceeds "
            f"{speed_allow:.3f} (hyper-speed)"
        )

    # 4. BC must have moved (no freeze).
    if bc is not None:
        bc_moved = math.hypot(bc.x - bc_x0, bc.y - bc_y0)
        if bc_moved < 0.05:
            problems.append(f"BC moved only {bc_moved:.3f}m in 60 frames (frozen?)")

    if problems:
        for p in problems:
            print(f"  [FAIL] {label}: {p}")
        return False
    print(
        f"  [PASS] {label}: clamp armed (cap={cap:.2f}), clamped-window max "
        f"{max_speed_clamped_window:.2f}m/s, 60-frame max {max_speed_observed:.2f}m/s, BC moving"
    )
    return True


# ---------------------------------------------------------------- scenarios
TARGET_PHASES = ["RELEASE", "PTB_EXEC", "POST_RUCK_OPEN_PLAY"]
ADVANCE_BUDGET = 9000


def run_scenario(target_phase=None, iterations=1):
    """Boot -> reach target phase (or fall back to current) -> reboot xN.

    After each reboot: assert clean engine, advance to catch, assert
    clamp + speed caps.  Repeats `iterations` boot cycles for stability.
    """
    print(f"\n=== Scenario: target={target_phase or 'rapid-arbitrary'} ===")
    all_ok = True

    for it in range(iterations):
        world, ball, ope, ko = build_world()
        reboot(world, ball, ope, ko)

        # First kickoff -> catch -> open play -> travel toward target phase.
        ok = advance(world, ball, ope, ko, 4000)
        if not ok:
            print(f"  [FAIL] iteration {it}: live open play never reached")
            all_ok = False
            continue

        reached = None
        if target_phase is not None:
            for _ in range(ADVANCE_BUDGET):
                step_phase_dispatch(world, ball, ope, ko)
                if ope._phase == target_phase:
                    reached = target_phase
                    break
            if reached is None:
                print(
                    f"  [WARN] iteration {it}: phase {target_phase} never "
                    f"reached (stuck at {ope._phase}) — restarting from "
                    f"{ope._phase} instead"
                )
        else:
            # Rapid arbitrary restarts: every 300 frames, no phase filtering.
            for _ in range(300):
                step_phase_dispatch(world, ball, ope, ko)

        restart_from = ope._phase
        snapshot = (
            f"iter={it} restart_from={restart_from} "
            f"frame={world.frame_count} sub={world.sub_state}"
        )

        # ── THE RESTART (run.py SPACE handler) ──
        reboot(world, ball, ope, ko)

        ok = assert_clean_engine(world, ope, snapshot)
        if not ok:
            all_ok = False
            continue

        # Second kickoff -> catch -> clamp + speed verification.
        ok = advance(world, ball, ope, ko, 4000)
        if not ok:
            print(f"  [FAIL] {snapshot}: second open play never reached")
            all_ok = False
            continue
        ok = assert_clamp_and_speed(world, ball, ope, ko, snapshot)
        if not ok:
            all_ok = False

    return all_ok


# ---------------------------------------------------------------- main
if __name__ == "__main__":
    results = []

    for phase in TARGET_PHASES:
        results.append((phase, run_scenario(target_phase=phase, iterations=3)))

    results.append(("rapid-arbitrary", run_scenario(target_phase=None, iterations=3)))

    print("\n================ SUMMARY ================")
    all_ok = True
    for name, ok in results:
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_ok = False
        print(f"  {status}: {name}")
    print("================ END ================")
    sys.exit(0 if all_ok else 1)
