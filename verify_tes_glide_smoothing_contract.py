"""verify_tes_glide_smoothing_contract.py

MACHINE CHECK for the bounded-glide contract (2026-09-20)
=========================================================

THE DEFECT THIS PINS
--------------------
The reported behaviour was:

    "defenders not involved in the contest must preserve role-depth
     authority, but converge via smooth glide.  The current implementation
     appears to re-target or correct too aggressively, creating visible
     jumps during defensive retreat."

Two of the glide authorities (``role_depth_glide``, ``attach_band``) wrote
a non-contest defender's ``x``/``y`` DIRECTLY and then rescaled its velocity
with ``vscale = CEILING / spd``.  That is an AMPLIFIER whenever
``spd < CEILING``: a defender backpedalling into its line at 1.0 m/s became
6.0 m/s for the frame the clamp fired, then dropped back on its release — a
two-sided velocity spike.  Both modules ALSO ran an unbounded Y slot-band
clamp AFTER the per-frame ceiling, so a body metres outside its band was
teleported onto the band edge in a single frame, defeating the very cap the
module docstring claimed to enforce.

THE CONTRACT ASSERTED HERE
--------------------------
  [1] ONE CEILING  — all five glide authorities share ONE constant, and the
      bounded-step primitives behave correctly.
  [2] NEVER AMPLIFY — a glide may only REDUCE speed to the ceiling; a body
      travelling below it is left byte-identical.
  [3] ONE FRAME BUDGET — the total displacement of every glide write in a
      frame (vector clamp + band correction combined) never exceeds
      ``ceiling * dt``.
  [4] CONVERGENCE, NOT TELEPORT — a body outside its band closes the gap
      over frames and reaches the band edge; it is never teleported.
  [5] ROLE-DEPTH AUTHORITY — the attach nudge never moves a body faster than
      the speed its own movement authority commanded (``move_speed``).
  [6] BOUNDED FROM FRAME ONE — the target_x glide caps on its first call
      instead of landing the uncapped writer swing.
  [7] LEGACY PARITY — with the gate off (``RLM_GLIDE_SMOOTH=0`` /
      ``tuners.glide_smooth_enabled = False``) every pre-fix behaviour is
      restored byte-for-byte, so this ships as a one-flag A/B.

Runs headless with stubbed bodies/world; no engine boot, no pygame window,
and therefore no dependence on the harness's known run-to-run
non-determinism (see the caveat recorded in defensive_tuners.py).

Usage:  python verify_tes_glide_smoothing_contract.py
Exit code 0 = contract holds, 1 = violation.
"""

from __future__ import annotations

import math
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
# Never let a stale lever from the caller's shell flip the A/B under test.
os.environ.pop("RLM_GLIDE_SMOOTH", None)

from game.defence.defence_lvl_1 import glide_budget as GB
from game.defence.defence_lvl_1 import role_depth_glide as RDG
from game.defence.defence_lvl_1 import attach_band as AB
from game.defence.defence_lvl_1 import target_x_glide as TXG
from game.managers import defence_manager as DM
from player.player import DefenderRole

DT = 1.0 / 60.0
CEILING = GB.GLIDE_SPEED_MPS
BUDGET = CEILING * DT
EPS = 1e-9

