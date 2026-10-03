"""verify_fullback_line_break_chase.py — the X-AXIS DEADLOCK contract
(frame-1814 telemetry, 2026-09-23).

THE DEFECT THIS PINS
--------------------
Frame 1814 of the live rollout shows the tagged fullback held STATIC at
``x = 63.80`` while the attacking ball carrier breaks the line and streams
downfield ``106.35 -> 111.49`` over a 40-frame window.  The fullback's
commanded target (``target_x``) never advances downfield, so no forward
velocity is produced and the last defender never initiates a pursuit phase.

WHY IT FREEZES
--------------
``apply_fullback_geometry`` (``fullback_role.py``) computes the sweeper
anchor as ``defensive_line_anchor_m(world) + depth``.  The line anchor
(``ruck_x + 10``) is LOCKED for the ruck and ``depth`` saturates at
``DEPTH_MAX_M = 30`` once the carrier passes halfway, so the anchor is a
per-ruck CONSTANT -- with a locked anchor of 33.80 it is 63.80 forever.

Two code paths then leave that constant standing as the target:

  1. The EMERGENCY-DEFENDER YIELD.  A genuine line break grants the tagged
     fullback tackle authority (the B2b path), and ``apply_fullback_geometry``
     then returns without assigning ``target_x`` at all -- the stale
     sweeper-anchor value is never cleared.
  2. The non-DEPTH (LATERAL) branch, which assigns ``target_x = anchor_x``
     unconditionally.

The dispatch's LAST X recovery write -- ``line_break_static_guard`` -- exists
precisely to clear a static post-break target, but it hard-exempts the tagged
fullback, so nothing else in the frame ever recovers it.

CONTRACT UNDER TEST (behaviour only)
------------------------------------
Once a GENUINE LINE BREAK exists, a tagged fullback's ``target_x`` must
actively ADVANCE downfield toward the carrier's projected interception point.
It may never be left pinned to the static sweeper anchor or to the body's own X.

Legs:
  [1] The frame-1814 signature reproduces the telemetry (anchor 33.80 ->
      static target 63.80 with the carrier at 106.35).
  [2] CONTROL -- the plain sweeper on a depth break advances every frame.
  [3] THE FAILURE -- the emergency-defender authority paths (lane stamp,
      armed/engaged, planted stance) must ALSO advance.
  [4] No regression -- off a break the sweeper still holds its depth.
  [5] No regression -- a LATERAL break still holds the sweeper's depth
      (the backfield anchor is not abandoned).
  [6] No regression -- a target that is NOT a static pin is left alone for
      an authority the release cannot withdraw (ENGAGED, and a lane holder
      INSIDE the intercept band).  A BEATEN lane holder's sentinel IS now
      claimed, because the 2026-09-27 BEATEN-OWNERSHIP-LOSS law withdraws
      that lane before this writer yields to it (see
      verify_fullback_beaten_ownership_loss.py).
  [7] A/B -- both levers off restores the historical freeze, and the
      ownership-loss law alone (static clearance off) still re-owns the body.
  [8] Source wiring.

Run:  python verify_fullback_line_break_chase.py
Exit code 0 = the pursuit clamp is cleared and the sweeper contract holds.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from player.movement_states import MovementState

from game.defence.defence_lvl_1 import fullback_role as fb
from game.defence.defence_lvl_1.fullback_role import (
    apply_fullback_geometry,
    compute_fullback_anchor,
    FB_CHASE_TRAIL_M,
    FB_ANCHOR_HYSTERESIS_M,
    FB_STATIC_TARGET_ANCHOR_EPS_M,
    FB_STATIC_TARGET_BODY_EPS_M,
    DEPTH_MAX_M,
)

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}"
          + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)
    return bool(condition)


def section(n, title: str) -> None:
    print(f"\n[{n}] {title}")


# ══════════════════════════════════════════════════════════════════════
# THE FRAME-1814 SCENARIO
# ══════════════════════════════════════════════════════════════════════

# The fullback's body X in the telemetry.
FB_STATIC_X: float = 63.80

# The carrier's end-to-end travel across the 40-frame window.
BC_START_X: float = 106.35
BC_END_X: float = 111.49
WINDOW_FRAMES: int = 40

# The lane both bodies occupy (the sweeper's own channel under open-play
# mirroring, so the break classifies on DEPTH, never on a Y breach).
LANE_Y: float = 35.0

# The LOCKED defensive line anchor that makes the sweeper anchor saturate at
# exactly FB_STATIC_X:  anchor_x = line_anchor + DEPTH_MAX_M.
LINE_ANCHOR_X: float = FB_STATIC_X - DEPTH_MAX_M   # 33.80

# Tolerance on "the target tracked the carrier" (metres).
TRACK_TOL_M: float = 1e-6


class _Ball:
    def __init__(self, x: float, y: float) -> None:
        self.x = x
        self.y = y


class _World:
    """Minimal world carrying the fields the FB authority reads."""

    def __init__(self, anchor_x: float = LINE_ANCHOR_X,
                 bc: _Ball | None = None, frame: int = 1) -> None:
        self.ruck_x_m = anchor_x - 10.0
        self.ruck_y_m = LANE_Y
        self.defensive_line_anchor = anchor_x
        self.anchor_locked = True
        self.ball_carrier = bc
        self.frame_count = frame
        self.tes_active = False
        self.in_kickoff = False
        self.fullback_recover_allowed = True


def make_fb(x: float = FB_STATIC_X, y: float = LANE_Y, pid: int = 201):
    from player.player import Player, DefenderRole

    p = Player(x=x, y=y, player_id=pid)
    p.jersey_number = 1
    p.max_speed_mps = 9.2
    p.max_defensive_speed_mps = 8.3
    p.fullback_role_active = True
    p.can_acquire_tackle = True
    p.defender_role = DefenderRole.DEF_LINE_READY
    p.tes_attached = False
    p.in_contact = False
    p.target_x = FB_STATIC_X
    p.target_y = LANE_Y
    return p


def _arm(defender, arm: str) -> None:
    """Grant the emergency-defender authority named by *arm*.

    The role and stance variants model stale pre-TES stamps; only the
    ``engaged`` variant transfers movement ownership to TES.
    """
    if arm == "lane":
        defender.tackle_role = "PRIMARY"
    elif arm == "engaged":
        fb.set_fullback_emergency_engaged(defender)
    elif arm == "stance":
        defender.pre_tackle_stance = True


def run_break_chase(arm: str = "none", pin_x: float | None = FB_STATIC_X,
                    start_x: float = BC_START_X, end_x: float = BC_END_X,
                    frames: int = WINDOW_FRAMES, bc_y: float = LANE_Y,
                    anchor_x: float = LINE_ANCHOR_X):
    """Drive *frames* of the real geometry writer; return the per-frame trace.

    Returns ``(bc_xs, target_xs, states)`` -- one entry per frame, in order.
    """
    bc = _Ball(start_x, bc_y)
    world = _World(anchor_x=anchor_x, bc=bc, frame=1)
    defender = make_fb()
    if pin_x is not None:
        defender.target_x = pin_x
    _arm(defender, arm)

    step = (end_x - start_x) / (frames - 1) if frames > 1 else 0.0
    bc_xs: list[float] = []
    target_xs: list[float] = []
    states: list[str | None] = []
    for index in range(frames):
        bc.x = start_x + step * index
        world.frame_count = index + 1
        apply_fullback_geometry(world, defender)
        bc_xs.append(float(bc.x))
        target_xs.append(float(defender.target_x))
        states.append(fb.movement_state_name(defender))
    return bc_xs, target_xs, states


def _report_trace(arm: str, bc_xs, target_xs, states) -> None:
    """Print the frozen-frame evidence for a failing leg."""
    frozen = [
        i for i in range(1, len(target_xs))
        if target_xs[i] <= target_xs[i - 1] + 1e-12
    ]
    print(f"    arm={arm!r} frames={len(target_xs)} "
          f"tx[0]={target_xs[0]:.3f} tx[-1]={target_xs[-1]:.3f} "
          f"bc[-1]={bc_xs[-1]:.3f} state[-1]={states[-1]!r} "
          f"non-advancing_frames={len(frozen)}")
    if frozen:
        print(f"    first 8 frozen frame indices: {frozen[:8]}")


def _assert_advances(arm: str, bc_xs, target_xs, states) -> None:
    """The contract: after the break, tx must advance with the carrier."""
    carrier_travel = bc_xs[-1] - bc_xs[0]
    tx_travel = target_xs[-1] - target_xs[0]
    pursuit_behind = 0.0

    check(f"[{arm}] the writer enters the pursuit phase (CHASE)",
          states[-1] == "CHASE",
          f"state={states[-1]!r}")

    non_advancing = [
        i for i in range(1, len(target_xs))
        if target_xs[i] <= target_xs[i - 1] + 1e-12
    ]
    check(f"[{arm}] target_x advances on EVERY frame of the break",
          not non_advancing,
          f"non-advancing frames={non_advancing[:8]}")

    check(f"[{arm}] target_x travel tracks the carrier travel",
          abs(tx_travel - carrier_travel) < TRACK_TOL_M,
          f"tx_travel={tx_travel:.4f} carrier_travel={carrier_travel:.4f}")

    check(f"[{arm}] target_x is NOT the static sweeper anchor (63.80)",
          abs(target_xs[-1] - FB_STATIC_X) > 1.0,
          f"tx[-1]={target_xs[-1]:.4f}")

    check(f"[{arm}] the whole window moved (no per-ruck constant)",
          (max(target_xs) - min(target_xs)) > 4.0,
          f"span={max(target_xs) - min(target_xs):.4f}")

    check(f"[{arm}] a stationary carrier is not pursued from behind",
          abs(target_xs[-1] - bc_xs[-1]) < 1e-9,
          f"tx[-1]={target_xs[-1]:.4f} expected="
          f"{bc_xs[-1]:.4f}")

    if not non_advancing:
        pursuit_behind = target_xs[-1] - target_xs[0]
    check(f"[{arm}] the pursuit phase actually happened",
          pursuit_behind > 0.0, f"{pursuit_behind:.4f} m commanded")


# ══════════════════════════════════════════════════════════════════════
# [1] THE FRAME-1814 SIGNATURE
# ══════════════════════════════════════════════════════════════════════

def sec_signature() -> None:
    section(1, "The frame-1814 signature reproduces the telemetry")
    bc = _Ball(BC_START_X, LANE_Y)
    anchor_x, anchor_y = compute_fullback_anchor(bc, LINE_ANCHOR_X)
    check("the locked line anchor yields the static target 63.80",
          abs(anchor_x - FB_STATIC_X) < 1e-9,
          f"anchor_x={anchor_x:.4f}")
    check("the static target is the SATURATED depth ceiling",
          abs((anchor_x - LINE_ANCHOR_X) - DEPTH_MAX_M) < 1e-9,
          f"depth={anchor_x - LINE_ANCHOR_X:.4f}")
    check("the carrier starts 42.55 m past the fullback's own anchor",
          abs((BC_START_X - FB_STATIC_X) - 42.55) < 1e-9,
          f"gap={BC_START_X - FB_STATIC_X:.2f} m")
    check("the carrier is on the sweeper's own lane (a DEPTH break)",
          abs(anchor_y - LANE_Y) < 1e-9 and BC_START_X > LINE_ANCHOR_X + 1.0,
          f"anchor_y={anchor_y:.4f}")
    check("the clearance constants are the documented pins",
          abs(FB_STATIC_TARGET_ANCHOR_EPS_M - 0.1) < 1e-12
          and abs(FB_STATIC_TARGET_BODY_EPS_M - 0.05) < 1e-12,
          f"anchor_eps={FB_STATIC_TARGET_ANCHOR_EPS_M} "
          f"body_eps={FB_STATIC_TARGET_BODY_EPS_M}")


# ══════════════════════════════════════════════════════════════════════
# [2] CONTROL — THE PLAIN SWEEPER ADVANCES
# ══════════════════════════════════════════════════════════════════════

def sec_sweeper() -> None:
    section(2, "CONTROL — the plain sweeper on a depth break")
    trace = run_break_chase(arm="none")
    _report_trace("none", *trace)
    _assert_advances("none", *trace)


# ══════════════════════════════════════════════════════════════════════
# [3] THE FAILURE — THE EMERGENCY-DEFENDER AUTHORITY PATHS
# ══════════════════════════════════════════════════════════════════════

def sec_authority_paths() -> None:
    section(3, "The emergency-defender authority paths must ALSO advance")
    for arm in ("lane", "engaged", "stance"):
        trace = run_break_chase(arm=arm)
        _report_trace(arm, *trace)
        _assert_advances(arm, *trace)


# ══════════════════════════════════════════════════════════════════════
# [4] NO REGRESSION — OFF A BREAK THE SWEEPER HOLDS ITS DEPTH
# ══════════════════════════════════════════════════════════════════════

def sec_off_break() -> None:
    section(4, "Off a break the sweeper still holds its backfield depth")
    bc_xs, target_xs, states = run_break_chase(
        arm="none", start_x=LINE_ANCHOR_X - 6.0, end_x=LINE_ANCHOR_X - 5.0,
    )
    # Off a break the command is the SWEEPER anchor, whose depth still lerps
    # with the carrier's x -- not the saturated frame-1814 constant.
    expected_x, _expected_y = compute_fullback_anchor(
        _Ball(bc_xs[-1], LANE_Y), LINE_ANCHOR_X
    )
    check("no break: the command is the sweeper depth anchor, within the "
          "documented anchor-timing hysteresis band",
          abs(target_xs[-1] - expected_x) <= FB_ANCHOR_HYSTERESIS_M + 1e-9,
          f"tx[-1]={target_xs[-1]:.4f} anchor={expected_x:.4f} "
          f"band={FB_ANCHOR_HYSTERESIS_M}")
    check("no break: the writer never enters the pursuit phase",
          states[-1] != "CHASE", f"state={states[-1]!r}")
    check("no break: the sweeper keeps a sweeper state (DRIFT / RECOVER)",
          states[-1] in ("DRIFT", "RECOVER"), f"state={states[-1]!r}")
    check("no break: the command holds depth behind the line, not on the BC",
          all(t > bx for t, bx in zip(target_xs, bc_xs)),
          f"min_tx={min(target_xs):.4f} max_bc={max(bc_xs):.4f}")


# ══════════════════════════════════════════════════════════════════════
# [5] NO REGRESSION — A LATERAL BREAK HOLDS THE SWEEPER'S DEPTH
# ══════════════════════════════════════════════════════════════════════

def sec_lateral_break() -> None:
    section(5, "A LATERAL break still holds the sweeper's depth")
    # A self-contained lateral geometry: the carrier is LEVEL with the line
    # in X (so it is not a DEPTH break), 10 m off the sweeper's lane, and
    # inside the sweeper's 2D reach.
    line_x = 40.0
    fb_x, fb_y = 50.0, 35.0
    bc = _Ball(line_x, fb_y + 10.0)
    world = _World(anchor_x=line_x, bc=bc, frame=1)
    defender = make_fb(x=fb_x, y=fb_y)
    from game.defence.defence_lvl_1.fullback_role import classify_fullback_break
    expected_x, anchor_y = compute_fullback_anchor(bc, line_x)
    kind = classify_fullback_break(bc, defender, line_x, anchor_y)
    check("the level, off-lane carrier classifies as a LATERAL break",
          kind == fb.FB_BREAK_LATERAL, str(kind))
    apply_fullback_geometry(world, defender)
    check("a lateral break does NOT abandon the backfield depth",
          abs(defender.target_x - expected_x) < 1e-9,
          f"tx={defender.target_x:.4f} anchor={expected_x:.4f}")
    check("the lateral branch does not charge the carrier downfield",
          defender.target_x > float(bc.x) - FB_CHASE_TRAIL_M,
          f"tx={defender.target_x:.4f} bc={bc.x:.4f}")
    check("the lateral break still tracks the carrier's lane in Y",
          abs(defender.target_y - bc.y) < 1e-9,
          f"ty={defender.target_y:.4f} lane={bc.y:.4f}")


def sec_intercept_projection() -> None:
    section(6, "A moving carrier is intercepted on its projected XY path")
    bc = _Ball(70.0, LANE_Y)
    bc.vx = 4.0
    bc.vy = 3.0
    defender = make_fb(x=40.0, y=20.0)
    horizon = fb._fullback_lead_horizon_s(defender, bc.x, bc.y)
    expected_x = bc.x + bc.vx * horizon
    expected_y = bc.y + bc.vy * horizon

    intercept_x, intercept_y = fb.fullback_chase_point(
        bc, defender, LINE_ANCHOR_X, fb.FB_BREAK_DEPTH,
    )
    check("the depth chase leads toward the future tryline position",
          abs(intercept_x - expected_x) < 1e-9
          and intercept_x > bc.x,
          f"tx={intercept_x:.4f} future-trail={expected_x:.4f}")
    check("the depth chase anticipates the future lateral lane",
          abs(intercept_y - expected_y) < 1e-9 and intercept_y > bc.y,
          f"ty={intercept_y:.4f} future-y={expected_y:.4f}")

    lateral_x, lateral_y = fb.fullback_chase_point(
        bc, defender, intercept_x + 1.0, fb.FB_BREAK_LATERAL,
    )
    check("the lateral chase keeps its backfield depth floor",
          lateral_x >= intercept_x + 1.0 and lateral_y > bc.y,
          f"tx={lateral_x:.4f} floor={intercept_x + 1.0:.4f}")

    bc.x = 100.0
    bc.vx = 9.0
    bc.vy = 0.0
    tryline_x, tryline_y = fb.fullback_chase_point(
        bc, defender, LINE_ANCHOR_X, fb.FB_BREAK_DEPTH,
    )
    check("a fast break 10 m out sends the fullback into the tryline area",
          tryline_x > bc.x and tryline_x <= fb.FIELD_MAX_X,
          f"target_x={tryline_x:.4f} carrier_x={bc.x:.4f}")
    check("the tryline interception stays on the projected carrier lane",
          abs(tryline_y - bc.y) < 1e-9,
          f"target_y={tryline_y:.4f} carrier_y={bc.y:.4f}")

    saved_intercept = fb.FB_CHASE_INTERCEPT
    try:
        fb.FB_CHASE_INTERCEPT = False
        legacy_x, legacy_y = fb.fullback_chase_point(
            bc, defender, LINE_ANCHOR_X, fb.FB_BREAK_DEPTH,
        )
    finally:
        fb.FB_CHASE_INTERCEPT = saved_intercept
    check("the rollback still uses the historical live-position trail",
          (legacy_x, legacy_y) == (bc.x - FB_CHASE_TRAIL_M, bc.y),
          f"target=({legacy_x:.4f}, {legacy_y:.4f})")


# ══════════════════════════════════════════════════════════════════════
# [6] NO REGRESSION — A LIVE TARGET IS NEVER OVERWRITTEN
# ══════════════════════════════════════════════════════════════════════

def sec_live_target_untouched() -> None:
    section(7, "A target that is NOT a static pin is left alone")
    # Already closing on the carrier: the writer must not claim the body.
    live_x = BC_START_X
    for arm in ("engaged", "lane"):
        bc = _Ball(BC_START_X, LANE_Y)
        world = _World(anchor_x=LINE_ANCHOR_X, bc=bc, frame=1)
        defender = make_fb()
        defender.target_x = live_x
        defender.target_y = LANE_Y
        _arm(defender, arm)
        apply_fullback_geometry(world, defender)
        check(f"[{arm}] a live chase target is not overwritten",
              abs(defender.target_x - live_x) < 1e-9,
              f"tx={defender.target_x:.4f}")

    # ── 2026-09-27 NARROWING OF THE YIELD ──
    # The lane arm on THIS fixture (FB 63.80, carrier 106.35) is a BEATEN
    # body: the carrier is 42.55 m past it.  The ownership-loss law therefore
    # withdraws that lane stamp, the authority stops yielding, and the
    # breakaway-chase branch re-owns the body -- which lands on the SAME
    # pursuit point as the live target below, so the value is unchanged while
    # the OWNER changes.
    bc = _Ball(BC_START_X, LANE_Y)
    world = _World(anchor_x=LINE_ANCHOR_X, bc=bc, frame=1)
    beaten_lane = make_fb()
    beaten_lane.target_x = live_x
    beaten_lane.target_y = LANE_Y
    _arm(beaten_lane, "lane")
    apply_fullback_geometry(world, beaten_lane)
    check("a BEATEN lane holder's lane stamp is withdrawn before the yield",
          beaten_lane.tackle_role is None, str(beaten_lane.tackle_role))
    check("...and the geometric writer owns the body again",
          beaten_lane.target_source == "fullback_geometry"
          and fb.movement_state_name(beaten_lane) == "CHASE",
          f"src={beaten_lane.target_source}")

    # An out-of-band sentinel (neither the anchor nor the body's own X) is
    # NEVER a static pin.  For an authority the release cannot withdraw
    # (ENGAGED) the writer still yields and the sentinel survives.  For the
    # BEATEN lane holder it does NOT: the ownership was withdrawn, so this
    # writer re-claims the command outright.
    for arm in ("engaged",):
        bc = _Ball(BC_START_X, LANE_Y)
        world = _World(anchor_x=LINE_ANCHOR_X, bc=bc, frame=1)
        defender = make_fb()
        defender.target_x = -999.0
        defender.target_y = -999.0
        _arm(defender, arm)
        apply_fullback_geometry(world, defender)
        check(f"[{arm}] an out-of-band target still yields untouched",
              defender.target_x == -999.0 and defender.target_y == -999.0,
              f"target=({defender.target_x}, {defender.target_y})")

    bc = _Ball(BC_START_X, LANE_Y)
    world = _World(anchor_x=LINE_ANCHOR_X, bc=bc, frame=1)
    beaten_sentinel = make_fb()
    beaten_sentinel.target_x = -999.0
    beaten_sentinel.target_y = -999.0
    _arm(beaten_sentinel, "lane")
    apply_fullback_geometry(world, beaten_sentinel)
    check("a BEATEN lane holder's out-of-band target IS re-claimed "
          "(the ownership was withdrawn, not yielded)",
          abs(beaten_sentinel.target_x - live_x) < 1e-9
          and abs(beaten_sentinel.target_y - LANE_Y) < 1e-9,
          f"tx={beaten_sentinel.target_x:.4f}")

    # CONTROL: a fullback remains owned by its geometry controller even when
    # a stale PRIMARY stamp is inside the intercept band.
    line_x = 40.0
    ctrl_bc = _Ball(45.0, LANE_Y)
    ctrl_world = _World(anchor_x=line_x, bc=ctrl_bc, frame=1)
    ctrl = make_fb(x=43.0, y=LANE_Y)
    ctrl.target_x = -999.0
    ctrl.target_y = -999.0
    _arm(ctrl, "lane")
    apply_fullback_geometry(ctrl_world, ctrl)
    check("CONTROL: a lane owner INSIDE the band keeps its stamp",
          ctrl.tackle_role == "PRIMARY", str(ctrl.tackle_role))
    check("CONTROL: geometry reclaims the command before TES",
          ctrl.target_x != -999.0 and ctrl.target_y != -999.0,
          f"target=({ctrl.target_x}, {ctrl.target_y})")


# ══════════════════════════════════════════════════════════════════════
# [7] SOURCE WIRING
# ══════════════════════════════════════════════════════════════════════

def sec_ab_lever() -> None:
    section(8, "A/B — legacy lane stamps cannot restore FB movement ownership")
    saved_static = fb.FB_STATIC_PURSUIT_CLEAR
    saved_beaten = fb.FB_BEATEN_OWNERSHIP
    try:
        # With either legacy lever setting, a selector role is not movement
        # ownership; the geometry controller continues the break chase.
        fb.FB_STATIC_PURSUIT_CLEAR = False
        fb.FB_BEATEN_OWNERSHIP = False
        _bc, tx_frozen, _st_frozen = run_break_chase(arm="lane")

        # ARM 2 — THE NEW LAW ALONE.  The legacy static-pursuit clearance is
        # still off, but the ownership-loss stage is on: the release withdraws
        # the lane stamp and the breakaway-chase branch re-owns the body, so
        # the freeze is gone WITHOUT any help from the 2026-09-26
        # establishment.  This is what makes the two stages independent.
        fb.FB_BEATEN_OWNERSHIP = True
        _bc2, tx_beaten_only, _st_beaten_only = run_break_chase(arm="lane")

        # ARM 3 — BOTH STAGES ON (the shipped default).
        fb.FB_STATIC_PURSUIT_CLEAR = True
        _bc3, tx_clear, _st_clear = run_break_chase(arm="lane")
    finally:
        fb.FB_STATIC_PURSUIT_CLEAR = saved_static
        fb.FB_BEATEN_OWNERSHIP = saved_beaten

    check("BOTH STAGES OFF still preserve geometry-owned break chase",
          (max(tx_frozen) - min(tx_frozen)) > 4.0
          and _st_frozen[-1] == "CHASE",
          f"span={max(tx_frozen) - min(tx_frozen):.4f} "
          f"state={_st_frozen[-1]!r}")
    check("ownership law ON preserves the same controller-owned chase",
          (max(tx_beaten_only) - min(tx_beaten_only)) > 4.0
          and _st_beaten_only[-1] == "CHASE",
          f"span={max(tx_beaten_only) - min(tx_beaten_only):.4f} "
          f"state={_st_beaten_only[-1]!r}")
    check("BOTH STAGES ON clears it: the same scenario advances with the "
          "carrier",
          (max(tx_clear) - min(tx_clear)) > 4.0
          and abs(tx_clear[-1] - FB_STATIC_X) > 1.0,
          f"span={max(tx_clear) - min(tx_clear):.4f} "
          f"tx[-1]={tx_clear[-1]:.4f}")
    check("the geometry-owned chase is stable across all legacy lever states",
          abs(tx_clear[-1] - tx_frozen[-1]) < 1e-9
          and abs(tx_clear[-1] - tx_beaten_only[-1]) < 1e-9,
          f"off={tx_frozen[-1]:.4f} beaten={tx_beaten_only[-1]:.4f} "
          f"on={tx_clear[-1]:.4f}")
    check("the shipped defaults are ON",
          fb.FB_STATIC_PURSUIT_CLEAR is True
          and fb.FB_BEATEN_OWNERSHIP is True)


def sec_wiring() -> None:
    section(9, "Source wiring — the clearance lives in the target loop")
    with open("game/defence/defence_lvl_1/fullback_role.py",
              encoding="utf-8") as handle:
        src = handle.read()
    check("fullback_role defines the static-pin clearance",
          "def clear_static_pursuit_target" in src)
    check("the geometry writer calls the clearance on the yield path",
          src.count("clear_static_pursuit_target(") >= 2)
    check("the clearance documents the frame-1814 deadlock",
          "frame-1814" in src.lower() or "frame 1814" in src.lower())
    check("the clearance tracks the pursuit point continuously",
          "FB_PURSUIT_SNAP_TOLERANCE_M" in src)
    check("the clearance honours this module's command envelope",
          "def is_fullback_command_target" in src)
    check("the lateral branch keeps the goal-side pursuit floor",
          "anchor_x, float(bc.x) - FB_CHASE_TRAIL_M" in src)
    check("the emergency-defender yield is still present (tag law intact)",
          "fullback_tackle_authority_active(defender)" in src)


def main() -> int:
    print("FULLBACK LINE-BREAK CHASE CONTRACT (frame-1814, 2026-09-23)")
    print("=" * 68)
    sec_signature()
    sec_sweeper()
    sec_authority_paths()
    sec_off_break()
    sec_lateral_break()
    sec_intercept_projection()
    sec_live_target_untouched()
    sec_ab_lever()
    sec_wiring()
    print("\n" + "=" * 68)
    if FAILURES:
        print(f"RESULT: FAIL ({len(FAILURES)} check(s))")
        for label in FAILURES:
            print(f"  - {label}")
        return 1
    print("RESULT: PASS — the line-break pursuit clamp is cleared: a tagged")
    print("        fullback projects XY toward the carrier's future position,")
    print("        continues advancing on every authority path and preserves")
    print("        the sweeper's lateral-break depth floor.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
