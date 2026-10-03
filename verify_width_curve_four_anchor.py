"""
verify_width_curve_four_anchor.py — FOUR-ANCHOR COMPRESSION MODEL CONTRACT
(2026-10-03).

WHAT IT VERIFIES
----------------
The defensive-line width curve is a FOUR-ANCHOR piecewise-linear
interpolation:

    ATTACKING_TRYLINE_X (10)  -> width_left_tryline_m
    HALFWAY_X           (60)  -> width_halfway_m
    DEFENDING_10M_X     (100) -> REGION * defending_10m_compression
    DEFENDING_TRYLINE_X (110) -> REGION * defending_tryline_compression

where REGION is the ONE defending-tryline width anchor
(``width_defending_tryline_m``).  The defending tryline and the defending
10 m line are INDEPENDENT COMPRESSIONS of that ONE region — the 10 m line has
NO separate width tuner.

SECTIONS
--------
  1. ANCHORS   — the four field-position anchors exist with the documented X
                 positions; the legacy LEFT/RIGHT_TRYLINE_X aliases agree.
  2. TUNERS    — the defending-tryline width REGION and the two compression
                 knobs exist; the region alias never diverges from the legacy
                 ``width_right_tryline_m`` storage.
  3. NO SEPARATE 10 m WIDTH ANCHOR — ``width_defending_10m_m`` does NOT exist;
                 the 10 m anchor IS region * defending_10m_compression.
  4. ANCHOR VALUES — at each anchor X the curve equals the anchor's width
                 EXACTLY (defaults AND a squeezed tuner set).
  5. LEGACY EQUIVALENCE — at the default tuners the four-anchor curve is
                 IDENTICAL to the retired three-anchor curve at every sampled
                 bc.x, so the model ships behaviour-neutral.
  6. INDEPENDENT GOAL-LINE SQUEEZE — each compression knob moves ONLY its own
                 goal-line anchor; the two knobs are independent.
  7. CLAMP + MONOTONICITY — flat outside [10, 110]; each segment monotone at
                 the defaults.
  8. GOVERNOR CONSUMPTION — the governor publishes exactly the four-anchor
                 command at the carrier X.

Run:  python verify_width_curve_four_anchor.py
Exit 0 = all checks pass; 1 = at least one failed.
"""

import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_width import (
    interpolated_defensive_width,
    attacking_tryline_anchor_width_m,
    halfway_anchor_width_m,
    defending_region_width_m,
    defending_10m_anchor_width_m,
    defending_tryline_anchor_width_m,
    ATTACKING_TRYLINE_X,
    HALFWAY_X,
    DEFENDING_10M_X,
    DEFENDING_TRYLINE_X,
    LEFT_TRYLINE_X,
    RIGHT_TRYLINE_X,
    LEGACY_10M_FRACTION,
)
from game.defence.defence_lvl_1.defensive_line_governor import (
    DefensiveLineGovernor,
)

TOL = 1e-9

failures: list = []
checks = [0]


def check(name, cond, detail=""):
    checks[0] += 1
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" -- " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def legacy_width(tuners, bc_x: float) -> float:
    """The RETIRED three-anchor curve, re-stated for the equivalence test.

    (10, width_left_tryline_m) -> (60, width_halfway_m) ->
    (110, width_right_tryline_m), linear between, clamped flat outside.
    """
    left = float(tuners.width_left_tryline_m)
    mid = float(tuners.width_halfway_m)
    right = float(tuners.width_right_tryline_m)
    x = min(max(float(bc_x), ATTACKING_TRYLINE_X), DEFENDING_TRYLINE_X)
    if x <= HALFWAY_X:
        t = (x - ATTACKING_TRYLINE_X) / (HALFWAY_X - ATTACKING_TRYLINE_X)
        return left * (1.0 - t) + mid * t
    t = (x - HALFWAY_X) / (DEFENDING_TRYLINE_X - HALFWAY_X)
    return mid * (1.0 - t) + right * t


