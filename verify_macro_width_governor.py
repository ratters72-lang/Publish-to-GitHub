"""
verify_macro_width_governor.py — Verification for the macro defensive line
width & spacing governor (spec 2026-08-17).

Confirms the requirements of the DEFENSIVE LINE WIDTH & SPACING SYSTEM:

    1. WIDTH STABILITY — the line width converges smoothly to the §4
       uniform reference ((n-1) * DEFENDER_SPACING_M) — the Decision-1
       width-error target — with no lateral jitter and no oscillation.
    2. SPACING STABILITY — §3 depth-role lateral offsets are applied
       consistently, SIGNED by depth_side (Decision 2); per-frame gap
       drift is ~0 after convergence.
    3. COMPRESSION-CURVE COMPATIBILITY — the §5 hook is present and is a
       pure no-op while compression_curve_enabled = False (byte-identical
       targets); when enabled, it scales the correction linearly by
       compression_curve_strength.
    4. INTEGRATION PURITY — the governor touches ONLY target_y of
       macro-eligible defenders: target_x (depth), the stabiliser alpha,
       geometry smooth time, compression weights, slowdown band and
       stance radius are byte-identical before/after.  Frozen / fullback /
       committed-tackler / no-role defenders are excluded.
    5. TUNER PUBLISH — the §1 tuners are published to the world every
       OPEN_PLAY frame by DefensiveBehaviourUnified._publish_macro_width_tuners.

Emits verify_macro_width_governor_report.json and exits 0 when all checks
pass.
"""

import io
import json
import math
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_geometry import DefensiveGeometry
from game.defence.defence_lvl_1.defensive_line_governor import DefensiveLineGovernor
from game.defence.defence_lvl_1.defensive_behaviour_unified import (
    DefensiveBehaviourUnified,
)

RUCK_X = 60.0
RUCK_Y = 35.0
ANCHOR_X = 70.0
DT = 1.0 / 60.0
# 30 s at 60 fps — first-order tau = 1/(2*alpha) = 6.25 s, so 30 s is
# ~99% converged (steady-state error < 1% of the initial error).
FRAMES = 1800
CONVERGENCE_TOL = 0.75

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
    ruck_x_m = RUCK_X
    ruck_y_m = RUCK_Y
    def_line_width_m = 40.0
    ball_x_m = RUCK_X
    ball_y_m = RUCK_Y
    defensive_line_anchor = ANCHOR_X
    in_pursuit_mode = False
    first_tackle_not_made = False
    governor_width_active = False
    governor_line_width_m = None
    sim_time_s = 0.0
    dt = DT


class MiniBC:
    x = RUCK_X + 5.0
    y = RUCK_Y
    vx = 0.0
    vy = 0.0


def make_defender(player_id, y, role, side):
    """Create a Player with a fixed depth role/side and a target at *y*."""
    d = Player(x=RUCK_X + 5.0, y=y, player_id=player_id)
    d.depth_role = role
    d.depth_side = side
    d.target_x = ANCHOR_X
    d.target_y = y
    d.frozen = False
    d.freeze_timer = 0.0
    d.fullback_role_active = False
    d._tackle_commit_active = False
    return d


# Base spacing-chain emission for one frame (the pipeline rewrites
# target_y every frame; the macro governor is the LAST writer).
# 6-role line: LEFT WINGER/CENTRE/A, RIGHT A/CENTRE/WINGER.
BASE_Y = [
    (15.0, "WINGER", -1),
    (24.0, "CENTRE", -1),
    (33.0, "A", -1),
    (37.0, "A", 1),
    (46.0, "CENTRE", 1),
    (55.0, "WINGER", 1),
]


def build_line():
    return [
        make_defender(i + 1, base, role, side)
        for i, (base, role, side) in enumerate(BASE_Y)
    ]


def emit_base_targets(defenders):
    for d, (base, _role, _side) in zip(defenders, BASE_Y):
        d.target_y = base
        d.target_x = ANCHOR_X


def reference_width_for(defenders, tuners):
    n = sum(
        1 for d in defenders
        if getattr(d, "depth_role", None) in ("A", "B", "C", "D", "CENTRE", "WINGER")
    )
    return max(0.0, (n - 1) * tuners.DEFENDER_SPACING_M)


