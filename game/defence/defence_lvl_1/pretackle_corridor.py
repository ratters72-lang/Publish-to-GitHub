"""pretackle_corridor.py — the pre-tackle-controlled approach corridor.

PURPOSE
-------
SPEED-AUTHORITY CONSOLIDATION (2026-09-15).  The speed audit
(project_info__65.md) established that a committed tackler's final
approach was written by FIVE stages across THREE integrators, and that
``pre_tackle_authority`` owned only the innermost 2.0 m of its own 4.0 m
slowdown band.  The result was untunable: changing
``pretackle_slowdown_band_m`` moved a value that a different writer had
already been overwritten, and the tackle armed at ``tackle_radius_m``
(1.5 m) before the band could act.

The CORRIDOR is the answer.  It is the span in which a COMMITTED tackler
is owned by ``pre_tackle_authority`` ALONE:

    - sole writer of ``target_x`` / ``target_y``
    - sole writer of ``move_speed``, ``vx`` / ``vy``
    - sole writer of ``movement_state`` and the plant verdict
    - sole INTEGRATOR of position (via ``_slow_step``)

and the corridor stamps a CONTACT CONTRACT on the body that the TES
outcome system inherits verbatim at ``TACKLE_INIT``.

MEMBERSHIP IS NOT INVENTED HERE
-------------------------------
The corridor NEVER designates a new tackler.  Per-side ownership is
already designated by ``intercept_tacklers.select_lane_tacklers``
(``tackle_role == "PRIMARY"``, at most one per side of the carrier) and
reconciled by ``pre_tackle_authority._reconcile_owners``.  The corridor
only EXTENDS THE WINDOW over which that existing owner is authority-owned,
from ``pre_tackle_trigger_radius_m`` (2.0 m) out to the corridor radius
(``max(pretackle_corridor_radius_m,
pretackle_slowdown_band_m + pretackle_corridor_margin_m)``, 4.5 m by
default).  A body therefore enters the corridor already designated, and
the slowdown band is genuinely available before the tackle arms.

WHY A LATCH AND NOT ``pre_tackle_state``
---------------------------------------
``pre_tackle_state`` is published by
``pre_tackle_authority.publish_pre_tackle_state`` which runs AFTER the
step-10a1 writers that must already know whether to yield.  The corridor
therefore carries its own per-frame latch (``_pta_corridor``), set by
``enter_corridor`` at the TOP of the step-10a1 block, so
``_apply_tackler_commit`` / ``_apply_pursuit_commit_override`` /
``apply_pre_tackle_stance`` / ``seed_hold`` / ``line_break_static_guard``
can all yield to the corridor in the same frame the corridor opens.
``pre_tackle_state`` in (COMMIT, PLANTED) is honoured as a fallback so a
body whose latch was cleared but whose state is still live is never
double-owned.

CONTRACTS
---------
  - RETREAT-OBLIGATION VETO (2026-09-17): a non-marker defender that has
    not completed its 10 m retreat may not be corridor-owned, and the
    incumbent bypass may not keep it owned.  The corridor is one of the
    five tackle-authority grants (it owns the committed tackler's
    approach speed across the whole slowdown band), so it must honour the
    single shared obligation predicate exactly like every other grant.
    The bypass check is load-bearing: ``_corridor_admit`` deliberately
    keeps an already-latched tackler through the transient gates that
    open in the final metres, and without the veto it would also keep a
    body that is still short of the anchor line.
  - Pure bookkeeping + plain-attribute writes.  The corridor NEVER writes
    ``target_x`` / ``target_y`` / ``move_speed`` / ``vx`` / ``vy`` /
    ``movement_state`` — that is ``apply_pre_tackle``'s job.  It writes
    only ``_pta_corridor*`` membership stamps and the
    ``pretackle_contact_*`` handoff block.
  - No engine imports (duck-typed), so it can be imported by
    ``pre_tackle_authority``, ``intercept_tacklers``, ``seed_hold`` and
    ``line_break_static_guard`` with no import cycle.
  - Determinism: identical inputs -> identical membership.  All selection
    is ``min()`` over caller list order with a ``player_id`` tiebreak.
  - CANARY: ``_tuners.pretackle_corridor_owns_speed = False`` makes
    ``enter_corridor`` a strict no-op -- no body is latched or released,
    so every ``enter_corridor``-driven behaviour returns to the
    pre-consolidation pipeline.  It is NOT a complete revert:
    ``is_corridor_owned`` is a one-argument predicate that cannot read the
    gate, so a body whose ``pre_tackle_state`` is still COMMIT / PLANTED
    keeps reading as owned and its yielding writers keep yielding.  That
    fallback is deliberate (belt-and-braces for the frame on which the
    latch is first written).  A TRUE revert needs either the authority to
    stop publishing COMMIT / PLANTED, or a gate-aware ownership predicate.
"""

