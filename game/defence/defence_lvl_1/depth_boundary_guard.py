"""depth_boundary_guard.py — THE DEPTH-TARGET BOUNDARY INVARIANT.

THE OPEN FINDING THIS MODULE CLOSES
-----------------------------------
Defensive depth-role targets were allowed to publish BEYOND the defending
team's own tryline during the TES drive.  The drive-era depth-role law is

    role depth X  =  bc.x + WIDTH_OFFSETS[depth_role]

(``defensive_depth_roles._depth_role_target_x``), which is unbounded in
``bc.x``.  The engine's fixed directional rule is "the defence always
retreats toward +X" (``fullback_role.py``), so the DEFENDING team's own
tryline is the +X tryline (x = 110) and every metre past it is in-goal.
A carrier driven to ``bc.x = 105`` therefore produced, with no clamp
anywhere:

    WINGER  -> 105 + 10 = 115    (5 m into goal)
    D       -> 105 +  5 = 110    (exactly the tryline)
    CENTRE  -> 105 +  5 = 110
    C       -> 105 +  3 = 108

Those values were published as ``role_depth_target_x`` (the canonical
depth stamp), handed to the TES depth obligation as the role depth, and
written onto ``target_x`` by the TES depth authority — so the line was
COMMANDED into its own in-goal, and the depth debt was measured against an
illegal target (a body at x=108 holding a WINGER role read 10 m of debt
against the illegal 115 and was driven further in-goal instead of being
capped at the line).

THE EXISTING PROTECTION THIS MIRRORS (the "10 m retreat" precedent)
-------------------------------------------------------------------
The post-tackle 10 m retreat protection is a tryline clamp inside the
anchor authority:

    defensive_line_anchor_m(world):
        raw_anchor = ruck_x_m + ANCHOR_DEPTH_M          # ruck_x + 10
        return max(LEFT_TRYLINE_X, min(RIGHT_TRYLINE_X, raw_anchor))

and the same law is duplicated as ``Field.legal_retreat_x()``
(``clamp_x(ruck_x + 10)``).  The RETREAT target can therefore never
publish beyond the tryline — but the DEPTH target (the drive-era
successor of the same geometry: "the line tracks the carrier, then
retreats the 10 m") never received the equivalent clamp.  This module IS
that clamp, expressed once for every depth-role producer.

THE INVARIANT (one sentence)
----------------------------
A DEFENSIVE DEPTH TARGET for a NON-FULLBACK depth role is clamped into the
legal field envelope ``[LEFT_TRYLINE_X, RIGHT_TRYLINE_X]`` and may never be
generated beyond the defending team's own tryline.

THE FULLBACK IS EXEMPT — BY DESIGN, NOT BY ACCIDENT
---------------------------------------------------
``fullback_role_active`` bodies own a deep backfield anchor that is
deliberately allowed to sit slightly in-goal
(``fullback_role.FIELD_MAX_X = 112.0`` — "allow the FB to sit a little
into-goal beyond the tryline").  The fullback is a sweeper, not a line
depth role: it takes no depth role, no spacing slot and no width cell, and
its geometry is owned solely by ``apply_fullback_geometry``.  Clamping it
here would fight that writer, so ``depth_boundary_exempt`` exempts it —
exactly as the finding scopes the invariant to "all non-fullback depth
roles".

WHAT IT APPLIES TO (targets AND positional locks)
-------------------------------------------------
The finding covers both halves of the same law, because both are generated
from ``bc.x + WIDTH_OFFSETS[role]``:

  * DEPTH-ROLE TARGETS — ``role_depth_target_x`` (the canonical stamp),
    ``tes_depth_target_x`` (the obligation's read), and the ``target_x``
    the TES depth authority publishes.
  * DEPTH-ROLE POSITIONAL LOCKS — ``depth_lock_x``, refreshed from the same
    live-BC law by ``defence_manager._refresh_depth_lock_x`` and then glided
    into ``x`` by the movement integrator, so an unclamped lock glides a
    non-fullback depth-role body past the defending tryline.

Both are clamped through ``clamp_depth_target_x`` — ONE law, two call
sites, no second derivation.

WHAT IT IS NOT
--------------
  * It is NOT a phase gate: it is a pure geometry clamp, valid in every
    phase where a depth target exists.
  * It is NOT a latch, a cooldown, a target store or a persistent
    attribute.  It is a pure function plus a read-only enforcement pass,
    exactly like ``anchor_authority.enforce_retreat_to_anchor``.
  * It never writes a Y axis, a velocity, a freeze flag, a role or a
    movement state — only the X target it was given, and only when that X
    is illegal.

THE SINGLE SOURCE OF THE ENVELOPE
---------------------------------
``LEFT_TRYLINE_X`` / ``RIGHT_TRYLINE_X`` are imported (function-locally)
from ``anchor_authority`` — the same constants the 10 m retreat protection
clamps on — so the depth boundary and the retreat boundary can never
disagree about where the tryline is.  No second copy exists here.

REVERSIBILITY
-------------
``DefenceTuners.depth_boundary_clamp_enabled`` (default True) and the
runtime lever ``RLM_DEPTH_BOUNDARY_CLAMP=0`` restore the pre-fix behaviour
byte-for-byte: the clamp becomes an identity function and the enforcement
pass returns an empty list.

IMPORT SAFETY
-------------
No module-scope PROJECT imports (only ``math``), so every defensive module
may import this in any order with no cycle.  The anchor-authority and
tuner reaches are FUNCTION-LOCAL, exactly like ``retreat_locomotion``.
"""

