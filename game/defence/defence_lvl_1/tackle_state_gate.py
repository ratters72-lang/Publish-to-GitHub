"""
tackle_state_gate.py — Radius-gated TACKLE-STATE hold/release authority.

SPEC (2026-08-23)
-----------------
"A defender stays in TACKLE-STATE for the entire TES/PTB while inside
tackle-radius, and only when they exit that radius do they re-enter
spacing and receive a depth role."

GATE GEOMETRY (confirmed 2026-08-23)
------------------------------------
  - tackle-radius = distance from the RUCK / TACKLE POINT
    (``world.ruck_x_m``, ``world.ruck_y_m``), radius
    ``TACKLE_STATE_RADIUS_M`` = 3.5 m (= OpenPlayEngine.RUCK_BUBBLE_RADIUS_M,
    which covers both markers standing at ruck_x+1 / ruck_x+2).
  - TACKLE-STATE holders = TES tacklers (TACKLER_1/2/3, stamped at
    ``_arm_tackler_role``) + the marker pair (MARKER_A/B) + non-marker
    ex-tacklers retreating from the ruck (all stamped at
    ``_assign_markers_for_new_tackle``).

CONTRACT
--------
  - While a holder remains INSIDE the radius it keeps its tackle-state
    role and is INVISIBLE to the line systems: no depth role
    (defensive_depth_roles), no spacing/width frame entry
    (defensive_pipeline), no engagement band switch
    (defensive_engagement_state), no ruck-pin slot (ruck_adjacent_pin),
    no lane-tackler selection (intercept_tacklers); the MMDL holds it.
  - The ONLY release is EXITING the radius (+ exit hysteresis). Phase
    timers (marker-freeze expiry) and boundary sweeps
    (_post_ruck_marker_cleanup / _reset_tackle_state) may NOT convert a
    holder while it stands inside the bubble.
  - PEEL: after the marker freeze expires (POST_RUCK tail / OPEN_PLAY),
    holders still inside the bubble are moved outward toward just past
    the radius so the exit actually happens.  During RELEASE /
    PTB_PREP / PTB_EXEC the marker targets (ruck+1/+2) own the holders
    by design and peel never runs.
  - ``update_tackle_state(world, dt)`` is called ONCE per
    OpenPlayEngine.update() AFTER the phase handlers (positions final)
    and BEFORE the ruck-adjacent pin; released defenders join the
    depth-role pool on the NEXT frame's preamble.

This module is the SINGLE source of the gate.  It imports nothing from
the engine or other defence modules (import-cycle law); consumers import
the predicates from here.
"""

from __future__ import annotations

import math

# ── GATE GEOMETRY ────────────────────────────────────────────────────
# Must match OpenPlayEngine.RUCK_BUBBLE_RADIUS_M (3.5).  Declared here
# (not imported) because defence modules must never import the engine;
# DefenceTuners carries the same value as ``tackle_state_radius_m``
# with the same sync comment.
TACKLE_STATE_RADIUS_M: float = 3.5

# Exit hysteresis: a holder must clear radius + this before release so a
# defender hovering on the boundary cannot flap in/out of the state.
TACKLE_STATE_EXIT_HYSTERESIS_M: float = 0.25

# Peel speed fallback when tuners are unavailable (mirrors the PTB-era
# post_catch_cap_mps ceiling family).
PEEL_SPEED_FALLBACK_MPS: float = 6.0

# Distance beyond the radius the peel aims for (clears the exit bound in
# one step so release fires on the following sweep).
PEEL_EXIT_LEAD_M: float = 0.6

# Phases in which the peel writer may move holders outward.  During
# RELEASE / PTB_PREP / PTB_EXEC the marker targets own the holders by
# design — peel must never fight them.
_PEEL_PHASES = frozenset({"POST_RUCK_OPEN_PLAY", "OPEN_PLAY"})

# Player attribute carrying the persistent hold flag.
FLAG_ATTR: str = "tackle_state_active"

