"""retreat_obligation.py — THE 10 m retreat-compliance AUTHORITY gate.

CONTRACT (redesigned 2026-09-18)
-------------------------------
A non-marker defender may NOT hold tackle authority while it is still
retreating.  "Still retreating" is NOT a new state invented here: it is
the engine's OWN live role, ``DefenderRole.DEF_RETREAT_10``.

That role is already the engine's retreat-completion signal.
``_update_post_ruck_open_play`` computes it exactly:

    if d.defender_role == DefenderRole.DEF_RETREAT_10:
        if d.x >= target_x:
            d.defender_role = DefenderRole.DEF_LINE_READY   # retreat DONE
        else:
            all_compliant = False                           # still retreating

So this module carries NO state.  Earlier revisions added a latch, a
stamp-time target, a discharge cooldown and a re-arm margin; every one of
them existed only to compensate for the role being destroyed before the
gate could observe it, and every one of them produced the same defect the
contract forbids — an obligation that does not discharge.  All of that is
deleted.  The predicate is now a pure read of engine truth:

    is_retreat_obligated(d) == (d.defender_role is DEF_RETREAT_10)

WHY THE ROLE SURVIVES TO THE VETOES
-----------------------------------
The role could not previously reach the gate: ``assign_def_line_ready``
promoted ``None`` / ``DEF_RETREAT_10`` / ``DEF_CHASE`` to
``DEF_LINE_READY``, and ``DefenceManager.update_open_play`` called it on
EVERY defender EVERY frame BEFORE the authority writers ran.  Every veto
therefore observed ``DEF_LINE_READY`` for every body and a role-based gate
could never fire.  That promotion is now narrowed (it promotes ``None`` /
``DEF_CHASE`` only), so the engine's own arrival test is the sole writer of
retreat-completion and the live role is the truth at every veto site.

ROLE SURVIVAL IS SAFE FOR GEOMETRY (checked before the change landed)
--------------------------------------------------------------------
``DEF_RETREAT_10`` is NOT in ``_SPACING_EXCLUDED_ROLES`` (it still takes
part in the spacing chain) and NOT in ``LINE_EXCLUDED_ROLES`` (it still
counts as a line body for population / sides / counts).  It IS in
``_HINGE_EXCLUDED_ROLES``, deliberately: a retreating body's live Y is not
a line pivot, and using one as the hinge fanned the short side out during
RELEASE/PTB.  Persisting the role into OPEN_PLAY therefore changes no
line geometry.

ONE SURFACE, THREE PIECES (anti-drift)
--------------------------------------
  1. THE VETO        — ``is_retreat_obligated`` at every authority grant
  2. THE REVOCATION  — ``strip_tackle_authority`` (symmetric with the veto)
  3. THE DRIFT AUDIT — ``audit_retreat_obligation_compliance`` names any
     obligated body that nonetheless carries an authority marker, so a
     future writer that forgets the veto is caught at runtime.

IMPORT SAFETY
-------------
No module-scope PROJECT imports (only ``os`` for the strict lever).  The
pre-tackle / corridor reaches inside ``strip_tackle_authority`` are
FUNCTION-LOCAL, so every defensive module may import this in any order
with no cycle.
"""

from __future__ import annotations

import os

__all__ = [
    "RETREAT_OBLIGATION_ROLE_NAME",
    "RETREAT_OBLIGATION_STRICT",
    "is_exempt_role",
    "is_retreat_obligated",
    "has_retreat_obligation_authority",
    "sync_retreat_obligation",
    "enforce_retreat_obligation",
    "strip_tackle_authority",
    "audit_retreat_obligation_compliance",
    "format_obligation_report",
]

# ── STRICT MODE ──────────────────────────────────────────────────────
# The drift audit is LOG-ONLY by default.  ``RLM_OBLIGATION_STRICT=1``
# promotes a violation to a hard failure, which is what proves the veto
# set is COMPLETE rather than merely observed.  Deliberately NOT keyed off
# ``__debug__``: the defence carries many authority writers, so a writer
# that forgets the veto must degrade a live run, not abort it.
RETREAT_OBLIGATION_STRICT: bool = (
    os.environ.get("RLM_OBLIGATION_STRICT", "") == "1"
)

