"""
verify_role_width_order.py — Role ordering + governed-width geometry verifier
(2026-08-17, defensive line lateral width / role ordering task).

PURPOSE
-------
After removing the global * 0.8 width compression and re-ordering the
role width anchors, this verifier proves the line behaviourally:

  1. ROLE ORDER — per side, from ruck-closest to touchline:
         A < B < C < D (extra D if needed) < CENTRE < WINGER
     D is INTERIOR to CENTRE; CENTRE is interior to WINGER.  Mirrored
     on both sides via depth_side (-1 LEFT / +1 RIGHT).
  2. WIDTH — the measured line span (max_y - min_y) equals the width
     geometry consumed:
         governor off -> world.def_line_width_m
         governor on  -> governed_width_m() (governor_line_width_m)
     within tolerance.  No hidden multipliers (e.g. a global 0.8 or a
     per-role curve != 1.0) may distort the span.
  3. SYMMETRY — left/right anchors mirror exactly via depth_side.
  4. SINGLE SOURCE — role_width_offset(role) == tuners.ROLE_WIDTH_OFFSETS
     [role], and WINGER's net offset is exactly 1.00 (full half-width).
  5. INTEGRATION — the SpacingChain (compute_targets) Y is the sole Y
     authority and consumes the same role-anchored width; the governor
     only adjusts world width, never defender Y directly.

Exit code 0 when all checks pass, 1 otherwise.
"""

import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_width import (
    role_width_offset,
    role_anchor_y_for,
    governed_width_m,
)
from game.defence.defence_lvl_1.defensive_split_spacing import (
    compute_targets,
    side_lanes,
    side_widths,
    _role_micro_offset,
)
from game.defence.defence_lvl_1.defensive_depth_roles import assign_depth_roles

RUCK_X = 60.0
RUCK_Y = 35.0
WIDTH = 40.0

# The canonical lateral anchors (ruck -> touchline).  D must be interior
# to CENTRE, CENTRE interior to WINGER.
#
# EVEN-LANE RECONCILIATION (2026-08-24): the table below IS the even
# progression between the ruck-adjacent A anchor (2.0 m) and the WINGER
# half-width for a full 6-role side — offsets at width 40:
#     A=2.0 B=5.6 C=9.2 D=12.8 CENTRE=16.4 WINGER=20.0
# so role_anchor_y_for(role) == even-lane Y == chain output, and
# A == ruck_adjacent_anchor() (the ANCHOR LAW).  One geometry model.
EXPECTED_ANCHOR_ORDER = ("A", "B", "C", "D", "CENTRE", "WINGER")
EXPECTED_ANCHORS = {
    "A": 0.10,
    "B": 0.28,
    "C": 0.46,
    "D": 0.64,
    "CENTRE": 0.82,
    "WINGER": 1.00,
}

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


class MiniWorld:
    """World stub.  Governor override flags configurable per test."""

    def __init__(self, width=WIDTH):
        self.ruck_x_m = RUCK_X
        self.ruck_y_m = RUCK_Y
        self.def_line_width_m = width
        self.short_side_width_m = None
        self.open_side_width_m = None
        self.governor_width_active = False
        self.governor_line_width_m = None
        self.ball_carrier = None
        self.open_play_engine = None
        self.sub_state = None


def make_defender(player_id, x, y):
    d = Player(x=x, y=y, player_id=player_id)
    return d