# =====================================================================
# [1] ANCHORS
# =====================================================================
def section1_anchors():
    print("\n[1] Four field-position anchors")
    check("ATTACKING_TRYLINE_X == 10", abs(ATTACKING_TRYLINE_X - 10.0) < TOL)
    check("HALFWAY_X == 60", abs(HALFWAY_X - 60.0) < TOL)
    check("DEFENDING_10M_X == 100", abs(DEFENDING_10M_X - 100.0) < TOL)
    check("DEFENDING_TRYLINE_X == 110", abs(DEFENDING_TRYLINE_X - 110.0) < TOL)
    check("anchors strictly ordered",
          ATTACKING_TRYLINE_X < HALFWAY_X < DEFENDING_10M_X
          < DEFENDING_TRYLINE_X)
    check("legacy LEFT_TRYLINE_X alias == ATTACKING_TRYLINE_X",
          abs(LEFT_TRYLINE_X - ATTACKING_TRYLINE_X) < TOL)
    check("legacy RIGHT_TRYLINE_X alias == DEFENDING_TRYLINE_X",
          abs(RIGHT_TRYLINE_X - DEFENDING_TRYLINE_X) < TOL)
    check("defending 10 m sits exactly 10 m out from the defending tryline",
          abs((DEFENDING_TRYLINE_X - DEFENDING_10M_X) - 10.0) < TOL)
    check("LEGACY_10M_FRACTION == 0.8",
          abs(LEGACY_10M_FRACTION - 0.8) < 1e-12,
          str(LEGACY_10M_FRACTION))


# =====================================================================
# [2] TUNERS + REGION ALIAS
# =====================================================================
def section2_tuners():
    print("\n[2] Region tuner + the two compression knobs")
    t = DefenceTuners()
    check("width_defending_tryline_m exists",
          hasattr(t, "width_defending_tryline_m"))
    check("defending_tryline_compression exists (float)",
          isinstance(getattr(t, "defending_tryline_compression", None), float))
    check("defending_10m_compression exists (float)",
          isinstance(getattr(t, "defending_10m_compression", None), float))
    check("defending_tryline_compression default == 1.0 (neutral)",
          abs(float(t.defending_tryline_compression) - 1.0) < TOL,
          str(t.defending_tryline_compression))
    check("defending_10m_compression default == 0.95 (legacy 10 m ratio)",
          abs(float(t.defending_10m_compression) - 0.95) < 1e-12,
          str(t.defending_10m_compression))
    check("region width == legacy width_right_tryline_m",
          abs(defending_region_width_m(t) - t.width_right_tryline_m) < TOL,
          f"{defending_region_width_m(t)} vs {t.width_right_tryline_m}")

    t2 = DefenceTuners()
    t2.width_defending_tryline_m = 70.0
    check("writing the region alias sets width_right_tryline_m "
          "(names cannot diverge)",
          abs(t2.width_right_tryline_m - 70.0) < TOL,
          str(t2.width_right_tryline_m))
    t3 = DefenceTuners()
    t3.width_right_tryline_m = 66.0
    check("writing width_right_tryline_m moves the region alias "
          "(names cannot diverge)",
          abs(defending_region_width_m(t3) - 66.0) < TOL,
          str(defending_region_width_m(t3)))


# =====================================================================
# [3] NO SEPARATE 10 m WIDTH ANCHOR
# =====================================================================
def section3_no_10m_width_anchor():
    print("\n[3] The 10 m anchor is a COMPRESSION, not a width")
    t = DefenceTuners()
    check("NO width_defending_10m_m tuner exists",
          not hasattr(t, "width_defending_10m_m"))
    check("no 'defending_10m_m' key in the tuner namespace",
          "defending_10m_m" not in vars(t))
    t.defending_10m_compression = 0.5
    want = defending_region_width_m(t) * 0.5
    check("10 m anchor == region * defending_10m_compression",
          abs(defending_10m_anchor_width_m(t) - want) < TOL,
          f"got {defending_10m_anchor_width_m(t):.6f} want {want:.6f}")
    t.defending_tryline_compression = 0.5
    want_t = defending_region_width_m(t) * 0.5
    check("tryline anchor == region * defending_tryline_compression",
          abs(defending_tryline_anchor_width_m(t) - want_t) < TOL,
          f"got {defending_tryline_anchor_width_m(t):.6f} "
          f"want {want_t:.6f}")


