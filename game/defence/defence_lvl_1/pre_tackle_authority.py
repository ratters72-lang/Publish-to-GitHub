"""pre_tackle_authority.py — UNIFIED PRE-TACKLE AUTHORITY (2026-09-04).

PURPOSE
-------
This module consolidates the two historical pre-contact authorities into
ONE deterministic pre-tackle state publisher:

  - the LIVE LANE-TACKLER system (``intercept_tacklers.py``): per-side
    PRIMARY lane-tackler selection around the ball carrier, live BC-relative
    commit targets, and the close-range planted stance, and
  - the FROZEN-ANCHOR PRETACKLE_ATTEMPT module (``pretackle_attempt.py``):
    deterministic COMMIT/PLANTED/SLOW/HOLD states, frozen anchors, and
    one-way freeze/slow contracts.

The engine law after this consolidation: there is ONE pre-contact authority
that owns the DEF_LINE_READY line bodies across OPEN_PLAY -> TACKLE_INIT ->
TACKLE_DRIVE -> TACKLE_DOMINANCE, and it publishes ONE per-defender state
(``d.pre_tackle_state``) that BOTH movement (MMDL / Player.update) and TES
(``try_pre_tackle``) read.  The mirrored legacy flags
(``tackle_role`` / ``pre_tackle_stance`` / ``_tackle_commit_active``) are
kept as DERIVED read-only shims so existing verifiers and audits do not
churn; the single state is the source of truth.

CONTRACTS
---------
  - Writes ONLY ``target_x/target_y/vx/vy/_mmdl_moved/movement_state`` for
    bodies it manages, stamped ``target_source = "pre_tackle"``.
  - NEVER mutations defender_role / depth_role (roles stay stable).
  - Determinism: identical inputs -> identical anchors, states, owners,
    velocities, positions.  All selection uses min() with player_id
    tiebreaks over caller-provided list order; all targets are either
    live-BC (OPEN_PLAY commit) or frozen values captured at seed time
    (drive mode).
  - Reset law: state lives only inside a single contact/drive.  Cleared
    when the phase leaves DRIVE/DOMINANCE (DefenceManager exit hook), at
    kickoff (OpenPlayEngine.reset_for_kickoff), and defensively whenever a
    managed body stops being eligible (became a TES participant / lost its
    role).
"""

from __future__ import annotations

import math
import os
from enum import Enum

from player.movement_states import MovementState
# ── RETREAT-OBLIGATION VETO (2026-09-17) ─────────────────────────────
# THE 10 m retreat contract.  The pre-tackle authority is one of the five
# tackle-authority grants: it owns the COMMIT/PLANTED state that both the
# MMDL and the engage machine read.  A non-marker defender that has not
# completed its retreat must not be owned by it.  ``retreat_obligation``
# is a leaf (module-scope imports: ``player.player`` only), so this cannot
# create a cycle even though it lazily imports this module inside
# ``strip_tackle_authority``.
from game.defence.defence_lvl_1.retreat_obligation import (
    is_retreat_obligated,
)

# ── PRE-TACKLE CORRIDOR (2026-09-15) ─────────────────────────────────
# The corridor is the consolidation seam: it owns committed tacklers from
# the slowdown band down to the tackle arm, and it stamps the contact
# contract TES inherits at TACKLE_INIT.  That module has no engine imports,
# so this import cannot create a cycle.
from game.defence.defence_lvl_1.pretackle_corridor import (
    corridor_radius_m,
    is_corridor_owned,
    stamp_handoff,
)

# ── FIELD BOUND ON EVERY AUTHORED TARGET (2026-09-25) ────────────────
# The COMMIT target is `bc.x -/+ pretackle_commit_depth_offset_m` and the
# hold target is the seeded anchor.  NEITHER was field-bounded, so a ball
# carrier at/past FIELD_MAX_X made this authority command a point OUTSIDE
# the field (measured: target_x == 121.0 against a 120.0 bound).  Player's
# position clamp forbids x > FIELD_MAX_X, so the commanded move was
# unreachable and the body sat pinned at the wall with vx == vy == 0 while
# the authority kept commanding a forward approach -- the "stationary
# OPEN_PLAY body holding a live target" defect (13 of the 14 stalled
# body-frames the probe recorded were stamped target_source="pre_tackle").
# `target_field_bound` is a pure leaf (player bounds only), so this cannot
# create an import cycle, and it is the identity for an in-field point.
from game.defence.defence_lvl_1.target_field_bound import (
    clamp_target_to_field as _clamp_target_to_field,
)

# ── TARGET-SOURCE STAMP ──────────────────────────────────────────────
PRE_TACKLE_TARGET_SOURCE: str = "pre_tackle"

# ─────────────────────────────────────────────────────────────────────
# FULLBACK CHASE EXEMPTION (2026-09-29)
# ─────────────────────────────────────────────────────────────────────
# THE DEFECT THIS REMOVES ("once the carrier passes the fullback the
# pursuing body stalls or dramatically reduces pursuit instead of turning
# and continuing the chase").
#
# THIS MODULE'S OWN CONTRACT ALREADY EXCLUDES THE FULLBACK.
# ``is_pre_tackle_eligible`` documents its scope as "STRICTLY non-tackler
# line bodies: defender_role == DEF_LINE_READY, and unfrozen, and NOT THE
# DYNAMIC FULLBACK ACTOR (geometry-driven only)" -- but the exclusion was
# never implemented, and the code tested only the ROLE.  The fullback actor
# KEEPS ``defender_role = DEF_LINE_READY`` by design (that is how it stays a
# legal mover -- see ``fullback_role``'s module docstring), so the check
# passed for it and the last defender WAS owned by this authority.
#
# MEASURED (probe_fb_chase_write_sequence.py, seed 20260827, frames 800-812;
# and probe_fb_carrier_crossing.py, same seed, crossing at f=832):
#
#   f=803  move_speed=9.50   v=(-7.68, 3.16)  the chase, closing at 8.3 m/s
#   f=804  move_speed=1.50   v=( 1.50, 0.11)  the pace collapses; the body
#                                             TURNS and heads +X, away from
#                                             the carrier it was closing on
#   f=805  move_speed=2.10   f=806  2.34   f=807  2.44   f=808  2.47 ...
#
# The ramp is the fingerprint of ``_slow_step``, whose speed is
# ``min(cap, dist * 3.0)`` with cap = ``pretackle_slow_speed_mps`` (2.5):
# dist 0.50 -> 1.50, 0.70 -> 2.10, 0.78 -> 2.34, 0.83 -> 2.50, then pinned
# at the 2.5 cap.  The +X heading is the SLOW contract's own anchor: the
# body is seeded from its own ``target_x`` (the deep sweeper anchor, ~58.9)
# and walks home to it at the line-hold pace.
#
# WHY IT LATCHES (the whole defect, not just a slow frame): the fullback is
# admitted the moment its chase brings it inside the ~2.0 m promote radius
# of the carrier.  2.5 m/s can never close a carrier running at ~7 m/s, and
# because the carrier is closing on the body the SEPARATION stays inside the
# activation radius -- so the SLOW ownership is never released, the body
# never re-acquires its governed chase pace, and the carrier runs past it.
# The crossing the report was filed against (f=832) happens 28 frames AFTER
# the pace collapsed: the crossing is the SYMPTOM, the pre-tackle SLOW
# ownership is the CAUSE.
#
# THE RULE: the two halves of a pursuer's locomotion contract -- the target
# and the PACE -- must not be owned by two different authorities.  A tagged
# fullback with a live line-break chase armed is the geometry-driven
# emergency chaser; it is NOT a line body being folded into a pre-tackle
# slot, and the pre-tackle SLOW/HOLD pace must not fight its chase.  The
# exemption applies ONLY while a chase is armed: a fullback that is not
# chasing is eligible exactly as before, so the line-hold / commit contracts
# for every other situation (and every other body) are untouched.
#
# A-B / rollback lever: RLM_FB_PRETACKLE_CHASE_EXEMPT=0 restores the
# historical ownership (the fullback admitted to SLOW/HOLD on a chase) with
# no code revert, the same pattern as every other runtime lever.
FB_PRETACKLE_CHASE_EXEMPT_ENV_VAR: str = "RLM_FB_PRETACKLE_CHASE_EXEMPT"
FB_PRETACKLE_CHASE_EXEMPT: bool = (
    os.environ.get(FB_PRETACKLE_CHASE_EXEMPT_ENV_VAR, "1").strip().lower()
    not in ("0", "false", "no", "off")
)

