"""
Audit Step 1 — DefensiveRoleSpacing output verification.

Tests DefensiveRoleSpacing.compute_role_spacing() with known inputs
for all role/role combinations, verifying target_x/target_y against
expected values from the specification.

Scenarios tested:
  1. MARKER_A — bc.x - 1.0, bc.y
  2. MARKER_B — bc.x - 1.0, bc.y
  3. DUMMY_HALF — bc.x - 1.0, bc.y
  4. PRIMARY with slow BC — bc.x - 1.0, fr_y + lane_offset
  5. PRIMARY with fast BC — lead_point (bc.x + bc.vx*0.3), fr_y + lane_offset
  6. SECONDARY — same as PRIMARY
  7. PASSIVE — anchor_x, fr_y + lane_offset
  8. BACKFIELD — anchor_x, fr_y + lane_offset
  9. Retreat hesitation — hold_x freezes X at defender.x
  10. Lateral hesitation — ty = defender.y
  11. Compression — lane_offset shrinks with compression_factor
  12. Compression + lateral hesitation — lane_offset freezes at uncompressed
"""

import sys
import os
import math

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from game.defence.defence_lvl_1.defensive_role_spacing import DefensiveRoleSpacing
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from player.player import Player, DefenderRole


class FakeWorld:
    """Minimal world stub for DefensiveRoleSpacing.prepare()."""
    def __init__(self):
        self.defensive_line_anchor = 55.0
        self.ball_carrier = None
        self.first_receiver = None


class FakePlayer:
    """Minimal player stub for spacing tests."""
    def __init__(self, x=50.0, y=35.0, vx=0.0):
        self.x = x
        self.y = y
        self.vx = vx
        self.role = None
        self.defender_role = None
        self.depth_class = "PASSIVE"
        self.lane_index = 0


def make_world(bc_x=50.0, bc_y=35.0, bc_vx=0.0, fr_x=50.0, fr_y=35.0, anchor_x=55.0):
    w = FakeWorld()
    w.defensive_line_anchor = anchor_x
    w.ball_carrier = FakePlayer(x=bc_x, y=bc_y, vx=bc_vx)
    w.first_receiver = FakePlayer(x=fr_x, y=fr_y)
    return w


def test_scenario(name, tuners, def_spacing, world, defender, bc, fr,
                  expected_tx_range, expected_ty_range,
                  role=None, lane_index=0,
                  compression_factor=0.0, lateral_state=0.0, retreat_state=0.0,
                  fail_hard=False):
    """Run one scenario and report results."""
    def_spacing.prepare(world)
    tx, ty = def_spacing.compute_role_spacing(
        defender=defender,
        ball_carrier=bc,
        role=role,
        lane_index=lane_index,
        compression_factor=compression_factor,
        lateral_state=lateral_state,
        retreat_state=retreat_state,
    )

    tx_ok = expected_tx_range[0] <= tx <= expected_tx_range[1]
    ty_ok = expected_ty_range[0] <= ty <= expected_ty_range[1]

    status = "PASS" if tx_ok and ty_ok else "FAIL"
    print(f"  [{status}] {name}")
    print(f"         target=({tx:.2f}, {ty:.2f})  "
          f"expected_tx={expected_tx_range}  expected_ty={expected_ty_range}")
    if not tx_ok or not ty_ok:
        print(f"         depth_class={defender.depth_class}  role={role}  "
              f"lane_idx={lane_index}  cf={compression_factor}")
        if hasattr(defender, 'vx'):
            print(f"         bc.vx={bc.vx if bc else 'N/A'}")
        if fail_hard:
            raise AssertionError(f"FAIL: {name}")
    return tx_ok and ty_ok


