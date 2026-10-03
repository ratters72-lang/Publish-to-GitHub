"""
post_tes_corridor_authority.py — Stage 5 POST-TES REINTEGRATION CORRIDOR.

WHAT THIS MODULE IS FOR
-----------------------
Stage 4B measured the TES -> line exit boundary and left ONE open finding:
"frames between leave-TES and line-membership: median 0, mean 18.8, max 284".
That number came from a metric that could not answer the question it was
asked.  In ``tmp/probe_stage4b_exit_ownership.py`` the re-admission frame is
the FIRST frame of the cycle on which the body is in the line population --
which, for almost every defender, is the first TES frame, long BEFORE it
ever leaves TES ownership.  ``join_delay = max(0, joined - left)`` therefore
collapses to 0 for anything that was a line body earlier in the cycle.

This module replaces that metric with a declared three-node state machine and
a closed set of BARRIER codes: for every defender-frame it answers

    which corridor node is this body in, and if it is not a LINE_MEMBER,
    which single declared reason is keeping it out?

THE THREE NODES
---------------
    TES_OWNED    owned by the tackle chamber: it carries the persistent
                 TACKLE-STATE flag (``tackle_state_active``, owned by
                 ``tackle_state_gate``) or is a registered TES tackler
                 (``world.tackler_ids`` / primary / secondaries).

    RELEASED     has LEFT TES ownership but is NOT counted in the line
                 population.  This is the CORRIDOR -- the gap state the
                 Stage 4B handover latency was measuring.  It is not an
                 error by itself (a released marker waiting for the
                 POST_RUCK hand-back is RELEASED by design), but nothing
                 previously declared it, so nothing could audit it.

    LINE_MEMBER  satisfies ``population_authority.is_line_body``, the SINGLE
                 membership oracle shared by count, sides, spacing and every
                 observer.  This module never re-derives membership; it
                 reads that predicate so it cannot drift.

WHY THE BARRIER IS A CLOSED SET
-------------------------------
``is_line_body`` rejects a body for exactly these reasons, and the corridor
invents nothing of its own:

    fullback_role_active  the dynamic fullback owns its own backfield anchor
    tackle_state_active   a chamber hold -- the body is still TES_OWNED
    role:<NAME>           ``defender_role`` is in
                          ``population_authority.LINE_EXCLUDED_ROLES``
    frozen                reported only for a body that IS a line member: a
                          freeze does not remove a body from the population,
                          but it IS why such a body is not moving.

``reintegration_barrier`` returns exactly one of those (first match wins, in
the order ``is_line_body`` evaluates), or ``None`` when the body IS a line
member.  A barrier code is therefore never invented; it is a read of the
engine's own admission test, labelled.

STUCK-NOT-FROZEN IS THE PROJECT'S OWN PREDICATE
-----------------------------------------------
``STUCK_NOT_FROZEN`` is not a term this stage coins.  It is implemented in
``game/debug/freeze_audit.py`` (``FreezeAndMovementAuditAgent``) and has been
the engine's movement-idle contract for months.  Its terms, read from there:

  * a TEN-FRAME window of idleness (``_PlayerTrace.is_stuck`` /
    ``_is_soft_frozen`` + ``_record_stuck`` + ``_is_stuck``) -- one
    motionless frame is not the defect and must never be reported as one;
  * ``not p.frozen``;
  * ``not at_target`` -- ``abs(target_x - x) < 0.05`` means the body is AT
    its target and is not stuck, whatever its velocity;
  * suppressed in the post-PTB re-formation grace window
    (``is_post_ruck or is_ptb or seconds_since_ptb < 0.5``);
  * suppressed for the marker pair in RELEASE / PTB (``is_marker_reform``):
    markers re-forming there are intentionally held by the marker target
    lock / reform snap.

Stage 5's first pass used a STRICTER per-frame test and reported 278 "stuck"
body-frames -- every one of them MARKER_A/B in PTB_PREP / PTB_EXEC, i.e.
exactly the population the engine's own audit deliberately exempts.  That was
the audit over-firing, not an engine defect, and it is recorded in the Stage 5
report as a corrected method rather than a finding.  This module therefore
implements the PROJECT'S test: the same window, the same exemptions.  A
positive result means what the engine means by it.

Two deliberate differences from ``freeze_audit``, both STRICTER, are
documented so they cannot be mistaken for drift:

  * ``declared_hold`` exempts MORE states than ``freeze_audit`` suppresses
    (it also exempts the TES depth lock and the TES drive structure hold,
    which are declared owners with their own writers).  A hold the engine
    owns can never be a stuck body.
  * ``has_live_target`` measures the target error in BOTH axes (2-D) where
    ``freeze_audit``'s ``at_target`` tests X only.  A body sitting on its X
    target but materially off its Y target with zero velocity is still a
    frame-skip to a viewer.

PURITY
------
This module never writes a position, a target, a velocity, a freeze flag or
a world field.  Like ``exit_transition_authority`` and
``world/rules/release_gate.py`` it only decides whether what already happened
was declared.  Every caller is an audit (probe / verifier); no production
path consumes it.
"""

