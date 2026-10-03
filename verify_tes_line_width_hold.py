"""verify_tes_line_width_hold.py — TES hold RESPECTS the defensive line width.

Asserts the 2026-09-11 line-width clamp on the TES drive structure hold
(``tes_drive_structure.py``):

  1. The held lateral CELL is clamped into ``ruck_y ± governed_width_m()/2``
     (+ ``tes_hold_line_width_tol_m``) — the seed lane from the OPEN_PLAY
     count-lane model can sit outside the line width, which fanned the held
     line wide (the reported outward Y push).
  2. A body OUTSIDE the band never FREEZES, even when it is within the freeze
     tolerance of the raw (unclamped) lane, and its target is the in-band cell
     so it jogs IN to the line width.
  3. A body on the clamped cell FREEZES (HOLD) — in-position behaviour intact.
  4. ``tes_hold_clamp_line_width_enabled = False`` restores the raw-lane hold
     (no clamp) — byte-identical legacy behaviour.
  5. The band tracks the governed line width (a wider ``def_line_width_m``
     yields a wider band).

Runs as a standalone script (exit 0 = pass, 1 = fail).
"""

from __future__ import annotations

import sys

from player.player import DefenderRole, Player
from player.movement_states import MovementState
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1 import tes_drive_structure as tds


class _Sub:
    def __init__(self, value: str):
        self.value = value


class _StubWorld:
    def __init__(self, ruck_x=60.0, ruck_y=35.0, sub="tackle_drive",
                 line_width=40.0):
        self.ruck_x_m = ruck_x
        self.ruck_y_m = ruck_y
        self.sub_state = _Sub(sub)
        self._ope_phase_label = sub.upper()
        self.def_line_width_m = line_width
        self.frame_spacing_targets = {}
        self.defending_team = []


def _mk(pid, x, y, role=DefenderRole.DEF_LINE_READY, depth_role="D"):
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
    t.anticipation_enabled = False
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
# PART 1 — CELL CLAMPED INTO THE GOVERNED LINE-WIDTH BAND
# ─────────────────────────────────────────────────────────────────────

def part1():
    print("[1] held cell is clamped into the governed line-width band")
    # def_line_width_m=40 -> half 20; ruck_y=35; tol 0.5 -> band [14.5, 55.5]
    world = _StubWorld(ruck_y=35.0, line_width=40.0)
    tuners = _tuners()
    bc = _BC(60.0, 35.0)
    hi_body = _mk(1, 60.0, 58.0, depth_role="WINGER")
    hi_body.frame_spacing_y = 58.0          # raw lane outside the band
    lo_body = _mk(2, 60.0, 10.0, depth_role="WINGER")
    lo_body.frame_spacing_y = 10.0          # raw lane below the band
    in_body = _mk(3, 60.0, 45.0, depth_role="C")
    in_body.frame_spacing_y = 45.0          # inside the band
    world.defending_team = [hi_body, lo_body, in_body]
    tds.apply_tes_drive_structure(
        world, [hi_body, lo_body, in_body], bc, 1 / 60, tuners,
    )
    band_hi = 35.0 + 20.0 + 0.5
    band_lo = 35.0 - 20.0 - 0.5
    _check(abs(float(hi_body._tes_hold_y) - band_hi) < 1e-6,
           f"1.1 high lane clamped to band hi ({hi_body._tes_hold_y:.2f})")
    _check(abs(float(lo_body._tes_hold_y) - band_lo) < 1e-6,
           f"1.2 low lane clamped to band lo ({lo_body._tes_hold_y:.2f})")
    _check(abs(float(in_body._tes_hold_y) - 45.0) < 1e-6,
           "1.3 in-band lane left unchanged")


# ─────────────────────────────────────────────────────────────────────
# PART 2 — OUT-OF-BAND BODY DOES NOT FREEZE; JOGS IN TO THE LINE WIDTH
# ─────────────────────────────────────────────────────────────────────

def part2():
    print("[2] out-of-band body jogs IN; on-band body freezes")
    world = _StubWorld(ruck_y=35.0, line_width=40.0)
    tuners = _tuners()
    bc = _BC(60.0, 35.0)
    # Body sits within tol of the RAW lane (58.0) -> would freeze unclamped,
    # but the raw lane is outside the band, so it must NOT freeze.
    d = _mk(4, 60.0, 58.1, depth_role="WINGER")
    d.frame_spacing_y = 58.0
    world.defending_team = [d]
    tds.apply_tes_drive_structure(world, [d], bc, 1 / 60, tuners)
    _check(d.movement_state == MovementState.DRIFT,
           "2.1 out-of-band body does NOT freeze (DRIFT)")
    _check(abs(float(d.target_y) - (35.0 + 20.0 + 0.5)) < 1e-6,
           f"2.2 target_y is the in-band cell ({d.target_y:.2f})")
    # A body sitting exactly on the clamped cell freezes.  X must be on the
    # BC-relative depth-role cell too (bc.x + WIDTH_OFFSETS[WINGER] = 70).
    d2 = _mk(5, 70.0, 35.0 + 20.0 + 0.5, depth_role="WINGER")
    d2.frame_spacing_y = 58.0
    world.defending_team = [d2]
    tds.apply_tes_drive_structure(world, [d2], bc, 1 / 60, tuners)
    _check(d2.movement_state == MovementState.HOLD,
           "2.3 body on the clamped cell freezes (HOLD)")
    _check(d2.move_speed == 0.0 and d2.vx == 0.0 and d2.vy == 0.0,
           "2.4 frozen body has zero velocity")


# ─────────────────────────────────────────────────────────────────────
# PART 3 — GATE + GOVERNED-WIDTH TRACKING
# ─────────────────────────────────────────────────────────────────────

def part3():
    print("[3] clamp gate + governed-width tracking")
    # Disabled -> raw lane held (no clamp).
    world = _StubWorld(ruck_y=35.0, line_width=40.0)
    t_off = _tuners(tes_hold_clamp_line_width_enabled=False)
    bc = _BC(60.0, 35.0)
    d = _mk(6, 60.0, 58.0, depth_role="WINGER")
    d.frame_spacing_y = 58.0
    world.defending_team = [d]
    tds.apply_tes_drive_structure(world, [d], bc, 1 / 60, t_off)
    _check(abs(float(d._tes_hold_y) - 58.0) < 1e-6,
           "3.1 disabled clamp holds the raw lane (58.0)")

    # Wider governed width -> wider band (larger tolerance for the same lane).
    world2 = _StubWorld(ruck_y=35.0, line_width=60.0)
    t2 = _tuners()
    d2 = _mk(7, 60.0, 58.0, depth_role="WINGER")
    d2.frame_spacing_y = 58.0
    world2.defending_team = [d2]
    tds.apply_tes_drive_structure(world2, [d2], bc, 1 / 60, t2)
    _check(abs(float(d2._tes_hold_y) - 58.0) < 1e-6,
           "3.2 wider line width (60) -> 58.0 now inside the band")

    # Band helper agrees with the governed width.
    band = tds._line_width_band(world, bc, _tuners())
    _check(band is not None and abs(band[1] - (35.0 + 20.0 + 0.5)) < 1e-6,
           "3.3 _line_width_band == ruck_y ± governed half (+tol)")


def main():
    print("=== verify_tes_line_width_hold ===")
    part1()
    part2()
    part3()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
