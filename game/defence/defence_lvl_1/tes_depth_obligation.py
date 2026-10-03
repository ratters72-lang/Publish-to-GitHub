"""tes_depth_obligation.py — THE TES DEPTH OBLIGATION (Stage 1, 2026-09-18).

CONTRACT (Stage 1: DEFINITION + MEASUREMENT ONLY)
-------------------------------------------------
During TES every defender must compare its current DEPTH to its assigned
ROLE DEPTH relative to the ball carrier:

    role depth  =  bc.x + WIDTH_OFFSETS[depth_role]     (during the drive)
    depth error =  d.x - role_depth                     (SIGNED)
    depth debt  = |d.x - role_depth|                     (UNSIGNED)

A defender whose depth differs from its role depth by MORE than the
acceptable tolerance is outside the acceptable band and is therefore in
the formal TES Depth Obligation state.  The obligation is DIRECTION-
AGNOSTIC (the user's locked decision): a body ahead of its role depth and
a body behind it are both outside the band.  The signed error is retained
purely so a probe can attribute WHICH SIDE of the role depth a body
occupies — the convergence law for catching up and backing off are not the
same, so later stages need that split.

WHAT THIS MODULE IS, AND WHAT IT DELIBERATELY IS NOT
----------------------------------------------------
This module is the SINGLE canonical definition of the TES depth debt and
the obligation verdict.  It is PURE and READ-ONLY.

It carries NO authority, NO latch, NO target, NO cooldown, NO re-arm, and
NO writer of any kind.  It never writes a single attribute onto a defender
or a world.  Stage 1 is measurement-only: define the debt, expose the
obligation truth, and let a probe prove or falsify the diagnosis BEFORE
any authority, gap-proportional convergence, latching, stripping or other
behavioural change is introduced (rollout Steps 1-3; Steps 4-10 add the
authority later).

The rollout's later stages mirror the shipped retreat pair —
``retreat_locomotion.retreat_debt_m`` (debt) + ``retreat_obligation.
is_retreat_obligated`` (veto) + ``enforce_retreat_obligation`` (phase-gated
authority) — and that shape is why this module is split the same way from
the outset: a debt measure any consumer can read, and one named veto.

THE DEBT MEASURE IS ERA-WIDE; THE OBLIGATION STATE IS TES-ONLY
--------------------------------------------------------------
Locked decision.  ``tes_depth_debt_m`` is valid across the WHOLE
``is_drive_phase()`` era (OPEN_PLAY with a live carrier, plus every TES
tackle phase) because that is exactly the window in which the role depth
is anchored to the live carrier — the debt is well defined there and
measuring it there costs nothing and needs no special case.

``is_tes_depth_obligated`` is gated STRICTLY to the TES chamber —
``tackle_init`` / ``tackle_contact`` / ``tackle_drive`` /
``tackle_dominance`` — because the obligation is a claim on TES structure
ownership, and TES structure ownership only exists inside the chamber.

WHY ``tackle_init`` IS IN THE CHAMBER SET (rollout Step 8)
---------------------------------------------------------
The live TES structure hold (``tes_drive_structure._TES_DRIVE_SUBSTATES``)
runs only in CONTACT / DRIVE / DOMINANCE.  It does NOT run at TACKLE_INIT,
so on the TES ENTRY frame nothing is compared at all today.  Rollout Step
8 is "Activate debt authority immediately at TES start rather than waiting
for later phases"; naming ``tackle_init`` in the chamber set from Stage 1
is what makes that gap measurable now and closable without redefining the
window later.

EXEMPTION — TACKLERS ARE EXEMPT BY DEFINITION
---------------------------------------------
Locked decision: TACKLER_1/2/3 (and the registered-TES-tackler reads) are
exempt ALONGSIDE markers / DUMMY_HALF / fullback, because CONTACT
AUTHORITY and DEPTH AUTHORITY must never compete for the same actor.  A
tackler's X is its collision point with the carrier — demanding it also
hold a role depth would put two authorities on one body.  This is a NAMED,
DOCUMENTED exemption, deliberately not an accident of role naming.

The same rule covers the marker pair (they play tight to the ruck by
design), the dummy half (an attacker), and the dynamic fullback (it owns
its own deep backfield anchor).

NO ROLE DEPTH => NO DEBT
------------------------
``has_role_depth(d)`` requires the defender's ``depth_role`` to be a real
entry in the tuner ``WIDTH_OFFSETS`` table.  A body with no depth role has
no assigned role depth, so it owes nothing and is never obligated.  This
is what keeps the obligation population exactly "bodies that own an
assigned role depth".

SINGLE SOURCE OF THE ROLE DEPTH (no third copy)
-----------------------------------------------
``tes_depth_target_x`` prefers the engine's own live published stamp
(``d.role_depth_target_x``, refreshed every drive frame by
``defensive_depth_roles.stamp_drive_depth_x``), and falls back to the
canonical law ``defensive_depth_roles._depth_role_target_x``.  Both agree
by construction — the stamp IS that function's output — so the obligation
measures against what the engine actually commanded rather than against a
restatement of the formula.

IMPORT SAFETY
-------------
No module-scope PROJECT imports (only ``math``), so every defensive module
may import this in any order with no cycle.  The role-depth, anchor and
drive-phase reaches are FUNCTION-LOCAL, exactly like
``retreat_locomotion.compliance_line_x``.
"""