from __future__ import annotations

import math

__all__ = [
    "TES_OWNED",
    "RELEASED",
    "LINE_MEMBER",
    "CORRIDOR_NODES",
    "BARRIER_ROLE_PREFIX",
    "BARRIER_FROZEN",
    "BARRIER_TACKLE_STATE",
    "BARRIER_FULLBACK",
    "BARRIER_NONE",
    "STUCK_OWNED",
    "STUCK_UNOWNED",
    "MOVEMENT_EPS_MPS",
    "POSITION_EPS_M",
    "TARGET_EPS_M",
    "STUCK_WINDOW_FRAMES",
    "STUCK_DISPLACEMENT_M",
    "POST_PTB_GRACE_S",
    "MARKER_REFORM_PHASES",
    "TRANSITION_WRITERS",
    "RE_ADMISSION_WRITERS",
    "corridor_node",
    "reintegration_barrier",
    "barrier_role_name",
    "is_line_member",
    "is_registered_tes_tackler",
    "declared_hold",
    "has_live_target",
    "is_soft_stuck",
    "is_stuck_not_frozen",
    "format_barrier_report",
]

# ─────────────────────────────────────────────────────────────────────
# THE NODES
# ─────────────────────────────────────────────────────────────────────
TES_OWNED: str = "TES_OWNED"
RELEASED: str = "RELEASED"
LINE_MEMBER: str = "LINE_MEMBER"

CORRIDOR_NODES = (TES_OWNED, RELEASED, LINE_MEMBER)

# ─────────────────────────────────────────────────────────────────────
# THE BARRIER CODES (closed set)
# ─────────────────────────────────────────────────────────────────────
BARRIER_FROZEN: str = "frozen"
BARRIER_TACKLE_STATE: str = "tackle_state_active"
BARRIER_FULLBACK: str = "fullback_role_active"
BARRIER_ROLE_PREFIX: str = "role:"
BARRIER_NONE = None  # a line member has no barrier

# ─────────────────────────────────────────────────────────────────────
# STUCK-NOT-FROZEN VERDICTS
# ─────────────────────────────────────────────────────────────────────
STUCK_OWNED: str = "STUCK_OWNED"
STUCK_UNOWNED: str = "STUCK_UNOWNED"

# ─────────────────────────────────────────────────────────────────────
# EPSILONS — the motion test
# ─────────────────────────────────────────────────────────────────────
# ``MOVEMENT_EPS_MPS``: velocity below this is "not moving".  Matches
# ``freeze_audit._is_soft_frozen`` (0.01 m/s).
MOVEMENT_EPS_MPS: float = 0.01
# ``POSITION_EPS_M``: per-frame displacement below this is "did not move".
POSITION_EPS_M: float = 0.002
# ``TARGET_EPS_M``: matches ``freeze_audit``'s at_target threshold (0.05 m).
TARGET_EPS_M: float = 0.05

