"""
ball_side_collapse.py — Ball-side collapse + dynamic depth roles (TASK X, 2026-08-26).

PURPOSE
-------
The depth-role system is PERSISTENT and hysteresis-protected: roles are
seeded once per kickoff and then held ("when every slot is occupied,
roles are immutable").  That static inheritance corrupts CENTRE/WINGER
(edge structure) and A/B/C (middle bands) because roles seeded while the
line was still settling survive into the tackle even though the settled
positions disagree.  ``defensive_rebanding`` repairs only the INVARIANT
violations (doubling / missing-A / mis-splits); it deliberately preserves
valid-but-stale assignments.

This module completes the fix: at every RUCK PIVOT (new tackle site) the
depth roles are refitted FROM SCRATCH off three inputs the static system
ignored:

    1. BALL-SIDE  — which side of the ruck the ball carrier is on
                    (``sign(bc.y − ruck_y)``); the side split is biased
                    so extra defenders collapse toward the ball.
    2. RUCK CONTEXT — ruck_x_m / ruck_y_m / ruck_established.
    3. SETTLED POSITIONS — the Stage-3F quiet window must have elapsed
                    (same clock ``defensive_rebanding.quiet_window_elapsed``
                    reads), so the refit happens on the post-retreat,
                    post-settle geometry rather than the seed-era line.

GATING
------
    GATE A  ruck_established must be True (Stage-3B pivot armed).
    GATE B  Stage-3F quiet window elapsed (positions settled).
    GATE C  a NEW pivot since the last refit (ruck_established rising
            edge, or the ruck point moved > pivot_ruck_move_threshold_m).
    GUARD   collapse-locked defenders (within COLLAPSE_LOCK_RADIUS_M of
            the BC) keep their roles — no role changes at contact.

The dynamic refit INVALIDATES the stale roles on the unlocked subset and
immediately re-runs ``assign_depth_roles`` with the ball-side bias, so
roles are valid the same frame (no one-frame hole).  The caller then
republishes frame geometry (same contract as rebanding).  Rebanding runs
immediately AFTER this in the DBU as the intra-ruck invariant net.

PURE W.R.T. POSITIONS: never writes d.x/d.y/target_*/movement — only the
same fields ``assign_depth_roles`` owns (depth_role/depth_side/
_depth_seeded/role_depth_target_x/y), plus the module pivot bookkeeping.

Returns True when ANY eligible defender was refit (so the caller
republishes frame geometry); False on no-op frames.
"""

import math

from world.world_state import GameSubState

from game.defence.defence_lvl_1.defensive_depth_roles import (
    EXCLUDED_ROLES,
    RUCK_X_FALLBACK,
    RUCK_Y_FALLBACK,
    assign_depth_roles,
    _resolve_tuners as _resolve_depth_tuners,
)
from game.defence.defence_lvl_1.defensive_split_spacing import (
    LEFT,
    RIGHT,
)

__all__ = [
    "resolve_ball_side_bias",
    "resolve_edge_release",
    "last_edge_release",
    "detect_pivot",
    "apply_dynamic_depth_roles",
    "reset_pivot_state",
]


# ─────────────────────────────────────────────────────────────────────
# MODULE PIVOT STATE — keyed by id(world); one entry per engine world
# ─────────────────────────────────────────────────────────────────────
_LAST_PIVOT_KEY: dict = {}

# ─────────────────────────────────────────────────────────────────────
# TASK XI EDGE-RELEASE STATE — keyed by id(world); one entry per engine
# world.  Stores the LAST APPLIED ``(active, side)`` pair so the
# assignment layer can detect mode TRANSITIONS (activate / deactivate /
# side-cross) and re-seed exactly the affected side.
# ─────────────────────────────────────────────────────────────────────
_EDGE_RELEASE_STATE: dict = {}


def reset_pivot_state() -> None:
    """Clear all module pivot bookkeeping.

    Called on kickoff reset so a reused WorldState object re-arms the
    next pivot as a fresh one.  The engine normally resets
    ``ruck_established`` to False at kickoff, which the refit GATE A
    also auto-clears; this helper is a belt-and-suspenders reset for
    tests / direct callers.  Also clears the TASK XI edge-release
    hysteresis state so a reused world starts symmetric.
    """
    _LAST_PIVOT_KEY.clear()
    _EDGE_RELEASE_STATE.clear()


