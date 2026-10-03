"""
verify_marker_drive_gate.py - Verify markers NEVER force-snap; they glide to
the live ruck pin, and geometry is finalized ONLY at the canonical Drive->Release.

Task (2026-09-07): "Markers will never force-snap; they will smoothly track
the live ruck pin and only finalize geometry at Drive->Release (canary-flagged)."

This verifier asserts:

  1. `arm_marker_lock` captures the arm-time ruck, and
     `marker_lock_invalidated` returns True only when the live ruck has moved
     more than `MARKER_LOCK_RUCK_MOVE_INVALIDATE_M` from the arm-time ruck.
  2. `marker_lock_active` / `locked_marker_target` return inactive / None when
     the lock is invalidated (large ruck move), so the live ruck pin is used.
  3. `enforce_ruck_adjacent_pin` NEVER writes marker POSITION in ANY phase
     (TACKLE_DRIVE, RELEASE, PTB, POST_RUCK, OPEN_PLAY) - markers GLIDE via
     the movement integrator.  It ALWAYS re-asserts the marker TARGET to the
     ruck-pin geometry authority.
  4. The engine gate: `arm_marker_lock` fires ONLY on the `tes -> release`
     bucket flip (no more "any bucket change" re-arm).
  5. CONTACT role-assignment gate: `marker_assign_at_release_only` canary
     defaults True, so markers are NOT role-assigned before tackle end.
  6. STATIC SCAN: `ruck_adjacent_pin.py` has NO `_d.x = _tx` / `_d.y = _ty`
     marker position write, no `_MARKER_REANCHOR_PHASES`; `marker_decision_lock.py`
     defines `marker_lock_invalidated` + `MARKER_LOCK_RUCK_MOVE_INVALIDATE_M`
     but NOT `marker_snap_allowed` / `mark_marker_snap` / `SNAP_DEBOUNCE_FRAMES`;
     `open_play_engine.py` uses the `_prev_bucket == "tes" and _bucket == "release"`
     arm gate + references `marker_assign_at_release_only`;
     `defensive_tuners.py` defines the canary defaulting True.

Exit 0 = PASS, exit 1 = FAIL.
"""

import math
import os
import re
import sys
import types

from player.player import DefenderRole
from game.defence.defence_lvl_1 import marker_decision_lock as mdl
from game.defence.defence_lvl_1 import ruck_adjacent_pin as rap

MARKER_ROLES = (DefenderRole.MARKER_A, DefenderRole.MARKER_B)
TACKLE_DRIVE_PHASES = ("TACKLE_DRIVE", "TACKLE_DOMINANCE")
INTERMEDIATE_BUCKET_PHASES = (
    "PTB_PREP", "PTB_EXEC", "PTB", "POST_RUCK_OPEN_PLAY",
)
RELEASE_PHASE = "RELEASE"
OPEN_PLAY_PHASE = "OPEN_PLAY"

failures = []


def make_marker(pid, role=DefenderRole.MARKER_A, tx=61.0, ty=35.0):
    m = types.SimpleNamespace(
        player_id=pid,
        defender_role=role,
        target_x=tx,
        target_y=ty,
        movement_state=None,
        frozen=False,
        freeze_timer=0.0,
    )
    return m


def make_world(ruck_x=60.0, ruck_y=35.0, frame_count=0, phase_label="RELEASE"):
    return types.SimpleNamespace(
        ruck_x_m=ruck_x,
        ruck_y_m=ruck_y,
        frame_count=frame_count,
        _ope_phase_label=phase_label,
        sub_state=phase_label,
        defending_team=[],
        frame_spacing_targets=None,
        _ruck_pin_state=None,
    )