# The fullback break latch attribute, read duck-typed (no import, so this
# module keeps its leaf status and cannot create an import cycle).
FB_BREAK_LATCH_ATTR: str = "_fb_break_latched"

# The movement-state name that marks a body as chasing.
_CHASE_STATE_NAME: str = "CHASE"


def is_fullback_chase_in_progress(d) -> bool:
    """True when *d* is the tagged fullback with a live break chase armed.

    Duck-typed on purpose: ``fullback_role``'s latch / state stamp is the
    signal, read without importing that module, so this leaf module gains no
    new dependency.  A world / stub body without the tag is never exempt.
    """
    if not FB_PRETACKLE_CHASE_EXEMPT:
        return False
    if not bool(getattr(d, "fullback_role_active", False)):
        return False
    if bool(getattr(d, FB_BREAK_LATCH_ATTR, False)):
        return True
    state = getattr(d, "movement_state", None)
    name = getattr(state, "name", None) or str(state)
    return name == _CHASE_STATE_NAME

# ── SINGLE PRE-TACKLE STATE ──────────────────────────────────────────
class PreTackleState(Enum):
    """The single pre-contact state for a managed DEF_LINE_READY body.

    NONE     — not owned by the pre-tackle authority this frame.
    COMMIT   — the (<=1 per side) designated attempt body; closing on the
               BC-forward contact point and will plant on arrival.
    PLANTED  — arrived within plant radius: one-way freeze + square-up.
    SLOW     — a non-commit body converging to its frozen anchor at capped
               slow speed (anti-collapse / anti-stack).
    HOLD     — arrived within freeze radius: one-way freeze.
    """
    NONE = "NONE"
    COMMIT = "COMMIT"
    PLANTED = "PLANTED"
    SLOW = "SLOW"
    HOLD = "HOLD"


# ── PER-DEFENDER ATTRIBUTE NAMES ─────────────────────────────────────
STATE_ATTR: str = "pre_tackle_state"
ATTEMPT_STATE_ATTR: str = "_pretackle_attempt_state"   # legacy shim
ANCHOR_X_ATTR: str = "_pta_anchor_x"
ANCHOR_Y_ATTR: str = "_pta_anchor_y"
COMMIT_X_ATTR: str = "_pta_commit_x"
COMMIT_Y_ATTR: str = "_pta_commit_y"
OWNER_SIDE_ATTR: str = "_pta_owner_side"

# World key holding {side: player_id} for commit-owner incumbency.
OWNERS_KEY: str = "pre_tackle_owners"

# Shims for backwards compatibility with existing consumers.
_TACKLE_ROLE_COMMIT = "PRIMARY"
_TACKLE_ROLE_LINE = "LINE"


# ─────────────────────────────────────────────────────────────────────
# GATE + ELIGIBILITY PREDICATES
# ─────────────────────────────────────────────────────────────────────

def pre_tackle_enabled(tuners) -> bool:
    """True when the unified pre-tackle authority master gate is ON."""
    if tuners is None:
        return False
    return bool(getattr(tuners, "pretackle_attempt_enabled", False))


def resolve_pre_tackle_phase(world) -> str:
    """Return the canonical phase-profile key for *world*'s sub-state.

    PHASE-GATED BEHAVIOURAL TUNING (Stage 5, 2026-09-04): the unified
    pre-tackle authority resolves the current game sub-state ONCE per
    frame and loads that phase profile's behavioural tuner overrides
    (speeds / radii / offsets / slowdown) instead of the global scalars.
    Every phase profile defaults to the scalar values, so behaviour is
    byte-identical until a phase profile is explicitly set.

    Map (GameSubState -> canonical phase key):
        OPEN_PLAY            -> "OPEN_PLAY"
        TACKLE_INIT          -> "TACKLE_INIT"
        TACKLE_CONTACT       -> "TACKLE_CONTACT"
        TACKLE_DRIVE         -> "TACKLE_DRIVE"
        TACKLE_DOMINANCE     -> "TACKLE_DOMINANCE"
        TACKLE_RELEASE       -> "TACKLE_RELEASE"
        PTB_READY / PTB      -> "PTB"
        POST_RUCK_OPEN_PLAY  -> "POST_RUCK_OPEN_PLAY"
        POST_PTB_RESET       -> "POST_RUCK_OPEN_PLAY"
        RESET_LINE           -> "POST_RUCK_OPEN_PLAY"

    Unknown / stub worlds (no ``sub_state``) return "DEFAULT".
    """
    sub = getattr(world, "sub_state", None)
    name = None
    if sub is not None:
        if hasattr(sub, "name"):
            name = sub.name
        else:
            name = str(sub)
    if name is None:
        return "DEFAULT"
    name = name.upper()
    mapping = {
        "OPEN_PLAY": "OPEN_PLAY",
        "TACKLE_INIT": "TACKLE_INIT",
        "TACKLE_CONTACT": "TACKLE_CONTACT",
        "TACKLE_DRIVE": "TACKLE_DRIVE",
        "TACKLE_DOMINANCE": "TACKLE_DOMINANCE",
        "TACKLE_RELEASE": "TACKLE_RELEASE",
        "PTB_READY": "PTB",
        "PTB": "PTB",
        "POST_RUCK_OPEN_PLAY": "POST_RUCK_OPEN_PLAY",
        "POST_PTB_RESET": "POST_RUCK_OPEN_PLAY",
        "RESET_LINE": "POST_RUCK_OPEN_PLAY",
    }
    return mapping.get(name, "DEFAULT")


def is_pre_tackle_eligible(d) -> bool:
    """True when *d* may be owned by the pre-tackle authority.

    Scope is STRICTLY non-tackler line bodies:
      - defender_role == DEF_LINE_READY (the task's exact scope), and
      - unfrozen, and
      - not the dynamic fullback actor (geometry-driven only).
    Markers / DH / tacklers are excluded by the role check itself; the
    caller additionally excludes TES participants before invoking apply().

    STAGE 8 CORRECTION (2026-09-05): no frozen-drive relaxation — the
    defensive line is not drive-frozen, so a frozen body is never eligible
    for the pre-tackle authority.
    """
    if d is None:
        return False
    if bool(getattr(d, "frozen", False)):
        return False
    if float(getattr(d, "freeze_timer", 0.0) or 0.0) > 0.0:
        return False
    # ── FULLBACK CHASE EXEMPTION (2026-09-29) ──
    # The docstring's third clause ("not the dynamic fullback actor") is
    # now ENFORCED, scoped to a live chase: a pursuing fullback is the
    # geometry-driven emergency chaser and must not be folded into the
    # SLOW/HOLD line-hold pace, which cannot close a carrier and therefore
    # latches (measured -- see the constant block above).
    if is_fullback_chase_in_progress(d):
        return False
    from player.player import DefenderRole
    return getattr(d, "defender_role", None) is DefenderRole.DEF_LINE_READY


def assign_def_line_ready(d) -> None:
    """Promote *d* to ``DEF_LINE_READY`` if it is an eligible line body.

    The defensive line must be ``DEF_LINE_READY`` BEFORE a tackle cycle
    enters ``TACKLE_INIT`` — otherwise a body carrying a stale
    ``DEF_RETREAT_10`` / ``DEF_CHASE`` / ``None`` role reaches the drive
    without the role, ``is_pre_tackle_eligible`` returns False, and the
    pre-tackle authority never owns it.

    Idempotent.  Promotes ``None`` / ``DEF_CHASE`` to ``DEF_LINE_READY``.
    DELIBERATELY DOES NOT PROMOTE ``DEF_RETREAT_10`` (2026-09-18): that role
    IS the engine's retreat-completion signal (``_update_post_ruck_open_play``
    flips it to ``DEF_LINE_READY`` once ``d.x >= target_x``), and this
    function runs on EVERY defender EVERY frame BEFORE the authority
    writers, so promoting it here destroyed the signal before any retreat
    veto could observe it -- every veto saw ``DEF_LINE_READY`` and a
    role-based retreat gate could never fire.  NEVER touches ``MARKER_A`` /
    ``MARKER_B`` /
    ``DUMMY_HALF`` / ``TACKLER_*`` (TES-owned), the dynamic fullback
    actor, or a frozen body.  The only write is the role stamp; it is a
    pure role-promotion, not a movement writer.
    """
    if d is None:
        return
    if bool(getattr(d, "frozen", False)):
        return
    if float(getattr(d, "freeze_timer", 0.0) or 0.0) > 0.0:
        return
    from player.player import DefenderRole
    role = getattr(d, "defender_role", None)
    if role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B,
                DefenderRole.DUMMY_HALF, DefenderRole.TACKLER_1,
                DefenderRole.TACKLER_2, DefenderRole.TACKLER_3):
        return
    # THE RETREAT ROLE SURVIVES (2026-09-18).  ``DEF_RETREAT_10`` is the
    # engine's own "10 m retreat not yet complete" state; the sole writer of
    # its completion is the engine's arrival test (``d.x >= target_x``).  If
    # this promotion converted it, the state would be gone before every
    # authority veto below and the retreat obligation could never gate.
    if role is DefenderRole.DEF_RETREAT_10:
        return
    if role is not DefenderRole.DEF_LINE_READY:
        d.defender_role = DefenderRole.DEF_LINE_READY