# ── THE OBLIGATION IS THE ENGINE'S OWN ROLE ──────────────────────────
# The single definition of "still retreating".  Read from the live role;
# never cached, stamped, duplicated or inferred.
RETREAT_OBLIGATION_ROLE_NAME: str = "DEF_RETREAT_10"

# Roles that never carry a 10 m retreat obligation.  Markers play tight to
# the ruck by design, the DH is an attacker, and the dynamic fullback owns
# its own deep backfield anchor.  (The engine never assigns DEF_RETREAT_10
# to these, so this is belt-and-suspenders.)
_EXEMPT_ROLE_NAMES = frozenset({
    "MARKER_A",
    "MARKER_B",
    "DUMMY_HALF",
})

# ``pre_tackle_state`` values that mean "authority-owned".
_AUTHORITY_STATE_NAMES = frozenset({"COMMIT", "PLANTED"})

# Boolean authority latches an obligated body must never carry.
# ``_pta_corridor`` is deliberately NOT in this tuple: the corridor owns a
# latch PLUS a frame stamp and a membership counter, so it is cleared and
# reported through its own authority (``clear_corridor``) below.
_AUTHORITY_FLAGS = (
    "_tackle_commit_active",
    "_pursuit_commit_active",
    "can_acquire_tackle",
)


def _role_name(d) -> str:
    """Return *d*'s defender role name (or the value verbatim)."""
    role = getattr(d, "defender_role", None)
    return getattr(role, "name", None) or str(role)


def is_exempt_role(d) -> bool:
    """True when *d* can never carry the retreat obligation."""
    if d is None:
        return True
    if _role_name(d) in _EXEMPT_ROLE_NAMES:
        return True
    return bool(getattr(d, "fullback_role_active", False))


# ─────────────────────────────────────────────────────────────────────
# 1. THE VETO
# ─────────────────────────────────────────────────────────────────────

def is_retreat_obligated(d, world=None) -> bool:
    """THE veto.  True while *d* is still retreating.

    Pure engine truth: the live role IS the obligation.  ``world`` is
    accepted (and unused) so every existing call site keeps its shape.
    """
    if d is None:
        return False
    if is_exempt_role(d):
        return False
    return _role_name(d) == RETREAT_OBLIGATION_ROLE_NAME


def has_retreat_obligation_authority(d, world=None) -> bool:
    """Positive-form companion of the veto (``not is_retreat_obligated``).

    Exists so call sites read as a grant rather than a double negative.
    """
    return not is_retreat_obligated(d, world)


# ─────────────────────────────────────────────────────────────────────
# 2. THE REVOCATION
# ─────────────────────────────────────────────────────────────────────