from __future__ import annotations

import math

__all__ = [
    "TES_DEPTH_OBLIGATION_PHASES",
    "TES_DEPTH_TOLERANCE_FALLBACK_M",
    "in_tes_depth_era",
    "in_tes_depth_chamber",
    "has_role_depth",
    "is_depth_exempt_role",
    "tes_depth_target_x",
    "tes_depth_error_m",
    "tes_depth_debt_m",
    "tes_depth_tolerance_m",
    "is_tes_depth_obligated",
    "format_depth_obligation_report",
]

# ── THE TES CHAMBER (the obligation window) ──────────────────────────
# Lower-cased sub-state values.  ``tackle_init`` is INCLUDED deliberately
# (rollout Step 8 — the obligation must start at TES ENTRY, not wait for
# the drive chamber the live structure hold happens to run in).
TES_DEPTH_OBLIGATION_PHASES: frozenset = frozenset({
    "tackle_init",
    "tackle_contact",
    "tackle_drive",
    "tackle_dominance",
})

# Tolerance fallback when no tuner object is reachable.  Mirrors
# ``DefenceTuners.tes_depth_obligation_tolerance_m`` (itself defaulted from
# the live TES structure-hold arrival deadband, 0.5 m), so a bare-namespace
# caller degrades to the same threshold rather than to a new number.
TES_DEPTH_TOLERANCE_FALLBACK_M: float = 0.5


# ─────────────────────────────────────────────────────────────────────
# SMALL HELPERS
# ─────────────────────────────────────────────────────────────────────

def _finite(v) -> bool:
    """True when *v* is a real finite number (never a bool)."""
    if isinstance(v, bool):
        return False
    return isinstance(v, (int, float)) and math.isfinite(float(v))


def _role_name(d) -> str:
    """Return *d*'s defender role name (or the value verbatim)."""
    role = getattr(d, "defender_role", None)
    return getattr(role, "name", None) or str(role)


def _substate_name(world) -> str:
    """Return the lower-cased sub-state (or OPE phase label) string.

    Mirrors ``tes_drive_structure._substate_name`` so the chamber test and
    the live structure hold can never disagree on what phase it is.
    """
    sub = getattr(world, "sub_state", None)
    if sub is None:
        return str(getattr(world, "_ope_phase_label", "") or "").lower()
    return str(getattr(sub, "value", sub)).lower()


def _resolve_tuners(world, tuners=None):
    """Return the live DefenceTuners (engine singleton when available)."""
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


def _live_bc(world):
    """The live ball carrier, or None."""
    return getattr(world, "ball_carrier", None)


# ─────────────────────────────────────────────────────────────────────
# 1. THE TWO ERAS — MEASURE-WIDE, OBLIGATE-IN-CHAMBER
# ─────────────────────────────────────────────────────────────────────

def in_tes_depth_era(world) -> bool:
    """True while the TES DEPTH DEBT is well defined (the drive era).

    Delegates to the engine's own ``defensive_width.is_drive_phase`` — the
    SINGLE drive-phase law — so the debt measure, the role-depth stamp and
    the width recompute can never disagree on what "the drive" is.  A live
    carrier is required by that predicate; kickoff-in-flight is never a
    drive.

    NOTE: this is the ERA the debt is measurable in (OPEN_PLAY plus the
    tackle phases).  It is NOT the obligation window — see
    ``in_tes_depth_chamber``.
    """
    if world is None:
        return False
    try:
        from game.defence.defence_lvl_1.defensive_width import is_drive_phase
        return bool(is_drive_phase(world))
    except Exception:
        # Degraded fallback: a live carrier plus a drive-shaped sub-state.
        # Never raises, so a probe on a stub world still measures.
        if _live_bc(world) is None:
            return False
        return _substate_name(world) in (
            TES_DEPTH_OBLIGATION_PHASES | frozenset({"open_play"})
        )