from __future__ import annotations

import math

__all__ = [
    "DEPTH_BOUNDARY_SOURCE",
    "DEPTH_BOUNDARY_FALLBACK_TRYLINE_X",
    "DEFAULT_DEPTH_BOUNDARY_MARGIN_M",
    "depth_boundary_clamp_enabled",
    "defending_tryline_x",
    "depth_boundary_exempt",
    "owns_depth_target",
    "is_beyond_defending_tryline",
    "clamp_depth_target_x",
    "apply_depth_boundary_guard",
    "audit_depth_boundary",
    "format_depth_boundary_report",
]

# ── ATTRIBUTION ──────────────────────────────────────────────────────
# The marker the enforcement pass reports; never a ``target_source`` stamp
# (this guard is not a movement writer — it only repairs an illegal X).
DEPTH_BOUNDARY_SOURCE: str = "depth_boundary_guard"

# ── FALLBACK ENVELOPE ────────────────────────────────────────────────
# Used ONLY when the anchor authority is unreachable (a bare-namespace
# caller).  Mirrors ``anchor_authority.RIGHT_TRYLINE_X`` so a degraded
# caller clamps to the same tryline rather than inventing one.
DEPTH_BOUNDARY_FALLBACK_TRYLINE_X: float = 110.0

# ── THE CLEARANCE INSIDE THE TRYLINE ─────────────────────────────────
# 0.0 = the tryline itself is legal and "beyond" means past it.  Exposed as
# a tuner so an operator can require a clearance without editing code; it is
# a geometry rail, never a tactical squeeze.
DEFAULT_DEPTH_BOUNDARY_MARGIN_M: float = 0.0

# Attributes the enforcement pass repairs, in priority order.  Both are
# DEPTH-ROLE X products of the same ``bc.x + WIDTH_OFFSETS[role]`` law.
_DEPTH_TARGET_ATTR: str = "role_depth_target_x"
_DEPTH_LOCK_ATTR: str = "depth_lock_x"


# ─────────────────────────────────────────────────────────────────────
# SMALL HELPERS
# ─────────────────────────────────────────────────────────────────────

def _finite(v) -> bool:
    """True when *v* is a real finite number (never a bool)."""
    if isinstance(v, bool):
        return False
    return isinstance(v, (int, float)) and math.isfinite(float(v))


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


