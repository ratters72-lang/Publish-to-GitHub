"""
verify_governor_width.py — Validation for governor width activation
(Task D, 2026-08-09).

Confirms the DefensiveLineGovernor's compression/expansion is LIVE:

    1. GATE — governor_width_enabled defaults True; publishes only when
       enabled + not disabled + not pursuit + BC exists.
    2. PUBLISH — compute_line_state writes world.def_line_width_m and the
       activation flags (governor_width_active, governor_line_width_m).
    3. READER — defensive_width.governed_width_m() overrides the raw width
       with the governed width, bounded ONLY by the physical field cap.
       The width-floor / width-ceiling tuner band is REMOVED (2026-09-14,
       Option B), so the 3-point curve is the sole width authority.
    4. DISCUSSION — the governed width IS the width geometry/spacing consume
       (role_anchor_y receives governed_width_m at every callsite).
"""

import sys
import os

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_width import (
    governed_width_m,
    role_anchor_y,
    interpolated_defensive_width,
    LEFT_TRYLINE_X,
    HALFWAY_X,
    RIGHT_TRYLINE_X,
)
from game.defence.defence_lvl_1.defensive_line_governor import DefensiveLineGovernor
from player.player import Player

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


class MiniWorld:
    phase = None
    sub_state = "OPEN_PLAY"
    ball_carrier = None
    ball = None
    ruck_x_m = 60.0
    ruck_y_m = 35.0
    def_line_width_m = 40.0
    ball_x_m = 60.0
    ball_y_m = 35.0
    defensive_line_anchor = 70.0
    in_pursuit_mode = False
    first_tackle_not_made = False
    governor_width_active = False
    governor_line_width_m = None
    sim_time_s = 0.0


class MiniBC:
    x = 65.0
    y = 35.0
    vx = 0.0
    vy = 0.0