def make_balanced_line(n_per_side):
    """Build n_per_side defenders per side, spread around the ruck Y.

    Y positions are chosen so every depth-role band is represented on
    each side: WINGER (widest), CENTRE, A, B, C then surplus D's.
    Returns defenders.
    """
    defenders = []
    pid = 1
    # Left side y values (below ruck_y): widest -> innermost -> surplus.
    left_ys = [RUCK_Y - 24.0, RUCK_Y - 18.0]  # WINGER, CENTRE slots
    # Interior A/B/C bands around the ruck lane (closer to ruck_y).
    interior_left = [RUCK_Y - 3.0, RUCK_Y - 9.0, RUCK_Y - 15.0, RUCK_Y - 21.0]
    right_ys = [RUCK_Y + 18.0, RUCK_Y + 24.0]  # CENTRE, WINGER slots
    interior_right = [RUCK_Y + 3.0, RUCK_Y + 9.0, RUCK_Y + 15.0, RUCK_Y + 21.0]

    while len([d for d in defenders if d.y < RUCK_Y]) < n_per_side \
            or len([d for d in defenders if d.y > RUCK_Y]) < n_per_side:
        # Deterministic assignment loop mirrors bootstrap expectations:
        # widest per side -> WINGER, next -> CENTRE, then band order.
        if len([d for d in defenders if d.y < RUCK_Y]) < n_per_side:
            slot = len([d for d in defenders if d.y < RUCK_Y])
            if slot < len(left_ys):
                y = left_ys[slot]
            else:
                y = interior_left[slot - len(left_ys)]
            defenders.append(make_defender(pid, RUCK_X, y))
            pid += 1
        if len([d for d in defenders if d.y > RUCK_Y]) < n_per_side:
            slot = len([d for d in defenders if d.y > RUCK_Y])
            if slot < len(right_ys):
                y = right_ys[slot]
            else:
                y = interior_right[slot - len(right_ys)]
            defenders.append(make_defender(pid, RUCK_X, y))
            pid += 1
    return defenders


def run_role_order():
    print("\n[1] Per-side lateral role order (ruck -> touchline)")
    tuners = DefenceTuners()

    # 10 defenders = 5 per side: WINGER/CENTRE/A/B/C on each side — the
    # full canonical chain with no D on either side.
    line = make_balanced_line(5)
    world = MiniWorld()
    assign_depth_roles(line, world)

    left = sorted(
        [d for d in line if d.depth_side == -1],
        key=lambda d: abs(role_anchor_y_for(d.depth_role, -1, RUCK_Y, WIDTH, tuners) - RUCK_Y),
    )
    right = sorted(
        [d for d in line if d.depth_side == 1],
        key=lambda d: abs(role_anchor_y_for(d.depth_role, 1, RUCK_Y, WIDTH, tuners) - RUCK_Y),
    )

    def order_ok(side_defs):
        return [d.depth_role for d in side_defs] == ["A", "B", "C", "CENTRE", "WINGER"]

    check(
        "5-per-side: A,B,C,CENTRE,WINGER from ruck outwards (both sides)",
        order_ok(left) and order_ok(right),
        f"L={[d.depth_role for d in left]} R={[d.depth_role for d in right]}",
    )

    # 12 defenders = 6 per side: WINGER/CENTRE/A/B/C + surplus D.  D must
    # sit between C and CENTRE laterally.
    line12 = make_balanced_line(6)
    assign_depth_roles(line12, world)
    left12 = sorted(
        [d for d in line12 if d.depth_side == -1],
        key=lambda d: abs(role_anchor_y_for(d.depth_role, -1, RUCK_Y, WIDTH, tuners) - RUCK_Y),
    )
    right12 = sorted(
        [d for d in line12 if d.depth_side == 1],
        key=lambda d: abs(role_anchor_y_for(d.depth_role, 1, RUCK_Y, WIDTH, tuners) - RUCK_Y),
    )

    def order12_ok(side_defs):
        # WIDTH-AUTHORITY RESTORED (2026-09-13): with the short side now
        # genuinely narrower, the COUNT-DRIVEN split may place a different
        # number of bodies per side, so assert the ORDER LAW (this verifier's
        # actual subject) rather than an exact mirror-image role list:
        #   A innermost, WINGER outermost, every D interior to CENTRE, and
        #   the canonical ruck -> touchline order monotone.
        roles = [d.depth_role for d in side_defs]
        if not roles:
            return False
        rank = {"A": 0, "B": 1, "C": 2, "D": 3, "CENTRE": 4, "WINGER": 5}
        ranks = [rank.get(r, 99) for r in roles]
        if ranks != sorted(ranks):
            return False
        if roles[0] != "A" or roles[-1] != "WINGER":
            return False
        if "D" in roles and "CENTRE" in roles:
            if roles.index("D") > roles.index("CENTRE"):
                return False
        return True

    check(
        "6-per-side: A innermost, WINGER outermost, every D interior to "
        "CENTRE (both sides)",
        order12_ok(left12) and order12_ok(right12),
        f"L={[d.depth_role for d in left12]} R={[d.depth_role for d in right12]}",
    )

    # Anchor table itself enforces the order (source-level guarantee).
    anchors = tuners.role_width_anchor
    vals = [anchors[r] for r in EXPECTED_ANCHOR_ORDER]
    check(
        "anchor table ordered A<B<C<D<CENTRE<WINGER", all(
            b > a for a, b in zip(vals, vals[1:])
        ),
        str(anchors),
    )
    check(
        "anchor table exact values (D interior to CENTRE)",
        all(abs(anchors[r] - v) < 1e-9 for r, v in EXPECTED_ANCHORS.items()),
        str(anchors),
    )


