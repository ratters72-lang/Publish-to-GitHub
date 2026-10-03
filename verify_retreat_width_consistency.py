"""
verify_retreat_width_consistency.py — RETREAT spacing width == count-lane width.

Proves the retreat/PTB spacing pipeline uses EXACTLY the same count-and-width
model as the open-play geometry/spacing chain — the COUNT is the single boss
for multiplicity, and the GOVERNED WIDTH owns the span (2026-09-13):

    1. NO RETREAT-SPECIFIC WIDTH CURVE (source guard) — every file under
       ``game/defence/defence_lvl_1/`` is scanned for the forbidden
       retreat-specific width identifiers (``compute_retreat_width``,
       ``retreat_width_curve``, ``retreat_width_m``, ``compressed_width_m``,
       ``build_retreat_split_spacing``).  Any occurrence fails the suite.

    2. WIDTH IDENTITY — ``side_widths(n_total=…)`` returns the GOVERNED
       per-side widths (3-point tuner interpolation → split factor →
       room cap), and the lane ladder is built FROM that width so the
       outermost lane lands on the side's width anchor.

    3. OPEN-PLAY SPAN — a full 7-defender line (depth roles assigned) run
       through the split-spacing chain yields a span equal to the
       width-derived lane span (outermost-right lane − outermost-left lane,
       both built with the per-side governed width).

    4. PTB / UNIFIED RETREAT SPAN — the unified forced-retreat writer builds
       ``retreat_spacing_targets`` via ``compute_targets`` (the same single
       Y authority); the retreat dict spans the count-lane width and every
       ``_frame_spacing_y`` value is byte-identical to the chain Y.

    5. SPAN INVARIANT UNDER ENGAGEMENT-BAND PULL — the Task IV
       engagement-band spacing-Y pull is a constant per-band offset applied
       to every defender, so the span stays at the width-derived lane span
       with a live ball carrier.

    6. WRITER WIRING — the unified source contains the spacing-authority
       retreat writers and NO legacy two-arg current-Y fallback.

Emits verify_retreat_width_report.json; exits 0 when all checks pass.
"""

import json
import os
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_split_spacing import (
    compute_targets as _split_compute_targets,
    side_widths as _side_widths,
    decide_side_counts,
    side_lanes,
    LEFT,
    RIGHT,
)
from game.defence.defence_lvl_1.defensive_pipeline import (
    frame_spacing_y as _frame_spacing_y,
)
from game.defence.defence_lvl_1.defensive_depth_roles import assign_depth_roles

# STAGE 3B: the unified forced-retreat writer now lives in the
# DefenceManager-owned RetreatManager (sole retreat authority).
UNIFIED_SRC_PATH = "game/managers/retreat_manager.py"
DEFENCE_LVL1_DIR = "game/defence/defence_lvl_1"

# Forbidden retreat-specific width identifiers — the exact names from the
# spec's "retreat width curve" bug pattern.  NONE may exist in the codebase.
FORBIDDEN_WIDTH_IDS = (
    "compute_retreat_width",
    "retreat_width_curve",
    "retreat_width_m",
    "compressed_width_m",
    "build_retreat_split_spacing",
)

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


class MiniWorld:
    phase = "OPEN_PLAY"
    sub_state = "OPEN_PLAY"
    ball_carrier = None
    ball = None
    ruck_x_m = 60.0
    ruck_y_m = 35.0
    def_line_width_m = 40.0
    ball_x_m = 60.0
    ball_y_m = 35.0
    defensive_line_anchor = 70.0
    anchor_locked = False
    in_pursuit_mode = False
    first_tackle_not_made = False
    governor_width_active = False
    governor_line_width_m = None
    sim_time_s = 0.0
    frame_count = 0
    short_side_width_m = None
    open_side_width_m = None
    defending_team = []


class MiniBC:
    x = 65.0
    y = 35.0
    vx = 0.0
    vy = 0.0


def role_defender(role, side, player_id, x=60.0):
    """Build a defender with an explicit depth_role + persistent depth_side."""
    d = Player(x=x, y=35.0 + side * 10.0, player_id=player_id)
    d.depth_role = role
    d.depth_side = side
    return d