def snapshot_purity(defenders, world, tuners):
    """Snapshot every field the governor must never modify."""
    return {
        "target_x": [d.target_x for d in defenders],
        "world_def_line_width": world.def_line_width_m,
        "governor_width_active": world.governor_width_active,
        "governor_line_width": world.governor_line_width_m,
        "stabiliser_base_alpha": tuners.stabiliser_base_alpha,
        "stabiliser_max_alpha": tuners.stabiliser_max_alpha,
        "geometry_smooth_time": tuners.geometry_smooth_time_s,
        "compression_weights": dict(tuners.width_curve_factor),
        "compression_strength": tuners.compression_strength,
        "compression_band": tuners.compression_band_width_m,
        "slowdown_band": tuners.pre_tackle_slowdown_band_m,
        "slowdown_scale": tuners.pre_tackle_slowdown_scale,
        "stance_radius": tuners.pre_tackle_stance_radius_m,
    }


def assert_snapshot_purity(name, before, defenders, world, tuners):
    """Assert the protected fields are byte-identical after a run."""
    after_target_x = [d.target_x for d in defenders]
    check(f"{name}: target_x (depth) untouched", after_target_x == before["target_x"])
    for field, tracer in (
        ("world_def_line_width", lambda: world.def_line_width_m),
        ("governor_width_active", lambda: world.governor_width_active),
        ("governor_line_width", lambda: world.governor_line_width_m),
        ("stabiliser_base_alpha", lambda: tuners.stabiliser_base_alpha),
        ("stabiliser_max_alpha", lambda: tuners.stabiliser_max_alpha),
        ("geometry_smooth_time", lambda: tuners.geometry_smooth_time_s),
        ("compression_strength", lambda: tuners.compression_strength),
        ("compression_band", lambda: tuners.compression_band_width_m),
        ("slowdown_band", lambda: tuners.pre_tackle_slowdown_band_m),
        ("slowdown_scale", lambda: tuners.pre_tackle_slowdown_scale),
        ("stance_radius", lambda: tuners.pre_tackle_stance_radius_m),
    ):
        check(f"{name}: {field} untouched", tracer() == before[field])
    check(
        f"{name}: compression weights untouched",
        dict(tuners.width_curve_factor) == before["compression_weights"],
    )


# ─────────────────────────────────────────────────────────────────────
# 1. WIDTH STABILITY
# ─────────────────────────────────────────────────────────────────────

def run_width_stability():
    print("\n[1] Width stability — smooth convergence, no jitter / oscillation")
    tuners = DefenceTuners()
    governor = DefensiveLineGovernor(tuners, geometry=DefensiveGeometry(tuners))
    world = MiniWorld()
    world.ball_carrier = MiniBC()
    defenders = build_line()

    reference = governor.uniform_spacing_reference_width(defenders)
    check(
        "reference width = (n-1) * DEFENDER_SPACING_M",
        math.isclose(reference, (len(defenders) - 1) * tuners.DEFENDER_SPACING_M),
        f"ref={reference:.2f}",
    )

    widths = []
    # No-micro-jitter is measured POST-governor across consecutive frames.
    # (Pre→post within a frame legitimately includes the §3 role offsets +
    # the stored macro DC offset — that is the intended line shape, not
    # jitter.)  Frame 1 seeds the baseline; the first sampled delta is
    # frame2 - frame1, so the initial raw→governed jump never counts.
    max_frame_delta = 0.0
    prev_post = None
    for _frame in range(FRAMES):
        emit_base_targets(defenders)
        governor.apply_macro_width_governor(world, defenders, DT)
        widths.append(governor.compute_line_width(defenders))
        post = [d.target_y for d in defenders]
        if prev_post is not None:
            for d, before in zip(defenders, prev_post):
                max_frame_delta = max(max_frame_delta, abs(d.target_y - before))
        prev_post = post

    # Convergence to the §4 reference (Decision-1 error target).
    final_width = widths[-1]
    check(
        "line width converges to §4 reference width",
        abs(final_width - reference) <= CONVERGENCE_TOL,
        f"final={final_width:.3f} ref={reference:.3f}",
    )

    # Monotone error decay — first-order feedback cannot oscillate.
    errors = [abs(w - reference) for w in widths]
    monotone = True
    for a, b in zip(errors, errors[1:]):
        if b > a + 1e-6:
            monotone = False
            break
    check("width error decays monotonically (no oscillation)", monotone)

    # No micro-jitter: per-frame target movement is bounded by the macro
    # rate (|error| <= 20 m, alpha=0.08, dt=1/60 -> <= 0.027 m/frame).
    check(
        "per-frame target movement bounded (no jitter)",
        max_frame_delta <= 0.2,
        f"max_frame_delta={max_frame_delta:.4f}",
    )
    return tuners, governor, defenders, world


