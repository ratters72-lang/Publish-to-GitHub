"""probe_fullback_lateral_tracking.py — LIVE lateral-tracking telemetry for the
dynamic fullback, run as a BEFORE/AFTER A-B (audit 2026-09-22, cured 2026-09-22).

Companion to ``FULLBACK_LATERAL_TRACKING_AUDIT.md``,
``FULLBACK_LATERAL_OPEN_PLAY_REPORT.md`` and
``verify_fullback_lateral_priority_exclusion.py``.

The verifier proves the NEGATIVE (the ``0.25`` lateral-priority constraint is
not on the sweeper's path) and pins the CURED contract; this probe QUANTIFIES
the cure with the live engine's own numbers, by running the SAME seeded rollout
twice and flipping only the documented rollback lever:

  BEFORE  ``fb.FB_LATERAL_OPEN_PLAY = False``  (== RLM_FB_LATERAL_OPEN_PLAY=0)
          the historical contract: the lateral destination is the
          ``LATERAL_TRACK = 0.6`` rail, and the speed taper is keyed on the
          X error, so lateral ground is throttled without ever being
          consulted.

  AFTER   ``fb.FB_LATERAL_OPEN_PLAY = True``   (the shipped default)
          the lateral destination mirrors the carrier's lane at full weight
          and every speed command is keyed on the 2D distance to that point.

Per run it reports:

  1. THE DESTINATION GAP — per frame ``|bc.y - anchor_y|``: the lateral ground
     the destination itself refuses to ask the sweeper to cover, plus the
     lever-independent width of the historical 0.6 rail.
  2. THE SPEED TAPER — frames where the command is throttled while lateral
     ground is still owed (the audit's starvation signature), the realised
     lateral speed ``|vy|`` on those frames, and the lateral lane residual
     ``|target_y - bc.y|`` the writer actually leaves.
  3. THE STEP-OUT GATE — sweep / recover / chase split.
  4. A/B DELTA — the same metrics side by side with the change.

Output: both rollouts, the per-axis debt statistics, and a periodic sample.

Env: RLM_FB_LATERAL_FRAMES  (default 3000) rollout length per leg,
     RLM_FB_LATERAL_SAMPLES (default 8)  periodic sample rows,
     RLM_FB_LATERAL_WORST   (default 8)  worst-frame rows (BEFORE leg).

Read-only diagnostic: writes nothing, changes no engine state.  The lever it
flips is restored before it returns.
"""
import io
import math
import os
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from game.defence.defence_lvl_1 import fullback_role as fb
from game.defence.defence_lvl_1.anchor_authority import (
    defensive_line_anchor_m as _line_anchor_m,
)
from game.defence.defence_lvl_1.fullback_role import (
    compute_fullback_anchor,
    FB_ARRIVED_PROXIMITY_M,
    FB_DECEL_START_M,
    FB_LATERAL_BREAK_RADIUS_M,
    FB_MAX_APPROACH_SPEED_MPS,
    LATERAL_TRACK,
)

# Lateral ground (m) still owed before a frame counts as "lateral work owed".
LATERAL_OWED_M = 2.0

_REAL_STDOUT = sys.stdout


def _quiet():
    sys.stdout = io.TextIOWrapper(
        open(os.devnull, "wb"), encoding="utf-8", errors="replace"
    )


def _loud():
    sys.stdout = _REAL_STDOUT


def _state_name(p):
    ms = getattr(p, "movement_state", None)
    if ms is None:
        return "-"
    return getattr(ms, "name", None) or str(ms)


