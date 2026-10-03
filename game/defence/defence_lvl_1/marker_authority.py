"""marker_authority.py — single-source marker geometry authority.

Directive (2026-09-04): Markers use geometry-authority targets ONLY
(ruck_x + MARKER_A/B_DEPTH_M, ruck_y), and every freeze / HOLD / timing
state on a marker suppresses VELOCITY only (frozen + move_speed=0) —
never overriding target_x/target_y.  Markers must remain pixel-correct
on both axes across TES → PTB → RELEASE → OPEN_PLAY.

The X-depth convention is the engine-spec authority (OpenPlayEngine
MARKER_A_DEPTH_M = 1.0, MARKER_B_DEPTH_M = 2.0, Y = ruck_y).  The legacy
Y-offset convention (ruck_y - 0.5/-1.0) is replaced.  All marker target
writers consume `marker_geometry_target`; `enforce_marker_geometry_authority`
re-asserts the ruck-pin target AFTER every marker writer so no freeze /
HOLD / timing / retreat / spacing / pin branch can leave a stale d.x/d.y.
"""

from player.player import DefenderRole


def _marker_depth_m(role):
    """Engine-spec X-depth for a marker role (single-sourced from OPE).

    Lazy import avoids a module-load cycle (OpenPlayEngine imports this
    package's defence modules).
    """
    from world.open_play_engine import MARKER_A_DEPTH_M, MARKER_B_DEPTH_M
    if role == DefenderRole.MARKER_A:
        return float(MARKER_A_DEPTH_M)
    if role == DefenderRole.MARKER_B:
        return float(MARKER_B_DEPTH_M)
    return 0.0


def marker_geometry_target(world, d):
    """Return the single geometry-authority (target_x, target_y) for a marker.

    Marker A -> (ruck_x + MARKER_A_DEPTH_M, ruck_y)
    Marker B -> (ruck_x + MARKER_B_DEPTH_M, ruck_y)

    Any non-marker returns (ruck_x, ruck_y) as a safe no-op default.

    STAGE 1 MARKER DECISION LOCK (2026-09-05): when the marker is inside its
    decision-lock window (armed at a ruck state flip), the PRE-FLIP target
    captured at arm time is returned instead of recomputing the (moved) ruck
    pin.  This prevents the marker from re-asserting a new target immediately
    after the ruck state flips — the source of the early double-snap / teleport
    feel.  The lock expires after MARKER_LOCK_S, after which the live ruck pin
    is recomputed as usual.
    """
    from game.defence.defence_lvl_1.marker_decision_lock import (
        locked_marker_target,
    )
    locked = locked_marker_target(world, d)
    if locked is not None:
        return locked
    ruck_x = getattr(world, "ruck_x_m", 60.0)
    ruck_y = getattr(world, "ruck_y_m", 35.0)
    role = getattr(d, "defender_role", None)
    # ── FIELD CLAMP (RELEASE deadlock fix) ────────────────────────────────
    # A ruck pin may never be projected OUTSIDE the field.  The ruck anchor
    # is the ball carrier's drive END x, and _clamp_position deliberately
    # skips the BC clamp while he is being driven, so the anchor can end up
    # past FIELD_MAX_X (measured: 120.593 against a 120.0 bound).
    #
    # `ruck_x + 1.0` / `ruck_x + 2.0` then pins the marker pair beyond the
    # dead-ball line, where _clamp_position makes them physically
    # unreachable.  markers_ready therefore stays False for the whole
    # RELEASE, release_convergence() can never return all_converged, and
    # the phase always burns its full RELEASE_MAX_DURATION_S watchdog with
    # the ball carrier pinned and unfrozen at the line.
    #
    # Clamping restores a REACHABLE pin without touching the marker depth
    # contract away from the field edge.
    from player.player import FIELD_MIN_X, FIELD_MAX_X
    _tx = float(ruck_x) + _marker_depth_m(role)
    _tx = max(FIELD_MIN_X, min(FIELD_MAX_X, _tx))
    return (_tx, float(ruck_y))


def enforce_marker_geometry_authority(world, defenders):
    """Re-assert every MARKER_A/B target to the ruck-pin geometry authority.

    Runs as a post-write safety net AFTER every marker writer (behaviour,
    timing, freeze, retreat, spacing, pin).  A marker that was pinned to
    d.x/d.y by a freeze/timing branch is re-targeted to the ruck pin while
    its velocity suppression (frozen / move_speed=0) is preserved.
    """
    for d in defenders:
        role = getattr(d, "defender_role", None)
        if role not in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
            continue
        tx, ty = marker_geometry_target(world, d)
        d.target_x = tx
        d.target_y = ty
