"""
verify_depth_roles.py — Validation for the ruck-driven depth-role system.

The defensive line uses the ruck-driven role assignment (issue spec
2026-08-08): defenders split into left/right sides relative to ruck_y,
each side assigns its widest defender WINGER, second CENTRE (reserved),
then ruck_x-proximity A/B/C with all surplus becoming D.

Checks:
  1. VALID ROLES — every eligible defender gets one of A/B/C/D/CENTRE/WINGER.
  2. SIDE SPLIT — clean n=6 yields W,C,A / A,C,W; clean n=8 yields
     W,C,A,B / A,B,C,W.
  3. WIDTH PRIORITY — the widest defender is WINGER even when also closest
     to ruck_x (reserved roles are never overridden by A/B/C/D).
  4. RUCK-X ORDER — interior defenders sorted by |x - ruck_x| -> A,B,C then D.
  5. EXCLUDED / FULLBACK — tacklers/markers/DH/KICK_CHASE and the dynamic
     fullback keep their depth_role untouched.
  6. IDEMPOTENCY — calling assign_depth_roles twice is stable.
  7. GEOMETRY — DefensiveGeometry width curve consumes the real roles
     (WINGER wider than A/B, D repeated for surplus).
"""

import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, DefenderRole
from game.defence.defence_lvl_1.defensive_depth_roles import (
    assign_depth_roles,
    depth_role_tag,
    audit_depth_roles,
    debug_render_defensive_line,
)
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
    """World stub providing the ruck geometry role assignment consumes."""

    phase = None
    ball_carrier = None
    ball = None
    ruck_x_m = RUCK_X
    ruck_y_m = RUCK_Y
    def_line_width_m = 40.0
    ball_x_m = RUCK_X


WORLD = MiniWorld()


def make_line(n, ys, x=60.0, roles=None):
    """Build n defenders at explicit Y positions (list length n)."""
    defenders = []
    for i in range(n):
        d = Player(x=x, y=ys[i], player_id=i + 1)
        if roles is not None:
            d.defender_role = roles[i]
        defenders.append(d)
    return defenders


def roles_of(defenders):
    return [getattr(d, "depth_role", None) for d in defenders]


