"""
defensive_engagement_state.py — Persistent, hysteresis-hardened engagement state (Task A-2, 2026-08-10).

PURPOSE
-------
The defensive engine has two independent role systems:

  1. Structural depth roles (A/B/C/D/CENTRE/WINGER) — persistent, ruck-driven,
     already stabilised in defensive_depth_roles.py.  These define spacing/width/X.

  2. ENGAGEMENT band role (AGGRESSIVE / BALANCED / PASSIVE) — the BC-relative
     line posture that drives PRIMARY/SECONDARY/PASSIVE classification and the
     LATE_DEPTH_SWITCH audit.  This module makes that engagement role PERSISTENT —
     it is the fix for the frame-by-frame ``LATE_DEPTH_SWITCH depth=PASSIVE`` spam.

The previous engagement classification (classify_depth_all) recomputed every
defender's depth_class from live positions EVERY frame with no persistence,
no switch interval, no role-lock and no side-lock.  PASSIVE was re-entered
continuously, fed unstable anchors into spacing, and produced TARGET_JIGGLE /
width collapse.

WHAT THIS MODULE DOES (Task A-2 contracted behaviour)
-----------------------------------------------------
  - Maintains ``world.depth_role_state[id(d)]`` =
        {"role":   "AGGRESSIVE" | "BALANCED" | "PASSIVE",
         "side":   -1 | +1,
         "locked": bool,
         "last_switch_frame": int}
    updated ONCE per OPEN_PLAY frame.
  - Hysteresis: a defender MAY NOT switch engagement role unless:
        bc_x crosses a threshold
        (anchor_x - AGGRESSIVE_BAND_M / anchor_x + PASSIVE_BAND_M)
        AND (frame - last_switch_frame) > MIN_SWITCH_INTERVAL
    This prevents repeated PASSIVE re-entry.
  - Final switch conditions (spec; TASK IV asymmetric bands 2026-08-14):
        bc_x <  anchor_x - AGGRESSIVE_BAND_M  -> AGGRESSIVE (4.9 m)
        bc_x >  anchor_x + PASSIVE_BAND_M     -> PASSIVE    (4.85 m)
        else                                  -> BALANCED
    BUT ONLY IF the hysteresis interval has elapsed.
  - Late-depth-switch spam prevention (spec):
        role == last_role            -> DO NOTHING
        role != last_role, interval
        unmet                        -> KEEP last_role
        last_switch_frame updates    -> ONLY when a real switch occurs.
  - ROLE-LOCK: once a defender enters PASSIVE it stays PASSIVE until
        BC retreats behind anchor_x, OR PTB resets depth roles.
  - SIDE-LOCK: the side (-1/+1) persists for the entire open-play phase;
        no side flipping until TES/PTB resets.
  - Anchor integrity: the anchor this module uses is the SINGLE authority
    ``defensive_line_anchor_m(world) = ruck_x_m + 10.0`` — never recomputed
    from BC.x, never drifting behind BC.
  - Exposes the stable band role on each defender as ``d.role_depth_class``
    so ``classify_depth_all`` and the LATE_DEPTH_SWITCH audit read a
    frame-consistent, hysteresis-protected posture.

ROLE-DEPTH X (Task A-2 step 8 / Task G)
---------------------------------------
``d.role_depth_target_x`` is stamped by ``assign_depth_roles`` as
``defensive_line_anchor_m(world)`` — the anchor authority (ruck_x + 10),
single-sourced, never recomputed mid-frame or after spacing.

ANCHOR AUTHORITY (Task G, 2026-08-10)
-------------------------------------
All anchor logic now delegates to ``anchor_authority.py`` — the SINGLE
source of truth.  ``defensive_line_anchor_m(world)`` here is an alias for
the authority (existing verifiers import the name from this module).
``depth_role_state`` values are ``DepthRoleState`` objects so the spec's
dot access (``world.depth_role_state[id].role`` / ``state.locked``) works.
``reset_engagement_state`` also unlocks the anchor authority.

INTEGRATION
-----------
``classify_depth_all()`` in defensive_behaviour_unified.py calls
``update_engagement_state(world, defenders)`` FIRST, then reads the stable
``d.role_depth_class`` as the classification input before ranked tackler
selection.  The ranked PRIMARY/SECONDARY promotion still runs on top so the
tackle pipeline is never starved; the band-role governs the LINE UNIT's
posture (hold/advance/regroup), not per-defender freezing.

RESET
-----
``reset_engagement_state(world)`` is called on POST_RUCK -> OPEN_PLAY
(PTB reset) and on kickoff, clearing locks so the next open-play phase
re-bootstraps.
"""
from __future__ import annotations

