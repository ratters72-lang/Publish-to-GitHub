"""tackle_engage_guard.py — RADIAL+FACING TACKLE ENGAGE GUARD (2026-09-10).

PURPOSE
-------
"Mate no tackles happen ever" — the tackle pipeline never fires.

Root cause: the engage decision is gated on a SLOT-Y threshold in THREE
independent places, all reading ``slot_arbitration.is_slot_tackle_eligible``
(``abs(bc.y - defender.y) < tackle_slot_threshold_m``, default 0.3 m):

  1. ``movement_mode_decision.decide_movement_state`` section 1.5 — the
     tackle-radius CHASE switch returns CHASE only when slot-in.
  2. ``pre_tackle_authority._reconcile_owners`` — a body may only become the
     per-side COMMIT owner (and therefore stamp ``tackle_role = "PRIMARY"``)
     when slot-in.
  3. ``pretackle_machine.run_machine`` / ``should_engage`` — the TES engage
     window requires slot-in (CONTACT GEOMETRY conjunct 1).

When the carrier runs a lane whose Y is more than 0.3 m off a defender's live
Y, all three veto.  The defender is radial-in (inside ``tackle_radius_m``),
closing, facing the carrier — and yet it is routed to DRIFT/RETREAT, never
becomes a COMMIT owner, and ``try_pre_tackle`` returns False forever
(``apply_outcome`` then actively mutates it).  No tackle.

THE GUARD
---------
A GENUINE LINE DEFENDER that is RADIAL-IN (``dist(d, bc) < tackle_radius_m``)
AND FACING the ball carrier (approach angle from its facing plane within
``tackle_engage_facing_max_deg``) has a live, physical tackle attempt.  For
exactly those bodies the SLOT-Y gate is BYPASSED and the engage is forced, so
the tackle pipeline must fire instead of freezing or drifting:

    radial-in AND facing-at-carrier  ->  bypass slot  ->  force engage

Scope is deliberately narrow (a HARD guard, not a blanket radial trigger):

  - GENUINE LINE DEFENDER only — the shared
    ``movement_mode_decision._is_genuine_line_defender`` predicate.  Markers /
    DUMMY_HALF / TES tacklers / KICK_CHASE / fullback / frozen / freeze-timer /
    tackle-state holders are excluded, so dedicated owners keep their
    contracts and the fullback is never drafted into a tackle.
  - NO ROLE OVERRIDE — a body already carrying the explicit non-initiator
    role ``THIRD`` (joiner-only, Finding 7) is never promoted to a tackle
    initiator: it is registered in the joiner system and that contract is
    preserved.  The guard admits ``tackle_role is None`` (the reported
    "no tackles ever" case) plus PRIMARY / SECONDARY.
  - RADIAL-IN only — a distant body is untouched.
  - FACING the carrier — a defender whose facing plane points AWAY from the
    carrier (carrier running past its shoulder) is NOT forced; that is the
    "radial proximity is not a tackle" case the slot gate was protecting.

COORDINATION (one predicate, four consumers)
-------------------------------------------
This module is the SINGLE definition of the guard.  The slot-gated consumers
call ``is_tackle_engage_guard`` and OR it into their slot test:

  - ``movement_mode_decision.decide_movement_state``      (movement state)
  - ``pre_tackle_authority._reconcile_owners``            (COMMIT owner / TES role)
  - ``pre_tackle_authority.apply_pre_tackle``             (radial promote)
  - ``pretackle_machine.run_machine`` / ``should_engage`` (TES engage window)

STAMP
-----
``stamp_tackle_engage_guard`` latches ``_tackle_engage_guard = True`` and
stamps ``target_source = "tackle_engage_guard"`` so the frame is auditable.
``force_tackle_engage`` also pins the closing posture
(``movement_state = CHASE``) that the TES engage window reads, so the guard
body is never left in a non-closing state by a lower-priority writer.

NOTE ON ``TACKLE_BEGIN``
------------------------
There is no ``MovementState.TACKLE_BEGIN`` member (``player/movement_states.py``
defines HOLD / RETREAT / SLIDE / SHOOT / CHASE / DRIFT only) and TES entry is
armed by ROLE + phase, not by a movement state.  The guard therefore
(1) forces the CLOSING posture the engage window reads and (2) leaves role /
phase arming to the existing authority chain (``pre_tackle_authority`` stamps
``tackle_role = "PRIMARY"``; ``OpenPlayEngine._start_tackle`` calls
``_arm_tackler_role(..., DefenderRole.TACKLER_1, "TACKLER_1", ctx)`` then
``_set_phase("TACKLE_INIT", GameSubState.TACKLE_INIT, "TACKLE_INIT")``).
"""

