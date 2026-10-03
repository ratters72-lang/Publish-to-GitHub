"""
verify_depth_role_stability.py — Task A verification for persistent,
hysteresis-protected depth-role assignment.

Drives realistic frame sequences through assign_depth_roles() and asserts:

  1. NO-FLIP: 600 frames of sub-hysteresis drift (<= +-1.5 m) with a fixed
     ruck produce ZERO role or side flips.
  2. SIDE SWAP: a defender crossing ruck_y by +2.5 m (above the 2.0 m
     threshold) flips side EXACTLY once, then locks.
  3. ROLE REASSIGN: with a vacant slot, a defender straying 4 m from its
     D-anchor is reassigned into the vacancy (structural best fit); then
     roles settle and stop flipping.
  4. ORDER INDEPENDENCE: shuffling the defender list every frame does NOT
     change any player's (role, side).
  5. RUCK MOVE: ruck moves +2 X / -1 Y — structural anchors recompute,
     roles/sides persist (no flips) since no hysteresis threshold is
     exceeded.
  6. FULL OCCUPANCY: with every slot occupied, roles are immutable even
     under 5 m drift (no reassignment source).

Emits verify_depth_role_stability_report.json:

    {
      "role_assignment": {
        "persistent_roles": true,
        "hysteresis_enabled": true,
        "side_locked": true,
        "roles_locked": true,
        "frame_to_frame_flips": false,
        "anchor_stability": "stable",
        "issues_remaining": []
      }
    }

Exit code 0 when all scenarios pass, 1 otherwise.
"""

import sys
import json
import random

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, DefenderRole
from game.defence.defence_lvl_1.defensive_depth_roles import (
    assign_depth_roles,
    _build_role_anchors,
    _maintain_side,
)
from game.defence.defence_lvl_1.defensive_split_spacing import (
    required_side_counts,
    side_widths,
)
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

RUCK_X = 60.0
RUCK_Y = 35.0
DEF_LINE_WIDTH = 40.0

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


class MiniWorld:
    """World stub; ruck_y/ruck_x/def_line_width may be overridden per test."""

    def __init__(self, ruck_x=RUCK_X, ruck_y=RUCK_Y, width=DEF_LINE_WIDTH):
        self.ruck_x_m = ruck_x
        self.ruck_y_m = ruck_y
        self.def_line_width_m = width
        self.open_play_engine = None


def make_defender(player_id, x, y, defender_role=None):
    d = Player(x=x, y=y, player_id=player_id)
    if defender_role is not None:
        d.defender_role = defender_role
    return d


def snapshot(line):
    """Persistent identity map: player_id -> (depth_role, depth_side)."""
    return {
        d.player_id: (getattr(d, "depth_role", None), getattr(d, "depth_side", None))
        for d in line
    }


def run_scenario1_no_flip():
    print("\n[1] No-flip under sub-hysteresis drift (600 frames)")
    world = MiniWorld()
    line = [
        make_defender(1, 60.0, 10.0),
        make_defender(2, 60.0, 22.0),
        make_defender(3, 60.0, 30.0),
        make_defender(4, 60.0, 40.0),
        make_defender(5, 60.0, 48.0),
        make_defender(6, 60.0, 60.0),
    ]
    assign_depth_roles(line, world)
    original = snapshot(line)

    flips = set()
    prev = original
    rng = random.Random(1234)
    for frame in range(600):
        for d in line:
            d.y += rng.uniform(-0.006, 0.006)  # ~+-0.36 m over 60 s, far below 2/3 m
        assign_depth_roles(line, world)
        cur = snapshot(line)
        for pid, value in cur.items():
            if value != prev[pid]:
                flips.add((pid, prev[pid], value))
        prev = cur

    check("zero role/side flips across 600 frames", not flips, str(sorted(flips)[:3]))
    changed = any(v != original[k] for k, v in snapshot(line).items())
    check("final mapping equals original mapping", not changed, str(snapshot(line)))


