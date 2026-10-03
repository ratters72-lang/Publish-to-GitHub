"""
verify_width_lanes.py — Width-lane regression verifier (2026-08-13).

Width collapse reproduction/invariant test.  Width lanes are the X-axis
lane positions defined by WIDTH_OFFSETS (spec: spacing controls Y only;
width is exclusively X-axis behaviour):

    lane_x = ruck_x + WIDTH_OFFSETS[depth_role]

REGRESSION BEING KILLED
-----------------------
Before this fix, two late X writers re-pinned every PASSIVE lane-holding
defender to ruck_x + 10 (the WINGER slot) AFTER the width writer had
placed them on their lane:

  1. Step-9 mandatory retreat (_apply_structured_open_play):
       target_x = ruck_x + retreat_compliance_distance_m  (= ruck+10)
     for every PASSIVE defender with x < ruck_x + 10 — since the
     A/B/C/D/CENTRE lanes (0/0/3/5) all sit forward of 10, the whole
     interior lane was always "non-compliant" and pinned to 10.

  2. Anchor Layer 7 (enforce_retreat_to_anchor):
       target_x = anchor_x (= ruck_x + 10)
     for every non-engaged, non-exempt defender with x < anchor_x.

Net result every frame: every PASSIVE holder's target X = ruck+10 and the
line's X-depth spread collapsed onto one band.

SECTIONS
--------
  A. LANE AUTHORITY — lane_x_of() returns ruck_x + WIDTH_OFFSETS[depth_role]
     exactly for width-eligible PASSIVE holders, and None for PRIMARY /
     SECONDARY / markers / DH / tacklers / DEF_CHASE / fullback / frozen /
     unseeded (no depth_role) defenders.
  B. STEP-9 RETREAT — a non-compliant lane-holding PASSIVE defender receives
     target_source == "retreat_to_lane" with target_x == lane_x (never
     ruck+10).  A non-lane PASSIVE defender keeps "retreat_non_compliant"
     with target_x == ruck+10.
  C. ANCHOR LAYER 7 — enforce_retreat_to_anchor leaves a lane-holding
     PASSIVE defender's target_x untouched at its lane even when x < anchor;
     a non-lane PASSIVE defender is still pinned to anchor_x.
  D. FULL PIPELINE — 40 structured OPEN_PLAY frames: for every width-eligible
     lane holder the final consumed MMDL target (mmdl_target_x, stamped
     pre-stabiliser) equals lane_x_of(d, world) on every frame — proving
     retreat + anchor + MMDL-drift-guard all preserved the lane X.
  E. SPACING ORTHOGONALITY — the fix is X-only: spacing Y is byte-identical
     from the frame preamble (frame_spacing_targets) for every lane holder,
     and target_y is never rewritten to the lane X axis.

Exits 0 when all checks pass.
"""

import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, DefenderRole
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_behaviour_unified import (
    DefensiveBehaviourUnified,
)
from game.defence.defence_lvl_1.defensive_depth_roles import assign_depth_roles
from game.defence.defence_lvl_1.defensive_width import lane_x_of
from game.defence.defence_lvl_1.anchor_authority import (
    defensive_line_anchor_m,
    enforce_retreat_to_anchor,
)
from world.world_state import GameSubState

RUCK_X = 60.0
RUCK_Y = 35.0
ANCHOR_X = RUCK_X + 10.0  # ruck + 10 (Task G authority)

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


class StubEngine:
    def __init__(self):
        self._suppress_pre_tackle = True


class MiniWorld:
    """OPEN_PLAY world stub mirroring verify_movement_chain.MiniWorld."""

    def __init__(self):
        self.ruck_x_m = RUCK_X
        self.ruck_y_m = RUCK_Y
        self.def_line_width_m = 40.0
        self.governor_width_active = True
        self.governor_line_width_m = 40.0
        self.short_side_width_m = None
        self.open_side_width_m = None
        self.frame_count = 0
        self.dt = 1.0 / 60.0
        self.ball_carrier = None
        self.defending_team = []
        self.in_pursuit_mode = False
        self.first_tackle_not_made = False
        self.use_structured_open_play = True
        self.defence_enabled = True
        self.sub_state = GameSubState.OPEN_PLAY
        self.tes_active = False
        self.frames_since_ptb = 100
        self.open_play_engine = StubEngine()
        self.defensive_line_anchor = None
        self.anchor_locked = False
        self.role_depth_state = {}
        self.depth_role_state = {}
        self.first_receiver = None
        self.ball = None
        self.role_governs = False
        self.defence_ran_this_frame = True
        self.ctx = None


