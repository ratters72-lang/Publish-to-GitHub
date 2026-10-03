"""verify_tes_drive_structure_hold.py — TES DRIVE STRUCTURE-HOLD verifier.

Asserts the 2026-09-11 TES-drive line contract (tes_drive_structure.py):

  1. In-position line body FREEZES (HOLD, zero velocity, no position move).
  2. Out-of-position line body JOGS toward its depth role (DRIFT) and is
     capped at tes_hold_jog_speed_mps.
  3. The DEPTH role holds RELATIVE to the live carrier: cell X is
     ``bc.x + WIDTH_OFFSETS[depth_role]`` and TRACKS the carrier every
     frame, while the LATERAL lane Y is locked at drive entry and is NOT
     moved by the carrier.
  4. Only tacklers are excluded from the structure hold (TES owns them).
  5. The spacing band stays LOCKED with a bounded stretch (the anticipation
     micro-adjustment is clamped to tes_band_stretch_max_m).
  6. At RELEASE the line unfreezes INSTANTLY and DEF_RETREAT_10 depth
     targeting switches from the drive-ENTRY ruck_x to the LIVE ruck_x.

Runs as a standalone script (exit 0 = pass, 1 = fail).
"""

from __future__ import annotations

import math
import sys

from player.player import DefenderRole, Player
from player.movement_states import MovementState
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1 import tes_drive_structure as tds


# ─────────────────────────────────────────────────────────────────────
# STUB WORLD
# ─────────────────────────────────────────────────────────────────────

class _Sub:
    def __init__(self, value: str):
        self.value = value


class _StubWorld:
    def __init__(self, ruck_x=60.0, ruck_y=35.0, sub="tackle_drive"):
        self.ruck_x_m = ruck_x
        self.ruck_y_m = ruck_y
        self.sub_state = _Sub(sub)
        self._ope_phase_label = sub.upper()
        self.frame_spacing_targets = {}
        self.defending_team = []


def _mk(pid, x, y, role=DefenderRole.DEF_LINE_READY, depth_role="A"):
    p = Player(x, y, pid)
    p.defender_role = role
    p.depth_role = depth_role
    p.max_speed_mps = 9.2
    return p


class _BC:
    def __init__(self, x, y):
        self.x = x
        self.y = y
        self.vx = 0.0
        self.vy = 0.0


def _tuners(**over):
    t = DefenceTuners()
    t.anticipation_enabled = False   # isolate the structure hold from lean
    for k, v in over.items():
        setattr(t, k, v)
    return t


_PASS = 0
_FAIL = 0


def _check(cond, label):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}")


# ─────────────────────────────────────────────────────────────────────
# PART 1 — FREEZE IN POSITION
# ─────────────────────────────────────────────────────────────────────

def part1():
    print("[1] in-position body freezes")
    world = _StubWorld(ruck_x=60.0)
    tuners = _tuners()
    # depth_role A -> offset 0.0 -> cell = (60.0, lane)
    d = _mk(1, 60.0, 30.0, depth_role="A")
    d.role_depth_target_y = 30.0
    bc = _BC(60.0, 35.0)
    world.defending_team = [d]
    held = tds.apply_tes_drive_structure(world, [d], bc, 1 / 60, tuners)
    _check(d in held, "1.1 body owned by structure hold")
    _check(d.move_speed == 0.0, "1.2 move_speed == 0 (frozen)")
    _check(d.vx == 0.0 and d.vy == 0.0, "1.3 velocity zeroed")
    _check(d.movement_state == MovementState.HOLD, "1.4 movement_state HOLD")
    _check(abs(d.target_x - d.x) < 1e-9 and abs(d.target_y - d.y) < 1e-9,
           "1.5 target pinned to current position")
    x0, y0 = d.x, d.y
    for _ in range(10):
        d.update(1 / 60)
    _check(abs(d.x - x0) < 1e-6 and abs(d.y - y0) < 1e-6,
           "1.6 position unchanged across 10 frames")


# ─────────────────────────────────────────────────────────────────────
# PART 2 — JOG OUT OF POSITION
# ─────────────────────────────────────────────────────────────────────

def part2():
    print("[2] out-of-position body jogs toward its depth role")
    world = _StubWorld(ruck_x=60.0)
    tuners = _tuners()
    d = _mk(2, 50.0, 30.0, depth_role="A")   # cell x = 60.0 (offset 0)
    d.role_depth_target_y = 30.0
    bc = _BC(60.0, 35.0)
    world.defending_team = [d]
    tds.apply_tes_drive_structure(world, [d], bc, 1 / 60, tuners)
    _check(d.movement_state == MovementState.DRIFT, "2.1 movement_state DRIFT")
    _check(abs(d.target_x - 60.0) < 1e-6, "2.2 target_x == depth-role cell")
    _check(abs(d.move_speed - tuners.tes_hold_jog_speed_mps) < 1e-6,
           "2.3 move_speed capped at tes_hold_jog_speed_mps")
    # One integration step moves it toward the cell, not away.
    x_before = d.x
    d.update(1 / 60)
    _check(d.x > x_before, "2.4 body jogs TOWARD the depth cell (+X)")