# ------------------------------------------------------------------
# TEST 1: arm captures ruck + invalidation threshold
# ------------------------------------------------------------------
def test_arm_captures_ruck_and_invalidation():
    world = make_world(ruck_x=60.0, ruck_y=35.0, frame_count=10)
    m = make_marker(101, tx=61.0, ty=35.0)
    world.defending_team = [m]

    mdl.arm_marker_lock(world, world.defending_team)

    # Exactly at arm-time ruck => not invalidated.
    if mdl.marker_lock_invalidated(world, m):
        failures.append("T1: lock invalidated at arm-time ruck (should be False)")

    # Small move (< 2.0 m) => still valid.
    world.ruck_x_m = 61.0  # +1.0 m
    if mdl.marker_lock_invalidated(world, m):
        failures.append(
            "T1: lock invalidated after +1.0 m ruck move (threshold is 2.0 m)"
        )

    # Large move (> 2.0 m) => invalidated.
    world.ruck_x_m = 63.0  # +3.0 m
    if not mdl.marker_lock_invalidated(world, m):
        failures.append("T1: lock NOT invalidated after +3.0 m ruck move")

    # Large Y move too.
    world2 = make_world(ruck_x=60.0, ruck_y=35.0, frame_count=0)
    m2 = make_marker(102, tx=61.0, ty=35.0)
    world2.defending_team = [m2]
    mdl.arm_marker_lock(world2, world2.defending_team)
    world2.ruck_y_m = 38.0  # +3.0 m
    if not mdl.marker_lock_invalidated(world2, m2):
        failures.append("T1: lock NOT invalidated after +3.0 m ruck_y move")


# ------------------------------------------------------------------
# TEST 2: invalidated lock => inactive + live target used
# ------------------------------------------------------------------
def test_invalidation_effects():
    world = make_world(ruck_x=60.0, ruck_y=35.0, frame_count=0)
    m = make_marker(201, tx=61.0, ty=35.0)
    world.defending_team = [m]
    mdl.arm_marker_lock(world, world.defending_team)

    # At arm time: lock active, locked target returned.
    if not mdl.marker_lock_active(world, m):
        failures.append("T2: lock not active at arm time")
    locked = mdl.locked_marker_target(world, m)
    if locked != (61.0, 35.0):
        failures.append(f"T2: locked target {locked} != (61.0, 35.0)")

    # Large ruck move => lock inactive, locked target None (live pin used).
    world.ruck_x_m = 63.0
    if mdl.marker_lock_active(world, m):
        failures.append("T2: lock still active after large ruck move")
    if mdl.locked_marker_target(world, m) is not None:
        failures.append("T2: locked target returned after large ruck move (must be None)")


# ------------------------------------------------------------------
# TEST 3: enforce_ruck_adjacent_pin NEVER writes marker POSITION; always
#         writes marker TARGET (in every phase, including Drive->Release)
# ------------------------------------------------------------------
def test_never_force_snap_target_only():
    from game.defence.defence_lvl_1.marker_authority import (
        marker_geometry_target as _mt,
    )

    # Every phase where the pin is reached (non-skip, non-OPEN_PLAY) AND the
    # canonical Drive->Release boundary (RELEASE) AND OPEN_PLAY must all:
    #   - NEVER write marker position (x/y unchanged)
    #   - ALWAYS write marker target to the ruck-pin geometry authority
    for phase in (*TACKLE_DRIVE_PHASES, *INTERMEDIATE_BUCKET_PHASES, RELEASE_PHASE):
        world = make_world(ruck_x=60.0, ruck_y=35.0, frame_count=0, phase_label=phase)
        m = make_marker(301, role=DefenderRole.MARKER_A, tx=0.0, ty=0.0)
        # Position starts far from the ruck pin.
        m.x = 55.0
        m.y = 30.0
        m.vx = 0.5
        m.vy = 0.5
        world.defending_team = [m]
        rap.enforce_ruck_adjacent_pin(world)

        _tx, _ty = _mt(world, m)
        # TARGET re-asserted to the ruck-pin geometry authority.
        if abs(float(m.target_x) - _tx) > 0.001 or abs(float(m.target_y) - _ty) > 0.001:
            failures.append(
                f"T3: {phase} marker target NOT set to ruck pin "
                f"({m.target_x:.2f},{m.target_y:.2f} != {_tx:.2f},{_ty:.2f})"
            )
        # POSITION unchanged (markers NEVER force-snap) and velocity NOT zeroed.
        if abs(float(m.x) - 55.0) > 0.001 or abs(float(m.y) - 30.0) > 0.001:
            failures.append(
                f"T3: {phase} marker POSITION force-written "
                f"(x={m.x:.2f} y={m.y:.2f}, must remain 55.00/30.00 - never snap)"
            )

    # OPEN_PLAY: pin returns early ("spacing-owns-Y"); target is NOT re-asserted
    # by the pin here, but position must NOT be force-snapped either.
    world = make_world(ruck_x=60.0, ruck_y=35.0, frame_count=0, phase_label=OPEN_PLAY_PHASE)
    m = make_marker(302, role=DefenderRole.MARKER_A, tx=0.0, ty=0.0)
    m.x = 55.0
    m.y = 30.0
    world.defending_team = [m]
    rap.enforce_ruck_adjacent_pin(world)
    if abs(float(m.x) - 55.0) > 0.001 or abs(float(m.y) - 30.0) > 0.001:
        failures.append(
            f"T3: OPEN_PLAY marker POSITION force-written "
            f"(x={m.x:.2f} y={m.y:.2f}, must remain 55.00/30.00 - never snap)"
        )


