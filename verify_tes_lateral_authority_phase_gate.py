"""verify_tes_lateral_authority_phase_gate.py — TES lateral (Y) authority.

Asserts the 2026-09-11 PHASE-GATED, STABLE-BAND lateral authority on the
TES drive structure hold (``tes_drive_structure.py``):

  1. ``is_lateral_authority_phase`` is the explicit phase gate: it is True
     only for the phases in ``tuners.tes_lateral_authority_phases`` (default
     = the three TES drive sub-states) and False for any other phase or an
     empty phase set.
  2. When the phase gate is CLOSED the lateral authority is released — the
     held cell is the body's TRUE drive-entry lane (no clamp, no stretch).
  3. The line-width band is centred on a STABLE drive-entry reference
     (``resolve_band_ref_y``), latched once per drive — a mid-drive ruck-Y
     change does NOT move the band.
  4. An OUT-OF-LANE body is auto-corrected IN to the band edge (jogs, does
     not freeze); an IN-LANE body keeps its EXACT lane — in-lane spacing is
     not disrupted even when the ruck Y moves (the pre-fix bug overwrote
     the stored lane and squeezed/destroyed in-lane spacing).
  5. ``tes_lateral_band_ref_latched = False`` restores the live-ruck band.
  6. The body's TRUE lane (``LANE_Y_ATTR``) is never overwritten, and the
     RELEASE clears drop the lane seed + the latched band reference.

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
    t.anticipation_enabled = False   # isolate from the anticipation stretch
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
# PART 1 — THE PHASE GATE
# ─────────────────────────────────────────────────────────────────────

def part1():
    print("[1] phase gate — only the configured phases own the lateral authority")
    t = _tuners()
    _check(tds.is_lateral_authority_phase(_StubWorld(sub="tackle_contact"), t),
           "1.1 tackle_contact is in the default phase set")
    _check(tds.is_lateral_authority_phase(_StubWorld(sub="tackle_drive"), t),
           "1.2 tackle_drive is in the default phase set")
    _check(tds.is_lateral_authority_phase(_StubWorld(sub="tackle_dominance"), t),
           "1.3 tackle_dominance is in the default phase set")
    _check(not tds.is_lateral_authority_phase(_StubWorld(sub="open_play"), t),
           "1.4 open_play is OUT of the phase set")
    _check(not tds.is_lateral_authority_phase(_StubWorld(sub="release"), t),
           "1.5 release is OUT of the phase set")

    # A narrower tuner set scopes the authority to one phase.
    t_scope = _tuners(tes_lateral_authority_phases=frozenset({"tackle_drive"}))
    _check(tds.is_lateral_authority_phase(_StubWorld(sub="tackle_drive"), t_scope),
           "1.6 scoped set admits tackle_drive")
    _check(not tds.is_lateral_authority_phase(
        _StubWorld(sub="tackle_contact"), t_scope),
        "1.7 scoped set rejects tackle_contact")

    # An empty phase set disables the lateral authority entirely.
    t_off = _tuners(tes_lateral_authority_phases=frozenset())
    _check(not tds.is_lateral_authority_phase(_StubWorld(sub="tackle_drive"), t_off),
           "1.8 empty phase set -> lateral authority disabled")


# ─────────────────────────────────────────────────────────────────────
# PART 2 — OUT-OF-PHASE RELEASES THE LATERAL AUTHORITY
# ─────────────────────────────────────────────────────────────────────

def part2():
    print("[2] out-of-phase -> held cell is the TRUE lane (no clamp)")
    # band would be [14.5, 55.5]; lane 58.0 is outside it.
    world = _StubWorld(ruck_y=35.0, sub="tackle_contact", line_width=40.0)
    tuners = _tuners(tes_lateral_authority_phases=frozenset({"tackle_drive"}))
    bc = _BC(60.0, 35.0)
    d = _mk(1, 60.0, 58.0, depth_role="A")
    d.frame_spacing_y = 58.0
    world.defending_team = [d]
    tds.apply_tes_drive_structure(world, [d], bc, 1 / 60, tuners)
    _check(abs(float(getattr(d, tds.HOLD_Y_ATTR)) - 58.0) < 1e-6,
           f"2.1 out-of-phase held cell is the true lane ({d._tes_hold_y:.2f})")
    _check(abs(float(getattr(d, tds.LANE_Y_ATTR)) - 58.0) < 1e-6,
           "2.2 true lane seed == 58.0")

    # In phase with the default set the same body IS clamped.
    world2 = _StubWorld(ruck_y=35.0, sub="tackle_drive", line_width=40.0)
    t2 = _tuners()
    d2 = _mk(2, 60.0, 58.0, depth_role="A")
    d2.frame_spacing_y = 58.0
    world2.defending_team = [d2]
    tds.apply_tes_drive_structure(world2, [d2], bc, 1 / 60, t2)
    _check(abs(float(getattr(d2, tds.HOLD_Y_ATTR)) - (35.0 + 20.0 + 0.5)) < 1e-6,
           f"2.3 in-phase held cell clamped to the band hi ({d2._tes_hold_y:.2f})")


# ─────────────────────────────────────────────────────────────────────
# PART 3 — STABLE BAND REFERENCE (LATCHED ONCE PER DRIVE)
# ─────────────────────────────────────────────────────────────────────

def part3():
    print("[3] band reference is latched at drive entry (does not follow ruck_y)")
    world = _StubWorld(ruck_y=35.0, sub="tackle_drive", line_width=40.0)
    tuners = _tuners()
    bc = _BC(60.0, 35.0)
    ref1 = tds.resolve_band_ref_y(world, bc, tuners)
    _check(abs(ref1 - 35.0) < 1e-6, "3.1 band reference latched at 35.0")
    _check(abs(float(getattr(world, tds.BAND_REF_Y_ATTR)) - 35.0) < 1e-6,
           "3.2 world band-ref attr stamped")
    band1 = tds._line_width_band(world, bc, tuners)
    _check(abs(band1[0] - 14.5) < 1e-6 and abs(band1[1] - 55.5) < 1e-6,
           f"3.3 band == [14.5, 55.5] ({band1})")

    # The ruck Y moves mid-drive (engine re-pins it to bc.y) — the latched
    # reference must NOT move, so the band is unchanged.
    world.ruck_y_m = 60.0
    ref2 = tds.resolve_band_ref_y(world, bc, tuners)
    band2 = tds._line_width_band(world, bc, tuners)
    _check(abs(ref2 - 35.0) < 1e-6,
           f"3.4 band reference UNCHANGED after ruck_y -> 60 ({ref2:.2f})")
    _check(abs(band2[0] - 14.5) < 1e-6 and abs(band2[1] - 55.5) < 1e-6,
           f"3.5 band UNCHANGED ({band2})")


# ─────────────────────────────────────────────────────────────────────
# PART 4 — AUTO-CORRECT OUT-OF-LANE; PRESERVE IN-LANE SPACING
# ─────────────────────────────────────────────────────────────────────

def part4():
    print("[4] out-of-lane auto-corrected IN; in-lane spacing preserved")
    world = _StubWorld(ruck_y=35.0, sub="tackle_drive", line_width=40.0)
    tuners = _tuners()
    bc = _BC(60.0, 35.0)
    out = _mk(3, 60.0, 58.0, depth_role="A")     # lane outside the band
    out.frame_spacing_y = 58.0
    inside = _mk(4, 60.0, 45.0, depth_role="A")  # lane inside the band
    inside.frame_spacing_y = 45.0
    world.defending_team = [out, inside]
    tds.apply_tes_drive_structure(world, [out, inside], bc, 1 / 60, tuners)

    _check(out.movement_state == MovementState.DRIFT,
           "4.1 out-of-lane body does NOT freeze (DRIFT)")
    _check(abs(float(out.target_y) - 55.5) < 1e-6,
           f"4.2 out-of-lane target_y is the in-band edge ({out.target_y:.2f})")
    _check(inside.movement_state == MovementState.HOLD,
           "4.3 in-lane body freezes (HOLD)")
    _check(abs(float(inside.target_y) - 45.0) < 1e-6,
           f"4.4 in-lane body target_y is its EXACT lane ({inside.target_y:.2f})")

    # The ruck Y moves mid-drive.  With the latched band the in-lane body's
    # cell is UNTOUCHED — the pre-fix bug re-clamped (and destroyed) it.
    world.ruck_y_m = 20.0
    tds.apply_tes_drive_structure(world, [out, inside], bc, 1 / 60, tuners)
    _check(abs(float(getattr(inside, tds.HOLD_Y_ATTR)) - 45.0) < 1e-6,
           f"4.5 in-lane cell unchanged after ruck move ({inside._tes_hold_y:.2f})")
    _check(abs(float(getattr(inside, tds.LANE_Y_ATTR)) - 45.0) < 1e-6,
           "4.6 true lane seed still 45.0 (never overwritten)")
    _check(abs(float(getattr(out, tds.LANE_Y_ATTR)) - 58.0) < 1e-6,
           "4.7 out-of-lane true lane seed still 58.0 (never overwritten)")


# ─────────────────────────────────────────────────────────────────────
# PART 5 — LEGACY: UNLATCHED BAND TRACKS THE LIVE RUCK
# ─────────────────────────────────────────────────────────────────────

def part5():
    print("[5] tes_lateral_band_ref_latched=False -> live-ruck band")
    world = _StubWorld(ruck_y=35.0, sub="tackle_drive", line_width=40.0)
    tuners = _tuners(tes_lateral_band_ref_latched=False)
    bc = _BC(60.0, 35.0)
    band1 = tds._line_width_band(world, bc, tuners)
    _check(abs(band1[0] - 14.5) < 1e-6 and abs(band1[1] - 55.5) < 1e-6,
           f"5.1 live band at ruck_y=35 ({band1})")
    world.ruck_y_m = 60.0
    band2 = tds._line_width_band(world, bc, tuners)
    _check(abs(band2[0] - 39.5) < 1e-6 and abs(band2[1] - 80.5) < 1e-6,
           f"5.2 live band follows ruck_y -> 60 ({band2})")
    _check(not hasattr(world, tds.BAND_REF_Y_ATTR),
           "5.3 unlatch path never stamps a latched reference")


# ─────────────────────────────────────────────────────────────────────
# PART 6 — RELEASE CLEARS + CLAMP-DISABLED NO-OP
# ─────────────────────────────────────────────────────────────────────

def part6():
    print("[6] RELEASE clears the lane seed + band ref; clamp-off is a no-op")
    world = _StubWorld(ruck_y=35.0, sub="tackle_drive", line_width=40.0)
    tuners = _tuners()
    bc = _BC(60.0, 35.0)
    d = _mk(5, 60.0, 30.0, depth_role="A")
    d.frame_spacing_y = 30.0
    world.defending_team = [d]
    tds.apply_tes_drive_structure(world, [d], bc, 1 / 60, tuners)
    _check(hasattr(d, tds.LANE_Y_ATTR) and hasattr(world, tds.BAND_REF_Y_ATTR),
           "6.1 lane seed + band ref present during the drive")
    cleared = tds.clear_tes_drive_structure(world, tuners)
    _check(cleared == 1, f"6.2 one hold cleared ({cleared})")
    _check(not hasattr(d, tds.LANE_Y_ATTR),
           "6.3 lane seed dropped at RELEASE")
    _check(not hasattr(world, tds.BAND_REF_Y_ATTR),
           "6.4 latched band ref dropped at RELEASE")

    # Clamp disabled -> the raw lane is held (legacy), regardless of the band.
    world2 = _StubWorld(ruck_y=35.0, sub="tackle_drive", line_width=40.0)
    t_off = _tuners(tes_hold_clamp_line_width_enabled=False)
    d2 = _mk(6, 60.0, 58.0, depth_role="A")
    d2.frame_spacing_y = 58.0
    world2.defending_team = [d2]
    tds.apply_tes_drive_structure(world2, [d2], bc, 1 / 60, t_off)
    _check(abs(float(getattr(d2, tds.HOLD_Y_ATTR)) - 58.0) < 1e-6,
           "6.5 clamp disabled -> raw lane held (58.0)")


def main():
    print("=== verify_tes_lateral_authority_phase_gate ===")
    part1()
    part2()
    part3()
    part4()
    part5()
    part6()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
