"""catch_x_authority.py — sole X-authority at the CATCH (chase-to-catch) transition.

PURPOSE
-------
Tacklers were freezing because multiple X-writers fought during the
chase-to-catch transition.  On the frame the kickoff ball is caught, and on
the first OPEN_PLAY frames after it, several writers re-point the SAME
defender's ``target_x`` against different conventions:

  - the KMC chase writer (``kickoff_engine._update_chasers``) writes
    ``target_x = ball_x + CHASE_OFFSET_X`` for every chaser;
  - the catch cleanup (``catch_system._perform_catch``) writes
    ``target_x = p.x`` for every non-BC player;
  - ``on_ball_secured()`` re-asserts the fullback + re-seeds depth roles;
  - then the full OPEN_PLAY pipeline (spacing / width / MMDL / retreat /
    pre-tackle / pursuit) writes ``target_x`` again.

The result is a frame-to-frame seesaw in ``target_x`` — the tackler never
settles on one destination and freezes at ``vx = 0``.

LAW (this module)
-----------------
During the CATCH transition (armed at reception, cleared when the post-catch
pursuit window expires) EVERY non-TES X writer for a genuine line defender is
suppressed, and this authority is the SOLE writer of ``target_x``:

    apply_catch_x_authority(...)  — BC-relative chase X
                                    (``bc.x + WIDTH_OFFSETS[depth_role]``),
                                    stamped ``target_source = "catch_x_authority"``.

Y is NEVER touched — the spacing authority keeps Y (the per-axis law the
codebase enforces with ``SpacingGuard`` / ``WidthGuard``).  This module only
owns the DEPTH (X) axis and only during the transition window.

So that exactly one writer owns ``target_x`` at CATCH, this authority is
invoked as the LAST X write of the OPEN_PLAY dispatch (after every writer,
after the writer-priority-band resolution, before movement integration).  Its
BC-relative chase alignment is deterministic: identical inputs produce
identical ``target_x``.

EXEMPT (never owned here — dedicated owners remain)
---------------------------------------------------
  - dynamic fullback           (``apply_fullback_geometry``)
  - markers / DUMMY_HALF       (ruck-pin / PTB owners)
  - TES tacklers / kick-chase  (TES / chase owners)
  - tackle-state holders       (gate peel writer)
  - frozen bodies              (freeze authority)

Reset law: the lock is cleared by ``clear_catch_x_authority`` (kickoff reset)
and decayed by OpenPlayEngine once ``post_catch_chase_window_s`` expires.
"""

from __future__ import annotations

from player.player import DefenderRole
from player.movement_states import MovementState, MovementMode

# The ``target_source`` stamp this authority writes.  Registered in
# writer_priority_bands._SOURCE_PRIORITY so any capture reflects its tier.
CATCH_X_AUTHORITY_SOURCE: str = "catch_x_authority"

# Exempt role names mirror movement_mode_decision._TACKLE_RADIUS_EXEMPT_ROLE_NAMES
# so the catch authority and the tackle-radius CHASE switch agree on which
# bodies are genuine line defenders.
_EXEMPT_ROLE_NAMES = frozenset({
    "MARKER_A",
    "MARKER_B",
    "DUMMY_HALF",
    "TACKLER_1",
    "TACKLER_2",
    "TACKLER_3",
    "KICK_CHASE",
})


def catch_x_authority_locked(world) -> bool:
    """True when the CATCH X-authority lock is armed.

    ``getattr`` defaults to False so harness worlds / verifiers without the
    field degrade to the normal last-writer-wins behaviour (no-op).
    """
    return bool(getattr(world, "catch_x_authority_lock", False))


def is_catch_x_authority_eligible(d, world=None) -> bool:
    """True when *d* is a genuine line defender whose X this authority owns.

    Mirrors ``_is_genuine_line_defender`` in movement_mode_decision: unfrozen,
    non-fullback, non-marker, non-DH, non-TES-tackler, non-kick-chase,
    non-tackle-state bodies.  Dedicated owners keep their contracts.
    """
    if d is None:
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
    role_name = (
        getattr(role, "name", None) or str(role) if role is not None else None
    )
    if role_name in _EXEMPT_ROLE_NAMES:
        return False
    return True


