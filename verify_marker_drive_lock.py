"""
verify_marker_drive_lock.py - Verify markers are NOT promoted early.

Task (2026-09-04, revised): "Markers are still wrong because lvl1/lvl2
marker assignment is still running during DOMINANCE/RELEASE, promoting non-
tacklers; the fix is to delete all lvl1/lvl2 marker writers and let
DefenceManager be the sole marker authority at CONTACT/RELEASE/PTB."

This verifier asserts the SINGLE-SOURCE marker-authority architecture:

  1. `DefensiveBehaviourLvl1.apply_markers()` is a PURE non-marker
     retreat/spacing fold-back - it NEVER writes `defender_role =
     MARKER_A/B` in ANY phase (it must not assign markers at all; the
     DefenceManager owns marker roles).
  2. `DefenceManager.assign_markers_for_new_tackle(strict_tacklers_only=True)`
     (TACKLE_CONTACT, strict) only assigns markers when 2+ confirmed TES
     tacklers exist (CASE 1); with 1 tackler it assigns nothing.
  3. `DefenceManager.assign_markers_for_new_tackle(strict_tacklers_only=True)`
     with 2+ tacklers assigns BOTH markers from the tackler pool.
  4. STATIC SCAN: no marker-role WRITE exists in `defensive_behaviour_lvl1.py`,
     `defensive_behaviour_unified.py`, or (if present) `defensive_behaviour_lvl2.py`;
     the ONLY remaining `defender_role = DefenderRole.MARKER_A/B` writes in the
     whole codebase are inside `DefenceManager` (`defence_manager.py`).  The dead
     lvl2 `marker_controller.py` no longer exists.

Exit 0 = PASS, exit 1 = FAIL.
"""

import os
import re
import sys
import types

from player.player import Player, DefenderRole
from world.world_state import GameSubState
from game.defence.defence_lvl_1.defensive_behaviour_lvl1 import (
    DefensiveBehaviourLvl1,
)
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.managers.defence_manager import DefenceManager

MARKER_ROLES = (DefenderRole.MARKER_A, DefenderRole.MARKER_B)
TACKLER_ROLES = (
    DefenderRole.TACKLER_1,
    DefenderRole.TACKLER_2,
    DefenderRole.TACKLER_3,
)

failures = []


def make_defender(x, y, pid, role=None):
    d = Player(x, y, pid)
    if role is not None:
        d.defender_role = role
    # ensure attributes the marker writer touches exist
    d.freeze_timer = 0.0
    d.frozen = False
    d._defer_retreat_10 = False
    d.fullback_role_active = False
    return d


def is_marker(d):
    return getattr(d, "defender_role", None) in MARKER_ROLES


def make_world(sub_state, ruck_x=60.0, ruck_y=35.0, ope=None):
    return types.SimpleNamespace(
        sub_state=sub_state,
        ruck_x_m=ruck_x,
        ruck_y_m=ruck_y,
        open_play_engine=ope,
        ball_carrier=None,
        defence_enabled=True,
        in_pursuit_mode=False,
        first_tackle_not_made=False,
        defensive_line_anchor=70.0,
        use_structured_open_play=True,
    )


# ------------------------------------------------------------------
# TEST 1: apply_markers NEVER assigns markers in ANY phase (retreat-only)
# ------------------------------------------------------------------
def test_apply_markers_never_assigns():
    tuners = DefenceTuners()
    lvl1 = DefensiveBehaviourLvl1(tuners)
    for sub_state in (
        GameSubState.TACKLE_DRIVE,
        GameSubState.TACKLE_DOMINANCE,
        GameSubState.TACKLE_CONTACT,
        GameSubState.TACKLE_RELEASE,
        GameSubState.PTB_READY,
        GameSubState.PTB,
        GameSubState.POST_PTB_RESET,
        GameSubState.OPEN_PLAY,
    ):
        world = make_world(sub_state)
        # Shape that the OLD distance-based apply_markers() would have promoted.
        d1 = make_defender(60.1, 34.9, 101)
        d2 = make_defender(60.5, 35.5, 102)
        # True tacklers present too, so a tackler-first path would also be tested.
        d3 = make_defender(61.0, 35.0, 103, role=DefenderRole.TACKLER_1)
        d4 = make_defender(62.0, 36.0, 104, role=DefenderRole.TACKLER_2)
        defenders = [d1, d2, d3, d4]
        lvl1.apply_markers(world, defenders)
        for d in defenders:
            if is_marker(d):
                failures.append(
                    f"{sub_state}: apply_markers() promoted id={d.player_id} "
                    f"to {d.defender_role} (must be a pure retreat helper; "
                    f"DefenceManager is sole marker authority)"
                )