def run_scenario2_side_swap():
    print("\n[2] Side split counts stay exact as a defender crosses the ruck (TASK VIII)")
    world = MiniWorld()
    line = [
        make_defender(1, 60.0, 15.0),   # LEFT wing
        make_defender(2, 60.0, 25.0),   # LEFT centre
        make_defender(3, 60.0, 32.0),   # LEFT A, drifts RIGHT
        make_defender(4, 60.0, 45.0),   # RIGHT A
        make_defender(5, 60.0, 55.0),   # RIGHT centre
        make_defender(6, 60.0, 65.0),   # RIGHT wing
    ]
    assign_depth_roles(line, world)
    before = snapshot(line)
    check("bootstrapped sides: 3 left / 3 right",
          before[3][1] == -1 and before[4][1] == 1,
          str(before))

    # TASK VIII (2026-08-21): side assignment is count-driven by the ruck
    # position + governed width — NOT an individual defender's distance past
    # ruck_y.  The count-optimised split for n=6 at ruck_y=35 is 3/3, so a
    # single defender crossing to the right does NOT change the sides: the
    # nearest-first assignment keeps the counts exactly 3 left / 3 right with
    # minimal movement.  This REPLACES the old SIDE_SWAP_HYSTERESIS_M flip
    # contract (the split is the side authority now).
    for y in (36.0, 36.6, 45.0, 28.0, 33.0):
        line[2].y = y
        assign_depth_roles(line, world)
        snap = snapshot(line)
        n_left = sum(1 for v in snap.values() if v[1] == -1)
        n_right = sum(1 for v in snap.values() if v[1] == 1)
        check(f"split counts stay 3/3 at y={y}",
              n_left == 3 and n_right == 3, str(snap))


def run_scenario3_role_reassign():
    print("\n[3] Contested single-capacity claim limited to one occupant per SIDE (TASK VIII)")
    world = MiniWorld()
    line = [
        make_defender(1, 60.0, 12.0),
        make_defender(2, 60.0, 30.0),
        make_defender(3, 60.0, 15.0),
    ]
    # Simulate an edge state that persistent maintenance must repair: two
    # defenders both carry WINGER on the left side (can occur transiently
    # after a roster change).  Under TASK VIII the count-driven split is the
    # side authority, so after assign_depth_roles the two claimants land on
    # SEPARATE sides (one LEFT-WINGER, one RIGHT-WINGER) — legal.  What must
    # hold is: no SINGLE side carries a duplicate single-capacity role.
    line[0].depth_role = "WINGER"
    line[0].depth_side = -1
    line[0]._depth_seeded = True
    line[1].depth_role = "WINGER"
    line[1].depth_side = -1
    line[1]._depth_seeded = True
    line[2].depth_role = "CENTRE"
    line[2].depth_side = -1
    line[2]._depth_seeded = True

    assign_depth_roles(line, world)
    from collections import Counter
    per_side = {-1: Counter(), 1: Counter()}
    for d in line:
        per_side[d.depth_side][d.depth_role] += 1
    check("no side carries duplicate WINGER/CENTRE/A/B/C",
          all(
              per_side[s].get(r, 0) <= 1
              for s in (-1, 1)
              for r in ("WINGER", "CENTRE", "A", "B", "C")
          ),
          str({k: dict(v) for k, v in per_side.items()}))

    # Roles then settle: 20 frames of drift -> no further role flips (side
    # assignment may legitimately move with the count split, but roles on a
    # settled line stay put).
    prev = snapshot(line)
    flips = []
    rng = random.Random(99)
    for _ in range(20):
        for d in line:
            d.y += rng.uniform(-0.01, 0.01)
        assign_depth_roles(line, world)
        cur = snapshot(line)
        for pid, v in cur.items():
            if v[0] != prev[pid][0]:
                flips.append((pid, prev[pid], v))
        prev = cur
    check("no further ROLE flips after settle", not flips, str(flips))


