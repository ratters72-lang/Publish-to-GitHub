"""
verify_split_spacing.py — Validation for Jon's split-design defensive
spacing (Task E) and the spacing→geometry integration (Task F).

Confirms:
    1. REQUIRED CONSTANTS — FULLBACK_EXCLUDED, SPACING_CHAIN_LENGTH=5,
       LEFT=-1, RIGHT=+1.
    2. LEFT CHAIN order + offsets — WINGER → CENTRE → A → B → C, each
       Y = ruck_y - single spacing_offset (SINGLE OFFSET enforced).
    3. RIGHT CHAIN order + offsets — WINGER → CENTRE → A → B → C, each
       Y = ruck_y + single spacing_offset (SINGLE OFFSET enforced).
    4. GOVERNED WIDTH — spacing reflects governed width changes (narrow
       vs wide chain spread).
    5. PROPORTIONAL SPLIT — tuner_short_side_factor /
       tuner_open_side_factor applied; world overrides beat factors.
    6. SIDELINE CLAMP — giant side widths fold to the usable band and
       every chain Y stays inside the sideline margins.
    7. GOVERNOR CLAMP — with the governor wired + active, every spacing
       Y is inside the governed width band.
    8. FULLBACK EXCLUDED — the actor occupies no chain slot and is pinned
       to world.fullback_default_x / fullback_default_y.
    9. SPACING DRIVES GEOMETRY — the structured OPEN_PLAY pipeline reads
       spacing_targets for Y (step 7), structured-close Y overrides to
       spacing_targets, and retreat preserves spacing Y.

SINGLE OFFSET CONTRACT (user verdict, 2026-08-10):
    y_raw = ruck_y + side_sign * compute_spacing_offset(role, side_width)
The chain offset is applied EXACTLY ONCE around the ruck anchor — never
doubled by additionally calling role_anchor_y() inside the chain.  This
verifier enforces that for every role and both sides.
"""

import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_width import (
    role_width_offset,
    clamp_to_sideline,
)
from game.defence.defence_lvl_1.defensive_split_spacing import (
    CHAIN_ROLE_ORDER,
    FULLBACK_EXCLUDED,
    LEFT,
    RIGHT,
    SPACING_CHAIN_LENGTH,
    compute_spacing_offset,
    compute_targets,
    side_chain,
    side_widths,
    spacing_target_y,
)
from game.defence.defence_lvl_1.defensive_depth_roles import assign_depth_roles

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
    governor_width_active = False
    governor_line_width_m = None
    sim_time_s = 0.0
    short_side_width_m = None
    open_side_width_m = None
    fullback_default_x = 60.0
    fullback_default_y = 35.0


class MiniBC:
    x = 65.0
    y = 35.0
    vx = 0.0
    vy = 0.0


def make_line(n):
    defs = []
    for i in range(n):
        d = Player(x=60.0 + i * 0.5, y=10.0 + i * 8.0, player_id=i + 1)
        defs.append(d)
    return defs


def role_defender(role, side, player_id):
    """Build a defender with an explicit depth_role + depth_side."""
    d = Player(x=60.0, y=35.0 + side * 10.0, player_id=player_id)
    d.depth_role = role
    d.depth_side = side
    return d


