"""Short-side PTB spacing verifier (2026-09-03).

Confirms the fixes for the "short-side spacing blows out at PTB — Marker B
drifts off its lane" defect:

  1. _side_hinge_body NEVER resolves to MARKER_A/B or DEF_RETREAT_10 (the
     hard hinge exclusion).
  2. spacing_y for a short-side chain defender lands on the FIXED structural
     ruck_adjacent_anchor (ruck_y - 2.0 on the LEFT short side) — not on a
     live marker / DR10 hinge.
  3. spacing_y for a MARKER_B / MARKER_A stays pinned to ruck_y (the lane).
  4. enforce_ruck_adjacent_pin re-asserts MARKER_A/B lane targets (belt-and-
     braces lane hold) during a reform-phase frame.

Mirrors the observed signature:
  [RUCK-PIN] f=2031 sub=TACKLE_RELEASE ruck=29.87 |
      R id=202 role=B tgt=31.87 pos=33.14 d=3.27m FOLLOW/OFF
      | R2 id=203 role=D gap=7.34->7.34 OK
"""
import sys
import types

sys.path.insert(0, ".")

from player.player import DefenderRole
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_split_spacing import (
    _side_hinge_body,
    ruck_adjacent_anchor,
    spacing_y,
    LEFT,
    RIGHT,
)
from game.defence.defence_lvl_1.ruck_adjacent_pin import (
    enforce_ruck_adjacent_pin,
)


def mk(i, y, role=None, def_role=None, side=LEFT, dr=None,
       tackle_state=False, is_fb=False):
    d = types.SimpleNamespace()
    d.player_id = i
    d.y = float(y)
    d.x = 60.0
    d.depth_role = role
    d.depth_side = side
    d.fullback_role_active = is_fb
    d.defender_role = def_role
    d.tackle_state_active = tackle_state
    d._depth_seeded = True if role is not None else False
    d._force_innermost_seed = False
    d.target_y = d.y
    d.target_x = d.x
    d.frame_side = side
    if dr is not None:
        d.depth_role = dr
    return d


def make_world(ruck_y, defenders, phase="TACKLE_DRIVE"):
    w = types.SimpleNamespace()
    w.ruck_y_m = ruck_y
    w.ruck_x_m = 60.0
    w.def_line_width_m = 40.0
    w.ruck_established = True
    w.governor_width_active = False
    w.ball_carrier = None
    w.sub_state = "tackle_drive"
    w.just_left_kickoff_timer = 0.0
    w.kickoff_side_sign = None
    w.defending_team = defenders
    w._ope_phase_label = phase
    w._post_retreat_open_play_frames = 99
    return w


failures = []

# ─────────────────────────────────────────────────────────────
# SETUP: LEFT is short (ruck_y=12.0 close to the left touchline).
# A MARKER_B sits ON the ruck line (y=12.0) — the PHYSICALLY NEAREST body,
# so pre-fix the hinge fell to it (or the DR10).  A DEF_RETREAT_10 sits
# deeper (y=8.0, behind the line) so the genuine A-pivot (y=10.0, the
# innermost CHAIN body) is the correct hinge/lane.  The A-pivot is the
# only non-excluded body between it and the ruck, so it is the A-lane
# holder under the FIXED-anchor law.
# ─────────────────────────────────────────────────────────────
RUCK_Y = 12.0
a_pivot = mk(0, 10.0, def_role=None)            # chain A, innermost chain body
a_pivot.depth_role = "A"
marker_b = mk(1, RUCK_Y, def_role=DefenderRole.MARKER_B, side=LEFT)
marker_a = mk(2, RUCK_Y, def_role=DefenderRole.MARKER_A, side=LEFT)
dr10 = mk(3, RUCK_Y - 4.0, def_role=DefenderRole.DEF_RETREAT_10, side=LEFT)

defenders = [a_pivot, marker_b, marker_a, dr10]
world = make_world(RUCK_Y, defenders, phase="TACKLE_DRIVE")
tuners = DefenceTuners()

# ── TEST 1: hinge never resolves to MARKER_B / DEF_RETREAT_10 ──
hinge = _side_hinge_body(world, RUCK_Y, LEFT, defenders)
if hinge is None:
    failures.append("TEST-1: hinge is None (expected the A-pivot)")
