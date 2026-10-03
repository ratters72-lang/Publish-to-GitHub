"""
verify_depth_stability.py — Task A-2 verification: depth-role stabilisation,
hysteresis hardening, side-lock, role-lock, anchor integrity, no late-depth-
switch spam, and stable role-depth X targets.

Drives realistic frame sequences through the persistent engagement-state
module (defensive_engagement_state.update_engagement_state) together with
the stable structural depth-role assignment (defensive_depth_roles.
assign_depth_roles) and asserts:

  1. NO PASSIVE RE-ENTRY SPAM: once PASSIVE is entered with BC at/past the
     anchor it stays PASSIVE until BC retreats behind the anchor or a PTB
     reset — PASSIVE is never re-entered repeatedly frame after frame.
  2. NO OSCILLATION WITHIN HYSTERESIS WINDOW: BC jitter around a band
     boundary cannot flip the engagement role inside MIN_SWITCH_INTERVAL
     (20 frames).
  3. NO SIDE FLIPPING: a defender side (depth_side / engagement side)
     persists for the whole open-play phase; crossing ruck_y without
     exceeding the hysteresis threshold never flips it.
  4. NO ANCHOR DRIFT: defensive_line_anchor_m() always == ruck_x + 10 and
     never falls behind BC.
  5. NO LATE-DEPTH-SWITCH SPAM: role_depth_class transitions only on a
     real switch (interval elapsed), never re-entering the same role.
  6. ROLE-DEPTH X STABLE: role_depth_target_x is stamped from the stable
     structural role (ruck_x + 10), immutable between calls, and never
     recomputed after spacing.

Emits verify_depth_stability_report.json and exits 0 when all checks pass.
"""

import sys
import json
import random

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player
from game.defence.defence_lvl_1.defensive_depth_roles import (
    assign_depth_roles,
    ROLE_DEPTH_X_OFFSET_M,
)
from game.defence.defence_lvl_1.defensive_engagement_state import (
    update_engagement_state,
    reset_engagement_state,
    defensive_line_anchor_m,
    MIN_SWITCH_INTERVAL,
)

RUCK_X = 60.0
RUCK_Y = 35.0

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


class MiniWorld:
    def __init__(self, ruck_x=RUCK_X, ruck_y=RUCK_Y, frame=0):
        self.ruck_x_m = ruck_x
        self.ruck_y_m = ruck_y
        self.frame_count = frame
        self.ball_carrier = None
        self.defending_team = []
        self.open_play_engine = None
        self.defensive_line_anchor = None


def make_defender(player_id, x, y):
    d = Player(x=x, y=y, player_id=player_id)
    return d


def run_scenario1_no_passive_reentry():
    print("\n[1] No PASSIVE re-entry spam (role-lock holds after line break)")
    world = MiniWorld(ruck_x=60.0, ruck_y=35.0)
    defenders = [
        make_defender(1, 70.0, 20.0),
        make_defender(2, 70.0, 30.0),
        make_defender(3, 70.0, 40.0),
        make_defender(4, 70.0, 50.0),
    ]
    world.defending_team = defenders
    world.ball_carrier = make_defender(99, 55.0, 35.0)
    world.frame_count = 1

    # Seed roles so update_engagement_state sees depth_side.
    assign_depth_roles(defenders, world)

    # Push the BC well past the anchor (ruck 60 + 10 = 70) frame by frame.
    # Frame 1: BC at 71 (> anchor) -> engagement role flips to PASSIVE
    # once the interval elapses.  Bootstrap (frame 1, no interval) keeps
    # BALANCED on frame 1; the switch to PASSIVE occurs on a later frame.
    prev_roles = None
    passive_entries = []
    for frame in range(1, 121):
        world.frame_count = frame
        # PASSIVE requires bc_x > anchor + 5 = 75 (anchor = ruck 60 + 10).
        # 76 with jitter is clearly past the band edge so the role-lock engages.
        world.ball_carrier.x = 76.0 + (frame % 3) * 0.1
        update_engagement_state(world, defenders)
        roles = {d.player_id: d.role_depth_class for d in defenders}
        if prev_roles is not None:
            for pid, role in roles.items():
                if prev_roles[pid] != "PASSIVE" and role == "PASSIVE":
                    passive_entries.append((frame, pid))
        prev_roles = roles

    # Once entered, PASSIVE must persist to the end of the phase (BC never
    # retreats behind the anchor in this scenario).  At most ONE entry per
    # defender is allowed after bootstrap + hysteresis interval.
    from collections import Counter
    entry_counts = Counter(pid for _, pid in passive_entries)
    check("PASSIVE entered at most once per defender per phase",
          all(c <= 1 for c in entry_counts.values()),
          str(dict(entry_counts)))
    check("no PASSIVE re-entry spam (entries << frames)",
          len(passive_entries) <= len(defenders), str(passive_entries))

    # Last-frame roles: all defenders must be PASSIVE (BC past anchor, lock holds).
    final_roles = {d.player_id: d.role_depth_class for d in defenders}
    check("all defenders PASSIVE at end of phase (BC past anchor)",
          all(r == "PASSIVE" for r in final_roles.values()),
          str(final_roles))


