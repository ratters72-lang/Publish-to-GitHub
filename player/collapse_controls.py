"""
player/collapse_controls.py -- Pre-tackle alignment & collapse geometry controls.

Implements the 2026-08-15 "Pre-Tackle Alignment & Collapse Geometry" task
pack.  The pack's target module (``tes_drive_movement.py``) does not exist
in this engine -- the real pre-tackle approach velocity is assembled by the
movement-state machine (``player/movement_states.apply_movement_state``)
for MMDL-driven closers, and the depth-role band/side authorities live in
``defensive_depth_roles`` / ``defensive_engagement_state``.  This module
exposes the PURE clamp/lock/latency math so those writers consume ONE
source of truth, plus the task's logging hooks.

All helpers are pure / duck-typed (no ``game.*`` imports) so any caller can
use them without an import cycle.

TASK PACK MAPPING
-----------------
  1. Distance-based lateral correction clamp   -> ``distance_factor`` /
                                                   ``collapse_lateral_scale``
  2. Band-weighted correction                  -> ``band_factor``
  3. BC lateral velocity dampening             -> ``bc_vy_factor``
  4. Collapse vector override (commit zone)    -> ``collapse_commit`` +
                                                   ``COLLAPSE_LATERAL_SCALE``
                                                   (heading override applied in
                                                   apply_movement_state)
  5. Stabilise spacing-Y before collapse       -> ``freeze_spacing_y``
  6. Lock depth-role inside 2.0m               -> ``depth_role_locked`` +
                                                   ``COLLAPSE_LOCK_RADIUS_M``
                                                   (consumed by the side/band
                                                   role-switch authorities)
  7. Movement-state commit inside 1.0m         -> ``should_commit`` +
                                                   ``COMMIT_RADIUS_M``
                                                   (consumed by the MMDL writer -> SHOOT)
  8. Logging hooks                             -> ``debug_collapse_log`` /
                                                   ``DEBUG_COLLAPSE_GEOMETRY``
"""

import math

# ─────────────────────────────────────────────────────────────────────
# RADII (metres)
# ─────────────────────────────────────────────────────────────────────
# Item 6: no depth-role / side / engagement-band changes inside 2.0m.
COLLAPSE_LOCK_RADIUS_M: float = 2.0
# Item 7: movement state forced to SHOOT (commit) inside 1.0m.
COMMIT_RADIUS_M: float = 1.0
# Items 1/4: < 0.8m is the collapse/commit zone -- the collapse vector
# overrides the lateral correction entirely.
COLLAPSE_COMMIT_RADIUS_M: float = 0.8
# Item 5: spacing-Y adjustments stop inside 1.5m.
SPACING_FREEZE_RADIUS_M: float = 1.5
# Item 1 mid-distance step < 1.2m.
DIST_BAND_1_2_M: float = 1.2
# Item 1 outer distance step < 2.0m.
DIST_BAND_2_0_M: float = 2.0

# ─────────────────────────────────────────────────────────────────────
# ITEM 1 -- distance-based lateral correction clamp cascade
# ─────────────────────────────────────────────────────────────────────
#    dist < 0.8 -> 0.0     (collision finish owns the lateral)
#    dist < 1.2 -> 0.35
#    dist < 2.0 -> 0.55
#    else       -> 1.0
DIST_LATERAL_SCALE_STEPS: tuple = (
    (COLLAPSE_COMMIT_RADIUS_M, 0.0),
    (DIST_BAND_1_2_M, 0.35),
    (DIST_BAND_2_0_M, 0.55),
)

# ─────────────────────────────────────────────────────────────────────
# ITEM 2 -- band-weighted correction
# ─────────────────────────────────────────────────────────────────────
# AGGRESSIVE: less lateral, more forward (step-in-front behaviour).
# BALANCED:   mild lateral reduction.
# PASSIVE:    more lateral allowed (holds the width line).
BAND_LATERAL_SCALE: dict = {
    "AGGRESSIVE": 0.65,
    "BALANCED": 0.85,
    "PASSIVE": 1.10,
}

# ─────────────────────────────────────────────────────────────────────
# ITEM 3 -- BC lateral velocity dampening
# ─────────────────────────────────────────────────────────────────────
# When the BC is stepping sideways fast (|vy| > 1 m/s) the lateral
# correction is damped so the defender stops chasing the step and keeps
# forward momentum into the contact point.
BC_LATERAL_DAMP: float = 0.60
BC_LATERAL_VY_THRESHOLD: float = 1.0

