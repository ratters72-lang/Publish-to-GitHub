"""verify_fb_chase_target_axis.py — EMERGENCY-CHASE TARGET-AXIS CONTRACT.

WHAT THIS GATES
---------------
Two contracts, both introduced by the 2026-09-28 emergency-chase intercept
work, plus the pure classifier the audit measured with.

  A. THE PURE CLASSIFIER (``game/debug/fb_chase_target_axis.py``).
     ``classify_target_axis``'s four labels and its conservative UNKNOWN,
     the attack-axis / heading-relative ahead tests, the Y-utilisation and
     best-fit-lead measurements, the crossing counter's coincidence band,
     and the ``AxisSummary`` means.  These are the definitions the audit's
     numbers are quoted in, so they are asserted rather than assumed.

  B. THE CHASE-POINT LAW (``fullback_role.fullback_chase_point``) AND ITS
     ROLLBACK LEVER.
       * ``RLM_FB_CHASE_INTERCEPT=0`` (lever OFF) restores the historical
         X-only trail BYTE-FOR-BYTE: ``(bc.x - FB_CHASE_TRAIL_M, bc.y)`` and
         ``max(anchor_x, bc.x - FB_CHASE_TRAIL_M)`` for a lateral break.
       * lever ON keeps the fullback one trail-width goal-side of the BC's
         CURRENT X while projecting only the BC's Y lane.
       * the near-range taper suppresses the lead to ZERO inside
         ``FB_CHASE_LEAD_NEAR_M`` so the pre-tackle slot gate still reads the
         carrier's own lane and the arm is not suppressed.
       * the projection is bounded by ``FB_CHASE_LEAD_MAX_M`` and the result
         lands inside the legal field strip.
       * a carrier at rest still gets the historical trail point.
       * the X command remains one trail-width goal-side of the CURRENT BC
         regardless of forward velocity.

WHY IT IS PURE
--------------
The classifier imports nothing but the standard library, and
``fullback_chase_point`` reads only ``bc.x/.y/.vy`` and the defender's
``x/.y/max_defensive_speed_mps``.  Both are therefore driven here with
hand-built stubs: no engine boot, no rollout, no address-order jitter, so a
failure is always a real contract break and never engine scheduling.

Exits 1 on any failed contract check (CI-style).
"""

import os

os.environ["SDL_VIDEODRIVER"] = "dummy"

import math
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import pygame

pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

import game.defence.defence_lvl_1.fullback_role as fbr
from game.debug.fb_chase_target_axis import (
    AXIS_EPS_M,
    AxisSummary,
    TARGET_CURRENT_POSITION,
    TARGET_INTERCEPT_XY,
    TARGET_LEAD_X,
    TARGET_TRAIL_X,
    TARGET_UNKNOWN,
    best_fit_lead_horizon,
    classify_target_axis,
    crossing_stats,
    is_ahead_of_heading,
    is_ahead_of_lane_x,
    lead_explains_y,
    overshoot_margin_m,
    y_axis_utilisation,
)

TOL = 1e-9
failures = []
checks_run = 0


def check(name, condition, detail=""):
    """Record and print one contract check."""
    global checks_run
    checks_run += 1
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


class Stub:
    """Minimal body the chase law reads (no engine, no pygame surface)."""

    def __init__(self, x=0.0, y=0.0, vx=0.0, vy=0.0):
        self.x = float(x)
        self.y = float(y)
        self.vx = float(vx)
        self.vy = float(vy)


def defender_at(x, y, ceiling=8.3):
    """A pursuer stub carrying the governed defensive ceiling."""
    d = Stub(x=x, y=y)
    d.max_defensive_speed_mps = float(ceiling)
    d.max_speed_mps = float(ceiling)
    return d


def with_lever(value):
    """Set the module lever and return the previous value (for restore)."""
    previous = fbr.FB_CHASE_INTERCEPT
    fbr.FB_CHASE_INTERCEPT = bool(value)
    return previous


