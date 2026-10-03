"""tes_depth_authority.py — THE TES DEPTH AUTHORITY (Stage 2, 2026-09-18).

DEFINITION (Stage 2 — the successor to the TES Depth Obligation)
----------------------------------------------------------------
TES Depth Authority is the direct successor to the TES Depth Obligation.
Any non-exempt defender inside the TES chamber whose depth debt exceeds
the obligation tolerance grants temporary locomotion authority to the
depth-discharge system.  While that authority is active the system owns
X-axis closure toward the canonical role depth target and applies
gap-proportional convergence until the debt falls within tolerance, at
which point the authority is immediately released.  No latching,
hysteresis, cooldowns, targets or persistent state are introduced in
Stage 2; the authority remains a pure, phase-gated read of the Stage 1
obligation truth and exists solely to ensure TES depth debts can
discharge.

WHAT THIS MODULE IS, AND WHAT IT DELIBERATELY IS NOT
----------------------------------------------------
Stage 1 (``tes_depth_obligation``) DEFINED the debt and the obligation
verdict; it carries no authority.  Stage 2 is the first WRITER: it turns
the obligation into locomotion so the debt can actually discharge.

This module is the exact counterpart of the shipped retreat pair —
``retreat_obligation.is_retreat_obligated`` (veto) +
``retreat_locomotion.retreat_debt_m`` (debt) +
``enforce_retreat_obligation`` (phase-gated authority).  It is the
``enforce_*`` half of the TES depth pair.

It carries NO latch, NO hysteresis, NO cooldown, NO re-arm, NO target
store and NO persistent attribute.  "Granted" and "released" are not
state transitions here: the authority is re-derived from scratch every
frame by reading ``is_tes_depth_obligated`` (Stage 1).  A body is under
the authority on exactly the frames its Stage-1 verdict is True, and is
released the instant that verdict goes False (debt back inside
tolerance).  There is nothing to clear, arm, or remember.

THE ONE LAW (gap-proportional convergence toward a MOVING target)
-----------------------------------------------------------------
    target depth  =  tes_depth_target_x(world, d)      (Stage 1, canonical)
    depth debt    =  tes_depth_debt_m(world, d)        (Stage 1, unsigned)
    closure speed =  target_motion + gain * debt       (this module)

where ``target_motion`` is the CANONICAL TARGET'S OWN MOTION (the live
carrier's X velocity) and ``gain`` is ``tes_depth_authority_gain_per_s``.
The result is floored at ``tes_depth_authority_min_speed_mps`` and capped
at the unified defensive ceiling.

WHY THE TARGET'S MOTION IS FED FORWARD
--------------------------------------
The role depth is ``bc.x + WIDTH_OFFSETS[depth_role]`` — it MOVES with
the driven carrier.  A purely proportional term cannot track a moving
target: it settles at a steady-state error of ``carrier_speed / gain``,
so a body pacing a 2.5 m/s drive with gain 1.2 would park at ~2.1 m of
debt for as long as the drive lasts and never discharge.  The
feed-forward is what makes the debt return under tolerance while the
carrier is still moving, which is the entire purpose of the authority.
It is a pure per-frame read of ``bc.vx`` — the same live value the
structure hold already reads — not a stored quantity.  The CLOSURE
component remains strictly proportional to the gap, which is the
convergence law the Stage-2 definition names; the feed-forward is
disableable (``tes_depth_authority_track_target``) so the
pure-proportional variant stays a one-flag A/B for measurement.

THE FREEZE CONTRACT GATES WHICH PHASES ACTUALLY MOVE
----------------------------------------------------
The authority reads the Stage-1 chamber, which INCLUDES ``tackle_init``.
It writes NOTHING for a body the sole freeze authority has pinned: in
TACKLE_INIT the line roles are ``must_be_frozen`` (and DEF_RETREAT_10 is
``must_lock_depth``), so a frozen body has no locomotion to grant and the
authority is a clean no-op on that frame.  The debt incurred at
TACKLE_INIT is therefore MEASURED (Stage 1) and CLOSED as soon as the
body is a legal mover (CONTACT onward, where the line roles are
``allowed_to_move``).  Stage 2 never overrides the freeze authority to
manufacture movement.

WHAT IT WRITES (exactly four fields, all per-frame commands)
------------------------------------------------------------
    target_x       = the canonical role depth target (Stage 1)
    move_speed     = the gap-proportional convergence speed (this module)
    movement_state = TES_DEPTH_AUTHORITY_MOVE_STATE (the accel-lerp path)
    target_source  = "tes_depth_authority" (attribution)

WHY THE INTEGRATION PATH IS AN ACCEL-LERP STATE, NOT DRIFT
----------------------------------------------------------
`move_speed` is only a CAP for the smooth-damp port: DRIFT/HOLD/SLIDE
route through `Player._uses_smooth_damp()`, whose critically-damped output
delivers only a FRACTION of the commanded speed on a long closure.
MEASURED IN-RUN (tmp_stage2_delivery.py, 11 699 granted body-frames):
the authority stamped 8.287 m/s and the body delivered 0.673 m/s -- 8.1%
of command, with 100% of granted frames below half command.  A command
the integrator throttles to a twelfth cannot discharge anything, which is
exactly why the first A/B showed no persistence improvement.

The engine's accel-lerp path (the states `_uses_smooth_damp` excludes)
reaches the commanded speed within a few frames.  This is the SAME fix the
shipped retreat pair applied for the SAME reason ("OPEN_PLAY closed at
0.360 m/s against a commanded 7.071 m/s").  The state here is only an
INTEGRATOR SELECTOR -- this authority never writes `defender_role`, and
TES membership is role/registry driven, so the selector carries no
membership meaning and leaks nothing across frames (the structure hold and
the OPEN_PLAY pipeline restamp the state every frame).

It NEVER touches ``target_y`` (lateral is owned by the spacing /
structure-hold authorities), ``vx``/``vy`` (the integrator owns velocity),
``frozen`` (the sole freeze authority owns it), or ``depth_lock_*``.  A
body it does not grant is written NOTHING, so a compliant frame is
byte-identical to the pre-Stage-2 engine.

WRITER-OWNERSHIP BOUNDARY (why the caller passes the structure pool)
--------------------------------------------------------------------
The depth authority is invoked by the TES drive authority over the SAME
body pool the structure hold owns — the non-tackler line bodies that are
not already owned by the pre-tackle pile authority.  That is a write
boundary, not a change to who is obligated: Stage 1's exemption set is
read verbatim and never redefined here.  A body at the pile is owned by
``pre_tackle_authority`` (a higher-priority TES writer), exactly as the
structure hold already defers to it; two authorities never fight for one
body's X.

IMPORT SAFETY
-------------
The only module-scope project import is ``tes_depth_obligation``, which
imports stdlib only at module scope (its project reaches are
function-local), so this module introduces no cycle and may be imported
in any order.  The ``MovementState`` import mirrors ``tes_drive_structure``.
"""