def strip_tackle_authority(world, d) -> list:
    """Revoke EVERY tackle-authority marker on *d*.  Returns what was cleared.

    The symmetric counterpart of the veto: one code path for revocation,
    so a latch granted by a writer that ran before the veto cannot survive.
    Reaches the pre-tackle / corridor authorities through FUNCTION-LOCAL
    imports (no cycle).

    Cleared: ``tackle_role`` PRIMARY/SECONDARY -> None, the canonical
    pre-tackle state + its anchors, ``_tackle_commit_active``,
    ``_pursuit_commit_active``, ``can_acquire_tackle``, the corridor latch,
    and the depth class (so ``DefensiveBehaviourUnified`` re-classifies it
    fresh rather than reading a promoted SECONDARY).
    """
    if d is None:
        return []
    cleared: list = []

    if getattr(d, "tackle_role", None) in ("PRIMARY", "SECONDARY"):
        d.tackle_role = None
        cleared.append("tackle_role")

    # ── ORDER IS LOAD-BEARING ──
    # The boolean latches are revoked BEFORE the pre-tackle state, because
    # ``clear_pre_tackle_state`` clears ``_tackle_commit_active`` itself as
    # one of its legacy shims.  Revoking the state first would therefore
    # silently swallow that flag's report entry — the revocation must name
    # everything it actually cleared, or the drift audit's evidence trail
    # lies about which authority was live.
    for attr in _AUTHORITY_FLAGS:
        if bool(getattr(d, attr, False)):
            try:
                setattr(d, attr, False)
            except Exception:
                pass
            cleared.append(attr)

    before_state = getattr(d, "pre_tackle_state", None)
    if getattr(before_state, "name", None) in _AUTHORITY_STATE_NAMES:
        try:
            from game.defence.defence_lvl_1.pre_tackle_authority import (
                clear_pre_tackle_state,
            )
            clear_pre_tackle_state(d)
        except Exception:
            d.pre_tackle_state = None
        cleared.append("pre_tackle_state")

    # Corridor membership must go with the authority it fed.  Cleared
    # through the corridor's OWN release path so the latch, the frame
    # stamp and the membership counter all go together.
    if bool(getattr(d, "_pta_corridor", False)):
        try:
            from game.defence.defence_lvl_1.pretackle_corridor import (
                clear_corridor,
            )
            clear_corridor(d)
        except Exception:
            d._pta_corridor = False
        cleared.append("corridor")

    # Re-classify fresh: a promoted SECONDARY is itself an authority grant
    # (it unlocks the structured-close / engageable branch), so it must not
    # survive the strip.
    if getattr(d, "depth_class", None) in ("PRIMARY", "SECONDARY"):
        d.depth_class = "PASSIVE"
        d._prev_depth_class = None
        cleared.append("depth_class")

    return cleared


# ─────────────────────────────────────────────────────────────────────
# 3. THE DRIFT AUDIT
# ─────────────────────────────────────────────────────────────────────

def _authority_markers(d) -> list:
    """Return every authority marker *d* currently carries."""
    found: list = []
    role = getattr(d, "tackle_role", None)
    if role in ("PRIMARY", "SECONDARY"):
        found.append(f"tackle_role={role}")
    state_name = getattr(getattr(d, "pre_tackle_state", None), "name", None)
    if state_name in _AUTHORITY_STATE_NAMES:
        found.append(f"pre_tackle_state={state_name}")
    for attr in _AUTHORITY_FLAGS:
        if bool(getattr(d, attr, False)):
            found.append(attr)
    if bool(getattr(d, "_pta_corridor", False)):
        found.append("_pta_corridor")
    if getattr(d, "depth_class", None) in ("PRIMARY", "SECONDARY"):
        found.append(f"depth_class={d.depth_class}")
    return found


def audit_retreat_obligation_compliance(world, defenders,
                                        raise_on_violation: bool = False
                                        ) -> list[str]:
    """Return every obligated body that nonetheless holds tackle authority.

    THE drift guard.  A body that is still retreating must carry NO
    authority marker; any marker means an ownership path granted authority
    without consulting the veto.
    """
    violations: list[str] = []
    for d in (defenders or ()):
        if d is None:
            continue
        if not is_retreat_obligated(d, world):
            continue
        markers = _authority_markers(d)
        if not markers:
            continue
        violations.append(
            "RETREAT_OBLIGATION_AUTHORITY id=%s role=%s %s"
            % (
                getattr(d, "player_id", "?"),
                _role_name(d),
                ",".join(markers),
            )
        )
    if raise_on_violation and violations:
        raise AssertionError(
            "RETREAT_OBLIGATION VIOLATION (tackle authority held while the "
            "10 m retreat obligation is unsatisfied):\n  "
            + "\n  ".join(violations)
        )
    return violations


# ─────────────────────────────────────────────────────────────────────
# PER-FRAME SYNC — runs BEFORE every authority-acquisition writer
# ─────────────────────────────────────────────────────────────────────

def sync_retreat_obligation(world, defenders) -> list:
    """Revoke authority from every still-retreating body.  Returns them.

    Stateless: the obligation is read from the live role, so there is no
    latch to advance, clear or re-arm.  Returns the gated list, which is
    the probe's wiring proof (an empty list across a rollout would mean the
    enforcement is nominal, not live).
    """
    gated: list = []
    if world is None:
        return gated
    for d in (defenders or ()):
        if d is None:
            continue
        if not is_retreat_obligated(d, world):
            continue
        strip_tackle_authority(world, d)
        gated.append(d)
    return gated