else:
    hinge_name = getattr(hinge, "player_id", None)
    hinge_role = getattr(hinge, "defender_role", None)
    hinge_role_name = getattr(hinge_role, "name", hinge_role)
    if hinge is not a_pivot:
        failures.append(
            "TEST-1: expected hinge = A-pivot (pid 0), got pid=%r role=%s"
            % (hinge_name, hinge_role_name)
        )
    if hinge_role_name in (
        "MARKER_A", "MARKER_B", "DEF_RETREAT_10",
        "DUMMY_HALF", "TACKLER_1", "TACKLER_2", "TACKLER_3",
        "DEF_CHASE", "KICK_CHASE",
    ):
        failures.append(
            "TEST-1: hinge resolved to excluded role %s" % hinge_role_name
        )

# ── TEST 2: spacing_y for the short-side A-pivot = ruck_y - 2.0 ──
a_y = spacing_y(world, a_pivot, tuners)
expected_a = float(ruck_adjacent_anchor(RUCK_Y, LEFT))
if abs(a_y - expected_a) > 0.05:
    failures.append(
        "TEST-2: spacing_y(A) = %.3f, expected FIXED anchor %.3f"
        % (a_y, expected_a)
    )

# ── TEST 3: spacing_y for MARKER_B / MARKER_A stays pinned to ruck_y ──
mb_y = spacing_y(world, marker_b, tuners)
ma_y = spacing_y(world, marker_a, tuners)
if abs(mb_y - RUCK_Y) > 0.01:
    failures.append("TEST-3: spacing_y(MARKER_B) = %.3f, expected ruck_y %.3f"
                    % (mb_y, RUCK_Y))
if abs(ma_y - RUCK_Y) > 0.01:
    failures.append("TEST-3: spacing_y(MARKER_A) = %.3f, expected ruck_y %.3f"
                    % (ma_y, RUCK_Y))

# ── TEST 4: ruck-pin re-asserts marker lane targets (belt-and-braces) ──
# Simulate a writer drifting the marker off its lane, then run the pin.
# MARKER LANE — SINGLE SOURCE (2026-09-04): marker_authority.marker_geometry_target
# (ruck_x + MARKER_A/B_DEPTH_M, ruck_y), re-asserted by enforce_ruck_adjacent_pin
# in the reform window so no retreat / spacing / pin writer can drift a marker
# off its ruck lane.  Never BC-relative.
from game.defence.defence_lvl_1.marker_authority import (
    marker_geometry_target as _mg_target,
)
_bc = types.SimpleNamespace(x=61.0, y=10.0)
world.ball_carrier = _bc
marker_b.y = RUCK_Y + 1.7     # drifted (simulates the 3.27m pos in the log)
marker_b.target_y = RUCK_Y + 1.7
marker_b.target_x = 62.5       # off the ruck_x + 2.0 lane
enforce_ruck_adjacent_pin(world)
_exp_tx, _exp_ty = _mg_target(world, marker_b)
if abs(marker_b.target_y - _exp_ty) > 0.01:
    failures.append(
        "TEST-4: ruck-pin did not re-assert MARKER_B target_y "
        "(got %.3f, expected %.3f)" % (marker_b.target_y, _exp_ty)
    )
if abs(marker_b.target_x - _exp_tx) > 0.01:
    failures.append(
        "TEST-4: ruck-pin did not re-assert MARKER_B target_x "
        "(got %.3f, expected %.3f)" % (marker_b.target_x, _exp_tx)
    )

# ─────────────────────────────────────────────────────────────
print("=" * 72)
print("SHORT-SIDE PTB SPACING VERIFIER (2026-09-03)")
print("=" * 72)
print("ruck_y=%.2f short=LEFT; MARKER_B nearest(ruck line), DR10 deep; "
      "A-pivot on A-lane %.2f" % (RUCK_Y, expected_a))
print("TEST-1 hinge pid=%s role=%s"
      % (getattr(hinge, "player_id", None),
         getattr(getattr(hinge, "defender_role", None), "name",
                 getattr(hinge, "defender_role", None))))
print("TEST-2 spacing_y(A)=%.3f (expected %.3f)" % (a_y, expected_a))
print("TEST-3 spacing_y(MARKER_B)=%.3f spacing_y(MARKER_A)=%.3f"
      % (mb_y, ma_y))
print("TEST-4 post-pin MARKER_B target=(%.2f,%.2f) expected=(%.2f,%.2f)"
      % (marker_b.target_x, marker_b.target_y, _exp_tx, _exp_ty))
print("-" * 72)
if failures:
    print("RESULT: FAIL")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("RESULT: ALL CHECKS PASSED")
print("  - hinge never resolves to MARKER_B / DEF_RETREAT_10")
print("  - spacing_y uses the FIXED ruck-adjacent anchor (ruck_y -/+ 2.0)")
print("  - MARKER_A/B pinned to ruck_y")
print("  - ruck-pin re-asserts marker lane targets (belt-and-braces)")
sys.exit(0)