# Roles that convert to DEF_LINE_READY on release.
_TACKLE_STATE_ROLE_NAMES = frozenset({
    "MARKER_A", "MARKER_B",
    "TACKLER_1", "TACKLER_2", "TACKLER_3",
})


# ─────────────────────────────────────────────────────────────────────
# RADIUS SOURCE
# ─────────────────────────────────────────────────────────────────────

def tackle_state_radius_m(world) -> float:
    """Return the live tackle-state radius (DefenceTuners override)."""
    ope = getattr(world, "open_play_engine", None)
    tuners = getattr(ope, "def_tuners", None) if ope is not None else None
    value = getattr(tuners, "tackle_state_radius_m", None)
    if value is None:
        return TACKLE_STATE_RADIUS_M
    return float(value)


# ─────────────────────────────────────────────────────────────────────
# FLAG PRIMITIVES
# ─────────────────────────────────────────────────────────────────────

def stamp_tackle_state(d) -> None:
    """Mark *d* as a TACKLE-STATE holder (idempotent)."""
    if d is not None:
        setattr(d, FLAG_ATTR, True)


def clear_tackle_state(d) -> None:
    """Clear the TACKLE-STATE hold flag on *d*."""
    if d is not None:
        setattr(d, FLAG_ATTR, False)


def is_tackle_state_flagged(d) -> bool:
    """True when *d* carries the persistent TACKLE-STATE hold flag."""
    return bool(getattr(d, FLAG_ATTR, False))


def distance_to_tackle_point(d, world):
    """Euclidean distance from *d* to the ruck/tackle point (None if unknown)."""
    ruck_x = getattr(world, "ruck_x_m", None)
    ruck_y = getattr(world, "ruck_y_m", None)
    if d is None or ruck_x is None or ruck_y is None:
        return None
    return math.hypot(float(d.x) - float(ruck_x), float(d.y) - float(ruck_y))


# ─────────────────────────────────────────────────────────────────────
# TES-DRIVE OUT-OF-RADIUS HOLD GATE (2026-09-07)
# ─────────────────────────────────────────────────────────────────────
# During the TES drive chamber (TACKLE_CONTACT / TACKLE_DRIVE /
# TACKLE_DOMINANCE) a defender BEYOND the tackle-state radius
# (R_tackle = 3.5 m) that is NOT a registered TES tackler must be treated
# as retreat/hold-only: NOT depth-locked, NOT driven by the live-BC
# tracking/spacing writers, monotone-back / stationary until PTB.  This is
# the out-of-band-motion gate — it prevents the "free-running while the
# tackle is locked" teleport read on mid/outer defenders, and it is the
# radius gate the drive writer chain must obey (Defect A/B/C).
#
# The registered-tackler exemption is REQUIRED: a TES tackler is engaged at
# the tackle point by definition, but on a synthetic geometry (and in the
# verifier) the RUCK may sit behind the BC, so the tackler's distance to the
# RUCK can exceed R_tackle while it is still a legal mover.  Tacklers are
# never held.

_TES_DRIVE_SUBSTATES = frozenset({
    "tackle_contact", "tackle_drive", "tackle_dominance",
})


def _substate_name(world) -> str:
    """Return the lower-cased sub-state (or OPE phase label) string."""
    sub = getattr(world, "sub_state", None)
    if sub is None:
        return str(getattr(world, "_ope_phase_label", "") or "").lower()
    return str(getattr(sub, "value", sub)).lower()


def is_tes_drive_substate(world) -> bool:
    """True during the TES drive chamber (CONTACT/DRIVE/DOMINANCE)."""
    return _substate_name(world) in _TES_DRIVE_SUBSTATES


def is_outside_tackle_radius(world, d) -> bool:
    """True when *d* is farther than R_tackle from the ruck/tackle point."""
    dist = distance_to_tackle_point(d, world)
    if dist is None:
        return False
    return dist > tackle_state_radius_m(world)