from __future__ import annotations

import math

# ── RETREAT-OBLIGATION VETO (2026-09-17) ─────────────────────────────
# THE 10 m retreat contract, applied to the corridor authority grant.
# ``retreat_obligation`` reaches nothing but ``player.player`` at module
# scope (its engine-module reads are function-local), so this import
# cannot create a cycle.
from game.defence.defence_lvl_1.retreat_obligation import (
    is_retreat_obligated,
)

__all__ = [
    "CORRIDOR_LATCH_ATTR",
    "CORRIDOR_FRAME_ATTR",
    "CORRIDOR_FRAMES_ATTR",
    "CONTACT_SPEED_ATTR",
    "CONTACT_VX_ATTR",
    "CONTACT_VY_ATTR",
    "CONTACT_PLANT_ATTR",
    "CONTACT_ANGLE_ATTR",
    "corridor_owns_speed",
    "handoff_enabled",
    "corridor_margin_m",
    "band_m",
    "plant_radius_m",
    "promote_radius_m",
    "explicit_radius_m",
    "corridor_radius_m",
    "is_committed_tackler",
    "is_corridor_owned",
    "corridor_frames",
    "is_corridor_eligible",
    "enter_corridor",
    "clear_corridor",
    "stamp_handoff",
    "contact_velocity",
]

# ── PER-BODY ATTRIBUTES ──────────────────────────────────────────────
# Membership latch (this frame).  Set by enter_corridor for owners, CLEARED
# for every other body so a body that leaves the corridor is released the
# same frame it leaves.
CORRIDOR_LATCH_ATTR: str = "_pta_corridor"
# Frame stamp of the latch (audit / staleness read).
CORRIDOR_FRAME_ATTR: str = "_pta_corridor_frame"
# Consecutive frames the body has been corridor-owned (evidence the taper
# had time to act).
CORRIDOR_FRAMES_ATTR: str = "_pta_corridor_frames"

# ── CONTACT-CONTRACT (HANDOFF) ATTRIBUTES ────────────────────────────
# Written every corridor frame by stamp_handoff() and inherited by the TES
# outcome system at TACKLE_INIT (see OpenPlayEngine._start_tackle).
CONTACT_SPEED_ATTR: str = "pretackle_contact_speed_mps"
CONTACT_VX_ATTR: str = "pretackle_contact_vx"
CONTACT_VY_ATTR: str = "pretackle_contact_vy"
CONTACT_PLANT_ATTR: str = "pretackle_plant_state"
CONTACT_ANGLE_ATTR: str = "pretackle_approach_angle_rad"

# ── ROLE SCOPE ───────────────────────────────────────────────────────
# Roles with their own dedicated movement owner: never corridor members.
_EXCLUDED_ROLE_NAMES = frozenset({
    "MARKER_A",
    "MARKER_B",
    "DUMMY_HALF",
    "TACKLER_1",
    "TACKLER_2",
    "TACKLER_3",
    "KICK_CHASE",
})

# A genuinely COMMITTED tackler: the per-side lane owner, or a body the
# tackler-commit / pursuit-commit writers marked as committed this cycle.
_COMMITTED_ROLES = frozenset({"PRIMARY", "SECONDARY"})

# The pre-tackle states that mean "authority-owned" even without a latch.
_OWNED_STATE_NAMES = frozenset({"COMMIT", "PLANTED"})

