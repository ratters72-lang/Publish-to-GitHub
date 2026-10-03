"""tes_width_obligation.py — THE TES WIDTH OBLIGATION (Stage 3a, 2026-09-19).

CONTRACT (Stage 3a: DEFINITION + MEASUREMENT ONLY)
--------------------------------------------------
During TES every defender must compare its current LATERAL (Y) position to
its assigned ROLE WIDTH slot relative to the ruck line:

    role width  =  role_depth_target_y                   (the published stamp)
    width error =  d.y - role_width                      (SIGNED)
    width debt  = |d.y - role_width|                     (UNSIGNED)

A defender whose lateral position differs from its role width by MORE than
the acceptable tolerance is outside the acceptable band and is therefore in
the formal TES Width Obligation state.  The obligation is DIRECTION-
AGNOSTIC (mirroring the locked depth decision): a body narrow of its role
width and a body wide of it are both outside the band.  The signed error is
retained purely so a probe can attribute WHICH SIDE of the role width a body
occupies — converging inward and converging outward are not the same
locomotion problem, so later stages need that split.

THE WIDTH / DEPTH AXIS SPLIT (why this is a SEPARATE obligation)
----------------------------------------------------------------
The TES Depth Obligation owns the X axis: it measures the carrier-tracked
depth ``bc.x + WIDTH_OFFSETS[depth_role]``.  This module owns the LATERAL
(Y) axis: it measures the ruck-anchored structural slot that
``defensive_depth_roles.assign_depth_roles`` publishes as
``d.role_depth_target_y``.  The two axes are MEASURED separately and
integrated by ONE loop (``Player.update`` drives both axes from a single
``target_x`` / ``target_y`` pair).

The DEPTH axis has a shipped writer — ``tes_depth_authority`` (Stage 2) —
which writes ``target_x`` and never ``target_y``.

The LATERAL axis has NO writer.  Measured live in the drive chamber (7374
obligated body-frames in one rollout), the lateral cell is already owned
end-to-end: the chamber-entry census / structure hold owns every line body's
slot, and ``pre_tackle_authority`` owns the pile bodies' Y.  Every writer
pool is therefore fully owned, so a width writer could only CONTEST an
existing owner — measured: arming one took the shipped recovery-ownership
contract from 0 to 4279 violations.  The Stage-3b writer was consequently
RETIRED (2026-09-19); this module is retained as the canonical MEASUREMENT
of the lateral debt, which keeps that ownership question auditable rather
than assumed.

WHAT THIS MODULE IS, AND WHAT IT DELIBERATELY IS NOT
----------------------------------------------------
This module is the SINGLE canonical definition of the TES width debt and
the obligation verdict.  It is PURE and READ-ONLY.

It carries NO authority, NO latch, NO target, NO cooldown, NO re-arm, and
NO writer of any kind.  It never writes a single attribute onto a defender
or a world.  Stage 3a is measurement-only: define the debt, expose the
obligation truth, and let a probe prove or falsify the diagnosis BEFORE any
authority, gap-proportional convergence, latching or other behavioural
change is introduced.  Stage 3b was built on this module and then RETIRED
(2026-09-19); no writer consumes this module today, and any future writer
must CONSUME the debt and the target defined here rather than re-declaring
them.

The rollout mirrors the shipped depth pair — ``tes_depth_obligation``
(debt + veto) + ``tes_depth_authority`` (phase-gated writer) — and this
module is the debt-and-veto half of the width pair, split the same way from
the outset so the shape is provably identical.  The writer half was
retired rather than shipped; only the debt-and-veto half remains.

THE DEBT MEASURE IS ERA-WIDE; THE OBLIGATION STATE IS TES-ONLY
-------------------------------------------------------------
Locked decision, mirroring depth.  ``tes_width_debt_m`` is valid across the
WHOLE ``is_drive_phase()`` era (OPEN_PLAY with a live carrier, plus every
TES tackle phase) because that is exactly the window in which the role
width anchor is published from the live ruck geometry — the debt is well
defined there and measuring it there costs nothing and needs no special
case.

``is_tes_width_obligated`` is gated STRICTLY to the TES chamber —
``tackle_init`` / ``tackle_contact`` / ``tackle_drive`` /
``tackle_dominance`` — because the obligation is a claim on TES structure
ownership, and TES structure ownership only exists inside the chamber.

WHY ``tackle_init`` IS IN THE CHAMBER SET
-----------------------------------------
The live TES structure hold runs only in CONTACT / DRIVE / DOMINANCE, so on
the TES ENTRY frame nothing is compared at all today.  Naming
``tackle_init`` in the chamber set from the outset makes that gap
measurable now and closable without redefining the window later — the same
reason the depth chamber set names it.

EXEMPTION — TACKLERS ARE EXEMPT BY DEFINITION
---------------------------------------------
Locked decision: TACKLER_1/2/3 (and the registered-TES-tackler reads) are
exempt ALONGSIDE markers / DUMMY_HALF / fullback, because CONTACT AUTHORITY
and WIDTH AUTHORITY must never compete for the same actor.  A tackler's Y
is its collision line with the carrier — demanding it also hold a role
width would put two authorities on one body.  This is a NAMED, DOCUMENTED
exemption, deliberately not an accident of role naming.

The same rule covers the marker pair (they play tight to the ruck by
design), the dummy half (an attacker), and the dynamic fullback (it owns
its own deep backfield anchor and tracks the ball laterally at its own
gain).

NO ROLE WIDTH => NO DEBT
------------------------
``has_role_width(d)`` requires the defender's ``depth_role`` to be a real
entry in the tuner ``ROLE_WIDTH_OFFSETS`` table.  A body with no depth role
has no assigned role width, so it owes nothing and is never obligated.  This
is what keeps the obligation population exactly "bodies that own an assigned
role width slot".

SINGLE SOURCE OF THE ROLE WIDTH (no third copy)
-----------------------------------------------
``tes_width_target_y`` prefers the engine's own live published stamp
(``d.role_depth_target_y``, refreshed every frame by
``defensive_depth_roles.assign_depth_roles`` from
``anchors[depth_side][depth_role]``), and falls back to the canonical law
``defensive_width.role_anchor_y_for(role, side, ruck_y, side_width, tuners)``
via the structural anchor builder.  Both agree by construction — the stamp
IS that function's output — so the obligation measures against what the
engine actually commanded rather than against a restatement of the formula.

IMPORT SAFETY
-------------
No module-scope PROJECT imports (only ``math``), so every defensive module
may import this in any order with no cycle.  The anchor, tuner, drive-phase
and role-table reaches are FUNCTION-LOCAL, exactly like
``tes_depth_obligation``.
"""

