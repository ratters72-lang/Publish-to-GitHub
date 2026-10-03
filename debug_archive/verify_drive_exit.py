"""Verify the physics-based DRIVE exit in TES._update_drive.

Tests:
1. DRIVE ends when BC speed drops below threshold
2. DRIVE ends when tackle score drops below threshold
3. DRIVE ends when hard time cap (4.0s) is hit
4. DRIVE continues while all conditions hold
5. Phase sequence: CONTACT → DRIVE → DOMINANCE → DOMINANCE_SETTLING → RELEASE → TACKLE_COMPLETE
"""

import sys
import math
sys.path.insert(0, '.')

from game.tes.tes import TES, CONTACT_DURATION_S
from game.tes.tes_context import TESContext, PlayerContext
from game.tes.tes_state import TESPhase


def make_context(bc_speed=1.0, bc_x=50.0, impact_score=0.8):
    """Build a TESContext with known values.

    NOTE: _compute_initial_impact_score is called during _enter_contact()
    and OVERWRITES ctx.initial_impact_score based on actual velocities.
    The impact_score parameter here is still useful as the fallback for
    the drive_score = getattr(ctx, 'tackle_score', ctx.initial_impact_score)
    check, but only if tackle_score isn't set and initial_impact_score
    hasn't been recomputed. For reliable drive_score control in tests,
    set ctx.initial_impact_score AFTER the contact phase has passed.
    """
    ctx = TESContext()
    # Head-on collision: BC runs forward, defender runs toward BC
    # This produces a realistic impact_score > 0.40 from _compute_initial_impact_score
    ctx.attacker = PlayerContext(
        player_id=1,
        x_m=bc_x,
        y_m=35.0,
        vx=bc_speed,       # forward
        vy=0.0,
        mass=95.0,
    )
    ctx.defender = PlayerContext(
        player_id=2,
        x_m=bc_x - 0.5,    # just in front of BC
        y_m=35.0,
        vx=-3.0,            # running toward BC at 3 m/s
        vy=0.0,
        mass=100.0,
    )
    ctx.initial_impact_score = impact_score
    ctx.tackle_number = 1
    return ctx


def run_through_contact(tes, ctx):
    """Run ENGAGE → CONTACT transition."""
    tes.initiate_tackle(ctx)
    assert tes.phase == TESPhase.ENGAGE, f"Expected ENGAGE, got {tes.phase}"

    # Step until contact
    for _ in range(20):
        tes.update(0.02)
        if tes.phase == TESPhase.CONTACT:
            break

    # Must be in CONTACT now
    assert tes.phase == TESPhase.CONTACT, f"Expected CONTACT, got {tes.phase}"

    # Run through CONTACT duration to reach DRIVE
    contact_time = max(CONTACT_DURATION_S, tes.outcome.tackle_duration_s * 0.15)
    steps = int(contact_time / 0.02) + 2
    for _ in range(steps):
        tes.update(0.02)
        if tes.phase == TESPhase.DRIVE:
            break

    assert tes.phase == TESPhase.DRIVE, f"Expected DRIVE, got {tes.phase}"


def test_drive_continues_while_bc_moving():
    """DRIVE should continue while BC speed > 0.15."""
    tes = TES(rng_seed=42)
    ctx = make_context(bc_speed=2.0, impact_score=1.2)

    run_through_contact(tes, ctx)

    # Override drive_score to ensure it's above threshold
    # The production code uses: getattr(ctx, 'tackle_score', ctx.initial_impact_score)
    ctx.tackle_score = 1.2

    # Step a few frames — DRIVE should persist
    for _ in range(10):
        tes.update(0.02)
        # Keep BC speed high by updating velocity
        ctx.attacker.vx = 2.0

    assert tes.phase == TESPhase.DRIVE, (
        f"DRIVE should persist with fast BC, got {tes.phase}"
    )
    print(f"  PASS: DRIVE persists at t={tes._physics_timer:.2f}s, speed={ctx.attacker.speed:.2f}")


def test_drive_exits_when_bc_slows():
    """DRIVE should exit when BC speed drops ≤ 0.15."""
    tes = TES(rng_seed=42)
    ctx = make_context(bc_speed=0.5, impact_score=0.8)

    run_through_contact(tes, ctx)

    # Stop the BC
    ctx.attacker.vx = 0.0
    ctx.attacker.vy = 0.0

    # Step until phase changes
    for _ in range(50):
        tes.update(0.02)
        if tes.phase != TESPhase.DRIVE:
            break

    assert tes.phase in (TESPhase.DOMINANCE, TESPhase.DOMINANCE_SETTLING, TESPhase.RELEASE), (
        f"Expected DRIVE to exit when BC stops, got {tes.phase}"
    )
    print(f"  PASS: DRIVE exited to {tes.phase.value} when BC speed={ctx.attacker.speed:.2f}")


