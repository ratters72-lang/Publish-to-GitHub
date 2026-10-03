"""
verify_governed_total_span.py — GOVERNED WIDTH IS THE TOTAL SPAN (2026-09-14).

THE CONTRACT
------------
At the ball carrier's X the 3-point width tuners interpolate a commanded
width, and ``governed_width_m`` clamps it to the tuner band and the sideline
safety cap.  That governed width is the AUTHORITATIVE TOTAL winger-to-winger
span of the defensive line.  So for every (bc.x, ruck_y, split):

    side_lanes(n_left , ruck_y, LEFT , tuners, span=left_w) [-1]
    side_lanes(n_right, ruck_y, RIGHT, tuners, span=right_w)[-1]

must be separated by EXACTLY the governed width, where (left_w, right_w) are
the published per-side spans.  Nothing else may shrink the realised span --
not the short-side 0.65 factor, not the room cap, not the defender-gap
floor.  The only bound left is the ABSOLUTE sideline clamp, which can only
bind when the ruck is so close to a touchline that the governed width does
not physically fit.

ASSERTIONS
----------
  T1  outermost-lane span == governed width at the interpolated bc.x
      on every grid case (tolerance 1e-9)
  T2  each outermost lane sits on its own side's half-span anchor
  T3  the two published halves SUM to the governed width
  T4  both outermost lanes stay inside the absolute sideline margins
  T5  the realised span responds 1:1 to the tuner (no absorbed delta)

Run:  python verify_governed_total_span.py
Exit 0 = all assertions hold; 1 = at least one failed.
"""

import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from world.world_state import GameSubState as _GameSubState  # noqa: F401
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_width import (
    interpolated_defensive_width,
    governed_width_m,
)
from game.defence.defence_lvl_1.defensive_split_spacing import (
    side_lanes,
    side_widths,
    LEFT,
    RIGHT,
)
from game.defence.defence_lvl_1.formation_width_authority import (
    publish_formation_width,
)

BC_X_GRID = (10.0, 25.0, 40.0, 60.0, 75.0, 90.0, 110.0)
# Ruck Y from 4 m to 66 m: the widest span the absolute sideline clamp can
# still hold on BOTH sides (room_left = ruck_y - margin, room_right =
# field_max - ruck_y, both positive).  Outside this band one side has zero
# room and the published span for it is legitimately 0.0 - the ladder
# contract refuses a zero span, which is a TES-scope caller concern, not a
# width-law one.
RUCK_Y_GRID = (4.0, 8.0, 15.0, 27.5, 35.0, 42.5, 55.0, 62.0, 66.0)
SPLIT_GRID = ((3, 3), (4, 4), (5, 5), (6, 5), (2, 4))

TUNER_SETS = (
    ("extreme_narrow", (18.0, 20.0, 22.0)),
    ("narrow",         (30.0, 33.0, 36.0)),
    ("default",        (50.0, 55.0, 60.0)),
    ("wide",           (80.0, 88.0, 96.0)),
    ("extreme_wide",   (130.0, 143.0, 156.0)),
)

TOL = 1e-9

failures = []
checks = [0]


def check(name, cond, detail=""):
    checks[0] += 1
    if not cond:
        failures.append(name)
        print("  [FAIL] %s%s" % (name, (" - " + detail) if detail else ""))


class StubCarrier:
    def __init__(self, x, y):
        self.x = float(x)
        self.y = float(y)


class StubWorld:
    """Minimal world carrying only what the width chain reads."""

    def __init__(self, bc_x, ruck_y, commanded):
        self.ruck_x_m = 60.0
        self.ruck_y_m = float(ruck_y)
        self.def_line_width_m = float(commanded)
        self.governor_line_width_m = float(commanded)
        self.governor_width_active = True
        self.frame_count = 1
        self.ball_carrier = StubCarrier(bc_x, ruck_y)
        self.defending_team = []
        self.sub_state = _GameSubState.OPEN_PLAY
        self.in_pursuit_mode = False


def make_tuners(vals):
    t = DefenceTuners()
    t.width_left_tryline_m = float(vals[0])
    t.width_halfway_m = float(vals[1])
    t.width_right_tryline_m = float(vals[2])
    return t