def expected_lead_m(speed_mps, distance_m):
    """``tau`` as the law solves it: damped, tapered, then clamped."""
    tau = (distance_m / speed_mps) * fbr.FB_CHASE_LEAD_DAMPING
    if distance_m <= fbr.FB_CHASE_LEAD_NEAR_M:
        taper = 0.0
    elif distance_m >= fbr.FB_CHASE_LEAD_FULL_M:
        taper = 1.0
    else:
        span = fbr.FB_CHASE_LEAD_FULL_M - fbr.FB_CHASE_LEAD_NEAR_M
        taper = (distance_m - fbr.FB_CHASE_LEAD_NEAR_M) / span if span else 1.0
    tau *= max(0.0, min(1.0, taper))
    return min(fbr.FB_CHASE_LEAD_MAX_S, max(0.0, tau))


def check_classifier_labels():
    """Section A1 — the four labels and the conservative UNKNOWN."""
    eps = AXIS_EPS_M
    check(
        "CURRENT_POSITION when both axes carry no command",
        classify_target_axis(50.0, 35.0, 50.0, 35.0) == TARGET_CURRENT_POSITION,
    )
    check(
        "CURRENT_POSITION at exactly the epsilon on both axes",
        classify_target_axis(50.0 + eps, 35.0 + eps, 50.0, 35.0)
        == TARGET_CURRENT_POSITION,
    )
    check(
        "INTERCEPT_XY when the Y axis carries a command",
        classify_target_axis(50.0, 35.0 + 1.0, 50.0, 35.0)
        == TARGET_INTERCEPT_XY,
    )
    check(
        "INTERCEPT_XY even when X ALSO carries a command",
        classify_target_axis(48.0, 36.0, 50.0, 35.0) == TARGET_INTERCEPT_XY,
    )
    check(
        "LEAD_X when only X commands and it sits ahead (larger X)",
        classify_target_axis(51.0, 35.0, 50.0, 35.0) == TARGET_LEAD_X,
    )
    check(
        "TRAIL_X when only X commands and it sits behind (smaller X)",
        classify_target_axis(49.0, 35.0, 50.0, 35.0) == TARGET_TRAIL_X,
    )
    check(
        "a Y command inside the epsilon is still a lane mirror",
        classify_target_axis(49.0, 35.0 + eps, 50.0, 35.0) == TARGET_TRAIL_X,
    )
    for bad in (float("nan"), float("inf"), None, "x"):
        check(
            f"UNKNOWN for unreadable input {bad!r}",
            classify_target_axis(bad, 35.0, 50.0, 35.0) == TARGET_UNKNOWN,
        )


def check_classifier_measures():
    """Section A2 — ahead tests, Y utilisation, best-fit lead."""
    check(
        "is_ahead_of_lane_x: larger X is ahead, smaller is not",
        is_ahead_of_lane_x(51.0, 50.0) and not is_ahead_of_lane_x(49.0, 50.0),
    )
    check(
        "is_ahead_of_lane_x: level with the carrier is NOT ahead",
        not is_ahead_of_lane_x(50.0, 50.0),
    )
    check(
        "is_ahead_of_heading: forward along a +X heading",
        is_ahead_of_heading(51.0, 35.0, 50.0, 35.0, 8.0, 0.0),
    )
    check(
        "is_ahead_of_heading: backward along a +X heading is not forward",
        not is_ahead_of_heading(49.0, 35.0, 50.0, 35.0, 8.0, 0.0),
    )
    check(
        "is_ahead_of_heading: a carrier at rest has no heading",
        not is_ahead_of_heading(51.0, 35.0, 50.0, 35.0, 0.0, 0.0),
    )
    check(
        "overshoot_margin_m is signed in the attack axis",
        abs(overshoot_margin_m(52.0, 50.0) - 2.0) < TOL
        and abs(overshoot_margin_m(48.0, 50.0) + 2.0) < TOL,
    )
    check(
        "y_axis_utilisation is zero for a lane mirror",
        abs(y_axis_utilisation(35.0, 35.0)) < TOL,
    )
    check(
        "y_axis_utilisation is |dy| for a real lateral command",
        abs(y_axis_utilisation(37.5, 35.0) - 2.5) < TOL,
    )

    vy = 6.0
    for tau in (0.10, 0.20, 0.35, 0.45):
        target_y = 35.0 + vy * tau
        got_tau, residual, mirror = best_fit_lead_horizon(target_y, 35.0, vy)
        check(
            f"best-fit lead recovers tau={tau:.2f} s from the Y command",
            (not mirror) and abs(residual) < 1e-6 and abs(got_tau - tau) < 1e-9,
            f"tau*={got_tau:.3f} residual={residual:.2e}",
        )
    check(
        "best-fit lead prefers NO LEAD on a mirror (tau 0 wins the tie)",
        best_fit_lead_horizon(35.0, 35.0, vy)[2] is True,
    )
    check(
        "best-fit lead is conservative on unreadable input",
        best_fit_lead_horizon(float("nan"), 35.0, vy) == (0.0, 0.0, True),
    )
    check(
        "lead_explains_y needs a real gain over the mirror",
        lead_explains_y(35.0 + vy * 0.35, 35.0, vy)
        and not lead_explains_y(35.05, 35.0, vy),
    )