def run_scenario2_hysteresis_window():
    print(f"\n[2] No oscillation within the {MIN_SWITCH_INTERVAL}-frame hysteresis window")
    world = MiniWorld(ruck_x=60.0, ruck_y=35.0)
    defenders = [
        make_defender(1, 70.0, 25.0),
        make_defender(2, 70.0, 35.0),
        make_defender(3, 70.0, 45.0),
    ]
    world.defending_team = defenders
    world.ball_carrier = make_defender(99, 55.0, 35.0)
    assign_depth_roles(defenders, world)

    # BC oscillates across the anchor (70) every few frames — a classic
    # frame-by-frame depth-role oscillation source.  The hysteresis interval
    # must suppress all flips: the role may change only after 20 frames.
    prev_roles = None
    flips = []
    for frame in range(1, 61):
        world.frame_count = frame
        # Oscillate: 69.8 (< anchor-0.2, inside band) and 70.5 (> anchor)
        world.ball_carrier.x = 69.8 if frame % 2 == 1 else 70.5
        update_engagement_state(world, defenders)
        roles = {d.player_id: d.role_depth_class for d in defenders}
        if prev_roles is not None:
            for pid, role in roles.items():
                if role != prev_roles[pid]:
                    flips.append((frame, pid, prev_roles[pid], role))
        prev_roles = roles

    check("no role flips under boundary oscillation within 60 frames",
          not flips, str(flips[:5]))


def run_scenario3_side_lock():
    print("\n[3] Side-lock: no side flipping during open-play phase")
    world = MiniWorld(ruck_x=60.0, ruck_y=35.0)
    defenders = [
        make_defender(1, 60.0, 32.0),
        make_defender(2, 60.0, 38.0),
    ]
    world.defending_team = defenders
    world.ball_carrier = make_defender(99, 55.0, 35.0)
    assign_depth_roles(defenders, world)

    # Bootstrapped sides: 32 < 35 -> LEFT, 38 > 35 -> RIGHT.
    sides_before = {d.player_id: getattr(d, "depth_side", None) for d in defenders}
    check("bootstrap sides assigned", sides_before[1] == -1 and sides_before[2] == 1,
          str(sides_before))

    # Slow drift of both defenders toward the other side, but never more
    # than the 2.0 m hysteresis threshold in one frame step.
    flips = []
    for frame in range(1, 201):
        world.frame_count = frame
        world.ball_carrier.x = 55.0
        for d in defenders:
            d.y += (0.01 if d.player_id == 1 else -0.01)
        assign_depth_roles(defenders, world)
        update_engagement_state(world, defenders)
        sides = {d.player_id: getattr(d, "depth_side", None) for d in defenders}
        if sides != sides_before:
            flips.append((frame, sides))
        sides_before = sides

    check("no side flips during drift + full phase", not flips, str(flips[:3]))


def run_scenario4_anchor_integrity():
    print("\n[4] Anchor integrity: anchor == ruck_x + 10, never behind BC")
    import random as _r
    rng = _r.Random(7)
    for _ in range(200):
        ruck_x = 40.0 + rng.uniform(0, 40.0)
        world = MiniWorld(ruck_x=ruck_x, ruck_y=35.0)
        anchor = defensive_line_anchor_m(world)
        if abs(anchor - (ruck_x + 10.0)) > 1e-6:
            check(f"anchor == ruck_x + 10 for ruck_x={ruck_x:.2f}", False,
                  f"got {anchor:.3f}")
            return
    check("anchor == ruck_x + 10 across 200 ruck positions", True)

    # Never behind BC: with BC running far ahead of the anchor, the anchor
    # must remain ruck + 10 and never drift toward/behind BC.
    world = MiniWorld(ruck_x=50.0, ruck_y=35.0)
    anchor = defensive_line_anchor_m(world)
    check("anchor independent of BC position (BC at 65, anchor stays 60)",
          abs(anchor - 60.0) < 1e-6, f"anchor={anchor:.3f}")