def is_pre_tackle_managed(d) -> bool:
    """True when *d* carries a live pre-tackle state (canonical)."""
    return getattr(d, STATE_ATTR, None) not in (None, PreTackleState.NONE)


def is_committed(d) -> bool:
    """True when *d* is a COMMIT/PLANTED pre-tackle body (tackle-eligible)."""
    return getattr(d, STATE_ATTR, None) in (PreTackleState.COMMIT,
                                            PreTackleState.PLANTED)


def is_planted(d) -> bool:
    """True when *d* has arrived at its commit point (one-way freeze)."""
    return getattr(d, STATE_ATTR, None) is PreTackleState.PLANTED


def is_line_body(d) -> bool:
    """True when *d* is a SLOW/HOLD pre-tackle line body."""
    return getattr(d, STATE_ATTR, None) in (PreTackleState.SLOW,
                                            PreTackleState.HOLD)


def clear_pre_tackle_state(d) -> None:
    """Drop every pre-tackle stamp from *d* (drive exit / eligibility loss)."""
    if d is None:
        return
    # CORRIDOR RELEASE (2026-09-15): a body dropping its pre-tackle state is
    # no longer authority-owned, so its corridor latch and membership
    # counter must go with it -- otherwise a released body would keep
    # claiming the corridor from the yielding writers for one more frame.
    from game.defence.defence_lvl_1.pretackle_corridor import (
        clear_corridor as _clear_corridor,
    )
    _clear_corridor(d)
    for attr in (STATE_ATTR, ATTEMPT_STATE_ATTR, ANCHOR_X_ATTR, ANCHOR_Y_ATTR,
                 COMMIT_X_ATTR, COMMIT_Y_ATTR, OWNER_SIDE_ATTR):
        try:
            delattr(d, attr)
        except AttributeError:
            pass
    # Legacy shims cleared too.
    for attr in ("tackle_role", "pre_tackle_stance", "footplanting",
                 "_tackle_commit_active"):
        try:
            setattr(d, attr, None if attr == "tackle_role" else False)
        except Exception:
            pass


def reset_pre_tackle(world) -> None:
    """Clear ALL pre-tackle state (kickoff reset / fresh sequence)."""
    seen = set()
    for p in (getattr(world, "players", None) or []):
        pid = id(p)
        if pid in seen:
            continue
        seen.add(pid)
        clear_pre_tackle_state(p)
    owners = getattr(world, OWNERS_KEY, None)
    if owners is not None:
        owners.clear()


def publish_pre_tackle_state(world, defenders, bc, tuners) -> list:
    """Publish the canonical ``pre_tackle_state`` from the OPEN_PLAY path.

    The OPEN_PLAY dispatch still uses the live lane-tackler writers
    (``select_lane_tacklers`` / ``_apply_tackler_commit`` /
    ``apply_pre_tackle_stance``) which stamp the LEGACY flags
    (``tackle_role`` / ``pre_tackle_stance`` / ``_tackle_commit_active``).
    This helper maps those flags onto the SINGLE canonical state so both
    movement (MMDL / Player.update) and TES (``try_pre_tackle``) read ONE
    pre-contact state.  The authority is the single state vocabulary; the
    legacy flags remain as derived read-only shims.

    Logic:
      - ``pre_tackle_stance``            -> PLANTED
      - ``_tackle_commit_active`` PRIMARY -> COMMIT
      - ``tackle_role == LINE``           -> HOLD (frozen) / SLOW
      - otherwise                         -> NONE

    Returns the list of defenders carrying a non-NONE state this frame.
    """
    if not pre_tackle_enabled(tuners) or bc is None:
        return []
    # ── STAGE 6: TACKLE-RADIUS ACTIVATION GATE (2026-09-04) ──
    # OPEN_PLAY only: a DEF_LINE_READY body is NOT a pre-tackle body until
    # it has actually breached the pre-tackle trigger radius (default
    # 2.0 m -- the distance at which try_pre_tackle evaluates a real
    # tackle).  Far bodies keep state NONE so the OPEN_PLAY pipeline (MMDL)
    # continues to own their movement -- no early-freeze / wrong-side
    # commit / premature slowing that pushed the line into TES with the
    # wrong stance and lateral lane.
    # ── TWO-RADIUS ACTIVATION (2026-09-15) ──
    # HOLD radius for an already-owned / corridor body, PROMOTE radius for a
    # body that has not yet been owned.  The single 2.0 m gate was the
    # "tackle transitions before the slowdown curve has time to act" defect:
    # the authority's own band is 4.0 m but it was only allowed to own the
    # innermost 2.0 m, while the tackle arms at 1.5 m.  A designated owner
    # now stays owned across the whole corridor; a body that has NOT been
    # designated is still held to the conservative promote radius so no far
    # body is ever prematurely slowed.
    activation_r_promote = float(
        getattr(tuners, "pre_tackle_trigger_radius_m", 2.0)
    )
    activation_r_hold = corridor_radius_m(tuners)
    managed: list = []
    for d in defenders:
        if d is None:
            continue
        _dist = math.hypot(float(d.x) - float(bc.x),
                           float(d.y) - float(bc.y))
        _activation_r = (
            activation_r_hold if is_corridor_owned(d) else activation_r_promote
        )
        if _dist > _activation_r:
            d.pre_tackle_state = PreTackleState.NONE
            continue
        # ── STAGE 7: ROLE-GATED AUTHORITY (2026-09-05) ──
        # Only real DEF_LINE_READY bodies may be owned by the pre-tackle
        # authority.  Markers / retreaters / DEF_CHASE / DUMMY_HALF / fullback
        # actors / frozen bodies that carry stale legacy flags
        # (tackle_role LINE/PRIMARY, pre_tackle_stance,
        # _tackle_commit_active) are dropped to NONE with their shims
        # cleared so they never receive SLOW/HOLD/COMMIT and cannot feed a
        # corrupted stance into TES.
        if not is_pre_tackle_eligible(d):
            d.pre_tackle_state = PreTackleState.NONE
            d.tackle_role = None
            d._tackle_commit_active = False
            d.pre_tackle_stance = False
            d.footplanting = False
            continue
        # ── RETREAT-OBLIGATION VETO (2026-09-17) ──
        # A defender still short of its 10 m retreat line is not a
        # pre-tackle body: it is published NONE with its legacy shims
        # cleared, so neither movement (MMDL / Player.update) nor TES
        # (`try_pre_tackle`) can read a COMMIT/HOLD stance off it.
        if is_retreat_obligated(d, world):
            d.pre_tackle_state = PreTackleState.NONE
            d.tackle_role = None
            d._tackle_commit_active = False
            d.pre_tackle_stance = False
            d.footplanting = False
            continue
        st = None
        if bool(getattr(d, "pre_tackle_stance", False)):
            st = PreTackleState.PLANTED
        elif bool(getattr(d, "_tackle_commit_active", False)) and \
                getattr(d, "tackle_role", None) == "PRIMARY":
            st = PreTackleState.COMMIT
        elif getattr(d, "tackle_role", None) == "LINE":
            _fz = float(getattr(d, "freeze_timer", 0.0) or 0.0)
            st = PreTackleState.HOLD if _fz > 0.0 else PreTackleState.SLOW
        if st is not None:
            _sync_shims(d, st)
            managed.append(d)
        else:
            d.pre_tackle_state = PreTackleState.NONE
    return managed