# Defaults mirror DefenceTuners so a detached caller / verifier stub keeps
# the documented behaviour.
_DEFAULT_CORRIDOR_RADIUS_M: float = 4.5
_DEFAULT_CORRIDOR_MARGIN_M: float = 0.5
_DEFAULT_BAND_M: float = 4.0
_DEFAULT_PLANT_RADIUS_M: float = 0.8
_DEFAULT_PROMOTE_RADIUS_M: float = 2.0


# ─────────────────────────────────────────────────────────────────────
# TUNER ACCESSORS
# ─────────────────────────────────────────────────────────────────────

def _tun(tuners, name: str, default):
    """Read *tuners* attribute *name*, falling back to *default*."""
    if tuners is None:
        return default
    value = getattr(tuners, name, None)
    return default if value is None else value


def corridor_owns_speed(tuners) -> bool:
    """Master gate.  False = the corridor is a strict no-op (A/B canary)."""
    return bool(_tun(tuners, "pretackle_corridor_owns_speed", True))


def handoff_enabled(tuners) -> bool:
    """True when TES must build its contact velocity from the stamp."""
    return bool(_tun(tuners, "pretackle_handoff_enabled", True))


def corridor_margin_m(tuners) -> float:
    """Margin (m) added to the slowdown band when deriving the radius."""
    return float(_tun(
        tuners, "pretackle_corridor_margin_m", _DEFAULT_CORRIDOR_MARGIN_M,
    ))


def band_m(tuners) -> float:
    """The canonical slowdown band (m)."""
    return float(_tun(
        tuners, "pretackle_slowdown_band_m", _DEFAULT_BAND_M,
    ))


def plant_radius_m(tuners) -> float:
    """The plant radius (m) — inside this the corridor plants."""
    return float(_tun(
        tuners, "pretackle_plant_radius_m", _DEFAULT_PLANT_RADIUS_M,
    ))


def promote_radius_m(tuners) -> float:
    """The legacy promotion radius (m) — may a body BECOME an owner.

    The corridor does not use this for its own membership (its owners are
    already designated by the lane system); it is exported so
    ``publish_pre_tackle_state`` can preserve the legacy 2.0 m gate for
    bodies the corridor does NOT own.
    """
    return float(_tun(
        tuners, "pre_tackle_trigger_radius_m", _DEFAULT_PROMOTE_RADIUS_M,
    ))


def explicit_radius_m(tuners) -> float:
    """The configured corridor radius (m) before the band floor is applied."""
    return float(_tun(
        tuners, "pretackle_corridor_radius_m", _DEFAULT_CORRIDOR_RADIUS_M,
    ))


def corridor_radius_m(tuners) -> float:
    """The effective corridor radius (m).

    ``max(explicit, band + margin)`` so widening the slowdown band
    AUTOMATICALLY widens the corridor — the tuner is a floor, never a
    silent cap on the band it is supposed to host.  ``assert_radii()``
    guarantees the configured value already satisfies the hierarchy; this
    derivation keeps that true after a band change.
    """
    return max(
        explicit_radius_m(tuners),
        band_m(tuners) + corridor_margin_m(tuners),
    )


# ─────────────────────────────────────────────────────────────────────
# PREDICATES
# ─────────────────────────────────────────────────────────────────────

def is_committed_tackler(d) -> bool:
    """True when *d* is a designated / marked committed tackler."""
    if d is None:
        return False
    if getattr(d, "tackle_role", None) in _COMMITTED_ROLES:
        return True
    return bool(getattr(d, "_tackle_commit_active", False))


def _owned_state_name(d) -> str | None:
    """The defender's canonical pre-tackle state name (or None)."""
    state = getattr(d, "pre_tackle_state", None)
    if state is None:
        return None
    return getattr(state, "name", None) or str(state)


def is_corridor_owned(d) -> bool:
    """True when *d* is owned by the pre-tackle corridor this frame.

    Authoritative test for every yielding writer.  Either the per-frame
    latch is set, or the canonical state is still a live authority state
    (belt-and-braces for the frame on which the latch is first written).
    """
    if d is None:
        return False
    if bool(getattr(d, CORRIDOR_LATCH_ATTR, False)):
        return True
    return _owned_state_name(d) in _OWNED_STATE_NAMES