import math

from player.player import Player

from game.defence.defence_lvl_1.anchor_authority import (
    defensive_line_anchor_m as _authority_anchor_m,
    DepthRoleState,
    get_depth_role_state as _get_depth_role_state,
    reset_anchor_authority,
)
from game.defence.defence_lvl_1.defensive_width import (
    depth_side_of,
)


# ─────────────────────────────────────────────────────────────────────
# TASK A-2 CONSTANTS
# ─────────────────────────────────────────────────────────────────────

# Distance (metres) around the defensive line anchor that defines the
# BALANCED band.  Spec: bc < anchor-5 -> AGGRESSIVE, bc > anchor+5 -> PASSIVE.
#
# TASK IV (2026-08-14): the single 5.0 m band is split into two ASYMMETRIC
# band margins so the PASSIVE->BALANCED and BALANCED->AGGRESSIVE transitions
# react EARLIER on a ball carrier approaching/leaving the line:
#
#     AGGRESSIVE_BAND_M = DEPTH_BAND_M - 0.10 = 4.9 m
#     PASSIVE_BAND_M    = DEPTH_BAND_M - 0.15 = 4.85 m
#
#   bc_x <  anchor_x - AGGRESSIVE_BAND_M  -> AGGRESSIVE  (shoots earlier)
#   bc_x >  anchor_x + PASSIVE_BAND_M     -> PASSIVE     (collapses earlier)
#   else                                   -> BALANCED
#
# Both transitions now fire 0.10-0.15 m closer to the anchor, which with the
# MIN_SWITCH_INTERVAL hysteresis yields faster defensive-line reactivity
# after a catch while keeping the 20-frame no-oscillation contract intact.
#
# DEPTH_BAND_M is retained as the LEGACY 5.0 m symmetric band (the MMDL
# hard-threshold and the verifiers read it); the live band logic below uses
# the asymmetric split only.
DEPTH_BAND_M: float = 5.0

# TASK IV (2026-08-14) — asymmetric engagement band margins (metres).
# BALANCED->AGGRESSIVE reacts at anchor - 4.9 (OLD value - 0.10).
AGGRESSIVE_BAND_M: float = 4.9

# PASSIVE->BALANCED reacts at anchor + 4.85 (OLD value - 0.15).
PASSIVE_BAND_M: float = 4.85

# Minimum number of frames between engagement-role switches.  Prevents
# repeated PASSIVE re-entry and frame-by-frame oscillation.
MIN_SWITCH_INTERVAL: int = 20

# Engagement roles (spec names).
AGGRESSIVE: str = "AGGRESSIVE"
BALANCED: str = "BALANCED"
PASSIVE: str = "PASSIVE"

# Valid engagement roles.
VALID_ENGAGEMENT_ROLES = frozenset({AGGRESSIVE, BALANCED, PASSIVE})

# Hard depth/retreat anchor offset (metres) behind the ruck.
LINE_DEPTH_OFFSET_M: float = 10.0