def _pivot_key(world) -> tuple:
    """Immutable identity of the current ruck point (rounded to 0.1 m)."""
    return (
        round(float(getattr(world, "ruck_x_m", RUCK_X_FALLBACK)), 1),
        round(float(getattr(world, "ruck_y_m", RUCK_Y_FALLBACK)), 1),
    )


def _resolve_tuners(world):
    """Return the live DefenceTuners (delegates to the depth-role source)."""
    return _resolve_depth_tuners(world)


# ─────────────────────────────────────────────────────────────────────
# BALL-SIDE BIAS RESOLUTION (shared with rebanding — single authority)
# ─────────────────────────────────────────────────────────────────────

def resolve_ball_side_bias(world, tuners=None) -> tuple:
    """Return ``(ball_side, ball_side_bias)`` for the current carrier.

    ``ball_side`` is LEFT (= -1) when the BC sits at least
    ``collapse_min_bc_offset_m`` LEFT of the ruck, RIGHT (= +1) when it
    sits that far RIGHT, and None when there is no carrier, the carrier
    is centred (within the threshold), or the bias tuner is 0.  The bias
    is ``collapse_ball_side_bias`` when active, else 0.

    Single source of truth consumed by BOTH ``apply_dynamic_depth_roles``
    and the reband STEP-1 count split, so the ball-side collapse is
    applied identically by the dynamic refit and by the intra-ruck repair.
    """
    if tuners is None:
        tuners = _resolve_tuners(world)
    bias = int(getattr(tuners, "collapse_ball_side_bias", 0))
    if bias <= 0:
        return None, 0
    bc = getattr(world, "ball_carrier", None)
    if bc is None:
        return None, 0
    ruck_y = float(getattr(world, "ruck_y_m", RUCK_Y_FALLBACK))
    min_off = float(getattr(tuners, "collapse_min_bc_offset_m", 4.0))
    offset = float(getattr(bc, "y", ruck_y)) - ruck_y
    if abs(offset) < min_off:
        return None, 0
    return (LEFT if offset < 0.0 else RIGHT), bias


# ─────────────────────────────────────────────────────────────────────
# TASK XI EDGE RELEASE — CENTRE/WINGER → interior conversion authority
# ─────────────────────────────────────────────────────────────────────

def last_edge_release(world) -> tuple:
    """Return the previously APPLIED ``(active, side)`` pair — no mutation.

    ``assign_depth_roles`` reads this BEFORE calling
    ``resolve_edge_release`` so it can detect a mode TRANSITION
    (activation / deactivation / ball-side cross) and re-seed exactly
    the affected side(s).  Unknown worlds default to ``(False, None)``
    — the legacy symmetric structure.
    """
    return _EDGE_RELEASE_STATE.get(id(world), (False, None))


# Retreat sub-states (TACKLE_RELEASE / PTB / POST_RUCK_OPEN_PLAY) — the
# phases where the engine pins world.ruck_y_m = bc.y, so the normal
# BC-vs-ruck offset gate below can never arm the W/C -> interior
# conversion (the BC is AT the ruck, offset ~0).
_RETREAT_PHASE_NAMES = frozenset({
    "tackle_release", "ptb", "post_ruck_open_play",
})


def _world_in_retreat_phase(world) -> bool:
    """True when *world* is in a RELEASE/PTB retreat sub-state.

    Accepts the GameSubState enum directly, its ``.value`` string, or the
    lowercase name.  Used by ``resolve_edge_release`` to arm the W/C ->
    interior conversion during the retreat (where the BC-vs-ruck offset is
    ~0 and the normal enter gate cannot fire).
    """
    sub = getattr(world, "sub_state", None)
    if sub is None:
        return False
    try:
        if sub in (
            GameSubState.TACKLE_RELEASE,
            GameSubState.PTB,
            GameSubState.POST_RUCK_OPEN_PLAY,
        ):
            return True
    except TypeError:
        pass
    if hasattr(sub, "value"):
        sub = sub.value
    return str(sub).lower() in _RETREAT_PHASE_NAMES