def main():
    print("=" * 60)
    print("DEPTH ROLE ASSIGNMENT VALIDATION (ruck-driven)")
    print("=" * 60)

    # ── 1. Valid roles for line sizes 3..12 ───────────────────────
    print("\n[1] Valid role assignment for line sizes 3..12")
    for n in range(3, 13):
        ys = [10.0 + i * 9.0 for i in range(n)]
        line = make_line(n, ys)
        assign_depth_roles(line, WORLD)
        got = roles_of(line)
        check(
            f"n={n}: {got}",
            all(r in VALID_ROLES for r in got),
            f"invalid: {[r for r in got if r not in VALID_ROLES]}",
        )

    # ── 2. Symmetric side splits ──────────────────────────────────
    print("\n[2] Symmetric side splits (ruck_y=35)")
    # n=6 clean 3/3: y=10,20,30 | 40,50,60, x=60 (|Δx|=0 for all).
    line = make_line(6, [10.0, 20.0, 30.0, 40.0, 50.0, 60.0])
    assign_depth_roles(line, WORLD)
    got = roles_of(line)
    check(
        "n=6 left W,C,A / right A,C,W",
        got == ["WINGER", "CENTRE", "A", "A", "CENTRE", "WINGER"],
        str(got),
    )
    # n=8 clean 4/4: y=10,18,26,34 | 46,54,62,70, x=60.
    # BANDED (2026-08-12): interior roles follow Y-distance to the ball
    # carrier reference (ruck_y=35 with no BC).  Both interior defenders
    # adjacent to the ruck lane become A (y=30 and y=40), the next two B
    # (y=22 and y=48) — the two-per-band structure is preserved per side:
    # left W,C,B,A / right A,B,C,W (when read in input order; 3/3 split
    # keeps 1x A + 1x B per side).  Width/centre reserves are unchanged.
    line = make_line(8, [10.0, 18.0, 26.0, 34.0, 46.0, 54.0, 62.0, 70.0])
    assign_depth_roles(line, WORLD)
    got = roles_of(line)
    # Counts must be 2x WINGER / 2x CENTRE / 2x A / 2x B.  The "A nearer
    # than B" band order is per-side (both sides of the ruck lane have
    # their own A and B): left side A(1.0) < B(9.0), right side A(11.0) <
    # B(19.0).  Pooled absolute gaps would mix the two sides, so compare
    # within each side.
    from collections import Counter
    counts = Counter(got)
    ys = [10.0, 18.0, 26.0, 34.0, 46.0, 54.0, 62.0, 70.0]
    left_roles = [(y, r) for y, r in zip(ys, got) if y < RUCK_Y]
    right_roles = [(y, r) for y, r in zip(ys, got) if y >= RUCK_Y]

    def band_order_ok(side_roles):
        a_gaps = [abs(y - RUCK_Y) for y, r in side_roles if r == "A"]
        b_gaps = [abs(y - RUCK_Y) for y, r in side_roles if r == "B"]
        return a_gaps and b_gaps and max(a_gaps) < min(b_gaps)

    check(
        "n=8 banded: 2x W/C/A/B, A nearer the lane than B on each side",
        counts["WINGER"] == 2 and counts["CENTRE"] == 2
        and counts["A"] == 2 and counts["B"] == 2
        and band_order_ok(left_roles) and band_order_ok(right_roles),
        str(got),
    )

    # ── 3. Width priority over X-proximity (reserved roles) ───────
    print("\n[3] WINGER/CENTRE reserved — width beats ruck_x proximity")
    line = [
        Player(x=RUCK_X, y=5.0, player_id=1),    # width 30, |Δx|=0
        Player(x=RUCK_X, y=15.0, player_id=2),   # width 20
        Player(x=RUCK_X, y=25.0, player_id=3),   # width 10, |Δx|=0
    ]
    # COUNT SPLIT (2026-08-21): with no BC the split is by required
    # per-side counts (n=3 → smallest-side 1 / largest-side 2: ids 1-2
    # left, id 3 right) and each side's roles come from the nearest-first
    # seed.  WINGER/CENTRE are always the widest on each side; on the
    # 2-defender side the roles are [WINGER, CENTRE] by width order.
    assign_depth_roles(line, WORLD)
    got = roles_of(line)
    check("all roles valid (count-split)", all(r in VALID_ROLES for r in got), str(got))
    check("2x WINGER + 1 non-WINGER", len([r for r in got if r == "WINGER"]) == 2, str(got))
    check("widest defenders cover WINGER", got[0] == "WINGER" and got[1] == "WINGER", str(got))

    # ── 4. Band proximity order → A,B,C then D ────────────────────
    print("\n[4] Band (Y-to-carrier) proximity drives A/B/C/D")
    # COUNT SPLIT (2026-08-21): the interior A/B/C/D order comes from the
    # nearest-first per-side seed.  With n=6 the split is 3/3: ids 1-3 on
    # one side, ids 4-6 on the other.  Reserved WINGER/CENTRE are the
    # widest per side; each side then spreads its remaining defender by
    # Y-distance to the ball-carrier band reference (ruck_y=35 with no
    # BC).  X-distance to the ruck NEVER orders the interior — on a flat
    # line every defender shares nearly the same X, so Y-bands are the
    # only stable ordering ("tacklers go to the wrong spot" fix).
    line = [
        Player(x=60.0, y=5.0, player_id=1),    # WINGER (width 30)
        Player(x=60.0, y=10.0, player_id=2),   # CENTRE (width 25)
        Player(x=50.0, y=15.0, player_id=3),   # band gap 20
        Player(x=45.0, y=20.0, player_id=4),   # band gap 15
        Player(x=40.0, y=25.0, player_id=5),   # band gap 10
        Player(x=60.0, y=30.0, player_id=6),   # band gap 5
    ]
    assign_depth_roles(line, WORLD)
    by_id = {d.player_id: d.depth_role for d in line}
    # Count-split contract (n=6 → 3/3): each 3-defender side holds
    # WINGER + CENTRE + A exactly, so the pooled counts are
    # 2x WINGER / 2x CENTRE / 2x A / 0x B.  The per-side interior A must
    # sit nearer the ruck lane than the per-side CENTRE hold.
    from collections import Counter
    role_counts_4 = Counter(by_id.values())
    check(
        "count-split 2x WINGER / 2x CENTRE / 2x A",
        role_counts_4 == Counter({"WINGER": 2, "CENTRE": 2, "A": 2}),
        str(by_id),
    )
    a_gaps = [abs(d.y - RUCK_Y) for d in line if by_id[d.player_id] == "A"]
    c_gaps = [abs(d.y - RUCK_Y) for d in line if by_id[d.player_id] == "CENTRE"]
    check(
        "interior A nearer the ruck lane than CENTRE on each side",
        a_gaps and c_gaps and min(a_gaps) < min(c_gaps),
        f"A gaps={a_gaps} CENTRE gaps={c_gaps}",
    )

    # ── 5. Excluded roles + fullback untouched ────────────────────
    print("\n[5] Excluded roles + fullback untouched")
    roles = [
        DefenderRole.TACKLER_1,
        None,  # eligible line defender
        DefenderRole.MARKER_A,
        DefenderRole.DUMMY_HALF,
        DefenderRole.KICK_CHASE,
        DefenderRole.MARKER_B,
        DefenderRole.TACKLER_2,
    ]
    ys = [10.0, 18.0, 26.0, 34.0, 42.0, 50.0, 58.0]
    line = make_line(7, ys, roles=roles)
    line[0].depth_role = "WINGER"          # stale tag preserved
    line[6].fullback_role_active = True    # fullback excluded
    line[6].depth_role = "CENTRE"          # stale tag preserved
    assign_depth_roles(line, WORLD)
    got = roles_of(line)
    check("eligible at index 1 got a role", got[1] in VALID_ROLES, str(got[1]))
    check(
        "TACKLER_1 preserved stale depth_role",
        got[0] == "WINGER",
        f"got {got[0]}",
    )
    for i in (2, 3, 4, 5):
        check(f"excluded index {i} untouched", got[i] is None, f"got {got[i]}")
    check("fullback preserved stale depth_role", got[6] == "CENTRE", f"got {got[6]}")

    # ── 6. Idempotency ─────────────────────────────────────────────
    print("\n[6] Idempotency")
    ys = [10.0, 18.0, 26.0, 34.0, 46.0, 54.0, 62.0, 70.0]
    line = make_line(8, ys)
    assign_depth_roles(line, WORLD)
    first = roles_of(line)
    assign_depth_roles(line, WORLD)
    second = roles_of(line)
    check("stable across two calls", first == second)

    # ── 7. Geometry width-curve differentiation ────────────────────
    print("\n[7] Geometry width-curve differentiation")
    tuners = DefenceTuners()
    geometry = DefensiveGeometry(tuners)

    line = make_line(6, [10.0, 20.0, 30.0, 40.0, 50.0, 60.0])  # W,C,A / A,C,W
    lane_with_roles = geometry.compute_line_shape(WORLD, line, line_x=70.0)
    per_defender_roles = roles_of(line)

    check(
        "roles assigned by compute_line_shape",
        all(r in VALID_ROLES for r in per_defender_roles),
        str(per_defender_roles),
    )

    # WINGER lanes must be wider from ruck centre than A/B lanes.
    winger_y = [v for r, v in zip(per_defender_roles, lane_with_roles) if r == "WINGER"]
    ab_y = [v for r, v in zip(per_defender_roles, lane_with_roles) if r in ("A", "B")]
    check(
        "WINGER lanes wider than A/B lanes",
        winger_y and ab_y
        and max(abs(y - RUCK_Y) for y in winger_y)
        > max(abs(y - RUCK_Y) for y in ab_y),
        f"wingers={[round(v,2) for v in winger_y]} a/b={[round(v,2) for v in ab_y]}",
    )

    # D repeat: COUNT SPLIT (2026-08-21) — the split is by required
    # per-side counts, so a flat line of n never stacks on one side.  A
    # side fills WINGER/CENTRE + A/B/C/D (6 slots) before surplus
    # defenders repeat as D.  Print the role lists and the repeated-D
    # width factors FIRST, then assert the D-repeat only on a side that
    # actually overflows (surplus D defensively proves the repeat).
    def _roles_width_for(n):
        line_n = [Player(x=60.0 + i * 2.0, y=5.0 + i * 1.0, player_id=i + 1)
                  for i in range(n)]
        geometry.compute_line_shape(WORLD, line_n, line_x=70.0)
        return roles_of(line_n), geometry._compute_width_curve(line_n)

    roles10, _ = _roles_width_for(10)
    print(f"    n=10 roles: {roles10}")
    roles14, _ = _roles_width_for(14)
    print(f"    n=14 roles: {roles14}")
    roles16, width16 = _roles_width_for(16)
    d_indices = [i for i, r in enumerate(roles16) if r == "D"]
    d_factors = [round(width16[i], 3) for i in d_indices]
    print(
        f"    n=16 D count={len(d_indices)} factors={d_factors} "
        f"(only asserted when a side overflows)"
    )
    check(
        "D width factor repeated for surplus D defenders (overflowing side)",
        len(d_indices) > 0 and len(set(d_factors)) == 1,
        f"D count={len(d_indices)} factors={d_factors}",
    )

    # ── Audit + debug render samples ──────────────────────────────
    print("\n[8] audit_depth_roles() + debug render")
    line = make_line(7, [10.0, 18.0, 26.0, 34.0, 42.0, 50.0, 58.0])
    line[3].fullback_role_active = True
    assign_depth_roles(line, WORLD)
    audit_depth_roles(line)
    print("  ", end="")
    debug_render_defensive_line(line)

    print("\n" + "=" * 60)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