# ─────────────────────────────────────────────────────────────────────
# 2. SPACING STABILITY
# ─────────────────────────────────────────────────────────────────────

def run_spacing_stability(governor, defenders, world, reference):
    print("\n[2] Spacing stability — §3 signed offsets + uniform, stable gaps")
    tuners = DefenceTuners()

    # §3 offsets must be applied SIGNED by depth_side (−1/+1) with the
    # tuner magnitude (Decision 2).  The governor also applies a symmetric
    # width-rescale that differs slightly between the two runs (the width
    # error differs by the §3 broadening), so the observable per-defender
    # difference is offset[role]*side + (off_A − off_B)*ratio.  The rescale
    # residual is bounded by alpha*dt*max_error: with a ~5 m broadening and
    # |ratio| ≤ 1 that is ≤ 0.007 m — orders of magnitude below the 2.5 m
    # unsigned-offset error the test must catch.  A 0.05 m band proves the
    # signed-magnitude property rigorously at the tuner scale while staying
    # immune to the small rescale term.
    def run_with_offsets(enabled):
        t = DefenceTuners()
        if not enabled:
            t.DEPTH_ROLE_LATERAL_OFFSET_M = {
                k: 0.0 for k in t.DEPTH_ROLE_LATERAL_OFFSET_M
            }
        g = DefensiveLineGovernor(t, geometry=DefensiveGeometry(t))
        w = MiniWorld()
        w.ball_carrier = MiniBC()
        d = build_line()
        emit_base_targets(d)
        g.apply_macro_width_governor(w, d, DT)
        return tuple(dd.target_y for dd in d)

    with_off = run_with_offsets(True)
    without_off = run_with_offsets(False)
    role_table = DefenceTuners().DEPTH_ROLE_LATERAL_OFFSET_M
    diffs = [a - b for a, b in zip(with_off, without_off)]
    diffs_ok = all(
        math.isclose(
            diff,
            role_table[role] * side,
            abs_tol=0.05,
        )
        for diff, (base, role, side) in zip(diffs, BASE_Y)
    )
    check(
        "depth-role offsets applied with signed magnitude (±0.05 m band)",
        diffs_ok,
        f"diffs={[round(d, 4) for d in diffs]}",
    )
    check(
        "offsets push each wing OUTWARD (signed, symmetric line)",
        (diffs[0] < 0 and diffs[-1] > 0),
        f"left_wing_delta={diffs[0]:.3f} right_wing_delta={diffs[-1]:.3f}",
    )

    # Uniform spacing stability: once converged, per-frame gap drift is
    # bounded to the macro rate (<< 0.01 m/frame).  At 1800 frames the
    # first-order offset still decays ~0.0002 m/frame, so the assertion is
    # a drift bound of 0.01 m per adjacent gap across the trailing window —
    # orders of magnitude below any micro-jitter.
    gaps_at_end = []
    for _ in range(10):
        emit_base_targets(defenders)
        governor.apply_macro_width_governor(world, defenders, DT)
        ys = sorted(d.target_y for d in defenders)
        gaps_at_end.append(tuple(b - a for a, b in zip(ys, ys[1:])))
    max_gap_drift = 0.0
    for gi in range(len(gaps_at_end[0])):
        vals = [g[gi] for g in gaps_at_end]
        max_gap_drift = max(max_gap_drift, max(vals) - min(vals))
    check(
        "adjacent gaps stable after convergence (drift <= 0.01 m)",
        max_gap_drift <= 0.01,
        f"max_gap_drift={max_gap_drift:.5f}",
    )

    # Overall width still matches the §4 reference after the §3 offsets.
    width_now = governor.compute_line_width(defenders)
    check(
        "width (incl. §3 offsets) held at reference",
        abs(width_now - reference) <= CONVERGENCE_TOL,
        f"width={width_now:.3f} ref={reference:.3f}",
    )
    return tuners


# ─────────────────────────────────────────────────────────────────────
# 3. COMPRESSION-CURVE COMPATIBILITY
# ─────────────────────────────────────────────────────────────────────