def check_crossing_stats():
    """Section A3 — the crossing counter's coincidence band."""
    def frames(pairs):
        return [
            {"fb_x": fx, "bc_x": 50.0, "fb_y": fy, "bc_y": 35.0}
            for fx, fy in pairs
        ]

    one_pass = crossing_stats(frames([(49.0, 35.0), (51.0, 35.0)]))
    check(
        "one sign change in X counts one crossing",
        one_pass.x_lane_crossings == 1,
        f"{one_pass.x_lane_crossings}",
    )
    back_and_forth = crossing_stats(
        frames([(49.0, 35.0), (51.0, 35.0), (49.0, 35.0)])
    )
    check(
        "two sign changes in X count two crossings",
        back_and_forth.x_lane_crossings == 2,
        f"{back_and_forth.x_lane_crossings}",
    )
    dither = crossing_stats(
        frames([(49.0, 35.0), (49.5, 35.0), (49.2, 35.0), (51.0, 35.0)])
    )
    check(
        "a sub-epsilon dither never creates a crossing",
        dither.x_lane_crossings == 1,
        f"{dither.x_lane_crossings}",
    )
    coincident = crossing_stats(
        frames([(50.0, 35.0), (49.0, 35.0), (50.0, 35.0), (51.0, 35.0)])
    )
    check(
        "a COINCIDENT frame neither creates nor breaks a crossing",
        coincident.x_lane_crossings == 1,
        f"{coincident.x_lane_crossings}",
    )
    lateral = crossing_stats(frames([(50.0, 33.0), (50.0, 37.0)]))
    check(
        "a lateral lane pass counts one Y crossing",
        lateral.y_lane_crossings == 1 and lateral.x_lane_crossings == 0,
        f"y={lateral.y_lane_crossings} x={lateral.x_lane_crossings}",
    )
    check(
        "crossing_stats skips unreadable frames instead of raising",
        crossing_stats([{"fb_x": None, "bc_x": 50.0, "fb_y": 1.0,
                         "bc_y": 35.0}]).frames == 0,
    )


def check_axis_summary():
    """Section A4 — the aggregate the ledger's columns are computed from."""
    empty = AxisSummary()
    check(
        "AxisSummary reports 0.0 on an empty population (no ZeroDivision)",
        empty.mean_y_axis_utilisation_m == 0.0
        and empty.mean_best_fit_lead_s == 0.0
        and empty.target_ahead_share == 0.0
        and empty.y_axis_mirror_share == 0.0,
    )
    populated = AxisSummary()
    populated.frames = 4
    populated.y_axis_utilisation_sum = 3.0
    populated.best_fit_tau_sum = 0.8
    populated.target_ahead_frames = 1
    populated.y_axis_mirror_frames = 2
    check(
        "AxisSummary means divide by the frame population",
        abs(populated.mean_y_axis_utilisation_m - 0.75) < TOL
        and abs(populated.mean_best_fit_lead_s - 0.2) < TOL
        and abs(populated.target_ahead_share - 0.25) < TOL
        and abs(populated.y_axis_mirror_share - 0.5) < TOL,
    )