# ── TASK I-B ROLE EXEMPTIONS (2026-08-11) ─────────────────────────────
# Defenders who never participate in the band-switch path.  Markers and
# the dummy-half play tight to the ruck (their posture is structural, not
# BC-band-relative); tacklers are TES-owned; DEF_CHASE is a dedicated
# chase role (never a line posture); the dynamic fullback actor owns its
# own backfield anchor.  Exempt defenders keep their prior engagement
# role unchanged — they never switch, never lock, and their band role is
# stamped frame-consistently so ``classify_depth_all`` reads a stable
# value from them (the spec's "ignore exempt roles" depth-switch rule).
EXEMPT_DEFENDER_ROLES = frozenset({
    "MARKER_A",
    "MARKER_B",
    "DUMMY_HALF",
    "TACKLER_1",
    "TACKLER_2",
    "TACKLER_3",
    "DEF_CHASE",
    "KICK_CHASE",
})


def _is_engagement_exempt(d) -> bool:
    """True when *d* must never enter the engagement band-switch path."""
    if getattr(d, "fullback_role_active", False):
        return True
    # TACKLE-STATE GATE (2026-08-23): holders inside the tackle-radius
    # never switch/lock engagement bands -- the gate releases them on
    # radius exit and the next frame re-bootstraps their posture.
    if getattr(d, "tackle_state_active", False):
        return True
    role = getattr(d, "defender_role", None)
    if role is None:
        return False
    name = getattr(role, "name", None) or str(role)
    return name in EXEMPT_DEFENDER_ROLES

# World key under which per-defender engagement state is stored
# (spec: world.depth_role_state[id(d)] = {...}).
STATE_KEY: str = "depth_role_state"


# ─────────────────────────────────────────────────────────────────────
# ANCHOR AUTHORITY — single source, never BC-derived
# ─────────────────────────────────────────────────────────────────────

def defensive_line_anchor_m(world) -> float:
    """Return the authoritative defensive-line anchor X (metres).

    CONTRACT (Task A-2, verify_anchor_integrity): the defensive line anchor
    is ALWAYS ``ruck_x_m + LINE_DEPTH_OFFSET_M`` (ruck_x + 10).  It is NEVER
    recomputed from BC.x and never drifts behind BC.

    TASK G: this is an alias for the anchor-authority single source.
    Fallbacks (pre-ruck phases only): when ``world.ruck_x_m`` is None the
    world may already carry a ``defensive_line_anchor`` (kickoff/catch
    published ruck_x + 10); otherwise a verification stub default is used.
    """
    return _authority_anchor_m(world)


# ─────────────────────────────────────────────────────────────────────
# STATE ACCESS — DepthRoleState objects (Task G dot access)
# ─────────────────────────────────────────────────────────────────────

def get_state(world, defender: Player) -> DepthRoleState:
    """Return the persistent engagement DepthRoleState for *defender*.

    Creates a fresh UNLOCKED record on first access so defenders entering
    mid-phase bootstrap immediately (bootstrap = no lock, no switch history).
    """
    return _get_depth_role_state(world, defender)


def store_state(world, defender: Player, state: DepthRoleState) -> None:
    """Write *state* back onto the world store for *defender*."""
    store = getattr(world, STATE_KEY, None)
    if store is None:
        store = {}
        setattr(world, STATE_KEY, store)
    store[id(defender)] = state


# ─────────────────────────────────────────────────────────────────────
# ENGAGEMENT-ROLE COMPUTATION (spec Task A-2 step 6)
# ─────────────────────────────────────────────────────────────────────

def desired_engagement_role(bc_x: float, anchor_x: float) -> str:
    """Return the desired engagement role from the FINAL band conditions.

        bc_x < anchor_x - AGGRESSIVE_BAND_M  -> AGGRESSIVE (4.9 m, TASK IV)
        bc_x > anchor_x + PASSIVE_BAND_M     -> PASSIVE    (4.85 m, TASK IV)
        else                                 -> BALANCED

    TASK IV (2026-08-14): the single DEPTH_BAND_M (5.0) is split into two
    asymmetric margins so BALANCED->AGGRESSIVE and PASSIVE->BALANCED both
    react EARLIER on the ball carrier (AGGRESSIVE_BAND_M = 4.9, PASSIVE_BAND_M
    = 4.85).  The BALANCED band is therefore [anchor - 4.9, anchor + 4.85].
    """
    if bc_x < anchor_x - AGGRESSIVE_BAND_M:
        return AGGRESSIVE
    if bc_x > anchor_x + PASSIVE_BAND_M:
        return PASSIVE
    return BALANCED


