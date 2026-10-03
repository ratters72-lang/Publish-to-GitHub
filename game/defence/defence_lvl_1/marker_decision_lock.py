"""marker_decision_lock.py — marker target-decision hysteresis.

Directive (2026-09-05, revised 2026-09-07): markers must not re-assert
targets immediately after a ruck state flip (TES → RELEASE, RELEASE → PTB,
PTB → POST_RUCK, POST_RUCK → OPEN_PLAY).  The ruck-adjacent pin re-asserts
the marker target every frame, and the marker geometry authority
(marker_authority.marker_geometry_target) recomputes the ruck-pin target,
so a state flip that moves the ruck would produce a hard target jump if the
marker re-evaluated immediately.

The DECISION LOCK removes the target jump WITHOUT touching spacing or
anticipation logic:

  1. MARKER_LOCK_S = 0.25 decision lock.  On a ruck state flip each marker's
     target decision is LOCKED for 0.25 s: marker_geometry_target returns the
     target captured at flip time instead of recomputing the (moved) ruck pin.
     This is ``prevent re-evaluation while locked``.

  2. LARGE-RUCK-MOVE INVALIDATION.  When the live ruck has moved more than
     ``MARKER_LOCK_RUCK_MOVE_INVALIDATE_M`` from the position captured at arm
     time, the lock is invalidated and the marker re-anchors to the live ruck
     pin instead of holding the stale pre-flip target (the marker "recovers"
     when the ruck shifts a lot).

NEVER FORCE-SNAP (2026-09-07): markers are NEVER position-snapped.  Markers
GLIDE to their ruck-pin lane via the movement integrator (Player.update
smooth-damp / accel-lerp toward target_x/target_y).  There is no snap
debounce machinery because no snap exists; the decision lock only smooths
the TARGET at flips.  Marker role assignment + geometry finalization happen
ONLY at the canonical Drive→Release transition (canary-flagged by
marker_assign_at_release_only=True).

State lives on ``world._marker_decision_lock`` keyed by player id.  The lock
is armed ONLY on the canonical Drive→Release transition by
OpenPlayEngine.update(); the clock is advanced every frame.
"""

from __future__ import annotations

import math

from player.player import DefenderRole

# Decision lock window (seconds) after a ruck state flip during which a
# marker keeps its pre-flip target instead of re-asserting the new ruck pin.
MARKER_LOCK_S: float = 0.25

# Large-ruck-move invalidation: when the live ruck has moved more than this
# from the position captured at arm time, the marker decision lock is
# INVALIDATED — the marker re-anchors to the live ruck pin instead of holding
# the stale pre-flip target.  This is the "recovery" path that lets markers
# track the live ruck pin when it shifts a lot outside the canonical
# Drive→Release boundary.
MARKER_LOCK_RUCK_MOVE_INVALIDATE_M: float = 2.0

# Phase bucket categories.  A transition between buckets is a "ruck state
# flip" that re-arms the marker decision lock.
_PHASE_BUCKETS: dict[str, str] = {
    "OPEN_PLAY": "open_play",
    "TACKLE_INIT": "tes",
    "TACKLE_CONTACT": "tes",
    "TACKLE_DRIVE": "tes",
    "TACKLE_DOMINANCE": "tes",
    "RELEASE": "release",
    "PTB_PREP": "ptb",
    "PTB_EXEC": "ptb",
    "PTB": "ptb",
    "POST_RUCK_OPEN_PLAY": "post_ruck",
}


def phase_bucket(phase: str) -> str:
    """Return the bucket key for *phase* (identity fallback for unknown)."""
    return _PHASE_BUCKETS.get(str(phase), str(phase))


def _store(world) -> dict:
    store = getattr(world, "_marker_decision_lock", None)
    if not isinstance(store, dict):
        store = {}
        setattr(world, "_marker_decision_lock", store)
    return store