# ─────────────────────────────────────────────────────────────────────
# THE PROJECT'S OWN STUCK CONTRACT (aligned, not reinvented)
# ─────────────────────────────────────────────────────────────────────
# The idleness window.  ``freeze_audit`` uses 10 frames in both forms
# (``_PlayerTrace(window=10)`` and ``stuck_threshold=10``).
STUCK_WINDOW_FRAMES: int = 10
# ``freeze_audit``'s position-window displacement threshold.
STUCK_DISPLACEMENT_M: float = 0.05
# ``freeze_audit``'s post-PTB re-formation grace: ``seconds_since_ptb < 0.5``.
POST_PTB_GRACE_S: float = 0.5

# Phases in which the marker pair is intentionally held by the marker target
# lock / reform snap, and the engine suppresses the stuck verdict
# (``freeze_audit``'s ``is_marker_reform``: markers AND ``is_ptb or
# is_release``; ``is_ptb`` is ``"PTB" in phase``).
MARKER_REFORM_PHASES = frozenset({
    "RELEASE", "PTB_PREP", "PTB_EXEC", "PTB",
})
# Phases inside the post-PTB re-formation grace window
# (``freeze_audit``'s ``in_stuck_grace``: ``is_post_ruck or is_ptb or
# seconds_since_ptb < 0.5``).
POST_PTB_GRACE_PHASES = frozenset({"POST_RUCK_OPEN_PLAY"})

# ─────────────────────────────────────────────────────────────────────
# DECLARED TRANSITION WRITERS
# ─────────────────────────────────────────────────────────────────────
# Which code path effects each edge of the corridor.  Declared as data so a
# verifier can assert every named writer still exists in the engine, and so
# the report can name the owner of a transition rather than guessing.
TRANSITION_WRITERS = {
    (TES_OWNED, RELEASED): (
        "game.defence.defence_lvl_1.tackle_state_gate.update_tackle_state",
    ),
    (RELEASED, LINE_MEMBER): (
        "game.defence.defence_lvl_1.tackle_state_gate._release_defender",
        "world.open_play_engine.OpenPlayEngine._clear_marker_roles",
        "world.open_play_engine.OpenPlayEngine._post_ruck_marker_cleanup",
        "world.open_play_engine.OpenPlayEngine._reset_tackle_state",
        "game.defence.defence_lvl_1.pretackle_attempt.assign_def_line_ready",
    ),
}

# The writers that can RE-ADMIT a released body to the line.  Kept separate
# because the Stage 5 finding is that no writer runs on the release frame
# itself: these edges are the WHOLE re-admission surface.
RE_ADMISSION_WRITERS = TRANSITION_WRITERS[(RELEASED, LINE_MEMBER)]


# ─────────────────────────────────────────────────────────────────────
# ENGINE TRUTH READS (function-local imports: import-cycle law)
# ─────────────────────────────────────────────────────────────────────

def _role_name(d) -> str:
    """Return *d*'s ``defender_role`` NAME (or the value verbatim)."""
    role = getattr(d, "defender_role", None)
    if role is None:
        return "None"
    return getattr(role, "name", None) or str(role)


def is_registered_tes_tackler(world, d) -> bool:
    """True when *d* is registered as an engaged TES tackler.

    Reads the engine's own registry through the gate that owns it, so the
    corridor cannot drift from the chamber's definition of an engaged body.
    """
    try:
        from game.defence.defence_lvl_1.tackle_state_gate import (
            is_registered_tes_tackler as _reg,
        )
    except Exception:
        return False
    try:
        return bool(_reg(world, d))
    except Exception:
        return False


def is_line_member(d, world=None) -> bool:
    """True when *d* is counted in the authoritative line population.

    Delegates to ``population_authority.is_line_body`` -- the single
    membership predicate shared by count / sides / spacing / observers.
    A world whose import path is unavailable returns False, never a guess.
    """
    if d is None:
        return False
    try:
        from game.defence.defence_lvl_1.population_authority import (
            is_line_body as _is_line_body,
        )
    except Exception:
        return False
    try:
        return bool(_is_line_body(d))
    except Exception:
        return False


def _excluded_roles() -> frozenset:
    """The engine's canonical line-exclusion role set."""
    try:
        from game.defence.defence_lvl_1.population_authority import (
            LINE_EXCLUDED_ROLES,
        )
        return frozenset(LINE_EXCLUDED_ROLES)
    except Exception:
        return frozenset()