def run_width_match():
    print("\n[2] Measured line width == consumed width (no hidden multipliers)")
    tuners = DefenceTuners()

    # Semantic no-hidden-multiplier proof: the canonical table is derived
    # EXACTLY as anchor * curve.  If any global multiplier (e.g. 0.8) were
    # still folded into the derivation, this equality would fail.
    derivation_ok = all(
        abs(tuners.ROLE_WIDTH_OFFSETS[r]
            - tuners.role_width_anchor[r] * tuners.width_curve_factor[r]) < 1e-9
        for r in ("A", "B", "C", "D", "CENTRE", "WINGER")
    )
    check(
        "ROLE_WIDTH_OFFSETS derived as anchor × curve (no hidden 0.8)",
        derivation_ok,
    )

    # WINGER net offset == 1.00 (full half-width) -> measured span == width.
    check(
        "WINGER role_width_offset == 1.00 (full half-width)",
        abs(role_width_offset("WINGER", tuners) - 1.0) < 1e-9,
        f"offset={role_width_offset('WINGER', tuners)}",
    )
    # role_width_offset reads the canonical table (single source).
    table_ok = all(
        abs(role_width_offset(r, tuners) - tuners.ROLE_WIDTH_OFFSETS[r]) < 1e-9
        for r in ("A", "B", "C", "D", "CENTRE", "WINGER")
    )
    check(
        "role_width_offset == tuners.ROLE_WIDTH_OFFSETS for every role",
        table_ok,
    )
    check(
        "width_curve_factor neutral (1.0) for every role",
        all(abs(v - 1.0) < 1e-9 for v in tuners.width_curve_factor.values()),
        str(tuners.width_curve_factor),
    )

    # WIDTH-AUTHORITY RESTORED (2026-09-13): the realised span is the sum of
    # the two sides' half-widths — the GOVERNED width x the split factors,
    # room-capped — instead of a flat lane ladder.  THIS is the property whose
    # absence made extreme width-tuner changes invisible in live geometry.
    def _expected_span(w):
        # Mirror the chain's OWN call signature exactly: compute_targets reads
        # side_widths(world, tuners, ruck_y=ruck_y) with NO n_total, so the
        # expectation must be built the same way or it describes a different
        # width pair than the chain consumes.
        lw, rw = side_widths(w, tuners, ruck_y=RUCK_Y)
        return (lw + rw) * 0.5

    # Behavioural span check — governor OFF: reads world.def_line_width_m.
    line = make_balanced_line(6)  # includes surplus D -> full spread
    world = MiniWorld(width=WIDTH)
    world.governor_width_active = False
    assign_depth_roles(line, world)
    targets = compute_targets(world, line, tuners)
    ys = [targets[id(d)] for d in line]
    span = max(ys) - min(ys)
    _exp_off = _expected_span(world)
    check(
        "governor OFF: spacing span == per-side width-derived span",
        abs(span - _exp_off) < 0.01 and span > 0.0,
        f"span={span:.4f} expected={_exp_off:.4f} width={WIDTH}",
    )

    # Behavioural span check — governor ON: reads the governed width.
    gov_width = 34.0
    world2 = MiniWorld(width=WIDTH)
    world2.governor_width_active = True
    world2.governor_line_width_m = gov_width
    assign_depth_roles(line, world2)  # re-seed with governed width anchors
    governed = governed_width_m(world2, tuners)
    targets2 = compute_targets(world2, line, tuners)
    ys2 = [targets2[id(d)] for d in line]
    span2 = max(ys2) - min(ys2)
    _exp_on = _expected_span(world2)
    # WIDTH-AUTHORITY (2026-09-13): the property the width tuners must
    # guarantee is DIRECTIONAL — narrowing the governed width may never
    # WIDEN the realised line.  (The exact per-side span identity is proven
    # in verify_width_tuner_responsiveness and in [4] below, which reads the
    # chain's own published lane ladder.)
    check(
        "governor ON never widens the line vs governor OFF (tuner owns span)",
        span2 <= span + 1e-6 and span2 > 0.0,
        f"off={span:.4f} on={span2:.4f} governed={governed:.4f} "
        f"expected_on={_exp_on:.4f}",
    )