from __future__ import annotations

import math

from player.movement_states import MovementState

from game.defence.defence_lvl_1.tes_depth_obligation import (
    TES_DEPTH_OBLIGATION_PHASES,
    is_tes_depth_obligated,
    tes_depth_debt_m,
    tes_depth_target_x,
)

__all__ = [
    "TES_DEPTH_AUTHORITY_SOURCE",
    "TES_DEPTH_AUTHORITY_PHASES",
    "DEFAULT_CONVERGENCE_GAIN_PER_S",
    "DEFAULT_MIN_SPEED_MPS",
    "DEFAULT_MAX_SPEED_MPS",
    "TES_DEPTH_AUTHORITY_MOVE_STATE",
    "depth_authority_enabled",
    "is_tes_depth_authority_active",
    "tes_depth_convergence_mps",
    "apply_tes_depth_authority",
    "format_depth_authority_report",
]

# ── THE AUTHORITY'S STAMP ────────────────────────────────────────────
# The ``target_source`` an authority-granted body carries for the frame.
# Registered in ``phase_authority_guard._TES_OWNED_SOURCES`` so the
# conductor-model phase audit accepts it during the TES chamber.
TES_DEPTH_AUTHORITY_SOURCE: str = "tes_depth_authority"

# ── THE CHAMBER (single-sourced from Stage 1, never re-declared) ─────
# The authority acts inside the Stage-1 obligation window.  Aliased rather
# than copied so the two can never drift; the contract verifier asserts
# the identity.
TES_DEPTH_AUTHORITY_PHASES: frozenset = TES_DEPTH_OBLIGATION_PHASES