def main():
    print("=" * 64)
    print("SPLIT-DESIGN SPACING VERIFICATION (Task E) + GEOMETRY (Task F)")
    print("=" * 64)

    # ── 1. Required constants ─────────────────────────────────────
    print("\n[1] Required constants")
    check(
        "FULLBACK_EXCLUDED is True",
        FULLBACK_EXCLUDED is True,
        f"FULLBACK_EXCLUDED={FULLBACK_EXCLUDED}",
    )
    check(
        "SPACING_CHAIN_LENGTH == 6 (WINGER-INWARD 6-role chain)",
        SPACING_CHAIN_LENGTH == 6,
        f"SPACING_CHAIN_LENGTH={SPACING_CHAIN_LENGTH}",
    )
    check("LEFT == -1", LEFT == -1, f"LEFT={LEFT}")
    check("RIGHT == +1", RIGHT == +1, f"RIGHT={RIGHT}")
    check(
        "CHAIN_ROLE_ORDER is the 6-role WINGER-INWARD chain",
        CHAIN_ROLE_ORDER == ("WINGER", "CENTRE", "D", "C", "B", "A"),
        str(CHAIN_ROLE_ORDER),
    )

    # ── 2+3. Left/right chains: order AND single-offset Y ─────────
    tuners = DefenceTuners()
    world = MiniWorld()

    left_chain_roles = ("WINGER", "CENTRE", "D", "C", "B", "A")
    right_chain_roles = ("WINGER", "CENTRE", "D", "C", "B", "A")
    left_defs = [role_defender(r, LEFT, 100 + i) for i, r in enumerate(left_chain_roles)]
    right_defs = [role_defender(r, RIGHT, 200 + i) for i, r in enumerate(right_chain_roles)]

    left_chain = side_chain(right_defs, left_defs, LEFT)
    right_chain = side_chain(right_defs, left_defs, RIGHT)

    print("\n[2] LEFT chain order + single-offset Y")
    print(f"      chain roles = {[getattr(d, 'depth_role', None) for d in left_chain]}")
    check(
        "left chain in canonical order",
        all(
            getattr(d, "depth_role", None) == r
            for d, r in zip(left_chain, left_chain_roles)
        ),
    )
    lw, rw = side_widths(world, tuners)
    for d, expected_role in zip(left_chain, left_chain_roles):
        offset = compute_spacing_offset(expected_role, lw, tuners)
        expected = clamp_to_sideline(world.ruck_y_m - offset, tuners)
        ty = spacing_target_y(d, lw, world.ruck_y_m, tuners) if expected_role == getattr(d, "depth_role", None) else None
        check(
            f"left {expected_role} Y == ruck_y - SINGLE spacing_offset",
            abs(ty - expected) < 1e-9,
            f"y={ty:.3f} expected={expected:.3f} offset={offset:.3f}",
        )

    print("\n[3] RIGHT chain order + single-offset Y")
    print(f"      chain roles = {[getattr(d, 'depth_role', None) for d in right_chain]}")
    check(
        "right chain in canonical order",
        all(
            getattr(d, "depth_role", None) == r
            for d, r in zip(right_chain, right_chain_roles)
        ),
    )
    for d, expected_role in zip(right_chain, right_chain_roles):
        offset = compute_spacing_offset(expected_role, rw, tuners)
        expected = clamp_to_sideline(world.ruck_y_m + offset, tuners)
        ty = spacing_target_y(d, rw, world.ruck_y_m, tuners) if expected_role == getattr(d, "depth_role", None) else None
        check(
            f"right {expected_role} Y == ruck_y + SINGLE spacing_offset",
            abs(ty - expected) < 1e-9,
            f"y={ty:.3f} expected={expected:.3f} offset={offset:.3f}",
        )

    # ── 4. COUNT-BOSS: lanes from the count, uniform gap ──────────
    print("\n[4] Count-authority spacing lanes (uniform gap)")
    world4 = MiniWorld()
    world4.governor_width_active = True
    world4.governor_line_width_m = 33.0
    line4 = make_line(7)
    assign_depth_roles(line4, world4)
    targets_wide = compute_targets(world4, line4, tuners)

    from game.defence.defence_lvl_1.defensive_split_spacing import (
        decide_side_counts,
        side_lanes,
        side_widths,
        ruck_adjacent_anchor,
    )
    n_total = len(line4)
    n_left, n_right = decide_side_counts(
        world4, tuners, n_total, ruck_y=world4.ruck_y_m,
    )
    # SPAN CONTRACT (Stage 2, 2026-09-13): the governed SPAN is the input and
    # the count only decides how many lanes share it.  The old assertions here
    # required the flat defender_gap_m step, which is the retired widthless
    # ladder — the engine now spaces the count across the governed width.
    lw4, rw4 = side_widths(world4, tuners, n_total=n_total,
                           ruck_y=world4.ruck_y_m)
    lanes_l = side_lanes(n_left, world4.ruck_y_m, LEFT, tuners, span=lw4)
    lanes_r = side_lanes(n_right, world4.ruck_y_m, RIGHT, tuners, span=rw4)
    min_gap = float(tuners.min_defender_gap_m)

    def _step_ok(ls):
        if not ls or len(ls) < 2:
            return False
        steps = [abs(ls[i] - ls[i - 1]) for i in range(1, len(ls))]
        return (all(abs(s - steps[0]) < 1e-6 for s in steps)
                and all(s >= min_gap - 1e-9 for s in steps))

    check("LEFT lanes uniform span-derived step (count-boss)",
          _step_ok(lanes_l),
          f"lanes={[round(v,2) for v in lanes_l]} n={n_left} span={lw4:.2f}")
    check("RIGHT lanes uniform span-derived step (count-boss)",
          _step_ok(lanes_r),
          f"lanes={[round(v,2) for v in lanes_r]} n={n_right} span={rw4:.2f}")
    check("LEFT A pivot == ruck_y-2.0", bool(lanes_l)
          and abs(lanes_l[0] - ruck_adjacent_anchor(world4.ruck_y_m, LEFT)) < 1e-6)
    check("RIGHT A pivot == ruck_y+2.0", bool(lanes_r)
          and abs(lanes_r[0] - ruck_adjacent_anchor(world4.ruck_y_m, RIGHT)) < 1e-6)


    # ── 5. Proportional split ─────────────────────────────────────
    print("\n[5] Proportional split (factors + overrides)")
    world5 = MiniWorld()
    world5.governor_width_active = True
    world5.governor_line_width_m = 40.0
    tuners5 = DefenceTuners()
    tuners5.tuner_short_side_factor = 0.8
    tuners5.tuner_open_side_factor = 1.2
    lw5, rw5 = side_widths(world5, tuners5)
    check(
        "short side honours tuner_short_side_factor",
        abs(lw5 - 40.0 * 0.8) < 1e-9,
        f"left={lw5}",
    )
    check(
        "open side honours tuner_open_side_factor",
        abs(rw5 - 40.0 * 1.2) < 1e-9,
        f"right={rw5}",
    )
    world5.short_side_width_m = 34.0
    world5.open_side_width_m = 52.0
    lw_o, rw_o = side_widths(world5, tuners5)
    check(
        "world overrides are NEUTRAL (2026-09-04)",
        abs(lw_o - 40.0 * 0.8) < 1e-9 and abs(rw_o - 40.0 * 1.2) < 1e-9,
        f"left={lw_o} right={rw_o}",
    )
    # Hard cap: a side may never exceed the governed width band max.
    world5.short_side_width_m = 400.0
    world5.open_side_width_m = 400.0
    max_w = tuners5.max_def_width_m
    lw_c, rw_c = side_widths(world5, tuners5)
    check(
        "hard cap clamps side widths",
        lw_c <= max_w and rw_c <= max_w,
        f"left={lw_c} right={rw_c} max={max_w}",
    )
    # Proportional split flows into per-defender chain Y.
    world5.short_side_width_m = None
    world5.open_side_width_m = None
    d_l = role_defender("WINGER", LEFT, 301)
    d_r = role_defender("WINGER", RIGHT, 302)
    y_l = spacing_target_y(d_l, 32.0, world5.ruck_y_m, tuners5)
    y_r = spacing_target_y(d_r, 48.0, world5.ruck_y_m, tuners5)
    check(
        "open-side winger wider than short-side winger",
        abs(y_r - world5.ruck_y_m) > abs(y_l - world5.ruck_y_m),
        f"left_dist={abs(y_l - world5.ruck_y_m):.2f} "
        f"right_dist={abs(y_r - world5.ruck_y_m):.2f}",
    )

    # ── 6. Sideline clamp ─────────────────────────────────────────
    print("\n[6] Sideline clamp")
    world6 = MiniWorld()
    tuners6 = DefenceTuners()
    tuners6.max_def_width_m = 200.0
    world6.short_side_width_m = 400.0
    world6.open_side_width_m = 400.0
    lw6, rw6 = side_widths(world6, tuners6)
    margin = tuners6.sideline_margin_m
    field_max = tuners6.field_width_m - margin
    check("left side folds to usable band", lw6 <= (field_max - margin) * 2.0, f"left={lw6}")
    check("right side folds to usable band", rw6 <= (field_max - margin) * 2.0, f"right={rw6}")
    # Every chain Y stays inside sideline margins.
    world6.short_side_width_m = None
    world6.open_side_width_m = None
    world6.governor_line_width_m = 60.0
    line6 = make_line(7)
    assign_depth_roles(line6, world6)
    targets6 = compute_targets(world6, line6, tuners6)
    in_margins = all(margin <= y <= field_max for y in targets6.values())
    check(
        "every spacing Y inside sideline margins",
        in_margins,
        f"ys={[round(v, 2) for v in targets6.values()]}",
    )

    # ── 7. Governor clamp ─────────────────────────────────────────
    print("\n[7] Governor clamp")
    from game.defence.defence_lvl_1.defensive_line_governor import DefensiveLineGovernor
    from game.defence.defence_lvl_1.defensive_geometry import DefensiveGeometry

    world7 = MiniWorld()
    tuners7 = DefenceTuners()
    tuners7.max_def_width_m = 200.0
    world7.governor_width_active = True
    world7.governor_line_width_m = 60.0
    world7.short_side_width_m = 400.0
    world7.open_side_width_m = 400.0
    world7.ruck_y_m = 35.0
    world7.ball_carrier = MiniBC()
    governor7 = DefensiveLineGovernor(tuners7, geometry=DefensiveGeometry(tuners7))
    line7 = make_line(7)
    assign_depth_roles(line7, world7)
    governor7.compute_line_state(world7, line7)
    targets7 = compute_targets(world7, line7, tuners7, governor=governor7)
    gw7 = governor7.governed_width_m(world7)
    half7 = gw7 * 0.5
    # The governor clamp pins each raw spacing Y into the governed band,
    # THEN the Task IV engagement-band spacing-Y pull (a fixed tuner
    # offset up to 1.25 m toward the ruck lane) runs after the clamp — the
    # documented design: the chain stays the pure single-offset authority
    # and the pull is an additive band posture on top.  So every final Y
    # must lie inside the governed band EXPANDED by the maximum pull.
    max_pull = max(
        abs(float(v)) for v in
        (tuners7.ENGAGEMENT_SPACING_Y_OFFSET or {}).values()
    ) + 1e-6
    in_band = all(
        (35.0 - half7 - max_pull) <= y <= (35.0 + half7 + max_pull)
        for y in targets7.values()
    )
    check(
        "governed clamp keeps spacing Y inside band ± band-pull envelope",
        in_band,
        f"ys={[round(v, 2) for v in targets7.values()]} gw={gw7:.2f}",
    )
    # Without the governor wired, the same extreme overrides fold at the
    # sideline clamp instead (unchanged behaviour).  The band pull remains
    # active (it is independent of the governor gate), so margin values
    # may legally sit 1.25 m below the 3.0 m margin line.
    targets_unclamped = compute_targets(world7, line7, tuners7)
    check(
        "governor clamp is gated on governor activation",
        targets_unclamped == targets7 or all(
            margin - max_pull <= y <= tuners7.field_width_m - margin + max_pull
            for y in targets_unclamped.values()
        ),
        f"ys={[round(v, 2) for v in targets_unclamped.values()]}",
    )

    # ── 8. Fullback excluded + pinned to defaults ─────────────────
    print("\n[8] Fullback exclusion")
    world8 = MiniWorld()
    world8.fullback_default_x = 72.0
    world8.fullback_default_y = 41.0
    line8 = make_line(7)
    assign_depth_roles(line8, world8)
    fbd = line8[0]
    fbd.fullback_role_active = True
    targets8 = compute_targets(world8, line8, tuners)
    check(
        "fullback actor excluded from chain slots",
        id(fbd) not in targets8
        or targets8[id(fbd)] == world8.fullback_default_y,
        f"fb_target={targets8.get(id(fbd))}",
    )
    check(
        "fullback pinned to world.fullback_default_y when present",
        targets8.get(id(fbd)) == 41.0,
        f"fb_y={targets8.get(id(fbd))}",
    )
    check(
        "all non-fullback defenders have a spacing target",
        all(id(d) in targets8 for d in line8 if d is not fbd),
    )

    # ── 9. Spacing drives geometry ────────────────────────────────
    print("\n[9] Spacing is the geometry target generator (movement reads spacing)")
    unified_src = open(
        "game/defence/defence_lvl_1/defensive_behaviour_unified.py",
        "r",
        encoding="utf-8",
        errors="replace",
    ).read()
    check(
        "pipeline calls compute_targets from split spacing",
        "defensive_split_spacing import compute_targets" in unified_src
        and "split_targets = _split_targets(world, sorted_defs, self.tuners" in unified_src,
    )
    check(
        "step-7 movement Y reads spacing_targets",
        "ty = split_targets.get(id(d))" in unified_src,
    )
    check(
        "step-7 X depth stays role-driven",
        "tx = role_t[0]" in unified_src,
    )
    check(
        "structured-close Y = spacing_targets (Task E override)",
        "SPACING OVERRIDE (Task E)" in unified_src
        and "spacing_ty = split_targets.get(id(d))" in unified_src,
    )
    # 2026-08-19: retreat now reads the frame-authoritative spacing
    # targets directly via _frame_spacing_y() — no independent
    # compute_targets call for retreat.  The contract is unchanged:
    # retreat never carries a non-spacing Y.
    check(
        "unified retreat Y reads frame-authoritative spacing",
        "_frame_spacing_y(world, d, self.tuners," in unified_src
        and "retreat_non_compliant" in unified_src,
    )
    # RUCK-ADJACENT EVEN SPACING (2026-08-18): spacing_target_y no longer
    # writes `ruck_y + side * offset` literally — it delegates to the
    # even side-lane helper (_even_side_lane_y / _ruck_a_offset_m) where
    # A anchors at ruck_y ± RUCK_ADJACENT_A_Y_OFFSET_M (2 m), WINGER at
    # ruck_y ± half-width, and interior defenders interpolate evenly
    # between the two anchors.  The single-writer contract is unchanged:
    # every Y still derives from ruck_y and this module's helpers only.
    spacing_src = open(
        "game/defence/defence_lvl_1/defensive_split_spacing.py",
        "r",
        encoding="utf-8",
        errors="replace",
    ).read()
    check(
        "spacing_target_y delegates to the even side-lane helper",
        "_even_side_lane_y(" in spacing_src
        and "ruck_adjacent_anchor(" in spacing_src,
        "even side-lane helpers present",
    )
    check(
        "every spacing Y stays ruck_adjacent (A ± 2m / WINGER ± half-width)",
        "RUCK_ADJACENT_ANCHOR_M" in spacing_src,
        "ruck-adjacent anchor LAW referenced",
    )

    print("\n" + "=" * 64)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