def _resolve_tuners(world, tuners=None):
    """Return the live DefenceTuners (engine singleton when available).

    Mirrors ``tes_depth_obligation._resolve_tuners`` so the gate, the
    clearance and the envelope are resolved from ONE tuner object.
    """
    if tuners is not None:
        return tuners
    ope = getattr(world, "open_play_engine", None) if world is not None else None
    if ope is not None:
        t = getattr(ope, "def_tuners", None)
        if t is not None:
            return t
    t = getattr(world, "tuners", None) if world is not None else None
    if t is not None:
        return t
    try:
        from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
        return DefenceTuners()
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────
# 1. THE GATE
# ─────────────────────────────────────────────────────────────────────

def depth_boundary_clamp_enabled(tuners) -> bool:
    """True when the depth-target boundary invariant is armed.

    Defaults to True so the invariant is live unless a tuner or the runtime
    lever explicitly turns it off (which restores the pre-fix behaviour
    byte-for-byte: no clamp, no enforcement pass).
    """
    import os
    if os.environ.get("RLM_DEPTH_BOUNDARY_CLAMP", "") == "0":
        return False
    return bool(_tuner(tuners, "depth_boundary_clamp_enabled", True))


# ─────────────────────────────────────────────────────────────────────
# 2. THE ENVELOPE (single-sourced from the anchor authority)
# ─────────────────────────────────────────────────────────────────────

def depth_boundary_envelope() -> tuple:
    """Return ``(left_tryline_x, right_tryline_x)`` — the legal envelope.

    Imported FUNCTION-LOCALLY from ``anchor_authority`` — the SAME constants
    ``defensive_line_anchor_m`` (the post-tackle 10 m retreat protection)
    clamps on — so the depth boundary and the retreat boundary provably
    share one field envelope.  Falls back to the module mirror when the
    authority is unreachable, so a bare-namespace caller still clamps.
    """
    try:
        from game.defence.defence_lvl_1.anchor_authority import (
            LEFT_TRYLINE_X,
            RIGHT_TRYLINE_X,
        )
        return float(LEFT_TRYLINE_X), float(RIGHT_TRYLINE_X)
    except Exception:
        return 10.0, DEPTH_BOUNDARY_FALLBACK_TRYLINE_X


def defending_tryline_x(tuners=None) -> float:
    """THE defending team's own tryline X (metres).

    The engine's fixed directional rule is "the defence always retreats
    toward +X", so the defending team's own tryline is the +X end of the
    envelope.  Returns that bound less the optional clearance
    (``depth_boundary_tryline_margin_m``, default 0.0), which is how a
    caller may ask for a clearance without a code edit.
    """
    _left, right = depth_boundary_envelope()
    margin = max(0.0, _float_tuner(
        tuners, "depth_boundary_tryline_margin_m",
        DEFAULT_DEPTH_BOUNDARY_MARGIN_M,
    ))
    return float(right) - margin


def attacking_tryline_x() -> float:
    """THE far (attacking) tryline X — the LOWER bound of the envelope.

    A depth target is always generated FORWARD of the carrier
    (``bc.x + WIDTH_OFFSETS[role]``, offsets >= 0), so this bound is a
    safety floor rather than a live constraint; it exists so the clamp is
    an envelope clamp and not a one-sided rail.
    """
    left, _right = depth_boundary_envelope()
    return float(left)


# ─────────────────────────────────────────────────────────────────────
# 3. THE POPULATION — WHO OWNS A BOUNDED DEPTH TARGET
# ─────────────────────────────────────────────────────────────────────

def depth_boundary_exempt(d) -> bool:
    """True when *d* is exempt from the depth boundary invariant.

    ONE exemption, by design: the DYNAMIC FULLBACK
    (``fullback_role_active``).  Its deep backfield anchor is deliberately
    allowed slightly in-goal (``fullback_role.FIELD_MAX_X``) and is owned
    solely by ``apply_fullback_geometry``; it holds no depth role and is a
    non-member of the line, so clamping its geometry would put two
    authorities on one body's X.

    Every other body that OWNS a depth role is in scope — which is the
    finding's "all non-fullback depth roles".
    """
    if d is None:
        return True
    return bool(getattr(d, "fullback_role_active", False))


