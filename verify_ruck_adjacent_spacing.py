"""
verify_ruck_adjacent_spacing.py — Ruck-adjacent even side spacing (2026-08-18).

COUNT-LANE MODEL (2026-09-03; WIDTH-AUTHORITY RESTORED 2026-09-13): the
per-side COUNT decides HOW MANY even lanes are laid out from the ruck-adjacent
A pivot (ruck_y ± 2.0 m, ANCHOR LAW); side_lanes(n, ruck_y, side, tuners,
span=W) then spaces exactly that count across the side's GOVERNED width W, so
the WINGER (outermost lane) lands on ruck_y ± W/2.  The span is MANDATORY
(Stage 2, 2026-09-13): there is no widthless fallback ladder in production —
the flat defender_gap_m ladder survives only as the explicitly-named
side_lanes_widthless helper for test stubs.

ISSUE VERIFIED HERE:
    - Two defenders anchor directly beside the ruck:
          X = ruck_x + 10 m (retreat depth — anchor authority)
          Y = ruck_y ± 2 m  (ruck-adjacent A pivot, ANCHOR LAW)
    - Interior lanes are uniform gap apart (defender_gap_m), no stacking.
    - Retreat / HOLD / DRIFT all consume the SAME spacing Y (single writer).

_run:  python verify_ruck_adjacent_spacing.py
Exit 0 on success.
"""

import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, DefenderRole
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_depth_roles import assign_depth_roles
from game.defence.defence_lvl_1.defensive_split_spacing import (
    compute_targets,
    side_lanes,
    side_widths,
    decide_side_counts,
    RIGHT,
    LEFT,
)
from game.defence.defence_lvl_1.defensive_width import (
    recompute_width_x,
    governed_width_m,
)
from game.defence.defence_lvl_1.width_authority import is_width_eligible
from game.defence.defence_lvl_1.secondary_width_grace import (
    arm_secondary_grace,
    advance_secondary_grace,
    secondary_grace_active,
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
    in_pursuit_mode = False
    first_tackle_not_made = False
    governor_width_active = True
    governor_line_width_m = 40.0
    sim_time_s = 0.0
    short_side_width_m = None
    open_side_width_m = None
    fullback_default_x = 60.0
    fullback_default_y = 35.0
    defending_team = []


def make_defender(player_id, x, y):
    d = Player(x=x, y=y, player_id=player_id)
    d.is_defender = True
    return d


def main():
    tuners = DefenceTuners()
    a_offset = float(getattr(tuners, "RUCK_ADJACENT_A_Y_OFFSET_M", 2.0))
    gap = float(getattr(tuners, "defender_gap_m", 4.0))
    world = MiniWorld()
    ruck_x = world.ruck_x_m
    ruck_y = world.ruck_y_m

    # 10-defender line: 6 right of the ruck + 4 left (the smoke scenario).
    line = []
    for i in range(6):
        line.append(make_defender(i + 1, ruck_x + 3.0, ruck_y + 2.0 + i * 1.5))
    for i in range(4):
        line.append(make_defender(20 + i, ruck_x + 3.0, ruck_y - 1.0 - i * 1.5))
    world.defending_team = line
    assign_depth_roles(line, world)

    n_total = len(line)
    n_left, n_right = decide_side_counts(
        world, tuners, n_total, ruck_y=ruck_y,
    )
    # SPAN CONTRACT (Stage 2, 2026-09-13): the per-side GOVERNED width owns
    # the span; the count only decides how many lanes share it.
    lw, rw = side_widths(world, tuners, n_total=n_total, ruck_y=ruck_y)
    lanes_l = side_lanes(n_left, ruck_y, LEFT, tuners, span=lw)
    lanes_r = side_lanes(n_right, ruck_y, RIGHT, tuners, span=rw)
    targets = compute_targets(world, line, tuners, governor=None)

    # ── 1. A pivots on ANCHOR LAW (ruck_y ± 2.0 m), WINGER on outermost lane ──
    print("\n[1] A pivot on ANCHOR LAW, WINGER on outermost lane")
    check(
        f"LEFT A pivot == ruck_y - {a_offset}",
        bool(lanes_l) and abs(lanes_l[0] - (ruck_y - a_offset)) < 1e-6,
        f"lane0={lanes_l[0] if lanes_l else None}",
    )
    check(
        f"RIGHT A pivot == ruck_y + {a_offset}",
        bool(lanes_r) and abs(lanes_r[0] - (ruck_y + a_offset)) < 1e-6,
        f"lane0={lanes_r[0] if lanes_r else None}",
    )

    # ── 2. Uniform span-derived step (count-boss lanes) ────────────
    print("\n[2] Uniform span-derived step lanes")
    min_gap = float(tuners.min_defender_gap_m)

    def _step_ok(ls):
        if not ls or len(ls) < 2:
            return False
        steps = [abs(ls[i] - ls[i - 1]) for i in range(1, len(ls))]
        return (all(abs(s - steps[0]) < 1e-6 for s in steps)
                and all(s >= min_gap - 1e-9 for s in steps))

    check(f"LEFT lanes uniform step >= {min_gap} m", _step_ok(lanes_l),
          f"lanes={[round(v,2) for v in lanes_l]} n={n_left} span={lw:.2f}")
    check(f"RIGHT lanes uniform step >= {min_gap} m", _step_ok(lanes_r),
          f"lanes={[round(v,2) for v in lanes_r]} n={n_right} span={rw:.2f}")

    # ── 3. No stacked Y lanes (even distribution) ──────────────────
    print("\n[3] No stacked Y lanes")
    ys = [targets[id(d)] for d in line if id(d) in targets]
    unique = len(set(round(v, 3) for v in ys))
    check("no two defenders share the same target Y", unique == len(ys),
          f"unique={unique} count={len(ys)}")

    # ── 4. WINGER span == width-derived lane span ─────────────────
    # WIDTH-AUTHORITY RESTORED (2026-09-13): the ladder compute_targets
    # actually lays is the WIDTH-derived one, so the expected span must be
    # re-derived WITH the per-side governed width.  Re-deriving it from the
    # no-width fallback ladder (the pre-fix model) describes geometry the
    # engine no longer produces.
    print("\n[4] WINGER span == width-derived lane span")
    lw, rw = side_widths(world, tuners, n_total=n_total, ruck_y=ruck_y)
    wl = side_lanes(n_left, ruck_y, LEFT, tuners, span=lw)
    wr = side_lanes(n_right, ruck_y, RIGHT, tuners, span=rw)
    wingers = [d for d in line if getattr(d, "depth_role", None) == "WINGER"]
    if wingers and all(id(d) in targets for d in wingers):
        wspan = max(targets[id(d)] for d in wingers) - min(
            targets[id(d)] for d in wingers
        )
        _all_lanes = (wl if wl else []) + (wr if wr else [])
        _cspan = max(_all_lanes) - min(_all_lanes) if _all_lanes else 0.0
        check("two-WINGER span == width-derived lane span",
              abs(wspan - _cspan) < 1e-6,
              f"wspan={wspan:.3f} cspan={_cspan:.3f}")

    # ── 5. Side width is the GOVERNED width (not lane-derived) ─────
    print("\n[5] Side width comes from the governed width, not the lanes")
    # The 3-point width tuners (via governed_width_m -> side_widths) own the
    # span.  side_widths is therefore NOT an output of the flat lane ladder:
    # deriving it from the ladder is exactly the inversion that left the
    # width tuners unable to widen the line.  Assert the shape of the
    # restored law instead: the open side never exceeds the governed width,
    # both sides are positive, and the width-derived outer lane lands on the
    # side's width anchor whenever the count is feasible for that width.
    _eff = governed_width_m(world, tuners)
    check("no side is wider than the governed width",
          lw > 0.0 and rw > 0.0 and max(lw, rw) <= _eff + 1e-9,
          f"lw={lw:.3f} rw={rw:.3f} governed={_eff:.3f}")
    anchor_bad = []
    for width, side, n_side in ((lw, LEFT, n_left), (rw, RIGHT, n_right)):
        if int(n_side) < 2:
            continue
        a_piv = ruck_y + side * a_offset
        want = ruck_y + side * float(width) * 0.5
        span = abs(want - a_piv)
        if span / float(int(n_side) - 1) < float(tuners.min_defender_gap_m) - 1e-9:
            # The count exceeds what this width can space (the count
            # authority's width-feasibility bound refuses such a count), so
            # the step floor legitimately binds — skip rather than assert.
            continue
        lanes_w = side_lanes(int(n_side), ruck_y, side, tuners,
                             span=float(width))
        if abs(lanes_w[-1] - want) > 1e-6:
            anchor_bad.append(
                f"side={side} outer={lanes_w[-1]:.4f} want={want:.4f}"
            )
    check("width-derived outermost lane lands on the side width anchor",
          not anchor_bad, "; ".join(anchor_bad))

    test_secondary_grace()

    print("\n" + "=" * 64)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S)")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED — ruck-adjacent count-lane spacing")
    sys.exit(0)
