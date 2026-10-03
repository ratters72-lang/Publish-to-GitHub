"""
verify_ruck_role_system.py — Validation for the ruck-driven role system.

Asserts the COMPLETE ROLE SYSTEM (issue spec 2026-08-08) exactly:

  R1  FULLBACK EXCLUSION — fullback_role_active defenders never receive a
      depth role (spec: FB is never part of the defensive line role system).
  R2  SPLIT — defenders split into left/right sides relative to ruck_y:
          left  = y <  ruck_y
          right = y >= ruck_y
  R3  WIDTH WINGER/CENTRE — on each side, widest-from-ruck_y → WINGER,
      second → CENTRE.  These are RESERVED and assigned FIRST.
  R4  RUCK-X PROXIMITY A/B/C/D — remaining defenders sorted by
      |x - ruck_x| ascending → A, B, C, then all surplus → D.
  R5  PRIORITY — no A/B/C/D can override a WINGER/CENTRE (widest defender
      stays WINGER even when it is also the closest to ruck_x).
  R6  COUNTS — across a symmetric line: exactly 2 WINGER, 2 CENTRE,
      2 A, 2 B, 2 C; D unlimited (0..many).
  R7  GEOMETRY READS ROLES — DefensiveGeometry's width_curve uses the
      actual depth_role (WINGER wider than A/B, D repeated).
"""

import sys
import os

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, DefenderRole
from game.defence.defence_lvl_1.defensive_depth_roles import assign_depth_roles
from game.defence.defence_lvl_1.defensive_geometry import DefensiveGeometry
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

VALID_ROLES = {"A", "B", "C", "D", "CENTRE", "WINGER"}
RUCK_X = 60.0
RUCK_Y = 35.0

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


class MiniWorld:
    """World stub with the ruck geometry the role system consumes."""

    phase = None
    ball_carrier = None
    ball = None
    ruck_x_m = RUCK_X
    ruck_y_m = RUCK_Y
    def_line_width_m = 40.0
    ball_x_m = RUCK_X


WORLD = MiniWorld()


def make_defender(player_id, x, y, defender_role=None):
    d = Player(x=x, y=y, player_id=player_id)
    if defender_role is not None:
        d.defender_role = defender_role
    return d


def roles_of(defenders):
    return [getattr(d, "depth_role", None) for d in defenders]