from __future__ import annotations

import math

from player.movement_states import MovementState


# ── TARGET-SOURCE STAMP ──────────────────────────────────────────────
TACKLE_ENGAGE_GUARD_SOURCE: str = "tackle_engage_guard"

# ── GUARD THRESHOLDS ─────────────────────────────────────────────────
# Mirrors DefenceTuners.tackle_radius_m (the TES tackle trigger radius).
TACKLE_RADIUS_M: float = 1.5

# How far off the carrier the defender's FACING plane may point and still
# count as "facing the ball carrier".  60 deg brackets a genuine square-on or
# shoulder-on tackle attempt while rejecting a defender whose facing is
# perpendicular-to-away (carrier running past its shoulder).
TACKLE_ENGAGE_FACING_MAX_DEG: float = 60.0

# Per-defender latch attribute (frame-auditable).
GUARD_LATCH_ATTR: str = "_tackle_engage_guard"

# The TES engage role the guard stamps.  Mirrors the vocabulary shared by
# ``pretackle_classifier.is_engage_role`` and
# ``pre_tackle_authority._TACKLE_ROLE_COMMIT`` ("PRIMARY").
TES_ENGAGE_ROLE: str = "PRIMARY"

# Explicit NON-initiator roles.  ``THIRD`` is documented as joiner-only
# (``pretackle_classifier.is_engage_role`` — Finding 7), so a body already
# carrying it is NEVER promoted to a tackle initiator by the guard: it is
# already registered in the joiner system and overriding that would break
# the joiner contract.  The guard admits bodies with NO role yet
# (``tackle_role is None`` — the reported "no tackles ever" case) plus
# PRIMARY / SECONDARY.
NON_ENGAGE_ROLES: frozenset = frozenset({"THIRD"})


# ── TUNABLE ACCESSORS ────────────────────────────────────────────────

def tackle_engage_guard_enabled(tuners) -> bool:
    """Master gate for the guard.  Defaults ON (it is a fix, not a feature)."""
    if tuners is None:
        return True
    return bool(getattr(tuners, "tackle_engage_guard_enabled", True))


def tackle_radius_m(tuners) -> float:
    """The radial engage radius (shared with the TES tackle trigger)."""
    if tuners is None:
        return TACKLE_RADIUS_M
    value = getattr(tuners, "tackle_radius_m", None)
    if value is None:
        return TACKLE_RADIUS_M
    return float(value)


def tackle_engage_facing_max_deg(tuners) -> float:
    """Max facing-plane deviation (deg) that still counts as facing the BC."""
    if tuners is None:
        return TACKLE_ENGAGE_FACING_MAX_DEG
    value = getattr(tuners, "tackle_engage_facing_max_deg", None)
    if value is None:
        return TACKLE_ENGAGE_FACING_MAX_DEG
    return float(value)


# ── GEOMETRY ─────────────────────────────────────────────────────────

def approach_angle_deg(d, bc) -> float:
    """Angle (deg) between *d*'s FACING plane and the d->BC ray.

    Uses the LIVE ball carrier rather than the anticipation-leaned reference:
    the guard asks the physical question "is this defender's facing plane
    pointing at the carrier right now".  Returns 180.0 (never facing) when
    either body is missing.
    """
    if d is None or bc is None:
        return 180.0
    to_bc = math.atan2(
        float(getattr(bc, "y", 0.0)) - float(getattr(d, "y", 0.0)),
        float(getattr(bc, "x", 0.0)) - float(getattr(d, "x", 0.0)),
    )
    facing = float(getattr(d, "facing", 0.0) or 0.0)
    delta = abs(to_bc - facing)
    delta = min(delta, math.pi * 2.0 - delta)
    return math.degrees(delta)


def distance_to_bc(d, bc) -> float:
    """Euclidean distance (m) between *d* and *bc* (inf when either is None)."""
    if d is None or bc is None:
        return float("inf")
    return math.hypot(
        float(getattr(d, "x", 0.0)) - float(getattr(bc, "x", 0.0)),
        float(getattr(d, "y", 0.0)) - float(getattr(bc, "y", 0.0)),
    )


def is_radial_in(d, bc, tuners) -> bool:
    """True when *d* is INSIDE the tackle radius of *bc*."""
    radius = tackle_radius_m(tuners)
    if radius <= 0.0:
        return False
    return distance_to_bc(d, bc) < radius


def is_facing_bc(d, bc, tuners) -> bool:
    """True when *d*'s facing plane points at *bc* within the facing band."""
    return approach_angle_deg(d, bc) <= tackle_engage_facing_max_deg(tuners)


# ── ROLE SCOPE ───────────────────────────────────────────────────────