def measure(bc_x, ruck_y, n_left, n_right, tuners):
    """Return the rungs for one grid case at the interpolated bc.x."""
    commanded = interpolated_defensive_width(tuners, bc_x)
    world = StubWorld(bc_x, ruck_y, commanded)
    governed = governed_width_m(world, tuners)
    pub = publish_formation_width(world, tuners)

    lw, rw = side_widths(world, tuners, ruck_y=ruck_y)
    lanes_l = side_lanes(int(n_left), ruck_y, LEFT, tuners, span=lw)
    lanes_r = side_lanes(int(n_right), ruck_y, RIGHT, tuners, span=rw)
    outer_l = float(lanes_l[-1]) if lanes_l else None
    outer_r = float(lanes_r[-1]) if lanes_r else None
    span = (outer_r - outer_l) if (outer_l is not None
                                   and outer_r is not None) else None
    return {
        "commanded": float(commanded),
        "governed": float(governed),
        "pub_left": float(pub["left_m"]),
        "pub_right": float(pub["right_m"]),
        "left_w": float(lw),
        "right_w": float(rw),
        "outer_l": outer_l,
        "outer_r": outer_r,
        "span": span,
    }


def main():
    print("=" * 76)
    print("VERIFY - GOVERNED WIDTH IS THE AUTHORITATIVE TOTAL LINE SPAN")
    print("=" * 76)

    margin = float(DefenceTuners().sideline_margin_m)
    field_max = float(DefenceTuners().field_width_m) - margin

    print("\n[T1] outermost-lane span == governed width at the interpolated bc.x")
    worst = (0.0, None)
    n_cases = 0
    for label, vals in TUNER_SETS:
        tuners = make_tuners(vals)
        for bc_x in BC_X_GRID:
            for ruck_y in RUCK_Y_GRID:
                for n_left, n_right in SPLIT_GRID:
                    r = measure(bc_x, ruck_y, n_left, n_right, tuners)
                    n_cases += 1
                    gap = abs(r["span"] - r["governed"])
                    if gap > worst[0]:
                        worst = (gap, "tuner=%s bc.x=%.1f ruck_y=%.1f %d-%d "
                                      "span=%.6f governed=%.6f"
                                      % (label, bc_x, ruck_y, n_left, n_right,
                                         r["span"], r["governed"]))
                    check(
                        "T1 %s bc.x=%.1f ruck_y=%.1f %d-%d span==governed"
                        % (label, bc_x, ruck_y, n_left, n_right),
                        gap <= TOL,
                        "span=%.9f governed=%.9f" % (r["span"], r["governed"]),
                    )
    print("      cases: %d   worst |span - governed| = %.3e m"
          % (n_cases, worst[0]))
    if worst[1]:
        print("      worst case: %s" % worst[1])

    print("\n[T2] each outermost lane sits on its own side's half-span anchor")
    worst2 = (0.0, None)
    for label, vals in TUNER_SETS:
        tuners = make_tuners(vals)
        for bc_x in BC_X_GRID:
            for ruck_y in RUCK_Y_GRID:
                for n_left, n_right in ((3, 3), (6, 5)):
                    r = measure(bc_x, ruck_y, n_left, n_right, tuners)
                    want_l = ruck_y - r["left_w"] * 0.5
                    want_r = ruck_y + r["right_w"] * 0.5
                    gap_l = abs(r["outer_l"] - want_l)
                    gap_r = abs(r["outer_r"] - want_r)
                    gap = max(gap_l, gap_r)
                    if gap > worst2[0]:
                        worst2 = (gap, "tuner=%s bc.x=%.1f ruck_y=%.1f "
                                      "L %.6f/%.6f R %.6f/%.6f"
                                      % (label, bc_x, ruck_y, r["outer_l"],
                                         want_l, r["outer_r"], want_r))
                    check(
                        "T2 %s bc.x=%.1f ruck_y=%.1f outer lane == half-span"
                        % (label, bc_x, ruck_y),
                        gap <= 1e-6,
                        "L err=%.3e R err=%.3e" % (gap_l, gap_r),
                    )
    print("      worst outer-lane anchor error = %.3e m" % worst2[0])
    if worst2[1]:
        print("      worst case: %s" % worst2[1])

    print("\n[T3] the two published halves SUM to the governed width")
    worst3 = (0.0, None)
    for label, vals in TUNER_SETS:
        tuners = make_tuners(vals)
        for bc_x in BC_X_GRID:
            for ruck_y in RUCK_Y_GRID:
                r = measure(bc_x, ruck_y, 4, 4, tuners)
                total = 0.5 * (r["left_w"] + r["right_w"])
                gap = abs(total - r["governed"])
                if gap > worst3[0]:
                    worst3 = (gap, "tuner=%s bc.x=%.1f ruck_y=%.1f "
                                   "total=%.6f governed=%.6f"
                                   % (label, bc_x, ruck_y, total,
                                      r["governed"]))
                check(
                    "T3 %s bc.x=%.1f ruck_y=%.1f halves sum to governed"
                    % (label, bc_x, ruck_y),
                    gap <= TOL,
                    "total=%.9f governed=%.9f" % (total, r["governed"]),
                )
    print("      worst |total - governed| = %.3e m" % worst3[0])
    if worst3[1]:
        print("      worst case: %s" % worst3[1])

    print("\n[T4] both outermost lanes stay inside the absolute sideline "
          "margins")
    worst4 = (0.0, None)
    for label, vals in TUNER_SETS:
        tuners = make_tuners(vals)
        for bc_x in BC_X_GRID:
            for ruck_y in RUCK_Y_GRID:
                r = measure(bc_x, ruck_y, 4, 4, tuners)
                over = max(margin - r["outer_l"], r["outer_r"] - field_max,
                           0.0)
                if over > worst4[0]:
                    worst4 = (over, "tuner=%s bc.x=%.1f ruck_y=%.1f "
                                    "L=%.6f R=%.6f"
                                    % (label, bc_x, ruck_y, r["outer_l"],
                                       r["outer_r"]))
                check(
                    "T4 %s bc.x=%.1f ruck_y=%.1f lanes inside the margins"
                    % (label, bc_x, ruck_y),
                    over <= 1e-6,
                    "L=%.6f R=%.6f band=[%.2f, %.2f]"
                    % (r["outer_l"], r["outer_r"], margin, field_max),
                )
    print("      worst margin overrun = %.3e m" % worst4[0])
    if worst4[1]:
        print("      worst case: %s" % worst4[1])

    print("\n[T5] the realised span responds 1:1 to the tuner")
    base_t = make_tuners(dict(TUNER_SETS)["default"])
    wide_t = make_tuners(dict(TUNER_SETS)["wide"])
    shifted = 0
    for bc_x in BC_X_GRID:
        for ruck_y in RUCK_Y_GRID:
            b = measure(bc_x, ruck_y, 4, 4, base_t)
            w = measure(bc_x, ruck_y, 4, 4, wide_t)
            d_gov = w["governed"] - b["governed"]
            d_span = w["span"] - b["span"]
            if abs(d_gov) <= TOL:
                continue
            shifted += 1
            check(
                "T5 bc.x=%.1f ruck_y=%.1f realised delta == governed delta"
                % (bc_x, ruck_y),
                abs(d_span - d_gov) <= TOL,
                "d_span=%+.9f d_governed=%+.9f" % (d_span, d_gov),
            )
    print("      cases where the tuner actually moved the governed width: %d"
          % shifted)

    print("\n" + "=" * 76)
    if failures:
        print("RESULT: %d FAILURE(S) of %d checks" % (len(failures), checks[0]))
        for name in failures[:20]:
            print("   - %s" % name)
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED (%d checks)" % checks[0])
    print("  - the outermost left/right lanes span EXACTLY governed_width_m")
    print("    at the interpolated bc.x on every grid case")
    print("  - the two published halves SUM to the governed width")
    print("  - the only live bound is the absolute sideline clamp")
    sys.exit(0)


if __name__ == "__main__":
    main()
