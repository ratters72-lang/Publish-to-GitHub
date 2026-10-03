"""line_break_static_guard.py — recovery guard for a static target_x after a line break.

PROBLEM
-------
After the ball carrier breaks the defensive line (``bc.x > anchor_x + 1.0``),
several writers can still pin a defender's ``target_x`` to a **static** value
before the frame is integrated:

  - Task-VI engaged-non-lane chase (``defensive_behaviour_unified`` step 7a)
    computes ``engaged_target_x = max(anchor_x, bc.x - def_line_depth_m * 0.7)``.
    When the BC is only just past the line that collapses to ``anchor_x`` — a
    per-ruck constant — so the defender sits on the anchor instead of closing.
  - ``enforce_retreat_to_anchor`` and the MMDL X-drift guard both re-pin
    forward / PASSIVE defenders back to ``anchor_x``.
  - The early ``_force_immediate_defence_reaction`` / step-7a collapse writers
    set ``target_x = bc.x`` + CHASE, but the anchor envelope, band resolve and
    the target_x glide cap run AFTER them and can re-pin the target to a static
    value before ``Player.update(dt)`` integration.

SYMPTOM
-------
``target_x`` is static, so no ``vx`` is produced.  The defender never closes
into ``TACKLE_RADIUS_M``, so ``_check_pre_tackle`` never fires and the tackle
pipeline never arms.  The pre-tackle classifier's ``is_closing_posture``
likewise requires ``movement_state in (CHASE, SHOOT)``, so CHASE must also be
forced.

LAW (this module)
-----------------
The LAST X recovery write of the OPEN_PLAY dispatch (invoked after every
writer, after the writer-priority-band resolve and the catch-X authority, and
just before movement integration).  For every eligible genuine line defender
whose ``target_x`` is static *after a line break* and is not already closing on
the live BC:

    ``target_x``        := ``float(bc.x)``
    ``movement_state``  := ``MovementState.CHASE``
    plus: ``frozen = False``, all timing/retreat locks cleared, and
    ``move_speed`` restored to the unified defensive ceiling.

This guarantees forward velocity toward the carrier so the tackle pipeline can
arm again.  It is a pure recovery guard: it never mutates roles, spacing, width
or anchor authority, and it yields entirely when the line is not broken or the
defender already owns a live chase target (committed tacklers / pursuit-commit
bodies are excluded).

EXEMPT (never touched — dedicated owners remain)
------------------------------------------------
  - fullback, markers, DUMMY_HALF, TACKLER_*, KICK_CHASE
  - frozen / freeze-timer bodies
  - ``tackle_state_active`` holders
  - ``_tackle_commit_active`` / ``_pursuit_commit_active`` bodies (already chase)
  - pre-tackle-managed COMMIT/PLANTED owners
  - CORRIDOR-OWNED bodies (pre-tackle corridor, 2026-09-15).  This guard
    restores ``move_speed`` to the FULL defensive ceiling, so it is the
    single most destructive writer for a corridor body: leaving it active
    would replace the governed slowdown taper with a full-speed CHASE on
    exactly the frames before contact.  The corridor is the sole motion
    authority for its bodies; they are excluded unconditionally.
"""

from __future__ import annotations

from collections import deque

from player.movement_states import MovementState

# The ``target_source`` stamp this guard writes.  Registered in
# writer_priority_bands._SOURCE_PRIORITY at the commit tier.
LINE_BREAK_STATIC_GUARD_SOURCE: str = "line_break_static_guard"

# Rolling window of ``target_x`` values used to detect a frame-to-frame static
# target.  A window must be full and its range below ``_LB_STATIC_EPS_M`` to be
# considered temporally static (mirrors freeze_audit's 10-frame target history,
# but shorter so the guard reacts faster).
_LB_STATIC_WINDOW_FRAMES: int = 6

# Range of the rolling ``target_x`` window below which the target is static.
_LB_STATIC_EPS_M: float = 0.1

# When ``abs(target_x - d.x)`` is below this the writer pinned the target to
# the body's own X — the immediate "target-suppressed" static case (freeze_audit
# uses 0.01; we use 0.05 so a sub-5cm pin is caught the same frame).
_LB_STATIC_SUPPRESSED_EPS_M: float = 0.05

# A body whose ``target_x`` is within this of the live ``bc.x`` is already
# closing on the carrier — the guard must not re-target it.
_LB_STATIC_MIN_BC_RELATIVE_M: float = 1.0