def is_genuine_line_defender(d) -> bool:
    """True when *d* is a genuine line body (shared MMDL predicate).

    Reuses ``movement_mode_decision._is_genuine_line_defender`` so the guard's
    scope can never drift from the tackle-radius CHASE switch scope.  Imported
    lazily because ``movement_mode_decision`` imports THIS module.
    """
    if d is None:
        return False
    from game.defence.defence_lvl_1.movement_mode_decision import (
        _is_genuine_line_defender,
    )
    return _is_genuine_line_defender(d)


# ── THE PREDICATE ────────────────────────────────────────────────────

def is_tackle_engage_guard(d, bc, world=None, tuners=None) -> bool:
    """True when the SLOT gate must be bypassed for *d*.

    The single definition of the hard guard:

        genuine line defender
          AND radial-in (dist(d, bc) < tackle_radius_m)
          AND facing the ball carrier (facing-plane angle <= facing band)

    Returns False when the master gate is off, either body is missing, the
    carrier does not own the ball, or TES is already live (during a live TES
    the engagement system owns every defender and no new tackle is forced).
    """
    if not tackle_engage_guard_enabled(tuners):
        return False
    if d is None or bc is None:
        return False
    # Never force a NEW engage while TES is live -- TES owns the tacklers.
    if bool(getattr(world, "tes_active", False)):
        return False
    # A tackle only exists with the carrier in possession.
    if world is not None and getattr(world, "ball_owner", None) not in (None, "BC"):
        return False
    if not is_genuine_line_defender(d):
        return False
    # Never override an explicit non-initiator role (THIRD — joiner-only).
    if getattr(d, "tackle_role", None) in NON_ENGAGE_ROLES:
        return False
    if not is_radial_in(d, bc, tuners):
        return False
    return is_facing_bc(d, bc, tuners)


# ── THE STAMP ────────────────────────────────────────────────────────

def stamp_tackle_engage_guard(d, world=None) -> None:
    """Latch the guard + stamp ``target_source`` on *d*.

    Idempotent within a frame.  ``_tackle_engage_guard`` lets downstream
    writers and verifiers see the guard fired; ``target_source`` names the
    authority that owns the frame's target/state write.
    """
    if d is None:
        return
    setattr(d, GUARD_LATCH_ATTR, True)
    if world is not None:
        setattr(d, "_tackle_engage_guard_frame",
                int(getattr(world, "frame_count", 0)))
    d.target_source = TACKLE_ENGAGE_GUARD_SOURCE


def clear_tackle_engage_guard(d) -> None:
    """Drop the guard latch (radius exit / eligibility loss / reset)."""
    if d is None:
        return
    for attr in (GUARD_LATCH_ATTR, "_tackle_engage_guard_frame"):
        try:
            delattr(d, attr)
        except AttributeError:
            pass


def is_tackle_engage_guard_stamped(d) -> bool:
    """True when *d* carries the guard latch."""
    return bool(getattr(d, GUARD_LATCH_ATTR, False))


def stamp_tes_tackler_role(d, role: str = TES_ENGAGE_ROLE) -> None:
    """Stamp the TES engage role on *d* when it carries no engage role.

    ``pretackle_classifier.is_engage_role`` — the shared role predicate read
    by ``pretackle_machine.run_machine`` AND by every lane-pipeline call site
    — admits only ``tackle_role in ("PRIMARY", "SECONDARY")``.  A guarded
    radial+facing line body carries ``tackle_role = None``, so it was
    rejected by that gate BEFORE the engage window was ever evaluated: the
    final chokepoint of "no tackles ever".  The guard therefore stamps the
    TES role itself — exactly what
    ``pre_tackle_authority._sync_shims(d, PreTackleState.COMMIT)`` does for a
    COMMIT owner.  An existing engage role is NEVER downgraded, so a body the
    lane system already selected keeps its role.
    """
    if d is None:
        return
    if getattr(d, "tackle_role", None) in ("PRIMARY", "SECONDARY"):
        return
    d.tackle_role = role


def force_tackle_engage(d, world=None) -> None:
    """Force the closing posture + TES role + guard stamp on *d*.

    Pins ``movement_state = CHASE`` — the CLOSING posture the TES engage
    window reads (``pretackle_machine.is_closing_posture``) — so a
    lower-priority writer in the same frame cannot leave a radial+facing body
    in DRIFT/RETREAT/HOLD and starve the engage, and stamps the TES engage
    role so ``is_engage_role`` admits the body.
    """
    if d is None:
        return
    d.movement_state = MovementState.CHASE
    stamp_tes_tackler_role(d)
    stamp_tackle_engage_guard(d, world=world)