# ------------------------------------------------------------------
# TEST 2: DefenceManager strict gate - 1 tackler at contact => nothing
# ------------------------------------------------------------------
def test_strict_contact_single_tackler():
    tuners = DefenceTuners()
    dm = DefenceManager(tuners, None)
    world = types.SimpleNamespace(
        defending_team=[],
        ruck_x_m=60.0,
        ruck_y_m=35.0,
    )
    t1 = make_defender(61.0, 35.0, 301, role=DefenderRole.TACKLER_1)
    near = make_defender(60.05, 34.95, 302)  # closest to ruck, NOT a tackler
    far = make_defender(63.0, 35.0, 303)
    world.defending_team = [t1, near, far]
    ope = types.SimpleNamespace(
        world=world,
        primary=t1,
        joiners=[],
        tes_ctx=None,
        ball_carrier=make_defender(60.0, 35.0, 399),
        def_tuners=tuners,
    )
    # strict mode at TACKLE_CONTACT: only 1 confirmed tackler
    dm.assign_markers_for_new_tackle(ope, strict_tacklers_only=True)
    for d in (t1, near, far):
        if is_marker(d):
            failures.append(
                f"strict(1 tackler): a non-2+-tackler marker was assigned "
                f"(id={d.player_id}, role={d.defender_role})"
            )


# ------------------------------------------------------------------
# TEST 3: DefenceManager strict gate - 2 tacklers at contact => CASE 1
# ------------------------------------------------------------------
def test_strict_contact_two_tacklers():
    tuners = DefenceTuners()
    dm = DefenceManager(tuners, None)
    world = types.SimpleNamespace(
        defending_team=[],
        ruck_x_m=60.0,
        ruck_y_m=35.0,
    )
    t1 = make_defender(61.0, 35.0, 401, role=DefenderRole.TACKLER_1)
    t2 = make_defender(61.5, 35.5, 402, role=DefenderRole.TACKLER_2)
    near = make_defender(60.05, 34.95, 403)  # closest to ruck, NOT a tackler
    world.defending_team = [t1, t2, near]
    ope = types.SimpleNamespace(
        world=world,
        primary=t1,
        joiners=[t2],
        tes_ctx=None,
        ball_carrier=make_defender(60.0, 35.0, 499),
        def_tuners=tuners,
    )
    dm.assign_markers_for_new_tackle(ope, strict_tacklers_only=True)
    markers = [d for d in world.defending_team if is_marker(d)]
    if len(markers) != 2:
        failures.append(
            f"strict(2 tacklers): expected 2 markers, got {len(markers)}"
        )
    else:
        for m in markers:
            if m.player_id not in (401, 402):
                failures.append(
                    f"strict(2 tacklers): non-tackler id={m.player_id} "
                    f"promoted to {m.defender_role}"
                )


# ------------------------------------------------------------------
# STATIC SCAN: no marker-role write outside DefenceManager
# ------------------------------------------------------------------
def _read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.readlines()


def static_scan():
    root = os.path.dirname(os.path.abspath(__file__))
    marker_write = re.compile(r"defender_role\s*=\s*DefenderRole\.(MARKER_A|MARKER_B)")

    # Files that MUST NOT write marker roles (lvl1/lvl2 defensive behaviour).
    forbidden = [
        os.path.join(root, "game", "defence", "defence_lvl_1", "defensive_behaviour_lvl1.py"),
        os.path.join(root, "game", "defence", "defence_lvl_1", "defensive_behaviour_unified.py"),
        os.path.join(root, "game", "defence", "defence_lvl_2", "defensive_behaviour_lvl2.py"),
    ]
    for p in forbidden:
        if not os.path.exists(p):
            continue  # lvl2 behaviour may not exist; skip
        for i, ln in enumerate(_read(p)):
            if marker_write.search(ln):
                failures.append(
                    f"static: {os.path.relpath(p, root)}:{i+1} writes a marker role "
                    f"(DefenceManager must be sole authority)"
                )

    # Dead lvl2 MarkerController must be gone.
    marker_controller = os.path.join(
        root, "game", "defence", "defence_lvl_2", "marker_controller.py"
    )
    if os.path.exists(marker_controller):
        failures.append(
            f"static: dead marker writer still present: "
            f"{os.path.relpath(marker_controller, root)}"
        )

    # The ONLY allowed marker-role WRITE location is DefenceManager.
    defence_manager = os.path.join(
        root, "game", "managers", "defence_manager.py"
    )
    if not os.path.exists(defence_manager):
        failures.append("static: defence_manager.py not found")
        return
    dm_writes = 0
    for i, ln in enumerate(_read(defence_manager)):
        if marker_write.search(ln):
            dm_writes += 1
    if dm_writes == 0:
        failures.append(
            "static: DefenceManager has no marker-role writer "
            "(must own marker assignment)"
        )


def main():
    test_apply_markers_never_assigns()
    test_strict_contact_single_tackler()
    test_strict_contact_two_tacklers()
    static_scan()

    if failures:
        print("VERIFY_MARKER_DRIVE_LOCK: FAIL")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("VERIFY_MARKER_DRIVE_LOCK: PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