# ── THE CONVERGENCE LAW'S DEFAULTS ───────────────────────────────────
# gain_per_s: metres/second of closure commanded per metre of debt.  The
# closure component is strictly proportional to the gap, so the body
# decelerates smoothly into the role depth instead of overshooting it.
DEFAULT_CONVERGENCE_GAIN_PER_S: float = 1.5

# min_speed_mps: the floor.  Defaults to the live TES structure-hold jog
# speed so the authority can never command a body SLOWER than the hold it
# replaces — that is what makes Stage 2 monotone (never worse) for small
# debts by construction.
DEFAULT_MIN_SPEED_MPS: float = 2.5

# max_speed_mps: the cap.  0.0 means "no cap of its own" — the unified
# defensive ceiling (``max_defensive_speed_mps``) is then the cap, so this
# authority obeys the same single top-speed law as every other writer.
DEFAULT_MAX_SPEED_MPS: float = 0.0

# Fallback ceiling when no tuner is reachable.  Mirrors
# DefenceTuners.max_defensive_speed_mps so a bare caller degrades to the
# same number rather than inventing one.
DEFAULT_DEFENSIVE_CEILING_MPS: float = 8.3

# ── THE INTEGRATION PATH (the accel-lerp selector) ───────────────────
# The movement state the authority stamps while it owns a body's X.  This
# is an INTEGRATOR SELECTOR, not a behavioural role: the engine's
# `Player._uses_smooth_damp()` returns False for this state, so the
# commanded `move_speed` is reached within a few frames instead of being
# throttled to a fraction of command by the critically-damped port (see
# the measured 8.1% delivery in the module docstring).  CHASE is used
# because it is the engine's generic "commanded speed" path and, unlike
# RETREAT, carries no retreat-contract semantics.
TES_DEPTH_AUTHORITY_MOVE_STATE = MovementState.CHASE


# ─────────────────────────────────────────────────────────────────────
# SMALL HELPERS
# ─────────────────────────────────────────────────────────────────────

def _finite(v) -> bool:
    """True when *v* is a real finite number (never a bool)."""
    if isinstance(v, bool):
        return False
    return isinstance(v, (int, float)) and math.isfinite(float(v))


def _resolve_tuners(world, tuners=None):
    """Return the live DefenceTuners (engine singleton when available).

    Mirrors ``tes_depth_obligation._resolve_tuners`` so the verdict (read
    through Stage 1) and the convergence constants are resolved from ONE
    tuner object on every frame.
    """
    if tuners is not None:
        return tuners
    ope = getattr(world, "open_play_engine", None)
    if ope is not None:
        t = getattr(ope, "def_tuners", None)
        if t is not None:
            return t
    t = getattr(world, "tuners", None)
    if t is not None:
        return t
    try:
        from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
        return DefenceTuners()
    except Exception:
        return None


def _tuner(tuners, name: str, default):
    """Read a tuner value safely, falling back to *default* if absent."""
    if tuners is None:
        return default
    value = getattr(tuners, name, None)
    if value is None:
        return default
    return value


def _float_tuner(tuners, name: str, default: float) -> float:
    """Numeric tuner read that can never raise on a malformed value."""
    try:
        return float(_tuner(tuners, name, default))
    except (TypeError, ValueError):
        return float(default)


def _live_bc(world):
    """The live ball carrier, or None."""
    return getattr(world, "ball_carrier", None)


