"""verify_ruck_pin_role_aware.py — Focused verifier for the ruck-pin jitter fix.

Reproduces the mechanism behind the captured log (RELEASE-era ruck-pin
flip-flop + wrong A-pivot):

  OLD behaviour (the bug):
    - slot1 = physically-nearest body per side (could be a WINGER/CENTRE).
    - slot2 target clamped to within 3.5 m of the A-lane, dragging a wide
      chain anchor inward.
    - slot1.y teleported (SNAP) to the A-anchor every frame during RELEASE.

  NEW behaviour (this fix):
    1. slot1 prefers the genuine A-pivot (depth_role == "A"); falls back to
       physical-nearest only when no A exists.
    2. slot2 prefers an interior body (B/C/D) over a wide anchor, and the
       3.5 m clamp yields when the spacing chain explicitly targets slot2
       beyond the bound (the chain is the Y authority for wide anchors).
    3. During RELEASE/PTB/POST_RUCK the position IS force-snapped (writing
       position, not just target) so the pin owns convergence; HOLD is
       cleared immediately so the body cannot sit frozen on its lane.

Run:  python verify_ruck_pin_role_aware.py
Exit 0 on success; prints a PASS/FAIL line per assertion.
"""
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player
from world.world_state import WorldState, GameSubState
from game.defence.defence_lvl_1.ruck_adjacent_pin import (
    select_first_two_defenders,
    enforce_ruck_adjacent_pin,
    LEFT,
    RIGHT,
    RUCK_PIN_SLOT2_MIN_GAP_M,
    RUCK_PIN_GLIDE_SPEED_MPS,
    ruck_adjacent_anchor,
)
from game.defence.defence_lvl_1.defensive_split_spacing import (
    _side_inside_out,
)


failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" - " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def make_defender(pid, x, y, depth_role=None, defender_role=None,
                  fullback=False, tackle_state=False, frozen=False):
    d = Player(x=x, y=y, player_id=pid)
    d.depth_role = depth_role
    d.defender_role = defender_role
    d.fullback_role_active = fullback
    d.tackle_state_active = tackle_state
    d.frozen = frozen
    d.freeze_timer = 0.0
    d.target_x = x
    d.target_y = y
    d.vx = 0.0
    d.vy = 0.0
    return d


def make_world(ruck_y, defenders, frame_spacing_targets=None, phase_label=""):
    w = WorldState()
    w.ruck_y_m = ruck_y
    w.ruck_x_m = 50.0
    w.defending_team = defenders
    w.frame_spacing_targets = frame_spacing_targets or {}
    w._ope_phase_label = phase_label
    w.sub_state = GameSubState.OPEN_PLAY
    w.tackler_ids = set()
    w.frame_count = 0
    return w