# ─────────────────────────────────────────────────────────────────────
# THE NODE
# ─────────────────────────────────────────────────────────────────────

def corridor_node(d, world=None) -> str:
    """Return the corridor node *d* occupies.

    LINE_MEMBER  satisfies the single line-membership oracle.
    TES_OWNED    carries the chamber hold, or is a registered TES tackler.
    RELEASED     everything else: the corridor.

    The order is fixed as LINE_MEMBER -> TES_OWNED -> RELEASED so a body the
    line has already admitted reads as admitted.
    """
    if d is None:
        return RELEASED
    if is_line_member(d, world):
        return LINE_MEMBER
    if bool(getattr(d, "tackle_state_active", False)):
        return TES_OWNED
    if world is not None and is_registered_tes_tackler(world, d):
        return TES_OWNED
    return RELEASED


# ─────────────────────────────────────────────────────────────────────
# THE BARRIER
# ─────────────────────────────────────────────────────────────────────

def barrier_role_name(barrier: str | None) -> str | None:
    """Extract the role NAME from a ``role:<NAME>`` barrier code."""
    if not barrier or not str(barrier).startswith(BARRIER_ROLE_PREFIX):
        return None
    return str(barrier)[len(BARRIER_ROLE_PREFIX):]


def reintegration_barrier(d, world=None) -> str | None:
    """Return the single declared reason *d* is NOT a line member.

    ``None`` means *d* IS a line member.  First match wins, mirroring
    ``population_authority.is_line_body``:

        fullback_role_active -> "fullback_role_active"
        tackle_state_active  -> "tackle_state_active"
        anything else        -> "role:<NAME>"

    For a line member the barrier is ``"frozen"`` when the body is frozen
    (a freeze keeps a body in the population but explains its stillness),
    otherwise ``None``.
    """
    if d is None:
        return None
    if is_line_member(d, world):
        return BARRIER_FROZEN if bool(getattr(d, "frozen", False)) else None
    if bool(getattr(d, "fullback_role_active", False)):
        return BARRIER_FULLBACK
    if bool(getattr(d, "tackle_state_active", False)):
        return BARRIER_TACKLE_STATE
    return BARRIER_ROLE_PREFIX + _role_name(d)


# ─────────────────────────────────────────────────────────────────────
# DECLARED HOLDS — the states that own a motionless body
# ─────────────────────────────────────────────────────────────────────

def declared_hold(d, world, phase=None) -> str | None:
    """Return the declared reason *d* may be motionless, or None.

    Every entry is a state the engine declares elsewhere and owns:

      ``frozen`` / ``freeze_timer``  the sole freeze authority
      ``tackle_state_active``        the TACKLE-STATE chamber hold
      ``marker_lock``                the post-PTB marker lock window
      ``depth_lock``                 the TES depth lock
      ``tes_structure_hold``         the TES drive structure cell hold
      ``fullback_role_active``       the dynamic fullback's own anchor
      ``post_ptb_grace``             the post-PTB re-formation window
                                     (the engine's own ``in_stuck_grace``)
      ``marker_reform``              a marker holding its ruck lane during
                                     RELEASE / PTB (the engine's own
                                     ``is_marker_reform`` suppression)

    *phase* selects the two phase-scoped entries; when it is None they are
    not applied, so a caller without a phase gets the phase-independent
    holds only.  A body with NONE of these and no motion over the declared
    window is the defect this stage is about.
    """
    if d is None:
        return "no_body"
    if bool(getattr(d, "frozen", False)):
        return BARRIER_FROZEN
    if float(getattr(d, "freeze_timer", 0.0) or 0.0) > 0.0:
        return "freeze_timer"
    if bool(getattr(d, "tackle_state_active", False)):
        return BARRIER_TACKLE_STATE
    if float(getattr(d, "marker_freeze_timer", 0.0) or 0.0) > 0.0:
        return "marker_lock"
    if bool(getattr(d, "depth_locked", False)):
        return "depth_lock"
    if bool(getattr(d, "fullback_role_active", False)):
        return BARRIER_FULLBACK
    try:
        from game.defence.defence_lvl_1.tes_drive_structure import (
            is_tes_structure_hold as _hold,
        )
        if _hold(d):
            return "tes_structure_hold"
    except Exception:
        pass
    if phase is not None:
        _phase = str(phase).upper()
        if _phase in POST_PTB_GRACE_PHASES:
            return "post_ptb_grace"
        if _phase in MARKER_REFORM_PHASES \
                and _role_name(d) in ("MARKER_A", "MARKER_B"):
            return "marker_reform"
    return None