def _entry(world, d) -> dict:
    store = _store(world)
    pid = getattr(d, "player_id", None)
    entry = store.get(pid)
    if not isinstance(entry, dict):
        entry = {
            "lock_remaining_s": 0.0,
            "locked_tx": None,
            "locked_ty": None,
            "locked_ruck_x": None,
            "locked_ruck_y": None,
        }
        store[pid] = entry
    return entry


def _is_marker(d) -> bool:
    return getattr(d, "defender_role", None) in (
        DefenderRole.MARKER_A,
        DefenderRole.MARKER_B,
    )


def arm_marker_lock(world, defenders) -> None:
    """Capture each marker's CURRENT target as the locked decision target.

    Called at a ruck state flip (phase bucket transition).  Each marker's
    ``target_x / target_y`` (the pre-flip value, set by the previous phase's
    last writer) is frozen as the locked target for MARKER_LOCK_S.  The ruck
    position at arm time is also captured so a later large ruck move can
    invalidate the lock.
    """
    ruck_x = getattr(world, "ruck_x_m", 60.0)
    ruck_y = getattr(world, "ruck_y_m", 35.0)
    for d in defenders or []:
        if not _is_marker(d):
            continue
        entry = _entry(world, d)
        entry["locked_tx"] = float(getattr(d, "target_x", 0.0))
        entry["locked_ty"] = float(getattr(d, "target_y", 0.0))
        entry["lock_remaining_s"] = MARKER_LOCK_S
        entry["locked_ruck_x"] = float(ruck_x)
        entry["locked_ruck_y"] = float(ruck_y)


def advance_marker_lock_clock(world, dt: float) -> None:
    """Decrement every marker lock timer by *dt* (floor at 0.0)."""
    store = _store(world)
    for entry in store.values():
        if isinstance(entry, dict):
            rem = entry.get("lock_remaining_s", 0.0)
            if rem > 0.0:
                entry["lock_remaining_s"] = max(0.0, rem - dt)


def marker_lock_invalidated(world, d) -> bool:
    """True when the live ruck moved far enough to invalidate the lock.

    The lock is armed at the canonical Drive→Release transition with the ruck
    position captured at arm time.  If the ruck later shifts more than
    ``MARKER_LOCK_RUCK_MOVE_INVALIDATE_M`` from that captured position, the
    lock is stale and must be invalidated so the marker re-anchors to the live
    ruck pin (the "recovery" path — the marker smoothly tracks the live pin).
    """
    if not _is_marker(d):
        return False
    entry = _entry(world, d)
    ruck_x = entry.get("locked_ruck_x")
    ruck_y = entry.get("locked_ruck_y")
    if ruck_x is None or ruck_y is None:
        return False
    live_x = float(getattr(world, "ruck_x_m", 60.0))
    live_y = float(getattr(world, "ruck_y_m", 35.0))
    dx = live_x - float(ruck_x)
    dy = live_y - float(ruck_y)
    return math.hypot(dx, dy) > MARKER_LOCK_RUCK_MOVE_INVALIDATE_M


def marker_lock_active(world, d) -> bool:
    """True when *d* is a marker inside its decision-lock window."""
    if not _is_marker(d):
        return False
    # Large-ruck-move override: an invalidated lock never reads as active.
    if marker_lock_invalidated(world, d):
        return False
    entry = _entry(world, d)
    return entry.get("lock_remaining_s", 0.0) > 0.0


def locked_marker_target(world, d):
    """Return ``(tx, ty)`` when the marker decision is locked, else None."""
    if not marker_lock_active(world, d):
        return None
    entry = _entry(world, d)
    tx = entry.get("locked_tx")
    ty = entry.get("locked_ty")
    if tx is None or ty is None:
        return None
    return (float(tx), float(ty))


def clear_marker_lock(world, defenders) -> None:
    """Drop lock/snap state for players that are no longer markers.

    Called on marker role cleanup so stale lock entries never leak across a
    marker → line-role conversion.
    """
    store = _store(world)
    for d in defenders or []:
        if not _is_marker(d):
            store.pop(getattr(d, "player_id", None), None)