def check_trail_rollback():
    """Section B1 — the lever OFF restores the historical trail exactly."""
    previous = with_lever(False)
    try:
        check(
            "lever OFF reports the 'trail' chase contract",
            fbr.fullback_chase_mode() == fbr.FB_CHASE_MODE_TRAIL,
            fbr.fullback_chase_mode(),
        )
        fb = defender_at(40.0, 35.0)
        bc = Stub(x=50.0, y=35.0, vx=9.0, vy=0.0)
        got = fbr.fullback_chase_point(bc, fb, 60.0, fbr.FB_BREAK_DEPTH)
        want = (50.0 - fbr.FB_CHASE_TRAIL_M, 35.0)
        check(
            "lever OFF + DEPTH break is byte-for-byte (bc.x - TRAIL, bc.y)",
            abs(got[0] - want[0]) < TOL and abs(got[1] - want[1]) < TOL,
            f"got={got[0]:.6f},{got[1]:.6f} want={want[0]:.6f},{want[1]:.6f}",
        )
        check(
            "lever OFF ignores the carrier's lateral velocity entirely",
            abs(got[1] - bc.y) < TOL,
            f"target_y={got[1]:.6f} bc.y={bc.y:.6f}",
        )
        lateral_bc = Stub(x=50.0, y=25.0, vx=0.0, vy=6.0)
        lateral = fbr.fullback_chase_point(
            lateral_bc, fb, 60.0, fbr.FB_BREAK_LATERAL
        )
        check(
            "lever OFF + LATERAL break is (max(anchor_x, bc.x - TRAIL), bc.y)",
            abs(lateral[0] - 60.0) < TOL and abs(lateral[1] - 25.0) < TOL,
            f"got={lateral[0]:.3f},{lateral[1]:.3f}",
        )
        deep_bc = Stub(x=120.0, y=35.0, vx=0.0, vy=0.0)
        unclamped = fbr.fullback_chase_point(
            deep_bc, fb, 60.0, fbr.FB_BREAK_DEPTH
        )
        check(
            "lever OFF is UNCLAMPED by design (rollback keeps its own value)",
            abs(unclamped[0] - (120.0 - fbr.FB_CHASE_TRAIL_M)) < TOL,
            f"target_x={unclamped[0]:.3f} (field bound is applied downstream)",
        )
        for heading in (0.0, 45.0, 90.0, 135.0, 180.0, 270.0):
            radians = math.radians(heading)
            vx = 8.0 * math.cos(radians)
            vy = 8.0 * math.sin(radians)
            moving = Stub(x=50.0, y=35.0, vx=vx, vy=vy)
            point = fbr.fullback_chase_point(
                moving, fb, 10.0, fbr.FB_BREAK_DEPTH
            )
            if point[0] >= 50.0 - TOL:
                check(
                    f"lever OFF never commands goal-side of the carrier "
                    f"(heading {heading:.0f} deg)",
                    False,
                    f"target_x={point[0]:.3f} bc.x=50.000",
                )
                break
        else:
            check(
                "lever OFF never commands goal-side of the carrier "
                "(6 headings)",
                True,
            )
    finally:
        fbr.FB_CHASE_INTERCEPT = previous