# When ``abs(target_x - anchor_x)`` is below this the writer pinned the
# target to the STATIC defensive anchor (``ruck_x + 10``) — the classic
# re-pin after a line break.  The anchor is a per-ruck constant, so a
# body targeting it produces no forward closure on the moving BC.  This is
# the immediate "pinned to the anchor" static signal.
_LB_STATIC_ANCHOR_EPS_M: float = 0.1

# Exempt role names mirror movement_mode_decision._TACKLE_RADIUS_EXEMPT_ROLE_NAMES
# so this guard and the tackle-radius CHASE switch agree on which bodies are
# genuine line defenders.
_EXEMPT_ROLE_NAMES = frozenset({
    "MARKER_A",
    "MARKER_B",
    "DUMMY_HALF",
    "TACKLER_1",
    "TACKLER_2",
    "TACKLER_3",
    "KICK_CHASE",
})


def _fb_shared_recovery_allowed(world, d) -> bool:
    """Stage 3 — may a SHARED recovery pathway name *d*'s recovery?

    THE RECOVERY-OWNERSHIP GATE.  Single-sourced through
    ``fullback_controller.shared_recovery_allowed`` so this guard and the other
    three shared recovery pathways (freeze-expiry, post-miss pursuit, the
    static-pin clearance) can never disagree about who owns a tagged
    fullback's recovery.

    TOTAL: an unreachable controller, or any failure inside it, reports True,
    which leaves the historical per-module fullback check below as the only
    arbiter -- i.e. exactly the pre-Stage-3 behaviour, never a silent widening
    of the guard's reach.
    """
    try:
        from game.defence.defence_lvl_1.fullback_controller import (
            shared_recovery_allowed as _allowed,
        )
    except Exception:                                    # noqa: BLE001
        return True
    try:
        return bool(_allowed(world, d))
    except Exception:                                    # noqa: BLE001
        return True


def is_static_guard_eligible(d, world=None) -> bool:
    """True when *d* is a genuine line body this guard may re-arm.

    The RECOVERY predicate plus this guard's two FREEZE GATES: a body that
    is ``frozen`` or still carrying a live ``freeze_timer`` is held by the
    freeze authority this frame and is never re-armed by the guard (the
    guard restores the FULL defensive ceiling speed and forces CHASE, so it
    must not run while the body is still being held on the ground).
    """
    if d is None:
        return False
    if bool(getattr(d, "frozen", False)):
        return False
    if float(getattr(d, "freeze_timer", 0.0) or 0.0) > 0.0:
        return False
    return is_static_guard_recovery_eligible(d, world)


