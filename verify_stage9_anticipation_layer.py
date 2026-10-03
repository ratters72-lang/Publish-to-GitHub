"""
verify_stage9_anticipation_layer.py — Verify the phase-gated, role-aware
defensive anticipation layer (Stage 9, 2026-09-05).

The layer applies a CONTROLLED LATERAL PRE-SHIFT of the defensive line toward
the predicted ball / threat lane during OPEN_PLAY and TES, band-limited to
nearby defenders, role-scaled, and clamped by slot hierarchy so spacing is
never destabilised and the WINGER never drifts.

Asserts (against ``anticipation_layer.apply_anticipation_pre_shift`` and
``defensive_pipeline.run_defensive_pipeline``):
  1. PHASE-GATED      : OPEN_PLAY + TES (is_drive_phase) apply the shift;
                        RELEASE / PTB / POST_RUCK return the spacing
                        unchanged (and clear the anticipation stamps).
  2. BAND-LIMITED     : a defender whose base spacing Y is beyond
                        anticipation_band_m of the threat lane gets ZERO
                        shift; a nearby defender gets a positive shift.
  3. ROLE-AWARE       : the per-role gain table is monotonically ordered
                        (A > B > C > D > CENTRE > WINGER) and WINGER == 0.15;
                        at equal proximity a higher-gain role shifts further.
  4. SLOT-HIERARCHY   : the shifted Y never crosses the midpoint to an
                        adjacent lane — per-side outer→inner ordering is
                        preserved (no role inversion, no winger drift).
  5. BOUNDED          : |shift| <= anticipation_max_gain_m.
  6. DIRECTION        : shift sign == sign(threat_y - base_y) (toward the
                        threat lane).
  7. PIPELINE         : run_defensive_pipeline publishes the shifted Y into
                        frame_spacing_targets / d.frame_spacing_y during the
                        drive, and does NOT during RELEASE.

Exit 0 = PASS, exit 1 = FAIL.
"""

import sys
import types

from player.player import Player, DefenderRole
from world.world_state import GameSubState
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.anticipation_layer import (
    threat_lane_y,
    apply_anticipation_pre_shift,
)
from game.defence.defence_lvl_1.defensive_pipeline import (
    run_defensive_pipeline,
)

failures = []


# ─────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────

def make_defender(x, y, pid, world, depth_role, depth_side, defender_role=None):
    """Build a Player carrying a depth_role / depth_side for the harness."""
    d = Player(x, y, pid)
    d._world_ref = world
    d.vx = 0.0
    d.vy = 0.0
    d.max_speed_mps = 9.2
    d.move_speed = 9.2
    d.frozen = False
    d.freeze_timer = 0.0
    d.target_x = x
    d.target_y = y
    d.depth_role = depth_role
    d.depth_side = depth_side
    d.frame_side = depth_side
    d.defender_role = defender_role
    d.fullback_role_active = False
    d.tackle_state_active = False
    d.frame_spacing_y = None
    return d


def make_world(sub_state=GameSubState.OPEN_PLAY, ruck_y=35.0, ruck_x=60.0):
    """Build a minimal world namespace carrying the ruck + sub-state."""
    return types.SimpleNamespace(
        ball_carrier=None,
        defending_team=[],
        attacking_team=[],
        sub_state=sub_state,
        frame_count=0,
        ruck_x_m=ruck_x,
        ruck_y_m=ruck_y,
        defensive_line_anchor=ruck_x + 10.0,
        def_line_width_m=40.0,
        governor_width_active=False,
        tes_active=False,
        ruck_established=True,
        dt=1.0 / 60.0,
    )


def make_bc(world, x, y, vx, vy):
    """Build a live ball carrier."""
    bc = Player(x, y, 999)
    bc._world_ref = world
    bc.x = float(x)
    bc.y = float(y)
    bc.vx = float(vx)
    bc.vy = float(vy)
    bc.max_speed_mps = 9.2
    world.ball_carrier = bc
    return bc


def spacing_dict(*pairs):
    """Build a {id(d): y} spacing dict from (defender, y) pairs."""
    return {id(d): float(y) for d, y in pairs}


def approx(a, b, tol=1e-6):
    return abs(float(a) - float(b)) <= tol


def report(msg):
    failures.append(msg)


# ─────────────────────────────────────────────────────────────────────
# TEST 1 — PHASE-GATED
# ─────────────────────────────────────────────────────────────────────

