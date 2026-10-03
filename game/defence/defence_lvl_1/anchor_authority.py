"""
anchor_authority.py — Anchor Authority Hardening (Task G, 2026-08-10).

PURPOSE
-------
Make the anchor (ruck_x + 10) the SINGLE source of truth for defensive
depth, immune to spacing, width, audit, band jitter, DH movement, BC
movement, and TES/PTB transitions.

AUTHORITY LAYERS (spec Task G)
------------------------------
  Layer 0 — Anchor definition:
      defensive_line_anchor_m(world) == world.ruck_x_m + 10.0
      The anchor is NEVER derived from BC.x, DH.x, spacing, band role,
      width chain, audit, defender positions, BC retreat/overshoot, or
      DH overshoot.  Anchor = ruck_x + 10.  Always.  Forever.

  Layer 1 — Anchor lock:
      Once ``compute_and_lock_anchor`` runs for the frame the anchor is
      immutable.  Any subsystem attempting to CHANGE the locked anchor
      is refused with ``AnchorAuthorityViolation``.  Re-publishing the
      SAME value is idempotent (permitted) so multiple legal writers in
      one frame (RELEASE republish, OPEN_PLAY republish) never trip it.

  Layer 2 — Depth target authority:
      ``player.role_depth_target_x`` must equal the anchor.  The stamping
      in defensive_depth_roles.py delegates here so the role-depth X is
      single-sourced from the anchor.

  Layer 3/4 — Spacing / width chain guards:
      Spacing and width chains may adjust Y only.  Any output carrying a
      ``depth`` key violates the contract and raises
      ``AnchorAuthorityViolation``.

  Layer 5 — Audit guard:
      ``audit_stable_depth`` reads ``world.depth_role_state[id].role``
      (the STABLE structural role), never the transient band role, and
      cannot override a locked depth.

  Layer 6 — TES/PTB reset authority:
      ``reset_anchor_authority`` clears the lock so the anchor recomputes
      from the NEW ruck next frame.  Reset happens ONLY at an explicit
      RUCK-TRANSITION event — every one of which re-locks ``ruck_x_m``
      first, so "the anchor moved" is always attributable to a named
      cycle event and never to drift.  The complete set (six writers;
      sites 2-4 are the tackle-cycle re-locks, documented here because
      the fullback break test depends on knowing the anchor may move
      MID-CYCLE, not only at the cycle boundary):

        - PTB complete                      (PTB_EXEC -> PTB handover)
        - CONTACT -> DRIVE entry            (_update_tackle_contact tail:
                                             "anchor the drive-time
                                             defensive line to the CURRENT
                                             tackle point, not the stale
                                             pre-tackle ruck")
        - DRIVE END lock                    (_update_tackle_drive)
        - progressive-drag ruck exit        (_exit_progressive_drag_to_ruck)
        - new ruck_x written on DOMINANCE -> RELEASE (_prepare_release)
        - kickoff reset / kickoff catch      (fresh ruck from BC)

      The four tackle-cycle writers move the anchor FORWARD (GOAL-SIDE),
      onto the tackle point, by design: the line's depth must track the
      real ruck, and a stale pre-tackle ruck would leave the line
      defending from last cycle's mark.  A consumer that needs the line
      anchor as a STABLE reference across the cycle must therefore latch
      it rather than re-read it; see ``fullback_role``'s break latch
      (``break_test_line_anchor_x``), which floors the live anchor with
      the anchor seen when a live break was first classified so a
      mid-cycle forward relock cannot retroactively withdraw a verdict
      already reached.  The anchor itself is NOT made read-only: the
      sweeper's own DEPTH geometry keeps following the live ruck.

      NEVER at POST_RUCK, BC/DH movement, band jitter, spacing recompute,
      width recompute, or audit events.

  Layer 7 — Defender retreat enforcement:
      ``enforce_retreat_to_anchor`` forces line-holding defenders ahead of
      the anchor back to ``anchor_x`` every frame.  Legal engagers
      (PRIMARY/SECONDARY close/chase), markers, dummy-half and the
      dynamic fullback are exempt so the tackle economy survives.

  Layer 8 — Anchor drift detection:
      ``assert_anchor_consistency`` raises if
      ``abs(role_depth_target_x - anchor_x) > 0.01``.

The state key ``depth_role_state`` stores ``DepthRoleState`` objects so
the spec's dot access (``world.depth_role_state[id].role`` /
``state.locked``) works.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from player.player import Player, DefenderRole


# ─────────────────────────────────────────────────────────────────────
# CONSTANTS (Authority Layer 0)
# ─────────────────────────────────────────────────────────────────────

# The defensive-line depth offset behind the ruck (metres).  Anchor =
# ruck_x + ANCHOR_DEPTH_M.  Single source; every other module must read
# the anchor through this module, never redefine the constant.
ANCHOR_DEPTH_M: float = 10.0

# The defensive line may retreat 10m behind the ruck, but it must never
# move behind the field tryline on the attacking side.  Attack runs +X,
# so the right-side bound is a hard clamp at x=110.
LEFT_TRYLINE_X: float = 10.0
RIGHT_TRYLINE_X: float = 110.0

# Default ruck X used only when a verification stub provides no ruck and
# no published anchor (matches defensive_engagement_state fallback).
DEFAULT_RUCK_X: float = 60.0

# Allowed residual difference between the locked anchor and a re-publish
# attempt (metres).  Legal re-publishers write the identical value.
LOCK_EPSILON_M: float = 1e-6

# Maximum allowed drift of a depth target from the anchor (metres).
# Layer 8: > 0.01 raises AnchorAuthorityViolation.
DRIFT_EPSILON_M: float = 0.01


# ─────────────────────────────────────────────────────────────────────
# EXCEPTION
# ─────────────────────────────────────────────────────────────────────

class AnchorAuthorityViolation(Exception):
    """Raised when a subsystem attempts to modify the locked anchor,
    override the depth target, or inject a depth key into a Y-only chain."""


class SpacingDepthViolation(AnchorAuthorityViolation):
    """Raised when the spacing chain attempts to output depth / X
    (``depth`` or ``target_x`` keys), or to overwrite chase / TES / PTB
    targets (Task H Authority Layer S0/S3/S4).

    Spacing is legally forbidden from touching X.  It is a pure lateral
    (Y-only) subsystem.
    """


# ─────────────────────────────────────────────────────────────────────
# DEPTH ROLE STATE — dot-access record (Layer 5 spec shape)
# ─────────────────────────────────────────────────────────────────────

@dataclass
class DepthRoleState:
    """Persistent structural depth-role/engagement record.

    Stored as ``world.depth_role_state[id(defender)]``.  Exposes the
    spec's attribute access: ``state.role`` (stable AGGRESSIVE/BALANCED/
    PASSIVE posture), ``state.side``, ``state.locked`` (role-lock), and
    ``state.last_switch_frame`` (hysteresis bookkeeping).
    """
    role: str = "BALANCED"
    side: Optional[int] = None
    locked: bool = False
    last_switch_frame: int = 0


def get_depth_role_state(world, defender: Player) -> DepthRoleState:
    """Return the persistent DepthRoleState for *defender* (create on first access)."""
    store = getattr(world, "depth_role_state", None)
    if store is None:
        store = {}
        setattr(world, "depth_role_state", store)
    state = store.get(id(defender))
    if state is None or not isinstance(state, DepthRoleState):
        state = DepthRoleState(
            role="BALANCED",
            side=getattr(defender, "depth_side", None),
            locked=False,
            last_switch_frame=getattr(world, "frame_count", 0),
        )
        store[id(defender)] = state
    return state


# ─────────────────────────────────────────────────────────────────────
# AUTHORITY LAYER 0 — ANCHOR DEFINITION
# ─────────────────────────────────────────────────────────────────────

def defensive_line_anchor_m(world) -> float:
    """Return the authoritative defensive-line anchor X (metres).

    CONTRACT: while a ruck exists the anchor is the usual ``ruck_x_m + 10``
    retreat line, but it is hard-clamped at the right tryline so the line
    never moves behind the field boundary.  This preserves the 10m rule up
    until the tryline is reached, and stops the line exactly at the tryline
    once the offset would overshoot it.

    When the frame is locked (Layer 1) the locked value is returned —
    the anchor is immutable for the frame.

    Pre-ruck fallbacks (kickoff/catch era): when ``ruck_x_m`` is None the
    published anchor (written via ``set_pre_ruck_anchor``) or a stub
    default is used, matching defensive_engagement_state semantics.
    """
    locked = getattr(world, "anchor_locked", False)
    published = getattr(world, "defensive_line_anchor", None)
    if locked and published is not None:
        return float(published)
    ruck_x = getattr(world, "ruck_x_m", None)
    if ruck_x is not None:
        raw_anchor = float(ruck_x) + ANCHOR_DEPTH_M
        return max(LEFT_TRYLINE_X, min(RIGHT_TRYLINE_X, raw_anchor))
    if published is not None and float(published) > 0.0:
        return float(published)
    return DEFAULT_RUCK_X + ANCHOR_DEPTH_M


# ─────────────────────────────────────────────────────────────────────
# AUTHORITY LAYER 1 — ANCHOR LOCK
# ─────────────────────────────────────────────────────────────────────

def compute_and_lock_anchor(world) -> float:
    """Compute the frame anchor once, publish it, and lock it.

    Called exactly once per frame, once per ruck, by the OPEN_PLAY
    defensive pipeline (and by the RELEASE/PTB-era republish writers with
    the same ruck).  Idempotent: re-publishing the SAME value is a no-op;
    attempting to CHANGE a locked anchor raises
    ``AnchorAuthorityViolation``.

    Returns the locked anchor value.
    """
    candidate = defensive_line_anchor_m(world)
    locked = getattr(world, "anchor_locked", False)
    current = getattr(world, "defensive_line_anchor", None)
    if locked and current is not None and abs(float(current) - candidate) > LOCK_EPSILON_M:
        raise AnchorAuthorityViolation(
            "anchor modification attempted: "
            f"locked={current:.4f} requested={candidate:.4f}"
        )
    world.defensive_line_anchor = candidate
    world.anchor_locked = True
    return candidate


def set_anchor(world, anchor_x: float) -> float:
    """Set the anchor with full authority enforcement.

    While a ruck exists the legal anchor is the usual ``ruck_x_m + 10``
    retreat line, clamped to the right tryline.  This preserves the normal
    retreat rule until the tryline is reached, then locks at the tryline.
    Locked anchors may only be re-published with the identical value.
    """
    ruck_x = getattr(world, "ruck_x_m", None)
    if ruck_x is not None:
        expected_raw = float(ruck_x) + ANCHOR_DEPTH_M
        expected = max(LEFT_TRYLINE_X, min(RIGHT_TRYLINE_X, expected_raw))
        if abs(float(anchor_x) - expected) > LOCK_EPSILON_M:
            raise AnchorAuthorityViolation(
                "anchor modification attempted: expected tryline-clamped "
                f"ruck_x + 10 ({expected:.4f}), got {float(anchor_x):.4f}"
            )
    locked = getattr(world, "anchor_locked", False)
    current = getattr(world, "defensive_line_anchor", None)
    if locked and current is not None and abs(float(current) - float(anchor_x)) > LOCK_EPSILON_M:
        raise AnchorAuthorityViolation(
            "anchor modification attempted: anchor is locked for this frame"
        )
    world.defensive_line_anchor = float(anchor_x)
    world.anchor_locked = True
    return float(anchor_x)


def set_pre_ruck_anchor(world, anchor_x: float) -> float:
    """Pre-ruck era anchor writer (kickoff in-flight only).

    During KICKOFF there is no ruck, so the anchor legitimately tracks
    the live ball (``ball.x_m + 10``) and is NOT locked.  Refuses to run
    while a ruck exists: once ``ruck_x_m`` is set, the authority is
    ``compute_and_lock_anchor`` / ``set_anchor`` only.
    """
    ruck_x = getattr(world, "ruck_x_m", None)
    if ruck_x is not None:
        raise AnchorAuthorityViolation(
            "pre-ruck anchor writer used with a live ruck (ruck_x_m set)"
        )
    world.defensive_line_anchor = float(anchor_x)
    world.anchor_locked = False
    return float(anchor_x)


def is_anchor_locked(world) -> bool:
    """Return True when the frame anchor has been locked."""
    return bool(getattr(world, "anchor_locked", False))


# ─────────────────────────────────────────────────────────────────────
# AUTHORITY LAYER 6 — TES/PTB RESET AUTHORITY
# ─────────────────────────────────────────────────────────────────────

def reset_anchor_authority(world) -> None:
    """Unlock the anchor so it recomputes from the NEW ruck next frame.

    Spec contract: reset anchor ONLY at an explicit RUCK-TRANSITION
    event, i.e. exactly when ``ruck_x_m`` itself is re-written.  The
    complete call-site set (see the Layer-6 header for the same list,
    with the tackle-cycle sites called out):

        PTB complete                      (PTB_EXEC -> PTB handover)
        CONTACT -> DRIVE entry            (_update_tackle_contact tail)
        DRIVE END lock                    (_update_tackle_drive)
        progressive-drag ruck exit        (_exit_progressive_drag_to_ruck)
        OPE._prepare_release              (DOMINANCE -> RELEASE)
        kickoff reset / kickoff catch     (fresh ruck from BC)

    The middle three are TACKLE-CYCLE re-locks that move the anchor
    FORWARD onto the live tackle point by design.  Consumers needing a
    cycle-stable line anchor must latch it (see ``fullback_role``'s
    ``break_test_line_anchor_x``) rather than assume the anchor is
    immutable for the duration of a tackle.

    NEVER at POST_RUCK, BC/DH movement, band jitter, spacing recompute,
    width recompute, or audit events.
    """
    world.anchor_locked = False


# ─────────────────────────────────────────────────────────────────────
# AUTHORITY LAYER 5 — AUDIT GUARD (stable role, never band role)
# ─────────────────────────────────────────────────────────────────────

def audit_stable_depth(world, defender: Player) -> str:
    """Return the STABLE structural depth role for *defender*.

    Reads ``world.depth_role_state[id].role`` — never the transient band
    role.  The audit cannot override a locked depth: the guard below is
    the spec's ``if state.locked: return`` — a locked depth is returned
    unchanged and no switch/override is attempted.
    """
    state = get_depth_role_state(world, defender)
    if state.locked:
        # audit cannot override locked depth — return the locked value.
        return state.role
    return state.role


# ─────────────────────────────────────────────────────────────────────
# AUTHORITY LAYER 7 — DEFENDER RETREAT ENFORCEMENT
# ─────────────────────────────────────────────────────────────────────

# Roles that play tight to the ruck/set and are exempt from anchor retreat.
_RETREAT_EXEMPT_ROLES = frozenset({
    DefenderRole.MARKER_A,
    DefenderRole.MARKER_B,
    DefenderRole.DUMMY_HALF,
    DefenderRole.TACKLER_1,
    DefenderRole.TACKLER_2,
    DefenderRole.TACKLER_3,
    DefenderRole.DEF_CHASE,
})

# Depth classes that legally engage ahead of the anchor (close/chase).
_ENGAGED_DEPTH_CLASSES = frozenset({"PRIMARY", "SECONDARY"})


def enforce_retreat_to_anchor(world, defenders) -> None:
    """Force line-holding defenders ahead of the anchor back to it.

    Spec: if ``d.x < anchor_x`` then ``d.target_x = anchor_x``.  Every
    PASSIVE / line-shaped defender must retreat to anchor depth every
    frame until reached; no defender may remain in front of the anchor
    via spacing, width, or audit pull.

    WIDTH LANE PRESERVATION (2026-08-13): a defender holding a WIDTH LANE
    (width = X from WIDTH_OFFSETS, lane_x = ruck_x + WIDTH_OFFSETS[
    depth_role]) is exempt from the anchor pin — its lane X IS its legal
    forward position by design.  Pinning lane holders to anchor_x every
    frame collapsed the line's X-depth spread onto the WINGER slot
    ("retreat overrides width-X").  The anchor itself, its lock (Layer 1)
    and the Layer-8 role-depth stamp are untouched — only which DEFENDER
    receives the ``target_x = anchor_x`` write is narrowed.

    Exempt (by contract):
      - WIDTH-LANE holders (this fix): their own lane writer owns X.
      - PRIMARY/SECONDARY closers — they legally engage ahead of the line
        (structured close / chase writers own their targets).
      - Markers, dummy-half, TES tacklers, DEF_CHASE.
      - The dynamic fullback actor (backfield coverage, own anchor).
      - Frozen defenders (their targets are pinned by the freeze owner).
    """
    # Lazy import avoids the module cycle: defensive_width imports
    # anchor_authority, so this layer imports lane_x_of on demand.
    from game.defence.defence_lvl_1.defensive_width import lane_x_of

    # ── DRIVE DEPTH AUTHORITY (2026-09-06: BC-anchored depth wins mid-drive) ──
    # During the drive (OPEN_PLAY + TES tackle phases) the depth-role X is
    # BC-anchored (``role_depth_target_x == bc.x + WIDTH_OFFSETS[role]``).
    # The anchor pin (``ruck_x + 10``) must NOT override it mid-drive — a
    # defender re-classified SECONDARY (12 m proximity promotion) is not a
    # WIDTH-LANE holder, so ``lane_x_of`` returns None and it would otherwise
    # be snapped back to the stale ``ruck_x + 10`` instead of tracking the
    # driven carrier.  The ``ruck_x + 10`` snap is a POST-DRIVE retreat
    # contract only (RELEASE / PTB / POST_RUCK).
    from game.defence.defence_lvl_1.defensive_width import is_drive_phase
    drive_active = is_drive_phase(world)

    anchor_x = defensive_line_anchor_m(world)
    for d in defenders:
        if d is None:
            continue
        if not isinstance(d, Player):
            continue
        if getattr(d, "frozen", False) and getattr(d, "freeze_timer", 0.0) > 0.0:
            continue
        role = getattr(d, "defender_role", None)
        if role in _RETREAT_EXEMPT_ROLES:
            continue
        if getattr(d, "fullback_role_active", False):
            continue
        depth_class = getattr(d, "depth_class", "PASSIVE") or "PASSIVE"
        if depth_class in _ENGAGED_DEPTH_CLASSES:
            continue
        # WIDTH LANE PRESERVATION: a lane holder's own X writer owns the
        # horizontal target — never re-pin it to the anchor.
        if lane_x_of(d, world) is not None:
            continue
        # DRIVE DEPTH AUTHORITY: during the drive a depth-role holder
        # carrying a live BC-anchored role_depth_target_x keeps its
        # BC-anchored X — the anchor pin (ruck_x + 10) is a post-drive
        # retreat contract only.  The stamp is refreshed every frame by
        # stamp_drive_depth_x (run_defensive_pipeline) and always equals
        # bc.x + WIDTH_OFFSETS[role] mid-drive.
        if drive_active and getattr(d, "role_depth_target_x", None) is not None:
            continue
        if d.x < anchor_x:
            d.target_x = anchor_x


# ─────────────────────────────────────────────────────────────────────
# AUTHORITY LAYER 8 — ANCHOR DRIFT DETECTION
# ─────────────────────────────────────────────────────────────────────

def assert_anchor_consistency(world, defenders) -> None:
    """Raise if any depth target drifted from the expected depth (Layer 8).

    Checks ``player.role_depth_target_x`` (the depth-role X stamp) against
    the DRIVE-AWARE expected depth and rejects any deviation > 0.01 m that
    a subsystem injected during the frame.

    DRIVE-AWARE (2026-09-05): during the drive (OPEN_PLAY + TES tackle
    phases) the expected depth is ``bc.x + WIDTH_OFFSETS[depth_role]`` so
    the line tracks the ball carrier RIGHT of the BC.  After the drive
    ends (RELEASE / PTB / POST_RUCK) the expected depth is the ruck
    anchor (``ruck_x + 10``) so the line retreats the 10m.  The old
    hard-equal-to-anchor check rejected the BC-based depth and crashed
    the run (id=201 role_depth_target_x=8.26 vs anchor=13.26).
    """
    if not is_anchor_locked(world):
        return
    # Lazy import avoids the cycle: defensive_depth_roles imports
    # defensive_line_anchor_m from this module at module top.
    from game.defence.defence_lvl_1.defensive_depth_roles import (
        _depth_role_target_x as _expected_depth_x,
    )
    anchor_x = defensive_line_anchor_m(world)
    for d in defenders:
        if d is None or not isinstance(d, Player):
            continue
        stamp = getattr(d, "role_depth_target_x", None)
        if stamp is None:
            continue
        expected = _expected_depth_x(world, d)
        if abs(float(stamp) - expected) > DRIFT_EPSILON_M:
            raise AnchorAuthorityViolation(
                "depth target drifted from expected depth: "
                f"id={getattr(d, 'player_id', '?')} "
                f"role_depth_target_x={stamp:.4f} expected={expected:.4f} "
                f"anchor={anchor_x:.4f}"
            )


# ─────────────────────────────────────────────────────────────────────
# AUTHORITY LAYERS 3/4 — SPACING & WIDTH CHAIN GUARDS
# ─────────────────────────────────────────────────────────────────────

def assert_spacing_never_modifies_depth(update: dict) -> None:
    """Guard run inside/after spacing recompute (Layer 3).

    Spec pattern:
        if key == 'depth':
            continue  # spacing cannot modify depth
    Spacing may adjust width/lateral offsets/gaps/stagger/compression —
    it may NEVER adjust depth, anchor, or role-depth target X.  Any
    output carrying a ``depth`` key is a contract violation.
    """
    for key in list(update.keys()):
        if key == "depth":
            raise AnchorAuthorityViolation(
                "spacing cannot modify depth (depth key present in spacing output)"
            )


def assert_width_never_modifies_depth(update: dict) -> None:
    """Guard run inside/after width recompute (Layer 4).

    Width may adjust width/lateral alignment/sideline compression — it may
    NEVER adjust depth, anchor, or role-depth target X.
    """
    for key in list(update.keys()):
        if key == "depth":
            raise AnchorAuthorityViolation(
                "width cannot modify depth (depth key present in width output)"
            )


def assert_output_has_no_depth(update, subsystem: str) -> None:
    """Generic depth-key guard for any spacing/width chain output.

    Recurses into nested dicts.  Raises ``AnchorAuthorityViolation`` when
    any level carries a ``depth`` key.
    """
    if isinstance(update, dict):
        if "depth" in update:
            raise AnchorAuthorityViolation(
                f"{subsystem} cannot modify depth (depth key present)"
            )
        for value in update.values():
            assert_output_has_no_depth(value, subsystem)