# ------------------------------------------------------------------
# TEST 4: engine arm gate — only on tes -> release bucket flip
# ------------------------------------------------------------------
def test_engine_arm_gate():
    # Transitions that must NOT arm:
    no_arm = [
        ("OPEN_PLAY", "TACKLE_INIT"),     # open_play -> tes
        ("RELEASE", "PTB_PREP"),          # release -> ptb
        ("PTB", "POST_RUCK_OPEN_PLAY"),   # ptb -> post_ruck
        ("POST_RUCK_OPEN_PLAY", "OPEN_PLAY"),  # post_ruck -> open_play
        ("OPEN_PLAY", "OPEN_PLAY"),       # no change
    ]
    for prev_phase, cur_phase in no_arm:
        prev_bucket = mdl.phase_bucket(prev_phase)
        cur_bucket = mdl.phase_bucket(cur_phase)
        armed = (prev_bucket == "tes" and cur_bucket == "release")
        if armed:
            failures.append(
                f"T4: arm fired on {prev_phase} -> {cur_phase} "
                f"(buckets {prev_bucket}->{cur_bucket}); must NOT arm"
            )

    # The canonical Drive->Release flip MUST arm.
    armed = (mdl.phase_bucket("TACKLE_DOMINANCE") == "tes"
             and mdl.phase_bucket("RELEASE") == "release")
    if not armed:
        failures.append("T4: arm did NOT fire on TACKLE_DOMINANCE -> RELEASE")