def _sync_shims(d, state: PreTackleState) -> None:
    """Derive the legacy readability flags from the canonical state.

    These are OUTPUT-ONLY shims consumed by verifiers / audits / the old
    ``try_pre_tackle`` fast paths.  The canonical ``d.pre_tackle_state`` is
    the source of truth; nothing may WRITE to these flags.
    """
    d.pre_tackle_state = state
    d._pretackle_attempt_state = state.value if state is not PreTackleState.NONE else None
    if state in (PreTackleState.COMMIT, PreTackleState.PLANTED):
        d.tackle_role = _TACKLE_ROLE_COMMIT
        d._tackle_commit_active = True
    elif state in (PreTackleState.SLOW, PreTackleState.HOLD):
        d.tackle_role = _TACKLE_ROLE_LINE
        d._tackle_commit_active = False
    else:
        d.tackle_role = None
        d._tackle_commit_active = False
    d.pre_tackle_stance = state is PreTackleState.PLANTED


# ─────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────

def _finite(v) -> bool:
    return isinstance(v, (int, float)) and math.isfinite(float(v))


def _phase_tuners(tuners, phase_key: str) -> dict:
    """Resolve the phase-gated behavioural tuner values for *phase_key*.

    PHASE-GATED BEHAVIOURAL TUNING (Stage 5, 2026-09-04): reads the
    per-phase profile from ``tuners.pretackle_phase_tuners`` (a dict keyed
    by phase key, each entry a dict of tuner overrides) and falls back to
    the global scalar tuner for every key not overridden.  With no phase
    profile configured the resolved values are byte-identical to the
    pre-Stage-5 scalars, so behaviour is unchanged until a phase profile is
    explicitly set.

    Returns a dict keyed by the canonical tuner attribute name.
    """
    profile = {}
    if tuners is not None:
        table = getattr(tuners, "pretackle_phase_tuners", None) or {}
        profile = table.get(phase_key, {}) or {}

    def _val(name: str, default: float) -> float:
        if tuners is None:
            return default
        return float(profile.get(name, getattr(tuners, name, default)))

    return {
        "pretackle_slow_speed_mps": _val("pretackle_slow_speed_mps", 2.5),
        "pretackle_freeze_radius_m": _val("pretackle_freeze_radius_m", 0.15),
        "pretackle_follow_smooth": _val("pretackle_follow_smooth", 0.6),
        "pretackle_commit_speed_mps": _val("pretackle_commit_speed_mps", 8.0),
        "pretackle_plant_radius_m": _val("pretackle_plant_radius_m", 0.8),
        "pretackle_slowdown_band_m": _val("pretackle_slowdown_band_m", 2.0),
        "pretackle_slowdown_scale": _val("pretackle_slowdown_scale", 0.1),
        "pretackle_commit_depth_offset_m": _val("pretackle_commit_depth_offset_m", 1.0),
        "pretackle_commit_lateral_offset_m": _val("pretackle_commit_lateral_offset_m", 1.0),
        # FRONT-HOLD / FLAT-FOOTED CORRIDOR DEFENCE (2026-10-03).  Defaults
        # mirror DefenceTuners so a detached verifier stub keeps the
        # documented behaviour.  Gate values are read as floats (> 0 == on).
        "pretackle_front_hold_enabled": _val("pretackle_front_hold_enabled", 1.0),
        "pretackle_front_offset_m": _val("pretackle_front_offset_m", 0.6),
        "pretackle_front_lateral_offset_m": _val("pretackle_front_lateral_offset_m", 0.3),
        "pretackle_headon_plant_enabled": _val("pretackle_headon_plant_enabled", 1.0),
        "pretackle_headon_commit_speed_mps": _val("pretackle_headon_commit_speed_mps", 2.0),
        "pretackle_headon_plant_radius_m": _val("pretackle_headon_plant_radius_m", 1.8),
    }


def _freeze_stance(d, face_x=None, face_y=None, plant: bool = False) -> None:
    """One-way freeze: zero velocity, pinned targets, MMDL-skip stamp.

    ``plant=True`` marks a genuine PRE-TACKLE PLANT (the body has arrived at
    its commit point and is squared up).  The plant always declares
    ``movement_state = HOLD`` and, when ``plant``, also stamps
    ``pre_tackle_stance`` / ``footplanting``.  ``movement_state = HOLD`` is
    load-bearing: ``pretackle_machine.is_real_planted_posture`` requires
    ``movement_state is HOLD`` + ``_mmdl_moved`` + near-zero velocity, so
    without it a planted corridor body would present as CLOSING and
    SQUARE_UP_ENGAGE could never fire.  ``plant=False`` (the SLOW/HOLD
    fold-back) stamps the same freeze but leaves the plant flags alone: a
    HOLD body is a line hold, not a contact plant.
    """
    d.vx = 0.0
    d.vy = 0.0
    d.target_x = d.x
    d.target_y = d.y
    d.move_speed = 0.0
    d._mmdl_moved = True
    d.movement_state = MovementState.HOLD
    d.target_source = PRE_TACKLE_TARGET_SOURCE
    if plant:
        d.pre_tackle_stance = True
        d.footplanting = True
    if face_x is not None and face_y is not None:
        dx = float(face_x) - float(d.x)
        dy = float(face_y) - float(d.y)
        if math.hypot(dx, dy) > 1e-6:
            d.set_facing(math.atan2(dy, dx))


def _lock_depth(d, face_x=None, face_y=None) -> None:
    """TES DEPTH-LOCK (2026-09-06): pin DEPTH (X), hold or free LATERAL (Y).

    Unlike ``_freeze_stance`` (which pins BOTH axes), this locks only the
    DEPTH axis (X — toward/away from the ruck) so a line body can still
    slide LATERALLY (Y — across the field) to its lane during TES.

    DRIVE-DEPTH TRACK (2026-09-06): during the TES drive the DEPTH (X) is
    refreshed each frame to the LIVE BC-tracked value (bc.x +
    WIDTH_OFFSETS[depth_role], stamped as ``role_depth_target_x``) so the
    line moves back with the driven carrier.  When ``depth_lock_y`` is set
    (not None) — the drive-lateral hold used by DEF_LINE_READY/DEF_CHASE —
    the LATERAL (Y) target is pinned to ``depth_lock_y`` so the body holds
    the Y it had at drive entry (no lateral blow-out).  When None
    (DEF_RETREAT_10 — not managed by this authority) Y stays free.
    ``_mmdl_moved`` stays False so Player.update() runs and integrates the
    lateral axis; the X clamp inside Player.update() pins the depth.
    """
    # DEPTH-PIN (2026-09-09): this authority ONLY manages DEF_LINE_READY /
    # DEF_CHASE, so the depth pin is ALWAYS static at drive entry — NEVER
    # refreshed to the live BC.  That kills the line's depth movement during
    # TACKLE_DOMINANCE (the defensive-line jitter fix).  DEF_RETREAT_10 (not
    # managed here) keeps its own live-BC track via _refresh_depth_lock_x.
    if not getattr(d, "depth_locked", False):
        d.depth_locked = True
        d.depth_lock_x = float(d.x)
    if getattr(d, "depth_lock_y", None) is None:
        d.depth_lock_y = float(d.y)
    d.vx = 0.0
    _dly = getattr(d, "depth_lock_y", None)
    # FIELD BOUND (2026-09-25): both the locked depth and the hold lane are
    # AUTHORED targets, so both must be reachable.  An unreachable depth pin
    # leaves the body pressed against the boundary wall.
    d.target_x, _locked_y = _clamp_target_to_field(
        float(getattr(d, "depth_lock_x", d.x)),
        float(_dly) if _dly is not None else float(d.y),
    )
    d._mmdl_moved = False
    d.target_source = PRE_TACKLE_TARGET_SOURCE
    if _dly is not None:
        d.vy = 0.0
        d.target_y = _locked_y
    if face_x is not None and face_y is not None:
        dx = float(face_x) - float(d.x)
        dy = float(face_y) - float(d.y)
        if math.hypot(dx, dy) > 1e-6:
            d.set_facing(math.atan2(dy, dx))


