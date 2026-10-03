"""
game/debug/fullback_invariant_audit.py -- Per-frame fullback-invariant audit.

Frame-accurate diagnostic that captures the dynamic fullback actor's
five invariant fields at a wiring point, so any regression that breaks
the "fullback is a deep, unfrozen, geometry-owned anchor" contract is
caught the moment it happens.

INVARIANT FIELDS CAPTURED (per call):
    fb_id           -- player_id of the active fullback actor
    fb_role         -- the actor's DefenderRole (must be a legal mover,
                       DEF_LINE_READY; never MARKER/TACKLER/DUMMY_HALF)
    fb_depth_role   -- the actor's depth_role (must be None: the depth
                       system excludes the fullback actor)
    fb_frozen       -- the actor's frozen flag (must be False: the
                       fullback is never line-frozen)
    fb_target_source-- the actor's target_source (must be
                       "fullback_geometry": the authoritative writer)

WIRING (requested):
  1. OpenPlayEngine._start_tackle -- the tackle-entry boundary.  The actor
     must survive tackle entry as a deep anchor; this is the frame where
     timing locks are wiped / roles are re-armed, so it is the most likely
     to leak a line role or freeze onto the last defender.
  2. OpenPlayEngine PTB-era phase handlers -- immediately AFTER
     _reassert_fullback(dt).  The five PTB-era handlers (_update_release,
     _update_ptb_prep, _update_ptb_exec, _update_ptb,
     _update_post_ruck_open_play) are high-risk sites for line-role
     leakage, freeze artefacts, or target-source overrides due to TES wipes
     and P7 retreat/marker/DH writers.  Running AFTER the re-assert captures
     the FINAL frame state (not a mid-write state).

The audit is OBSERVATIONAL ONLY -- it never modifies player/world state,
physics, or phase ordering.

SELF-LIMITING: like the sibling audits, prints at most
AUDIT_PER_PHASE_FRAMES frames per game phase across AUDIT_MAX_PHASES
distinct phases, then goes silent.  Call rearm_fullback_invariant_audit()
to re-arm for another capture run.
"""

from player.player import DefenderRole

from game.defence.defence_lvl_1.fullback_role import (
    is_fullback_emergency_engaged as _fb_emergency_engaged,
)

# ── Audit gates ───────────────────────────────────────────────────────
DEBUG_FULLBACK_INVARIANT_AUDIT: bool = True
AUDIT_PER_PHASE_FRAMES: int = 8
AUDIT_MAX_PHASES: int = 4

_printed_per_phase: dict[str, int] = {}
_phases_printed: int = 0

# ── Invariant violation tags (consistent with engine diagnostic tags) ──
FB_ROLE_CONTAMINATION: str = "FB_ROLE_CONTAMINATION"
FB_DEPTH_ROLE_LEAK: str = "FB_DEPTH_ROLE_LEAK"
FB_FROZEN_VIOLATION: str = "FB_FROZEN_VIOLATION"
FB_TARGET_SOURCE_OVERRIDE: str = "FB_TARGET_SOURCE_OVERRIDE"
FB_ID_MISMATCH: str = "FB_ID_MISMATCH"
# B2b (2026-09-16): the emergency-defender path.
FB_TAG_LOST_ON_ENGAGE: str = "FB_TAG_LOST_ON_ENGAGE"

# Roles that must NEVER appear on the fullback actor -- UNLESS the actor is
# engaged in the emergency-defender path.
#
# EMERGENCY-DEFENDER EXEMPTION (B2b, 2026-09-16)
# ---------------------------------------------
# TACKLE ELIGIBILITY and LINE MEMBERSHIP are separate concepts.  On a
# GENUINE LINE BREAK the tagged fullback is armed as a TES tackler exactly
# like any other defender and carries TACKLER_1/2/3 for the duration of the
# tackle -- WITHOUT losing the tag.  The tag is what keeps it out of the
# line (population / depth role / side / spacing / width / span / formation
# slot), so a TACKLER_* role on a TAGGED, ENGAGED actor is CORRECT, not a
# contamination.  A TACKLER_* role on a tagged actor that is NOT engaged is
# still a violation: that is the "absorbed into the line" signature.
CONTAMINATED_ROLES = frozenset({
    DefenderRole.MARKER_A,
    DefenderRole.MARKER_B,
    DefenderRole.TACKLER_1,
    DefenderRole.TACKLER_2,
    DefenderRole.TACKLER_3,
    DefenderRole.DUMMY_HALF,
})

# The target_source values owned by the emergency path.  While engaged, the
# commit / corridor / engage-guard writers own the actor's target (the
# fullback geometry authority yields to TES by construction), so these are
# legal on an engaged actor; any OTHER value is a genuine override.
_EMERGENCY_TARGET_SOURCES = frozenset({
    "fullback_geometry",
    "tackler_commit",
    "pre_tackle",
    "tackle_engage_guard",
    "line_break_static_guard",
})


def rearm_fullback_invariant_audit() -> None:
    """Reset the per-phase print budget for another capture run."""
    global _printed_per_phase, _phases_printed
    _printed_per_phase = {}
    _phases_printed = 0


def _phase_key(world) -> str:
    raw_phase = getattr(world, "phase", None)
    key = getattr(raw_phase, "value", None) or str(raw_phase)
    if key in ("None", ""):
        key = "OPEN_PLAY"
    return key