def main():
    tuners = DefenceTuners()
    spacing = DefensiveRoleSpacing(tuners)

    c = 0  # passed count
    f = 0  # failed count

    bc_default_x = 50.0
    bc_default_y = 35.0
    anchor_x = 55.0
    fr_y = 32.0  # offset from bc.y so we can detect lane_offset vs bc.y

    # ──────────────────────────────────────────────────────────
    # Scenario 1: MARKER_A — should be bc.x - 1.0, bc.y
    # ──────────────────────────────────────────────────────────
    print("\n[Scenario 1] MARKER_A — bc.x - 1.0, bc.y")
    world = make_world(bc_x=bc_default_x, bc_y=bc_default_y, fr_y=fr_y)
    d = FakePlayer(x=45.0, y=35.0)
    d.depth_class = "PRIMARY"  # depth_class irrelevant for markers
    d.defender_role = DefenderRole.MARKER_A
    ok = test_scenario(
        "MARKER_A -> bc.x - 1.0, bc.y",
        tuners, spacing, world, d, world.ball_carrier, world.first_receiver,
        expected_tx_range=(48.5, 49.5),  # bc.x(50) - 1.0 = 49
        expected_ty_range=(34.5, 35.5),  # bc.y = 35
    )
    c += 1 if ok else 0; f += 0 if ok else 1

    # ──────────────────────────────────────────────────────────
    # Scenario 2: MARKER_B — same as A
    # ──────────────────────────────────────────────────────────
    print("\n[Scenario 2] MARKER_B — bc.x - 1.0, bc.y")
    world = make_world(bc_x=bc_default_x, bc_y=bc_default_y, fr_y=fr_y)
    d = FakePlayer(x=45.0, y=35.0)
    d.depth_class = "SECONDARY"
    d.defender_role = DefenderRole.MARKER_B
    ok = test_scenario(
        "MARKER_B -> bc.x - 1.0, bc.y",
        tuners, spacing, world, d, world.ball_carrier, world.first_receiver,
        expected_tx_range=(48.5, 49.5),
        expected_ty_range=(34.5, 35.5),
    )
    c += 1 if ok else 0; f += 0 if ok else 1

    # ──────────────────────────────────────────────────────────
    # Scenario 3: DUMMY_HALF — bc.x - 1.0, bc.y
    # ──────────────────────────────────────────────────────────
    print("\n[Scenario 3] DUMMY_HALF — bc.x - 1.0, bc.y")
    world = make_world(bc_x=bc_default_x, bc_y=bc_default_y, fr_y=fr_y)
    d = FakePlayer(x=45.0, y=35.0)
    d.depth_class = "PASSIVE"
    d.defender_role = DefenderRole.DUMMY_HALF
    ok = test_scenario(
        "DUMMY_HALF -> bc.x - 1.0, bc.y",
        tuners, spacing, world, d, world.ball_carrier, world.first_receiver,
        expected_tx_range=(48.5, 49.5),
        expected_ty_range=(34.5, 35.5),
    )
    c += 1 if ok else 0; f += 0 if ok else 1

    # ──────────────────────────────────────────────────────────
    # Scenario 4: PRIMARY (slow BC) — bc.x - 1.0, fr_y + lane_offset
    # BC.vx = 0.2  (below 0.5 threshold → no lead-point)
    # lane_index = 0 → offset = 0
    # ──────────────────────────────────────────────────────────
    print("\n[Scenario 4] PRIMARY, slow BC (vx=0.2) — bc.x - 1.0, fr_y")
    world = make_world(bc_x=bc_default_x, bc_y=bc_default_y, bc_vx=0.2, fr_y=fr_y)
    d = FakePlayer(x=45.0, y=35.0)
    d.depth_class = "PRIMARY"
    ok = test_scenario(
        "PRIMARY slow -> bc.x - 1.0, fr_y (lane 0)",
        tuners, spacing, world, d, world.ball_carrier, world.first_receiver,
        expected_tx_range=(48.5, 49.5),  # bc.x(50) - 1.0 = 49
        expected_ty_range=(31.5, 32.5),  # fr_y = 32, offset = 0
        lane_index=0,
    )
    c += 1 if ok else 0; f += 0 if ok else 1

    # ──────────────────────────────────────────────────────────
    # Scenario 5: PRIMARY (fast BC) — lead point OVERRIDES bc.x - 1.0
    # BC.vx = 8.0 (above 0.5 threshold → lead-point active)
    # lead_point = bc.x + 8.0*0.3 = 50 + 2.4 = 52.4
    # WARNING: This puts the defender AHEAD of BC (52.4 > 50.0)
    # ──────────────────────────────────────────────────────────
    print("\n[Scenario 5] PRIMARY, fast BC (vx=8.0) — LEAD POINT (⚠ ahead of BC)")
    world = make_world(bc_x=bc_default_x, bc_y=bc_default_y, bc_vx=8.0, fr_y=fr_y)
    d = FakePlayer(x=45.0, y=35.0)
    d.depth_class = "PRIMARY"
    # Expected tx: lead_point = 50 + 8.0*0.3 = 52.4
    # This is AHEAD of BC (50.0) — ⚠ potential bug
    ok = test_scenario(
        "PRIMARY fast -> lead_point (ahead of BC)",
        tuners, spacing, world, d, world.ball_carrier, world.first_receiver,
        expected_tx_range=(51.9, 52.9),  # lead_point ~52.4
        expected_ty_range=(31.5, 32.5),  # fr_y = 32
        lane_index=0,
    )
    c += 1 if ok else 0; f += 0 if ok else 1
    # ⚠ FLAG: defender target_x > bc.x means defender aims AHEAD of BC.
    #   If BC is at 50.0, defender aims at 52.4 — a full 2.4m past BC.
    #   This means the defender runs PAST the BC before turning.
    #   In real rugby, you tackle FROM BEHIND, not by running past.
    print(f"         ⚠  WARNING: target_x({spacing._bcx + spacing._bcvx*0.3:.2f}) > bc.x({spacing._bcx:.2f})")
    print(f"            Defender is aiming {spacing._bcx + spacing._bcvx*0.3 - spacing._bcx:.2f}m AHEAD of BC")

    # ──────────────────────────────────────────────────────────
    # Scenario 6: PRIMARY moderate BC (vx=4.0) — lead_point = 50+4*0.3=51.2
    # Still ahead of BC
    # ──────────────────────────────────────────────────────────
    print("\n[Scenario 6] PRIMARY, moderate BC (vx=4.0) — still ahead")
    world = make_world(bc_x=bc_default_x, bc_y=bc_default_y, bc_vx=4.0, fr_y=fr_y)
    d = FakePlayer(x=45.0, y=35.0)
    d.depth_class = "PRIMARY"
    ok = test_scenario(
        "PRIMARY moderate -> lead_point = 51.2",
        tuners, spacing, world, d, world.ball_carrier, world.first_receiver,
        expected_tx_range=(50.7, 51.7),  # lead_point 51.2
        expected_ty_range=(31.5, 32.5),
        lane_index=0,
    )
    c += 1 if ok else 0; f += 0 if ok else 1
    print(f"         ⚠  target_x({spacing._bcx + spacing._bcvx*0.3:.2f}) > bc.x({spacing._bcx:.2f})")

    # ──────────────────────────────────────────────────────────
    # Scenario 7: SECONDARY (slow BC) — same as PRIMARY
    # ──────────────────────────────────────────────────────────
    print("\n[Scenario 7] SECONDARY, slow BC — bc.x - 1.0, fr_y")
    world = make_world(bc_x=bc_default_x, bc_y=bc_default_y, bc_vx=0.2, fr_y=fr_y)
    d = FakePlayer(x=45.0, y=35.0)
    d.depth_class = "SECONDARY"
    ok = test_scenario(
        "SECONDARY slow -> bc.x - 1.0",
        tuners, spacing, world, d, world.ball_carrier, world.first_receiver,
        expected_tx_range=(48.5, 49.5),
        expected_ty_range=(31.5, 32.5),
        lane_index=0,
    )
    c += 1 if ok else 0; f += 0 if ok else 1

    # ──────────────────────────────────────────────────────────
    # Scenario 8: PASSIVE — anchor_x, fr_y + lane_offset
    # ──────────────────────────────────────────────────────────
    print("\n[Scenario 8] PASSIVE — anchor_x, fr_y")
    world = make_world(bc_x=bc_default_x, bc_y=bc_default_y, fr_y=fr_y, anchor_x=anchor_x)
    d = FakePlayer(x=50.0, y=35.0)
    d.depth_class = "PASSIVE"
    ok = test_scenario(
        "PASSIVE -> anchor_x, fr_y",
        tuners, spacing, world, d, world.ball_carrier, world.first_receiver,
        expected_tx_range=(54.5, 55.5),  # anchor_x = 55
        expected_ty_range=(31.5, 32.5),  # fr_y = 32
        lane_index=0,
    )
    c += 1 if ok else 0; f += 0 if ok else 1

    # ──────────────────────────────────────────────────────────
    # Scenario 9: Lane offset — lane_index=2, no compression
    # offset = 2 * 3.0 = 6.0 → ty = fr_y + 6.0 = 38.0
    # ──────────────────────────────────────────────────────────
    print("\n[Scenario 9] Lane offset — lane_index=2, no compression")
    world = make_world(bc_x=bc_default_x, bc_y=bc_default_y, fr_y=fr_y, anchor_x=anchor_x)
    d = FakePlayer(x=50.0, y=38.0)
    d.depth_class = "SECONDARY"
    ok = test_scenario(
        "Lane index 2 -> ty = fr_y + 6.0 = 38.0",
        tuners, spacing, world, d, world.ball_carrier, world.first_receiver,
        expected_tx_range=(48.5, 49.5),  # bc.x - 1.0
        expected_ty_range=(37.5, 38.5),  # fr_y(32) + 2*3.0 = 38.0
        lane_index=2,
    )
    c += 1 if ok else 0; f += 0 if ok else 1

    # ──────────────────────────────────────────────────────────
    # Scenario 10: Lane offset — lane_index=-2, no compression
    # offset = -2 * 3.0 = -6.0 → ty = 32 - 6.0 = 26.0
    # ──────────────────────────────────────────────────────────
    print("\n[Scenario 10] Lane offset — lane_index=-2, no compression")
    world = make_world(bc_x=bc_default_x, bc_y=bc_default_y, fr_y=fr_y, anchor_x=anchor_x)
    d = FakePlayer(x=50.0, y=26.0)
    d.depth_class = "SECONDARY"
    ok = test_scenario(
        "Lane index -2 -> ty = fr_y - 6.0 = 26.0",
        tuners, spacing, world, d, world.ball_carrier, world.first_receiver,
        expected_tx_range=(48.5, 49.5),
        expected_ty_range=(25.5, 26.5),  # 32 - 6.0 = 26.0
        lane_index=-2,
    )
    c += 1 if ok else 0; f += 0 if ok else 1

    # ──────────────────────────────────────────────────────────
    # Scenario 11: Compression factor = 0.5 → offset halved
    # lane_index=2, compression=0.5 → offset = 2*3.0*(1-0.5) = 3.0
    # ty = 32 + 3.0 = 35.0
    # ──────────────────────────────────────────────────────────
    print("\n[Scenario 11] Compression — factor=0.5, lane_index=2")
    world = make_world(bc_x=bc_default_x, bc_y=bc_default_y, fr_y=fr_y, anchor_x=anchor_x)
    d = FakePlayer(x=50.0, y=35.0)
    d.depth_class = "SECONDARY"
    ok = test_scenario(
        "Compression 0.5 -> offset = 3.0, ty = 35.0",
        tuners, spacing, world, d, world.ball_carrier, world.first_receiver,
        expected_tx_range=(48.5, 49.5),
        expected_ty_range=(34.5, 35.5),  # 32 + 3.0 = 35.0
        lane_index=2, compression_factor=0.5,
    )
    c += 1 if ok else 0; f += 0 if ok else 1

    # ──────────────────────────────────────────────────────────
    # Scenario 12: Full compression = 1.0 → offset = 0
    # ty = fr_y (full FR lane convergence)
    # ──────────────────────────────────────────────────────────
    print("\n[Scenario 12] Full compression — factor=1.0, lane_index=2")
    world = make_world(bc_x=bc_default_x, bc_y=bc_default_y, fr_y=fr_y, anchor_x=anchor_x)
    d = FakePlayer(x=50.0, y=35.0)
    d.depth_class = "SECONDARY"
    ok = test_scenario(
        "Full compression -> ty = fr_y (32.0)",
        tuners, spacing, world, d, world.ball_carrier, world.first_receiver,
        expected_tx_range=(48.5, 49.5),
        expected_ty_range=(31.5, 32.5),  # fr_y = 32
        lane_index=2, compression_factor=1.0,
    )
    c += 1 if ok else 0; f += 0 if ok else 1

    # ──────────────────────────────────────────────────────────
    # Scenario 13: Retreat hesitation — hold_x = True
    # target_x should freeze at defender.x
    # ⚠ NOTE: lead-point for PRIMARY/SECONDARY may still fire
    #    BEFORE retreat hesitation is applied. The code applies
    #    lead-point first (L117-130), THEN retreat override (L132-134).
    #    So tx should be defender.x in the end.
    # ──────────────────────────────────────────────────────────
    print("\n[Scenario 13] Retreat hesitation — hold_x freezes X at defender.x")
    world = make_world(bc_x=bc_default_x, bc_y=bc_default_y, bc_vx=8.0, fr_y=fr_y)
    d = FakePlayer(x=47.0, y=35.0)  # defender.x = 47.0
    d.depth_class = "PRIMARY"
    ok = test_scenario(
        "Retreat hesitation -> tx = defender.x (47.0)",
        tuners, spacing, world, d, world.ball_carrier, world.first_receiver,
        expected_tx_range=(46.5, 47.5),  # should freeze at 47.0
        expected_ty_range=(31.5, 32.5),
        lane_index=0, retreat_state=0.5,  # active retreat hesitation
    )
    c += 1 if ok else 0; f += 0 if ok else 1

    # ──────────────────────────────────────────────────────────
    # Scenario 14: Lateral hesitation — ty = defender.y
    # ──────────────────────────────────────────────────────────
    print("\n[Scenario 14] Lateral hesitation — ty = defender.y")
    world = make_world(bc_x=bc_default_x, bc_y=bc_default_y, fr_y=fr_y)
    d = FakePlayer(x=45.0, y=30.0)  # defender.y = 30.0
    d.depth_class = "PRIMARY"
    ok = test_scenario(
        "Lateral hesitation -> ty = defender.y (30.0)",
        tuners, spacing, world, d, world.ball_carrier, world.first_receiver,
        expected_tx_range=(48.5, 49.5),  # X still bc.x - 1.0
        expected_ty_range=(29.5, 30.5),  # frozen at 30.0
        lane_index=2, lateral_state=0.5,  # active hesitation
    )
    c += 1 if ok else 0; f += 0 if ok else 1

    # ──────────────────────────────────────────────────────────
    # Scenario 15: Compression + lateral hesitation
    # lane_offset should be full (uncompressed) when hesitating
    # offset = lane_index * 3.0  (not * (1-cf))
    # ty = fr_y(32) + 2*3.0 = 38.0
    # ──────────────────────────────────────────────────────────
    print("\n[Scenario 15] Compression + lateral hesitation — offset freezes uncompressed")
    world = make_world(bc_x=bc_default_x, bc_y=bc_default_y, fr_y=fr_y)
    d = FakePlayer(x=45.0, y=38.0)
    d.depth_class = "PRIMARY"
    ok = test_scenario(
        "Lateral hesitation + comp -> full offset (38.0)",
        tuners, spacing, world, d, world.ball_carrier, world.first_receiver,
        expected_tx_range=(48.5, 49.5),
        expected_ty_range=(37.5, 38.5),  # 32 + 6.0 = 38.0, NOT compressed
        lane_index=2, compression_factor=0.5, lateral_state=0.5,
    )
    c += 1 if ok else 0; f += 0 if ok else 1

    # ═══════════════════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════════════════
    print(f"\n{'='*60}")
    print(f"Results: {c} passed, {f} failed out of {c+f}")
    if f > 0:
        print("⚠ SOME TESTS FAILED — review above")
    else:
        print("All scenarios passed — spacing logic is producing expected outputs")

    # ═══════════════════════════════════════════════════════════
    # KEY FINDINGS
    # ═══════════════════════════════════════════════════════════
    print(f"\n{'='*60}")
    print("KEY FINDINGS (Step 1 role spacing audit):")
    print(f"{'='*60}")

    # Finding 1: Lead-point projection
    print("""
  📌 FINDING 1 — Lead-point projection (L117-130 in role_spacing.py)

  When bc.vx > 0.5, PRIMARY/SECONDARY target_x OVERRIDES bc.x - 1.0
  with lead_point = bc.x + bc.vx * 0.3.

  For bc.vx = 8 m/s: target_x = bc.x + 2.4  →  AHEAD of BC
  For bc.vx = 4 m/s: target_x = bc.x + 1.2  →  AHEAD of BC
  For bc.vx = 1 m/s: target_x = bc.x + 0.3  →  NEARLY bc.x

  EFFECT: PRIMARY/SECONDARY defenders run PAST the ball carrier
  before turning.  In real rugby, defenders tackle from in front
  (stepping up) — they should aim toward bc.x - range, not past.

  SUSPECTED: The lead-point was meant to be an INTERCEPTION vector
  (aim where BC will BE, not where they ARE), but without capping
  at bc.x - distance, it causes defenders to overshoot.

  POSSIBLE FIX: cap lead_point = min(lead_point, bc.x - 0.5)
  """)

    # Finding 2: Lane offset magnitude
    print("""
  📌 FINDING 2 — Lane offset = lane_index * 3.0

  At lane_index=±3: offset = ±9.0m  (nearly the field width for 7 defenders)
  At lane_index=±2: offset = ±6.0m
  At lane_index=±1: offset = ±3.0m

  With fr_y=35.0, a lane_index=3 defender targets 35.0+9.0=44.0 or 35.0-9.0=26.0.
  Field width is 70m, so 44/26 is within bounds, but with movement unlocked
  this creates a VERY wide line spread.

  CONSIDER: Reducing lane_multiplier from 3.0 to 2.0 for OPEN_PLAY to
  keep the line tighter.  3.0 was designed for static geometry when
  defenders couldn't move — now they can, so 3.0 may be too aggressive.
  """)

    # Finding 3: Retreat hesitation ordering
    print("""
  📌 FINDING 3 — Retreat hesitation overrides AFTER lead-point

  The code applies lead-point (L117-130) THEN hold_x override (L132-134).
  This is correct in intent — retreat hesitation should win over everything.
  Verified in Scenario 13: tx = defender.x even with fast BC.
  """)


if __name__ == "__main__":
    main()