def main():
    print("=" * 60)
    print("GOVERNOR WIDTH ACTIVATION (Task D)")
    print("=" * 60)

    tuners = DefenceTuners()
    tuners.assert_radii()

    # ── 1. Tuner gate default ──────────────────────────────────────
    print("\n[1] Tuner gate")
    check(
        "governor_width_enabled default True",
        tuners.governor_width_enabled is True,
    )

    # ── 2. Governor publishes on compute_line_state ────────────────
    print("\n[2] Governor publishes governed width to the world")
    from game.defence.defence_lvl_1.defensive_geometry import DefensiveGeometry
    gov = DefensiveLineGovernor(tuners, geometry=DefensiveGeometry(tuners))
    world = MiniWorld()
    world.ball_carrier = MiniBC()

    # Bridge: governor reads world.def_line_width_m as its baseline and
    # publishes the interpolated width every frame.  First compute seeds,
    # then sweep the BC across the field and assert the published width is
    # EXACTLY the 3-point interpolation (the restored 2026-08-17 width
    # source — the legacy compression ramp is kept but no longer the
    # width source).
    gov.compute_line_state(world, [])
    first = gov.line_width
    win = interpolated_defensive_width(tuners, world.ball_carrier.x)
    check(
        "published width == interpolated_defensive_width(bc.x)",
        abs(first - win) < 1e-9,
        f"gov={first:.4f} interpolated={win:.4f}",
    )
    for x in (10.0, 35.0, HALFWAY_X, 85.0, RIGHT_TRYLINE_X):
        world.ball_carrier.x = x
        # The per-frame width delta clamp is SMOOTHING ONLY — reset its
        # baseline before each probe so the sweep measures the width
        # SOURCE (the 3-point interpolation) at each field position, not
        # the 1.0 m/frame convergence lag of a teleported BC.
        gov._prev_line_width = None
        gov.compute_line_state(world, [])
        expected = interpolated_defensive_width(tuners, x)
        check(
            f"bc.x={x}: world.def_line_width_m == interpolated width",
            abs(world.def_line_width_m - expected) < 1e-9,
            f"world={world.def_line_width_m:.4f} expected={expected:.4f}",
        )
        # The published width matches the 3-point tuners exactly: no
        # compression ramp, no hidden multiplier redefines it.
        check(
            f"bc.x={x}: governor width == exact 3-point tuner lerp",
            abs(gov.line_width - expected) < 1e-9,
            f"gov={gov.line_width:.4f}",
        )
    # Per-frame delta clamp is SMOOTHING only: a 1-frame BC jump still
    # yields the interpolated width (the clamp allows a 1.0 m/frame step,
    # and the interpolation is bounded at <= 0.1 m/frame across the full
    # field — so the clamp never deviates the steady-state publication).
    world.ball_carrier.x = HALFWAY_X
    gov.compute_line_state(world, [])
    mid_width = gov.line_width
    world.ball_carrier.x = RIGHT_TRYLINE_X
    gov.compute_line_state(world, [])
    end_width = gov.line_width
    check(
        "width tracks field position: opp-tryline wider than halfway "
        "(3-point interpolation, not BC-proximity compression)",
        end_width > mid_width,
        f"halfway={mid_width:.4f} opp={end_width:.4f}",
    )
    check(
        "governor activated on world",
        getattr(world, "governor_width_active", False) is True,
    )
    check(
        "world.def_line_width_m overridden by governor",
        world.def_line_width_m == gov.line_width,
        f"world={world.def_line_width_m} gov={gov.line_width}",
    )
    check(
        "governor_line_width_m published",
        world.governor_line_width_m == gov.line_width,
    )

    # ── 3. Reader: governed_width_m overrides raw ──────────────────
    print("\n[3] governed_width_m reader")
    world.def_line_width_m = 40.0
    world.governor_width_active = True
    world.governor_line_width_m = 33.0
    governed = governed_width_m(world, tuners)
    check(
        "governed width overrides raw 40.0",
        governed == 33.0,
        f"governed={governed}",
    )
    # ── NO MINIMUM-WIDTH RAIL (2026-09-14, Option B) ──
    # The former width-floor tuner is REMOVED.  The 3-point width curve is
    # the sole width authority, so a small requested width is realised
    # EXACTLY: requested == intended, with no silent raise.
    world.governor_line_width_m = 5.0
    check(
        "governed width realises a sub-floor request EXACTLY (no floor)",
        governed_width_m(world, tuners) == 5.0,
        f"got={governed_width_m(world, tuners)} (requested 5.0)",
    )
    world.governor_line_width_m = 0.0
    check(
        "governed width realises zero width EXACTLY (no floor)",
        governed_width_m(world, tuners) == 0.0,
        f"got={governed_width_m(world, tuners)} (requested 0.0)",
    )
    # The ONE surviving bound is the PHYSICAL FIELD CAP
    # (field_width_m - 2 * sideline_margin_m = 64.0): a winger at full
    # half-width may not stand off the field.  That is geometry, not a rail.
    sideline_effective_max = (
        tuners.field_width_m - tuners.sideline_margin_m
    ) - tuners.sideline_margin_m
    world.governor_line_width_m = 999.0
    check(
        "governed width capped by the physical field cap",
        governed_width_m(world, tuners) == sideline_effective_max,
        f"got={governed_width_m(world, tuners)} expected={sideline_effective_max}",
    )
    world.governor_line_width_m = 150.0
    check(
        "governed width capped to the sideline margins (physical)",
        governed_width_m(world, tuners) == sideline_effective_max,
        f"got={governed_width_m(world, tuners)}",
    )

    # ── 4. Disabled/gated → raw width unchanged ────────────────────
    print("\n[4] Gate off → raw width")
    world.governor_width_active = False
    world.def_line_width_m = 40.0
    check(
        "gate off returns raw width",
        governed_width_m(world, tuners) == 40.0,
    )

    # ── 4a. Governor.governed_width_m() (Task D) ──────────────────
    print("\n[4a] Governor.governed_width_m() clamps")
    gov_reader = DefensiveLineGovernor(tuners, geometry=DefensiveGeometry(tuners))
    # No world → falls back to governor's own smoothed width.
    gov_reader.line_width = 33.0
    check(
        "governed_width_m() no-world uses smoothed width",
        gov_reader.governed_width_m() == 33.0,
        f"got={gov_reader.governed_width_m()}",
    )
    # NO MINIMUM RAIL (2026-09-14, Option B): the width curve is the sole
    # authority, so a sub-floor request is returned EXACTLY.  The governor's
    # accessor and the module accessor share ONE body, so they agree.
    world.def_line_width_m = 5.0
    check(
        "governed_width_m() returns a sub-floor request EXACTLY (no floor)",
        gov_reader.governed_width_m(world) == 5.0,
        f"got={gov_reader.governed_width_m(world)} (requested 5.0)",
    )
    # The only bound is the physical field cap.
    sideline_effective_max = (
        tuners.field_width_m - tuners.sideline_margin_m
    ) - tuners.sideline_margin_m
    world.def_line_width_m = 999.0
    check(
        "governed_width_m() capped by the physical field cap",
        gov_reader.governed_width_m(world) == sideline_effective_max,
        f"got={gov_reader.governed_width_m(world)} expected={sideline_effective_max}",
    )
    world.def_line_width_m = 150.0
    check(
        "governed_width_m() respects the physical sideline cap",
        gov_reader.governed_width_m(world) == sideline_effective_max,
        f"got={gov_reader.governed_width_m(world)} max_total={sideline_effective_max}",
    )

    # ── 4b. Governor.apply_width_clamp() (Task D) ──────────────────
    print("\n[4b] Governor.apply_width_clamp() governed band")
    world.def_line_width_m = 40.0
    world.ruck_y_m = 35.0
    # Prime the governor's ruck-Y stash + clamp baseline.
    gov_reader.compute_line_state(world, [])
    half = gov_reader.governed_width_m(world) * 0.5
    band_lo = 35.0 - half
    band_hi = 35.0 + half
    # In-band Y unchanged.
    in_band = 35.0
    check(
        "in-band Y unchanged by apply_width_clamp",
        gov_reader.apply_width_clamp(in_band, world) == in_band,
    )
    # Below the band clamps to the band floor.
    check(
        "below-band Y clamps to band floor",
        gov_reader.apply_width_clamp(band_lo - 10.0, world) == band_lo,
        f"got={gov_reader.apply_width_clamp(band_lo - 10.0, world):.2f}",
    )
    # Above the band clamps to the band ceiling.
    check(
        "above-band Y clamps to band ceiling",
        gov_reader.apply_width_clamp(band_hi + 10.0, world) == band_hi,
        f"got={gov_reader.apply_width_clamp(band_hi + 10.0, world):.2f}",
    )
    # Never crosses the sideline margin even at narrow ruck positions.
    world.ruck_y_m = 2.0  # near the touchline
    gov_reader.compute_line_state(world, [])
    check(
        "edge ruck clamp never crosses sideline margin",
        gov_reader.apply_width_clamp(0.0, world) >= tuners.sideline_margin_m,
        f"got={gov_reader.apply_width_clamp(0.0, world):.2f}",
    )
    # Idempotent: applying twice never changes the result further.
    world.ruck_y_m = 35.0
    y_snap = 99.0
    once = gov_reader.apply_width_clamp(y_snap, world)
    twice = gov_reader.apply_width_clamp(once, world)
    check(
        "apply_width_clamp is idempotent",
        once == twice,
        f"once={once} twice={twice}",
    )

    # ── 4c. World flag cleared on zero-state paths (Task D) ────────
    print("\n[4c] World governor flags truthful on zero-state")
    world.governor_width_active = True
    world.governor_line_width_m = 33.0
    world.ball_carrier = None  # no BC → zero state
    gov_reader.compute_line_state(world, [])
    check(
        "no-BC zero state clears governor_width_active",
        getattr(world, "governor_width_active", True) is False,
    )
    check(
        "no-BC zero state clears governor_line_width_m",
        getattr(world, "governor_line_width_m", 33.0) is None,
    )
    world.ball_carrier = MiniBC()
    world.in_pursuit_mode = True
    gov_reader.compute_line_state(world, [])
    check(
        "pursuit zero state clears governor_width_active",
        getattr(world, "governor_width_active", True) is False,
    )
    world.in_pursuit_mode = False

    # ── 5. Geometry consumes governed width ────────────────────────
    print("\n[5] Role anchors consume governed width")
    world.governor_width_active = True
    world.governor_line_width_m = 33.0
    w = governed_width_m(world, tuners)
    d = Player(x=60.0, y=30.0, player_id=1)
    d.depth_role = "WINGER"
    d.depth_side = 1
    check(
        "WINGER anchor uses governed width",
        role_anchor_y(d, 35.0, w, tuners) == role_anchor_y(d, 35.0, 33.0, tuners),
    )

    # ── 6. Split-spacing: the governed width owns the span ─────────
    print("\n[6] Split-spacing: the governed WIDTH owns the span")
    # WIDTH-AUTHORITY RESTORED (2026-09-13).  The governed width is the
    # SPAN input; the per-side COUNT is only the multiplicity sharing it.
    # The previous assertion here required ``side_widths`` to be an OUTPUT
    # of the count-lane ladder, which is precisely the inversion that made
    # the width tuners under-responsive: moving them could not widen the
    # line because the lanes were built from a flat defender_gap_m.
    try:
        from game.defence.defence_lvl_1.defensive_split_spacing import (
            side_widths,
            lane_gap_m,
            side_lanes,
            LEFT,
            RIGHT,
        )
        world.governor_width_active = True
        ruck_y = float(world.ruck_y_m)

        world.governor_line_width_m = 33.0
        lw_narrow, rw_narrow = side_widths(world, tuners, ruck_y=ruck_y)
        world.governor_line_width_m = 60.0
        lw_wide, rw_wide = side_widths(world, tuners, ruck_y=ruck_y)

        check(
            "governed width drives the per-side span (non-decreasing, one strict)",
            lw_narrow <= lw_wide + 1e-9 and rw_narrow <= rw_wide + 1e-9
            and (lw_wide - lw_narrow > 1e-6 or rw_wide - rw_narrow > 1e-6),
            f"narrow=({lw_narrow:.3f},{rw_narrow:.3f}) "
            f"wide=({lw_wide:.3f},{rw_wide:.3f})",
        )

        # The outermost lane lands on the side's width anchor, so the
        # width tuners define the REALISED line span.
        anchor_bad = []
        for width, side in ((lw_wide, LEFT), (rw_wide, RIGHT)):
            lanes = side_lanes(5, ruck_y, side, tuners, span=width)
            want = ruck_y + side * float(width) * 0.5
            if abs(lanes[-1] - want) > 1e-6:
                anchor_bad.append(
                    f"side={side} outer={lanes[-1]:.4f} want={want:.4f}"
                )
        check(
            "outermost lane lands on the side width anchor (ruck_y +/- w/2)",
            not anchor_bad, "; ".join(anchor_bad),
        )

        # COUNT is the multiplicity: more bodies on one span -> tighter step.
        g3 = lane_gap_m(lw_wide, 3, tuners)
        g8 = lane_gap_m(lw_wide, 8, tuners)
        check(
            "count owns the multiplicity (more defenders -> tighter step)",
            g8 < g3,
            f"n=3 step={g3:.4f} n=8 step={g8:.4f}",
        )
        # GOVERNED WIDTH IS THE AUTHORITATIVE TOTAL SPAN (2026-09-14): the
        # ``min_defender_gap_m`` FLOOR is REMOVED from ``lane_gap_m``.  It
        # pinned the step ABOVE ``span/(n-1)``, so the outermost lane overran
        # the published span, hit the sideline clamp and was re-spaced INWARD
        # -- the ladder stopped spanning the governed width (the
        # LANE_STEP_FLOOR reducer).  The derived step is now the PURE
        # span-derived value: ``(span/2 - ruck_adjacent_anchor) / (n - 1)``.
        want_step = (float(lw_wide) * 0.5 - 2.0) / 59.0
        check(
            "derived step is FLOORLESS (span-derived, no min_defender_gap_m pin)",
            abs(lane_gap_m(lw_wide, 60, tuners) - want_step) < 1e-9,
            f"step={lane_gap_m(lw_wide, 60, tuners):.4f} "
            f"span-derived={want_step:.4f}",
        )
    except Exception as exc:  # noqa: BLE001
        check("split-spacing import", False, str(exc))

    print("\n" + "=" * 60)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