def capture_fullback_invariants(world) -> dict | None:
    """Return the fullback's invariant fields, or None when no FB actor.

    Reads the same source of truth the engine publishes on the world
    (``fullback_actor_id``) and the live Player object behind it.  When
    no actor is currently active (``world.fullback_role_active`` False or
    no ``fullback_actor_id``), returns None so callers can skip the frame
    without guessing.
    """
    fb = None
    for d in getattr(world, "defending_team", []) or []:
        if getattr(d, "fullback_role_active", False):
            fb = d
            break

    if fb is None:
        # Fall back to the published actor id (kickoff/debug stubs may
        # publish without a live role flag).
        actor_id = getattr(world, "fullback_actor_id", None)
        if actor_id is None:
            return None
        for d in getattr(world, "players", []) or []:
            if getattr(d, "player_id", None) == actor_id:
                fb = d
                break
        if fb is None:
            return None

    ope = getattr(world, "open_play_engine", None)
    ope_phase = getattr(ope, "_phase", None) if ope is not None else None

    # ── EMERGENCY-DEFENDER SCAN (B2b, 2026-09-16) ──
    # Any body carrying the engagement marker WITHOUT the tag has lost the
    # fullback tag while tackling -- the exact failure mode the emergency
    # path exists to prevent (the actor would be absorbed into the line).
    _tag_lost = [
        getattr(d, "player_id", None)
        for d in (getattr(world, "defending_team", []) or [])
        if _fb_emergency_engaged(d)
        and not getattr(d, "fullback_role_active", False)
    ]

    role = getattr(fb, "defender_role", None)
    return {
        "frame": getattr(world, "frame_count", None),
        "phase": _phase_key(world),
        "ope_phase": ope_phase,
        "fb_id": getattr(fb, "player_id", None),
        "fb_role": role,
        "fb_role_name": getattr(role, "name", None),
        "fb_depth_role": getattr(fb, "depth_role", None),
        "fb_depth_side": getattr(fb, "depth_side", None),
        "fb_frozen": bool(getattr(fb, "frozen", False)),
        "fb_target_source": getattr(fb, "target_source", None),
        "published_fb_id": getattr(world, "fullback_actor_id", None),
        "fb_emergency_engaged": _fb_emergency_engaged(fb),
        "fb_tag_lost_ids": _tag_lost,
    }


def _invariant_violations(rec: dict | None) -> list[str]:
    """Return the list of invariant violations for a capture record."""
    if rec is None:
        return []
    violations: list[str] = []
    _engaged = bool(rec.get("fb_emergency_engaged", False))

    role = rec.get("fb_role")
    if role in CONTAMINATED_ROLES and not _engaged:
        violations.append(FB_ROLE_CONTAMINATION)

    if (rec.get("fb_depth_role") is not None
            or rec.get("fb_depth_side") is not None):
        violations.append(FB_DEPTH_ROLE_LEAK)

    if rec.get("fb_frozen") and not _engaged:
        violations.append(FB_FROZEN_VIOLATION)

    _src = rec.get("fb_target_source")
    if _engaged:
        if _src not in _EMERGENCY_TARGET_SOURCES:
            violations.append(FB_TARGET_SOURCE_OVERRIDE)
    elif _src != "fullback_geometry":
        violations.append(FB_TARGET_SOURCE_OVERRIDE)

    pub_id = rec.get("published_fb_id")
    fb_id = rec.get("fb_id")
    if pub_id is not None and fb_id is not None and pub_id != fb_id:
        violations.append(FB_ID_MISMATCH)

    if rec.get("fb_tag_lost_ids"):
        violations.append(FB_TAG_LOST_ON_ENGAGE)

    return violations


def audit_fullback_invariants(world) -> dict | None:
    """Capture + print one frame of the fullback-invariant audit.

    Self-limiting (per-phase print budget).  Returns the capture record
    (or None) so a verifier can inspect invariants without console spam.
    Never mutates any engine state.
    """
    global _phases_printed

    if not DEBUG_FULLBACK_INVARIANT_AUDIT:
        return capture_fullback_invariants(world)

    phase_key = _phase_key(world)
    if _phases_printed >= AUDIT_MAX_PHASES:
        return capture_fullback_invariants(world)
    if _printed_per_phase.get(phase_key, 0) >= AUDIT_PER_PHASE_FRAMES:
        return capture_fullback_invariants(world)

    rec = capture_fullback_invariants(world)
    if rec is None:
        return None

    # Only count + print frames that actually have a fullback actor.
    _printed_per_phase[phase_key] = _printed_per_phase.get(phase_key, 0) + 1
    if _printed_per_phase[phase_key] == 1:
        _phases_printed += 1

    violations = _invariant_violations(rec)
    v_str = ",".join(violations) if violations else "OK"
    role_name = rec.get("fb_role_name") or str(rec.get("fb_role"))
    print(
        f"[FB_INVARIANT] frame={rec.get('frame')} "
        f"phase={rec.get('phase')} ope={rec.get('ope_phase')} "
        f"fb_id={rec.get('fb_id')} fb_role={role_name} "
        f"fb_depth_role={rec.get('fb_depth_role')} "
        f"fb_frozen={rec.get('fb_frozen')} "
        f"fb_target_source={rec.get('fb_target_source')} "
        f"-> {v_str}"
    )
    return rec