# ─────────────────────────────────────────────────────────────────────
# 1. THE GATE
# ─────────────────────────────────────────────────────────────────────

def depth_authority_enabled(tuners) -> bool:
    """True when the TES depth authority is armed.

    Defaults to True so the convergence law is live unless a tuner or the
    runtime lever explicitly turns it off (which restores the pre-Stage-2
    behaviour byte-for-byte: the structure hold's flat jog speed).
    """
    import os
    if os.environ.get("RLM_TES_DEPTH_AUTHORITY", "") == "0":
        return False
    return bool(_tuner(tuners, "tes_depth_authority_enabled", True))


# ─────────────────────────────────────────────────────────────────────
# 2. THE VERDICT (a pure read of Stage 1, gated by the freeze contract)
# ─────────────────────────────────────────────────────────────────────

def is_tes_depth_authority_active(world, d, tuners=None) -> bool:
    """True while the depth-discharge system OWNS *d*'s X closure.

    Two conditions, both pure reads:

      1. ``is_tes_depth_obligated(world, d, tuners)`` — the Stage-1 truth
         (inside the TES chamber, owns a role depth, non-exempt, debt
         strictly above tolerance).
      2. NOT currently frozen — the sole freeze authority's verdict.  A
         frozen body has no locomotion to grant (TACKLE_INIT pins the
         line roles), so the authority is a no-op there by contract.

    This is the grant AND the release: there is no latch to set or clear,
    so the authority is held for exactly the frames this returns True.
    """
    if d is None:
        return False
    if bool(getattr(d, "frozen", False)):
        return False
    return bool(is_tes_depth_obligated(world, d, tuners))


# ─────────────────────────────────────────────────────────────────────
# 3. THE CONVERGENCE LAW
# ─────────────────────────────────────────────────────────────────────

def tes_depth_convergence_mps(world, d, debt_m: float,
                              tuners=None) -> float:
    """The commanded X-closure speed for a body carrying *debt_m* metres.

        speed = clamp( target_motion + gain * debt, floor, cap )

    ``target_motion`` is the canonical target's own X motion (the live
    carrier's |vx|) fed forward so a MOVING role depth can be tracked to
    zero error; it is disabled by ``tes_depth_authority_track_target``.
    ``gain * debt`` is the gap-proportional convergence term.  The floor
    defaults to the structure-hold jog speed (never slower than the hold
    it replaces) and the cap defaults to the unified defensive ceiling.

    Returns the floor for any body carrying real debt; the gap term is
    never negative.
    """
    gain = max(0.0, _float_tuner(
        tuners, "tes_depth_authority_gain_per_s",
        DEFAULT_CONVERGENCE_GAIN_PER_S,
    ))
    floor = max(0.0, _float_tuner(
        tuners, "tes_depth_authority_min_speed_mps", DEFAULT_MIN_SPEED_MPS,
    ))
    ceiling = _float_tuner(
        tuners, "max_defensive_speed_mps", DEFAULT_DEFENSIVE_CEILING_MPS,
    )
    cap_raw = _float_tuner(
        tuners, "tes_depth_authority_max_speed_mps", DEFAULT_MAX_SPEED_MPS,
    )
    if cap_raw <= 0.0 or cap_raw > ceiling:
        cap = ceiling
    else:
        cap = cap_raw

    try:
        debt = max(0.0, float(debt_m))
    except (TypeError, ValueError):
        debt = 0.0

    # ── GAP-PROPORTIONAL CONVERGENCE (the convergence law) ──
    closure = gain * debt

    # ── TARGET-MOTION FEED-FORWARD (track a moving role depth) ──
    track = 0.0
    if bool(_tuner(tuners, "tes_depth_authority_track_target", True)):
        bc = _live_bc(world)
        bc_vx = getattr(bc, "vx", None) if bc is not None else None
        if _finite(bc_vx):
            track = abs(float(bc_vx))

    speed = track + closure
    if speed < floor:
        speed = floor
    if speed > cap:
        speed = cap
    return float(speed)