# =====================================================================
# [4] ANCHOR VALUES
# =====================================================================
def section4_anchor_values():
    print("\n[4] Curve equals each anchor's width AT the anchor X")
    t = DefenceTuners()
    for x, want in (
        (ATTACKING_TRYLINE_X, attacking_tryline_anchor_width_m(t)),
        (HALFWAY_X, halfway_anchor_width_m(t)),
        (DEFENDING_10M_X, defending_10m_anchor_width_m(t)),
        (DEFENDING_TRYLINE_X, defending_tryline_anchor_width_m(t)),
    ):
        got = interpolated_defensive_width(t, x)
        check(f"bc.x={x:.0f} -> {want:.4f} m", abs(got - want) < TOL,
              f"got {got:.6f}")

    t2 = DefenceTuners()
    t2.defending_tryline_compression = 0.8
    t2.defending_10m_compression = 0.6
    for x, want in (
        (ATTACKING_TRYLINE_X, attacking_tryline_anchor_width_m(t2)),
        (HALFWAY_X, halfway_anchor_width_m(t2)),
        (DEFENDING_10M_X, defending_10m_anchor_width_m(t2)),
        (DEFENDING_TRYLINE_X, defending_tryline_anchor_width_m(t2)),
    ):
        got = interpolated_defensive_width(t2, x)
        check(f"squeezed: bc.x={x:.0f} -> {want:.4f} m",
              abs(got - want) < TOL, f"got {got:.6f}")


# =====================================================================
# [5] LEGACY EQUIVALENCE (behaviour-neutral ship)
# =====================================================================
def section5_legacy_equivalence():
    print("\n[5] Default tuners reproduce the RETIRED three-anchor curve")
    t = DefenceTuners()
    worst = 0.0
    worst_x = None
    x = ATTACKING_TRYLINE_X
    while x <= DEFENDING_TRYLINE_X + 1e-9:
        d = abs(interpolated_defensive_width(t, x) - legacy_width(t, x))
        if d > worst:
            worst, worst_x = d, x
        x += 0.5
    check("four-anchor curve == three-anchor curve at every 0.5 m step "
          "(default tuners)", worst <= 1e-9,
          f"worst |diff|={worst:.3e} at bc.x={worst_x}")
    print("      worst |four-anchor - three-anchor| = %.3e m (bc.x=%s)"
          % (worst, worst_x))
    for x, want in (
        (ATTACKING_TRYLINE_X, t.width_left_tryline_m),
        (HALFWAY_X, t.width_halfway_m),
        (DEFENDING_TRYLINE_X, t.width_right_tryline_m),
    ):
        check(f"legacy anchor bc.x={x:.0f} -> {want:.1f} m",
              abs(interpolated_defensive_width(t, x) - want) < TOL)


# =====================================================================
# [6] INDEPENDENT GOAL-LINE SQUEEZE
# =====================================================================
def section6_independent_squeeze():
    print("\n[6] The two goal-line squeeze knobs are INDEPENDENT")
    base = DefenceTuners()

    t_a = DefenceTuners()
    t_a.defending_tryline_compression = 0.7
    check("tryline squeeze leaves the 10 m anchor unchanged",
          abs(defending_10m_anchor_width_m(t_a)
              - defending_10m_anchor_width_m(base)) < TOL)
    check("tryline squeeze DOES move the tryline anchor",
          defending_tryline_anchor_width_m(t_a)
          < defending_tryline_anchor_width_m(base) - 1e-6)

    t_b = DefenceTuners()
    t_b.defending_10m_compression = 0.7
    check("10 m squeeze leaves the tryline anchor unchanged",
          abs(defending_tryline_anchor_width_m(t_b)
              - defending_tryline_anchor_width_m(base)) < TOL)
    check("10 m squeeze DOES move the 10 m anchor",
          defending_10m_anchor_width_m(t_b)
          < defending_10m_anchor_width_m(base) - 1e-6)

    for t in (t_a, t_b):
        check("squeeze leaves the attacking-tryline and halfway anchors alone",
              abs(interpolated_defensive_width(t, ATTACKING_TRYLINE_X)
                  - interpolated_defensive_width(base,
                                                 ATTACKING_TRYLINE_X)) < TOL
              and abs(interpolated_defensive_width(t, HALFWAY_X)
                      - interpolated_defensive_width(base, HALFWAY_X)) < TOL)

    check("squeezing the tryline knob narrows the defending tryline width",
          interpolated_defensive_width(t_a, DEFENDING_TRYLINE_X)
          < interpolated_defensive_width(base, DEFENDING_TRYLINE_X) - 1e-6)
    check("squeezing the 10 m knob narrows the defending 10 m width",
          interpolated_defensive_width(t_b, DEFENDING_10M_X)
          < interpolated_defensive_width(base, DEFENDING_10M_X) - 1e-6)