from __future__ import annotations

import math

__all__ = [
    "TES_WIDTH_OBLIGATION_PHASES",
    "TES_WIDTH_TOLERANCE_FALLBACK_M",
    "in_tes_width_era",
    "in_tes_width_chamber",
    "has_role_width",
    "is_width_exempt_role",
    "tes_width_target_y",
    "tes_width_error_m",
    "tes_width_debt_m",
    "tes_width_tolerance_m",
    "is_tes_width_obligated",
    "format_width_obligation_report",
]

# ── THE TES CHAMBER (the obligation window) ──────────────────────────
# Lower-cased sub-state values.  ``tackle_init`` is INCLUDED deliberately
# (the obligation must start at TES ENTRY, not wait for the drive chamber
# the live structure hold happens to run in) — identical to the depth
# chamber set.
TES_WIDTH_OBLIGATION_PHASES: frozenset = frozenset({
    "tackle_init",
    "tackle_contact",
    "tackle_drive",
    "tackle_dominance",
})

# Tolerance fallback when no tuner object is reachable.  Mirrors
# ``DefenceTuners.tes_width_obligation_tolerance_m`` (itself defaulted from
# the live TES slot-recovery deadband, 0.5 m), so a bare-namespace caller
# degrades to the same threshold rather than to a new number.
TES_WIDTH_TOLERANCE_FALLBACK_M: float = 0.5


# ─────────────────────────────────────────────────────────────────────
# SMALL HELPERS
# ─────────────────────────────────────────────────────────────────────

def _finite(v) -> bool:
    """True when *v* is a real finite number (never a bool)."""
    if isinstance(v, bool):
        return False
    return isinstance(v, (int, float)) and math.isfinite(float(v))


def _substate_name(world) -> str:
    """Return the lower-cased sub-state (or OPE phase label) string.

    Mirrors ``tes_depth_obligation._substate_name`` so the width chamber
    test and the depth chamber test can never disagree on what phase it is.
    """
    sub = getattr(world, "sub_state", None)
    if sub is None:
        return str(getattr(world, "_ope_phase_label", "") or "").lower()
    return str(getattr(sub, "value", sub)).lower()


def _resolve_tuners(world, tuners=None):
    """Return the live DefenceTuners (engine singleton when available).

    Mirrors ``tes_depth_obligation._resolve_tuners`` so the width verdict
    and the depth verdict are resolved from ONE tuner object per frame.
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


def _live_bc(world):
    """The live ball carrier, or None."""
    return getattr(world, "ball_carrier", None)


# ─────────────────────────────────────────────────────────────────────
# 1. THE TWO ERAS — MEASURE-WIDE, OBLIGATE-IN-CHAMBER
# ─────────────────────────────────────────────────────────────────────

def in_tes_width_era(world) -> bool:
    """True while the TES WIDTH DEBT is well defined (the drive era).

    Delegates to the engine's own ``defensive_width.is_drive_phase`` — the
    SINGLE drive-phase law — so the width debt measure, the role-width
    stamp and the depth debt measure can never disagree on what "the drive"
    is.  A live carrier is required by that predicate; kickoff-in-flight is
    never a drive.

    NOTE: this is the ERA the debt is measurable in (OPEN_PLAY plus the
    tackle phases).  It is NOT the obligation window — see
    ``in_tes_width_chamber``.
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
            TES_WIDTH_OBLIGATION_PHASES | frozenset({"open_play"})
        )