def corridor_frames(d) -> int:
    """Consecutive corridor-owned frames for *d* (0 when not owned)."""
    if d is None:
        return 0
    try:
        return int(getattr(d, CORRIDOR_FRAMES_ATTR, 0) or 0)
    except (TypeError, ValueError):
        return 0


def is_corridor_eligible(d, bc, tuners, world=None) -> bool:
    """True when *d* may be corridor-owned (before the distance test).

    Scope mirrors the pre-tackle authority's own eligibility: unfrozen,
    no freeze timer, not a marker / DH / TES tackler / kick-chase body,
    and already a designated committed tackler.

    A tagged fullback is never corridor-owned. Its geometry controller keeps
    locomotion until the emergency classifier successfully arms TES.
    """
    if d is None or bc is None:
        return False
    if bool(getattr(d, "frozen", False)):
        return False
    if float(getattr(d, "freeze_timer", 0.0) or 0.0) > 0.0:
        return False
    if bool(getattr(d, "fullback_role_active", False)):
        return False
    if bool(getattr(d, "tackle_state_active", False)):
        return False
    role = getattr(d, "defender_role", None)
    role_name = getattr(role, "name", None) or str(role) if role is not None else None
    if role_name in _EXCLUDED_ROLE_NAMES:
        return False
    # Membership is not invented here: the body must already be the
    # designated per-side owner (or a genuinely committed tackler).
    if not is_committed_tackler(d):
        return False
    # ── RETREAT-OBLIGATION VETO (2026-09-17) ──
    # A body that still owes its 10 m retreat may not be corridor-owned:
    # the corridor is a tackle-authority grant, and this is the single
    # shared obligation predicate.  A ``world``-less caller (detached
    # verifier stub) keeps the historical behaviour, since
    # ``is_retreat_obligated(d, None)`` is False.
    if is_retreat_obligated(d, world):
        return False
    return _within_radius(d, bc, tuners)


def _within_radius(d, bc, tuners) -> bool:
    """True when *d* is within the corridor radius of the live carrier."""
    if d is None or bc is None:
        return False
    dist = math.hypot(
        float(getattr(d, "x", 0.0) or 0.0) - float(getattr(bc, "x", 0.0) or 0.0),
        float(getattr(d, "y", 0.0) or 0.0) - float(getattr(bc, "y", 0.0) or 0.0),
    )
    return dist <= corridor_radius_m(tuners)


def _corridor_admit(d, bc, tuners, prev_latched, world=None) -> bool:
    """Admit *d* to this frame's corridor pool.

    A body is admitted when it is fully eligible (``is_corridor_eligible``),
    OR when it was ALREADY latched last frame and is still a designated
    committed tackler inside the radius.

    WHY THE INCUMBENT BYPASS EXISTS (2026-09-15)
    -------------------------------------------
    ``is_corridor_eligible`` rejects ``tackle_state_active`` and a live
    ``freeze_timer``.  Both gates OPEN exactly in the final metres of the
    approach -- the TACKLE-STATE GATE stamps itself inside the tackle
    radius, and the structured pipeline's hold branch sets ``freeze_timer``.
    Before this bypass either one REVOKED the corridor latch in the very
    frames the taper must govern, which handed the final approach speed
    back to ``tackler_commit`` (measured live: the final writer at
    2.0-2.4 m with the latch dropped, ``move_speed`` back at the full 4.0
    commit ceiling) and to ``struct_freeze_timer``.

    An already-latched committed tackler therefore keeps its ownership for
    as long as it stays a committed tackler inside the radius.  Ownership
    is released by leaving the radius or losing the role -- never by a
    transient gate that happens to open mid-approach.
    """
    # ── RETREAT-OBLIGATION VETO (2026-09-17) ──
    # Checked BEFORE the eligibility fast path so it ALSO covers the
    # incumbent bypass below: an obligated body must not enter the
    # corridor, and must not be retained in it by the bypass either --
    # otherwise the transient gates that open inside the final metres
    # would hand an illegal approach back to the legacy writers.
    if is_retreat_obligated(d, world):
        return False
    if bool(getattr(d, "fullback_role_active", False)):
        return False
    if bool(getattr(d, "fullback_role_active", False)):
        return False
    if is_corridor_eligible(d, bc, tuners, world):
        return True
    if not prev_latched.get(id(d), False):
        return False
    if not is_committed_tackler(d):
        return False
    role = getattr(d, "defender_role", None)
    role_name = (
        (getattr(role, "name", None) or str(role)) if role is not None else None
    )
    if role_name in _EXCLUDED_ROLE_NAMES:
        return False
    return _within_radius(d, bc, tuners)