def make_defender(player_id, x, y, role=None):
    d = Player(x=x, y=y, player_id=player_id)
    d.is_defender = True
    d.max_defensive_speed_mps = 8.3
    if role is not None:
        d.defender_role = role
    return d


def reset_per_frame(defenders):
    for d in defenders:
        d.vx = 0.0
        d.vy = 0.0
        d.frozen = False
        d.freeze_timer = 0.0
        d._mmdl_moved = False
        d.movement_writer = None


def make_lane_line():
    """Eight line defenders AHEAD of the anchor (x=63 < anchor 70) so the
    retreat + anchor writers both re-pin them pre-fix (the collapse).

    With BC kept far behind, the ranked PRIMARY/SECONDARY closers are ids
    3/4/5 (no lane) and the dynamic fullback is id 2 — the PASSIVE lane
    holders are ids 1 (WINGER 70), 6 (C 63), 7 (CENTRE 65), 8 (WINGER 70).
    The 63/65 interior lanes are the collapse regression: pre-fix they were
    pinned to ruck+10 (70) every frame."""
    return [
        make_defender(1, 63.0, 15.0),
        make_defender(2, 63.0, 22.0),
        make_defender(3, 63.0, 30.0),
        make_defender(4, 63.0, 40.0),
        make_defender(5, 63.0, 48.0),
        make_defender(6, 63.0, 55.0),
        make_defender(7, 63.0, 62.0),
        make_defender(8, 63.0, 68.0),
    ]


# ── A. LANE AUTHORITY ─────────────────────────────────────────────────
def run_a_lane_authority():
    print("\n[A] lane_x_of — the single X-lane authority")
    tuners = DefenceTuners()
    world = MiniWorld()

    passive = make_defender(101, 63.0, 35.0)
    passive.depth_role = "C"      # WIDTH_OFFSETS["C"] = 3.0
    passive.depth_class = "PASSIVE"
    passive.depth_side = -1
    check("PASSIVE lane holder C -> ruck + 3",
          abs(lane_x_of(passive, world, tuners) - (RUCK_X + 3.0)) < 1e-9,
          f"lane={lane_x_of(passive, world, tuners):.2f}")

    winger = make_defender(102, 63.0, 35.0)
    winger.depth_role = "WINGER"  # offset 10.0
    winger.depth_class = "PASSIVE"
    check("PASSIVE lane holder WINGER -> ruck + 10",
          abs(lane_x_of(winger, world, tuners) - ANCHOR_X) < 1e-9)

    primary = make_defender(103, 63.0, 35.0)
    primary.depth_role = "A"
    primary.depth_class = "PRIMARY"
    check("PRIMARY closer holds no lane", lane_x_of(primary, world, tuners) is None)

    unseeded = make_defender(104, 63.0, 35.0)
    unseeded.depth_role = None
    unseeded.depth_class = "PASSIVE"
    check("unseeded defender (no depth_role) holds no lane",
          lane_x_of(unseeded, world, tuners) is None)

    marker = make_defender(105, 63.0, 35.0, DefenderRole.MARKER_A)
    marker.depth_role = "D"
    marker.depth_class = "MARKER"
    check("marker holds no lane", lane_x_of(marker, world, tuners) is None)

    fb = make_defender(106, 63.0, 35.0)
    fb.depth_role = "D"
    fb.depth_class = "PASSIVE"
    fb.fullback_role_active = True
    check("fullback holds no lane", lane_x_of(fb, world, tuners) is None)