def main():
    print("=" * 64)
    print("RUCK-DRIVEN ROLE SYSTEM VALIDATION (issue spec 2026-08-08)")
    print("=" * 64)

    # ── R1. FULLBACK EXCLUSION ────────────────────────────────────
    print("\n[R1] Fullback exclusion (fullback_role_active)")
    line = [
        make_defender(1, 62.0, 10.0),   # left, widest
        make_defender(2, 61.0, 20.0),   # left
        make_defender(3, 60.0, 30.0),   # left, closest to ruck_x
        make_defender(4, 60.0, 40.0),   # right, closest
        make_defender(5, 61.0, 50.0),   # right
        make_defender(6, 62.0, 60.0),   # right, widest
        make_defender(7, 63.0, 70.0),   # FULLBACK — excluded
    ]
    line[6].fullback_role_active = True
    line[6].depth_role = "WINGER"  # stale tag must be preserved
    assign_depth_roles(line, WORLD)
    check("fullback depth_role untouched (None/unchanged)", line[6].depth_role == "WINGER")
    eligible = [d for d in line if not d.fullback_role_active]
    check("all eligible line defenders have valid roles",
          all(roles_of(eligible)), str(roles_of(eligible)))
    check("exactly 2 WINGER across eligible line",
          sum(1 for r in roles_of(eligible) if r == "WINGER") == 2,
          str(roles_of(eligible)))

    # ── R2/R3/R4. 3/3 symmetric split → W,C,A per side ───────────
    print("\n[R2-R4] Symmetric 3/3 split (n=6) — each side W,C,A")
    line = [
        make_defender(1, 60.1, 10.0),   # left  widest (25)
        make_defender(2, 60.1, 20.0),   # left  second (15)
        make_defender(3, 60.0, 30.0),   # left  closest to ruck_x → A
        make_defender(4, 60.0, 40.0),   # right closest to ruck_x → A
        make_defender(5, 60.1, 50.0),   # right second (15)
        make_defender(6, 60.1, 60.0),   # right widest (25)
    ]
    assign_depth_roles(line, WORLD)
    roles = roles_of(line)
    check("left side: WINGER, CENTRE, A",
          roles[0] == "WINGER" and roles[1] == "CENTRE" and roles[2] == "A",
          str(roles))
    check("right side: A, CENTRE, WINGER",
          roles[3] == "A" and roles[4] == "CENTRE" and roles[5] == "WINGER",
          str(roles))
    from collections import Counter
    counts = Counter(roles)
    check("exactly 2 WINGER / 2 CENTRE / 2 A (no B/C/D)",
          counts == Counter({"WINGER": 2, "CENTRE": 2, "A": 2}),
          str(dict(counts)))

    # ── R4. Ruck-X proximity drives A/B/C on a 6-defender side ───
    print("\n[R4] X-proximity determines A/B/C/D on a single 6-side")
    # 6 defenders all on the left of ruck_y (y < 35), ruck_x = 60.
    #   y=5  x=60.0  (width 30) -> WINGER  (also closest to ruck_x: proves R5)
    #   y=10 x=60.0  (width 25) -> CENTRE
    #   y=15 x=50.0  (|Δx|=10)  -> B
    #   y=20 x=45.0  (|Δx|=15)  -> C
    #   y=25 x=40.0  (|Δx|=20)  -> D
    #   y=30 x=60.0  (|Δx|=0)   -> A   (closest remaining to ruck_x)
    line = [
        make_defender(1, 60.0, 5.0),
        make_defender(2, 60.0, 10.0),
        make_defender(3, 50.0, 15.0),
        make_defender(4, 45.0, 20.0),
        make_defender(5, 40.0, 25.0),
        make_defender(6, 60.0, 30.0),
    ]
    assign_depth_roles(line, WORLD)
    by_id = {d.player_id: d.depth_role for d in line}
    check("1=WINGER 2=CENTRE 6=A 3=B 4=C 5=D",
          by_id == {1: "WINGER", 2: "CENTRE", 6: "A", 3: "B", 4: "C", 5: "D"},
          str(by_id))

    # ── R5. Width beats X-proximity (reserved roles win) ─────────
    print("\n[R5] WINGER/CENTRE reserved — never overridden by A/B/C/D")
    # Widest defender is ALSO exactly on ruck_x (distance 0).  Width rule
    # must win: it is WINGER, never A.
    line = [
        make_defender(1, 60.0, 5.0),    # width 30, |Δx|=0 → WINGER (R5)
        make_defender(2, 60.0, 15.0),   # width 20 → CENTRE
        make_defender(3, 60.0, 25.0),   # width 10, |Δx|=0 → A
    ]
    assign_depth_roles(line, WORLD)
    roles = roles_of(line)
    check("widest+closest defender is WINGER (not A)",
          roles[0] == "WINGER", str(roles))
    check("second widest is CENTRE (not B)",
          roles[1] == "CENTRE", str(roles))
    check("remaining becomes A (X-proximity)",
          roles[2] == "A", str(roles))

    # ── R4. D overflow — surplus defenders all become D ───────────
    print("\n[R4] D overflow — 8 interior defenders → A,B,C + 5×D")
    # All 10 on the left of ruck_y; y spread well below 35.
    line = []
    for i in range(10):
        line.append(make_defender(i + 1, 60.0 + i * 2.0, 5.0 + i * 1.0))
    assign_depth_roles(line, WORLD)
    roles = roles_of(line)
    counts = Counter(roles)
    check("single WINGER (widest, y=5)", counts["WINGER"] == 1, str(roles))
    check("single CENTRE (second widest, y=6)", counts["CENTRE"] == 1, str(roles))
    check("exactly one A / one B / one C", 
          counts["A"] == 1 and counts["B"] == 1 and counts["C"] == 1,
          str(dict(counts)))
    # D is unlimited: 10 defenders, minus WINGER+CENTRE, minus A/B/C →
    # 5 surplus D (0..many depending on line size).
    check("D unlimited: 5 D for 10-defender single side",
          counts["D"] == 5, str(dict(counts)))
    # X-proximity order among the remaining (non-W/C) interior:
    # A must be the closest remaining to ruck_x.
    a = next(d for d in line if d.depth_role == "A")
    remaining = [d for d in line if d.depth_role not in ("WINGER", "CENTRE")]
    check("A is the closest remaining to ruck_x (x=60+i*2)",
          abs(a.x - RUCK_X) == min(abs(d.x - RUCK_X) for d in remaining),
          f"A x={a.x:.1f}")

    # ── R2. Ruck shift + side hysteresis (2026-08-09, Task A) ──
    # Sides are PERSISTENT and protected by SIDE_SWAP_HYSTERESIS_M (2.0 m):
    # a ruck shift inside the 2 m dead-band keeps sides locked; a shift
    # beyond the band re-splits both defenders onto the new right side.
    print("\n[R2] Ruck_y shift re-splits sides only beyond the 2 m hysteresis")
    line = [
        make_defender(1, 60.0, 34.0),   # just left of ruck 35
        make_defender(2, 60.0, 36.0),   # just right of ruck 35
    ]
    assign_depth_roles(line, WORLD)
    roles_a = roles_of(line)
    check("split at 35: [WINGER] left, [WINGER] right",
          roles_a[0] == "WINGER" and roles_a[1] == "WINGER", str(roles_a))

    # Ruck moves to 33 (−2 m, exactly the dead-band): the LEFT defender at
    # y=34 is only 1 m past the new ruck — inside the 2 m band, so its side
    # is locked.  No re-split.
    class ShiftedWorld33(MiniWorld):
        ruck_y_m = 33.0
    assign_depth_roles(line, ShiftedWorld33())
    roles_deadband = roles_of(line)
    check("ruck_y=33 (inside dead-band): sides stay locked",
          roles_deadband == roles_a, str(roles_deadband))

    # Ruck moves to 31 (−4 m, beyond the band): the LEFT defender at y=34 is
    # now 3 m past the new ruck — outside the 2 m band, so it flips RIGHT.
    # Both defenders are now on the right → WINGER + CENTRE.
    class ShiftedWorld31(MiniWorld):
        ruck_y_m = 31.0
    assign_depth_roles(line, ShiftedWorld31())
    roles_b = roles_of(line)
    check("ruck_y=31 (beyond dead-band): both on right → WINGER + CENTRE",
          roles_b[0] == "CENTRE" and roles_b[1] == "WINGER", str(roles_b))

    # ── R6. Exactly-2 counts on a balanced n=8 line ───────────────
    print("\n[R6] Balanced n=8 → exactly 2 of each constrained role")
    line = [
        make_defender(1, 60.3, 10.0),   # left  W (25)
        make_defender(2, 60.2, 20.0),   # left  C (15)
        make_defender(3, 60.1, 27.0),   # left  A (8)
        make_defender(4, 60.0, 33.0),   # left  B (2)
        make_defender(5, 60.0, 37.0),   # right A (2)
        make_defender(6, 60.1, 43.0),   # right B (8)
        make_defender(7, 60.2, 50.0),   # right C (15)
        make_defender(8, 60.3, 60.0),   # right W (25)
    ]
    assign_depth_roles(line, WORLD)
    counts = Counter(roles_of(line))
    check("2 WINGER / 2 CENTRE / 2 A / 2 B (no C/D)",
          counts == Counter({"WINGER": 2, "CENTRE": 2, "A": 2, "B": 2}),
          str(dict(counts)))

    # ── R7. Geometry reads roles (width curve, D repeats) ─────────
    print("\n[R7] DefensiveGeometry width curve reads roles")
    tuners = DefenceTuners()
    geometry = DefensiveGeometry(tuners)

    # D-overflow line from R4 — D must repeat in the width curve.
    line = []
    for i in range(10):
        line.append(make_defender(i + 1, 60.0 + i * 2.0, 5.0 + i * 1.0))
    lane_y = geometry.compute_line_shape(WORLD, line, line_x=70.0)
    roles = roles_of(line)
    width_curve = geometry._compute_width_curve(line)
    d_indices = [i for i, r in enumerate(roles) if r == "D"]
    check("multiple D defenders → D width factor repeated",
          len(d_indices) >= 2 and len({width_curve[i] for i in d_indices}) == 1,
          f"d_roles={len(d_indices)} factors={[round(width_curve[i], 3) for i in d_indices]}")
    check("all 10 lane_y within def_line_width_m/2 of ruck_y",
          all(abs(y - RUCK_Y) <= 20.0 + 0.5 for y in lane_y),
          f"lanes={[round(v, 2) for v in lane_y]}")

    # WINGER wider than A/B on a symmetric 3/3 split.
    line = [
        make_defender(1, 60.1, 10.0),
        make_defender(2, 60.1, 20.0),
        make_defender(3, 60.0, 30.0),
        make_defender(4, 60.0, 40.0),
        make_defender(5, 60.1, 50.0),
        make_defender(6, 60.1, 60.0),
    ]
    lane_y = geometry.compute_line_shape(WORLD, line, line_x=70.0)
    roles = roles_of(line)
    winger_offsets = [abs(y - RUCK_Y) for r, y in zip(roles, lane_y) if r == "WINGER"]
    ab_offsets = [abs(y - RUCK_Y) for r, y in zip(roles, lane_y) if r in ("A", "B")]
    check("WINGER lanes further from ruck_y than A/B lanes",
          winger_offsets and ab_offsets and max(winger_offsets) > max(ab_offsets),
          f"wingers={[round(v,2) for v in winger_offsets]} a/b={[round(v,2) for v in ab_offsets]}")

    # ── Excluded roles still untouched ────────────────────────────
    print("\n[EXTRA] Excluded roles (markers/tacklers/DH/KICK_CHASE) untouched")
    line = [
        make_defender(1, 60.0, 10.0, DefenderRole.MARKER_A),
        make_defender(2, 60.0, 20.0),              # eligible
        make_defender(3, 60.0, 30.0, DefenderRole.TACKLER_1),
        make_defender(4, 60.0, 40.0, DefenderRole.DUMMY_HALF),
        make_defender(5, 60.0, 50.0, DefenderRole.KICK_CHASE),
    ]
    line[0].depth_role = None
    line[2].depth_role = "D"  # stale tag preserved
    assign_depth_roles(line, WORLD)
    check("MARKER_A untouched (None)", line[0].depth_role is None)
    check("eligible defender assigned a role", line[1].depth_role in VALID_ROLES,
          str(line[1].depth_role))
    check("TACKLER_1 preserved stale depth_role", line[2].depth_role == "D")
    check("DUMMY_HALF untouched", line[3].depth_role is None)
    check("KICK_CHASE untouched", line[4].depth_role is None)

    print("\n" + "=" * 64)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