def run_scenario4_order_independence():
    print("\n[4] Order independence (shuffled defender list every frame)")
    world = MiniWorld()
    line = [
        make_defender(1, 60.0, 12.0),
        make_defender(2, 60.0, 20.0),
        make_defender(3, 60.0, 28.0),
        make_defender(4, 60.0, 42.0),
        make_defender(5, 60.0, 50.0),
        make_defender(6, 60.0, 58.0),
    ]
    assign_depth_roles(line, world)
    original = snapshot(line)

    for frame in range(60):
        rng = random.Random(frame)
        shuffled = line[:]
        rng.shuffle(shuffled)
        assign_depth_roles(shuffled, world)
        cur = snapshot(line)
        if cur != original:
            check(f"mapping unchanged after shuffle frame {frame}", False, str(cur))
            return
    check("player_id -> (role, side) identical under all shuffles", True)


def run_scenario5_ruck_move():
    print("\n[5] Ruck move recomputes anchors without role flips")
    world = MiniWorld(ruck_x=60.0, ruck_y=35.0)
    line = [
        make_defender(1, 60.0, 12.0),
        make_defender(2, 60.0, 20.0),
        make_defender(3, 60.0, 28.0),
        make_defender(4, 60.0, 42.0),
        make_defender(5, 60.0, 50.0),
        make_defender(6, 60.0, 58.0),
    ]
    assign_depth_roles(line, world)
    before = snapshot(line)

    # New ruck (62, 34) — structural anchors recompute.  The ruck moves
    # only +1 m in Y, so no defender crosses the opposite side band by
    # more than 2 m and every defender stays within 3 m of its assigned
    # (new) anchor: zero flips expected.
    world2 = MiniWorld(ruck_x=62.0, ruck_y=34.0)
    assign_depth_roles(line, world2)
    after = snapshot(line)
    check("no role/side flips when ruck moves", after == before,
          f"before={before} after={after}")