def is_registered_tes_tackler(world, d) -> bool:
    """True when *d* is a registered TES tackler (primary / joiner)."""
    pid = getattr(d, "player_id", None)
    if pid is None:
        return False
    if pid in (getattr(world, "tackler_ids", None) or ()):
        return True
    if pid == getattr(world, "primary_tackler_id", None):
        return True
    return pid in (getattr(world, "secondary_tacklers", None) or ())


def is_tes_drive_hold(world, d) -> bool:
    """True when *d* must be treated as retreat/hold-only this TES frame.

    A body is held when: the engine is in a TES drive sub-state AND the
    body is outside the tackle-state radius AND it is not a registered
    TES tackler.  Held bodies receive NO live-BC depth tracking, NO
    spacing/width writer, and NO depth-lock X/Y glide — only a
    monotone-back / stationary hold until PTB.
    """
    if not is_tes_drive_substate(world):
        return False
    if is_registered_tes_tackler(world, d):
        return False
    return is_outside_tackle_radius(world, d)


def is_in_tackle_state(d, world) -> bool:
    """True when *d* HOLDS TACKLE-STATE: flagged AND inside the radius.

    This is the predicate every line-system consumer gates on.  A flagged
    defender that has already exited the radius is mid-release (the next
    sweep clears it) and reads as NOT holding the state.
    """
    if not is_tackle_state_flagged(d):
        return False
    dist = distance_to_tackle_point(d, world)
    if dist is None:
        return False
    return dist <= tackle_state_radius_m(world)


# ─────────────────────────────────────────────────────────────────────
# RELEASE
# ─────────────────────────────────────────────────────────────────────

def _release_defender(d) -> None:
    """Convert a released holder back into an ordinary line defender."""
    from player.player import DefenderRole

    # FROZEN-VELOCITY GUARANTEE (2026-09-07): a released holder re-enters
    # the line pool with ZERO velocity.  The release fires AFTER the
    # freeze-compliance zeroing pass in OpenPlayEngine.update(); the
    # SLOT-INVARIANT REPAIR (run_defensive_pipeline re-run) then re-integrates
    # the freshly-released defender toward a fresh depth target.  Carrying the
    # old tackle-state velocity into that repair injects same-frame motion into
    # a body that the FINAL apply_phase_invariants will re-freeze (the
    # frozen-body-carries-velocity teleport).  Zeroing here means no writer can
    # ever inherit stale motion off a release.
    d.vx = 0.0
    d.vy = 0.0

    role = getattr(d, "defender_role", None)
    name = getattr(role, "name", None) or str(role)
    if name in _TACKLE_STATE_ROLE_NAMES:
        d.defender_role = DefenderRole.DEF_LINE_READY
    d.marker_freeze_timer = 0.0
    d.apply_physics_forces = True
    body = getattr(d, "body", None)
    if body is not None and hasattr(body, "collision_enabled"):
        body.collision_enabled = True
    # Fresh classification on the next OPEN_PLAY frame.
    d.depth_class = "PASSIVE"
    d._prev_depth_class = None
    d.tackle_role = None
    # DEPTH-ROLE INVALIDATION (2026-08-23): the released holder re-enters
    # the line pool fresh.  Its depth_role / role_depth_target_x / y were
    # stamped (or carried) from BEFORE the tackle, against the OLD ruck's
    # anchor.  Clear them so defensive_depth_roles.assign_depth_roles()
    # reseeds fresh geometry on the NEXT frame from the current ruck and
    # anchor_x.  Do NOT assign a new depth role here.
    d.depth_role = None
    d.role_depth_target_x = None
    d.role_depth_target_y = None
    clear_tackle_state(d)


# ─────────────────────────────────────────────────────────────────────
# PEEL WRITER
# ─────────────────────────────────────────────────────────────────────

def _peel_speed_mps(world) -> float:
    ope = getattr(world, "open_play_engine", None)
    tuners = getattr(ope, "def_tuners", None) if ope is not None else None
    value = getattr(tuners, "post_catch_cap_mps", None)
    if value is None:
        return PEEL_SPEED_FALLBACK_MPS
    return float(value)


def _is_frozen(d) -> bool:
    if bool(getattr(d, "frozen", False)):
        return True
    return float(getattr(d, "freeze_timer", 0.0) or 0.0) > 0.0