def make_full_line():
    """Full 7-defender line: 3 LEFT (WINGER/CENTRE/A) + 4 RIGHT (WINGER/CENTRE/A/B)."""
    left = [
        role_defender("WINGER", -1, 101),
        role_defender("CENTRE", -1, 102),
        role_defender("A", -1, 103),
    ]
    right = [
        role_defender("WINGER", 1, 201),
        role_defender("CENTRE", 1, 202),
        role_defender("A", 1, 203),
        role_defender("B", 1, 204),
    ]
    return left + right


def span_of(targets) -> float:
    """max(targets.values()) - min(targets.values())."""
    return max(targets.values()) - min(targets.values())


def count_lane_span(world, line, tuners) -> float:
    """WIDTH-derived lane span: outermost-right lane − outermost-left lane.

    WIDTH-AUTHORITY RESTORED (2026-09-13): the COUNT decides how many lanes
    each side gets; the per-side GOVERNED width spaces them.  The expected
    span must therefore be computed from lanes built WITH the per-side
    width — the flat defender_gap_m fallback ladder describes a different
    geometry than the engine now produces.
    """
    n = len([d for d in line if not getattr(d, "fullback_role_active", False)])
    ruck_y = float(getattr(world, "ruck_y_m", 35.0))
    n_left, n_right = decide_side_counts(world, tuners, n, ruck_y=ruck_y)
    lw, rw = _side_widths(world, tuners, n_total=n, ruck_y=ruck_y)
    lanes_l = side_lanes(n_left, ruck_y, LEFT, tuners, span=lw)
    lanes_r = side_lanes(n_right, ruck_y, RIGHT, tuners, span=rw)
    all_lanes = (lanes_l if lanes_l else []) + (lanes_r if lanes_r else [])
    return max(all_lanes) - min(all_lanes) if all_lanes else 0.0


# ─────────────────────────────────────────────────────────────────────
# 1. NO RETREAT-SPECIFIC WIDTH CURVE (source guard)
# ─────────────────────────────────────────────────────────────────────
def run_section1():
    print("\n[1] No retreat-specific width curve in the defence modules")
    hits = []
    for root, _dirs, files in os.walk(DEFENCE_LVL1_DIR):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for i, ln in enumerate(fh, 1):
                    for ident in FORBIDDEN_WIDTH_IDS:
                        if ident in ln:
                            hits.append(f"{path}:{i}: {ln.strip()[:80]}")
    check("scanned defence_lvl_1 for forbidden width identifiers", True,
          f"{len(hits)} hit(s) scanned")
    check(
        "no compute_retreat_width / retreat_width_curve / retreat_width_m / "
        "compressed_width_m / build_retreat_split_spacing anywhere",
        not hits,
        "; ".join(hits) or "clean",
    )