def in_tes_depth_chamber(world) -> bool:
    """True only inside the TES chamber (the obligation window).

    The obligation is a claim on TES structure ownership, so it is gated
    strictly to ``tackle_init`` / ``tackle_contact`` / ``tackle_drive`` /
    ``tackle_dominance`` with a live carrier.  Plain OPEN_PLAY is NOT in
    this set: the debt can be measured there, but no body is obligated
    there.
    """
    if world is None:
        return False
    if _live_bc(world) is None:
        return False
    return _substate_name(world) in TES_DEPTH_OBLIGATION_PHASES


# ─────────────────────────────────────────────────────────────────────
# 2. THE POPULATION — WHO OWES A ROLE DEPTH AT ALL
# ─────────────────────────────────────────────────────────────────────

def has_role_depth(d, tuners=None) -> bool:
    """True when *d* owns an ASSIGNED role depth.

    A defender with no ``depth_role`` (or one absent from the canonical
    ``WIDTH_OFFSETS`` table) has no assigned role depth, so it owes no
    depth and is never obligated.
    """
    if d is None:
        return False
    role = getattr(d, "depth_role", None)
    if role is None:
        return False
    if tuners is None:
        try:
            from game.defence.defence_lvl_1.defensive_tuners import (
                DefenceTuners,
            )
            tuners = DefenceTuners()
        except Exception:
            tuners = None
    offsets = getattr(tuners, "WIDTH_OFFSETS", None) if tuners else None
    if isinstance(offsets, dict):
        return role in offsets
    # No tuner table reachable: any non-empty role string is an assignment.
    return bool(str(role))


def is_depth_exempt_role(d, world=None) -> bool:
    """True when *d* can never carry the TES depth obligation.

    THE EXEMPTION IS BY DEFINITION: contact authority and depth authority
    must never compete for the same actor.  Exempt:

      * ``MARKER_A`` / ``MARKER_B`` — play tight to the ruck by design;
      * ``DUMMY_HALF`` — an attacker, not a line body;
      * the dynamic fullback (``fullback_role_active``) — owns its own
        deep backfield anchor;
      * every TES tackler — ``TACKLER_1/2/3`` roles, the ``is_tackler``
        flag, and the registered-tackler registries
        (``world.tackler_ids`` / ``world.primary_tackler_id`` /
        ``world.secondary_tacklers``).  A tackler's X is its collision
        point with the carrier; it is not holding a role depth.
    """
    if d is None:
        return True
    from player.player import DefenderRole
    role = getattr(d, "defender_role", None)
    if role in (
        DefenderRole.MARKER_A,
        DefenderRole.MARKER_B,
        DefenderRole.DUMMY_HALF,
        DefenderRole.TACKLER_1,
        DefenderRole.TACKLER_2,
        DefenderRole.TACKLER_3,
    ):
        return True
    if bool(getattr(d, "fullback_role_active", False)):
        return True
    # Tackler flags / registries (a joiner can be armed before its
    # TACKLER_n role is stamped, so the flag and registry reads matter).
    if bool(getattr(d, "is_tackler", False)):
        return True
    pid = getattr(d, "player_id", None)
    if pid is not None and world is not None:
        if pid in (getattr(world, "tackler_ids", None) or ()):
            return True
        if pid == getattr(world, "primary_tackler_id", None):
            return True
        if pid in (getattr(world, "secondary_tacklers", None) or ()):
            return True
    return False


# ─────────────────────────────────────────────────────────────────────
# 3. THE ROLE DEPTH, THE ERROR AND THE DEBT
# ─────────────────────────────────────────────────────────────────────

def tes_depth_target_x(world, d, tuners=None) -> float | None:
    """The assigned ROLE DEPTH X for *d* relative to the ball carrier.

    Prefers the engine's own live published stamp (``role_depth_target_x``,
    refreshed every drive frame by
    ``defensive_depth_roles.stamp_drive_depth_x``), because that is what
    the engine actually COMMANDED this frame.  Falls back to the canonical
    law ``defensive_depth_roles._depth_role_target_x`` for a body the
    stamper skips (no stamp yet) or a stub world.

    Returns ``None`` when no target is resolvable (no carrier, no role,
    no anchor) — callers treat that as "no measurable debt".
    """
    if d is None:
        return None
    stamped = getattr(d, "role_depth_target_x", None)
    if _finite(stamped):
        return float(stamped)
    try:
        from game.defence.defence_lvl_1.defensive_depth_roles import (
            _depth_role_target_x,
        )
        value = _depth_role_target_x(world, d)
    except Exception:
        return None
    if not _finite(value):
        return None
    return float(value)


