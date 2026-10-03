"""target_field_bound.py — clamp an AUTHORED target into the playable field.

THE DEFECT THIS EXISTS TO PREVENT
---------------------------------
Measured live (``probe_open_play_stall_authority.py``, seed 20260827,
6000 frames, 1404 OPEN_PLAY frames): with the ball carrier at or past
``FIELD_MAX_X`` (120.0), the pre-tackle authority authored its OPEN_PLAY
COMMIT target as ``bc.x -/+ pretackle_commit_depth_offset_m`` (default
1.0 m) — e.g. ``target_x == 121.0`` against a 120.0 bound.  Two of the
three "sibling" commit writers in ``defensive_behaviour_unified`` do the
same with ``bc.x +/- ConvergenceManager`` offsets.

``Player._clamp_position`` forbids ``x > FIELD_MAX_X``.  A body commanded
to a point OUTSIDE the field therefore has an UNREACHABLE target: it walks
into the boundary, the clamp cancels the displacement, and the body sits
pinned at the wall with ``vx == vy == 0`` while its authority keeps
commanding a forward approach.  That is precisely the reported symptom —
"some agents remain stationary in OPEN_PLAY with static targets" — and it
is a TARGET-AUTHORITY defect, not a freeze: ``frozen`` is False on every
one of those frames (13 of the 14 stalled body-frames the probe recorded
were stamped ``target_source == "pre_tackle"``).

A target outside the field is unreachable by construction, so clamping one
can only ever make a commanded move MORE achievable.  This is the same
idiom ``marker_authority.py`` already applies to its own authored target
("past FIELD_MAX_X (measured: 120.593 against a 120.0 bound)") and the
same one ``open_play_engine.py`` applies to the carrier's RELEASE stand-up
target.  It lives in its own leaf module because three separate writers
need it and a leaf module cannot create an import cycle.

CONTRACT
--------
PURE function of (tx, ty).  Never mutates its arguments, never reads or
writes world/player state, never raises for numeric input.  For a point
already inside ``[FIELD_MIN_X, FIELD_MAX_X] x [FIELD_MIN_Y, FIELD_MAX_Y]``
it returns the inputs BYTE-IDENTICAL, so every existing in-field caller
(and every verifier fixture, which sits mid-field) is unaffected.
"""

from __future__ import annotations

# The playable strip is owned by the player module (the same bounds
# ``Player._clamp_position`` enforces, so a clamped target is always
# reachable by an unclamped step).
from player.player import (
    FIELD_MIN_X,
    FIELD_MAX_X,
    FIELD_MIN_Y,
    FIELD_MAX_Y,
)


def clamp_target_x(tx: float) -> float:
    """Return *tx* clamped into ``[FIELD_MIN_X, FIELD_MAX_X]``."""
    return max(FIELD_MIN_X, min(FIELD_MAX_X, float(tx)))


def clamp_target_y(ty: float) -> float:
    """Return *ty* clamped into ``[FIELD_MIN_Y, FIELD_MAX_Y]``."""
    return max(FIELD_MIN_Y, min(FIELD_MAX_Y, float(ty)))


def clamp_target_to_field(tx: float, ty: float) -> tuple:
    """Return ``(tx, ty)`` clamped into the playable field.

    Identity for an in-field point, so this is a no-op on every frame where
    the carrier is inside the field.
    """
    return clamp_target_x(tx), clamp_target_y(ty)


def target_is_in_field(tx: float, ty: float) -> bool:
    """True when ``(tx, ty)`` is inside the playable field.

    Single predicate for the verifier and for any future audit that needs
    to assert the invariant rather than enforce it.
    """
    if tx is None or ty is None:
        return False
    return (
        FIELD_MIN_X <= float(tx) <= FIELD_MAX_X
        and FIELD_MIN_Y <= float(ty) <= FIELD_MAX_Y
    )