# ─────────────────────────────────────────────────────────────────────
# 2. WIDTH IDENTITY — retreat width source == count-derived width
# ─────────────────────────────────────────────────────────────────────
def run_section2():
    print("\n[2] Retreat spacing width source == count-derived width")
    world = MiniWorld()
    tuners = DefenceTuners()
    n_total = 4
    lw, rw = _side_widths(world, tuners, n_total=n_total, ruck_y=world.ruck_y_m)
    n_left, n_right = decide_side_counts(
        world, tuners, n_total, ruck_y=world.ruck_y_m,
    )
    # WIDTH-AUTHORITY RESTORED (2026-09-13): the per-side width is the
    # GOVERNED width (3-point tuner interpolation) x the side split factor,
    # room-capped — NOT an output of the lane ladder.  Assert the restored
    # direction instead.
    from game.defence.defence_lvl_1.defensive_width import governed_width_m
    _eff = governed_width_m(world, tuners)
    check(
        "side widths are governed-width derived (never wider than the governor)",
        lw > 0.0 and rw > 0.0 and max(lw, rw) <= _eff + 1e-9,
        f"left={lw} right={rw} governed={_eff} "
        f"n_left={n_left} n_right={n_right}",
    )
    _anchor_bad = []
    for _w, _side, _n in ((lw, LEFT, n_left), (rw, RIGHT, n_right)):
        if int(_n) < 2:
            continue
        _a_piv = world.ruck_y_m + _side * 2.0
        _want = world.ruck_y_m + _side * float(_w) * 0.5
        if abs(_want - _a_piv) / float(int(_n) - 1) < \
                float(tuners.min_defender_gap_m) - 1e-9:
            # The count exceeds what this width can space, so the step floor
            # legitimately binds — skip rather than assert.
            continue
        _lanes = side_lanes(int(_n), world.ruck_y_m, _side, tuners,
                            span=float(_w))
        if abs(_lanes[-1] - _want) > 1e-6:
            _anchor_bad.append(
                f"side={_side} outer={_lanes[-1]:.4f} want={_want:.4f}"
            )
    check(
        "width-derived outermost lane lands on the side width anchor",
        not _anchor_bad, "; ".join(_anchor_bad),
    )
    # The WINGER placement is PROPORTIONAL to the side width: doubling the
    # width pushes the outermost lane further out (this is the property whose
    # absence made extreme width-tuner changes invisible).
    _narrow = side_lanes(2, world.ruck_y_m, RIGHT, tuners, span=20.0)[-1]
    _wide = side_lanes(2, world.ruck_y_m, RIGHT, tuners, span=40.0)[-1]
    check(
        "a wider side width places the WINGER further out (proportional)",
        _wide > _narrow + 1e-9,
        f"narrow={_narrow:.3f} wide={_wide:.3f}",
    )


# ─────────────────────────────────────────────────────────────────────
# 3. OPEN-PLAY SPAN — full line, spacing dict span == count-lane span
# ─────────────────────────────────────────────────────────────────────
def run_section3():
    print("\n[3] OPEN-PLAY spacing span == count-lane derived span")
    world = MiniWorld()
    tuners = DefenceTuners()
    line = make_full_line()
    assign_depth_roles(line, world)
    targets = _split_compute_targets(world, line, tuners)

    roles = {getattr(d, "depth_role", None) for d in line}
    check("full line active (7 defenders, 6 canonical roles)", len(line) == 7)
    check("depth roles assigned across the line", roles <= {
        "WINGER", "CENTRE", "A", "B", "D",
    }, str(sorted(roles)))

    _cspan = count_lane_span(world, line, tuners)
    span = span_of(targets)
    check(
        "open-play spacing span == count-lane derived span",
        abs(span - _cspan) < 0.01,
        f"span={span:.4f} expected={_cspan:.4f}",
    )

    wingers = [d for d in line if getattr(d, "depth_role", None) == "WINGER"]
    wspan = max(targets[id(d)] for d in wingers) - min(
        targets[id(d)] for d in wingers
    )
    check(
        "two-WINGER span == count-lane derived span (± 0.01)",
        abs(wspan - _cspan) < 0.01,
        f"winger_span={wspan:.4f} expected={_cspan:.4f}",
    )

    # Every chain Y derives from the count-lane model and stays on-field.
    margin = float(getattr(tuners, "sideline_margin_m", 3.0))
    field_max = float(getattr(tuners, "field_width_m", 70.0)) - margin
    all_on_field = all(
        margin - 1e-6 <= targets[id(d)] <= field_max + 1e-6
        for d in line if id(d) in targets
    )
    check(
        "no Y crosses the sideline margin (on-field count-lane)",
        all_on_field,
        f"ys={[round(targets[id(d)], 2) for d in line if id(d) in targets]}",
    )