# ─────────────────────────────────────────────────────────────────────
# PART 3 — IGNORE BALL-CARRIER MOVEMENT
# ─────────────────────────────────────────────────────────────────────

def part3():
    print("[3] depth tracks the carrier; the lane stays locked")
    world = _StubWorld(ruck_x=60.0)
    tuners = _tuners()
    d = _mk(3, 62.0, 30.0, depth_role="B")   # offset 2.0
    d.frame_spacing_y = 30.0                 # last OPEN_PLAY applied lane
    bc = _BC(60.0, 35.0)
    world.defending_team = [d]
    tds.apply_tes_drive_structure(world, [d], bc, 1 / 60, tuners)
    cell_x_1 = float(getattr(d, tds.HOLD_X_ATTR))
    lane_1 = float(getattr(d, tds.HOLD_Y_ATTR))
    _check(abs(cell_x_1 - 62.0) < 1e-6,
           "3.1 cell_x == bc.x + role offset (60 + 2)")
    _check(abs(lane_1 - 30.0) < 1e-6,
           "3.2 lane Y taken from the last OPEN_PLAY frame_spacing_y")
    # Drive the carrier 5 m downfield and 4 m laterally, re-apply.
    bc.x = 65.0
    bc.y = 39.0
    tds.apply_tes_drive_structure(world, [d], bc, 1 / 60, tuners)
    cell_x_2 = float(getattr(d, tds.HOLD_X_ATTR))
    lane_2 = float(getattr(d, tds.HOLD_Y_ATTR))
    _check(abs(cell_x_2 - 67.0) < 1e-6,
           "3.3 cell_x TRACKS the live carrier (65 + 2)")
    _check(abs(cell_x_2 - cell_x_1 - 5.0) < 1e-6,
           "3.4 depth role holds RELATIVE to the carrier")
    _check(abs(lane_2 - lane_1) < 1e-9,
           "3.5 lane Y unmoved by BC movement (band locked)")
    # The mid-drive spacing dict must NOT be consulted for the lane.
    world.frame_spacing_targets = {id(d): 99.0}
    d.frame_spacing_y = 30.0
    tds.clear_body_hold(d)
    tds.apply_tes_drive_structure(world, [d], bc, 1 / 60, tuners)
    _check(abs(float(getattr(d, tds.HOLD_Y_ATTR)) - 30.0) < 1e-6,
           "3.6 mid-drive frame_spacing_targets ignored for the lane")


# ─────────────────────────────────────────────────────────────────────
# PART 4 — TACKLERS EXCLUDED
# ─────────────────────────────────────────────────────────────────────

def part4():
    print("[4] tacklers are never structure-held (TES owns them)")
    world = _StubWorld(ruck_x=60.0)
    tuners = _tuners()
    tackler = _mk(4, 58.0, 35.0, role=DefenderRole.TACKLER_1, depth_role="A")
    _check(not tds.is_structure_hold_eligible(tackler),
           "4.1 TACKLER_1 not eligible")
    marker = _mk(5, 60.0, 35.0, role=DefenderRole.MARKER_A, depth_role="A")
    _check(not tds.is_structure_hold_eligible(marker),
           "4.2 MARKER_A not eligible")
    fb = _mk(6, 60.0, 35.0, role=DefenderRole.DEF_LINE_READY, depth_role="A")
    fb.fullback_role_active = True
    _check(not tds.is_structure_hold_eligible(fb),
           "4.3 fullback not eligible")
    line = _mk(7, 60.0, 35.0, role=DefenderRole.DEF_LINE_READY, depth_role="A")
    _check(tds.is_structure_hold_eligible(line),
           "4.4 DEF_LINE_READY eligible")
    # A tackler passed in the pool is left untouched (target not re-pointed).
    world.defending_team = [tackler]
    tackler.target_x = 999.0
    bc = _BC(60.0, 35.0)
    tds.apply_tes_drive_structure(world, [tackler], bc, 1 / 60, tuners)
    _check(abs(tackler.target_x - 999.0) < 1e-9,
           "4.5 tackler target untouched by the authority")


# ─────────────────────────────────────────────────────────────────────
# PART 5 — BOUNDED BAND STRETCH (ANTICIPATION MICRO-ADJUSTMENT)
# ─────────────────────────────────────────────────────────────────────