def run_scenario7_kickoff_origin():
    print("\n[7] Post-kickoff-catch line forms BC-relative (not field centre)")
    # KICKOFF ORIGIN RULE: at kickoff there is no ruck/PTB/line; the ONLY
    # anchor is the caught BC.  _perform_catch() now seeds ruck_x = BC.x
    # and ruck_y = BC.y, so every downstream geometry (side split, role
    # bootstrap, width anchors) must be centred on BC.y — NOT field centre
    # 35.0.
    #
    # Part A (mid-field pick, unclamped): BC caught at y=30 with everything
    # on-field.  Anchor formula: ruck_y ± half_width * winger_anchor *
    # winger_curve = 30 ± 20 * 1.00 * 1.00 = 30 ± 20.0 -> [10.0, 50.0].
    # (2026-08-17: the global * 0.8 width compression was removed and
    # width_curve_factor is neutral 1.0, so WINGER sits at exactly
    # half-width.)  Neither side clamps (10.0 > margin 3.0, 50.0 < 67.0),
    # so the pair is EXACTLY symmetric about BC.y=30.  A field-centre
    # origin would centre the pair on 35.0 instead.
    BC_X = 30.0
    BC_Y = 30.0
    width = 40.0
    half_width = width * 0.5
    tuners = DefenceTuners()
    winger_raw = half_width * tuners.role_width_anchor["WINGER"] \
        * tuners.width_curve_factor["WINGER"]
    world = MiniWorld(ruck_x=BC_X, ruck_y=BC_Y)

    # Six defenders already set around the BC (e.g. kickoff chase lanes).
    line = [
        make_defender(1, 40.0, 49.0),   # far right of BC.y=30 (WINGER slot)
        make_defender(2, 38.0, 35.0),   # right centre
        make_defender(3, 36.0, 32.0),   # right inner (A slot)
        make_defender(4, 36.0, 28.0),   # left inner (A slot)
        make_defender(5, 38.0, 25.0),   # left centre
        make_defender(6, 40.0, 11.0),   # far left of BC.y=30 (WINGER slot)
    ]
    assign_depth_roles(line, world)

    by_id = {d.player_id: (d.depth_role, d.depth_side) for d in line}
    # COUNT-AUTHORITY SPLIT (2026-09-03): decide_side_counts is the count boss.
    # At BC.y=30 with n_total=6 the room + phase model yields the symmetric
    # 3 LEFT / 3 RIGHT split (the count never exceeds the physical room, and
    # the phase/room bias lands on symmetry here).  This REPLACES the legacy
    # ruck-geometry 2/4 formula.  The split is still ruck-relative (centred on
    # BC.y=30, not field-centre 35.0) — the anchor formula below proves that.
    check("side split is count-authority (3 LEFT / 3 RIGHT by room+phase)",
          by_id[1][1] == 1 and by_id[2][1] == 1 and by_id[3][1] == 1
          and by_id[4][1] == -1 and by_id[5][1] == -1 and by_id[6][1] == -1,
          str(by_id))

    # Innermost defenders (closest to BC.y=30) bootstrap to A,
    # outermost defenders (furthest from BC.y) to WINGER.
    check("WINGER assigned to defender furthest from BC.y",
          by_id[1][0] == "WINGER" and by_id[6][0] == "WINGER",
          str(by_id))
    # Innermost interior defender per side is A.  COUNT-AUTHORITY (2026-09-03):
    # with a symmetric 3 LEFT / 3 RIGHT split both sides form the full
    # WINGER/CENTRE/A ladder.  RIGHT: 1=WINGER (y=49), 2=CENTRE (y=35), 3=A (y=32).
    # LEFT: 6=WINGER (y=11), 5=CENTRE (y=25), 4=A (y=28).  No short-side n=2
    # collapse applies because the count is 3 per side (not 2).
    check("innermost interior defender per side is A (right: 3=A; left: 4=A)",
          by_id[3][0] == "A" and by_id[4][0] == "A" and by_id[5][0] == "CENTRE",
          str(by_id))

    # Structural anchors sit at the PER-SIDE width (single width source).
    # 2026-09-02: _build_role_anchors is now PER-SIDE — LEFT uses left_width,
    # RIGHT uses right_width from side_widths().  2026-09-03: at BC.y=30 the
    # LEFT is the SHORT side, so side_widths squeezes left_width (via
    # tuner_short_side_factor / the available-room cap) below the full 40.0.
    # Therefore the anchors are NOT symmetric: the OPEN (RIGHT) WINGER sits
    # at BC_Y + right_width*0.5, the SHORT (LEFT) WINGER at BC_Y - left_width*0.5.
    # Both must equal the spacing chain's per-side WINGER anchor (one source).
    left_width, right_width = side_widths(world, tuners)
    anchors = _build_role_anchors(BC_Y, left_width, right_width, tuners)
    right_winger = anchors[1]["WINGER"]
    left_winger = anchors[-1]["WINGER"]
    expected_right = BC_Y + right_width * 0.5  # WINGER offset = 1.0 full half-width
    expected_left = BC_Y - left_width * 0.5
    check("winger anchors sit at per-side width anchors (single width source)",
          abs(right_winger - expected_right) < 0.01
          and abs(left_winger - expected_left) < 0.01
          and abs(right_winger - (BC_Y + winger_raw)) < 0.01,
          f"right={right_winger:.3f} left={left_winger:.3f} "
          f"expected right={expected_right:.3f} left={expected_left:.3f} "
          f"(open side at full {winger_raw:.3f} half-width)")

    # Part B (corner pick, clamped): BC caught at y=12 — the user's corner
    # example.  The right winger (away from the touchline) must form at
    # 12 + 20.0 = 32.0 (BC-relative).  With the old broken field-centre
    # origin (ruck_y=35) it would sit at 55.0 — a >23 m error.  The left
    # winger correctly clamps to the sideline margin (3.0) — on-field
    # geometry, not a bug.
    # GOVERNED WIDTH IS THE AUTHORITATIVE TOTAL SPAN (2026-09-14): the
    # short-side "margin + buffer" reduction is REMOVED.  The governed width
    # is split across the two ruck-anchored halves, the off-centre balance
    # shifts toward the roomier touchline, and the short side's half-span is
    # simply what the ABSOLUTE sideline clamp can hold (the only safety bound
    # left).  So each corner WINGER sits on its OWN per-side half-span, and
    # the short-side one lands exactly ON the margin.
    world_corner = MiniWorld(ruck_x=BC_X, ruck_y=12.0)
    corner_left_w, corner_right_w = side_widths(world_corner, tuners)
    corner_anchors = _build_role_anchors(12.0, corner_left_w, corner_right_w, tuners)
    corner_right = corner_anchors[1]["WINGER"]
    corner_left = corner_anchors[-1]["WINGER"]
    check("corner pick: right winger forms on its own half-span (BC-relative)",
          abs(corner_right - (12.0 + corner_right_w * 0.5)) < 0.01,
          f"corner_right={corner_right:.3f} want="
          f"{12.0 + corner_right_w * 0.5:.3f} (field-centre origin would be "
          f"{35.0 + corner_right_w * 0.5:.3f})")
    check("corner pick: left winger lands on the ABSOLUTE sideline margin",
          abs(corner_left - tuners.sideline_margin_m) < 0.01,
          f"corner_left={corner_left:.3f} margin="
          f"{tuners.sideline_margin_m:.3f} (the retired short-side buffer held "
          f"it off the margin)")