def owns_depth_target(d, tuners=None) -> bool:
    """True when *d* holds a DEPTH ROLE and therefore a depth target.

    A body with no ``depth_role`` (or one absent from the canonical
    ``WIDTH_OFFSETS`` table) has no assigned depth, so there is no
    depth-role target to bound — markers, the dummy half and tacklers
    carry none and are untouched by construction rather than by a second
    exclusion list.
    """
    if d is None:
        return False
    role = getattr(d, "depth_role", None)
    if role is None:
        return False
    tuners = _resolve_tuners(None, tuners)
    offsets = getattr(tuners, "WIDTH_OFFSETS", None) if tuners else None
    if isinstance(offsets, dict) and offsets:
        return role in offsets
    # No tuner table reachable: any non-empty role string is an assignment.
    return bool(str(role))


# ─────────────────────────────────────────────────────────────────────
# 4. THE INVARIANT (the pure law)
# ─────────────────────────────────────────────────────────────────────

def is_beyond_defending_tryline(world, d, target_x, tuners=None) -> bool:
    """True when *target_x* for *d* is outside the legal field envelope.

    The single readable expression of the finding: a NON-exempt depth role
    whose target sits beyond the defending team's own tryline (or short of
    the far tryline, which the depth law cannot produce but which the
    envelope check still rejects).
    """
    if d is None or depth_boundary_exempt(d):
        return False
    if not _finite(target_x):
        return False
    tx = float(target_x)
    return tx > defending_tryline_x(tuners) + 1e-9 or tx < attacking_tryline_x() - 1e-9


def clamp_depth_target_x(world, d, target_x, tuners=None) -> float | None:
    """THE boundary invariant: clamp a depth target into the legal envelope.

    Returns:

        * the value UNCHANGED when the guard is disabled, when *d* is
          exempt (the dynamic fullback), when *d* owns no depth role, or
          when the target is already inside the envelope — so every
          legal frame is byte-identical to the pre-fix engine;
        * the clamped value when the target would be generated beyond the
          defending team's own tryline (or short of the far tryline);
        * ``target_x`` verbatim (including ``None``) when it is not a real
          finite number, so a caller's "no measurable target" convention is
          never converted into a number.

    This is a PURE function: it writes nothing, latches nothing and carries
    no state between calls.
    """
    if target_x is None:
        return None
    if not _finite(target_x):
        return target_x
    if d is None or depth_boundary_exempt(d):
        return float(target_x)
    tuners = _resolve_tuners(world, tuners)
    if not depth_boundary_clamp_enabled(tuners):
        return float(target_x)
    if not owns_depth_target(d, tuners):
        return float(target_x)
    tx = float(target_x)
    low = attacking_tryline_x()
    high = defending_tryline_x(tuners)
    if tx > high:
        return high
    if tx < low:
        return low
    return tx


# ─────────────────────────────────────────────────────────────────────
# 5. THE ENFORCEMENT PASS (repairs anything a writer re-pinned)
# ─────────────────────────────────────────────────────────────────────

def apply_depth_boundary_guard(world, defenders, tuners=None) -> list:
    """Repair every illegal depth-role X on *defenders*.  Returns them.

    The read-only enforcement counterpart of the pure law, mirroring
    ``anchor_authority.enforce_retreat_to_anchor``: it exists so the
    invariant holds even for a writer that produced a depth X from the
    ``bc.x + WIDTH_OFFSETS[role]`` law WITHOUT routing through
    ``clamp_depth_target_x`` (present or future).  It is the reason the
    guarantee is independent of the writer enumeration.

    Repairs, in order, the two depth-role X products of that ONE law:

        ``role_depth_target_x``  — the canonical depth stamp
        ``depth_lock_x``         — the depth-role positional lock (glided
                                   into ``x`` by the movement integrator)

    Writes ONLY an attribute it found illegal, and only on a body that owns
    a depth role and is not the dynamic fullback.  A compliant frame —
    which is every frame whose carrier has not been driven to the tryline —
    is written NOTHING, so the pass is byte-identical to the pre-fix engine
    on all legal frames.

    Returns the list of repaired bodies (the wiring proof: an empty list
    across a whole rollout is expected, and a non-empty list names exactly
    which writer let an illegal depth X through).
    """
    repaired: list = []
    if world is None:
        return repaired
    tuners = _resolve_tuners(world, tuners)
    if not depth_boundary_clamp_enabled(tuners):
        return repaired

    for d in (defenders or ()):
        if d is None or depth_boundary_exempt(d):
            continue
        if not owns_depth_target(d, tuners):
            continue
        touched = False
        for attr in (_DEPTH_TARGET_ATTR, _DEPTH_LOCK_ATTR):
            value = getattr(d, attr, None)
            if not _finite(value):
                continue
            clamped = clamp_depth_target_x(world, d, value, tuners)
            if clamped is None or abs(float(clamped) - float(value)) <= 1e-9:
                continue
            setattr(d, attr, float(clamped))
            touched = True
        if touched:
            repaired.append(d)
    return repaired