def run_compression_hook():
    print("\n[3] Compression-curve compatibility (§5 hook inert by default)")

    def run_scenario(enabled, strength, frames=40):
        tuners = DefenceTuners()
        tuners.compression_curve_enabled = enabled
        tuners.compression_curve_strength = strength
        gov = DefensiveLineGovernor(tuners, geometry=DefensiveGeometry(tuners))
        world = MiniWorld()
        world.ball_carrier = MiniBC()
        defs = build_line()
        history = []
        for _ in range(frames):
            emit_base_targets(defs)
            gov.apply_macro_width_governor(world, defs, DT)
            history.append(tuple(round(d.target_y, 9) for d in defs))
        return history, gov

    # The §5 hook multiplies the adjustment by compression_curve_strength
    # when enabled.  Therefore enabled=True with strength=1.0 is
    # byte-identical to disabled (the normal governor), proving the hook
    # cannot alter behaviour when compression curves are not actually
    # applied.  strength=0.0 is NOT identical — it correctly zeroes the
    # correction entirely (the intended "strength 0 = no correction"
    # semantics); the inert DEFAULT is enabled=False.
    off_history, _ = run_scenario(False, 0.0)
    on_full_history, _ = run_scenario(True, 1.0)
    check(
        "enabled=True with strength=1.0 is byte-identical to disabled",
        off_history == on_full_history,
    )
    check(
        "inert default: compression_curve_enabled = False",
        DefenceTuners().compression_curve_enabled is False,
    )
    # strength=0.0 must zero the macro RESCALE only — the §3 depth-role
    # offsets still apply (they are the spacing distribution, not the
    # compression-curve correction).  So the targets must equal the
    # §3-broadened chain (base + offset[role]*depth_side) EXACTLY, i.e.
    # zero width-correction movement on top of the role offsets.
    on_zero_history, _ = run_scenario(True, 0.0)
    role_table = DefenceTuners().DEPTH_ROLE_LATERAL_OFFSET_M
    zero_ok = all(
        all(
            math.isclose(y, base + role_table[r] * s, abs_tol=1e-9)
            for y, (base, r, s) in zip(frame, BASE_Y)
        )
        for frame in on_zero_history
    )
    check(
        "enabled=True with strength=0.0 zeroes the macro rescale",
        zero_ok,
    )

    # The integrated correction must scale linearly with strength on a
    # single frame (same world/line state, strength 0.5 vs 1.0).
    _, gov_half = run_scenario(True, 0.5, frames=1)
    _, gov_full = run_scenario(True, 1.0, frames=1)
    check(
        "strength scales the correction (0.5 -> half the full-strength step)",
        math.isclose(
            gov_half.macro_lateral_offset_m,
            0.5 * gov_full.macro_lateral_offset_m,
            abs_tol=1e-9,
        ),
        f"half={gov_half.macro_lateral_offset_m:.6f} "
        f"full={gov_full.macro_lateral_offset_m:.6f}",
    )

    # The width movement on frame 1 must equal the applied symmetric
    # rescale measured from the §3-broadened base spread: the governor
    # measures the width AFTER applying §3 offsets (spread here 45.0 =
    # 40.0 base + 2.5 left + 2.5 right), and the rescale adds +-off to the
    # extremes exactly (the line is symmetric about the mean), so
    #     after = base_spread_with_offsets + 2 * off.
    t_half = DefenceTuners()
    t_half.compression_curve_enabled = True
    t_half.compression_curve_strength = 0.5
    g_h = DefensiveLineGovernor(t_half, geometry=DefensiveGeometry(t_half))
    w_h = MiniWorld()
    w_h.ball_carrier = MiniBC()
    d_h = build_line()
    emit_base_targets(d_h)
    role_table = DefenceTuners().DEPTH_ROLE_LATERAL_OFFSET_M
    ys_off = [b + role_table[r] * s for b, r, s in BASE_Y]
    base_spread_with_offsets = max(ys_off) - min(ys_off)
    g_h.apply_macro_width_governor(w_h, d_h, DT)
    w_after = g_h.compute_line_width(d_h)
    expected = base_spread_with_offsets + 2 * g_h.macro_lateral_offset_m
    check(
        "width correction equals 2 * integrated offset (symmetric rescale)",
        math.isclose(w_after, expected, abs_tol=1e-6),
        f"after={w_after:.4f} expected={expected:.4f} "
        f"base={base_spread_with_offsets:.4f} 2*off={2 * g_h.macro_lateral_offset_m:.4f}",
    )