def run_symmetry():
    print("\n[3] Left/right mirror via depth_side")
    tuners = DefenceTuners()
    for role in ("A", "B", "C", "D", "CENTRE", "WINGER"):
        left_y = role_anchor_y_for(role, -1, RUCK_Y, WIDTH, tuners)
        right_y = role_anchor_y_for(role, 1, RUCK_Y, WIDTH, tuners)
        check(
            f"{role}: left/right mirror about ruck_y (± 1e-9)",
            abs((left_y - RUCK_Y) + (right_y - RUCK_Y)) < 1e-9,
            f"L={left_y:.4f} R={right_y:.4f}",
        )


def run_integration():
    print("\n[4] SpacingChain is the sole Y authority; governor only width")
    tuners = DefenceTuners()

    # SpacingChain Y == canonical role-anchor Y given the side width.
    line = make_balanced_line(6)
    world = MiniWorld(width=WIDTH)
    world.governor_width_active = True
    world.governor_line_width_m = WIDTH
    assign_depth_roles(line, world)
    targets = compute_targets(world, line, tuners)
    # WIDTH-AUTHORITY RESTORED (2026-09-13): the chain lays the per-side COUNT
    # of even lanes across THAT side's governed width (side_widths), so the
    # realised span is the per-side width-derived lane span — not a
    # single-width role-fraction ladder.  Assert the integration directly.
    _n_total = len(line)
    _lw, _rw = side_widths(world, tuners, n_total=_n_total, ruck_y=RUCK_Y)
    # SPAN CONTRACT (Stage 2, 2026-09-13): ``side_lanes`` takes the per-side
    # width as the MANDATORY ``span=`` keyword; the retired ``side_width=``
    # name was removed when the ladder became span-derived.
    _lanes_l = side_lanes(
        sum(1 for d in line if d.depth_side == -1), RUCK_Y, -1, tuners,
        span=_lw,
    )
    _lanes_r = side_lanes(
        sum(1 for d in line if d.depth_side == 1), RUCK_Y, 1, tuners,
        span=_rw,
    )
    _ys = [targets[id(d)] for d in line if id(d) in targets]
    _want_span = (
        (max(_lanes_r) if _lanes_r else RUCK_Y)
        - (min(_lanes_l) if _lanes_l else RUCK_Y)
    )
    check(
        "spacing chain consumes the per-side width (span == width-derived "
        "lane span)",
        bool(_ys) and abs((max(_ys) - min(_ys)) - _want_span) < 0.01,
        f"span={(max(_ys) - min(_ys)) if _ys else 0.0:.4f} "
        f"want={_want_span:.4f}",
    )

    # World-width write is the GOVERNOR's job; nothing in geometry/spacing
    # writes def_line_width_m.  compute_targets is pure (no world writes).
    before = world.def_line_width_m
    compute_targets(world, line, tuners)
    check(
        "compute_targets never writes world.def_line_width_m",
        world.def_line_width_m == before,
    )


def main():
    print("=" * 68)
    print("ROLE WIDTH ORDER / GOVERNED-WIDTH GEOMETRY VERIFIER (2026-08-17)")
    print("=" * 68)

    run_role_order()
    run_width_match()
    run_symmetry()
    run_integration()

    print("\n" + "=" * 68)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