def tes_depth_error_m(world, d, target_x=None) -> float:
    """SIGNED depth error in metres: ``d.x - role_depth``.

    POSITIVE => the defender is AHEAD of its role depth (over-committed
    toward the carrier's line / upfield of where its role should sit).
    NEGATIVE => the defender is BEHIND its role depth (short of its role
    slot, left in the wake of the drive).

    Returns ``0.0`` when no target is resolvable, so a caller can sum it
    safely.  This value is DIAGNOSTIC ONLY — obligation verdicts are
    direction-agnostic (see ``tes_depth_debt_m``).
    """
    if d is None:
        return 0.0
    if target_x is None:
        target_x = tes_depth_target_x(world, d)
    if target_x is None or not _finite(target_x):
        return 0.0
    try:
        x = float(getattr(d, "x", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return x - float(target_x)


def tes_depth_debt_m(world, d, target_x=None) -> float:
    """UNSIGNED TES depth debt in metres: ``|d.x - role_depth|``.

    THE canonical TES depth debt.  Never negative; ``0.0`` when no target
    is measurable.  Direction-agnostic by the locked decision: a body
    ahead of its role depth and a body behind it both carry debt, because
    both are outside the acceptable band.
    """
    return abs(tes_depth_error_m(world, d, target_x=target_x))


def tes_depth_tolerance_m(tuners=None) -> float:
    """THE acceptable depth tolerance, in metres.

    Reads ``tuners.tes_depth_obligation_tolerance_m``, defaulting to the
    module fallback (0.5 m — the live TES structure-hold arrival deadband
    the tuner itself defaults from).  A malformed value degrades to the
    fallback rather than raising.
    """
    value = getattr(tuners, "tes_depth_obligation_tolerance_m", None)
    if value is None:
        return TES_DEPTH_TOLERANCE_FALLBACK_M
    try:
        tol = float(value)
    except (TypeError, ValueError):
        return TES_DEPTH_TOLERANCE_FALLBACK_M
    if not math.isfinite(tol) or tol < 0.0:
        return TES_DEPTH_TOLERANCE_FALLBACK_M
    return tol


# ─────────────────────────────────────────────────────────────────────
# 4. THE VETO
# ─────────────────────────────────────────────────────────────────────

def is_tes_depth_obligated(world, d, tuners=None) -> bool:
    """THE obligation verdict.  True when *d* owes TES depth right now.

    All four must hold, in this order:

      1. INSIDE THE TES CHAMBER — ``in_tes_depth_chamber(world)``.  The
         window is ``tackle_init`` / ``tackle_contact`` / ``tackle_drive``
         / ``tackle_dominance`` with a live carrier.
      2. OWNS A ROLE DEPTH     — ``has_role_depth(d)``.
      3. NOT EXEMPT            — ``is_depth_exempt_role(d, world)`` is
         False (markers / DH / fullback / tacklers).
      4. DEBT EXCEEDS THE TOLERANCE — ``debt > tolerance`` (strict; a body
         exactly on the tolerance is inside the acceptable band).

    Pure read: writes nothing, latches nothing, and carries no state
    between calls.  A future writer that grants depth authority while this
    returns True is the drift the later rollout stages guard against.
    """
    if d is None:
        return False
    if not in_tes_depth_chamber(world):
        return False
    tuners = _resolve_tuners(world, tuners)
    if not has_role_depth(d, tuners):
        return False
    if is_depth_exempt_role(d, world):
        return False
    target_x = tes_depth_target_x(world, d, tuners)
    if target_x is None:
        return False
    debt = tes_depth_debt_m(world, d, target_x=target_x)
    return debt > tes_depth_tolerance_m(tuners)


# ─────────────────────────────────────────────────────────────────────
# 5. LOGGING (diagnostic only — never consumed by behaviour)
# ─────────────────────────────────────────────────────────────────────

def format_depth_obligation_report(world, defenders, tuners=None) -> str:
    """Compact one-line rendering of the live depth obligations.

    Diagnostic only.  Naming a body here proves the verdict is reachable;
    an empty row means no body owed depth on this frame.
    """
    tuners = _resolve_tuners(world, tuners)
    tol = tes_depth_tolerance_m(tuners)
    rows = []
    for d in (defenders or ()):
        if d is None:
            continue
        if not is_tes_depth_obligated(world, d, tuners):
            continue
        role = getattr(d, "depth_role", None) or "-"
        debt = tes_depth_debt_m(world, d)
        rows.append(
            "id=%s %s debt=%.2f" % (
                getattr(d, "player_id", "?"), role, debt,
            )
        )
    if not rows:
        return "tes_depth_obligation=clear (tol=%.2f)" % tol
    return "tes_depth_obligation=%d [%s] (tol=%.2f)" % (
        len(rows), ", ".join(rows), tol,
    )