def _blank_stats():
    return {
        "frames": 0,
        "active_frames": 0,
        "sweep_frames": 0,        # DRIFT
        "recover_frames": 0,      # RECOVER
        "chase_frames": 0,        # CHASE
        "other_frames": 0,
        "cap_active_frames": 0,
        "floor_frames": 0,
        "taper_active_frames": 0,
        "lateral_owed_frames": 0,
        "throttled_lateral_frames": 0,
        "lateral_dominant_frames": 0,
        "lane_reached_frames": 0,
        "arrived_frames": 0,
        "sum_lateral_debt_m": 0.0,
        "max_lateral_debt_m": 0.0,
        "max_lateral_debt_frame": 0,
        "max_depth_debt_m": 0.0,
        "sum_dest_gap_m": 0.0,
        "max_dest_gap_m": 0.0,
        "max_dest_gap_frame": 0,
        "sum_rail_gap_m": 0.0,
        "max_rail_gap_m": 0.0,
        "sum_lane_residual_m": 0.0,
        "max_lane_residual_m": 0.0,
        "max_lane_residual_frame": 0,
        "sum_owed_abs_vy_mps": 0.0,
        "max_abs_lateral_vy_mps": 0.0,
        "sum_owed_speed_mps": 0.0,
        "max_speed_seen_mps": 0.0,
    }


def run_rollout(max_frames, open_play):
    """Seeded engine rollout with the open-play lever set as requested.

    Returns (stats, rows).  The module global is saved and restored.
    """
    saved_lever = fb.FB_LATERAL_OPEN_PLAY
    fb.FB_LATERAL_OPEN_PLAY = bool(open_play)

    os.environ["SDL_VIDEODRIVER"] = "dummy"
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

    import random
    random.seed(20260827)

    collision_system = CollisionSystem()
    field = Field(1920, 1080)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball
    open_play_engine = OpenPlayEngine(world)
    world.open_play = open_play_engine
    world.open_play_engine = open_play_engine
    kickoff_engine = KickoffEngine(world, ball, field)

    s = _blank_stats()
    rows = []
    dt = 1.0 / 60.0

    try:
        kickoff_engine.reset_for_kickoff()
        apply_receiving_kickoff_formation(world, field)
        apply_kicking_team_formation(world, field)
        world.ball_owner = "NONE"
        world.ball_carrier = None
        world.ball.possessed_by = None
        kickoff_engine.kickoff()

        for frame in range(1, max_frames + 1):
            s["frames"] = frame
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
                open_play_engine.update(dt)
                if not world.tes_active:
                    collision_system.resolve_all(world.players, world=world)
            else:
                continue

            tagged = [p for p in world.defending_team
                      if getattr(p, "fullback_role_active", False)]
            if len(tagged) != 1:
                continue
            actor = tagged[0]
            if getattr(actor, "tes_attached", False):
                continue
            s["active_frames"] += 1

            state = _state_name(actor)
            if state == "DRIFT":
                s["sweep_frames"] += 1
            elif state == "RECOVER":
                s["recover_frames"] += 1
            elif state == "CHASE":
                s["chase_frames"] += 1
            else:
                s["other_frames"] += 1

            tx = float(getattr(actor, "target_x", actor.x) or 0.0)
            ty = float(getattr(actor, "target_y", actor.y) or 0.0)
            ax = abs(tx - float(actor.x))
            ay = abs(ty - float(actor.y))
            speed = float(getattr(actor, "move_speed", 0.0) or 0.0)

            bc = getattr(world, "ball_carrier", None)
            bc_y = float(getattr(bc, "y", 35.0)) if bc is not None else None

            # ── DESTINATION GAP vs the RAIL WIDTH ──
            # ``anchor_live`` is what the writer actually used this frame
            # (lever-dependent); ``anchor_rail`` is the historical loose-ball
            # 0.6 destination and is lever-independent, so the OLD contract's
            # width stays measurable even on the AFTER leg.
            dest_gap = 0.0
            rail_gap = 0.0
            lane_residual = 0.0
            if bc_y is not None:
                la = float(_line_anchor_m(world))
                _lx, anchor_live_y = compute_fullback_anchor(bc, la)
                _rx, anchor_rail_y = compute_fullback_anchor(
                    bc, la, open_play=False
                )
                dest_gap = abs(bc_y - anchor_live_y)
                rail_gap = abs(bc_y - anchor_rail_y)
                lane_residual = abs(ty - bc_y)
                s["sum_dest_gap_m"] += dest_gap
                s["sum_rail_gap_m"] += rail_gap
                s["sum_lane_residual_m"] += lane_residual
                if dest_gap > s["max_dest_gap_m"]:
                    s["max_dest_gap_m"] = dest_gap
                    s["max_dest_gap_frame"] = frame
                if rail_gap > s["max_rail_gap_m"]:
                    s["max_rail_gap_m"] = rail_gap
                if lane_residual > s["max_lane_residual_m"]:
                    s["max_lane_residual_m"] = lane_residual
                    s["max_lane_residual_frame"] = frame
                if lane_residual <= FB_ARRIVED_PROXIMITY_M:
                    s["lane_reached_frames"] += 1

            # The 2D error the tuned helpers now consume (the raw commands
            # measured here are what the engine itself wrote).
            two_d = math.hypot(tx - float(actor.x), ty - float(actor.y))
            taper_active = two_d < FB_DECEL_START_M
            lateral_owed = ay > LATERAL_OWED_M
            throttled = taper_active and lateral_owed
            if taper_active:
                s["taper_active_frames"] += 1
            if lateral_owed:
                s["lateral_owed_frames"] += 1
                s["sum_owed_speed_mps"] += speed
            if throttled:
                s["throttled_lateral_frames"] += 1
            if ay > ax:
                s["lateral_dominant_frames"] += 1
            if abs(speed - FB_MAX_APPROACH_SPEED_MPS) < 1e-6:
                s["cap_active_frames"] += 1
            if abs(speed - FB_MAX_APPROACH_SPEED_MPS * 0.1) < 1e-6:
                s["floor_frames"] += 1
            if speed > s["max_speed_seen_mps"]:
                s["max_speed_seen_mps"] = speed

            s["sum_lateral_debt_m"] += ay
            if ay > s["max_lateral_debt_m"]:
                s["max_lateral_debt_m"] = ay
                s["max_lateral_debt_frame"] = frame
            if ax > s["max_depth_debt_m"]:
                s["max_depth_debt_m"] = ax
            if ay <= FB_ARRIVED_PROXIMITY_M:
                s["arrived_frames"] += 1

            # REAL lateral closing speed is the actor's OWN vy.  Never a
            # frame-over-frame difference of |dy|: |dy| is measured against a
            # MOVING target, so differentiating it reports the target's
            # motion, not the body's.
            vy = abs(float(getattr(actor, "vy", 0.0) or 0.0))
            if vy > s["max_abs_lateral_vy_mps"]:
                s["max_abs_lateral_vy_mps"] = vy
            if lateral_owed:
                s["sum_owed_abs_vy_mps"] += vy

            rows.append({
                "frame": frame,
                "state": state,
                "ax": ax,
                "ay": ay,
                "two_d": two_d,
                "speed": speed,
                "vy": float(getattr(actor, "vy", 0.0) or 0.0),
                "bc_y": bc_y,
                "y": float(actor.y),
                "ty": ty,
                "dest_gap": dest_gap,
                "rail_gap": rail_gap,
                "lane_residual": lane_residual,
                "lateral_owed": lateral_owed,
                "throttled": throttled,
            })
    finally:
        fb.FB_LATERAL_OPEN_PLAY = saved_lever
        pygame.quit()

    return s, rows