# ─────────────────────────────────────────────────────────────────────
# 4. THE WRITER
# ─────────────────────────────────────────────────────────────────────

def apply_tes_depth_authority(world, defenders, tuners=None) -> list:
    """Grant X-closure authority to every obligated body this frame.

    Returns the list of bodies the authority acted on (the probe's wiring
    proof — an empty list across a whole rollout would mean the authority
    is nominal, not live).

    For each body: if it is not under the authority (Stage-1 verdict False,
    or the freeze authority has pinned it) it is written NOTHING.  If it
    IS, the four per-frame command fields are stamped and nothing else.
    Does NOT integrate — the caller's single ``Player.update`` loop does
    that, exactly once, so no body is ever double-integrated.
    """
    granted: list = []
    if world is None:
        return granted
    tuners = _resolve_tuners(world, tuners)
    if not depth_authority_enabled(tuners):
        return granted

    for d in (defenders or ()):
        if d is None:
            continue
        if not is_tes_depth_authority_active(world, d, tuners):
            continue
        target_x = tes_depth_target_x(world, d, tuners)
        if target_x is None or not _finite(target_x):
            continue
        # ── THE DEPTH BOUNDARY INVARIANT (defence boundary, 2026-09-19) ──
        # This authority may NEVER publish a depth-role X beyond the
        # defending team's own tryline.  The canonical role depth is
        # ``bc.x + WIDTH_OFFSETS[depth_role]``, which is unbounded in bc.x:
        # a carrier driven past x = 100 produced a WINGER target of 110-120 —
        # a command INTO the team's own in-goal, with the depth debt then
        # measured against that illegal target.  The post-tackle 10 m retreat
        # protection has always been tryline-clamped
        # (``anchor_authority.defensive_line_anchor_m``); this is the
        # equivalent invariant for the drive-era depth target, applied AT THE
        # WRITER so the guarantee does not depend on the law above it.  The
        # dynamic fullback is exempt (its backfield anchor is deliberately
        # allowed slightly in-goal) and a legal target passes through
        # unchanged, so a compliant frame stays byte-identical.  Imported
        # FUNCTION-LOCALLY to keep this module's documented module-scope
        # import set and introduce no new cycle.
        try:
            from game.defence.defence_lvl_1.depth_boundary_guard import (
                clamp_depth_target_x as _clamp_depth_target_x,
            )
            _bounded_x = _clamp_depth_target_x(world, d, target_x, tuners)
            if _bounded_x is not None and _finite(_bounded_x):
                target_x = float(_bounded_x)
        except Exception:
            pass
        debt = tes_depth_debt_m(world, d, target_x=target_x)
        speed = tes_depth_convergence_mps(world, d, debt, tuners)

        d.target_x = float(target_x)
        d.move_speed = float(speed)
        d.movement_state = TES_DEPTH_AUTHORITY_MOVE_STATE
        d.target_source = TES_DEPTH_AUTHORITY_SOURCE
        granted.append(d)
    return granted


# ─────────────────────────────────────────────────────────────────────
# 5. LOGGING (diagnostic only — never consumed by behaviour)
# ─────────────────────────────────────────────────────────────────────

def format_depth_authority_report(world, defenders, tuners=None) -> str:
    """Compact one-line rendering of the live depth authority.

    Diagnostic only.  Naming a body here proves the authority is
    reachable; an empty row means no body owed depth on this frame.
    """
    tuners = _resolve_tuners(world, tuners)
    rows = []
    for d in (defenders or ()):
        if d is None or not is_tes_depth_authority_active(world, d, tuners):
            continue
        debt = tes_depth_debt_m(world, d)
        speed = tes_depth_convergence_mps(world, d, debt, tuners)
        rows.append(
            "id=%s %s debt=%.2f speed=%.2f" % (
                getattr(d, "player_id", "?"),
                getattr(d, "depth_role", None) or "-",
                debt, speed,
            )
        )
    if not rows:
        return "tes_depth_authority=clear"
    return "tes_depth_authority=%d [%s]" % (len(rows), ", ".join(rows))
