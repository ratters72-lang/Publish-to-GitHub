"""seed_hold.py — stale-seed lockdown (kill residual jitter, 2026-09-09).

The residual-jitter signal (TARGET_JIGGLE / slot flip-flop / lane saw) comes
from competing movement/spacing writers re-pointing a line body's
``target_y`` (lateral) and ``target_x`` (depth) frame-to-frame while its
geometry seeds are stale — i.e. while the per-defender spacing-Y seed or the
depth-role seed has not been refreshed for the current frame.

This module implements the three-part stale-seed lockdown:

  1. FREEZE LATERAL WRITERS — no writer's ``target_y`` output lands:
     ``target_y`` is restored to the frame-start Y.  The post-writer pass in
     DefenceManager.update_open_play runs after every writer, so this is the
     authoritative final value.
  2. CLAMP target_x DRIFT — ``target_x`` is clamped into the anchor band
     (``anchor_x ± SEED_HOLD_XDRIFT_BAND_M``), killing the seesawing depth
     target.
  3. FORCE MovementState HOLD — ``movement_state = MovementState.HOLD`` with
     ``move_speed = 0`` / ``vx = vy = 0`` so the body cannot move while its
     geometry is undefined.

``is_seed_stale(d, world)`` is the single staleness predicate.  It is scoped
to genuine line bodies (depth-role lanes) — markers / DH / tacklers / chase /
fullback / frozen / tackle-state bodies are owned by their own writers and
never held here.

The depth-role seed is considered stale ONLY once a ruck is established
(``world.ruck_established``): before the first tackle the bands are
legitimately unformed, so a ``depth_role`` of None is NOT stale there.

The un-gated per-frame preamble (``run_defensive_pipeline``) refreshes every
seed each frame, so in steady OPEN_PLAY ``is_seed_stale`` returns False and
this module is a byte-identical no-op.  It fires only in the reform/restore
windows and on force-reseeded defenders — the residual-jitter cases.
"""

from __future__ import annotations

from player.player import DefenderRole
from player.movement_states import MovementState
from game.defence.defence_lvl_1.anchor_authority import (
    defensive_line_anchor_m as _anchor_m,
)
# ── PRE-TACKLE CORRIDOR (2026-09-15) ──
# SPEED-AUTHORITY CONSOLIDATION.  This module is a POST-WRITER pass: it runs
# after every movement writer and zeroes move_speed / vx / vy and forces HOLD
# on any body whose geometry seeds look stale.  A committed tackler chasing
# the carrier is legitimately OFF its lane seed (its depth_role / spacing-Y
# seed is not refreshed while it converges), so without this exemption the
# seed-hold would zero the corridor's governed approach velocity on exactly
# the frames that matter -- the last writer overwriting the taper.  Bodies
# the corridor owns are excluded; everything else is byte-identical.
from game.defence.defence_lvl_1.pretackle_corridor import (
    is_corridor_owned as _is_corridor_owned,
)

# The depth-role lanes whose spacing-Y / depth-role seeds this module guards.
_VALID_LANE_ROLES = frozenset({"A", "B", "C", "D", "CENTRE", "WINGER"})

# Roles that never hold a line seed (their own writers own their targets).
_NON_LINE_ROLES = frozenset({
    "MARKER_A", "MARKER_B", "DUMMY_HALF",
    "TACKLER_1", "TACKLER_2", "TACKLER_3",
    "DEF_CHASE", "KICK_CHASE",
})

# target_x drift band around the anchor (mirrors the MMDL X-drift guard).
SEED_HOLD_XDRIFT_BAND_M: float = 14.0