def _role_name(d) -> str:
    role = getattr(d, "defender_role", None)
    return getattr(role, "name", None) or str(role)


def _peel_holder(world, d, dt: float, radius: float) -> str:
    """Move an expired-freeze holder outward toward just past the radius.

    Direct integration (mirrors OPE's ``_move_toward`` retreat pattern);
    ``_mmdl_moved`` is stamped so the engine movement loop skips the
    player this frame (same contract as the pre-tackle stance writer).
    Returns "PEEL" when movement was applied, "" otherwise.
    """
    phase = str(getattr(world, "_ope_phase_label", "") or "")
    if phase not in _PEEL_PHASES:
        return ""
    # TES DRIVE STRUCTURE-HOLD EXEMPTION (2026-09-11): a body held on its
    # TES drive structure cell is owned by tes_drive_structure.py -- the
    # declared sole line writer for the chamber.  The peel integrates
    # POSITION directly (d.y += ny * move) toward the ruck-radius ring;
    # measured live it dragged held line bodies up to 1.6 m off their
    # locked lanes across an 8-tackle run (a steady ~0.4 m/s outward
    # drift, largest in the longest chambers).  The hold owns the chamber,
    # so the peel must never fight it.
    from game.defence.defence_lvl_1.tes_drive_structure import (
        is_tes_structure_hold as _is_struct_hold,
    )
    if _is_struct_hold(d):
        return ""
    if _is_frozen(d):
        return ""
    if float(getattr(d, "marker_freeze_timer", 0.0) or 0.0) > 0.0:
        return ""
    # DEF_RETREAT_10 holders in POST_RUCK are owned by the per-defender
    # retreat writer — peel only what nobody else moves.
    if phase == "POST_RUCK_OPEN_PLAY" and _role_name(d) == "DEF_RETREAT_10":
        return ""

    ruck_x = float(getattr(world, "ruck_x_m"))
    ruck_y = float(getattr(world, "ruck_y_m"))
    dx = float(d.x) - ruck_x
    dy = float(d.y) - ruck_y
    dist = max(1e-6, math.hypot(dx, dy))
    nx, ny = dx / dist, dy / dist
    target_dist = radius + PEEL_EXIT_LEAD_M
    tx = ruck_x + nx * target_dist
    ty = ruck_y + ny * target_dist

    spd = _peel_speed_mps(world)
    step = spd * dt
    remaining = max(0.0, target_dist - dist)
    move = min(step, remaining)
    d.vx = nx * spd
    d.vy = ny * spd
    d.x += nx * move
    d.y += ny * move
    d.target_x = tx
    d.target_y = ty
    d._mmdl_moved = True
    return "PEEL"


# ─────────────────────────────────────────────────────────────────────
# PER-FRAME SWEEP (sole release authority)
# ─────────────────────────────────────────────────────────────────────

