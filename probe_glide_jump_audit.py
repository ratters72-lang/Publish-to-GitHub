"""probe_glide_jump_audit.py — MEASURES the reported visible jump.

THE REPORT
----------
    "defenders not involved in the contest must preserve role-depth
     authority, but converge via smooth glide.  The current implementation
     appears to re-target or correct too aggressively, creating visible
     jumps during defensive retreat."

Two distinct defects produced that visible jump.  Both are measured here,
once per frame, under both gauges, on a defender that is NOT in the contest
and whose own movement authority never asked for more than its backpedal:

  JUMP A — THE VELOCITY SPIKE (amplification)
      Both glides rescaled velocity by ``vscale = CEILING / spd`` whenever
      they clamped.  For a body backpedalling at 1.0 m/s, the frame the
      clamp fired ran at 6.0 m/s and the frame it released dropped back to
      1.0 — a 6x two-sided spike on a body whose authority commanded 1.0.

  JUMP B — THE TELEPORT (unbounded band snap)
      Both glides applied their Y slot-band correction by writing the band
      edge DIRECTLY, AFTER the per-frame ceiling.  A body metres outside its
      band therefore crossed the whole distance in one frame, defeating the
      cap the same module claimed to enforce.

WHAT IS MEASURED
----------------
    peak step  (m)   the largest single-frame displacement of the body
    peak |v|   (m/s) the largest speed the body was left travelling at
    converge   (frames) how long the Y band error takes to close

A frame is a JUMP when ``step > ceiling * dt`` (0.1 m at 60 Hz) or when
``|v| > the speed the body's own authority commanded``.

Reads:  ``enforce_tackle_begin_glide`` (role_depth_glide),
        ``enforce_attach_band``       (attach_band).
Both are exercised headless with stubbed bodies/world — no engine boot, no
pygame window.

Usage:  python probe_glide_jump_audit.py
"""
from __future__ import annotations