def check_intercept_law():
    """Section B2 — the lever ON projects on both axes, bounded."""
    previous = with_lever(True)
    try:
        check(
            "lever ON reports the 'intercept' chase contract",
            fbr.fullback_chase_mode() == fbr.FB_CHASE_MODE_INTERCEPT,
            fbr.fullback_chase_mode(),
        )
        fb = defender_at(40.0, 35.0)

        # ── DEGENERATE EQUIVALENCE ──
        stationary = Stub(x=50.0, y=35.0, vx=0.0, vy=0.0)
        still = fbr.fullback_chase_point(
            stationary, fb, 60.0, fbr.FB_BREAK_DEPTH
        )
        check(
            "a carrier AT REST reduces EXACTLY to the historical trail",
            abs(still[0] - (50.0 - fbr.FB_CHASE_TRAIL_M)) < TOL
            and abs(still[1] - 35.0) < TOL,
            f"got={still[0]:.6f},{still[1]:.6f}",
        )

        # ── THE Y AXIS IS NOW USED ──
        wide = Stub(x=50.0, y=35.0, vx=0.0, vy=6.0)
        lateral = fbr.fullback_chase_point(
            wide, defender_at(50.0, 20.0), 60.0, fbr.FB_BREAK_DEPTH
        )
        # 15 m away: the taper is saturated and the horizon hits its ceiling,
        # so the lateral projection is the LEAD_MAX_M clamp.
        saturated = min(
            6.0 * expected_lead_m(8.3, 15.0), fbr.FB_CHASE_LEAD_MAX_M
        )
        check(
            "a lateral carrier produces a REAL lateral command (Y is used)",
            abs(lateral[1] - (35.0 + saturated)) < TOL,
            f"target_y={lateral[1]:.3f} bc.y=35.000 "
            f"(displacement {saturated:.3f} m)",
        )
        # 6 m away: INSIDE the taper ramp, so this gates the ramp itself
        # rather than the saturated case above.
        ramped = min(
            6.0 * expected_lead_m(8.3, 6.0), fbr.FB_CHASE_LEAD_MAX_M
        )
        ramped_point = fbr.fullback_chase_point(
            wide, defender_at(44.0, 35.0), 60.0, fbr.FB_BREAK_DEPTH
        )
        check(
            "inside the taper ramp the lead scales with distance, not full",
            abs(ramped_point[1] - (35.0 + ramped))
            < TOL
            and ramped < saturated,
            f"ramped {ramped:.3f} m < saturated {saturated:.3f} m",
        )
        check(
            "a pure lateral carrier leaves X goal-side of its current position",
            abs(lateral[0] - (50.0 - fbr.FB_CHASE_TRAIL_M)) < TOL,
            f"target_x={lateral[0]:.3f}",
        )

        diagonal = Stub(x=50.0, y=35.0, vx=8.0, vy=4.0)
        diagonal_point = fbr.fullback_chase_point(
            diagonal, defender_at(40.0, 35.0), 60.0, fbr.FB_BREAK_DEPTH
        )
        diagonal_tau = expected_lead_m(8.3, 10.0)
        check(
            "forward speed never moves the X command past the live BC",
            abs(diagonal_point[0] - (50.0 - fbr.FB_CHASE_TRAIL_M)) < TOL,
            f"target_x={diagonal_point[0]:.3f} bc.x=50.000",
        )
        check(
            "the Y command anticipates lateral velocity without an X-trail offset",
            abs(diagonal_point[1] - (35.0 + 4.0 * diagonal_tau)) < TOL,
            f"target_y={diagonal_point[1]:.3f}",
        )

        # ── NEAR-RANGE TAPER (the slot-gate preservation contract) ──
        near = fbr.fullback_chase_point(
            wide, defender_at(49.0, 35.0), 60.0, fbr.FB_BREAK_DEPTH
        )
        check(
            "inside FB_CHASE_LEAD_NEAR_M the projection is ZERO",
            abs(near[1] - 35.0) < TOL,
            f"target_y={near[1]:.6f} bc.y=35.000",
        )
        # ...and the margin falls back to the ATTACK AXIS there, so the
        # near-band command is the legacy trail point on EVERY heading.  That
        # is what keeps the 0.3 m pre-tackle slot gate armed at contact range:
        # a margin applied along a 45-degree heading would sit 0.71 m off the
        # carrier's lane and suppress the arm.
        near_deviation = 0.0
        for heading in (0.0, 30.0, 45.0, 90.0, 135.0, 180.0, 270.0):
            radians = math.radians(heading)
            heading_bc = Stub(
                x=50.0,
                y=35.0,
                vx=8.0 * math.cos(radians),
                vy=8.0 * math.sin(radians),
            )
            point = fbr.fullback_chase_point(
                heading_bc, defender_at(49.0, 35.0), 60.0, fbr.FB_BREAK_DEPTH
            )
            near_deviation = max(
                near_deviation,
                abs(point[0] - (50.0 - fbr.FB_CHASE_TRAIL_M)),
                abs(point[1] - 35.0),
            )
        check(
            "at contact range EVERY heading gives the legacy trail point "
            "(the slot gate stays armed)",
            near_deviation <= TOL,
            f"worst deviation {near_deviation:.2e} m over 7 headings",
        )

        # ── THE PROJECTION BOUND ──
        bound = fbr.FB_CHASE_TRAIL_M + fbr.FB_CHASE_LEAD_MAX_M + TOL
        worst = 0.0
        for distance in (0.5, 1.0, 2.5, 3.0, 5.0, 8.0, 12.0, 20.0):
            for speed in (0.06, 1.0, 4.0, 8.3, 20.0):
                runner = Stub(x=50.0, y=35.0, vx=speed, vy=speed)
                point = fbr.fullback_chase_point(
                    runner,
                    defender_at(50.0 - distance, 35.0),
                    10.0,
                    fbr.FB_BREAK_DEPTH,
                )
                worst = max(
                    worst,
                    math.hypot(point[0] - 50.0, point[1] - 35.0),
                )
        check(
            "the command is never further from the carrier than "
            "TRAIL + LEAD_MAX",
            worst <= bound,
            f"worst {worst:.4f} m vs bound {bound:.4f} m",
        )

        # ── THE GOAL-SIDE X CONTRACT ──
        worst_ahead = -99.0
        worst_behind = 99.0
        for speed in (0.06, 2.0, 6.0, 9.0, 15.0):
            runner = Stub(x=50.0, y=35.0, vx=speed, vy=0.0)
            for distance in (0.5, 2.0, 3.0, 6.0, 8.0, 15.0, 30.0):
                point = fbr.fullback_chase_point(
                    runner,
                    defender_at(50.0 - distance, 35.0),
                    10.0,
                    fbr.FB_BREAK_DEPTH,
                )
                margin = point[0] - 50.0
                worst_ahead = max(worst_ahead, margin)
                worst_behind = min(worst_behind, margin)
        check(
            "the X command never moves ahead of the live BC",
            worst_ahead <= -fbr.FB_CHASE_TRAIL_M + TOL,
            f"closest X command {worst_ahead:.4f} m from BC",
        )
        check(
            "the X trail stays exactly one trail-width behind",
            abs(worst_behind + fbr.FB_CHASE_TRAIL_M) <= TOL,
            f"farthest X command {worst_behind:.4f} m from BC",
        )

        # ── THE FIELD BOUND ──
        edge = Stub(x=111.0, y=69.0, vx=9.0, vy=9.0)
        point = fbr.fullback_chase_point(
            edge, defender_at(100.0, 60.0), 10.0, fbr.FB_BREAK_DEPTH
        )
        check(
            "the intercept command lands inside the legal field strip",
            fbr.LEFT_TRYLINE_X - TOL <= point[0] <= fbr.FIELD_MAX_X + TOL
            and -TOL <= point[1] <= 70.0 + TOL,
            f"target=({point[0]:.3f},{point[1]:.3f})",
        )
        floored = fbr.fullback_chase_point(
            Stub(x=50.0, y=35.0, vx=9.0, vy=0.0),
            defender_at(40.0, 35.0),
            70.0,
            fbr.FB_BREAK_LATERAL,
        )
        check(
            "a LATERAL break is floored at the sweeper's own depth anchor",
            abs(floored[0] - 70.0) < TOL,
            f"target_x={floored[0]:.3f} anchor=70.000",
        )

        # ── TOTALITY ──
        for bad in (None, Stub(x=float("nan"), y=35.0),
                    Stub(x=50.0, y=float("inf"))):
            try:
                point = fbr.fullback_chase_point(
                    bad, defender_at(40.0, 35.0), 62.0, fbr.FB_BREAK_DEPTH
                )
                ok = all(math.isfinite(v) for v in point)
            except Exception as error:  # noqa: BLE001 - reported, not hidden
                ok = False
                point = repr(error)
            check(
                "an unreadable carrier yields a finite anchor pin, never NaN",
                ok,
                f"point={point}",
            )

        # ── PURITY / DETERMINISM ──
        runner = Stub(x=50.0, y=35.0, vx=7.0, vy=-3.0)
        first = fbr.fullback_chase_point(
            runner, defender_at(41.0, 30.0), 55.0, fbr.FB_BREAK_DEPTH
        )
        second = fbr.fullback_chase_point(
            runner, defender_at(41.0, 30.0), 55.0, fbr.FB_BREAK_DEPTH
        )
        check(
            "the chase law is pure: identical inputs give identical output",
            first == second,
            f"{first} vs {second}",
        )
    finally:
        fbr.FB_CHASE_INTERCEPT = previous


def main():
    """Run every section; exit 1 on any failed contract."""
    print("=" * 72)
    print("EMERGENCY-CHASE TARGET-AXIS + INTERCEPT-LEVER CONTRACT VERIFIER")
    print("=" * 72)

    print("\n[A] Pure classifier contract")
    check_classifier_labels()
    check_classifier_measures()
    check_crossing_stats()
    check_axis_summary()

    print("\n[B] Chase-point law + rollback lever")
    check_trail_rollback()
    check_intercept_law()

    print("\n" + "=" * 72)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S) of {checks_run} checks")
        for name in failures:
            print(f"  - {name}")
        pygame.quit()
        return 1
    print(f"RESULT: ALL {checks_run} CHECKS PASSED")
    pygame.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
