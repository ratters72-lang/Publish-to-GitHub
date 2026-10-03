"""Geometry validation for multi-tackler joining.

Checks that a candidate tackler has a valid approach vector,
does not collide with existing tacklers, and does not produce
illegal overlap or backwards movement.
"""

import math
from .tes_context import PlayerContext, ATTACH_RADIUS


def can_join(
    candidate: PlayerContext,
    bc: PlayerContext,
    engaged_tacklers: list[PlayerContext],
) -> bool:
    """Check geometry validity for a candidate joining the tackle.

    All of the following must be true:
      1. Candidate is moving towards the BC (not away)
      2. No collision / illegal overlap with existing tacklers
      3. No backwards movement relative to BC's forward direction

    Parameters
    ----------
    candidate : PlayerContext
        The defender attempting to join.
    bc : PlayerContext
        The ball carrier.
    engaged_tacklers : list[PlayerContext]
        All defenders already in the engagement.

    Returns
    -------
    bool
        True if the join is geometrically valid.
    """
    # 1. Candidate must be moving toward BC (unless already inside the
    #    genuine attach radius — i.e. truly in the pile).
    # The old '< 1.0 m → skip movement check' exemption was too loose: it
    # let a line defender who merely drifted within 1.0 m of the BC join
    # as a tackler without any commitment, which over-populated the TES
    # registry and split the defensive line.  The exemption is now limited
    # to candidates already inside ATTACH_RADIUS (0.7 m) — only a tackler
    # actually attached to the pile skips the movement check.
    dist_to_bc = math.hypot(candidate.x_m - bc.x_m, candidate.y_m - bc.y_m)
    if dist_to_bc > ATTACH_RADIUS:
        if not _is_moving_toward(candidate, bc):
            return False

    # 2. No collision with existing tacklers
    for other in engaged_tacklers:
        if other.player_id == candidate.player_id:
            continue
        if _would_collide(candidate, other):
            return False

    # 3. No backwards movement relative to BC's forward direction
    if _is_moving_backward_relative_to_bc(candidate, bc):
        return False

    return True


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _is_moving_toward(candidate: PlayerContext, bc: PlayerContext) -> bool:
    """True if candidate velocity has a positive component toward BC."""
    cand_spd = candidate.speed
    if cand_spd < 0.01:
        return False  # not moving at all

    # Direction from candidate to BC
    dx = bc.x_m - candidate.x_m
    dy = bc.y_m - candidate.y_m
    to_bc_dist = math.hypot(dx, dy)
    if to_bc_dist < 0.01:
        return True  # already on top of BC (edge case — let through)

    to_bc_dx = dx / to_bc_dist
    to_bc_dy = dy / to_bc_dist

    # Unit velocity of candidate
    cand_dx = candidate.vx / cand_spd
    cand_dy = candidate.vy / cand_spd

    # Dot product: > 0 means some component toward BC
    dot = cand_dx * to_bc_dx + cand_dy * to_bc_dy
    return dot > 0.0


def _would_collide(candidate: PlayerContext, other: PlayerContext) -> bool:
    """True if candidate would overlap with an existing tackler.

    Uses a minimum separation radius of 0.5 m.
    """
    MIN_SEPARATION_M = 0.5
    dx = candidate.x_m - other.x_m
    dy = candidate.y_m - other.y_m
    dist = math.hypot(dx, dy)
    return dist < MIN_SEPARATION_M


def _is_moving_backward_relative_to_bc(candidate: PlayerContext, bc: PlayerContext) -> bool:
    """True if candidate's movement is backwards from BC's perspective.

    BC's forward direction is assumed to be along BC's velocity vector.
    If BC is stationary, BC's facing direction is used.
    A candidate moving opposite to BC's forward direction (dot < 0)
    is considered backwards, which is illegal for joining.
    """
    bc_spd = bc.speed
    if bc_spd < 0.01:
        # BC not moving — use BC's facing direction
        bc_fwd_dx = math.cos(bc.facing)
        bc_fwd_dy = math.sin(bc.facing)
    else:
        bc_fwd_dx = bc.vx / bc_spd
        bc_fwd_dy = bc.vy / bc_spd

    # Candidate direction (vs BC)
    cand_to_bc_dx = bc.x_m - candidate.x_m
    cand_to_bc_dy = bc.y_m - candidate.y_m
    dist = math.hypot(cand_to_bc_dx, cand_to_bc_dy)
    if dist < 0.01:
        return False  # on top of BC

    cand_to_bc_dx /= dist
    cand_to_bc_dy /= dist

    # If candidate is behind BC (dot < 0) they are moving backward
    # relative to BC's forward direction
    dot = cand_to_bc_dx * bc_fwd_dx + cand_to_bc_dy * bc_fwd_dy
    return dot < 0.0