_FAILURES: list[str] = []
_COUNT = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _COUNT
    _COUNT += 1
    if cond:
        print(f"  [PASS] {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _FAILURES.append(name)


# ─────────────────────────────────────────────────────────────────────
# STUBS — plain objects carrying exactly the attributes the authorities read
# ─────────────────────────────────────────────────────────────────────

class Body:
    """A stand-in for ``player.player.Player`` (plain attribute bag)."""

    def __init__(self, pid: int, x: float, y: float, **kw) -> None:
        self.player_id = pid
        self.x = float(x)
        self.y = float(y)
        self.vx = 0.0
        self.vy = 0.0
        self.target_x = float(x)
        self.target_y = float(y)
        self.frozen = False
        self.freeze_timer = 0.0
        self.fullback_role_active = False
        self.depth_locked = False
        self._tes_hold_active = False
        self.move_speed = 2.5
        self.movement_state = None
        self.defender_role = DefenderRole.DEF_LINE_READY
        self.depth_role = "C"
        self.target_source = ""
        for key, value in kw.items():
            setattr(self, key, value)


class World:
    """Minimal world stub for the glide authorities."""

    def __init__(self, bc: Body | None = None) -> None:
        self.frame_count = 1
        self.dt = DT
        self.ball_carrier = bc
        self.tes_active = False


class Ope:
    """Minimal engine stub; ``_phase`` drives the tackle-begin window."""

    def __init__(self, phase: str = "TACKLE_INIT") -> None:
        self._phase = phase
        self.primary = None
        self.joiners: tuple = ()
        self._attached_player_slots: dict = {}
        self.tes_ctx = None


class Tuners:
    """Minimal DefenceTuners stub (only the attributes these modules read)."""

    def __init__(self, smooth: bool = True) -> None:
        self.glide_smooth_enabled = smooth
        self.tackle_begin_glide_enabled = True
        self.attach_slot_reserve_enabled = True
        self.tackle_assist_radius_m = 5.0
        self.tackle_snap_radius_m = 2.0
        self.joiner_grace_s = 0.1


def _spd(body: Body) -> float:
    return math.hypot(body.vx, body.vy)


# ─────────────────────────────────────────────────────────────────────
# [1] ONE CEILING + bounded-step primitives
# ─────────────────────────────────────────────────────────────────────

def section_one_ceiling() -> None:
    print("\n[1] ONE GLIDE CEILING + bounded-step primitives")

    check(
        "glide_budget.GLIDE_SPEED_MPS is the single 6.0 m/s ceiling",
        abs(GB.GLIDE_SPEED_MPS - 6.0) < EPS,
        f"got {GB.GLIDE_SPEED_MPS}",
    )
    check(
        "role_depth_glide declares no ceiling of its own",
        abs(RDG.TACKLE_BEGIN_GLIDE_SPEED_MPS - CEILING) < EPS,
        f"got {RDG.TACKLE_BEGIN_GLIDE_SPEED_MPS}",
    )
    check(
        "target_x_glide declares no ceiling of its own",
        abs(TXG.TARGET_X_GLIDE_SPEED_MPS - CEILING) < EPS,
        f"got {TXG.TARGET_X_GLIDE_SPEED_MPS}",
    )
    check(
        "defence_manager DEPTH_LOCK_GLIDE declares no ceiling of its own",
        abs(DM.DEPTH_LOCK_GLIDE_SPEED_MPS - CEILING) < EPS,
        f"got {DM.DEPTH_LOCK_GLIDE_SPEED_MPS}",
    )
    check(
        "frame budget == ceiling * dt",
        abs(GB.frame_budget(DT) - BUDGET) < EPS,
        f"got {GB.frame_budget(DT)}",
    )
    check(
        "frame budget floors a non-positive ceiling to zero (never unlimited)",
        GB.frame_budget(DT, 0.0) == 0.0,
    )
    check(
        "bounded_step never overshoots",
        abs(GB.bounded_step(0.0, 0.05, BUDGET) - 0.05) < EPS,
    )
    check(
        "bounded_step is capped at the budget",
        abs(GB.bounded_step(0.0, 1.0, BUDGET) - BUDGET) < EPS,
    )
    check(
        "remaining reports the unspent budget",
        abs(GB.remaining((0.0, 0.0), 0.03, 0.04, DT) - (BUDGET - 0.05)) < 1e-9,
        f"got {GB.remaining((0.0, 0.0), 0.03, 0.04, DT)}",
    )
    check(
        "remaining floors at zero once the budget is spent",
        GB.remaining((0.0, 0.0), 5.0, 0.0, DT) == 0.0,
    )


# ─────────────────────────────────────────────────────────────────────
# [2] NEVER AMPLIFY
# ─────────────────────────────────────────────────────────────────────

def section_never_amplify() -> None:
    print("\n[2] NEVER AMPLIFY — a glide only ever REDUCES speed")

    slow = Body(1, 0.0, 0.0)
    slow.vx, slow.vy = 1.0, 0.0
    touched = GB.cap_body_velocity(slow, CEILING)
    check(
        "a 1.0 m/s body is NOT scaled up to the 6.0 m/s ceiling",
        (not touched) and abs(slow.vx - 1.0) < EPS and abs(slow.vy) < EPS,
        f"vx={slow.vx} vy={slow.vy} touched={touched}",
    )

    fast = Body(2, 0.0, 0.0)
    fast.vx, fast.vy = 6.0, 8.0          # 10.0 m/s, retains direction
    touched = GB.cap_body_velocity(fast, CEILING)
    check(
        "a 10.0 m/s body is throttled DOWN to exactly the ceiling",
        touched and abs(_spd(fast) - CEILING) < 1e-9,
        f"spd={_spd(fast)}",
    )
    check(
        "throttling preserves direction",
        fast.vx > 0.0 and fast.vy > 0.0 and abs(fast.vx / fast.vy - 0.75) < 1e-9,
        f"vx={fast.vx} vy={fast.vy}",
    )


# ─────────────────────────────────────────────────────────────────────
# [3] + [4] ONE FRAME BUDGET, CONVERGENCE NOT TELEPORT (role_depth_glide)
# ─────────────────────────────────────────────────────────────────────

def section_role_depth_glide() -> None:
    print("\n[3] ONE FRAME BUDGET + [4] CONVERGENCE (role_depth_glide)")

    tuners = Tuners()
    ope = Ope()
    world = World()

    # (a) velocity amplification is gone: a slow clamped body keeps its speed.
    body = Body(10, 50.0, 35.0)
    body.vx, body.vy = 1.0, 0.0
    body.target_y = 35.0                       # no band error in this case
    RDG.enforce_tackle_begin_glide(
        world, [body], DT, tuners, ope, pre_pos={10: (49.0, 35.0)},
    )
    moved = math.hypot(body.x - 49.0, body.y - 35.0)
    check(
        "vector clamp holds the frame budget",
        abs(moved - BUDGET) < 1e-9,
        f"moved={moved}",
    )
    check(
        "clamped slow body is NOT accelerated (was vscale=6.0 amplification)",
        abs(_spd(body) - 1.0) < 1e-9 and body.vx > 0.0,
        f"spd={_spd(body)}",
    )

    # (b) the band correction is BOUNDED: clamped frame defers, it does not snap.
    body = Body(11, 50.0, 35.0)
    body.vx, body.vy = 1.0, 0.0
    body.target_y = 40.0                       # band = (39.7, 40.3)
    RDG.enforce_tackle_begin_glide(
        world, [body], DT, tuners, ope, pre_pos={11: (49.0, 35.0)},
    )
    total = math.hypot(body.x - 49.0, body.y - 35.0)
    check(
        "vector clamp + band clamp combined stay inside ONE ceiling",
        total <= BUDGET + 1e-9,
        f"total={total} budget={BUDGET}",
    )
    check(
        "band correction is DEFERRED when the budget is spent (no teleport)",
        abs(body.y - 35.0) < EPS,
        f"y={body.y} (legacy would have snapped to 39.7)",
    )

    # (b2) ... and it CONVERGES over frames once there is budget.
    body = Body(12, 50.0, 35.0)
    body.target_y = 40.0
    for _ in range(400):
        world.frame_count += 1
        RDG.enforce_tackle_begin_glide(
            world, [body], DT, tuners, ope,
            pre_pos={12: (body.x, body.y)},
        )
    check(
        "band error converges onto the band edge over frames",
        abs(body.y - 39.7) < 1e-6,
        f"y={body.y} (target band edge 39.7)",
    )

    # (c) LEGACY PARITY — the gate restores the pre-fix behaviour exactly.
    legacy = Tuners(smooth=False)
    body = Body(13, 50.0, 35.0)
    body.vx, body.vy = 1.0, 0.0
    body.target_y = 40.0
    RDG.enforce_tackle_begin_glide(
        world, [body], DT, legacy, ope, pre_pos={13: (49.0, 35.0)},
    )
    check(
        "LEGACY: vscale amplification restored when the gate is off",
        abs(_spd(body) - 6.0) < 1e-9,
        f"spd={_spd(body)}",
    )
    check(
        "LEGACY: unbounded band snap restored when the gate is off",
        abs(body.y - 39.7) < 1e-9,
        f"y={body.y}",
    )


# ─────────────────────────────────────────────────────────────────────
# [5] ROLE-DEPTH AUTHORITY RESPECTED (attach_band)
# ─────────────────────────────────────────────────────────────────────

def section_attach_band() -> None:
    print("\n[5] ROLE-DEPTH AUTHORITY + BUDGET (attach_band)")

    tuners = Tuners()
    ope = Ope()
    bc = Body(90, 54.0, 35.0)

    # (a) the nudge may never exceed the body's own commanded speed.
    world = World(bc)
    body = Body(20, 50.0, 35.0)              # 4.0 m from BC: inside R_assist,
    body.target_y = 35.0                      # outside R_tackle
    body.move_speed = 2.5
    body.vx, body.vy = 1.0, 0.0
    AB.enforce_attach_band(
        world, [body], DT, tuners, ope, bc=bc,
        pre_pos={20: (50.0, 35.0)}, glide_speed_mps=CEILING,
    )
    step = math.hypot(body.x - 50.0, body.y - 35.0)
    check(
        "nudge never exceeds the body's own commanded move_speed * dt",
        step <= (2.5 * DT) + 1e-9,
        f"step={step} allowed={2.5 * DT}",
    )
    check(
        "nudge is still strictly inward (toward the carrier)",
        body.x > 50.0 and step > 0.0,
        f"x={body.x}",
    )
    check(
        "nudged slow body is NOT accelerated (was vscale=6.0 amplification)",
        abs(_spd(body) - 1.0) < 1e-9,
        f"spd={_spd(body)}",
    )

    # (b) the nudge never stacks on top of movement already spent this frame.
    #     The 1.0 m of travel is PRE-EXISTING (the integrator did it before
    #     this pass runs); what must be proved is that the NUDGE adds nothing
    #     on top of a budget the frame has already spent — so measure the
    #     delta THIS CALL caused, not the pre-existing travel itself.
    world = World(bc)
    body = Body(21, 50.0, 35.0)
    body.target_y = 35.0
    body.move_speed = 2.5
    _x0, _y0 = body.x, body.y
    AB.enforce_attach_band(
        world, [body], DT, tuners, ope, bc=bc,
        pre_pos={21: (49.0, 35.0)},           # already travelled 1.0 m
        glide_speed_mps=CEILING,
    )
    added = math.hypot(body.x - _x0, body.y - _y0)
    check(
        "nudge adds nothing when the frame budget is already spent",
        added <= EPS,
        f"added={added} (a fresh budget would have added {BUDGET})",
    )
    check(
        "position stays bounded by pre-existing travel + the frame budget",
        math.hypot(body.x - 49.0, body.y - 35.0) <= (1.0 + BUDGET) + 1e-9,
    )

    # (c) the Y band clamp is bounded and converges.
    #     GEOMETRY MATTERS: the authority only manages bodies inside
    #     R_assist, so the carrier must sit BEYOND the band (bc.y = 39) and
    #     the body just below it, so converging on the band also moves TOWARD
    #     the carrier and the body stays inside the radius throughout.  (With
    #     the carrier at y=35, converging on a band 4.7 m away exits
    #     R_assist at y=38 and the authority correctly stops managing the
    #     body — which is the boundary asserted in (c3) below.)
    bc_c = Body(91, 54.0, 39.0)
    world = World(bc_c)
    body = Body(22, 50.0, 36.5)
    body.target_y = 40.0                      # band = (39.7, 40.3)
    body.move_speed = 0.0                     # isolate the band correction
    for _ in range(400):
        world.frame_count += 1
        AB.enforce_attach_band(
            world, [body], DT, tuners, ope, bc=bc_c,
            pre_pos={22: (body.x, body.y)}, glide_speed_mps=CEILING,
        )
    check(
        "attach band Y error converges onto the band edge over frames",
        abs(body.y - 39.7) < 1e-6,
        f"y={body.y}",
    )
    check(
        "band convergence never overshoots the band edge",
        body.y <= 39.7 + 1e-9,
        f"y={body.y}",
    )

    # (c2) NO COMMANDED SPEED => NO NUDGE.  A body whose own movement
    #      authority commanded zero speed must not be dragged toward the
    #      carrier: doing so re-targets a non-contest defender against the
    #      explicit instruction of the authority that owns it.
    world = World(bc)
    body = Body(24, 50.0, 35.0)
    body.target_y = 35.0                      # no band error: nudge only
    body.move_speed = 0.0
    _x0, _y0 = body.x, body.y
    AB.enforce_attach_band(
        world, [body], DT, tuners, ope, bc=bc,
        pre_pos={24: (50.0, 35.0)}, glide_speed_mps=CEILING,
    )
    check(
        "a zero commanded move_speed body is NOT nudged at all",
        math.hypot(body.x - _x0, body.y - _y0) <= EPS,
        f"moved={math.hypot(body.x - _x0, body.y - _y0)}",
    )

    # (c3) AUTHORITY BOUNDARY: outside R_assist the authority writes nothing
    #      at all — no nudge, and no silent pull back inside the radius.
    world = World(bc)
    body = Body(25, 50.0, 42.0)               # hypot(4, 7) = 8.06 > R_assist
    body.target_y = 40.0
    body.move_speed = 2.5
    _x0, _y0 = body.x, body.y
    AB.enforce_attach_band(
        world, [body], DT, tuners, ope, bc=bc,
        pre_pos={25: (50.0, 42.0)}, glide_speed_mps=CEILING,
    )
    check(
        "a body outside R_assist is not written at all",
        math.hypot(body.x - _x0, body.y - _y0) <= EPS,
        f"x={body.x} y={body.y}",
    )

    # (d) LEGACY PARITY.
    legacy = Tuners(smooth=False)
    world = World(bc)
    body = Body(23, 50.0, 35.0)
    body.target_y = 40.0
    body.move_speed = 2.5
    body.vx, body.vy = 1.0, 0.0
    AB.enforce_attach_band(
        world, [body], DT, legacy, ope, bc=bc,
        pre_pos={23: (50.0, 35.0)}, glide_speed_mps=CEILING,
    )
    check(
        "LEGACY: flat 6 m/s nudge restored when the gate is off",
        abs(body.x - (50.0 + BUDGET)) < 1e-9,
        f"x={body.x}",
    )
    check(
        "LEGACY: vscale amplification restored when the gate is off",
        abs(_spd(body) - 6.0) < 1e-9,
        f"spd={_spd(body)}",
    )
    check(
        "LEGACY: unbounded band snap restored when the gate is off",
        abs(body.y - 39.7) < 1e-9,
        f"y={body.y}",
    )


# ─────────────────────────────────────────────────────────────────────
# [6] BOUNDED FROM FRAME ONE (target_x_glide)
# ─────────────────────────────────────────────────────────────────────

def section_target_x_glide() -> None:
    print("\n[6] BOUNDED FROM FRAME ONE (target_x_glide)")

    tuners = Tuners()
    world = World()

    # (a) first call seeds from the body's OWN x and caps on that same call.
    body = Body(30, 50.0, 35.0)
    body.target_x = 60.0                      # a 10 m writer swing
    TXG.glide_cap_target_x(body, DT, tuners=tuners, world=world)
    check(
        "first call caps the swing from the body's own position",
        abs(body.target_x - (50.0 + BUDGET)) < 1e-9,
        f"target_x={body.target_x} (legacy would have landed 60.0)",
    )

    # (b) it then converges over frames.  In the engine the frame's writers
    #     RE-STAMP the raw target every frame and the glide caps whatever it
    #     finds, so the stub must do the same — otherwise the glide would be
    #     capping its own previous output and could never make progress.
    for _ in range(400):
        body.target_x = 60.0
        TXG.glide_cap_target_x(body, DT, tuners=tuners, world=world)
    check(
        "target_x converges onto the writer value over frames",
        abs(body.target_x - 60.0) < 1e-6,
        f"target_x={body.target_x}",
    )

    # (c) LEGACY PARITY — the gate restores the uncapped first frame.
    legacy = Tuners(smooth=False)
    body = Body(31, 50.0, 35.0)
    body.target_x = 60.0
    TXG.glide_cap_target_x(body, DT, tuners=legacy, world=world)
    check(
        "LEGACY: uncapped first frame restored when the gate is off",
        abs(body.target_x - 60.0) < EPS
        and abs(getattr(body, "_prev_target_x_glide", 0.0) - 60.0) < EPS,
        f"target_x={body.target_x}",
    )


# ─────────────────────────────────────────────────────────────────────
# [7] TUNER WIRING
# ─────────────────────────────────────────────────────────────────────

def section_tuner_wiring() -> None:
    print("\n[7] TUNER WIRING")

    from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

    tuners = DefenceTuners()
    check(
        "DefenceTuners exposes glide_smooth_enabled, default True",
        bool(getattr(tuners, "glide_smooth_enabled", False)) is True,
    )
    check(
        "glide_smooth_enabled reads the tuner when the world is silent",
        GB.glide_smooth_enabled(tuners, World()) is True,
    )
    tuners.glide_smooth_enabled = False
    check(
        "glide_smooth_enabled honours the tuner override",
        GB.glide_smooth_enabled(tuners, World()) is False,
    )
    tuners.glide_smooth_enabled = True
    world = World()
    world.glide_smooth_enabled = False
    check(
        "glide_smooth_enabled honours the world override",
        GB.glide_smooth_enabled(tuners, world) is False,
    )

    os.environ["RLM_GLIDE_SMOOTH"] = "0"
    try:
        check(
            "RLM_GLIDE_SMOOTH=0 is the runtime kill switch",
            GB.glide_smooth_enabled(tuners, World()) is False,
        )
    finally:
        os.environ.pop("RLM_GLIDE_SMOOTH", None)


def main() -> int:
    print("=" * 72)
    print("VERIFY: TES bounded-glide smoothing contract (2026-09-20)")
    print("=" * 72)
    section_one_ceiling()
    section_never_amplify()
    section_role_depth_glide()
    section_attach_band()
    section_target_x_glide()
    section_tuner_wiring()
    print("\n" + "=" * 72)
    if _FAILURES:
        print(f"RESULT: FAIL — {len(_FAILURES)}/{_COUNT} checks failed")
        for name in _FAILURES:
            print(f"  - {name}")
        return 1
    print(f"RESULT: PASS — {_COUNT}/{_COUNT} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
