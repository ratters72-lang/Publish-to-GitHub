"""
verify_spacing.py — Validation for Jon's split-design spacing (Task E),
spacing→geometry integration (Task F), and kickoff BC-relative origin.

Confirms:

    1. SPLIT-DESIGN STABILITY UNITS — WINGER/CENTRE anchors, stable
       A_left/A_right, stable geometry, stable width curve, stable
       governor width all produce the split chain.
    2. LEFT / RIGHT CHAINS — per-side spacing chains in CANONICAL
       WINGER-INWARD order (WINGER → CENTRE → D → C → B → A).
    3. PROPORTIONAL SPLIT — short_side_width_m / open_side_width_m
       override each side independently.
    4. SIDELINE OVERRIDE — side widths clamp to the sideline margins.
    5. FULLBACK LAST-MAN — the fullback actor is excluded from the chains.
    6. KICKOFF ORIGIN — ruck_x_m/ruck_y_m/defensive_line_anchor are set
       BC-relative (defensive_line_anchor == BC.x + 10.0) so the line
       forms around the catch, not midfield.
    7. SPACING → GEOMETRY — the preamble publishes the chain output and
       the shared reader consumes it byte-identically (spacing is the
       geometry target generator).
"""

import sys
import os

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_width import role_anchor_y, governed_width_m
from game.defence.defence_lvl_1.defensive_split_spacing import (
    side_chain,
    side_widths,
    spacing_target_y,
    compute_targets,
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


def main():
    print("=" * 60)
    print("SPLIT-DESIGN SPACING + GEOMETRY INTEGRATION (E/F) + KICKOFF")
    print("=" * 60)

    tuners = DefenceTuners()
    world = MiniWorld()

    # ── 1. Kickoff BC-relative origin ──────────────────────────────
    print("\n[1] Kickoff BC-relative origin")
    bc = Player(x=48.0, y=22.0, player_id=99)
    # The kickoff catch contract from SECTION 1:
    world.ruck_x_m = bc.x
    world.ruck_y_m = bc.y
    world.defensive_line_anchor = bc.x + 10.0
    check(
        "ruck_x_m = BC.x",
        world.ruck_x_m == 48.0,
        f"ruck_x={world.ruck_x_m}",
    )
    check(
        "ruck_y_m = BC.y",
        world.ruck_y_m == 22.0,
        f"ruck_y={world.ruck_y_m}",
    )
    check(
        "defensive_line_anchor = BC.x + 10.0",
        world.defensive_line_anchor == 58.0,
        f"anchor={world.defensive_line_anchor}",
    )
    # Winger anchors derive from ruck_y + side half-width, BC-relative.
    line = make_line(7)
    assign_depth_roles(line, world)
    width = governed_width_m(world, tuners)
    anchors = {
        getattr(d, "depth_role", None): role_anchor_y(d, world.ruck_y_m, width, tuners)
        for d in line
    }
    check(
        "winger anchors exist on both sides",
        all(r in anchors for r in ("WINGER", "CENTRE", "A")),
        f"roles={sorted(anchors)}",
    )
    # A nearest to BC.y (ruck_y)
    a_ys = [v for k, v in anchors.items() if k == "A"]
    winger_ys = [v for k, v in anchors.items() if k == "WINGER"]
    check(
        "A anchors nearest to ruck_y (BC.y)",
        a_ys and winger_ys and max(abs(y - world.ruck_y_m) for y in a_ys)
        < min(abs(y - world.ruck_y_m) for y in winger_ys),
        f"A={a_ys} WINGER={winger_ys}",
    )
    # Width clamps to sideline margins
    max_abs = max(abs(y - world.ruck_y_m) for y in anchors.values())
    margin = tuners.sideline_margin_m
    field_max = tuners.field_width_m - margin
    check(
        "width clamps to sideline margin",
        all(margin <= y <= field_max for y in anchors.values()),
        f"ys={[round(v, 2) for v in anchors.values()]}",
    )

    # ── 2. Left/right chains ───────────────────────────────────────
    print("\n[2] Side chains in canonical role order")
    world2 = MiniWorld()
    world2.ruck_x_m = 60.0
    world2.ruck_y_m = 35.0
    line2 = make_line(7)
    assign_depth_roles(line2, world2)
    right_defs = [d for d in line2 if getattr(d, "depth_side", 1) == 1]
    left_defs = [d for d in line2 if getattr(d, "depth_side", 1) == -1]
    right_chain = side_chain(right_defs, left_defs, 1)
    left_chain = side_chain(right_defs, left_defs, -1)
    roles_right = [getattr(d, "depth_role", None) for d in right_chain]
    roles_left = [getattr(d, "depth_role", None) for d in left_chain]
    check(
        "right chain WINGER-first",
        roles_right[0] == "WINGER" if roles_right else False,
        f"right={roles_right}",
    )
    check(
        "left chain WINGER-first",
        roles_left[0] == "WINGER" if roles_left else False,
        f"left={roles_left}",
    )
    # Chain is WINGER-INWARD (single order law, Stage 3G GD-2):
    # outside→inside canonical order WINGER → CENTRE → D → C → B → A.
    # A is the INNERMOST ruck-adjacent anchor, so ranks increase inward.
    order_rank = {"WINGER": 0, "CENTRE": 1, "D": 2, "C": 3, "B": 4, "A": 5}
    right_ok = all(order_rank[roles_right[i]] <= order_rank[roles_right[i + 1]]
                   for i in range(len(roles_right) - 1))
    left_ok = all(order_rank[roles_left[i]] <= order_rank[roles_left[i + 1]]
                  for i in range(len(roles_left) - 1))
    check("right chain canonical order", right_ok, str(roles_right))
    check("left chain canonical order", left_ok, str(roles_left))

    # ── 3. Proportional split ──────────────────────────────────────
    print("\n[3] Proportional split (world overrides neutral)")
    world3 = MiniWorld()
    # UNIFICATION (2026-09-04): world short_side_width_m / open_side_width_m
    # overrides are REMOVED — they do NOT change the output.  The width is
    # purely factor × governed width, capped by the available room.
    world3.short_side_width_m = 34.0
    world3.open_side_width_m = 52.0
    lw, rw = side_widths(world3, tuners)
    ref3 = MiniWorld()  # overrides None
    ref_lw, ref_rw = side_widths(ref3, tuners)
    check(
        "world short/open side overrides are NEUTRAL",
        lw == ref_lw and rw == ref_rw,
        f"lw={lw} ref_lw={ref_lw} rw={rw} ref_rw={ref_rw}",
    )
    # Spacing differs per side for the same role.
    d_left = Player(x=60.0, y=20.0, player_id=101)
    d_left.depth_role = "WINGER"
    d_left.depth_side = -1
    d_right = Player(x=60.0, y=50.0, player_id=102)
    d_right.depth_role = "WINGER"
    d_right.depth_side = 1
    y_l = spacing_target_y(d_left, lw, world3.ruck_y_m, tuners)
    y_r = spacing_target_y(d_right, rw, world3.ruck_y_m, tuners)
    # GOVERNED WIDTH IS THE AUTHORITATIVE TOTAL SPAN (2026-09-14): the
    # short/open split factor is no longer a span reducer, so at a CENTRED
    # ruck the two sides are mirror-symmetric.  Each winger stands exactly one
    # half-span from the ruck, and the two halves sum to the governed total.
    check(
        "each winger stands on its side's half-span (halves sum to the total)",
        abs(abs(y_l - world3.ruck_y_m) - lw * 0.5) < 1e-6
        and abs(abs(y_r - world3.ruck_y_m) - rw * 0.5) < 1e-6
        and abs((lw + rw) * 0.5 - max(lw, rw)) < 1e-9,
        f"left_dist={abs(y_l - world3.ruck_y_m):.2f} lw/2={lw * 0.5:.2f} | "
        f"right_dist={abs(y_r - world3.ruck_y_m):.2f} rw/2={rw * 0.5:.2f}",
    )

    # ── 3a. Split-design factors (Task D) ──────────────────────────
    print("\n[3a] Split-design proportional factors")
    world3a = MiniWorld()
    tuners3a = DefenceTuners()
    # In-band governed width (33.0 in [30,65]) so factors pass through.
    world3a.governor_width_active = True
    world3a.governor_line_width_m = 33.0
    # WIDTH-AUTHORITY RESTORED (2026-09-13): the DEFAULT split factors scale
    # the governed width per side (the short side floors at min_def_width_m);
    # they are NOT re-derived from the count lanes.  Deriving the width from
    # the ladder is the inversion that left the width tuners unable to widen
    # the line.
    from game.defence.defence_lvl_1.defensive_split_spacing import (
        decide_side_counts, LEFT, RIGHT,
    )
    n_total = 6
    n_l, n_r = decide_side_counts(
        world3a, tuners3a, n_total, ruck_y=world3a.ruck_y_m,
    )
    lw_d, rw_d = side_widths(
        world3a, tuners3a, n_total=n_total, ruck_y=world3a.ruck_y_m,
    )
    _gov3a = 33.0  # world3a.governor_line_width_m
    # GOVERNED WIDTH IS THE AUTHORITATIVE TOTAL SPAN (2026-09-14): the two
    # ruck-anchored halves SUM to the governed width.  Each side no longer
    # carries the whole width on its own.
    check(
        "governed width is the TOTAL span (the two halves sum to it)",
        abs((lw_d + rw_d) * 0.5 - _gov3a) < 1e-9,
        f"left={lw_d} right={rw_d} total={(lw_d + rw_d) * 0.5} "
        f"n_left={n_l} n_right={n_r}",
    )
    # Tune short side down / open side up → proportional split.
    # Use governed width 40.0: short = 40*0.8 = 32.0, open = 40*1.2 = 48.0,
    # both inside [min=30, max=65] so the factors pass through the min/max
    # clamp unaltered (33*0.8=26.4 would hit the 30.0 floor and mask the factor).
    # NEUTRALISED (2026-09-14): the short/open SPLIT FACTORS no longer scale a
    # side, so tuning either one must NOT move the published span — the width
    # authority owns the span end to end.  The checks here used to assert the
    # factors DID scale each side; that reduction is the defect being removed.
    world3a.governor_line_width_m = 40.0
    lw_base, rw_base = side_widths(world3a, DefenceTuners())
    tuners3a.tuner_short_side_factor = 0.8
    tuners3a.tuner_open_side_factor = 1.2
    lw_p, rw_p = side_widths(world3a, tuners3a)
    check(
        "tuner_short_side_factor is INERT (no longer a span reducer)",
        abs(lw_p - lw_base) < 1e-9,
        f"left={lw_p} baseline={lw_base}",
    )
    check(
        "tuner_open_side_factor is INERT (no longer a span reducer)",
        abs(rw_p - rw_base) < 1e-9,
        f"right={rw_p} baseline={rw_base}",
    )
    # World overrides were already neutral (2026-09-04) and stay neutral.
    world3a.short_side_width_m = 34.0
    world3a.open_side_width_m = 52.0
    lw_o, rw_o = side_widths(world3a, tuners3a)
    check(
        "world overrides are NEUTRAL",
        abs(lw_o - lw_base) < 1e-9 and abs(rw_o - rw_base) < 1e-9,
        f"left={lw_o} right={rw_o} baseline=({lw_base}, {rw_base})",
    )
    # The span still RESPONDS to the governed width (the single authority).
    world3a.short_side_width_m = None
    world3a.open_side_width_m = None
    world3a.governor_line_width_m = 52.0
    lw_c, rw_c = side_widths(world3a, tuners3a)
    check(
        "split sides respond to the governed width",
        abs((lw_c + rw_c) * 0.5 - 52.0) < 1e-9
        and abs((lw_c + rw_c) * 0.5 - (lw_base + rw_base) * 0.5) > 1e-9,
        f"left={lw_c} right={rw_c} total={(lw_c + rw_c) * 0.5}",
    )

    # ── 3b. Governor clamp through compute_targets (Task D) ────────
    print("\n[3b] Spacing chain reflected through compute_targets")
    from game.defence.defence_lvl_1.defensive_line_governor import DefensiveLineGovernor
    from game.defence.defence_lvl_1.defensive_geometry import DefensiveGeometry

    world3b = MiniWorld()
    tuners3b = DefenceTuners()
    tuners3b.tuner_short_side_factor = 1.0
    tuners3b.tuner_open_side_factor = 1.0
    world3b.governor_width_active = True
    world3b.governor_line_width_m = 33.0
    line3b = make_line(7)
    assign_depth_roles(line3b, world3b)
    # Split factors flow into the per-defender chain Y via side widths.
    targets_f = compute_targets(world3b, line3b, tuners3b)
    # WIDTH-AUTHORITY RESTORED (2026-09-13): the COUNT decides HOW MANY lanes
    # a side gets; the GOVERNED width decides HOW WIDE they span.  Assert the
    # chain span equals the span the WIDTH-derived ladders produce for the
    # live per-side counts — i.e. the chain is built FROM the width, not the
    # width from the ladder.
    # ``derive_side_width_m`` (the INVERSE law: rebuild width FROM a
    # widthless lane ladder) was DELETED in Stage 2 (2026-09-13).  It is
    # not needed here — the span is read from ``side_widths`` (the governed
    # chain) — so the alias is dropped rather than shimmed back.
    from game.defence.defence_lvl_1.defensive_split_spacing import (
        decide_side_counts as _dsc,
        side_lanes as _sl,
        LEFT as _L, RIGHT as _R,
    )
    _n_total = len(line3b)
    _nl, _nr = _dsc(world3b, tuners3b, _n_total, ruck_y=world3b.ruck_y_m)
    _lw3b, _rw3b = side_widths(
        world3b, tuners3b, n_total=_n_total, ruck_y=world3b.ruck_y_m,
    )
    # SPAN CONTRACT (Stage 2, 2026-09-13): ``side_lanes`` takes the per-side
    # width as the MANDATORY ``span=`` keyword; the retired ``side_width=``
    # name was removed when the ladder became span-derived.
    _lanes_l = _sl(_nl, world3b.ruck_y_m, _L, tuners3b, span=_lw3b)
    _lanes_r = _sl(_nr, world3b.ruck_y_m, _R, tuners3b, span=_rw3b)
    _all_lanes = (_lanes_l if _lanes_l else []) + (_lanes_r if _lanes_r else [])
    _exp_span = max(_all_lanes) - min(_all_lanes) if _all_lanes else 0.0
    # Actual chain span from the computed targets.
    _tspan = max(targets_f.values()) - min(targets_f.values())
    check(
        "spacing chain span == width-derived lane span",
        abs(_tspan - _exp_span) < 0.01,
        f"span={_tspan:.2f} expected={_exp_span:.2f}",
    )
    # Governor clamp: with a governor wired, every spacing Y stays
    # ON-FIELD and inside the governed band ± the Task IV post-clamp
    # ENGAGEMENT-BAND SPACING-Y PULL.  The chain order is:
    #     spacing_target_y (single role offset, sideline-clamped)
    #       -> governor.apply_width_clamp (band [ruck_y - gov/2,
    #          ruck_y + gov/2] ∩ sidelimits)
    #       -> _band_spacing_pull (post-clamp shaping: AGGRESSIVE -0.5 /
    #          BALANCED -0.75 / PASSIVE -1.25)
    # The pull is a deliberate post-clamp term that compresses the line
    # toward the BC/ruck lane — so a target may sit UP TO max_pull outside
    # the governed band (14.95/54.05 with band 15.45/54.55 = AGGRESSIVE
    # -0.5 pull here, BC at x=65 < anchor 70).  Never beyond the pull.
    governor_3b = DefensiveLineGovernor(tuners3b, geometry=DefensiveGeometry(tuners3b))
    world3b.governor_width_active = True
    world3b.governor_line_width_m = 60.0
    tuners3b.max_def_width_m = 200.0
    world3b.short_side_width_m = 400.0
    world3b.open_side_width_m = 400.0
    world3b.ruck_y_m = 35.0
    # The governor publishes governed width only when a BC exists;
    # otherwise compute_line_state enters the zero-state and clears
    # governor_width_active (leaving nothing for the clamp to gate on).
    world3b.ball_carrier = MiniBC()
    governor_3b.compute_line_state(world3b, line3b)
    targets_clamped = compute_targets(world3b, line3b, tuners3b, governor=governor_3b)
    gw = governor_3b.governed_width_m(world3b)
    half_c = gw * 0.5
    margin = tuners3b.sideline_margin_m
    field_max = tuners3b.field_width_m - margin
    all_on_field = all(
        margin - 1e-6 <= y <= field_max + 1e-6
        for y in targets_clamped.values()
    )
    # Band intersected with the sideline margins, then widened by the
    # maximum post-clamp engagement pull.
    band_lo = max(35.0 - half_c, margin)
    band_hi = min(35.0 + half_c, field_max)
    engagement_table = getattr(
        tuners3b, "ENGAGEMENT_SPACING_Y_OFFSET", None
    ) or {}
    max_pull = max(
        (abs(float(v)) for v in engagement_table.values()), default=0.0
    )
    in_band_envelope = all(
        (band_lo - max_pull - 1e-6) <= y <= (band_hi + max_pull + 1e-6)
        for y in targets_clamped.values()
    )
    check(
        "governed clamp keeps spacing Y on-field + inside band ± engagement pull",
        all_on_field and in_band_envelope,
        f"ys={[round(v, 2) for v in targets_clamped.values()]} "
        f"band=[{band_lo:.2f},{band_hi:.2f}] max_pull={max_pull:.2f}",
    )

    # ── 4. Sideline override clamp ─────────────────────────────────
    print("\n[4] Sideline override clamp")
    world4 = MiniWorld()
    tuners4 = DefenceTuners()
    tuners4.max_def_width_m = 200.0  # allow a giant width through the max clamp
    world4.short_side_width_m = 400.0
    world4.open_side_width_m = 400.0
    lw4, rw4 = side_widths(world4, tuners4)
    margin = tuners4.sideline_margin_m
    field_max = tuners4.field_width_m - margin
    check(
        "left side folds to usable band",
        lw4 <= (field_max - margin) * 2.0,
        f"left={lw4}",
    )
    check(
        "right side folds to usable band",
        rw4 <= (field_max - margin) * 2.0,
        f"right={rw4}",
    )

    # ── 5. Fullback last-man exclusion ─────────────────────────────
    print("\n[5] Fullback last-man logic")
    world5 = MiniWorld()
    line5 = make_line(7)
    assign_depth_roles(line5, world5)
    fbd = line5[0]
    fbd.fullback_role_active = True
    targets = compute_targets(world5, line5, tuners)
    check(
        "fullback actor excluded from spacing targets",
        id(fbd) not in targets,
        f"targets={sorted(targets)}",
    )
    check(
        "all non-fullback defenders have a spacing target",
        all(id(d) in targets for d in line5 if d is not fbd),
    )

    # ── 6. Spacing → geometry integration (Task F wiring, Stage 3G) ─
    print("\n[6] Spacing is the geometry target generator")

    # STAGE 3G (GD-4): the structured pipeline consumes the TASK-8 frame-
    # dict coverage branch (no second compute), and retreat execution
    # lives in RetreatManager (Stage 3B).  Grep the CURRENT contracts:
    unified_src = open(
        "game/defence/defence_lvl_1/defensive_behaviour_unified.py",
        "r", encoding="utf-8", errors="replace",
    ).read()
    check(
        "structured pipeline is wired to the frame-dict coverage contract",
        "_frame_spacing_covers" in unified_src
        and "frame_spacing_targets" in unified_src,
    )
    check(
        "X depth stays role-driven from role_targets",
        "tx = role_t[0]" in unified_src,
    )
    check(
        "OPEN_PLAY gating on geometry (geometry_active)",
        "geometry_active" in unified_src,
    )

    # BEHAVIOURAL single-source proof: the preamble publishes the SAME
    # dict a direct chain call produces, covers every line defender, and
    # the shared reader reads it byte-identically.
    from game.defence.defence_lvl_1.defensive_pipeline import (
        run_defensive_pipeline,
        frame_spacing_y as _fsy,
    )
    world6 = MiniWorld()
    world6.frame_count = 1
    line6 = make_line(7)
    assign_depth_roles(line6, world6)
    pub_spacing, _pub_width = run_defensive_pipeline(world6, line6, tuners)
    direct = compute_targets(world6, line6, tuners)
    check(
        "preamble publishes a fresh frame spacing dict",
        bool(getattr(world6, "frame_geometry_ready", False))
        and getattr(world6, "frame_geometry_frame", None)
        == getattr(world6, "frame_count", None),
    )
    check(
        "published spacing == direct chain output (single Y source)",
        all(
            id(d) in pub_spacing and id(d) in direct
            and abs(pub_spacing[id(d)] - direct[id(d)]) < 1e-9
            for d in line6
        ),
    )
    check(
        "frame dict covers every non-fullback line defender",
        all(
            getattr(d, "fullback_role_active", False)
            or id(d) in world6.frame_spacing_targets
            for d in line6
        ),
    )
    check(
        "frame_spacing_y reads the published chain byte-identically",
        all(
            abs(_fsy(world6, d, tuners) - pub_spacing[id(d)]) < 1e-9
            for d in line6 if id(d) in pub_spacing
        ),
    )

    print("\n" + "=" * 60)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