def is_static_guard_recovery_eligible(d, world=None) -> bool:
    """True when *d* is a genuine line body a post-break recovery may re-target.

    THE RECOVERY HALF of ``is_static_guard_eligible``, WITHOUT the two freeze
    gates.  This is the single definition of "a body that may be handed a
    live pursuit point after a line break": it is read by this guard's own
    re-arm write AND by ``freeze_expiry_pursuit``'s expiry hand-back, so the
    two writers can never disagree about which bodies are genuine line
    bodies, nor about which dedicated owners stay untouched.

    Mirrors ``_is_genuine_line_defender`` (movement_mode_decision) for the
    role / fullback / tackle-state gates, and additionally skips
    already-committed bodies (``_tackle_commit_active`` /
    ``_pursuit_commit_active`` and pre-tackle COMMIT/PLANTED owners) so a
    recovery never fights an existing live chase.
    """
    if d is None:
        return False
    # CORRIDOR EXEMPTION (2026-09-15): the corridor owns move_speed for its
    # bodies, and this guard's recovery writes the FULL ceiling speed.  Never
    # fight the corridor -- it is the sole motion authority for its bodies.
    # (`_tackle_commit_active` below would usually cover this already, but the
    # exemption is stated explicitly so the guard cannot be re-enabled for a
    # corridor body by a future change to the commit-flag semantics.)
    from game.defence.defence_lvl_1.pretackle_corridor import (
        is_corridor_owned as _is_corridor_owned,
    )
    if _is_corridor_owned(d):
        return False
    # RETREAT-OBLIGATION VETO (2026-09-18): a body that still owes its 10 m
    # retreat may not hold tackle authority, and this guard is the single
    # most authority-granting writer of the dispatch -- it restores the FULL
    # defensive ceiling speed and forces CHASE.  An obligated body's retreat
    # target IS the static anchor, which is exactly the signal this guard
    # hunts for, so without this veto it re-armed the body into a chase on
    # every frame: measured live as bodies sitting ~8 m short of the line
    # for the whole rollout with target_source "line_break_static_guard",
    # movement_state CHASE, and target_x racing away with the BC while the
    # anchor stayed fixed.
    from game.defence.defence_lvl_1.retreat_obligation import (
        is_retreat_obligated as _is_retreat_obligated,
    )
    # ── POST-MISS PURSUIT CARVE-OUT (2026-09-26) ──
    # A defender the carrier beat OUTRIGHT is ENTITLED TO PURSUE for a
    # bounded window after the miss (``post_miss_pursuit``).  The veto above
    # exists so a still-retreating body is not re-armed into a chase on every
    # frame -- but that is exactly the wrong answer for a beaten defender with
    # a LIVE line break, which is the only case the entitlement admits (it
    # requires the canonical ``bc.x > anchor_x + 1.0`` predicate).
    #
    # This carve-out is ALSO what un-vetoes the expiry hand-back:
    # ``freeze_expiry_pursuit.is_expiry_pursuit_eligible`` delegates to this
    # recovery predicate, so the live-break hand-back and this guard regain
    # the SAME eligible set in one place -- no drift is possible between the
    # three pursuit writers.
    from game.defence.defence_lvl_1.post_miss_pursuit import (
        owns_post_miss_pursuit as _owns_post_miss_pursuit,
    )
    if _is_retreat_obligated(d, world) and not _owns_post_miss_pursuit(
        world, d
    ):
        return False
    # ── FULLBACK RECOVERY OWNERSHIP (Stage 3, 2026-09-30) ──
    # The tagged fullback owns its OWN recovery (misses, owed-ground travel and
    # break re-engagement are ``fullback_role`` + ``fullback_controller``
    # behaviours), so neither this recovery predicate nor the guard built on it
    # may name a target, a state or a pace for that body.  The check below is
    # the historical tag test PLUS the controller's own ownership predicate, so
    # the denied set is identical with the recovery lever ON or OFF (OFF simply
    # leaves the tag test as the sole arbiter).  It is deliberately the SAME
    # gate ``freeze_expiry_pursuit`` and ``post_miss_pursuit`` read, so the
    # three pursuit writers cannot drift apart about the last defender.
    if not _fb_shared_recovery_allowed(world, d) or bool(
        getattr(d, "fullback_role_active", False)
    ):
        return False
    if bool(getattr(d, "tackle_state_active", False)):
        return False
    if bool(getattr(d, "_tackle_commit_active", False)):
        return False
    if bool(getattr(d, "_pursuit_commit_active", False)):
        return False

    # Pre-tackle COMMIT / PLANTED owners chase a live destination; never re-arm.
    from game.defence.defence_lvl_1.pre_tackle_authority import (
        PreTackleState,
        is_pre_tackle_managed,
    )
    if is_pre_tackle_managed(d):
        _st = getattr(d, "pre_tackle_state", None)
        if _st in (PreTackleState.COMMIT, PreTackleState.PLANTED):
            return False

    role = getattr(d, "defender_role", None)
    role_name = (
        getattr(role, "name", None) or str(role) if role is not None else None
    )
    if role_name in _EXEMPT_ROLE_NAMES:
        return False
    return True


def advance_static_window(d) -> None:
    """Append *d*'s current ``target_x`` to its rolling static window."""
    window = getattr(d, "_lb_static_tx_window", None)
    if window is None:
        window = deque(maxlen=_LB_STATIC_WINDOW_FRAMES)
        setattr(d, "_lb_static_tx_window", window)
    window.append(float(getattr(d, "target_x", 0.0) or 0.0))


def clear_static_window(d) -> None:
    """Drop *d*'s static window (fresh frame / non-line-break reset)."""
    if hasattr(d, "_lb_static_tx_window"):
        del d._lb_static_tx_window


def _window_is_static(d) -> bool:
    """True when *d*'s rolling ``target_x`` window is full and flat."""
    window = getattr(d, "_lb_static_tx_window", None)
    if window is None or len(window) < _LB_STATIC_WINDOW_FRAMES:
        return False
    return (max(window) - min(window)) < _LB_STATIC_EPS_M