def resolve_edge_release(world, tuners=None) -> tuple:
    """Resolve the TASK XI edge-release mode for the current carrier.

    Returns ``(active, release_side)``:

    - ``active=False, side=None`` — positional identity intact: the
      canonical WINGER/CENTRE slots hold on both sides.
    - ``active=True, side=S``     — the CENTRE/WINGER slots ON SIDE S
      dissolve; their holders join the interior depth stack by pure
      Y-axis ordering (closest-to-ruck → A, next B, then C/D).  The
      weak side keeps its canonical edge structure.

    GATES
    -----
    GATE 1  ``collapse_edge_release_enabled`` tuner must be True.
    GATE 2  a ball carrier must exist (no carrier → restore symmetry).
    ENTER   |bc.y − ruck_y| >= ``collapse_edge_release_offset_m``
            (floored at ``collapse_min_bc_offset_m`` so the release can
            never arm before the Task X collapse itself).
    HYSTERESIS  once active, the release persists until the offset
            falls to/below (enter − ``collapse_edge_release_hysteresis_m``),
            preventing flap around the threshold.  A BC crossing to the
            OTHER side while active ends the old side's release and
            arms the new side (the caller treats the pair change as a
            transition affecting both sides).

    SINGLE WRITER: this function owns the stored state (read by
    ``last_edge_release``).  ``assign_depth_roles`` calls it on EVERY
    invocation (per-frame preamble, dynamic refit, reband bootstrap,
    geometry chokepoint), so every consumer sees ONE consistent mode —
    the unified depth-role model.
    """
    if tuners is None:
        tuners = _resolve_tuners(world)
    if not bool(getattr(tuners, "collapse_edge_release_enabled", False)):
        _EDGE_RELEASE_STATE[id(world)] = (False, None)
        return False, None

    ruck_y = float(getattr(world, "ruck_y_m", RUCK_Y_FALLBACK))

    # ── RELEASE/PTB PHASE-AWARE ARMING (2026-08-29) ──
    # During RELEASE/PTB the engine pins world.ruck_y_m = bc.y at the
    # DOMINANCE->RELEASE boundary, so the BC-vs-ruck offset used by the
    # normal enter gate below is ~0 and the W/C -> interior conversion
    # can never arm there (CENTER/WINGER then persist and stack on the
    # ruck-adjacent A near the TES).  In those phases the release is keyed
    # off the RUCK's field-side position instead: ball side =
    # sign(ruck_y - field_centre).  The far side keeps its canonical
    # winger/centre edge structure.
    if _world_in_retreat_phase(world):
        field_center = float(getattr(tuners, "field_width_m", 70.0)) * 0.5
        if abs(ruck_y - field_center) > 1e-9:
            ball_side = LEFT if ruck_y < field_center else RIGHT
            _EDGE_RELEASE_STATE[id(world)] = (True, ball_side)
            return True, ball_side
        # ruck exactly on field centre -> fall through to the BC-vs-ruck logic.

    bc = getattr(world, "ball_carrier", None)
    if bc is None:
        _EDGE_RELEASE_STATE[id(world)] = (False, None)
        return False, None

    offset = float(getattr(bc, "y", ruck_y)) - ruck_y
    abs_off = abs(offset)
    side = LEFT if offset < 0.0 else RIGHT

    min_collapse = float(getattr(tuners, "collapse_min_bc_offset_m", 4.0))
    enter = max(
        float(getattr(tuners, "collapse_edge_release_offset_m", 9.0)),
        min_collapse,
    )
    hyst = max(0.0, float(getattr(tuners, "collapse_edge_release_hysteresis_m", 2.0)))
    exit_at = enter - hyst

    prev_active, prev_side = _EDGE_RELEASE_STATE.get(id(world), (False, None))

    active = False
    if abs_off >= enter:
        active = True                      # fresh arming (or re-arm past exit)
    elif (
        prev_active
        and prev_side == side
        and abs_off > exit_at
    ):
        active = True                      # persist within the hysteresis band

    _EDGE_RELEASE_STATE[id(world)] = (active, side if active else None)
    return active, (side if active else None)


# ─────────────────────────────────────────────────────────────────────
# PIVOT DETECTION
# ─────────────────────────────────────────────────────────────────────