def is_seed_stale(d, world) -> bool:
    """True when *d* is a line body whose spacing-Y or depth-role seed is stale.

    Returns False for non-line bodies (their own writers own them) and for
    healthy line bodies whose per-frame seeds were refreshed this frame.
    """
    if d is None:
        return False
    # CORRIDOR EXEMPTION (2026-09-15): a corridor-owned tackler is written
    # by pre_tackle_authority alone -- it must never be held here.  It is a
    # genuine converger, so its geometry seeds are legitimately stale; the
    # HOLD below would zero the corridor's governed taper and contact
    # velocity.
    if _is_corridor_owned(d):
        return False
    if bool(getattr(d, "fullback_role_active", False)):
        return False
    if bool(getattr(d, "frozen", False)):
        return False
    if float(getattr(d, "freeze_timer", 0.0) or 0.0) > 0.0:
        return False
    if bool(getattr(d, "tackle_state_active", False)):
        return False

    role = getattr(d, "defender_role", None)
    role_name = getattr(role, "name", None) or str(role) if role is not None else None
    if role_name in _NON_LINE_ROLES:
        return False
    # Chase-owned bodies carry live chase target fields.
    if hasattr(d, "chase_target_x") or hasattr(d, "chase_target_y"):
        return False

    # ── DEPTH-ROLE SEED STALE (only once a ruck is established) ──
    if bool(getattr(world, "ruck_established", True)):
        depth_role = getattr(d, "depth_role", None)
        if depth_role is None or depth_role not in _VALID_LANE_ROLES:
            return True
        if getattr(d, "depth_side", None) is None:
            return True
        if not bool(getattr(d, "_depth_seeded", False)):
            return True
        if getattr(d, "role_depth_target_x", None) is None:
            return True
        if getattr(d, "role_depth_target_y", None) is None:
            return True

    # ── SPACING-Y SEED STALE ──
    from game.defence.defence_lvl_1.defensive_pipeline import (
        frame_geometry_is_fresh as _geo_fresh,
    )
    if not _geo_fresh(world):
        return True
    dct = getattr(world, "frame_spacing_targets", None)
    if not dct:
        return True
    if id(d) not in dct:
        return True
    if getattr(d, "frame_spacing_y", None) is None:
        return True
    return False


def apply_seed_hold(world, defenders, pre_pos=None) -> list:
    """Apply the stale-seed lockdown to every stale line body.

    For each stale body:
      1. FREEZE LATERAL:  ``d.target_y = pre_y`` (frame-start Y).
      2. CLAMP target_x:  ``d.target_x`` into ``[anchor - band, anchor + band]``.
      3. FORCE HOLD:      ``d.movement_state = HOLD``, ``move_speed = 0.0``,
                          ``vx = 0.0``, ``vy = 0.0``.

    ``pre_pos`` is ``{player_id: (x, y)}`` captured at the very START of the
    frame by the caller, so the freeze reference is the frame-authoritative
    start position (mirrors the tackle-begin glide snapshot).

    Returns the list of held defenders.
    """
    anchor_x = _anchor_m(world)
    lo = anchor_x - SEED_HOLD_XDRIFT_BAND_M
    hi = anchor_x + SEED_HOLD_XDRIFT_BAND_M
    held: list = []
    for d in defenders:
        # CORRIDOR EXEMPTION (2026-09-15): belt-and-braces against a caller
        # supplying its own stale predicate.  The corridor is the sole motion
        # authority for these bodies; this pass must never zero them.
        if _is_corridor_owned(d):
            continue
        if not is_seed_stale(d, world):
            continue
        pid = getattr(d, "player_id", None)
        if pre_pos is not None and pid is not None and pid in pre_pos:
            _pre_x, _pre_y = pre_pos[pid]
        else:
            _pre_x = float(getattr(d, "x", 0.0) or 0.0)
            _pre_y = float(getattr(d, "y", 0.0) or 0.0)
        # 1. FREEZE LATERAL (target_y = frame-start Y) — no Y writer lands.
        d.target_y = float(_pre_y)
        # 2. CLAMP target_x drift into the anchor band.
        tx = float(getattr(d, "target_x", _pre_x) or _pre_x)
        d.target_x = max(lo, min(hi, tx))
        # 3. FORCE MovementState HOLD.
        d.movement_state = MovementState.HOLD
        d.move_speed = 0.0
        d.vx = 0.0
        d.vy = 0.0
        d.target_source = "seed_hold"
        held.append(d)
    return held


def clear_seed_hold(d) -> None:
    """Clear a seed-hold target-source stamp on *d* (reset safety net)."""
    if d is None:
        return
    if getattr(d, "target_source", None) == "seed_hold":
        d.target_source = None