def _mean(total, n):
    return total / n if n else 0.0


def report_leg(name, s, rows, worst_n, sample_n, show_samples):
    n = max(1, s["active_frames"])
    print(f"\n{'-' * 78}")
    print(f"{name}")
    print(f"{'-' * 78}")
    print(f"  rollout frames                     : {s['frames']}")
    print(f"  frames with exactly one tagged FB  : {s['active_frames']}")
    print(f"    DRIFT / RECOVER / CHASE / other  : "
          f"{s['sweep_frames']} / {s['recover_frames']} / "
          f"{s['chase_frames']} / {s['other_frames']}")

    print("\n  [1] DESTINATION vs THE CARRIER LANE")
    print(f"    mean destination gap |bc.y - anchor_y| : "
          f"{_mean(s['sum_dest_gap_m'], n):6.2f} m")
    print(f"    max  destination gap                   : "
          f"{s['max_dest_gap_m']:6.2f} m "
          f"(frame {s['max_dest_gap_frame']})")
    print(f"    max  0.6-rail gap |bc.y - rail_y|      : "
          f"{s['max_rail_gap_m']:6.2f} m "
          f"(the lever-independent OLD clamp width)")
    print(f"    mean writer lane residual |target_y-bc.y| : "
          f"{_mean(s['sum_lane_residual_m'], n):6.2f} m")
    print(f"    max  writer lane residual                  : "
          f"{s['max_lane_residual_m']:6.2f} m "
          f"(frame {s['max_lane_residual_frame']})")
    print(f"    frames the target landed ON the carrier lane : "
          f"{s['lane_reached_frames']} "
          f"({100.0 * s['lane_reached_frames'] / n:.1f}% of FB frames)")

    print("\n  [2] SPEED COMMAND vs LATERAL WORK OWED")
    print(f"    frames with lateral work owed (|dy| > "
          f"{LATERAL_OWED_M} m) : {s['lateral_owed_frames']} "
          f"({100.0 * s['lateral_owed_frames'] / n:.1f}%)")
    print(f"    THROTTLED frames (2D error inside the "
          f"{FB_DECEL_START_M} m taper band AND |dy| > "
          f"{LATERAL_OWED_M} m) : {s['throttled_lateral_frames']} "
          f"({100.0 * s['throttled_lateral_frames'] / n:.1f}%)")
    print(f"    mean commanded speed while lateral owed : "
          f"{_mean(s['sum_owed_speed_mps'], s['lateral_owed_frames']):5.2f} m/s")
    print(f"    mean |vy| while lateral owed (REAL closure) : "
          f"{_mean(s['sum_owed_abs_vy_mps'], s['lateral_owed_frames']):5.2f} m/s")
    print(f"    max |vy| observed                           : "
          f"{s['max_abs_lateral_vy_mps']:5.2f} m/s")
    print(f"    frames commanded at the {FB_MAX_APPROACH_SPEED_MPS} m/s cap : "
          f"{s['cap_active_frames']}   at the 10% floor : {s['floor_frames']}")
    print(f"    frames where |dy| > |dx| (lateral axis owes more) : "
          f"{s['lateral_dominant_frames']} "
          f"({100.0 * s['lateral_dominant_frames'] / n:.1f}%)")

    print("\n  [3] PER-AXIS DEBT")
    print(f"    max depth debt  |dx| : {s['max_depth_debt_m']:6.2f} m")
    print(f"    max lateral debt |dy|: {s['max_lateral_debt_m']:6.2f} m "
          f"(frame {s['max_lateral_debt_frame']})")
    print(f"    mean lateral debt    : "
          f"{_mean(s['sum_lateral_debt_m'], n):6.2f} m")
    print(f"    frames within the arrival proximity "
          f"({FB_ARRIVED_PROXIMITY_M} m): {s['arrived_frames']}")

    if show_samples:
        step = max(1, s["frames"] // max(1, sample_n))
        print(f"\n  [4] LANE SAMPLE (every ~{step} frames)")
        print("      frame   state    bc.y   target_y   actor.y"
              "  laneResid  destGap")
        shown = 0
        for r in rows:
            if r["frame"] % step or shown >= sample_n:
                continue
            if r["bc_y"] is None:
                continue
            print(f"      {r['frame']:5d}   {r['state']:<7} "
                  f"{r['bc_y']:6.2f}   {r['ty']:8.2f}  {r['y']:8.2f}  "
                  f"{r['lane_residual']:9.2f}  {r['dest_gap']:7.2f}")
            shown += 1

    if worst_n and s["throttled_lateral_frames"]:
        print(f"\n  [5] WORST {worst_n} THROTTLED FRAMES "
              "(command held down while lateral ground owed)")
        print("      frame   state     |dx|    |dy|    2D   speed     vy"
              "  destGap")
        worst = sorted(
            (r for r in rows if r["throttled"]),
            key=lambda r: r["ay"],
            reverse=True,
        )[:worst_n]
        for r in worst:
            print(f"      {r['frame']:5d}   {r['state']:<7} "
                  f"{r['ax']:6.2f}  {r['ay']:6.2f}  {r['two_d']:5.2f}  "
                  f"{r['speed']:5.2f}  {r['vy']:+6.2f}  {r['dest_gap']:7.2f}")


def report_ab(before, after):
    nb = max(1, before["active_frames"])
    na = max(1, after["active_frames"])

    def pct(s, n, key):
        return 100.0 * s[key] / n

    print("\n" + "=" * 78)
    print("A/B — RLM_FB_LATERAL_OPEN_PLAY=0 (BEFORE)  vs  the shipped default "
          "(AFTER)")
    print("=" * 78)
    print(f"  {'metric':<46}{'BEFORE':>10}{'AFTER':>10}{'delta':>11}")
    print(f"  {'FB frames':<46}{before['active_frames']:>10d}"
          f"{after['active_frames']:>10d}"
          f"{after['active_frames'] - before['active_frames']:>+11d}")
    print(f"  {'mean destination gap to the carrier lane (m)':<46}"
          f"{_mean(before['sum_dest_gap_m'], nb):>10.2f}"
          f"{_mean(after['sum_dest_gap_m'], na):>10.2f}"
          f"{_mean(after['sum_dest_gap_m'], na) - _mean(before['sum_dest_gap_m'], nb):>+11.2f}")
    print(f"  {'max destination gap to the carrier lane (m)':<46}"
          f"{before['max_dest_gap_m']:>10.2f}{after['max_dest_gap_m']:>10.2f}"
          f"{after['max_dest_gap_m'] - before['max_dest_gap_m']:>+11.2f}")
    print(f"  {'mean writer lane residual |target_y-bc.y| (m)':<46}"
          f"{_mean(before['sum_lane_residual_m'], nb):>10.2f}"
          f"{_mean(after['sum_lane_residual_m'], na):>10.2f}"
          f"{_mean(after['sum_lane_residual_m'], na) - _mean(before['sum_lane_residual_m'], nb):>+11.2f}")
    print(f"  {'max writer lane residual (m)':<46}"
          f"{before['max_lane_residual_m']:>10.2f}"
          f"{after['max_lane_residual_m']:>10.2f}"
          f"{after['max_lane_residual_m'] - before['max_lane_residual_m']:>+11.2f}")
    print(f"  {'frames the target reached the lane (%)':<46}"
          f"{pct(before, nb, 'lane_reached_frames'):>10.1f}"
          f"{pct(after, na, 'lane_reached_frames'):>10.1f}"
          f"{pct(after, na, 'lane_reached_frames') - pct(before, nb, 'lane_reached_frames'):>+11.1f}")
    print(f"  {'mean lateral debt |dy| (m)':<46}"
          f"{_mean(before['sum_lateral_debt_m'], nb):>10.2f}"
          f"{_mean(after['sum_lateral_debt_m'], na):>10.2f}"
          f"{_mean(after['sum_lateral_debt_m'], na) - _mean(before['sum_lateral_debt_m'], nb):>+11.2f}")
    print(f"  {'max lateral debt |dy| (m)':<46}"
          f"{before['max_lateral_debt_m']:>10.2f}"
          f"{after['max_lateral_debt_m']:>10.2f}"
          f"{after['max_lateral_debt_m'] - before['max_lateral_debt_m']:>+11.2f}")
    print(f"  {'lateral-dominant frames |dy|>|dx| (%)':<46}"
          f"{pct(before, nb, 'lateral_dominant_frames'):>10.1f}"
          f"{pct(after, na, 'lateral_dominant_frames'):>10.1f}"
          f"{pct(after, na, 'lateral_dominant_frames') - pct(before, nb, 'lateral_dominant_frames'):>+11.1f}")
    print(f"  {'THROTTLED frames (taper on while |dy|>2) (%)':<46}"
          f"{pct(before, nb, 'throttled_lateral_frames'):>10.1f}"
          f"{pct(after, na, 'throttled_lateral_frames'):>10.1f}"
          f"{pct(after, na, 'throttled_lateral_frames') - pct(before, nb, 'throttled_lateral_frames'):>+11.1f}")
    print(f"  {'mean commanded speed while lateral owed (m/s)':<46}"
          f"{_mean(before['sum_owed_speed_mps'], before['lateral_owed_frames']):>10.2f}"
          f"{_mean(after['sum_owed_speed_mps'], after['lateral_owed_frames']):>10.2f}"
          f"{_mean(after['sum_owed_speed_mps'], after['lateral_owed_frames']) - _mean(before['sum_owed_speed_mps'], before['lateral_owed_frames']):>+11.2f}")
    print(f"  {'mean |vy| while lateral owed (m/s)':<46}"
          f"{_mean(before['sum_owed_abs_vy_mps'], before['lateral_owed_frames']):>10.2f}"
          f"{_mean(after['sum_owed_abs_vy_mps'], after['lateral_owed_frames']):>10.2f}"
          f"{_mean(after['sum_owed_abs_vy_mps'], after['lateral_owed_frames']) - _mean(before['sum_owed_abs_vy_mps'], before['lateral_owed_frames']):>+11.2f}")
    print(f"  {'frames on the arrival proximity (%)':<46}"
          f"{pct(before, nb, 'arrived_frames'):>10.1f}"
          f"{pct(after, na, 'arrived_frames'):>10.1f}"
          f"{pct(after, na, 'arrived_frames') - pct(before, nb, 'arrived_frames'):>+11.1f}")
    print(f"  {'RECOVER frames (cap-exempt re-rail) (%)':<46}"
          f"{pct(before, nb, 'recover_frames'):>10.1f}"
          f"{pct(after, na, 'recover_frames'):>10.1f}"
          f"{pct(after, na, 'recover_frames') - pct(before, nb, 'recover_frames'):>+11.1f}")
    print(f"  {'CHASE frames (%)':<46}"
          f"{pct(before, nb, 'chase_frames'):>10.1f}"
          f"{pct(after, na, 'chase_frames'):>10.1f}"
          f"{pct(after, na, 'chase_frames') - pct(before, nb, 'chase_frames'):>+11.1f}")


def reading(before, after):
    print("\n" + "=" * 78)
    print("READING")
    print("=" * 78)
    print(f"  * The 0.6 rail is a DESTINATION clamp: under the rollback lever the")
    print(f"    sweeper is never asked to cover more than "
          f"{LATERAL_TRACK:.1f} of the way to")
    print(f"    the carrier, so up to "
          f"{35.0 * (1.0 - LATERAL_TRACK):.0f} m of lane is left unasked.  The")
    print(f"    shipped default removes that clamp; the max rail gap above is")
    print(f"    the width of the contract the default replaces.")
    print("  * The taper is now keyed on the 2D distance to the commanded")
    print("    point, so lateral ground is consulted rather than throttled:")
    print("    compare the THROTTLED share, the mean commanded speed while")
    print("    lateral ground is owed, and the mean |vy| on those frames.")
    print("  * The step-out gate is 2D as well "
          f"(reach {FB_LATERAL_BREAK_RADIUS_M:.0f} m), so a lateral channel")
    print("    breach is contestable; that shows up in the CHASE share.")
    print("  * NOTHING here involves LATERAL_PRIORITY_BY_STATE / the 0.25:")
    print("    the tagged actor is never integrated by apply_movement_state")
    print("    (machine-checked in verify_fullback_lateral_priority_exclusion.py).")


def main():
    max_frames = int(os.environ.get("RLM_FB_LATERAL_FRAMES", "3000"))
    sample_n = int(os.environ.get("RLM_FB_LATERAL_SAMPLES", "8"))
    worst_n = int(os.environ.get("RLM_FB_LATERAL_WORST", "8"))

    print("FULLBACK LATERAL TRACKING — LIVE A/B (audit 2026-09-22, cured)")
    print("=" * 78)
    print(f"  rollout length per leg : {max_frames} frames")

    _quiet()
    try:
        before_stats, before_rows = run_rollout(max_frames, open_play=False)
        after_stats, after_rows = run_rollout(max_frames, open_play=True)
    finally:
        _loud()

    report_leg("BEFORE — RLM_FB_LATERAL_OPEN_PLAY=0 (the historical contract)",
               before_stats, before_rows, worst_n, sample_n,
               show_samples=False)
    report_leg("AFTER — shipped default (open-play lateral mirroring + 2D error)",
               after_stats, after_rows, 0, sample_n, show_samples=True)
    report_ab(before_stats, after_stats)
    reading(before_stats, after_stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