# ─────────────────────────────────────────────────────────────────────
# 4. PTB / UNIFIED RETREAT SPAN — retreat dict == count-lane span
# ─────────────────────────────────────────────────────────────────────
def run_section4():
    print("\n[4] PTB / unified forced-retreat span == count-lane span")
    world = MiniWorld()
    tuners = DefenceTuners()
    line = make_full_line()
    for d in line:
        d.x = 60.0
    world.defending_team = list(line)
    assign_depth_roles(line, world)

    from game.defence.defence_lvl_1.defensive_pipeline import (
        run_defensive_pipeline,
    )
    world.frame_count = 1
    spacing_fresh, _width_fresh = run_defensive_pipeline(world, line, tuners)
    retreat_targets = dict(spacing_fresh)
    _cspan = count_lane_span(world, line, tuners)

    span = span_of(retreat_targets)
    check(
        "PTB/unified retreat spacing span == count-lane derived span",
        abs(span - _cspan) < 0.01,
        f"span={span:.4f} expected={_cspan:.4f}",
    )

    identical = True
    for d in line:
        fy = _frame_spacing_y(world, d, tuners)
        if id(d) in retreat_targets and abs(fy - retreat_targets[id(d)]) > 1e-9:
            identical = False
    check(
        "retreat Y targets == frame-authoritative spacing Y (single source)",
        identical,
    )

    span_fresh = span_of(spacing_fresh)
    check(
        "un-gated preamble dict span == count-lane span (PTB-era)",
        abs(span_fresh - _cspan) < 0.01,
        f"span={span_fresh:.4f}",
    )


# ─────────────────────────────────────────────────────────────────────
# 5. SPAN INVARIANT UNDER ENGAGEMENT-BAND PULL (live BC)
# ─────────────────────────────────────────────────────────────────────
def run_section5():
    print("\n[5] Span invariant under engagement-band pull (live BC)")
    world = MiniWorld()
    world.ball_carrier = MiniBC()
    tuners = DefenceTuners()
    line = make_full_line()
    assign_depth_roles(line, world)
    targets = _split_compute_targets(world, line, tuners)
    _cspan = count_lane_span(world, line, tuners)
    span = span_of(targets)
    check(
        "span stays count-lane span with a live BC (band pull is uniform)",
        abs(span - _cspan) < 0.01,
        f"span={span:.4f} expected={_cspan:.4f}",
    )


# ─────────────────────────────────────────────────────────────────────
# 6. WRITER WIRING — unified source uses spacing authority for retreat Y
# ─────────────────────────────────────────────────────────────────────
def run_section6():
    print("\n[6] Unified/PTB retreat writer consumes the spacing authority")
    src = open(UNIFIED_SRC_PATH, "r", encoding="utf-8", errors="replace").read()

    start_i = src.find("retreat_spacing_targets = {}")
    end_i = src.find("Now process only compliant")
    retreat_block = src[start_i:end_i]
    check("unified forced-retreat block located",
          start_i != -1 and end_i != -1 and end_i > start_i)
    check(
        "retreat targets read from the published frame dict (PURE CONSUMER)",
        "frame_spacing_targets" in retreat_block
        and "_split_retreat_targets(" not in retreat_block,
    )
    check(
        "retreat Y read from retreat_spacing_targets",
        "retreat_spacing_targets.get(id(d))" in retreat_block,
    )
    check(
        "retreat Y fallback is the frame-authoritative spacing",
        "_frame_spacing_y(" in retreat_block,
    )
    check(
        "no legacy current-Y fallback in the retreat writer",
        "retreat_spacing_targets.get(id(d), d.y)" not in retreat_block,
    )


def main():
    print("=" * 72)
    print("RETREAT SPACING WIDTH CONSISTENCY (RETREAT WIDTH == COUNT-LANE)")
    print("=" * 72)

    run_section1()
    run_section2()
    run_section3()
    run_section4()
    run_section5()
    run_section6()

    report = {
        "retreat_width_consistency": {
            "no_retreat_width_curve": failures == [],
            "retreat_span_matches_width_derived_lane_span": (
                "open-play spacing span == width-derived lane span"
                not in failures
                and "PTB/unified retreat spacing span == width-derived lane span"
                not in failures
            ),
            "issues_remaining": failures,
        }
    }

    report_path = "verify_retreat_width_report.json"
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print(f"\nReport written to {report_path}")

    print("\n" + "=" * 72)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED — retreat spacing width == width-derived span")
    sys.exit(0)


if __name__ == "__main__":
    main()