# ─────────────────────────────────────────────────────────────────────
# MEMBERSHIP — once per frame, at the TOP of the step-10a1 block
# ─────────────────────────────────────────────────────────────────────

def enter_corridor(world, defenders, bc, tuners) -> list:
    """Open the corridor for this frame and return the owned bodies.

    Runs BEFORE ``_apply_tackler_commit`` so every yielding writer can see
    the latch in the same frame the corridor opens.  Bookkeeping only —
    never writes a target or a speed.

    Membership rule (per side of the carrier's lane):

        eligible  AND  dist(d, bc) <= corridor_radius_m(tuners)
            -> latched (the body was ALREADY the designated owner)

    with a hard cap of ONE latched body per side, chosen by the smallest
    ``|x - bc.x|`` and a ``player_id`` tiebreak (deterministic).  An
    incumbent (latched last frame, or a live ``pre_tackle_state``) is
    never displaced by a challenger inside the same side.

    Also clears the latch on EVERY other body, so a body that has left the
    corridor (radius exit, lost its role, froze, became a TES participant)
    is released in the same frame and re-enters the normal pipeline.
    """
    if not corridor_owns_speed(tuners) or bc is None:
        _clear_all(defenders)
        return []
    if not defenders:
        return []

    # 1. Snapshot each body's previous membership streak, then clear the
    #    latch on EVERY body (explicit release).  A body that is not
    #    re-latched below therefore ends the frame with a membership count
    #    of 0; only a body re-latched in step 3 increments its streak.
    # Capture the PREVIOUS latch alongside the streak before clearing:
    # ``_corridor_admit`` uses it to keep an already-owned committed
    # tackler latched through the transient gates that open in the final
    # metres (see its docstring).
    prev_frames: dict = {}
    prev_latched: dict = {}
    for d in defenders:
        if d is None:
            continue
        prev_frames[id(d)] = corridor_frames(d)
        prev_latched[id(d)] = bool(getattr(d, CORRIDOR_LATCH_ATTR, False))
        d.__dict__[CORRIDOR_LATCH_ATTR] = False
        d.__dict__[CORRIDOR_FRAMES_ATTR] = 0

    r_corridor = corridor_radius_m(tuners)
    bcy = float(getattr(bc, "y", 0.0) or 0.0)
    bcx = float(getattr(bc, "x", 0.0) or 0.0)

    # 2. Partition the eligible pool by side of the carrier's lane.
    by_side: dict[int, list] = {-1: [], 1: []}
    for d in defenders:
        if not _corridor_admit(d, bc, tuners, prev_latched, world):
            continue
        _dy = float(getattr(d, "y", 0.0) or 0.0)
        by_side[-1 if _dy < bcy else 1].append(d)

    # 3. Per side: keep the incumbent if it is still inside the corridor,
    #    otherwise latch the smallest-|x - bc.x| eligible body.
    owners: list = []
    for side in (-1, 1):
        pool = by_side[side]
        if not pool:
            continue
        incumbent = None
        for d in pool:
            if (
                bool(getattr(d, CORRIDOR_LATCH_ATTR, False))
                or _owned_state_name(d) in _OWNED_STATE_NAMES
            ):
                incumbent = d
                break
        chosen = incumbent
        if chosen is None:
            chosen = min(
                pool,
                key=lambda dd: (
                    abs(float(getattr(dd, "x", 0.0) or 0.0) - bcx),
                    int(getattr(dd, "player_id", 0) or 0),
                ),
            )
        # Radius re-check against the LIVE carrier (a body exactly on the
        # boundary is admitted — <=).
        _dist = math.hypot(
            float(getattr(chosen, "x", 0.0) or 0.0) - bcx,
            float(getattr(chosen, "y", 0.0) or 0.0) - bcy,
        )
        if _dist > r_corridor:
            continue
        chosen.__dict__[CORRIDOR_LATCH_ATTR] = True
        chosen.__dict__[CORRIDOR_FRAME_ATTR] = int(
            getattr(world, "frame_count", 0) or 0
        )
        chosen.__dict__[CORRIDOR_FRAMES_ATTR] = (
            prev_frames.get(id(chosen), 0) + 1
        )
        owners.append(chosen)
    return owners