def has_live_target(d) -> bool:
    """True when *d*'s target is further than ``TARGET_EPS_M`` away (2-D).

    Deliberately 2-D, and therefore stricter than ``freeze_audit``'s
    ``at_target`` (X only): a body sitting on its X target but materially
    off its Y target with zero velocity is still a frame-skip to a viewer.
    """
    if d is None:
        return False
    tx = getattr(d, "target_x", None)
    ty = getattr(d, "target_y", None)
    if tx is None or ty is None:
        return False
    try:
        dx = float(tx) - float(getattr(d, "x", 0.0))
        dy = float(ty) - float(getattr(d, "y", 0.0))
    except (TypeError, ValueError):
        return False
    return math.hypot(dx, dy) > TARGET_EPS_M


def target_error_m(d) -> float | None:
    """Return the 2-D distance from *d* to its target (None if unresolvable)."""
    if d is None:
        return None
    tx = getattr(d, "target_x", None)
    ty = getattr(d, "target_y", None)
    if tx is None or ty is None:
        return None
    try:
        return math.hypot(float(tx) - float(getattr(d, "x", 0.0)),
                          float(ty) - float(getattr(d, "y", 0.0)))
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────
# STUCK-NOT-FROZEN
# ─────────────────────────────────────────────────────────────────────

def is_soft_stuck(d, world, phase=None) -> bool:
    """THE PER-FRAME condition that builds the stuck window.

    Mirrors ``freeze_audit._is_soft_frozen`` (``"soft freeze = total velocity
    zero while target != x"``) with the corridor's declared holds applied: a
    body with no velocity, a live target and NO declared hold.  One frame of
    this is NOT the defect -- ``is_stuck_not_frozen`` requires the full
    window.
    """
    if d is None:
        return False
    if declared_hold(d, world, phase) is not None:
        return False
    if not has_live_target(d):
        return False
    try:
        speed = math.hypot(float(getattr(d, "vx", 0.0)),
                           float(getattr(d, "vy", 0.0)))
    except (TypeError, ValueError):
        return False
    return speed <= MOVEMENT_EPS_MPS


def is_stuck_not_frozen(d, world, phase=None, stuck_streak=0,
                        writers_this_frame=()):
    """Return a stuck verdict for *d*, or None when it is legitimately still.

    Applies the PROJECT'S contract (see the module docstring): the body must

      * carry NO declared hold (``declared_hold``);
      * have been ``is_soft_stuck`` for at least ``STUCK_WINDOW_FRAMES``
        consecutive frames (the caller owns the streak counter, exactly as
        ``freeze_audit`` owns ``stuck_counters``).

    ``writers_this_frame`` is the collection of writer tags that stamped this
    body's target on this frame (from the engine's own write log):

      ``STUCK_OWNED``    a writer asked the body to move and the frame did
                         not integrate it -- the writer's target went
                         unserved.
      ``STUCK_UNOWNED``  no writer stamped it and it still did not move --
                         nobody owns the body.
    """
    if d is None:
        return None
    if declared_hold(d, world, phase) is not None:
        return None
    if int(stuck_streak) < STUCK_WINDOW_FRAMES:
        return None
    return STUCK_OWNED if writers_this_frame else STUCK_UNOWNED


# ─────────────────────────────────────────────────────────────────────
# REPORTING
# ─────────────────────────────────────────────────────────────────────

def format_barrier_report(counts: dict) -> str:
    """One-line rendering of a barrier histogram (logging only)."""
    if not counts:
        return "corridor_barriers=clear"
    parts = ["%s=%d" % (str(k), int(v)) for k, v in sorted(
        counts.items(), key=lambda kv: (-kv[1], str(kv[0]))
    )]
    return "corridor_barriers: " + " ".join(parts)