def detect_pivot(world, tuners=None) -> bool:
    """True when a NEW ruck pivot since the last refit.

    A pivot is:
      - the very first armed frame seen for this world (stored None), or
      - the ruck point moved > ``pivot_ruck_move_threshold_m`` on either
        axis relative to the last refit's ruck point.

    Sub-threshold jitter within one ruck never re-fires it.  Reads only
    bookkeeping — it does NOT mutate the stored key; ``apply_dynamic_depth_roles``
    is the single writer so detect+apply gate identically.
    """
    if not bool(getattr(world, "ruck_established", False)):
        return False
    if tuners is None:
        tuners = _resolve_tuners(world)
    key = _pivot_key(world)
    stored = _LAST_PIVOT_KEY.get(id(world))
    if stored is None:
        return True
    threshold = float(getattr(tuners, "pivot_ruck_move_threshold_m", 0.5))
    dx = abs(key[0] - stored[0])
    dy = abs(key[1] - stored[1])
    return dx > threshold or dy > threshold


# ─────────────────────────────────────────────────────────────────────
# MAIN ENTRY — dynamic depth-role refit
# ─────────────────────────────────────────────────────────────────────

def apply_dynamic_depth_roles(world, defenders, tuners=None) -> bool:
    """Refit depth roles off ball-side + ruck context + settled positions.

    Call site contract (DBU OPEN_PLAY pipelines, both paths — placed
    immediately BEFORE ``reband_after_stabilisation``)::

        if _apply_dynamic_depth_roles(world, line_defenders):
            run_defensive_pipeline(world, line_defenders, tuners,
                                   governor=...)     # republish
        if _reband_after_stabilisation(world, line_defenders):
            run_defensive_pipeline(...)               # republish

    The dynamic refit invalidates stale seed-era roles on the settled
    subset and re-bootstraps them with the ball-side bias; rebanding then
    enforces the intra-ruck slot invariants across the WHOLE line.

    Returns True when any eligible defender's role/side changed, so the
    caller republishes frame geometry (Task 2 single-source law).
    """
    # ── GATE A: bands exist only once the ruck pivot armed ──
    if not bool(getattr(world, "ruck_established", False)):
        _LAST_PIVOT_KEY.pop(id(world), None)
        return False

    if tuners is None:
        tuners = _resolve_tuners(world)

    # ── GATE B: Stage-3F quiet window elapsed (settled positions) ──
    # Lazy import: defensive_rebanding imports resolve_ball_side_bias from
    # this module, so an eager import here would create an import cycle.
    from game.defence.defence_lvl_1.defensive_rebanding import (
        quiet_window_elapsed,
    )
    if not quiet_window_elapsed(world, tuners):
        return False

    # ── GATE C: NEW pivot since the last refit ──
    if not detect_pivot(world, tuners):
        return False

    # ── Eligible contract — identical to rebanding ──
    # STAGE 5 (2026-09-13): the refit operates on the SINGLE canonical line
    # population — the exact set the count / side / spacing authority
    # publishes.  (The historical filter omitted DEF_CHASE.)
    from game.defence.defence_lvl_1.population_authority import (
        line_population as _line_population,
    )
    eligible = _line_population(defenders)
    if not eligible:
        _LAST_PIVOT_KEY[id(world)] = _pivot_key(world)
        return False

    # ── COLLAPSE LOCK — nobody within COLLAPSE_LOCK_RADIUS_M of the BC ──
    # A defender already engaged at contact keeps its role this frame; the
    # refit pool is every OTHER eligible defender.  (The DBU reband runs
    # after and enforces invariants across the whole line.)
    from player.collapse_controls import depth_role_locked
    bc = getattr(world, "ball_carrier", None)
    if bc is None:
        pool = eligible
    else:
        pool = [
            d for d in eligible
            if not depth_role_locked(
                math.hypot(float(d.x) - float(bc.x), float(d.y) - float(bc.y))
            )
        ]

    if pool:
        ball_side, bias = resolve_ball_side_bias(world, tuners)
        # Invalidate the stale static roles on the settled subset.
        for d in pool:
            d.depth_role = None
            d.depth_side = None
            d._depth_seeded = False
        # Immediate deterministic re-split (ball-side biased) + bootstrap.
        assign_depth_roles(
            pool, world,
            ball_side=ball_side, ball_side_bias=bias,
        )

    # Single pivot stamp — even an empty pool consumes the pivot so it is
    # never re-armed on subsequent frames of the same ruck.
    _LAST_PIVOT_KEY[id(world)] = _pivot_key(world)
    return bool(pool)