def _clear_all(defenders) -> None:
    """Drop the corridor latch + counter from every body (gate off)."""
    for d in defenders or ():
        if d is None:
            continue
        d.__dict__[CORRIDOR_LATCH_ATTR] = False
        d.__dict__[CORRIDOR_FRAMES_ATTR] = 0


def clear_corridor(d) -> None:
    """Drop the corridor stamps from *d* (reset / eligibility loss)."""
    if d is None:
        return
    for attr in (CORRIDOR_LATCH_ATTR, CORRIDOR_FRAME_ATTR,
                 CORRIDOR_FRAMES_ATTR):
        d.__dict__.pop(attr, None)


# ─────────────────────────────────────────────────────────────────────
# CONTACT CONTRACT — inherited by the TES outcome system at TACKLE_INIT
# ─────────────────────────────────────────────────────────────────────

def contact_velocity(d, tuners) -> tuple:
    """The velocity the TES outcome system must inherit for *d*.

    THE single read path for "the corridor's approach speed, inherited
    directly by the tackle outcome system at TACKLE_INIT".  Returns the
    corridor's stamped contact velocity ``(vx, vy)`` when the handoff gate
    is ON and a stamp exists, otherwise the live ``vx`` / ``vy`` --
    byte-identical to the pre-consolidation behaviour.

    Safe for any body: a body that never entered the corridor carries no
    stamp and returns its live velocity, so callers may apply this to every
    tackler and joiner unconditionally.
    """
    if d is None:
        return (0.0, 0.0)
    live = (
        float(getattr(d, "vx", 0.0) or 0.0),
        float(getattr(d, "vy", 0.0) or 0.0),
    )
    if not handoff_enabled(tuners):
        return live
    svx = getattr(d, CONTACT_VX_ATTR, None)
    svy = getattr(d, CONTACT_VY_ATTR, None)
    if svx is None or svy is None:
        return live
    try:
        return (float(svx), float(svy))
    except (TypeError, ValueError):
        return live


def stamp_handoff(d, plant_state: str, approach_angle_rad: float) -> None:
    """Record the corridor's contact contract on *d* for the TES handoff.

    Written EVERY corridor frame so the value the tackle reads is always
    the most recent governed frame, never a stale one.  In the pure
    single-integrator corridor this equals the live ``vx`` / ``vy`` the
    corridor itself just integrated, so the stamp is primarily a
    DETERMINISM + AUDITABILITY guarantee: it makes "the pre-tackle
    corridor's contact velocity, inherited directly" literally true, and
    it survives any future writer that touches the tackler's velocity
    between the corridor and the tackle trigger.
    """
    if d is None:
        return
    vx = float(getattr(d, "vx", 0.0) or 0.0)
    vy = float(getattr(d, "vy", 0.0) or 0.0)
    d.__dict__[CONTACT_VX_ATTR] = vx
    d.__dict__[CONTACT_VY_ATTR] = vy
    d.__dict__[CONTACT_SPEED_ATTR] = math.hypot(vx, vy)
    d.__dict__[CONTACT_PLANT_ATTR] = str(plant_state)
    try:
        d.__dict__[CONTACT_ANGLE_ATTR] = float(approach_angle_rad)
    except (TypeError, ValueError):
        d.__dict__[CONTACT_ANGLE_ATTR] = 0.0