def update_tackle_state(world, dt: float) -> list:
    """Audit + peel + release all TACKLE-STATE holders for this frame.

    Called once per OpenPlayEngine.update() AFTER the phase handlers and
    BEFORE the ruck-adjacent pin.  For every flagged defender:

      - dist > radius + hysteresis  -> RELEASED (flag cleared, role
        converted to DEF_LINE_READY, physics/collision restored).  The
        defender re-enters spacing and receives a depth role on the NEXT
        frame's preamble.
      - otherwise                   -> still inside the bubble: HOLD
        (or PEEL once its marker freeze has expired and the phase allows).

    Returns the list of defenders released this frame.
    """
    sub = getattr(world, "sub_state", None)
    sub_name = str(getattr(sub, "value", sub) or "").lower()
    if sub_name in {"release", "tackle_release"}:
        # RELEASE / TACKLE_RELEASE: the release-phase handlers own marker and
        # retreat movement; the tackle-state radius gate must not pin these
        # bodies in a HOLD state for the whole release window, otherwise the
        # markers sit at the ruck bubble forever and the phase never settles.
        for d in (getattr(world, "defending_team", None) or []):
            if is_tackle_state_flagged(d):
                clear_tackle_state(d)
        return []

    radius = tackle_state_radius_m(world)
    exit_bound = radius + TACKLE_STATE_EXIT_HYSTERESIS_M
    frame = getattr(world, "frame_count", 0)

    released: list = []
    audit_parts: list = []

    from game.defence.defence_lvl_1.tes_drive_structure import (
        clear_body_hold as _clear_stale_hold2,
        hold_is_live as _hold_is_live2,
        is_tes_structure_hold as _is_struct_hold2,
        reconcile_stale_holds as _reconcile_stale2,
    )
    # CHAMBER-EXIT RECONCILIATION (2026-09-12): the structure hold is a
    # CHAMBER-SCOPED state, but its nominal exit writer
    # (``clear_tes_drive_structure``) is not reliably invoked — the defence
    # manager reaches it only past an ``is_tackle_phase`` early return that
    # stays True across RELEASE/PTB, so in a live 8-tackle run it was called
    # ZERO times while ``apply_tes_drive_structure`` ran 1,455 times.  Every
    # hold therefore outlived its chamber and the NEXT tackle, where the
    # exemption below skipped the body BEFORE its radius release test:
    # ``tackle_state_active`` could never clear, ``_line_defenders`` dropped
    # the body from every later rebuild, and the rebuild pool collapsed
    # 12 -> 2 across eight chambers while the slot table still owned 11.
    # ``update_tackle_state`` is the documented once-per-frame hook, so the
    # boundary is enforced HERE, off the engine-stamped chamber seq.
    _reconcile_stale2(world)

    for d in (getattr(world, "defending_team", None) or []):
        if not is_tackle_state_flagged(d):
            continue
        # TES DRIVE STRUCTURE-HOLD EXEMPTION (2026-09-11): a held structure
        # body keeps TACKLE-STATE across the chamber, but its movement is
        # owned by tes_drive_structure.py.  Skip it entirely so neither the
        # release conversion nor the peel can move it mid-chamber.
        #
        # CHAMBER-SCOPED (2026-09-12): the exemption is valid ONLY while the
        # hold is LIVE.  A stale stamp (chamber seq advanced, or the engine
        # already outside the drive) must NOT exempt the body — that is the
        # permanent-exemption collapse this gate caused.  The reconciliation
        # above normally clears it first; this test keeps the exemption
        # correct even if a caller reaches the sweep without reconciling.
        if _is_struct_hold2(d) and _hold_is_live2(world, d):
            continue
        if _is_struct_hold2(d):
            # Stale hold: drop it and fall through to the ordinary radius
            # release test so the body rejoins the rebuild population.
            _clear_stale_hold2(d)
        dist = distance_to_tackle_point(d, world)
        if dist is None:
            continue

        if dist > exit_bound:
            _release_defender(d)
            released.append(d)
            audit_parts.append(
                f"id={getattr(d, 'player_id', '?')} "
                f"role={_role_name(d)} d={dist:.2f}m RELEASED"
            )
            continue

        status = _peel_holder(world, d, dt, radius) or "HOLD"
        audit_parts.append(
            f"id={getattr(d, 'player_id', '?')} "
            f"role={_role_name(d)} d={dist:.2f}m {status}"
        )

    if audit_parts:
        sub = str(getattr(world, "sub_state", "?")).split(".")[-1]
        print(
            f"[TACKLE-GATE] f={frame} sub={sub} "
            f"radius={radius:.2f} | " + " | ".join(audit_parts)
        )
    return released


# ─────────────────────────────────────────────────────────────────────
# RESET (kickoff / fresh sequence)
# ─────────────────────────────────────────────────────────────────────

def reset_tackle_state(world) -> None:
    """Clear every TACKLE-STATE hold flag (kickoff reset path).

    Players are normally recreated at kickoff; this sweeps the surviving
    engine/world objects so no stale flag can leak into a new sequence.
    """
    for p in (getattr(world, "players", None) or []):
        setattr(p, FLAG_ATTR, False)