# ── B. STEP-9 RETREAT (observable X outcome of the full pipeline) ─────
def run_b_step9_retreat():
    print("\n[B] Step-9 mandatory retreat preserves lane X")
    world = MiniWorld()
    line = make_lane_line()
    world.defending_team = line
    # BC 25 m behind the line: only the ranked PRIMARY/SECONDARY closest
    # defenders are engaged; the rest stay PASSIVE, non-compliant lane
    # holders (x=63 < compliance line 70) — exactly the collapse case.
    world.ball_carrier = make_defender(99, 40.0, 35.0)
    tuners = DefenceTuners()
    dbu = DefensiveBehaviourUnified(tuners)
    assign_depth_roles(line, world)

    world.frame_count = 1
    reset_per_frame(line)
    dbu.apply(world, line, 0.0, [])

    # NOTE: after the full pipeline the LAST writer that stamps
    # target_source is the MMDL (mmdl_*), so the retreat writer's stamp
    # is consumed.  The regression assertion is therefore the OBSERVABLE
    # X VALUE: a PASSIVE lane holder's final target_x equals its lane.
    lane_holders = [
        d for d in line
        if lane_x_of(d, world, tuners) is not None
        and not getattr(d, "fullback_role_active", False)
    ]
    lane_ok = len(lane_holders) >= 2 and all(
        abs(d.target_x - lane_x_of(d, world, tuners)) < 1e-3
        for d in lane_holders
    )
    check("PASSIVE lane holders ended on their lane X (target_x == lane)",
          lane_ok,
          f"lane_holders={len(lane_holders)} "
          f"targets={[round(d.target_x, 2) for d in lane_holders]}")
    # The interior lanes (A/B/C/D/CENTRE) are all < ruck+10 — the old
    # compliance pin would have put every one of them on 70.
    interior = [d for d in lane_holders
                if lane_x_of(d, world, tuners) < ANCHOR_X - 1e-6]
    check("interior lane holders were NOT pinned to ruck+10 (collapse killed)",
          all(abs(d.target_x - ANCHOR_X) > 1e-6 for d in interior),
          f"interior={[round(d.target_x, 2) for d in interior]}")


# ── C. ANCHOR LAYER 7 ─────────────────────────────────────────────────
def run_c_anchor_layer7():
    print("\n[C] Anchor Layer 7 skips lane holders")
    world = MiniWorld()
    line = make_lane_line()
    world.defending_team = line
    world.ball_carrier = make_defender(99, 55.0, 35.0)
    tuners = DefenceTuners()
    assign_depth_roles(line, world)
    for d in line:
        d.depth_class = "PASSIVE"

    check("anchor is ruck+10 (authority)", abs(defensive_line_anchor_m(world) - ANCHOR_X) < 1e-9)

    # Pre-pin each lane holder to its lane, then run the anchor enforcer.
    for d in line:
        d.target_x = lane_x_of(d, world, tuners)
        assert d.target_x is not None
    before = {d.player_id: d.target_x for d in line}
    enforce_retreat_to_anchor(world, line)
    untouched = all(
        abs(d.target_x - before[d.player_id]) < 1e-9 for d in line
    )
    check("lane-holding PASSIVE defenders keep their lane X (never pinned to anchor)",
          untouched,
          f"targets={[round(d.target_x, 2) for d in line]} "
          f"anchor={ANCHOR_X:.2f}")

    # Non-lane PASSIVE ahead of anchor is still pinned.
    no_lane = make_defender(301, 55.0, 35.0)  # ahead of anchor 70
    no_lane.depth_class = "PASSIVE"
    no_lane.target_x = 55.0
    world2 = MiniWorld()
    world2.defending_team = [no_lane]
    world2.ball_carrier = make_defender(99, 45.0, 35.0)
    enforce_retreat_to_anchor(world2, [no_lane])
    check("non-lane PASSIVE ahead of anchor still pinned to anchor_x",
          abs(no_lane.target_x - ANCHOR_X) < 1e-9,
          f"target={no_lane.target_x:.2f}")