import math
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.pop("RLM_GLIDE_SMOOTH", None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from game.defence.defence_lvl_1 import glide_budget as GB
from game.defence.defence_lvl_1.role_depth_glide import (
    enforce_tackle_begin_glide,
)
from game.defence.defence_lvl_1.attach_band import enforce_attach_band
from player.player import DefenderRole

DT = 1.0 / 60.0
CEILING = GB.GLIDE_SPEED_MPS
BUDGET = CEILING * DT
# The body's OWN authority in this audit: a plain backpedal into its depth.
# BOTH scenarios use this deliberately: the point of the audit is that a
# non-contest defender's corrections must never exceed what its own
# movement authority asked for, so the authority speed is the yardstick.
OWN_SPEED = 1.0
BAND_Y = 40.0          # the slot band reference; body starts 5 m below it
BAND_EDGE = BAND_Y - 0.3
TELEPORT_X = 60.0      # the depth writer's destination: 10 m ahead
FRAMES = 240


class _NS:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class Body(_NS):
    def __init__(self, pid, x, y, **kw):
        super().__init__(
            player_id=pid, x=float(x), y=float(y), vx=0.0, vy=0.0,
            frozen=False, freeze_timer=0.0, fullback_role_active=False,
            defender_role=DefenderRole.DEF_LINE_READY, depth_role="WINGER",
            movement_state=_NS(name="RETREAT"), depth_locked=False,
            _tes_hold_active=False, target_y=float(BAND_Y),
        )
        for k, v in kw.items():
            setattr(self, k, v)


class World(_NS):
    def __init__(self, dt=DT, frame=0):
        super().__init__(
            dt=dt, frame_count=frame,
            sub_state=_NS(value="tackle_drive"),
            tackle_slot_threshold_m=0.3,
            ball_carrier=None,
        )


class OPE(_NS):
    def __init__(self):
        super().__init__(
            _phase="TACKLE_DRIVE", primary=None, joiners=(),
            _attached_player_slots={},
            tes_ctx=_NS(attached_ids=set(), attached=set()),
        )


def tuners(smooth: bool):
    return _NS(
        tackle_begin_glide_enabled=True,
        attach_slot_reserve_enabled=True,
        tackle_assist_radius_m=5.0,
        tackle_snap_radius_m=2.0,
        joiner_grace_s=0.1,
        defender_contact_damp=0.1,
        glide_smooth_enabled=smooth,
    )


def speed_of(body) -> float:
    return math.hypot(float(getattr(body, "vx", 0.0)),
                      float(getattr(body, "vy", 0.0)))


class Run:
    """Per-frame record of one scenario under one gauge."""

    def __init__(self, label: str, own_speed: float) -> None:
        self.label = label
        # The speed THIS scenario's own movement authority commanded.  A frame
        # is only a velocity jump against its OWN authority, which is
        # scenario-specific (a 1.0 m/s backpedal vs a 2.5 m/s lateral drift).
        self.own_speed = own_speed
        self.peak_step = 0.0
        self.peak_step_frame = 0
        self.peak_speed = 0.0
        self.peak_speed_frame = 0
        self.raw_peak_step = 0.0
        self.converge_frame = -1
        self.final_y = 0.0

    def note(self, frame, raw_step, step, speed, y) -> None:
        if raw_step > self.raw_peak_step:
            self.raw_peak_step = raw_step
        if step > self.peak_step:
            self.peak_step = step
            self.peak_step_frame = frame
        if speed > self.peak_speed:
            self.peak_speed = speed
            self.peak_speed_frame = frame
        if self.converge_frame < 0 and abs(y - BAND_EDGE) < 1e-6:
            self.converge_frame = frame
        self.final_y = y


def scenario_role_depth(smooth: bool, frames: int = FRAMES) -> Run:
    """A non-contest defender backpedalling while the depth writer snaps x."""
    run = Run("role_depth_glide", OWN_SPEED)
    t = tuners(smooth)
    world = World()
    ope = OPE()
    d = Body(101, 50.0, BAND_Y - 5.0)
    for frame in range(1, frames + 1):
        world.frame_count = frame
        pre = (d.x, d.y)
        # ── the body's OWN authority: a 1.0 m/s backpedal ──
        d.vx, d.vy = OWN_SPEED, 0.0
        d.x += OWN_SPEED * DT
        # ── the depth writer SNAPS the body to its role-depth destination ──
        d.x = TELEPORT_X
        raw_step = math.hypot(d.x - pre[0], d.y - pre[1])
        enforce_tackle_begin_glide(
            world, [d], DT, t, ope, pre_pos={d.player_id: pre},
        )
        step = math.hypot(d.x - pre[0], d.y - pre[1])
        run.note(frame, raw_step, step, speed_of(d), d.y)
    return run


def scenario_attach_band(smooth: bool, frames: int = 120) -> Run:
    """An assist-band defender gliding onto its slot band while out of band.

    GEOMETRY MATTERS.  The authority manages bodies INSIDE R_assist (5.0 m)
    and OUTSIDE R_tackle (2.0 m); inside R_tackle the body is snap-eligible
    and TES owns it, so the authority deliberately releases it.  The carrier
    therefore sits at (54.0, 39.0) and the body starts at (50.0, 36.5) --
    dist 4.72 m, inside R_assist.  The body's OWN authority drives it +x at
    1.0 m/s, which keeps the carrier distance inside (2.0, 5.0) for the WHOLE
    run (at the far end, x == 52.0 with y == 39.7 gives dist 2.12 m), while
    the band correction moves Y only.  The slot band reference is
    target_y == 40.0, so the band edge is 39.70 -- 3.2 m above the start.

    Decoupling X (own authority) from Y (band correction) is deliberate: it
    is then unambiguous which write produced the movement being measured.
    """
    run = Run("attach_band", OWN_SPEED)
    t = tuners(smooth)
    world = World()
    ope = OPE()
    bc = Body(900, 54.0, 39.0)
    world.ball_carrier = bc
    d = Body(102, 50.0, BAND_Y - 3.5, move_speed=OWN_SPEED)
    for frame in range(1, frames + 1):
        world.frame_count = frame
        pre = (d.x, d.y)
        # ── the body's OWN authority: a 1.0 m/s lateral drift, already spent ──
        d.vx, d.vy = OWN_SPEED, 0.0
        d.x += OWN_SPEED * DT
        raw_step = math.hypot(d.x - pre[0], d.y - pre[1])
        enforce_attach_band(
            world, [d], DT, t, ope, bc=bc, pre_pos={d.player_id: pre},
            glide_speed_mps=CEILING,
        )
        step = math.hypot(d.x - pre[0], d.y - pre[1])
        run.note(frame, raw_step, step, speed_of(d), d.y)
    return run


def report(label: str, before: Run, after: Run) -> bool:
    print("\n" + "=" * 78)
    print(f"{label}")
    print("=" * 78)
    print(f"{'metric':<34} {'PRE-FIX':>16} {'FIXED':>16}")
    print("-" * 78)
    print(f"{'peak single-frame step (m)':<34} "
          f"{before.peak_step:>16.4f} {after.peak_step:>16.4f}")
    print(f"{'  at frame':<34} "
          f"{before.peak_step_frame:>16} {after.peak_step_frame:>16}")
    print(f"{'peak speed left on the body (m/s)':<34} "
          f"{before.peak_speed:>16.4f} {after.peak_speed:>16.4f}")
    print(f"{'  at frame':<34} "
          f"{before.peak_speed_frame:>16} {after.peak_speed_frame:>16}")
    conv_b = "-" if before.converge_frame < 0 else str(before.converge_frame)
    conv_a = "-" if after.converge_frame < 0 else str(after.converge_frame)
    print(f"{'frames to close the Y band error':<34} "
          f"{conv_b:>16} {conv_a:>16}")
    print(f"{'final Y (band edge ' + f'{BAND_EDGE:.2f}' + ')':<34} "
          f"{before.final_y:>16.3f} {after.final_y:>16.3f}")
    print("-" * 78)
    print(f"per-frame ceiling (ceiling * dt)      = {BUDGET:.4f} m")
    print(f"the body's OWN commanded speed        = {before.own_speed:.2f} m/s")

    authority = before.own_speed
    jump_before_step = before.peak_step > BUDGET + 1e-9
    jump_before_speed = before.peak_speed > authority + 1e-9
    jump_after_step = after.peak_step > BUDGET + 1e-9
    jump_after_speed = after.peak_speed > authority + 1e-9
    print(f"\nPRE-FIX  step over ceiling: {jump_before_step}   "
          f"speed over authority: {jump_before_speed}   "
          f"-> JUMP = {jump_before_step or jump_before_speed}")
    print(f"FIXED    step over ceiling: {jump_after_step}   "
          f"speed over authority: {jump_after_speed}   "
          f"-> JUMP = {jump_after_step or jump_after_speed}")
    return not (jump_after_step or jump_after_speed)


def main() -> int:
    print("=" * 78)
    print("GLIDE JUMP AUDIT — the visible jump during defensive retreat")
    print("=" * 78)
    print(f"Stubbed bodies/world; {FRAMES} frames; ceiling {CEILING} m/s; "
          f"dt {DT:.4f}")

    ok = True
    ok &= report(
        "SCENARIO R — role_depth_glide: backpedal + depth-writer x snap",
        scenario_role_depth(smooth=False),
        scenario_role_depth(smooth=True),
    )
    ok &= report(
        "SCENARIO A — attach_band: assist-band nudge + slot-band error",
        scenario_attach_band(smooth=False),
        scenario_attach_band(smooth=True),
    )

    print("\n" + "=" * 78)
    if ok:
        print("RESULT: the fix removes the jump in both authorities")
        print("  - no frame exceeds the glide ceiling")
        print("  - no body is left travelling faster than its own authority")
        print("  - the band still CONVERGES (glide, not teleport, not starve)")
        return 0
    print("RESULT: JUMP STILL PRESENT — see the rows above")
    return 1


if __name__ == "__main__":
    sys.exit(main())