def test_phase_gate():
    tuners = DefenceTuners()
    tuners.anticipation_enabled = True

    # -- OPEN_PLAY (drive): the shift applies --
    world = make_world(GameSubState.OPEN_PLAY)
    bc = make_bc(world, 60.0, 35.0, 2.0, 3.0)   # threat_y = 35 + 3*0.35 = 36.05
    a = make_defender(70.0, 33.0, 1, world, "A", -1, DefenderRole.LINE)
    spacing = spacing_dict((a, 33.0))
    shifted = apply_anticipation_pre_shift(world, [a], tuners, spacing)
    if approx(shifted[id(a)], 33.0):
        report("PHASE-GATED: OPEN_PLAY did not shift an in-band defender")
    if not bool(getattr(a, "anticipation_active", False)):
        report("PHASE-GATED: anticipation_active not stamped in OPEN_PLAY")

    # -- TES (TACKLE_DRIVE) is also a drive phase --
    world2 = make_world(GameSubState.TACKLE_DRIVE)
    bc2 = make_bc(world2, 60.0, 35.0, 0.0, 3.0)
    a2 = make_defender(70.0, 33.0, 2, world2, "A", -1, DefenderRole.LINE)
    margin = float(getattr(tuners, "sideline_margin_m", 3.0))
    field_max = float(getattr(tuners, "field_width_m", 70.0)) - margin
    threat2 = min(field_max, max(margin, 35.0 + 3.0 * 0.35))
    spacing2 = spacing_dict((a2, 33.0))
    shifted2 = apply_anticipation_pre_shift(world2, [a2], tuners, spacing2)
    if approx(shifted2[id(a2)], 33.0):
        report("PHASE-GATED: TACKLE_DRIVE (TES) did not shift an in-band defender")

    # -- RELEASE is NOT a drive phase: no-op --
    world3 = make_world(GameSubState.TACKLE_RELEASE)
    bc3 = make_bc(world3, 60.0, 35.0, 0.0, 3.0)
    a3 = make_defender(70.0, 33.0, 3, world3, "A", -1, DefenderRole.LINE)
    # Seed an existing stamp to prove it gets cleared.
    a3.anticipation_shift_m = 0.9
    a3.anticipation_active = True
    spacing3 = spacing_dict((a3, 33.0))
    shifted3 = apply_anticipation_pre_shift(world3, [a3], tuners, spacing3)
    if not approx(shifted3[id(a3)], 33.0):
        report("PHASE-GATED: RELEASE modified the spacing (must be a no-op)")
    if bool(getattr(a3, "anticipation_active", False)):
        report("PHASE-GATED: RELEASE left anticipation_active stamped")
    if not approx(getattr(a3, "anticipation_shift_m", 0.9), 0.0):
        report("PHASE-GATED: RELEASE did not clear anticipation_shift_m")


# ─────────────────────────────────────────────────────────────────────
# TEST 2 — BAND-LIMITED
# ─────────────────────────────────────────────────────────────────────

def test_band_limit():
    tuners = DefenceTuners()
    tuners.anticipation_enabled = True
    world = make_world(GameSubState.OPEN_PLAY)
    bc = make_bc(world, 60.0, 33.0, 0.0, 0.0)   # threat_y = 33.0
    band = float(tuners.anticipation_band_m)      # 8.0
    # Nearby A (2.0 m off threat) -> shift > 0.
    near = make_defender(70.0, 35.0, 11, world, "A", 1, DefenderRole.LINE)
    # Far A (12.0 m off threat, beyond band) -> shift == 0.
    far = make_defender(70.0, 45.0, 12, world, "A", 1, DefenderRole.LINE)
    spacing = spacing_dict((near, 35.0), (far, 45.0))
    # Both on the RIGHT side (depth_side=+1), ordered by |y-rack_y|:
    # near y=35 (dist 0 from ruck_y 35), far y=45 (dist 10).  Outer->inner
    # = far (45), near (35).  threat_y=33 < both, so both shift toward -Y.
    shifted = apply_anticipation_pre_shift(world, [near, far], tuners, spacing)
    if approx(shifted[id(near)], 35.0) and approx(shifted[id(far)], 45.0):
        report("BAND-LIMIT: no defender shifted (harness invalid)")
    # Far defender (beyond band) must NOT move.
    if not approx(shifted[id(far)], 45.0):
        report(f"BAND-LIMIT: far defender shifted (|45-33|={abs(45.0-33.0):.2f} >= band {band:.2f})")
    # Near defender must move.
    if approx(shifted[id(near)], 35.0):
        report("BAND-LIMIT: near defender did not shift")