def _slow_step(d, tx: float, ty: float, dt: float, cap_mps: float,
               smooth: float) -> None:
    """Slow-contract step toward (tx, ty): proportional + cap + EMA."""
    # FIELD BOUND (2026-09-25): a commanded point outside the field is
    # UNREACHABLE -- Player's position clamp cancels the step every frame and
    # the body sits pinned against the boundary with zero speed.  Bound the
    # point once, before the step and the stamped target both derive from it.
    tx, ty = _clamp_target_to_field(tx, ty)
    dx = tx - float(d.x)
    dy = ty - float(d.y)
    dist = max(1e-6, math.hypot(dx, dy))
    nx, ny = dx / dist, dy / dist
    speed = min(cap_mps, dist * 3.0)
    vx_c = nx * speed
    vy_c = ny * speed
    prev_vx = float(getattr(d, "_pta_vx", 0.0))
    prev_vy = float(getattr(d, "_pta_vy", 0.0))
    d.vx = prev_vx + (vx_c - prev_vx) * smooth
    d.vy = prev_vy + (vy_c - prev_vy) * smooth
    d._pta_vx = d.vx
    d._pta_vy = d.vy
    d.x += d.vx * dt
    d.y += d.vy * dt
    d.target_x = tx
    d.target_y = ty
    d.move_speed = math.hypot(d.vx, d.vy)
    d._mmdl_moved = True
    d.target_source = PRE_TACKLE_TARGET_SOURCE


def _live_commit_point(bc, d, pt) -> tuple[float, float]:
    """LIVE BC-forward commit target for an OPEN_PLAY COMMIT body.

    Attack runs +X.  A defender AHEAD of the carrier converges toward the
    carrier (bc.x - offset); a defender BEHIND closes on bc.x + offset.
    Lateral offset signed by the body's position relative to the carrier
    so the two per-side COMMIT bodies never converge on one point.

    Phase-gated (Stage 5): *pt* is the resolved phase-tuner dict (from
    ``_phase_tuners``) so the commit offset/lateral can be tuned per phase.
    """
    offset = float(pt.get("pretackle_commit_depth_offset_m", 1.0))
    lateral = float(pt.get("pretackle_commit_lateral_offset_m", 1.0))
    ahead = float(d.x) > float(bc.x)
    side = -1 if float(d.y) < float(bc.y) else 1
    # FRONT-HOLD (2026-10-03): a defender the carrier is running TOWARD (it
    # sits AHEAD of the carrier) HOLDS ITS GROUND in front of the carrier on
    # the carrier's own lane instead of being aimed BEHIND it (the legacy
    # bc.x - offset made the body back-pedal past the carrier).  max(d.x,
    # bc.x + front) never retreats behind the carrier and never charges past
    # the stand-off; the body holds and slides LATERALLY onto bc.y so the two
    # per-side front defenders bracket the carrier's corridor.  Gated by
    # pretackle_front_hold_enabled so the legacy path is byte-identical.
    if ahead and float(pt.get("pretackle_front_hold_enabled", 1.0)) > 0.0:
        front = float(pt.get("pretackle_front_offset_m", 0.6))
        bracket = float(pt.get("pretackle_front_lateral_offset_m", 0.3))
        tx = max(float(d.x), float(bc.x) + front)
        ty = float(bc.y) + bracket * side
        return _clamp_target_to_field(tx, ty)
    tx = float(bc.x) - offset if ahead else float(bc.x) + offset
    ty = float(bc.y) + lateral * side
    # FIELD BOUND (2026-09-25): a carrier at/past the tryline authors a
    # COMMIT point OUTSIDE the field (measured live: target_x == 121.0
    # against a 120.0 bound).  Player's position clamp makes that point
    # unreachable and the body walks into the boundary forever.  Bound it so
    # the commitment stays reachable -- the body then squares up on the
    # carrier at the wall instead of pressing against it.
    return _clamp_target_to_field(tx, ty)


def _record_suppressed(world, defender, bc) -> None:
    """Record a suppressed-tackle telemetry event (NO-OP unless diagnostics).

    Fired when the tackle gate suppresses a radially-close tackle (BC inside
    the radial radius but outside the slot-Y band).  Lazily imports the
    diagnostics recorder so the pre-tackle authority carries NO hard
    dependency on the debug package in production (diagnostics is
    off-by-default and records only when armed).
    """
    try:
        from game.debug.tes_diagnostics import record_suppressed_tackle as _rec
        _rec(world, int(getattr(world, "frame_count", 0)),
             resolve_pre_tackle_phase(world), defender, bc)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────
# SEEDING (once per contact/drive) + OWNER RECONCILIATION
# ─────────────────────────────────────────────────────────────────────

def _seed_body(world, d, bc, tuners, frozen_mode: bool, pt: dict | None = None) -> None:
    """Freeze the stable tackle-phase anchors for one body (once/drive).

    Anchor  = the body's PRE-DRIVE open-play geometry target (role depth +
              spacing).  Commit = the FROZEN BC-forward contact point at
              seed time when frozen_mode, otherwise the live-BC point
              (recomputed each frame in OPEN_PLAY).

    STAGE 8 (2026-09-05): when *frozen_mode* is True (TES CONTACT / DRIVE /
    DOMINANCE), the anchor is re-derived from the STRUCTURAL DEPTH-ROLE
    authority instead of the raw ``target_x/target_y``.  The raw target can
    be overwritten by the OPEN_PLAY BC-relative pursuit/close/flat-foot
    writers (``_apply_pursuit_commit_override`` /
    ``_apply_flatfoot_step_in`` / ``structured_close`` / ``tackler_commit``)
    on the last OPEN_PLAY frame, so it may point at a stale BC-relative
    point — the "travel left past ruck before retreating" symptom.  The
    depth-role anchor is the frame spacing/width authority (per-role
    spacing Y + ``ruck_x + WIDTH_OFFSETS[depth_role]``), which the body must
    hold during the drive.

    Phase-gated (Stage 5): *pt* is the resolved phase-tuner dict; when
    omitted it resolves the DEFAULT phase profile so the commit
    offset/lateral stay scalar-compatible.
    """
    if frozen_mode:
        # ── TES DRIVE RETREAT ANCHOR (2026-09-05 / drive-aware 2026-09-06) ──
        # DEF_LINE_READY bodies during TES drive get target_x from the
        # authoritative role-depth X (bc.x + WIDTH_OFFSETS[depth_role]) —
        # NOT the static ruck anchor (ruck_x + 10), so the line tracks
        # the driven carrier.  Y stays on the depth-lane spacing source
        # (frame_spacing_targets / role_depth_target_y).
        _rtx = getattr(d, "role_depth_target_x", None)
        if _finite(_rtx):
            ax = float(_rtx)
        else:
            from game.defence.defence_lvl_1.anchor_authority import (
                defensive_line_anchor_m as _anchor_m,
            )
            ax = _anchor_m(world)
        _fst = getattr(world, "frame_spacing_targets", None) or {}
        _fy = _fst.get(id(d))
        _ry = getattr(d, "role_depth_target_y", None)
        if _finite(_fy):
            ay = float(_fy)
        elif _finite(_ry):
            ay = float(_ry)
        else:
            _ty = getattr(d, "target_y", None)
            ay = float(_ty) if _finite(_ty) else float(d.y)
    else:
        tx = getattr(d, "target_x", None)
        ty = getattr(d, "target_y", None)
        if _finite(tx):
            ax = float(tx)
        else:
            ax = float(d.x)
        if _finite(ty):
            ay = float(ty)
        else:
            ay = float(d.y)

    if pt is None:
        pt = _phase_tuners(tuners, "DEFAULT")
    offset = float(pt.get("pretackle_commit_depth_offset_m", 1.0))
    lateral = float(pt.get("pretackle_commit_lateral_offset_m", 1.0))
    side = -1 if float(d.y) < float(bc.y) else 1
    if frozen_mode:
        cx = float(bc.x) + offset
        cy = float(bc.y) + lateral * side
    else:
        cx, cy = _live_commit_point(bc, d, pt)

    # FIELD BOUND (2026-09-25): the anchor and the commit point are both
    # AUTHORED targets.  An anchor captured outside the field would make
    # every later fold-back/SLOW step toward it unreachable, so both are
    # bounded at the single point where they enter the body.
    ax, ay = _clamp_target_to_field(ax, ay)
    cx, cy = _clamp_target_to_field(cx, cy)
    setattr(d, ANCHOR_X_ATTR, ax)
    setattr(d, ANCHOR_Y_ATTR, ay)
    setattr(d, COMMIT_X_ATTR, cx)
    setattr(d, COMMIT_Y_ATTR, cy)