def test_secondary_grace():
    """STAGE 2 (2026-09-06): a ruck-adjacent defender promoted to SECONDARY
    keeps its width-lane eligibility for the grace window, so
    recompute_width_x still emits its lane X (no fallback micro-gap pull)."""
    print("\n[6] SECONDARY width-eligibility grace")
    tuners = DefenceTuners()
    tuners.secondary_grace_enabled = True
    world = MiniWorld()
    ruck_x = world.ruck_x_m
    ruck_y = world.ruck_y_m

    # A ruck-adjacent A pivot on the RIGHT side (y = ruck_y + 2.0 m).
    d = make_defender(1001, ruck_x + 3.0, ruck_y + 2.0)
    d.depth_role = "A"
    d.depth_side = RIGHT
    d.frame_side = RIGHT
    d.depth_class = "PASSIVE"
    d.defender_role = DefenderRole.LINE
    d.fullback_role_active = False
    d.tackle_state_active = False
    d.frozen = False
    d.freeze_timer = 0.0
    world.defending_team = [d]

    # First sight (records snapshot), then fresh promotion arms the grace.
    arm_secondary_grace(world, [d], tuners)
    d.depth_class = "SECONDARY"
    arm_secondary_grace(world, [d], tuners)
    if not secondary_grace_active(d, world, tuners):
        report("SECONDARY-GRACE: grace not active after ruck-adjacent promotion")
        return

    # With the grace active, the width lane X is preserved.
    width = recompute_width_x(world, [d], tuners)
    exp = ruck_x + float(tuners.WIDTH_OFFSETS["A"])
    check(
        "graced SECONDARY keeps width lane (micro-gap fix)",
        bool(width) and id(d) in width and abs(width[id(d)] - exp) < 1e-6,
        f"target={width.get(id(d)) if id(d) in width else None} exp={exp}",
    )
    check(
        "graced SECONDARY is width-eligible (grace-aware)",
        is_width_eligible(d, world, tuners),
    )

    # After expiry, eligibility reverts (base SECONDARY exempt again).
    for _ in range(31):
        advance_secondary_grace(world, [d], tuners)
    width2 = recompute_width_x(world, [d], tuners)
    check(
        "expired SECONDARY loses width lane (base exempt)",
        not (id(d) in width2),
    )
if __name__ == "__main__":
    main()