# ─────────────────────────────────────────────────────────────────────
# ITEM 4 -- collapse vector override (commit zone)
# ─────────────────────────────────────────────────────────────────────
# Inside COLLAPSE_COMMIT_RADIUS_M the final heading is the unit vector
# from defender -> BC with only a tiny lateral allowance (0.25) so the
# finish is a straight line into contact.
COLLAPSE_LATERAL_SCALE: float = 0.25

# ─────────────────────────────────────────────────────────────────────
# ITEM 8 -- logging hook
# ─────────────────────────────────────────────────────────────────────
DEBUG_COLLAPSE_GEOMETRY: bool = False


# ─────────────────────────────────────────────────────────────────────
# PURE FACTORS
# ─────────────────────────────────────────────────────────────────────

def distance_factor(dist_to_bc) -> float:
    """Item 1 cascade factor for a given BC distance.

    1.0 outside 2.0m, 0.55 inside 2.0m, 0.35 inside 1.2m, 0.0 inside 0.8m.
    """
    dist_to_bc = float(dist_to_bc)
    for radius, factor in DIST_LATERAL_SCALE_STEPS:
        if dist_to_bc < radius:
            return factor
    return 1.0


def band_factor(band) -> float:
    """Item 2 band weight (unknown band -> neutral 1.0)."""
    return BAND_LATERAL_SCALE.get(band, 1.0)


def bc_vy_factor(bc_vy) -> float:
    """Item 3 damp factor from the BC's live lateral velocity."""
    bc_vy = float(bc_vy if bc_vy is not None else 0.0)
    if abs(bc_vy) > BC_LATERAL_VY_THRESHOLD:
        return BC_LATERAL_DAMP
    return 1.0


def collapse_lateral_scale(dist_to_bc, band=None, bc_vy=0.0,
                           include_distance: bool = True) -> float:
    """Combined lateral-correction multiplier (items 1 + 2 + 3).

    ``include_distance=False`` skips the item-1 cascade.  The commit-zone
    caller uses this so the tiny 0.25 lateral allowance of the collapse
    vector (item 4) survives while the band + BC-damp still apply.
    """
    scale = 1.0
    if include_distance:
        scale *= distance_factor(dist_to_bc)
    scale *= band_factor(band)
    scale *= bc_vy_factor(bc_vy)
    return scale


# ─────────────────────────────────────────────────────────────────────
# ZONE GUARDS
# ─────────────────────────────────────────────────────────────────────

def collapse_commit(dist_to_bc) -> bool:
    """Item 4: True inside the 0.8m commit zone (collapse vector override)."""
    return float(dist_to_bc) < COLLAPSE_COMMIT_RADIUS_M


def freeze_spacing_y(dist_to_bc) -> bool:
    """Item 5: True inside 1.5m -- stop the spacing chain pulling sideways."""
    return float(dist_to_bc) < SPACING_FREEZE_RADIUS_M


def depth_role_locked(dist_to_bc) -> bool:
    """Item 6: True inside 2.0m -- no depth-role / side / band changes."""
    return float(dist_to_bc) < COLLAPSE_LOCK_RADIUS_M


def should_commit(dist_to_bc) -> bool:
    """Item 7: True inside 1.0m -- force the COMMIT (SHOOT) movement state."""
    return float(dist_to_bc) < COMMIT_RADIUS_M


# ─────────────────────────────────────────────────────────────────────
# ITEM 8 -- logging hook
# ─────────────────────────────────────────────────────────────────────

def debug_collapse_log(player, *, dist_to_bc, lateral_corr_before,
                       lateral_corr_after, bc_vy, band, spacing_y_adjust,
                       collapse_vx, collapse_vy, movement_state) -> None:
    """Emit one per-defender collapse-geometry debug line (no-op unless
    ``DEBUG_COLLAPSE_GEOMETRY`` is True)."""
    if not DEBUG_COLLAPSE_GEOMETRY:
        return
    ms_name = getattr(movement_state, "name", None) or str(movement_state)
    print(
        "[COLLAPSE_GEOM] id=%s dist_bc=%.3f lat_corr=%.3f->%.3f "
        "bc_vy=%.3f band=%s spacing_y_adj=%.3f collapse=(%.3f, %.3f) "
        "ms=%s"
        % (
            getattr(player, "player_id", "?"),
            float(dist_to_bc),
            float(lateral_corr_before),
            float(lateral_corr_after),
            float(bc_vy if bc_vy is not None else 0.0),
            band,
            float(spacing_y_adjust),
            float(collapse_vx),
            float(collapse_vy),
            ms_name,
        )
    )