def is_target_x_static(d, world=None, bc=None, anchor_x=None) -> bool:
    """True when *d*'s ``target_x`` is static *and* is not already closing on bc.

    Pure predicate: it reads existing state (including ``_lb_static_tx_window``)
    but never mutates the window or the defender.  ``enforce_line_break_static_guard``
    is responsible for advancing the window.

    Three static signals are recognised (each mirrors a way a writer can pin a
    target after a line break):
      1. **Pinned to the anchor** — ``abs(target_x - anchor_x)`` <
         ``_LB_STATIC_ANCHOR_EPS_M``: the writer re-pinned the target to the
         STATIC defensive anchor (``ruck_x + 10``) — a per-ruck constant that
         produces no forward closure on the moving BC.
      2. **Immediate / suppressed** — ``abs(target_x - d.x)`` <
         ``_LB_STATIC_SUPPRESSED_EPS_M``: the writer pinned the target to the
         body's own X, so no velocity is produced this frame.
      3. **Temporal / flat** — the rolling window range is below
         ``_LB_STATIC_EPS_M``: the target is frame-to-frame constant.
    """
    if d is None or bc is None:
        return False
    tx = float(getattr(d, "target_x", 0.0) or 0.0)
    # A body already targeting (roughly) the live BC is closing — not static.
    if abs(tx - float(getattr(bc, "x", 0.0) or 0.0)) < _LB_STATIC_MIN_BC_RELATIVE_M:
        return False
    if anchor_x is not None and abs(tx - float(anchor_x)) < _LB_STATIC_ANCHOR_EPS_M:
        return True
    if abs(tx - float(getattr(d, "x", 0.0) or 0.0)) < _LB_STATIC_SUPPRESSED_EPS_M:
        return True
    return _window_is_static(d)


def enforce_line_break_static_guard(world, defenders, bc=None, tuners=None) -> list:
    """Re-arm every eligible defender whose ``target_x`` is static after a line break.

    This is the LAST X recovery write of the OPEN_PLAY dispatch.  It runs after
    every writer (and after the writer-priority-band resolve and the catch-X
    authority) and just before movement integration, so nothing else can re-pin
    the target afterward.

    For each eligible genuine line defender whose ``target_x`` is static after a
    line break (and not already closing on the BC): overwrite ``target_x`` with
    the live ``bc.x``, force ``movement_state = CHASE``, clear all timing /
    retreat locks, restore ``frozen = False`` and ceiling speed, and stamp
    ``target_source = "line_break_static_guard"`` — so the tackle pipeline can
    arm again.

    When the line is NOT broken (or ``bc`` is None) all windows are cleared and
    the guard is a no-op (returns ``[]``).

    Returns the list of re-armed defenders.
    """
    if bc is None:
        bc = getattr(world, "ball_carrier", None)

    # Line-break gate — single-sourced from movement_mode_decision
    # (``bc.x > anchor_x + 1.0``).
    from game.defence.defence_lvl_1.movement_mode_decision import (
        carrier_past_defensive_line,
    )
    if bc is None or not carrier_past_defensive_line(world, bc):
        for d in defenders:
            clear_static_window(d)
        return []

    # The anchor is the single source of the static re-pin; read it lazily.
    from game.defence.defence_lvl_1.anchor_authority import (
        defensive_line_anchor_m as _anchor_m,
    )
    anchor_x = float(_anchor_m(world))

    ceiling = float(getattr(tuners, "max_defensive_speed_mps", 8.3)) if tuners is not None else 8.3
    rearmed: list = []
    for d in defenders:
        # ── FULLBACK RECOVERY OWNERSHIP (Stage 3, 2026-09-30) ──
        # The dispatch's LAST X recovery write must not seize a body whose
        # recovery the controller owns; the gate is stated explicitly here as
        # well as inside the eligibility predicate so this guard's own contract
        # ("re-arm a static post-line-break line body") is readable in one
        # place.  (The predicate already denies the same bodies, so this is a
        # declaration, not a behaviour change.)
        if not _fb_shared_recovery_allowed(world, d):
            clear_static_window(d)
            continue
        if not is_static_guard_eligible(d, world):
            clear_static_window(d)
            continue
        advance_static_window(d)
        if not is_target_x_static(d, world, bc, anchor_x=anchor_x):
            continue

        # ── OVERWRITE with the LIVE bc.x ──
        try:
            d.target_x = float(bc.x)
        except (TypeError, ValueError):
            clear_static_window(d)
            continue
        # ── FORCE CHASE so the tackle pipeline can arm ──
        d.movement_state = MovementState.CHASE
        d.frozen = False
        d.timing_locked = False
        d.reaction_timer = 0.0
        d.retreat_delay = 0.0
        d.lateral_hesitation = 0.0
        d.step_stagger = 0.0
        d.step_stagger_active = False
        d.move_speed = min(
            float(getattr(d, "max_speed_mps", ceiling) or ceiling),
            ceiling,
        )
        d.target_source = LINE_BREAK_STATIC_GUARD_SOURCE
        clear_static_window(d)
        rearmed.append(d)
    return rearmed