# ─────────────────────────────────────────────────────────────────────
# 4. INTEGRATION PURITY + EXCLUSIONS
# ─────────────────────────────────────────────────────────────────────

def run_integration_purity():
    print("\n[4] Integration purity + excluded defenders")
    tuners = DefenceTuners()
    governor = DefensiveLineGovernor(tuners, geometry=DefensiveGeometry(tuners))
    world = MiniWorld()
    world.ball_carrier = MiniBC()
    defenders = build_line()
    reference = reference_width_for(defenders, tuners)

    # Excluded actors carved out of the macro pool.
    frozen = make_defender(99, 20.0, "WINGER", -1)
    frozen.frozen = True
    fullback = make_defender(98, 60.0, "D", 1)
    fullback.fullback_role_active = True
    committed = make_defender(97, 50.0, "CENTRE", 1)
    committed._tackle_commit_active = True
    no_role = make_defender(96, 30.0, None, -1)
    exclusions = [frozen, fullback, committed, no_role]

    before = snapshot_purity(defenders + exclusions, world, tuners)
    before_excluded = {d.player_id: d.target_y for d in exclusions}

    for _ in range(FRAMES):
        emit_base_targets(defenders)
        for d in exclusions:
            d.target_y = before_excluded[d.player_id]
        governor.apply_macro_width_governor(world, defenders + exclusions, DT)

    assert_snapshot_purity("purity", before, defenders + exclusions, world, tuners)
    untouched = all(
        d.target_y == before_excluded[d.player_id] for d in exclusions
    )
    check("frozen/fullback/committed/no-role defenders untouched", untouched)
    # The macro pool still converged to the §4 reference despite exclusions.
    check(
        "macro pool converged with exclusions present",
        abs(governor.compute_line_width(defenders) - reference) <= CONVERGENCE_TOL,
        f"width={governor.compute_line_width(defenders):.3f} ref={reference:.3f}",
    )


# ─────────────────────────────────────────────────────────────────────
# 5. TUNER PUBLISH
# ─────────────────────────────────────────────────────────────────────

def run_tuner_publish():
    print("\n[5] Tuner publish every OPEN_PLAY frame (spec §1/§2)")
    tuners = DefenceTuners()
    world = MiniWorld()
    DefensiveBehaviourUnified._publish_macro_width_tuners(world, tuners)
    check("world.line_width_target_m published",
          world.line_width_target_m == tuners.DEF_LINE_TARGET_WIDTH_M)
    check("world.defender_spacing_m published",
          world.defender_spacing_m == tuners.DEFENDER_SPACING_M)
    check("world.depth_role_lateral_offset_m published",
          world.depth_role_lateral_offset_m is tuners.DEPTH_ROLE_LATERAL_OFFSET_M)
    check("world.width_governor_alpha published",
          world.width_governor_alpha == tuners.width_governor_alpha)
    check("world.compression_curve_enabled published",
          world.compression_curve_enabled == tuners.compression_curve_enabled)
    check("world.compression_curve_strength published",
          world.compression_curve_strength == tuners.compression_curve_strength)


def main():
    print("=" * 72)
    print("MACRO WIDTH GOVERNOR VALIDATION (spec 2026-08-17)")
    print("=" * 72)

    tuners0 = DefenceTuners()
    tuners0.assert_radii()

    _t, governor, defenders, world = run_width_stability()
    reference = governor.uniform_spacing_reference_width(defenders)
    run_spacing_stability(governor, defenders, world, reference)
    run_compression_hook()
    run_integration_purity()
    run_tuner_publish()

    report = {
        "macro_width_governor": {
            "width_stability": True,
            "spacing_stability": True,
            "compression_curve_compatible": True,
            "integration_pure": True,
            "tuners_published": True,
            "issues_remaining": failures,
        }
    }
    if failures:
        for key in report["macro_width_governor"]:
            if key != "issues_remaining":
                report["macro_width_governor"][key] = False

    report_path = "verify_macro_width_governor_report.json"
    with io.open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print(f"\nReport written to {report_path}")
    print(json.dumps(report, indent=2))

    print("\n" + "=" * 72)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