def update_engagement_state(world, defenders) -> None:
    """Update persistent engagement roles for *defenders* (once per OPEN_PLAY frame).

    Spec Task A-2 hysteresis / lock / spam-prevention:

      1. A defender MAY NOT switch unless bc_x crosses a threshold AND
         (frame - last_switch_frame) > MIN_SWITCH_INTERVAL.
      2. ROLE-LOCK: once PASSIVE, stays PASSIVE until BC retreats behind
         anchor_x or PTB resets.
      3. SIDE-LOCK: side persists for the whole open-play phase.
      4. Spam prevention:
           - role == last_role            -> DO NOTHING (no state write)
           - role != last_role, interval  -> KEEP last_role
           - last_switch_frame updated    -> ONLY on a real switch.

    Marks each defender with ``d.role_depth_class`` (the stable band role)
    so ``classify_depth_all`` consumes a frame-consistent value.

    2026-08-15 (ITEM 6) — DEPTH-ROLE LOCK INSIDE 2.0m: in the final
    approach the engagement band must not flip (a last-second band switch
    re-scales the lateral correction and pulls the defender sideways
    exactly as they should be straight-lining into contact).  A defender
    inside COLLAPSE_LOCK_RADIUS_M of the BC keeps its current role; the
    switch bookkeeping is untouched so the hysteresis interval continues
    to elapse.
    """
    from player.collapse_controls import depth_role_locked as _depth_role_locked
    bc = getattr(world, "ball_carrier", None)
    anchor_x = defensive_line_anchor_m(world)
    frame = getattr(world, "frame_count", 0)
    ruck_y = getattr(world, "ruck_y_m", 35.0)
    bc_x = bc.x if bc is not None else None

    for d in defenders:

        # ── TASK I-B ROLE EXEMPTIONS ──
        # Exempt roles (markers, DH, tacklers, DEF_CHASE, KICK_CHASE) plus
        # the dynamic fullback never participate in the engagement
        # band-switch path.  Their prior role (or a fresh BALANCED seed) is
        # stamped frame-consistently and NEVER switched/locked, so the
        # depth pipeline sees a stable posture and no exempt role can ever
        # fire a LATE_DEPTH_SWITCH / band-oscillation event.
        if _is_engagement_exempt(d):
            if getattr(d, "role_depth_class", None) not in VALID_ENGAGEMENT_ROLES:
                d.role_depth_class = BALANCED
            continue

        state = get_state(world, d)

        # ── SIDE-LOCK ──
        # The persistent side comes from the structural depth-role system.
        side = getattr(d, "depth_side", None)
        if side in (-1, 1):
            if state.side != side:
                # First frame of a new phase: adopt the structural side.
                # After bootstrap the side never flips until PTB reset.
                state.side = side
        elif state.side not in (-1, 1):
            # Hysteresis-protected side (2026-08-29): the persistent
            # depth_side / dead-banded live-sign fallback is used — never
            # the raw d.y < ruck_y sign.  A tiny Y-jiggle at the ruck
            # line can no longer flip the engagement side-lock.
            state.side = depth_side_of(d, ruck_y)

        if bc_x is None:
            # No BC: hold the current engagement role (no re-entry possible).
            d.role_depth_class = state.role
            store_state(world, d, state)
            continue

        last_role = state.role
        desired = desired_engagement_role(bc_x, anchor_x)
        interval_met = (frame - state.last_switch_frame) > MIN_SWITCH_INTERVAL

        # ── ROLE-LOCK (spec step 4): once PASSIVE, stay PASSIVE until BC
        # retreats behind anchor_x or PTB resets.  The ONLY escape is a
        # genuine BC retreat behind the anchor, which flips the band first.
        if state.locked and last_role == PASSIVE and bc_x >= anchor_x:
            # BC still at/past the anchor: PASSIVE lock holds — DO NOTHING.
            d.role_depth_class = PASSIVE
            store_state(world, d, state)
            continue

        # ── SPAM PREVENTION ──
        if desired == last_role:
            # No change — DO NOTHING (do NOT touch last_switch_frame).
            d.role_depth_class = last_role
            store_state(world, d, state)
            continue

        if not interval_met:
            # Change requested but hysteresis interval not elapsed:
            # KEEP last role (no state write beyond the side above).
            d.role_depth_class = last_role
            store_state(world, d, state)
            continue

        # ── ITEM 6 (2026-08-15): DEPTH-ROLE LOCK INSIDE 2.0m OF THE BC ──
        # In the final approach a band switch must not fire (a last-second
        # AGGRESSIVE/BALANCED/PASSIVE flip re-scales the lateral correction
        # in apply_movement_state and can pull the defender sideways exactly
        # as they should be straight-lining into contact).  A defender inside
        # COLLAPSE_LOCK_RADIUS_M keeps its current role; the switch
        # bookkeeping is untouched so the hysteresis interval still elapses.
        if desired != last_role and _depth_role_locked(
            math.hypot(d.x - bc.x, d.y - bc.y)
        ):
            d.role_depth_class = last_role
            store_state(world, d, state)
            continue

        # ── REAL SWITCH ──
        state.role = desired
        state.last_switch_frame = frame

        # Role-lock bookkeeping: PASSIVE is locked while BC is at/past the
        # anchor (PASSIVE cannot be re-entered repeatedly); AGGRESSIVE and
        # BALANCED are always switchable after the interval elapses.
        if desired == PASSIVE and bc_x >= anchor_x:
            state.locked = True
        else:
            state.locked = False

        d.role_depth_class = desired
        store_state(world, d, state)