# =====================================================================
# [7] CLAMP + MONOTONICITY
# =====================================================================
def section7_clamp_and_monotonic():
    print("\n[7] Clamped flat outside [10, 110]; monotone segments")
    t = DefenceTuners()
    check("bc.x below the attacking tryline clamps flat",
          abs(interpolated_defensive_width(t, -50.0)
              - interpolated_defensive_width(t, ATTACKING_TRYLINE_X)) < TOL)
    check("bc.x above the defending tryline clamps flat",
          abs(interpolated_defensive_width(t, 500.0)
              - interpolated_defensive_width(t, DEFENDING_TRYLINE_X)) < TOL)
    for lo, hi in (
        (ATTACKING_TRYLINE_X, HALFWAY_X),
        (HALFWAY_X, DEFENDING_10M_X),
        (DEFENDING_10M_X, DEFENDING_TRYLINE_X),
    ):
        vals = [interpolated_defensive_width(t, lo + (hi - lo) * i / 20.0)
                for i in range(21)]
        check(f"segment [{lo:.0f}, {hi:.0f}] non-decreasing at defaults",
              all(b >= a - TOL for a, b in zip(vals, vals[1:])))


# =====================================================================
# [8] GOVERNOR CONSUMPTION
# =====================================================================
class _ProbeBC:
    def __init__(self, x, y=35.0):
        self.x = float(x)
        self.y = float(y)
        self.vx = 0.0
        self.vy = 0.0


class _ProbeWorld:
    def __init__(self, bc_x):
        self.ruck_x_m = 60.0
        self.ruck_y_m = 35.0
        self.def_line_width_m = 40.0
        self.governor_line_width_m = 40.0
        self.governor_width_active = True
        self.frame_count = 1
        self.dt = 1.0 / 60.0
        self.ball_carrier = _ProbeBC(bc_x)
        self.defending_team = []
        self.in_pursuit_mode = False


def section8_governor_consumption():
    print("\n[8] Governor publishes exactly the four-anchor command")
    t = DefenceTuners()
    for bc_x in (10.0, 40.0, 60.0, 80.0, 100.0, 110.0):
        world = _ProbeWorld(bc_x)
        gov = DefensiveLineGovernor(t, None)
        gov.compute_line_state(world, [])
        want = interpolated_defensive_width(t, bc_x)
        check(f"governor publishes the command at bc.x={bc_x:.0f}",
              abs(float(world.def_line_width_m) - want) < 1e-9,
              f"published={world.def_line_width_m:.6f} want={want:.6f}")


def main():
    print("=" * 78)
    print("FOUR-ANCHOR COMPRESSION MODEL CONTRACT")
    print("=" * 78)
    DefenceTuners().assert_radii()
    section1_anchors()
    section2_tuners()
    section3_no_10m_width_anchor()
    section4_anchor_values()
    section5_legacy_equivalence()
    section6_independent_squeeze()
    section7_clamp_and_monotonic()
    section8_governor_consumption()
    print("\n" + "=" * 78)
    if failures:
        print("RESULT: %d FAILURE(S) of %d checks"
              % (len(failures), checks[0]))
        for f in failures:
            print("   - %s" % f)
        return 1
    print("RESULT: ALL CHECKS PASSED (%d checks)" % checks[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