def part5():
    print("[5] spacing band stays locked with a bounded stretch")
    tuners = _tuners(anticipation_enabled=True)
    tuners.tes_band_stretch_max_m = 0.4
    tuners.anticipation_max_gain_m = 10.0   # deliberately huge
    world = _StubWorld(ruck_x=60.0)
    d = _mk(8, 60.0, 35.0, depth_role="A")
    bc = _BC(60.0, 38.0)   # carrier 3 m off the lane (in-band)
    world.ball_carrier = bc   # is_drive_phase requires a live carrier
    stretch = tds._anticipation_stretch(world, d, bc, tuners, 35.0)
    # raw gain is huge (10) so the stretch MUST be clamped to the cap.
    _check(abs(stretch - 0.4) < 1e-9,
           f"5.1 stretch clamped to the cap (+0.4) ({stretch:.4f})")
    _check(stretch > 0.0, "5.2 stretch leans TOWARD the threat lane")
    # With the cap removed (0) the stretch is exactly zero.
    t0 = _tuners(anticipation_enabled=True)
    t0.tes_band_stretch_max_m = 0.0
    world.ball_carrier = bc
    s0 = tds._anticipation_stretch(world, d, bc, t0, 35.0)
    _check(s0 == 0.0, "5.3 zero cap -> zero stretch")
    # Anticipation disabled -> no stretch at all.
    t_off = _tuners(anticipation_enabled=False)
    s_off = tds._anticipation_stretch(world, d, bc, t_off, 35.0)
    _check(s_off == 0.0, "5.4 anticipation disabled -> zero stretch")


# ─────────────────────────────────────────────────────────────────────
# PART 6 — RELEASE: INSTANT UNFREEZE + LIVE-RUCK RETARGET
# ─────────────────────────────────────────────────────────────────────

def part6():
    print("[6] RELEASE unfreezes instantly and re-points DEF_RETREAT_10")
    world = _StubWorld(ruck_x=60.0)
    tuners = _tuners()
    d = _mk(9, 60.0, 30.0, depth_role="A")
    d.role_depth_target_y = 30.0
    r = _mk(10, 70.0, 30.0, role=DefenderRole.DEF_RETREAT_10, depth_role="D")
    r.role_depth_target_y = 30.0
    bc = _BC(60.0, 35.0)
    world.defending_team = [d, r]
    tds.apply_tes_drive_structure(world, [d, r], bc, 1 / 60, tuners)
    _check(tds.is_tes_structure_hold(d) and tds.is_tes_structure_hold(r),
           "6.1 both bodies held during the drive")

    # The ruck moves (new tackle) at RELEASE; clear = instant unfreeze.
    world.ruck_x_m = 72.0
    cleared = tds.clear_tes_drive_structure(world, tuners)
    _check(cleared == 2, f"6.2 two holds cleared (got {cleared})")
    _check(not tds.is_tes_structure_hold(d) and not tds.is_tes_structure_hold(r),
           "6.3 every hold stamp dropped (unfrozen)")
    _check(not hasattr(world, tds.ENTRY_RUCK_X_ATTR),
           "6.4 drive-entry ruck capture cleared")
    _expected = 72.0 + float(tuners.WIDTH_OFFSETS["D"])
    _check(abs(float(r.role_depth_target_x) - _expected) < 1e-6,
           "6.5 DEF_RETREAT_10 depth retargeted to LIVE ruck_x + offset")
    # A line body is NOT retargeted by this helper (depth authority owns it).
    _check(getattr(d, "role_depth_target_x", None) in (None, 60.0),
           "6.6 non-retreat line body not re-pointed by the helper")


# ─────────────────────────────────────────────────────────────────────
# PART 7 — GATE + DISABLED AUTHORITY
# ─────────────────────────────────────────────────────────────────────

def part7():
    print("[7] feature gate")
    tuners = _tuners(tes_drive_structure_enabled=False)
    world = _StubWorld(ruck_x=60.0)
    d = _mk(11, 50.0, 30.0, depth_role="A")
    bc = _BC(60.0, 35.0)
    world.defending_team = [d]
    held = tds.apply_tes_drive_structure(world, [d], bc, 1 / 60, tuners)
    _check(held == [], "7.1 disabled authority is a no-op")
    _check(not tds.is_tes_structure_hold(d), "7.2 body not held when disabled")
    # Outside the drive chamber the authority is also a no-op.
    tuners2 = _tuners()
    world2 = _StubWorld(ruck_x=60.0, sub="open_play")
    d2 = _mk(12, 50.0, 30.0, depth_role="A")
    held2 = tds.apply_tes_drive_structure(world2, [d2], bc, 1 / 60, tuners2)
    _check(held2 == [], "7.3 outside the drive chamber -> no-op")


def main():
    print("=== verify_tes_drive_structure_hold ===")
    part1()
    part2()
    part3()
    part4()
    part5()
    part6()
    part7()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