def in_tes_width_chamber(world) -> bool:
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
    return _substate_name(world) in TES_WIDTH_OBLIGATION_PHASES


# ─────────────────────────────────────────────────────────────────────
# 2. THE POPULATION — WHO OWES A ROLE WIDTH AT ALL
# ─────────────────────────────────────────────────────────────────────

def has_role_width(d, tuners=None) -> bool:
    """True when *d* owns an ASSIGNED role width slot.

    A defender with no ``depth_role`` (or one absent from the canonical
    ``ROLE_WIDTH_OFFSETS`` table) has no assigned role width, so it owes no
    lateral slot and is never obligated.
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
    offsets = getattr(tuners, "ROLE_WIDTH_OFFSETS", None) if tuners else None
    if isinstance(offsets, dict):
        return role in offsets
    # No tuner table reachable: any non-empty role string is an assignment.
    return bool(str(role))


def is_width_exempt_role(d, world=None) -> bool:
    """True when *d* can never carry the TES width obligation.

    THE EXEMPTION IS BY DEFINITION: contact authority and width authority
    must never compete for the same actor.  Exempt:

      * ``MARKER_A`` / ``MARKER_B`` — play tight to the ruck by design;
      * ``DUMMY_HALF`` — an attacker, not a line body;
      * the dynamic fullback (``fullback_role_active``) — owns its own
        deep backfield anchor and its own lateral gain;
      * every TES tackler — ``TACKLER_1/2/3`` roles, the ``is_tackler``
        flag, and the registered-tackler registries
        (``world.tackler_ids`` / ``world.primary_tackler_id`` /
        ``world.secondary_tacklers``).  A tackler's Y is its collision
        line with the carrier; it is not holding a role width.
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
# 3. THE ROLE WIDTH, THE ERROR AND THE DEBT
# ─────────────────────────────────────────────────────────────────────

def _canonical_role_width_y(world, d, tuners=None) -> float | None:
    """The canonical role-width anchor Y for *d* (the fallback law).

    Rebuilds the SAME per-side structural anchor the depth-role system
    publishes, through the canonical width formula: the per-side width from
    the spacing chain is fed into ``role_anchor_y_for`` so the fallback can
    never disagree with the stamp.  Returns None when the geometry is not
    resolvable (no role, no side, no ruck) — callers treat that as
    "no measurable debt".
    """
    role = getattr(d, "depth_role", None)
    side = getattr(d, "depth_side", None)
    if role is None or side not in (-1, 1):
        return None
    if tuners is None:
        tuners = _resolve_tuners(world, None)
    if tuners is None:
        return None
    try:
        from game.defence.defence_lvl_1.defensive_width import (
            role_anchor_y_for,
        )
        from game.defence.defence_lvl_1.defensive_split_spacing import (
            side_widths,
        )
    except Exception:
        return None
    try:
        ruck_y = float(getattr(world, "ruck_y_m", 0.0) or 0.0)
        left_w, right_w = side_widths(world, tuners, ruck_y=ruck_y)
        side_width = right_w if int(side) == 1 else left_w
        value = role_anchor_y_for(
            role, int(side), ruck_y, float(side_width), tuners,
        )
    except Exception:
        return None
    if not _finite(value):
        return None
    return float(value)


def tes_width_target_y(world, d, tuners=None) -> float | None:
    """The assigned ROLE WIDTH Y for *d* — the structural lateral slot.

    Prefers the engine's own live published stamp (``role_depth_target_y``,
    refreshed every frame by
    ``defensive_depth_roles.assign_depth_roles``), because that is what the
    engine actually COMMANDED this frame.  Falls back to the canonical law
    (``role_anchor_y_for`` over the current per-side width) for a body the
    assigner skips (no stamp yet) or a stub world.

    Returns ``None`` when no target is resolvable (no role, no side, no
    ruck geometry) — callers treat that as "no measurable debt".
    """
    if d is None:
        return None
    stamped = getattr(d, "role_depth_target_y", None)
    if _finite(stamped):
        return float(stamped)
    return _canonical_role_width_y(world, d, tuners)