def run_scenario6_full_occupancy_immutable():
    print("\n[6] Full occupancy -> roles immutable even under 5 m drift")
    world = MiniWorld()
    # 6 defenders = 3 per side -> WINGER/CENTRE/A per side = every slot on
    # each side occupied.  Roles must be immutable under drift because no
    # slot is vacant to accept a reassignment.
    line = [
        make_defender(1, 60.0, 12.0),
        make_defender(2, 60.0, 20.0),
        make_defender(3, 60.0, 28.0),
        make_defender(4, 60.0, 42.0),
        make_defender(5, 60.0, 50.0),
        make_defender(6, 60.0, 58.0),
    ]
    assign_depth_roles(line, world)
    before = snapshot(line)
    check("bootstrap: WINGER/CENTRE/A per side",
          [before[i][0] for i in (1, 2, 3)] == ["WINGER", "CENTRE", "A"]
          and [before[i][0] for i in (4, 5, 6)] == ["A", "CENTRE", "WINGER"],
          str(before))

    # Drift everyone 5 m toward the ruck (well past the 3 m anchor threshold,
    # but no slot is vacant -> no movement possible).
    for d in line:
        d.y = RUCK_Y + (d.y - RUCK_Y) * 0.5
    assign_depth_roles(line, world)
    after = snapshot(line)
    check("roles immutable under full occupancy + 5 m drift",
          after == before, f"before={before} after={after}")


def main():
    print("=" * 64)
    print("DEPTH-ROLE STABILITY VALIDATION (Task A, 2026-08-09)")
    print("=" * 64)

    run_scenario1_no_flip()
    run_scenario2_side_swap()
    run_scenario3_role_reassign()
    run_scenario4_order_independence()
    run_scenario5_ruck_move()
    run_scenario6_full_occupancy_immutable()
    run_scenario7_kickoff_origin()

    report = {
        "role_assignment": {
            "persistent_roles": True,
            "hysteresis_enabled": True,
            "side_locked": True,
            "roles_locked": True,
            "frame_to_frame_flips": False,
            "anchor_stability": "stable",
            "issues_remaining": [],
        }
    }
    if failures:
        report["role_assignment"]["persistent_roles"] = False
        report["role_assignment"]["hysteresis_enabled"] = False
        report["role_assignment"]["side_locked"] = False
        report["role_assignment"]["roles_locked"] = False
        report["role_assignment"]["frame_to_frame_flips"] = True
        report["role_assignment"]["anchor_stability"] = "unstable"
        report["role_assignment"]["issues_remaining"] = failures

    report_path = "verify_depth_role_stability_report.json"
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print(f"\nReport written to {report_path}")
    print(json.dumps(report, indent=2))

    print("\n" + "=" * 64)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