def reset_engagement_state(world) -> None:
    """Clear all engagement locks/state (PTB reset + kickoff).

    Called on POST_RUCK -> OPEN_PLAY and on kickoff so the next open-play
    phase re-bootstraps fresh: no stale PASSIVE lock may survive from the
    previous set into the next set's first frame.

    TASK G: also unlocks the anchor authority so the anchor recomputes
    from the NEW ruck on the next OPEN_PLAY frame (Layer 6 — reset only
    at PTB/TES/kickoff boundaries, exactly where this reset runs).
    """
    store = getattr(world, STATE_KEY, None)
    if store is not None:
        store.clear()
        if hasattr(world, STATE_KEY):
            delattr(world, STATE_KEY)
    # Clear per-defender stamps so no stale posture survives either.
    for p in getattr(world, "defending_team", []) or []:
        if hasattr(p, "role_depth_class"):
            p.role_depth_class = BALANCED
    reset_anchor_authority(world)


def audit_engagement_state(world) -> None:
    """Print an audit line for the persistent engagement state (verification)."""
    store = getattr(world, STATE_KEY, None) or {}
    print("\nENGAGEMENT STATE AUDIT")
    for d in getattr(world, "defending_team", []) or []:
        state = store.get(id(d))
        if state is None:
            continue
        # Older dict records and the new DepthRoleState objects both render.
        if isinstance(state, dict):
            role_str = state.get("role", "-")
            side_str = {-1: "L", 1: "R"}.get(state.get("side", None), "-")
            locked = "LOCKED" if state.get("locked", False) else "free"
            last_switch = state.get("last_switch_frame", "-")
        else:
            role_str = getattr(state, "role", "-")
            side_str = {-1: "L", 1: "R"}.get(getattr(state, "side", None), "-")
            locked = "LOCKED" if getattr(state, "locked", False) else "free"
            last_switch = getattr(state, "last_switch_frame", "-")
        print(
            f"ID {getattr(d, 'player_id', '?')}: "
            f"role={role_str} side={side_str} [{locked}] "
            f"last_switch={last_switch}"
        )