# ─────────────────────────────────────────────────────────────────────
# 6. THE AUDIT (drift detection — names the offending writer)
# ─────────────────────────────────────────────────────────────────────

def audit_depth_boundary(world, defenders, tuners=None,
                         raise_on_violation: bool = False) -> list:
    """Return one row per depth target generated beyond the tryline.

    The DRIFT DETECTOR: it names every non-fullback depth role holding an
    X outside the legal envelope, so a writer that bypasses the invariant
    announces itself instead of being silently blessed (the same role
    ``retreat_obligation.audit_retreat_obligation_compliance`` plays for the
    retreat contract).

    Read-only: it writes nothing, so it can run BEFORE the enforcement pass
    and still report what was produced.  ``raise_on_violation`` promotes the
    finding to a hard failure for the strict verifier run.
    """
    violations: list = []
    if world is None:
        return violations
    tuners = _resolve_tuners(world, tuners)
    tryline = defending_tryline_x(tuners)

    for d in (defenders or ()) if isinstance(defenders, (list, tuple)) else ():
        if d is None or depth_boundary_exempt(d):
            continue
        if not owns_depth_target(d, tuners):
            continue
        for attr in (_DEPTH_TARGET_ATTR, _DEPTH_LOCK_ATTR):
            value = getattr(d, attr, None)
            if not _finite(value):
                continue
            if is_beyond_defending_tryline(world, d, value, tuners):
                violations.append(
                    "DEPTH_BOUNDARY_VIOLATION id=%s role=%s %s=%.4f "
                    "tryline=%.4f" % (
                        getattr(d, "player_id", "?"),
                        getattr(d, "depth_role", None) or "-",
                        attr, float(value), tryline,
                    )
                )

    if violations and raise_on_violation:
        raise AssertionError("; ".join(violations))
    return violations


# ─────────────────────────────────────────────────────────────────────
# 7. LOGGING (diagnostic only — never consumed by behaviour)
# ─────────────────────────────────────────────────────────────────────

def format_depth_boundary_report(world, defenders, tuners=None) -> str:
    """Compact one-line rendering of the live depth boundary state."""
    tuners = _resolve_tuners(world, tuners)
    tryline = defending_tryline_x(tuners)
    rows = []
    for d in (defenders or ()):
        if d is None or depth_boundary_exempt(d):
            continue
        if not owns_depth_target(d, tuners):
            continue
        value = getattr(d, _DEPTH_TARGET_ATTR, None)
        if not _finite(value):
            continue
        rows.append(
            "id=%s %s x=%.2f lock=%s" % (
                getattr(d, "player_id", "?"),
                getattr(d, "depth_role", None) or "-",
                float(value),
                ("%.2f" % float(getattr(d, _DEPTH_LOCK_ATTR)))
                if _finite(getattr(d, _DEPTH_LOCK_ATTR, None)) else "-",
            )
        )
    if not rows:
        return "depth_boundary=clear (tryline=%.2f)" % tryline
    return "depth_boundary=%d [%s] (tryline=%.2f)" % (
        len(rows), ", ".join(rows), tryline,
    )