def enforce_retreat_obligation(world, defenders) -> tuple[list, list]:
    """Apply the 10 m contract for THIS frame, in whatever era the role
    is live in.  Returns ``(gated, completed)``.

    WHY THIS EXISTS (measured live, 2026-09-18)
    -------------------------------------------
    The obligation is CREATED in the engine's ``_update_release``: every
    non-marker, non-DH, non-fullback defender becomes ``DEF_RETREAT_10``
    at DOMINANCE -> RELEASE.  It then lives through RELEASE, PTB_PREP,
    PTB_EXEC, PTB, POST_RUCK and OPEN_PLAY.

    But the veto and the completion test ran in exactly ONE place —
    ``DefenceManager.update_open_play`` — which returns immediately
    unless ``ope._phase == "OPEN_PLAY"``.  RELEASE and PTB are engine
    SUB-phases of ``world.phase == OPEN_PLAY``, so for that whole era:

      * an obligated body KEPT the tackle authority it had been granted
        on the previous true OPEN_PLAY frame — measured as
        ``depth_class=PRIMARY``/``SECONDARY``, the exact markers the
        drift audit names, and the field the tackle-eligibility check in
        ``open_play_engine`` reads.  1316 violations in one 20 s rollout.
      * it never took the ``depth_class == "PASSIVE"`` branch that the
        retreat movement contract keys on
        (``movement_mode_decision``: short of the line AND PASSIVE ->
        RETREAT), so it never converged on the line and NEVER discharged:
        every one of the 10 bodies ran the full 707-frame window.

    ONE DEFINITION OF EACH HALF
    ---------------------------
    1. REVOKE  — ``strip_tackle_authority``, the existing revocation.
    2. COMPLETE — ``x >= defensive_line_anchor_m(world)``, the engine's
       OWN arrival test from ``_update_post_ruck_open_play``.  No new
       threshold, no tolerance, no latch: the same comparison, reachable
       because this runs in every era instead of one.

    TES SAFETY
    ----------
    Callers invoke this only when the tackle-escape is not active
    (``world.tes_active`` is False).  During CONTACT/DRIVE/DOMINANCE the
    ruck X is a drive-entry value and a body upfield could satisfy the
    arrival test prematurely, so completion is never applied mid-TES —
    the engine's own POST_RUCK window is the earliest legal completion
    point, and this extends it forward, never backward.
    """
    gated: list = []
    completed: list = []
    if world is None:
        return gated, completed

    anchor = None
    for d in (defenders or ()):
        if d is None or not is_retreat_obligated(d, world):
            continue

        strip_tackle_authority(world, d)
        gated.append(d)

        # The retreat line is read ONCE per call, lazily, and only after
        # an obligated body is actually found — so a frame with no
        # obligation pays nothing.  FUNCTION-LOCAL import keeps this
        # module free of project-scope imports (import safety, see
        # module docstring).
        if anchor is None:
            try:
                from game.defence.defence_lvl_1.anchor_authority import (
                    defensive_line_anchor_m,
                )
                anchor = float(defensive_line_anchor_m(world))
            except Exception:
                anchor = float("nan")

        try:
            reached = float(getattr(d, "x", 0.0) or 0.0) >= anchor
        except (TypeError, ValueError):
            reached = False
        if not reached:
            continue

        # RETREAT COMPLETE — the engine's own milestone.
        from player.player import DefenderRole
        d.defender_role = DefenderRole.DEF_LINE_READY
        completed.append(d)

    return gated, completed


def format_obligation_report(world, defenders) -> str:
    """Compact one-line rendering of the obligation state (logging only)."""
    rows = [
        "id=%s" % getattr(d, "player_id", "?")
        for d in (defenders or ())
        if is_retreat_obligated(d, world)
    ]
    if not rows:
        return "retreat_obligation=clear"
    return "retreat_obligation=%d [%s]" % (len(rows), ", ".join(rows))