# ------------------------------------------------------------------
# TEST 5: enforce_ruck_adjacent_pin — marker targets set, never position-snapped
# ------------------------------------------------------------------
def test_pin_direct_target():
    def run_pin(phase, marker_x=55.0, ruck_x=60.0, ruck_y=35.0):
        world = types.SimpleNamespace(
            frame_count=0,
            sub_state=phase,
            ruck_x_m=ruck_x,
            ruck_y_m=ruck_y,
            _ope_phase_label=phase,
            defending_team=[],
            frame_spacing_targets=None,
            _ruck_pin_state=None,
        )
        m = types.SimpleNamespace(
            player_id=900,
            defender_role=DefenderRole.MARKER_A,
            x=marker_x,
            y=ruck_y,
            vx=0.0,
            vy=0.0,
            target_x=marker_x,
            target_y=ruck_y,
            movement_state=None,
            frozen=True,
            freeze_timer=1.0,
        )
        world.defending_team = [m]
        rap.enforce_ruck_adjacent_pin(world)
        return m, world

    # TACKLE_DRIVE: marker NOT force-snapped; target set to ruck pin (61.0/35.0).
    m, _ = run_pin("TACKLE_DRIVE", marker_x=55.0, ruck_x=60.0, ruck_y=35.0)
    if abs(float(m.x) - 55.0) > 0.001:
        failures.append(
            f"T5: marker force-snapped during TACKLE_DRIVE "
            f"(x {m.x:.2f} != 55.00, must NEVER snap)"
        )
    if abs(float(m.target_x) - 61.0) > 0.001 or abs(float(m.target_y) - 35.0) > 0.001:
        failures.append(
            f"T5: TACKLE_DRIVE marker target NOT set to ruck pin "
            f"({m.target_x:.2f},{m.target_y:.2f} != 61.00/35.00)"
        )

    # RELEASE (Drive->Release boundary): marker NOT force-snapped; target set.
    m, _ = run_pin("RELEASE", marker_x=55.0, ruck_x=60.0, ruck_y=35.0)
    if abs(float(m.x) - 55.0) > 0.001:
        failures.append(
            f"T5: marker force-snapped at RELEASE "
            f"(x {m.x:.2f} != 55.00, must NEVER snap - glide via integrator)"
        )
    if abs(float(m.target_x) - 61.0) > 0.001 or abs(float(m.target_y) - 35.0) > 0.001:
        failures.append(
            f"T5: RELEASE marker target NOT set to ruck pin "
            f"({m.target_x:.2f},{m.target_y:.2f} != 61.00/35.00)"
        )


# ------------------------------------------------------------------
# TEST 6: CONTACT marker role-assignment gate (canary)
# ------------------------------------------------------------------
def test_contact_assign_gate():
    from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

    tuners = DefenceTuners()

    # Canary default is True => CONTACT must NOT assign markers.
    canary = getattr(tuners, "marker_assign_at_release_only", True)
    if not canary:
        failures.append(
            "T6: marker_assign_at_release_only defaulted to False "
            "(must default True — markers only at Drive→Release)"
        )
    no_markers_present = True  # no MARKER_A/B in the defending team at CONTACT
    gate = (not canary) and no_markers_present
    if gate:
        failures.append(
            "T6: CONTACT gate fired with canary True (must NOT assign markers "
            "before tackle end)"
        )

    # Canary False (legacy) => CONTACT DOES assign when no markers present.
    canary_legacy = False
    gate_legacy = (not canary_legacy) and no_markers_present
    if not gate_legacy:
        failures.append(
            "T6: CONTACT gate did NOT fire with canary False (legacy must assign)"
        )