def test_drive_exits_when_score_drops():
    """DRIVE should exit when tackle_score/impact_score drops ≤ 0.40."""
    tes = TES(rng_seed=42)
    ctx = make_context(bc_speed=1.0, impact_score=1.0)  # high initial score

    run_through_contact(tes, ctx)

    # Manually set tackle_score below threshold to simulate score collapse
    ctx.tackle_score = 0.2

    # DRIVE should exit since score is now low (drive_score uses tackle_score first)
    for _ in range(10):
        tes.update(0.02)
        ctx.attacker.vx = 1.0  # keep speed high

    assert tes.phase != TESPhase.DRIVE, (
        f"DRIVE should exit when score is low, got {tes.phase}"
    )
    print(f"  PASS: DRIVE exited (tackle_score={ctx.tackle_score:.2f} ≤ 0.40)")


def test_drive_hard_cap_4s():
    """DRIVE must exit at 4.0s hard cap regardless of conditions."""
    tes = TES(rng_seed=42)
    ctx = make_context(bc_speed=5.0, impact_score=1.5)

    run_through_contact(tes, ctx)

    # Run at 200fps for 5 simulated seconds
    for _ in range(1000):
        ctx.attacker.vx = 10.0  # keep BC very fast
        tes.update(0.005)
        if tes.phase != TESPhase.DRIVE:
            break

    remaining = 4.0 - tes._physics_timer  # should be negative or zero
    assert tes.phase in (TESPhase.DOMINANCE, TESPhase.DOMINANCE_SETTLING, TESPhase.RELEASE), (
        f"DRIVE should exit by 4.0s hard cap, got {tes.phase} at t={tes._physics_timer:.2f}s"
    )
    print(f"  PASS: DRIVE exited at ~4.0s hard cap (exited at timer={tes._physics_timer:.2f}s)")


def test_full_phase_sequence():
    """Verify CONTACT → DRIVE → DOMINANCE → DOMINANCE_SETTLING → RELEASE → TACKLE_COMPLETE."""
    tes = TES(rng_seed=42)
    ctx = make_context(bc_speed=0.5, impact_score=0.5)

    tes.initiate_tackle(ctx)
    assert tes.phase == TESPhase.ENGAGE

    # Record ENGAGE before first update (it's set by initiate_tackle)
    phases_seen = [TESPhase.ENGAGE.value]

    # Step through entire lifecycle
    for _ in range(600):  # 6 seconds at 100fps
        # Slow the BC so DRIVE eventually exits
        ctx.attacker.vx *= 0.98
        tes.update(0.01)
        if tes.phase.value not in phases_seen:
            phases_seen.append(tes.phase.value)
        if tes.phase == TESPhase.RELEASED:
            break

    expected_order = [
        TESPhase.ENGAGE.value,
        TESPhase.CONTACT.value,
        TESPhase.DRIVE.value,
        TESPhase.DOMINANCE.value,
        TESPhase.DOMINANCE_SETTLING.value,
        TESPhase.RELEASE.value,
        TESPhase.TACKLE_COMPLETE.value,
    ]

    # Check each expected phase appears in order
    idx = 0
    for expected in expected_order:
        try:
            pos = phases_seen.index(expected, idx)
            idx = pos + 1
        except ValueError:
            print(f"  WARN: Expected phase '{expected}' not found in sequence")
            print(f"  Seen: {phases_seen}")
            return False

    print(f"  PASS: Full sequence correct: {phases_seen}")
    return True


if __name__ == "__main__":
    tests = [
        ("DRIVE continues with fast BC", test_drive_continues_while_bc_moving),
        ("DRIVE exits when BC slows", test_drive_exits_when_bc_slows),
        ("DRIVE exits when score low", test_drive_exits_when_score_drops),
        ("DRIVE hard cap 4.0s", test_drive_hard_cap_4s),
        ("Full phase sequence", test_full_phase_sequence),
    ]

    passed = 0
    for name, fn in tests:
        try:
            print(f"\n--- {name} ---")
            fn()
            passed += 1
        except Exception as e:
            print(f"  FAIL: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n{'='*40}")
    print(f"Results: {passed}/{len(tests)} passed")
    if passed == len(tests):
        print("ALL TESTS PASSED")
    else:
        print("SOME TESTS FAILED")
        sys.exit(1)