def tes_width_error_m(world, d, target_y=None) -> float:
    """SIGNED width error in metres: ``d.y - role_width``.

    POSITIVE => the defender is WIDE of its role width (further from the
    ruck than its slot).  NEGATIVE => the defender is NARROW of its role
    width (tighter to the ruck than its slot).

    Returns ``0.0`` when no target is resolvable, so a caller can sum it
    safely.  This value is DIAGNOSTIC ONLY — obligation verdicts are
    direction-agnostic (see ``tes_width_debt_m``).
    """
    if d is None:
        return 0.0
    if target_y is None:
        target_y = tes_width_target_y(world, d)
    if target_y is None or not _finite(target_y):
        return 0.0
    try:
        y = float(getattr(d, "y", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return y - float(target_y)


def tes_width_debt_m(world, d, target_y=None) -> float:
    """UNSIGNED TES width debt in metres: ``|d.y - role_width|``.

    THE canonical TES width debt.  Never negative; ``0.0`` when no target
    is measurable.  Direction-agnostic by the locked decision: a body wide
    of its role width and a body narrow of it both carry debt, because both
    are outside the acceptable band.
    """
    return abs(tes_width_error_m(world, d, target_y=target_y))


def tes_width_tolerance_m(tuners=None) -> float:
    """THE acceptable width tolerance, in metres.

    Reads ``tuners.tes_width_obligation_tolerance_m``, defaulting to the
    module fallback (0.5 m — the live TES slot-recovery deadband the tuner
    itself defaults from).  A malformed value degrades to the fallback
    rather than raising.
    """
    value = getattr(tuners, "tes_width_obligation_tolerance_m", None)
    if value is None:
        return TES_WIDTH_TOLERANCE_FALLBACK_M
    try:
        tol = float(value)
    except (TypeError, ValueError):
        return TES_WIDTH_TOLERANCE_FALLBACK_M
    if not math.isfinite(tol) or tol < 0.0:
        return TES_WIDTH_TOLERANCE_FALLBACK_M
    return tol


# ─────────────────────────────────────────────────────────────────────
# 4. THE VETO
# ─────────────────────────────────────────────────────────────────────

def is_tes_width_obligated(world, d, tuners=None) -> bool:
    """THE obligation verdict.  True when *d* owes TES width right now.

    All four must hold, in this order:

      1. INSIDE THE TES CHAMBER — ``in_tes_width_chamber(world)``.  The
         window is ``tackle_init`` / ``tackle_contact`` / ``tackle_drive``
         / ``tackle_dominance`` with a live carrier.
      2. OWNS A ROLE WIDTH    — ``has_role_width(d)``.
      3. NOT EXEMPT           — ``is_width_exempt_role(d, world)`` is
         False (markers / DH / fullback / tacklers).
      4. DEBT EXCEEDS THE TOLERANCE — ``debt > tolerance`` (strict; a body
         exactly on the tolerance is inside the acceptable band).

    Pure read: writes nothing, latches nothing, and carries no state
    between calls.  Any future writer that grants width authority while
    this returns True must first prove it does not contest the chamber's
    existing lateral owners (the census / structure hold for line bodies,
    ``pre_tackle_authority`` for the pile) — the write boundary whose
    absence retired Stage 3b.
    """
    if d is None:
        return False
    if not in_tes_width_chamber(world):
        return False
    tuners = _resolve_tuners(world, tuners)
    if not has_role_width(d, tuners):
        return False
    if is_width_exempt_role(d, world):
        return False
    target_y = tes_width_target_y(world, d, tuners)
    if target_y is None:
        return False
    debt = tes_width_debt_m(world, d, target_y=target_y)
    return debt > tes_width_tolerance_m(tuners)


# ─────────────────────────────────────────────────────────────────────
# 5. LOGGING (diagnostic only — never consumed by behaviour)
# ─────────────────────────────────────────────────────────────────────

def format_width_obligation_report(world, defenders, tuners=None) -> str:
    """Compact one-line rendering of the live width obligations.

    Diagnostic only.  Naming a body here proves the verdict is reachable;
    an empty row means no body owed width on this frame.
    """
    tuners = _resolve_tuners(world, tuners)
    tol = tes_width_tolerance_m(tuners)
    rows = []
    for d in (defenders or ()):
        if d is None:
            continue
        if not is_tes_width_obligated(world, d, tuners):
            continue
        role = getattr(d, "depth_role", None) or "-"
        debt = tes_width_debt_m(world, d)
        rows.append(
            "id=%s %s debt=%.2f" % (
                getattr(d, "player_id", "?"), role, debt,
            )
        )
    if not rows:
        return "tes_width_obligation=clear (tol=%.2f)" % tol
    return "tes_width_obligation=%d [%s] (tol=%.2f)" % (
        len(rows), ", ".join(rows), tol,
    )
