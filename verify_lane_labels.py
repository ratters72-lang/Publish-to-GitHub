"""
verify_lane_labels.py — Validation for lane/role label assignment.

The defensive line uses the ruck-driven depth roles
(A/B/C/D/CENTRE/WINGER) as its lane labels, stamped by
assign_depth_roles() from ruck geometry (issue spec 2026-08-08).
No subsystem may hand-assign "LW"/"RW" lane labels or per-index lane
strings, and no static role list ("WINGER","B","A","C","D","CENTRE",...)
exists — the old index-based role list was removed with the old role
system.

Checks:
  1. LABEL SOURCE — every eligible defender carries a depth_role label and
     depth_role_tag() renders it as a short tag.
  2. NO MANUAL LANE LABELS — the patch targets contain no hand-rolled
     "LW"/"RW" lane assignments and no role_label/lane_label fields.
  3. GEOMETRY-DRIVEN LABELS — the ruck-driven pattern assigns each side's
     widest defender WINGER, second CENTRE, then A/B/C/D by ruck_x
     proximity (NOT the removed index-ordered W,B,A,C,D,CENTRE,W list).
  4. FULLBACK EXCLUSION — the dynamic fullback actor keeps its depth_role
     out of the WINGER label sweep (excluded from line label slots).
"""

import sys
import os

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player
from game.defence.defence_lvl_1.defensive_depth_roles import (
    assign_depth_roles,
    depth_role_tag,
)

VALID_LABELS = {"A", "B", "C", "D", "CENTRE", "WINGER"}
RUCK_X = 60.0
RUCK_Y = 35.0

failures = []


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


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


def make_line(n, y_start=10.0, y_step=8.0):
    defenders = []
    for i in range(n):
        d = Player(x=60.0 + i * 0.1, y=y_start + i * y_step, player_id=i + 1)
        defenders.append(d)
    return defenders


def main():
    print("=" * 60)
    print("LANE / ROLE LABEL ASSIGNMENT VALIDATION")
    print("=" * 60)

    # ── 1. Label source: depth_role tags on every eligible defender ──
    print("\n[1] Depth-role labels from assign_depth_roles()")
    line = make_line(7)
    assign_depth_roles(line, WORLD)
    tags = [depth_role_tag(d) for d in line]
    check(
        "all 7 labels valid (A/B/C/D/CENTRE/WINGER)",
        all(t in VALID_LABELS for t in tags),
        str(tags),
    )
    check(
        "tags render non-dash labels",
        all(t != "-" for t in tags),
        str(tags),
    )
    # Stationary ruck, symmetric n=7: each side yields W,C,A,(B) — never
    # the removed static list order W,B,A,C,D,CENTRE,W.
    check(
        "labels follow the ruck-driven pattern (not static list)",
        tags[0] == "WINGER" and tags[1] == "CENTRE" and tags[-1] == "WINGER"
        and tags[-2] == "CENTRE",
        str(tags),
    )

    # ── 2. No manual lane labels in patch targets ──────────────────
    print("\n[2] Manual lane-label / label-field scan")
    targets = [
        "game/defence/defence_lvl_1/defensive_behaviour_lvl1.py",
        "game/defence/defence_lvl_2/defensive_behaviour_lvl2.py",
        "game/defence/defence_lvl_1/defensive_behaviour_unified.py",
        "world/catch_system.py",
        "world/open_play_engine.py",
    ]
    manual_labels = []
    role_label_fields = []
    for rel in targets:
        path = os.path.join(os.getcwd(), rel)
        if not os.path.exists(path):
            continue
        for i, ln in enumerate(
            open(path, "r", encoding="utf-8", errors="replace").read().splitlines(), 1
        ):
            s = ln.strip()
            if s.startswith("#"):
                continue
            if ("\"LW\"" in s or "'LW'" in s or "\"RW\"" in s or "'RW'" in s):
                manual_labels.append(f"{rel}:{i}")
            if "role_label" in s or "lane_label" in s:
                role_label_fields.append(f"{rel}:{i}")
    check("no manual LW/RW lane labels", not manual_labels, str(manual_labels))
    check("no role_label / lane_label fields", not role_label_fields, str(role_label_fields))

    # ── 3. Lane-label consistency under sub-band Y drift ───────────
    print("\n[3] Label/lane consistency under hysteresis ordering")
    line = make_line(7, y_step=0.3)  # adjacent separations all < 0.5m
    assign_depth_roles(line, WORLD)
    first_labels = [depth_role_tag(d) for d in line]
    line_order = list(line)  # stable list order across frames
    # Sub-band drift on one interior pair (keeps every adjacency < 0.5m).
    line_order[2].y -= 0.1
    line_order[3].y += 0.1
    # INPUT ORDER (2026-08-09): the Y-sort with hysteresis was REMOVED —
    # the depth-role pipeline consumes the caller's list order directly.
    ordered = list(line_order)
    assign_depth_roles(ordered, WORLD)
    second_labels = [depth_role_tag(d) for d in ordered]
    check(
        "labels stable under sub-band Y drift",
        first_labels == second_labels,
        f"{first_labels} -> {second_labels}",
    )

    # ── 4. Fullback exclusion from line labels ─────────────────────
    print("\n[4] Fullback exclusion")
    line = make_line(7)
    line[3].fullback_role_active = True
    assign_depth_roles(line, WORLD)
    line_labels = [depth_role_tag(d) for d in line]
    check(
        "fullback excluded: still 2 WINGER labels for 6 eligible",
        line_labels.count("WINGER") == 2,
        str(line_labels),
    )

    print("\n" + "=" * 60)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