# ─────────────────────────────────────────────────────────────────────
# TEST 3 — ROLE-AWARE
# ─────────────────────────────────────────────────────────────────────

def test_role_aware():
    tuners = DefenceTuners()
    tuners.anticipation_enabled = True
    # Gain table ordering + WINGER target value.
    gains = tuners.anticipation_role_gain
    order = ("A", "B", "C", "D", "CENTRE", "WINGER")
    for i in range(len(order) - 1):
        if not (gains[order[i]] > gains[order[i + 1]]):
            report(
                f"ROLE-AWARE: gain table not monotonic "
                f"{order[i]}({gains[order[i]]}) <= {order[i+1]}({gains[order[i+1]]})"
            )
    if not approx(gains["WINGER"], 0.15):
        report(f"ROLE-AWARE: WINGER gain != 0.15 (got {gains['WINGER']})")

    # Equal-proximity comparison: lone A (LEFT, y=33) vs lone WINGER
    # (RIGHT, y=44) are both 5.5 m from threat_y=38.5 and both un-clamped
    # (no neighbour).  A must shift more than WINGER.
    world = make_world(GameSubState.OPEN_PLAY)
    bc = make_bc(world, 60.0, 35.0, 0.0, 10.0)  # threat_y = 35 + 10*0.35 = 38.5
    a = make_defender(70.0, 33.0, 21, world, "A", -1, DefenderRole.LINE)
    w = make_defender(70.0, 44.0, 22, world, "WINGER", 1, DefenderRole.LINE)
    spacing = spacing_dict((a, 33.0), (w, 44.0))
    shifted = apply_anticipation_pre_shift(world, [a, w], tuners, spacing)
    shift_a = abs(shifted[id(a)] - 33.0)
    shift_w = abs(shifted[id(w)] - 44.0)
    if not (shift_a > shift_w > 0.0):
        report(
            f"ROLE-AWARE: shift A={shift_a:.4f} not > shift WINGER={shift_w:.4f}"
        )


# ─────────────────────────────────────────────────────────────────────
# TEST 4 — SLOT-HIERARCHY CLAMP
# ─────────────────────────────────────────────────────────────────────

def test_slot_clamp():
    tuners = DefenceTuners()
    tuners.anticipation_enabled = True
    # Large max_gain so the raw shift would cross the midpoint.
    tuners.anticipation_max_gain_m = 5.0
    world = make_world(GameSubState.OPEN_PLAY)
    bc = make_bc(world, 60.0, 28.0, 0.0, 0.0)   # threat_y = 28.0
    # LEFT side, outer→inner = B(y=31, outer) -> A(y=33, inner).
    b = make_defender(70.0, 31.0, 31, world, "B", -1, DefenderRole.LINE)
    a = make_defender(70.0, 33.0, 32, world, "A", -1, DefenderRole.LINE)
    spacing = spacing_dict((b, 31.0), (a, 33.0))
    shifted = apply_anticipation_pre_shift(world, [b, a], tuners, spacing)
    sb = shifted[id(b)]
    sa = shifted[id(a)]
    # Midpoint between A and B is (33+31)/2 = 32.0.  A (inner) shifting
    # toward -Y (toward threat 28 / toward B) must NOT cross 32.0.
    if sa < 32.0 - 1e-6:
        report(f"SLOT-CLAMP: A crossed the midpoint (sa={sa:.4f} < 32.0)")
    # Ordering preserved: B (outer) must remain more -Y than A (inner).
    if not (sb < sa):
        report(f"SLOT-CLAMP: ordering inverted sb={sb:.4f} sa={sa:.4f}")
    # B (outer) has no outer neighbour, so it may move freely toward -Y.
    if not (sb < 31.0 - 1e-6):
        report(f"SLOT-CLAMP: B did not shift outward (sb={sb:.4f})")


# ─────────────────────────────────────────────────────────────────────
# TEST 5 — BOUNDED  +  TEST 6 — DIRECTION
# ─────────────────────────────────────────────────────────────────────

