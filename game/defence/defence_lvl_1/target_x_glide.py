"""target_x_glide.py — per-frame target_x glide cap (TARGET_JIGGLE fix, 2026-09-07).

The TARGET_JIGGLE audit (game/debug/freeze_audit.py) fires when a STATIONARY
line body's ``target_x`` ranges > 5.0 m across a 10-frame window — i.e.
competing movement/spacing writers re-point a parked defender's ``target_x``
frame-to-frame (``bc.x + WIDTH_OFFSETS[role]`` vs ``ruck_x + offset`` vs
``anchor_x`` vs retreat lane vs MMDL X-drift guard).

This helper clamps EVERY non-exempt line body's ``target_x`` change to
``TARGET_X_GLIDE_SPEED_MPS * dt`` per frame, so the target converges over
frames instead of seesawing metres.  It runs ONCE per frame at the movement
merge point (``DefenceManager.update_open_play``), after ALL writers and
before ``p.update(dt)`` integration and the audit.

UNIVERSAL RATE LIMIT (2026-09-09): applies regardless of movement state —
parked (RETREAT/DRIFT/HOLD/SLOW) AND pursuit (CHASE/SHOOT).  The old
parked-state-only gate let a pursuit body's target_x seesaw metres (the
residual TARGET_JIGGLE bounce).  EXEMPT (legally snap): committed tacklers,
pre_tackle COMMIT/PLANTED owners, markers / DH, the dynamic fullback, and
drive-end-authoritative bodies — those must reach the live target.
"""
from __future__ import annotations

# ── THE GLIDE BUDGET (single source) ─────────────────────────────────
# glide_budget owns the ONE glide ceiling.  It imports only stdlib at
# module scope, so this reach cannot create a cycle in any load order.
from game.defence.defence_lvl_1.glide_budget import (
    GLIDE_SPEED_MPS,
    glide_smooth_enabled,
)

# SINGLE-SOURCED from glide_budget.GLIDE_SPEED_MPS (was a local 6.0 that
# merely mirrored defence_manager.DEPTH_LOCK_GLIDE_SPEED_MPS).
TARGET_X_GLIDE_SPEED_MPS: float = GLIDE_SPEED_MPS

# The parked line states whose target_x was historically glide-capped.
# UNIVERSAL RATE LIMIT (2026-09-09): the cap now applies to EVERY non-exempt
# body regardless of state, so the state gate is removed below.  Kept as
# documentation of the original scope.
_PARKED_STATES = frozenset({"RETREAT", "DRIFT", "HOLD", "SLOW"})


def is_target_x_glide_eligible(d, world=None) -> bool:
    """True when *d*'s target_x may be glide-capped.

    UNIVERSAL RATE LIMIT (2026-09-09): applies to EVERY non-exempt defender
    regardless of movement state — parked (RETREAT/DRIFT/HOLD/SLOW) AND
    pursuit (CHASE/SHOOT).  The old parked-state-only restriction let a CHASE
    body's target_x seesaw metres (the residual TARGET_JIGGLE bounce).  Legal
    snap exemptions (committed tacklers, pre-tackle COMMIT/PLANTED, markers /
    DH, fullback, drive-end-authoritative) return False below.
    """
    if d is None:
        return False
    if bool(getattr(d, "frozen", False)):
        return False
    if bool(getattr(d, "fullback_role_active", False)):
        return False
    if bool(getattr(d, "_tackle_commit_active", False)):
        return False

    # Pre-tackle COMMIT/PLANTED owners chase the live BC — never capped.
    from game.defence.defence_lvl_1.pre_tackle_authority import (
        PreTackleState,
        is_pre_tackle_managed,
    )
    if is_pre_tackle_managed(d):
        _st = getattr(d, "pre_tackle_state", None)
        if _st in (PreTackleState.COMMIT, PreTackleState.PLANTED):
            return False

    from player.player import DefenderRole
    role = getattr(d, "defender_role", None)
    if role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B,
                DefenderRole.DUMMY_HALF):
        return False

    # Drive-end-authoritative frames are the deliberate one-frame snap.
    # Only applies with a REAL stamp (> -1) inside a REAL world (frame known);
    # a defaul/-1 stamp or world=None must never exempt a parked body.
    _auth_until = getattr(d, "authoritative_until_frame", -1)
    if _auth_until is not None and _auth_until > -1 and world is not None:
        _frame = getattr(world, "frame_count", -1)
        if _auth_until >= _frame:
            return False

    # UNIVERSAL: every non-exempt body is glide-capped regardless of state.
    return True


def glide_cap_target_x(d, dt: float, max_step: float | None = None,
                       tuners=None, world=None) -> None:
    """Clamp *d*.target_x to at most *max_step* m/frame from its previous value.

    BOUNDED FROM FRAME ONE (2026-09-20).  The original self-seeded the
    baseline from the RAW writer value and then returned WITHOUT capping, so
    the first frame after a reset — or after the baseline had been cleared —
    landed the full uncapped writer swing.  That is a re-target spike at
    exactly the moment the body has no glide history to blunt it (kickoff,
    fresh sequence, thaw).  The baseline is now seeded from the body's OWN
    ``x`` and the cap is applied on that SAME call, so frame one is already a
    bounded glide from where the body actually stands.

    Afterwards each frame's target_x is clamped toward the previous frame's
    value, so a large writer swing converges over frames instead of snapping.

    REVERSIBLE: ``RLM_GLIDE_SMOOTH=0`` or ``tuners.glide_smooth_enabled =
    False`` restores the legacy "self-seed the raw value, no cap on the first
    call" behaviour byte-for-byte.
    """
    _max = TARGET_X_GLIDE_SPEED_MPS * dt if max_step is None else float(max_step)
    prev = getattr(d, "_prev_target_x_glide", None)
    if prev is None:
        if not glide_smooth_enabled(tuners, world):
            # LEGACY PATH — seed the raw writer value, no cap this frame.
            d._prev_target_x_glide = float(d.target_x)
            return
        # Seed from the body's own position so the cap below has a truthful
        # origin, and let it apply on this same call.
        prev = float(getattr(d, "x", float(d.target_x)))
    if _max <= 0.0:
        d._prev_target_x_glide = float(d.target_x)
        return
    tx = float(d.target_x)
    dx = tx - float(prev)
    if abs(dx) > _max:
        d.target_x = float(prev) + (1.0 if dx > 0.0 else -1.0) * _max
    d._prev_target_x_glide = float(d.target_x)


def clear_target_x_glide(d) -> None:
    """Drop the glide baseline (kickoff / fresh-sequence reset)."""
    if hasattr(d, "_prev_target_x_glide"):
        del d._prev_target_x_glide