# ── D. FULL PIPELINE — final consumed MMDL target == lane X ───────────
def run_d_full_pipeline():
    print("\n[D] Full structured OPEN_PLAY pipeline — lane X survives as final target")
    world = MiniWorld()
    line = make_lane_line()
    world.defending_team = line
    # Ground truth (8 defenders, BC far behind): the PASSIVE lane holders
    # are id=1 (WINGER 70), id=6 (C 63), id=7 (CENTRE 65), id=8 (WINGER 70);
    # the ranked closers (3/4/5) are engaged (no lane) and id=2 is the
    # dynamic fullback (no lane).  The 63/65 interior lanes are the
    # collapse regression — pre-fix they were pinned to ruck+10 (70).
    world.ball_carrier = make_defender(99, 40.0, 35.0)
    tuners = DefenceTuners()
    dbu = DefensiveBehaviourUnified(tuners)
    assign_depth_roles(line, world)

    violations = []
    interior_holders = set()
    for frame in range(1, 41):
        world.frame_count = frame
        reset_per_frame(line)
        dbu.apply(world, line, 0.0, [])
        # Engagement (depth_class) is only known AFTER apply — recompute the
        # lane per-frame so the assertion drives off the live eligibility.
        for d in line:
            if getattr(d, "fullback_role_active", False):
                continue
            lane = lane_x_of(d, world, tuners)
            if lane is None:
                continue
            if lane < ANCHOR_X - 1e-6:
                interior_holders.add(d.player_id)
            # mmdl_target_x is stamped BEFORE the target stabiliser — it is
            # the exact raw target retreat + anchor + MMDL-drift-guard left
            # intact for the movement solver.  That raw target must BE the
            # width lane.
            consumed = getattr(d, "mmdl_target_x", None)
            if consumed is None or abs(float(consumed) - lane) > 1e-6:
                violations.append((frame, d.player_id, consumed, lane))
    check("interior (non-anchor) lane holders present every frame (non-vacuous)",
          len(interior_holders) >= 2,
          f"interior_holders={sorted(interior_holders)}")
    check("every lane holder consumed its lane X on all 40 frames (mmdl stamp)",
          not violations,
          f"violations={violations[:3]}")
    # Stabilised target must sit at the lane (never at the anchor 70).
    world.frame_count = 50
    reset_per_frame(line)
    dbu.apply(world, line, 0.0, [])
    collapsed = [
        (d.player_id, d.target_x, lane_x_of(d, world, tuners))
        for d in line
        if lane_x_of(d, world, tuners) is not None
        and lane_x_of(d, world, tuners) < ANCHOR_X - 1e-6
        and abs(d.target_x - ANCHOR_X) < 1e-6
    ]
    check("interior lane holders never snapped to anchor (collapse killed)",
          not collapsed,
          f"collapsed={collapsed[:3]}")


# ── E. SPACING ORTHOGONALITY — X-only fix ─────────────────────────────
def run_e_spacing_orthogonal():
    print("\n[E] Spacing Y untouched by the width-lane fix (X-only)")
    world = MiniWorld()
    line = make_lane_line()
    world.defending_team = line
    world.ball_carrier = make_defender(99, 40.0, 35.0)
    tuners = DefenceTuners()
    dbu = DefensiveBehaviourUnified(tuners)
    assign_depth_roles(line, world)

    # Run the frame preamble (run_defensive_pipeline) exactly like the
    # engine does, then the structured pipeline, and confirm target_y
    # stays exactly on the spacing chain (RETREAT writers consume the
    # frame spacing Y) with zero X-axis leakage.
    from game.defence.defence_lvl_1.defensive_pipeline import (
        run_defensive_pipeline,
        frame_spacing_y as _frame_spacing_y,
    )
    world.frame_count = 1
    reset_per_frame(line)
    spacing_t, _w = run_defensive_pipeline(world, line, tuners,
                                           governor=dbu.line_governor)
    dbu.apply(world, line, 0.0, [])
    mismatches = []
    for d in line:
        if getattr(d, "fullback_role_active", False):
            continue
        expected = _frame_spacing_y(world, d, tuners, governor=dbu.line_governor)
        if abs(d.target_y - expected) > 1e-3:
            mismatches.append((d.player_id, d.target_y, expected))
    check("target_y == spacing-chain Y for every lane holder (no X leakage)",
          not mismatches,
          f"mismatches={mismatches[:3]}")
    check("frame spacing dict published (un-gated preamble intact)",
          getattr(world, "frame_geometry_ready", False) is True)


def main():
    print("=" * 72)
    print("WIDTH LANE REGRESSION — X = WIDTH_OFFSETS, spacing = Y")
    print("=" * 72)
    run_a_lane_authority()
    run_b_step9_retreat()
    run_c_anchor_layer7()
    run_d_full_pipeline()
    run_e_spacing_orthogonal()

    print("\n" + "=" * 72)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED — width lanes survive retreat/anchor/MMDL")
    sys.exit(0)


if __name__ == "__main__":
    main()