def test_bounded_and_direction():
    tuners = DefenceTuners()
    tuners.anticipation_enabled = True
    world = make_world(GameSubState.OPEN_PLAY)
    bc = make_bc(world, 60.0, 30.0, 0.0, 6.0)   # threat_y = 30 + 6*0.35 = 32.1
    max_gain = float(tuners.anticipation_max_gain_m)   # 1.5
    a = make_defender(70.0, 33.0, 41, world, "A", 1, DefenderRole.LINE)
    spacing = spacing_dict((a, 33.0))
    shifted = apply_anticipation_pre_shift(world, [a], tuners, spacing)
    sy = shifted[id(a)]
    shift = sy - 33.0
    # BOUNDED
    if abs(shift) > max_gain + 1e-6:
        report(f"BOUNDED: |shift|={abs(shift):.4f} > max_gain={max_gain:.4f}")
    # DIRECTION: threat_y=32.1 < base_y=33 -> shift must be negative (-Y).
    threat = threat_lane_y(world, bc, tuners)
    if not (threat < 33.0):
        report(f"DIRECTION: harness invalid, threat={threat:.4f} >= 33.0")
    if shift > 1e-6:
        report(
            f"DIRECTION: shift={shift:.4f} sign mismatch (threat {threat:.4f} "
            f"< base 33.0 -> expect -Y shift)"
        )


# ─────────────────────────────────────────────────────────────────────
# TEST 7 — PIPELINE INTEGRATION
# ─────────────────────────────────────────────────────────────────────

def _pipeline_team(world):
    """Build a 6-defender line (WINGER/CENTRE/A per side) for the pipeline."""
    line = [
        make_defender(68.0, 15.0, 51, world, "WINGER", -1, DefenderRole.LINE),
        make_defender(69.0, 22.0, 52, world, "CENTRE", -1, DefenderRole.LINE),
        make_defender(70.0, 33.0, 53, world, "A", -1, DefenderRole.LINE),
        make_defender(70.0, 37.0, 54, world, "A", 1, DefenderRole.LINE),
        make_defender(69.0, 48.0, 55, world, "CENTRE", 1, DefenderRole.LINE),
        make_defender(68.0, 55.0, 56, world, "WINGER", 1, DefenderRole.LINE),
    ]
    world.defending_team = line
    return line


def test_pipeline():
    tuners = DefenceTuners()
    tuners.anticipation_enabled = True

    # OPEN_PLAY: the published frame spacing must carry the shift.
    world = make_world(GameSubState.OPEN_PLAY)
    bc = make_bc(world, 60.0, 35.0, 1.0, 4.0)   # threat within band of A slots
    line = _pipeline_team(world)
    # Assign depth roles + sides are already stamped; run the preamble.
    spacing, width = run_defensive_pipeline(world, list(line), tuners)
    # Every chain defender should carry a frame_spacing_y (published).
    for d in line:
        if getattr(d, "frame_spacing_y", None) is None:
            report(f"PIPELINE: id={d.player_id} frame_spacing_y not stamped in OPEN_PLAY")
    # The A defenders (closest to threat lane, lowest |y - ruck_y|) should
    # have shifted (their anticipation_active stamped True).
    near_as = [d for d in line if getattr(d, "depth_role", None) == "A"]
    if not near_as:
        report("PIPELINE: no A defenders in harness")
    for a in near_as:
        if not bool(getattr(a, "anticipation_active", False)):
            report(f"PIPELINE: A id={a.player_id} not anticipation_active in OPEN_PLAY")

    # RELEASE: the published frame spacing must NOT carry the shift.
    world2 = make_world(GameSubState.TACKLE_RELEASE)
    bc2 = make_bc(world2, 60.0, 35.0, 1.0, 4.0)
    line2 = _pipeline_team(world2)
    run_defensive_pipeline(world2, list(line2), tuners)
    for d in line2:
        if bool(getattr(d, "anticipation_active", False)):
            report(
                f"PIPELINE: id={d.player_id} anticipation_active during RELEASE "
                f"(must be phase-gated off)"
            )


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────

def main():
    test_phase_gate()
    test_band_limit()
    test_role_aware()
    test_slot_clamp()
    test_bounded_and_direction()
    test_pipeline()

    if failures:
        print("VERIFY_STAGE9_PHASE_GATED_ANTICIPATION: FAIL")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("VERIFY_STAGE9_PHASE_GATED_ANTICIPATION: PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