def apply_catch_x_authority(world, defenders, bc, tuners=None) -> list:
    """Sole X-writer during the CATCH transition.

    For every eligible genuine line defender, ``target_x`` is set to
    ``bc.x + WIDTH_OFFSETS[depth_role]`` — the same BC-relative role-depth X
    the structured pipeline computes — so the chaser tracks the caught
    carrier instead of snapping to an anchor / spacing cell.  Bodies without
    a known depth role fall back to ``bc.x``.  Y is never touched.

    When the lock is OFF (normal play) this is a pure no-op: it returns ``[]``
    and never writes, so behaviour is byte-identical outside the transition.

    Returns the list of defenders whose ``target_x`` was written.
    """
    if not catch_x_authority_locked(world):
        return []
    if bc is None:
        return []
    offsets = {}
    if tuners is not None:
        offsets = getattr(tuners, "WIDTH_OFFSETS", None) or {}
    written = []
    for d in defenders:
        if d is None:
            continue
        if not is_catch_x_authority_eligible(d, world):
            continue
        role = getattr(d, "depth_role", None)
        if role in offsets:
            d.target_x = float(bc.x) + float(offsets[role])
        else:
            d.target_x = float(bc.x)
        d.target_source = CATCH_X_AUTHORITY_SOURCE
        written.append(d)
    return written


def arm_catch_x_authority(world) -> None:
    """Arm the lock (called at reception, in ``_perform_catch``)."""
    if world is None:
        return
    world.catch_x_authority_lock = True


def clear_catch_x_authority(world) -> None:
    """Clear the lock (kickoff reset / fresh sequence)."""
    if world is None:
        return
    world.catch_x_authority_lock = False


def force_line_defenders_chase(world) -> int:
    """Force every genuine line defender into the CHASE movement state.

    CATCH CHASE FORCE (2026-09-10): on the catch frame the post-catch
    cleanup leaves every non-BC body unfrozen with ``defender_role = None``
    and ``target_x = x``.  A defender left in HOLD/DRIFT/RETREAT after the
    catch is NOT a closing posture, so ``pretackle_classifier.classify_pretackle``
    (``is_closing_posture`` requires ``movement_state in (CHASE, SHOOT)``)
    rejects it and tackling stays off.  Forcing CHASE here means the first
    OPEN_PLAY frame starts with every genuine line defender in a closing
    posture, so the pre-tackle classifier can fire and tackling switches
    back on immediately.

    Mechanism (paired with the task directive):
      1. ``post_catch_chase_window_s > 0`` — arms (top-up only) the pursuit
         window the MMDL reads (``_in_post_catch_chase_window`` routes
         engageable defenders to CHASE) and the depth classifier's
         post-catch promotion (PASSIVE -> SECONDARY).
      2. ``movement_state = CHASE`` — the closing movement state.  CHASE is
         a MovementState (``MovementMode`` has no CHASE value); the
         ``movement_mode`` contract is normalised to NORMAL (the correct
         line-chase contract) so no stale TACKLER/RETREAT mode interferes.

    Only genuine line defenders are forced (``is_catch_x_authority_eligible``
    — mirrors ``_is_genuine_line_defender`` in movement_mode_decision):
    fullback, markers, DH, TES tacklers, kick-chase, frozen, and
    tackle-state holders keep their dedicated owners.

    Returns the number of defenders forced to CHASE.
    """
    if world is None:
        return 0
    # 1. Arm the post-catch pursuit window (top-up only, never shorten).
    _window = float(getattr(world, "POST_CATCH_CHASE_WINDOW_S", 3.0))
    world.post_catch_chase_window_s = max(
        float(getattr(world, "post_catch_chase_window_s", 0.0) or 0.0),
        _window,
    )
    # 2. Force CHASE on every genuine line defender.
    forced = 0
    for d in (getattr(world, "defending_team", None) or ()):
        if not is_catch_x_authority_eligible(d, world):
            continue
        # CHASE is the closing movement state; it is a valid transition
        # from every MovementState, so a direct force is legal.
        d.movement_state = MovementState.CHASE
        # MovementMode has no CHASE value; NORMAL is the line-chase
        # movement contract.  Clear any stale TACKLER/RETREAT mode.
        if getattr(d, "movement_mode", None) is not None:
            d.movement_mode = MovementMode.NORMAL
        forced += 1
    return forced