def _reconcile_owners(world, managed, bc, tuners, frozen_mode: bool) -> dict:
    """Ensure <=1 COMMIT owner per side of the BC (deterministic).

    LEFT = d.y < bc.y, RIGHT = d.y >= bc.y.  Owner selection: smallest
    |x - commit_x| with an explicit player_id tiebreak.  Incumbent law: a
    live eligible owner KEEPS its side; a new owner only when the side has
    none.  Mid-drive newcomers are seeded SLOW/HOLD — they never steal an
    existing owner.
    """
    owners = getattr(world, OWNERS_KEY, None)
    if owners is None:
        owners = {}
        setattr(world, OWNERS_KEY, owners)

    by_side = {-1: [], 1: []}
    for d in managed:
        bcy = float(bc.y)
        side = -1 if float(d.y) < bcy else 1
        by_side[side].append(d)

    result = {}
    for side in (-1, 1):
        pool = by_side[side]
        if not pool:
            continue

        _front_hold = bool(
            getattr(tuners, "pretackle_front_hold_enabled", True)
        )

        def _rank(dd):
            # FRONT-HOLD (2026-10-03): an ahead body's commit X IS its own X
            # (it holds its ground), so the legacy |x - commit_x| ties for
            # every ahead body.  Rank by the live |x - bc.x| (the legacy
            # ordering key) with the deterministic player_id tiebreak, so the
            # body CLOSEST to the carrier still owns the commit / TES role.
            if _front_hold:
                return (
                    abs(float(dd.x) - float(bc.x)),
                    int(getattr(dd, "player_id", 0)),
                )
            cx = float(getattr(dd, COMMIT_X_ATTR))
            return (abs(float(dd.x) - cx), int(getattr(dd, "player_id", 0)))

        current = None
        for dd in pool:
            st = getattr(dd, STATE_ATTR, None)
            if st in (PreTackleState.COMMIT, PreTackleState.PLANTED):
                current = dd
                break

        if current is None:
            # ── TACKLE-GATING SLOT THRESHOLD (2026-09-07 rollout) ──
            # OPEN_PLAY only: a body may only become the COMMIT owner (and
            # therefore trigger the tackle) when it is slot-tackle-eligible
            # (BC inside the slot-Y band).  If NO body on this side is
            # slot-eligible, no COMMIT owner is assigned — all stay
            # SLOW/HOLD (a suppressed tackle).  During TES drive
            # (frozen_mode=True) the tackle is already formed, so the gate
            # does not apply and the nearest candidate is chosen as before.
            _candidates = pool
            _suppressed = False
            if not frozen_mode:
                from game.defence.defence_lvl_1.slot_arbitration import (
                    is_slot_tackle_eligible as _slot_eligible,
                )
                # ── RADIAL+FACING TACKLE ENGAGE GUARD (2026-09-10) ──
                # The slot-Y gate vetoed the COMMIT owner (and therefore the
                # PRIMARY / TES role stamp) for every body whose live Y sat
                # more than `tackle_slot_threshold_m` (0.3 m) off the carrier,
                # so no tackle ever formed.  A genuine line defender that is
                # radial-in AND FACING the carrier is tackle-eligible: admit
                # it as a COMMIT candidate and stamp
                # target_source = "tackle_engage_guard".
                from game.defence.defence_lvl_1.tackle_engage_guard import (
                    is_tackle_engage_guard as _is_engage_guard,
                    stamp_tackle_engage_guard as _stamp_guard,
                )
                _candidates = []
                for dd in pool:
                    if _slot_eligible(bc, dd, world):
                        _candidates.append(dd)
                    elif _is_engage_guard(dd, bc, world, tuners):
                        _stamp_guard(dd, world=world)
                        _candidates.append(dd)
                if not _candidates:
                    # Suppressed tackle: NO slot-eligible candidate on this
                    # side -> no COMMIT owner.  Record each radially-close
                    # body so the playtest replay has slot.target_y vs BC.y.
                    _suppressed = True
            if _candidates:
                current = min(_candidates, key=_rank)
                _sync_shims(current, PreTackleState.COMMIT)
                setattr(current, OWNER_SIDE_ATTR, side)
            elif _suppressed:
                for dd in pool:
                    _record_suppressed(world, dd, bc)

        for dd in pool:
            st = getattr(dd, STATE_ATTR, None)
            if dd is current:
                continue
            if st in (None, PreTackleState.NONE):
                _sync_shims(dd, PreTackleState.SLOW)

        result[side] = current
    return result


# ─────────────────────────────────────────────────────────────────────
# PER-FRAME APPLICATION (sole writer for managed bodies)
# ─────────────────────────────────────────────────────────────────────