def main():
    print("=" * 64)
    print("RUCK-PIN ROLE-AWARE FIX VERIFIER (2026-09-02)")
    print("=" * 64)

    ruck_y = 32.86
    anchor_left = ruck_adjacent_anchor(ruck_y, LEFT)   # 30.86
    anchor_right = ruck_adjacent_anchor(ruck_y, RIGHT)  # 34.86
    dt = 1.0 / 60.0
    max_step = RUCK_PIN_GLIDE_SPEED_MPS * dt  # 6.0 * (1/60) = 0.1

    # ── TEST 1: slot1 prefers the A-pivot over a physically-nearer WINGER ──
    # The WINGER id=2 is physically NEAREST (y=32.0 -> dist 0.86) but a
    # genuine A-pivot id=1 exists (y=30.86 -> dist 2.0, depth_role="A").
    # OLD code would pick id=2 (WINGER) as slot1 and pin the WINGER to the
    # A-lane (the wrong-position defect).  NEW code must prefer id=1.
    print("\n[1] slot1 prefers genuine A-pivot over physically-nearer WINGER")
    a_pivot = make_defender(1, x=50.0, y=30.86, depth_role="A")
    winger_near = make_defender(2, x=50.0, y=32.0, depth_role="WINGER")
    w = make_world(ruck_y, [a_pivot, winger_near])
    picks = select_first_two_defenders(w)
    left_slot1, left_slot2 = picks[LEFT]
    check("1.1 LEFT slot1 is the A-pivot (id=1), not the WINGER (id=2)",
          left_slot1 is a_pivot,
          "slot1 id=%s role=%s" % (
              getattr(left_slot1, "player_id", None) if left_slot1 else None,
              getattr(left_slot1, "depth_role", None) if left_slot1 else None))
    check("1.2 LEFT slot2 is the WINGER (id=2) (no interior body on side)",
          left_slot2 is winger_near,
          "slot2 id=%s" % (getattr(left_slot2, "player_id", None) if left_slot2 else None))

    # ── TEST 2: slot2 clamp yields to the spacing chain (wide WINGER goal) ──
    # id=2 is slot2 (WINGER).  The spacing chain targets id=2 WIDE (y=10,
    # far beyond the 3.5m bound from anchor 30.86).  OLD code clamped slot2
    # target back to anchor-3.5=27.36 (dragging the WINGER inward).  NEW code
    # must yield: the sliding chain target (10.0) is preserved, no clamp.
    print("\n[2] slot2 clamp yields to a wide spacing-chain target")
    a_pivot = make_defender(1, x=50.0, y=30.86, depth_role="A")
    winger = make_defender(2, x=50.0, y=30.0, depth_role="WINGER")
    winger.target_y = 10.0   # spacing chain wants the WINGER at y=10 (wide)
    spacing = {id(a_pivot): anchor_left, id(winger): 10.0}
    w = make_world(ruck_y, [a_pivot, winger], frame_spacing_targets=spacing,
                   phase_label="TACKLE_RELEASE")
    enforce_ruck_adjacent_pin(w)
    check("2.1 WINGER slot2 target_y preserved (no inward clamp)",
          abs(winger.target_y - 10.0) < 1e-6,
          "target_y=%.2f (expected 10.0)" % winger.target_y)
    check("2.2 WINGER slot2 position NOT teleported inward",
          abs(winger.y - 30.0) < 1e-6,
          "y=%.2f (expected 30.0)" % winger.y)

    # ── TEST 3: slot2 clamp still guards NON-spacing drift (no dict) ──
    # When the spacing chain does NOT supply a wide target for slot2, the
    # 3.5 m max-gap clamp must still fire to contain organic drift.  This is
    # exercised in a REFORM phase (TACKLE_DRIVE) — in OPEN_PLAY the pin now
    # writes no Y at all (spacing-owns-Y), so we deliberately use a phase
    # where the pin is active to isolate the clamp.
    print("\n[3] slot2 clamp still guards non-spacing drift (no wide dict)")
    a_pivot3 = make_defender(11, x=50.0, y=30.86, depth_role="A")
    drifter3 = make_defender(12, x=50.0, y=25.0, depth_role="B")  # 5.86m from anchor
    drifter3.target_y = 25.0
    spacing3 = {id(a_pivot3): anchor_left, id(drifter3): 25.0}
    # TACKLE_DRIVE is now target-only (2026-09-07): the pin clamps the
    # slot-2 Y TARGET to the band every frame but does NOT teleport the
    # position — the depth-locked line glides under the drive-depth-track.
    # Assert the guard is the TARGET clamp, and that the position was not
    # surgically moved (no teleport).
    w3 = make_world(ruck_y, [a_pivot3, drifter3],
                    frame_spacing_targets=spacing3, phase_label="TACKLE_DRIVE")
    # To isolate the "no wide dict" guard, clear the spacing dict so the pin
    # sees no spacing target for the drifter (falls back to the clamp).
    w3.frame_spacing_targets = {}
    before3 = drifter3.y
    enforce_ruck_adjacent_pin(w3)
    check("3.1 no-dict drifter TARGET clamped to the bound (27.36)",
          abs(drifter3.target_y - (anchor_left - 3.5)) < 1e-6,
          "target_y=%.2f (expected %.2f)" % (drifter3.target_y, anchor_left - 3.5))
    check("3.2 no-dict drifter POSITION not teleported (target-only drive)",
          abs(drifter3.y - before3) < 1e-6,
          "y=%.2f moved=%.4f (expected 0.0 in target-only)" % (drifter3.y, abs(drifter3.y - before3)))

    # ── TEST 4: position SNAP FIRES during RELEASE (force-snap, 2026-09-03) ──
    # In the reform window the pin must OWN the A-pivot by writing position
    # (not just target) so it converges on the anchor instead of being parked
    # short by the movement integrator.  The teleport/jitter trade-off is
    # intentional — the pin owns convergence.
    print("\n[4] position SNAP fires during RELEASE (force-snap)")
    a_pivot4 = make_defender(21, x=50.0, y=anchor_left - 0.2, depth_role="A")  # within snap threshold
    w4 = make_world(ruck_y, [a_pivot4], phase_label="TACKLE_RELEASE")
    enforce_ruck_adjacent_pin(w4)
    check("4.1 RELEASE: A-pivot target_y pinned to anchor",
          abs(a_pivot4.target_y - anchor_left) < 1e-6,
          "target_y=%.2f (expected %.2f)" % (a_pivot4.target_y, anchor_left))
    check("4.2 RELEASE: A-pivot position SNAPPED to anchor (within threshold)",
          abs(a_pivot4.y - anchor_left) < 1e-6,
          "y=%.2f (expected %.2f — position written by pin)" % (a_pivot4.y, anchor_left))

    # ── TEST 5: OPEN_PLAY pin writes NO Y (spacing chain owns Y) ──
    # In OPEN_PLAY the ruck-adjacent pin must be a pure no-op for Y: it does
    # not pin slot1.target_y to the anchor, does not snap slot1.y, and does
    # not clamp slot2.  The split-spacing chain (frame_spacing_targets) is the
    # SOLE Y authority — writing Y here was the residual jitter source.
    print("\n[5] OPEN_PLAY pin writes NO Y (spacing chain owns Y)")
    a_pivot5 = make_defender(31, x=50.0, y=27.0, depth_role="A")
    a_pivot5.target_y = 40.0   # a pre-existing spacing-chain target
    w5 = make_world(ruck_y, [a_pivot5], phase_label="OPEN_PLAY")
    enforce_ruck_adjacent_pin(w5)
    check("5.1 OPEN_PLAY: slot1 target_y NOT overwritten by pin",
          abs(a_pivot5.target_y - 40.0) < 1e-6,
          "target_y=%.2f (expected 40.0 — spacing chain owns Y)" % a_pivot5.target_y)
    check("5.2 OPEN_PLAY: slot1 position NOT snapped to anchor",
          abs(a_pivot5.y - 27.0) < 1e-6,
          "y=%.2f (expected 27.0 — pin writes no Y)" % a_pivot5.y)

    # ── TEST 6: role-aware slot0 (WINGER anchor) in _side_inside_out ──
    # A real WINGER-role defender exists but is NOT the physically-widest
    # body (a D drifted wide during RELEASE/PTB).  slot0 must be the real
    # WINGER, never the wide-drifting D — otherwise the side is "stretched
    # too far" (the D gets placed at the half-width WINGER anchor).
    print("\n[6] _side_inside_out: real WINGER wins slot0 over a wide-drifting D")
    winger6 = make_defender(41, x=50.0, y=26.0, depth_role="WINGER")  # dist 6.86
    drifter6 = make_defender(42, x=50.0, y=10.0, depth_role="D")      # dist 22.86 (widest)
    interior6 = make_defender(43, x=50.0, y=30.86, depth_role="A")    # dist 2.0
    ordered6 = _side_inside_out([drifter6, winger6, interior6], LEFT, ruck_y)
    check("6.1 slot0 is the real WINGER (id=41), not the wide-drifting D (id=42)",
          ordered6[0] is winger6,
          "slot0 id=%s role=%s" % (
              getattr(ordered6[0], "player_id", None) if ordered6 else None,
              getattr(ordered6[0], "depth_role", None) if ordered6 else None))
    check("6.2 wide-drifting D is an interior slot (slot1), not the WINGER anchor",
          ordered6[1] is drifter6,
          "D id=%s index=%d" % (
              getattr(drifter6, "player_id", None),
              ordered6.index(drifter6) if drifter6 in ordered6 else -1))

    # ── TEST 7: no A-pivot → non-A fallback is NOT snapped (2026-09-07) ──
    # When the genuine A-pivot is excluded (marker/tackler during the reform
    # window), _pick_a_pivot falls back to the physically-nearest body, which
    # can be an outer-role (B/C/WINGER/CENTRE).  The A-lane snap must be
    # role-gated to depth_role == "A"; a non-A fallback keeps its organic
    # y/target_y (no A-lane teleport) — the marker/tackler owns the ruck.
    print("\n[7] no A-pivot: non-A fallback is NOT snapped to the A-lane")
    c_body7 = make_defender(71, x=50.0, y=27.5, depth_role="C")
    c_body7.target_y = 27.5
    b_body7 = make_defender(72, x=50.0, y=26.0, depth_role="B")
    b_body7.target_y = 26.0
    w7 = make_world(ruck_y, [c_body7, b_body7], phase_label="TACKLE_DRIVE")
    pick7 = select_first_two_defenders(w7)
    left_s1, _ = pick7[LEFT]
    check("7.1 LEFT slot1 is the C-role fallback (no A present)",
          left_s1 is c_body7,
          "slot1 id=%s role=%s" % (
              getattr(left_s1, "player_id", None) if left_s1 else None,
              getattr(left_s1, "depth_role", None) if left_s1 else None))
    enforce_ruck_adjacent_pin(w7)
    check("7.2 C-role fallback y NOT teleported to A-lane (kept organic)",
          abs(c_body7.y - 27.5) < 1e-6,
          "y=%.2f (expected 27.5)" % c_body7.y)
    check("7.3 C-role fallback target_y NOT overwritten to A-lane",
          abs(c_body7.target_y - 27.5) < 1e-6,
          "target_y=%.2f (expected 27.5)" % c_body7.target_y)

    # ── TEST 8: slot-2 MIN-GAP floor — collapsed inward slot-2 pushed OUT ──
    # A slot-2 that drifts INWARD toward the A-pivot (gap -> 0, collapsing /
    # stacking onto it, or sitting between the pivot and the ruck) must be
    # pushed back OUT to at least RUCK_PIN_SLOT2_MIN_GAP_M from the anchor.
    # The old max-gap-only clamp left a collapsed slot-2 untouched because
    # |y - anchor| < MAX_GAP.
    print("\n[8] slot-2 MIN-GAP floor: collapsed slot-2 pushed OUT")
    a_pivot8 = make_defender(81, x=50.0, y=30.86, depth_role="A")
    collapsed8 = make_defender(82, x=50.0, y=30.40, depth_role="B")  # 0.46m from anchor
    collapsed8.target_y = 30.40
    w8 = make_world(ruck_y, [a_pivot8, collapsed8], phase_label="TACKLE_RELEASE")
    before8 = collapsed8.y
    enforce_ruck_adjacent_pin(w8)
    expected_min = anchor_left - RUCK_PIN_SLOT2_MIN_GAP_M  # 29.86
    check("8.1 collapsed slot-2 position glided toward MIN bound (no teleport)",
          0.0 < abs(collapsed8.y - before8) <= max_step + 1e-6 and collapsed8.y < before8,
          "y=%.2f moved=%.4f (cap %.4f)" % (collapsed8.y, abs(collapsed8.y - before8), max_step))
    check("8.2 collapsed slot-2 target synced to the MIN bound",
          abs(collapsed8.target_y - expected_min) < 1e-6,
          "target_y=%.2f (expected %.2f)" % (collapsed8.target_y, expected_min))

    # ── TEST 9: symmetric side-sign band — RIGHT slot-2 never crosses ruck ──
    # The side-sign band lives ONLY on the outward side of the A-pivot.  A
    # RIGHT slot-2 collapsed INWARD is pushed OUT to anchor + MIN_GAP (still
    # on the RIGHT of the ruck), never clamped to the LEFT half.  This mirrors
    # test 8 for the RIGHT side (symmetric side-sign fix, 2026-09-07).
    print("\n[9] symmetric side-sign band: RIGHT slot-2 pushed OUT (no cross-ruck)")
    a_pivot9 = make_defender(91, x=50.0, y=34.86, depth_role="A")
    collapsed9 = make_defender(92, x=50.0, y=34.50, depth_role="B")  # 0.36m from anchor
    collapsed9.target_y = 34.50
    w9 = make_world(ruck_y, [a_pivot9, collapsed9], phase_label="TACKLE_RELEASE")
    before9 = collapsed9.y
    enforce_ruck_adjacent_pin(w9)
    expected_right = anchor_right + RUCK_PIN_SLOT2_MIN_GAP_M  # 35.86
    check("9.1 RIGHT collapsed slot-2 position glided toward MIN bound (no teleport)",
          0.0 < abs(collapsed9.y - before9) <= max_step + 1e-6 and collapsed9.y > before9,
          "y=%.2f moved=%.4f (cap %.4f)" % (collapsed9.y, abs(collapsed9.y - before9), max_step))
    check("9.2 RIGHT slot-2 stays on the RIGHT side (no cross-ruck)",
          collapsed9.y > ruck_y,
          "y=%.2f (must be > ruck_y %.2f)" % (collapsed9.y, ruck_y))

    # ── TEST 10: role-gated spacing yield — only a WINGER yields ──
    # A CENTRE slot-2 with a wide spacing-chain target is CLAMPED to the band
    # (it is not the winger), preventing the "outside defenders push out away
    # from tes except for winger" drift.  (The WINGER yield is test 2.)
    print("\n[10] role-gated yield: CENTRE slot-2 with wide chain target is CLAMPED")
    a_pivot10 = make_defender(101, x=50.0, y=30.86, depth_role="A")
    centre10 = make_defender(102, x=50.0, y=30.0, depth_role="CENTRE")
    centre10.target_y = 10.0   # chain wants it beyond the band
    spacing10 = {id(a_pivot10): anchor_left, id(centre10): 10.0}
    w10 = make_world(ruck_y, [a_pivot10, centre10],
                     frame_spacing_targets=spacing10, phase_label="TACKLE_RELEASE")
    before10 = centre10.y
    enforce_ruck_adjacent_pin(w10)
    expected_clamp = anchor_left - RUCK_PIN_SLOT2_MIN_GAP_M  # 29.86 (band_hi)
    check("10.1 CENTRE slot-2 with wide target clamped toward the band (no wide drift)",
          0.0 < abs(centre10.y - before10) <= max_step + 1e-6 and centre10.y < before10,
          "y=%.2f moved=%.4f (cap %.4f)" % (centre10.y, abs(centre10.y - before10), max_step))
    check("10.2 CENTRE slot-2 target synced to the band (not the wide 10.0)",
          abs(centre10.target_y - expected_clamp) < 1e-6,
          "target_y=%.2f (expected %.2f)" % (centre10.target_y, expected_clamp))

    print("\n" + "=" * 64)
    if failures:
        print("RESULT: %d FAILURE(S): %s" % (len(failures), failures))
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