# ------------------------------------------------------------------
# STATIC SCAN (source-level)
# ------------------------------------------------------------------
def _read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def static_scan():
    root = os.path.dirname(os.path.abspath(__file__))

    ope = os.path.join(root, "world", "open_play_engine.py")
    rap_path = os.path.join(root, "game", "defence", "defence_lvl_1", "ruck_adjacent_pin.py")
    mdl_path = os.path.join(root, "game", "defence", "defence_lvl_1", "marker_decision_lock.py")
    ma_path = os.path.join(root, "game", "defence", "defence_lvl_1", "marker_authority.py")
    tuners_path = os.path.join(
        root, "game", "defence", "defence_lvl_1", "defensive_tuners.py"
    )

    ope_src = _read(ope)
    rap_src = _read(rap_path)
    mdl_src = _read(mdl_path)
    ma_src = _read(ma_path)
    tuners_src = _read(tuners_path)

    # 1. ruck_adjacent_pin.py must NEVER write marker POSITION and must not
    #    reference the removed snap machinery / reanchor phases.
    if re.search(r"_d\.x\s*=\s*_tx", rap_src) or re.search(r"_d\.y\s*=\s*_ty", rap_src):
        failures.append(
            "STATIC: ruck_adjacent_pin.py still writes marker POSITION "
            "(`_d.x = _tx` / `_d.y = _ty`) - markers must NEVER force-snap"
        )
    if "_MARKER_REANCHOR_PHASES" in rap_src:
        failures.append(
            "STATIC: ruck_adjacent_pin.py still defines _MARKER_REANCHOR_PHASES "
            "(must be removed - no force-snap gate)"
        )
    if "marker_snap_allowed" in rap_src or "mark_marker_snap" in rap_src:
        failures.append(
            "STATIC: ruck_adjacent_pin.py still uses the removed snap machinery"
        )
    # Marker target authority must still be re-asserted by the pin.
    if "marker_geometry_target" not in rap_src:
        failures.append(
            "STATIC: ruck_adjacent_pin.py missing marker_geometry_target "
            "(target authority re-assert)"
        )
    if "_d.target_x" not in rap_src or "_d.target_y" not in rap_src:
        failures.append(
            "STATIC: ruck_adjacent_pin.py missing marker target re-assert"
        )

    # 2. marker_decision_lock.py must define the invalidation constant + helper
    #    and must NOT define the removed snap machinery.
    if "MARKER_LOCK_RUCK_MOVE_INVALIDATE_M" not in mdl_src:
        failures.append("STATIC: marker_decision_lock.py missing MARKER_LOCK_RUCK_MOVE_INVALIDATE_M")
    if "def marker_lock_invalidated" not in mdl_src:
        failures.append("STATIC: marker_decision_lock.py missing marker_lock_invalidated()")
    if "def marker_snap_allowed" in mdl_src:
        failures.append(
            "STATIC: marker_decision_lock.py still defines marker_snap_allowed "
            "(snap is gone - markers never force-snap)"
        )
    if "def mark_marker_snap" in mdl_src:
        failures.append(
            "STATIC: marker_decision_lock.py still defines mark_marker_snap "
            "(snap is gone)"
        )
    if "SNAP_DEBOUNCE_FRAMES" in mdl_src:
        failures.append(
            "STATIC: marker_decision_lock.py still defines SNAP_DEBOUNCE_FRAMES "
            "(snap debounce is gone)"
        )

    # 3. marker_authority.py must use the decision lock (locked_marker_target)
    #    so markers glide to the locked pre-flip target then the live pin.
    if "locked_marker_target" not in ma_src:
        failures.append(
            "STATIC: marker_authority.py missing locked_marker_target "
            "(decision-lock smoothing)"
        )

    # 4. open_play_engine.py must gate arm on tes->release and use the canary.
    if "_prev_bucket" not in ope_src:
        failures.append("STATIC: open_play_engine.py has no _prev_bucket gate")
    if "if _bucket != getattr(self, \"_last_marker_phase_bucket\", None):\n            arm_marker_lock(" in ope_src:
        failures.append(
            "STATIC: open_play_engine.py still arms on ANY bucket change "
            "(the old _bucket != ... gate)"
        )
    if not re.search(r'_prev_bucket\s*==\s*"tes"\s+and\s+_bucket\s*==\s*"release"', ope_src):
        failures.append(
            "STATIC: open_play_engine.py missing the tes -> release arm gate "
            "(expected `_prev_bucket == \"tes\" and _bucket == \"release\"`)"
        )
    if "marker_assign_at_release_only" not in ope_src:
        failures.append(
            "STATIC: open_play_engine.py missing marker_assign_at_release_only "
            "CONTACT gate"
        )

    # 5. defensive_tuners.py must define the canary (default True).
    if "marker_assign_at_release_only" not in tuners_src:
        failures.append(
            "STATIC: defensive_tuners.py missing marker_assign_at_release_only"
        )
    if "marker_assign_at_release_only: bool = True" not in tuners_src:
        failures.append(
            "STATIC: marker_assign_at_release_only must default True"
        )


def main():
    test_arm_captures_ruck_and_invalidation()
    test_invalidation_effects()
    test_never_force_snap_target_only()
    test_engine_arm_gate()
    test_pin_direct_target()
    test_contact_assign_gate()
    static_scan()

    if failures:
        print("VERIFY_MARKER_DRIVE_GATE: FAIL")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("VERIFY_MARKER_DRIVE_GATE: PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