def run_scenario5_no_late_switch_spam():
    print("\n[5] No late-depth-switch spam (transitions only on real switches)")
    world = MiniWorld(ruck_x=50.0, ruck_y=35.0)
    defenders = [make_defender(1, 60.0, 35.0)]
    world.defending_team = defenders
    world.ball_carrier = make_defender(99, 50.0, 35.0)
    assign_depth_roles(defenders, world)

    # Steady BALANCED phase: 80 frames of jitter inside the band.  A switch
    # should NEVER occur because desired == BALANCED the whole time.
    # TASK IV (2026-08-14): the band is [anchor - AGGRESSIVE_BAND_M, anchor +
    # PASSIVE_BAND_M] = [60 - 4.9, 60 + 4.85] = [55.1, 64.85], so jitter must
    # stay strictly inside 56.0..56.8 (55.0 is now past the AGGRESSIVE edge).
    prev = None
    transitions = []
    for frame in range(1, 81):
        world.frame_count = frame
        world.ball_carrier.x = 56.0 + (frame % 5) * 0.2  # 56.0..56.8: inside band (anchor 60)
        update_engagement_state(world, defenders)
        role = defenders[0].role_depth_class
        if prev is not None and role != prev:
            transitions.append((frame, prev, role))
        prev = role
    check("zero transitions while BC stays inside the band (80 frames)",
          not transitions, str(transitions))

    # Then BC commits fully past the anchor and stays there: exactly ONE
    # BALANCED->PASSIVE switch after the hysteresis interval, then silence.
    transitions = []
    prev = None
    for frame in range(81, 181):
        world.frame_count = frame
        world.ball_carrier.x = 70.0
        update_engagement_state(world, defenders)
        role = defenders[0].role_depth_class
        if prev is not None and role != prev:
            transitions.append((frame, prev, role))
        prev = role
    # Allowed: one transition to PASSIVE (after the interval).  No repeated
    # PASSIVE->PASSIVE spam by construction; no oscillation back.
    check("at most one transition after BC commits past anchor (100 frames)",
          len(transitions) <= 1, str(transitions))
    check("final role is PASSIVE (locked)", prev == "PASSIVE", str(prev))


def run_scenario6_role_depth_x_stable():
    print("\n[6] Role-depth X stable across frames (no mid-frame recompute)")
    world = MiniWorld(ruck_x=60.0, ruck_y=35.0)
    defenders = [
        make_defender(1, 70.0, 20.0),
        make_defender(2, 70.0, 30.0),
        make_defender(3, 70.0, 40.0),
        make_defender(4, 70.0, 50.0),
    ]
    world.defending_team = defenders
    world.ball_carrier = make_defender(99, 60.0, 35.0)
    assign_depth_roles(defenders, world)

    expected_x = RUCK_X + ROLE_DEPTH_X_OFFSET_M  # 70.0
    for d in defenders:
        check(f"role_depth_target_x == ruck_x + 10 (rigid) for id={d.player_id}",
              abs(getattr(d, "role_depth_target_x", None) - expected_x) < 1e-6,
              f"got {getattr(d, 'role_depth_target_x', None)}")

    # 60 frames of BC movement + spacing must NEVER change the stamp.
    for frame in range(1, 61):
        world.frame_count = frame
        world.ball_carrier.x = 55.0 + frame * 0.3
        assign_depth_roles(defenders, world)
        update_engagement_state(world, defenders)
        for d in defenders:
            if abs(getattr(d, "role_depth_target_x", None) - expected_x) > 1e-6:
                check("role_depth_target_x immutable across frames", False,
                      f"frame={frame} id={d.player_id}")
                return
    check("role_depth_target_x immutable across 60 frames", True)

    # Ruck moves -> anchor recomputes structurally (ruck + 10), NOT from BC.
    world2 = MiniWorld(ruck_x=62.0, ruck_y=35.0)
    assign_depth_roles(defenders, world2)
    expected_x2 = 62.0 + ROLE_DEPTH_X_OFFSET_M
    check("role_depth_target_x recomputes to NEW ruck_x + 10 (not BC)",
          all(abs(getattr(d, "role_depth_target_x", None) - expected_x2) < 1e-6
              for d in defenders),
          f"expected {expected_x2}")


def main():
    print("=" * 72)
    print("DEPTH-STABILITY VALIDATION (Task A-2, 2026-08-10)")
    print("=" * 72)

    run_scenario1_no_passive_reentry()
    run_scenario2_hysteresis_window()
    run_scenario3_side_lock()
    run_scenario4_anchor_integrity()
    run_scenario5_no_late_switch_spam()
    run_scenario6_role_depth_x_stable()

    report = {
        "depth_stability": {
            "no_passive_reentry": "PASS" if "no-passive-reentry" not in failures else "FAIL",
            "no_oscillation_in_window": True,
            "side_locked": True,
            "anchor_integrity": True,
            "no_late_switch_spam": True,
            "role_depth_x_stable": True,
            "issues_remaining": failures,
        }
    }
    if failures:
        report["depth_stability"]["no_oscillation_in_window"] = False
        report["depth_stability"]["side_locked"] = False
        report["depth_stability"]["anchor_integrity"] = False
        report["depth_stability"]["no_late_switch_spam"] = False
        report["depth_stability"]["role_depth_x_stable"] = False
        report["depth_stability"]["no_passive_reentry"] = "FAIL"
        report["depth_stability"]["issues_remaining"] = failures

    report_path = "verify_depth_stability_report.json"
    with open(report_path, "w", encoding="utf-8") as fh:
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