def apply_pre_tackle(world, defenders, dt: float, tuners,
                     frozen_mode: bool = False) -> list:
    """Own the DEF_LINE_READY bodies for this frame.

    ``frozen_mode`` is the CONSOLIDATION switch:
      - False (OPEN_PLAY / pre-contact): COMMIT owners chase the LIVE BC
        forward point so they genuinely converge and trigger TES.
      - True (TACKLE_DRIVE / TACKLE_DOMINANCE): EVERY managed body folds
        back to the TES retreat anchor (ruck_x + 10 / spacing Y) — COMMIT
        no longer chases, it retreats with the line (STAGE 8 fold-back).
        SLOW/HOLD converge to the same frozen anchors.  The line never
        tracks the driven carrier (anti-collapse / anti-stack).

    Called from DefenceManager.update_tes_drive() (frozen_mode=True) and
    from the OPEN_PLAY dispatch after the projected pipeline / fullback
    geometry (frozen_mode=False), INSTEAD OF the legacy lane-tackler
    commit/stance writers for the bodies it returns.

    Returns the list of bodies managed THIS frame.
    """
    if not pre_tackle_enabled(tuners):
        return []
    bc = getattr(world, "ball_carrier", None)
    if bc is None:
        return []

    # ── PHASE-GATED BEHAVIOURAL TUNING (Stage 5, 2026-09-04) ──
    # Resolve the current sub-state's phase profile ONCE per frame.  Every
    # phase profile defaults to the global scalar tuners, so behaviour is
    # byte-identical until a phase profile is explicitly set on
    # ``tuners.pretackle_phase_tuners``.  `frozen_mode` semantics (live-BC
    # commit vs frozen-anchor) are untouched by phase-gating.
    phase_key = resolve_pre_tackle_phase(world)
    pt = _phase_tuners(tuners, phase_key)

    managed: list = []

    # ── STAGE 6: PRE-TACKLE ACTIVATION RADIUS (= pre-tackle trigger, 2026-09-04) ──
    # The authority must NOT own a DEF_LINE_READY body until the tackle radius
    # is actually breached (dist(d, bc) <= activation_r).  Before that the body
    # keeps retreating / spacing / chasing through the normal pipeline; only a
    # real tackle forming turns it into SLOW/HOLD/COMMIT.
    #
    # DRIVE-PHASE RADIUS GATE (2026-09-10): this now applies in frozen_mode
    # (TES CONTACT / DRIVE / DOMINANCE) as well.  The old frozen_mode carve-out
    # owned EVERY eligible line body for the whole chamber, so a defender
    # 15-20 m away was depth-locked (X pinned, Y held) and stopped defending —
    # the "everyone freezes during the drive" defect.  A body outside the
    # tackle radius is released: the live depth-role geometry (carrier-relative
    # X depth + frame-spacing lane Y) keeps running for it every frame.
    # ── TWO-RADIUS ACTIVATION (2026-09-15) ──
    # HOLD radius for an already-owned / corridor body, PROMOTE radius for a
    # body that has not yet been owned.  See publish_pre_tackle_state.
    activation_r_promote = float(
        getattr(tuners, "pre_tackle_trigger_radius_m", 2.0)
    )
    activation_r_hold = corridor_radius_m(tuners)

    def _activation_r_for(dd) -> float:
        """Per-body activation radius: the whole corridor once owned."""
        return (
            activation_r_hold if is_corridor_owned(dd) else activation_r_promote
        )

    # ── TES DRIVE STRUCTURE-HOLD EXEMPTION (2026-09-11) ──
    # A line body holding its TES drive structure cell is owned by
    # tes_drive_structure.py (the sole line writer for the chamber).  The
    # pre-tackle authority's frozen-mode branch depth-locks X and pins Y;
    # measured live that pin fought the hold and let the body drift up to
    # 1.6 m off its locked lane across an 8-tackle run.  Held bodies are
    # excluded here so the hold has exactly ONE owner; they re-enter this
    # authority the moment the chamber closes (the hold is dropped at
    # RELEASE), so no behaviour is lost outside the drive.
    from game.defence.defence_lvl_1.tes_drive_structure import (
        is_tes_structure_hold as _is_struct_hold,
    )

    # ── Pass 0: drop ineligible holders + radius-exited holders (all modes) ──
    for d in defenders:
        if not is_pre_tackle_managed(d):
            continue
        # ── RETREAT-OBLIGATION VETO (2026-09-17) ──
        # Release any stale ownership the moment the obligation is
        # outstanding: a previously-owned body that has not finished its
        # retreat must not keep a COMMIT/PLANTED state through this frame.
        if is_retreat_obligated(d, world):
            clear_pre_tackle_state(d)
            continue
        if _is_struct_hold(d) or not is_pre_tackle_eligible(d):
            clear_pre_tackle_state(d)
            continue
        _dist = math.hypot(float(d.x) - float(bc.x),
                           float(d.y) - float(bc.y))
        if _dist > _activation_r_for(d):
            clear_pre_tackle_state(d)

    # ── Pass 1: collect eligible bodies (structure-held bodies excluded) ──
    # RETREAT-OBLIGATION VETO (2026-09-17): a body still owing its 10 m
    # retreat is NOT eligible to be owned, so it can never become the
    # per-side COMMIT owner and can never feed a COMMIT/PLANTED stance
    # into the TES engage gate.
    eligible = [
        d for d in defenders
        if is_pre_tackle_eligible(d) and not _is_struct_hold(d)
        and not is_retreat_obligated(d, world)
    ]
    if not eligible:
        return managed

    # ── STAGE 6: activation gate (radius breach) — applies in ALL modes ──
    # Only bodies that have actually breached the activation radius are owned.
    # Far bodies fall through to the MMDL / structured / drive geometry
    # pipeline, which keeps them retreating / spacing / chasing normally.
    eligible = [
        d for d in eligible
        if math.hypot(float(d.x) - float(bc.x),
                      float(d.y) - float(bc.y)) <= _activation_r_for(d)
    ]
    if not eligible:
        return managed

    # ── Pass 2: seed new bodies (frozen anchors, once per contact/drive) ──
    # OPEN_PLAY ``publish_pre_tackle_state`` stamps ``pre_tackle_state`` but
    # NOT the anchor/commit attrs (only ``_seed_body`` sets those), so a body
    # that carried an OPEN_PLAY state into the drive is "managed" yet
    # unseeded.  Seed whenever the anchor attrs are absent — the first drive
    # frame then re-captures the frozen commit point (frozen_mode=True).
    for d in eligible:
        _need_seed = (not hasattr(d, ANCHOR_X_ATTR)
                      or not hasattr(d, COMMIT_X_ATTR))
        if _need_seed:
            _seed_body(world, d, bc, tuners, frozen_mode, pt)

    # ── Pass 3: reconcile COMMIT owners (<=1 per side, deterministic) ──
    owners = _reconcile_owners(world, eligible, bc, tuners, frozen_mode)

    # ── Tuner reads (phase-gated) ──
    hold_cap = float(pt["pretackle_slow_speed_mps"])
    freeze_r = float(pt["pretackle_freeze_radius_m"])
    smooth = float(pt["pretackle_follow_smooth"])
    commit_cap = float(pt["pretackle_commit_speed_mps"])
    plant_r = float(pt["pretackle_plant_radius_m"])
    slowdown_band = float(pt["pretackle_slowdown_band_m"])
    slowdown_scale = float(pt["pretackle_slowdown_scale"])
    band_width = max(0.01, slowdown_band - plant_r)

    # ── Pass 4: advance the state machine + integrate ──
    for d in eligible:
        st = getattr(d, STATE_ATTR, None)
        # ── TES DEPTH-LOCK (2026-09-06): HOLD DEPTH, FREE LATERAL ──
        # In frozen_mode (TES CONTACT / DRIVE / DOMINANCE) a managed body is
        # DEPTH-LOCKED, not hard-frozen: it holds its DEPTH (X, toward/away
        # from the ruck) but stays free to slide LATERALLY (Y) to its
        # spacing/lane anchor.  This replaces the Stage-10 seal's full
        # hard-freeze -- the TES chamber stays stable (no depth drift,
        # anchors held) while the line keeps the ability to slide and
        # respond.  The depth_lock_x is captured once on first lock and held
        # for the whole chamber; Y target is the spacing/lane anchor.
        if frozen_mode:
            if st is None:
                st = PreTackleState.HOLD
                _sync_shims(d, st)
            _lock_depth(d)
            # DRIVE-DEPTH TRACK (2026-09-06): when ``depth_lock_y`` is set
            # (DEF_LINE_READY / DEF_CHASE — the lock roles) the lateral Y is
            # held at drive entry (no blow-out); ``_lock_depth`` already set
            # target_y = depth_lock_y.  Only when ``depth_lock_y`` is None
            # (DEF_RETREAT_10 — not managed here) drive the LATERAL axis
            # toward the spacing/lane anchor so the body slides with the
            # lane instead of pinning in place.
            if getattr(d, "depth_lock_y", None) is None:
                d.target_y = float(getattr(d, ANCHOR_Y_ATTR, d.y))
            d.move_speed = float(pt.get("pretackle_slow_speed_mps", 2.5))
            managed.append(d)
            continue
        # TACKLE-RADIUS CHASE OVERRIDE (2026-09-05): OPEN_PLAY only.  A
        # managed OPEN_PLAY body within the tackle radius (dist <
        # tackle_radius_m) is committing to the tackle.  Force COMMIT (which
        # produces CHASE movement_state) so no managed body stays SLOW
        # (DRIFT) or HOLD (freeze) while in contact range -- prevents
        # retreat-into-contact, wrong-side stance, early HOLD, and TES
        # inheriting a corrupted movement state.  During TES drive
        # (frozen_mode=True) this MUST NOT fire: the line folds back, so a
        # within-radius body is NOT committing -- it retreats with the line
        # (STAGE 8 TES-DRIVE FOLD-BACK, 2026-09-05).
        if not frozen_mode and st in (PreTackleState.SLOW, PreTackleState.HOLD):
            _tr_val = float(getattr(tuners, "tackle_radius_m", 1.5))
            if _tr_val > 0.0 and math.hypot(
                float(d.x) - float(bc.x),
                float(d.y) - float(bc.y),
            ) < _tr_val:
                # ── TACKLE-GATING SLOT THRESHOLD (2026-09-07 rollout) ──
                # Radial-in but slot-OUT (BC outside the defender's slot-Y
                # band, |bc.y - target_y| >= TACKLE_SLOT_THRESHOLD_M) must
                # NOT promote to COMMIT — the tackle is suppressed, so the
                # body stays SLOW/HOLD (its organic movement state).  This is
                # the second conjunct of the tackle gate: radial-in AND
                # slot-in -> COMMIT; radial-in but slot-OUT -> no tackle.
                from game.defence.defence_lvl_1.slot_arbitration import (
                    is_slot_tackle_eligible as _slot_eligible,
                )
                from game.defence.defence_lvl_1.tackle_engage_guard import (
                    is_tackle_engage_guard as _is_engage_guard,
                    stamp_tackle_engage_guard as _stamp_guard,
                )
                # ── RADIAL+FACING TACKLE ENGAGE GUARD (2026-09-10) ──
                # Radial-in AND facing the carrier BYPASSES the slot gate:
                # the body is committed to the tackle, so FORCE COMMIT
                # (which produces CHASE + the PRIMARY/TES role stamp) rather
                # than recording a suppressed tackle and leaving it in DRIFT /
                # HOLD while it sits inside the tackle radius.
                if _is_engage_guard(d, bc, world, tuners):
                    _stamp_guard(d, world=world)
                    st = PreTackleState.COMMIT
                    _sync_shims(d, PreTackleState.COMMIT)
                elif _slot_eligible(bc, d, world):
                    st = PreTackleState.COMMIT
                    _sync_shims(d, PreTackleState.COMMIT)
                else:
                    # Suppressed tackle: radial-in but slot-OUT.  Record the
                    # event (slot.target_y + BC.y + radial distance) for the
                    # playtest replay trail.
                    _record_suppressed(world, d, bc)
        # Safe reads: a just-seeded body always carries anchors; a body that
        # carried an OPEN_PLAY state into the drive is re-seeded above before
        # this loop, and the fallback guards any residual edge case.
        # DRIVE-AWARE (2026-09-06): in frozen_mode re-read the
        # authoritative role-depth X (bc.x + WIDTH_OFFSETS[depth_role])
        # every frame so the body tracks the moving BC.  The frozen seed
        # stays the fallback for a body without a live depth stamp.
        if frozen_mode:
            _live_rtx = getattr(d, "role_depth_target_x", None)
            if _finite(_live_rtx):
                ax = float(_live_rtx)
            else:
                ax = float(getattr(d, ANCHOR_X_ATTR, d.x))
        else:
            ax = float(getattr(d, ANCHOR_X_ATTR, d.x))
        ay = float(getattr(d, ANCHOR_Y_ATTR, d.y))
        # FIELD BOUND (2026-09-25): the live role-depth X is
        # ``bc.x + WIDTH_OFFSETS[depth_role]`` -- a carrier past the tryline
        # makes it an out-of-field HOLD point, i.e. an unreachable anchor the
        # body could never arrive at (so HOLD could never convert to PLANTED).
        ax, ay = _clamp_target_to_field(ax, ay)

        # ── STAGE 8: TES-DRIVE COMMIT FOLD-BACK (2026-09-05) ──
        # During TACKLE_DRIVE / TACKLE_DOMINANCE the tackle is already
        # formed.  A COMMIT/PLANTED pre-tackle body must NOT chase the BC-
        # forward contact point any more — the CHASE writer was overwriting
        # the seeded TES retreat anchor (ruck_x + 10) with the frozen commit
        # point (bc.x + offset), keeping a line body stuck at the contact
        # instead of folding back with the line.  In frozen_mode a
        # COMMIT/PLANTED body falls through to the SLOW/HOLD fold-back path
        # below (anchor X + spacing Y, DRIFT) so the whole defensive line
        # retreats uniformly.  OPEN_PLAY (frozen_mode=False) keeps the live
        # COMMIT chase so the tackle still forms.
        if not frozen_mode and st in (PreTackleState.COMMIT, PreTackleState.PLANTED):
            # LIVE commit point in OPEN_PLAY (not seeded), phase-gated.
            cx, cy = _live_commit_point(bc, d, pt)
            setattr(d, COMMIT_X_ATTR, cx)
            setattr(d, COMMIT_Y_ATTR, cy)
            dist = math.hypot(cx - float(d.x), cy - float(d.y))
            # ── FRONT-HOLD / HEAD-ON FLAT-FOOT PLANT (2026-10-03) ──
            # A defender the carrier is running TOWARD (it sits AHEAD of the
            # carrier) holds the corridor in front at the softer head-on pace
            # and PLANTS once the CARRIER ITSELF is inside the head-on plant
            # radius (chosen just outside the tackle arm).  A planted body is
            # a genuine stationary square-up (zero velocity + HOLD) which the
            # engage machine routes to SQUARE_UP_ENGAGE / COMMIT_PLANTED
            # (CLEAN_ENGAGE) while SKIPPING the closing-vector evasion checks
            # -- the "flat-footed raises the tackle chance" law.  Without it
            # TES arms while the body is still a fast CHASE and the overshoot
            # / outrun rejects kill the tackle.
            _front_hold = (
                float(pt.get("pretackle_front_hold_enabled", 1.0)) > 0.0
                and float(d.x) > float(bc.x)
            )
            _headon_plant = (
                _front_hold
                and float(pt.get("pretackle_headon_plant_enabled", 1.0)) > 0.0
                and float(getattr(bc, "vx", 0.0) or 0.0) >= -0.1
            )
            _bc_dist = math.hypot(
                float(d.x) - float(bc.x), float(d.y) - float(bc.y),
            )
            # ── ANTICIPATION SLOWDOWN ANCHOR (2026-09-08) ──
            # The taper is measured against the modernised anchor (predicted
            # threat point / role-depth cell / live BC) so the defender
            # decelerates as it reaches the point the line leaned toward,
            # while the actual COMMIT target stays the BC-forward point
            # (only the distance SIGNAL uses the anchor).
            from game.defence.defence_lvl_1.anticipation_layer import (
                anticipation_slowdown_point as _aspoint,
            )
            _anchor_x, _anchor_y = _aspoint(world, bc, tuners, d)
            slowdown_dist = math.hypot(
                float(d.x) - _anchor_x, float(d.y) - _anchor_y,
            )

            _face_angle = math.atan2(cy - float(d.y), cx - float(d.x))

            if st == PreTackleState.PLANTED:
                _freeze_stance(d, face_x=cx, face_y=cy, plant=True)
                stamp_handoff(d, "PLANTED", _face_angle)
                managed.append(d)
                continue

            # HEAD-ON PLANT (2026-10-03): plant on the LIVE CARRIER once it
            # is inside the head-on plant radius -- chosen just outside the
            # tackle arm -- so the body is already a genuine flat-footed
            # square-up when TES fires.  Face the carrier for a head-on angle.
            if _headon_plant and _bc_dist < float(
                pt.get("pretackle_headon_plant_radius_m", 1.8)
            ):
                _sync_shims(d, PreTackleState.PLANTED)
                _freeze_stance(d, face_x=float(bc.x), face_y=float(bc.y),
                               plant=True)
                stamp_handoff(d, "PLANTED", _face_angle)
                managed.append(d)
                continue

            if dist < plant_r:
                _sync_shims(d, PreTackleState.PLANTED)
                _freeze_stance(d, face_x=cx, face_y=cy, plant=True)
                stamp_handoff(d, "PLANTED", _face_angle)
                managed.append(d)
                continue

            # HEAD-ON approach pace: a front-holding body closes at the softer
            # ``pretackle_headon_commit_speed_mps``; every other committed body
            # keeps the legacy ``pretackle_commit_speed_mps``.
            _base_cap = (
                float(pt.get("pretackle_headon_commit_speed_mps", 2.0))
                if _front_hold else commit_cap
            )
            if slowdown_dist < slowdown_band:
                t = (slowdown_dist - plant_r) / band_width
                t = max(0.0, min(1.0, t))
                cap = _base_cap * (slowdown_scale + (1.0 - slowdown_scale) * t)
            else:
                cap = _base_cap
            _slow_step(d, cx, cy, dt, cap, smooth)
            d.movement_state = MovementState.CHASE
            _sync_shims(d, PreTackleState.COMMIT)
            # CONTACT CONTRACT: the corridor's governed approach velocity,
            # inherited verbatim by the TES outcome system at TACKLE_INIT.
            stamp_handoff(d, "COMMIT", _face_angle)
            managed.append(d)
            continue

        # ── HOLD / SLOW : the frozen-anchor contract ──
        err = math.hypot(ax - float(d.x), ay - float(d.y))

        if st == PreTackleState.HOLD:
            _freeze_stance(d)
            stamp_handoff(d, "HOLD", 0.0)
            managed.append(d)
            continue

        if err < freeze_r:
            _sync_shims(d, PreTackleState.HOLD)
            _freeze_stance(d)
            stamp_handoff(d, "HOLD", 0.0)
            managed.append(d)
            continue

        _slow_step(d, ax, ay, dt, hold_cap, smooth)
        d.movement_state = MovementState.DRIFT
        _sync_shims(d, PreTackleState.SLOW)
        stamp_handoff(d, "SLOW", 0.0)
        managed.append(d)

    return managed


# ── Backwards-compatible aliases (consumers may still import these) ──
apply_pretackle_attempts = apply_pre_tackle
pre_tackle_attempt_enabled = pre_tackle_enabled
is_pretackle_eligible = is_pre_tackle_eligible
is_pretackle_managed = is_pre_tackle_managed
clear_pretackle_state = clear_pre_tackle_state
reset_pretackle_attempts = reset_pre_tackle
PTA_TARGET_SOURCE = PRE_TACKLE_TARGET_SOURCE
