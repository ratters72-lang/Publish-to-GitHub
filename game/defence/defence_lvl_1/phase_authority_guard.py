"""phase_authority_guard.py — Stage 3H phase-based movement authority guard.

THE CONDUCTOR MODEL (one owner per concern per phase)
----------------------------------------------------
    TES phases (INIT/CONTACT/DRIVE/DOMINANCE/RELEASE/PTB):
        TES exclusively owns tackler/marker geometry, freeze, and
        target_x/target_y.  NO open-play defensive-line / collapse /
        retreat / close writer may run.
    POST_RUCK_OPEN_PLAY:
        Geometry restricted to retreat/collapse + depth/width.
    OPEN_PLAY:
        Geometry restricted to depth/width + collapse; lane logic marks
        tacklers ONLY (never rewrites global target_x except via the
        single BC-close / commit authority).
    ALL phases:
        The ATTACK pipeline must NEVER touch defender geometry.

This module formalises that map and provides audit/verification entry
points:

    LEGAL_TARGET_SOURCES — the phase→writer-family authority table.

    phase_of_engine(engine)          -> current phase string (uppercase).
    legal_sources_for_phase(phase)   -> the set of target_source values the
                                        phase owns.
    report_phase_authority(world, phase, defenders) -> list[str]
        Log-only audit: returns every defender whose target_source is not
        owned by the phase.  (Callers MAY raise on non-empty.)

    attack_isolated_from_defenders(snapshot_before, snapshot_after) -> list[str]
        Proves the attack pipeline mutated NO defender's target_x /
        target_y / movement_state / move_speed / frozen.  Callers snapshot
        the defending team before/after the attack update and compare.

NOTE: this is a *consumer* audit module — it never writes defender or
world state.  It is the formal, runnable contract for the conductor model.
"""

from __future__ import annotations

from player.player import Player

__all__ = [
    "PhaseAuthorityViolation",
    "LEGAL_TARGET_SOURCES",
    "phase_of_engine",
    "legal_sources_for_phase",
    "report_phase_authority",
    "report_recovery_band_authority",
    "report_post_ruck_band_authority",
    "attack_isolated_from_defenders",
]


# ─────────────────────────────────────────────────────────────────────
# PHASE → WRITER-FAMILY AUTHORITY TABLE (the conductor map)
# ─────────────────────────────────────────────────────────────────────

# OPE internal phases (uppercase) that are TES-exclusive per the conductor
# model.  `is_tackle_phase` covers INIT/CONTACT/DRIVE/DOMINANCE; RELEASE
# and PTB_* are also TES-exclusive (markers / BC / DH / retreat only).
_TES_PHASES = frozenset({
    "TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE",
    "RELEASE", "PTB_PREP", "PTB_EXEC", "PTB",
})

# target_source values the TES phases own.  These are the ONLY writers a
# defender may carry while a TES phase is active.  (The freeze-pin and
# fullback-anchor sources are legal in every phase.)
_TES_OWNED_SOURCES = frozenset({
    "tes_support_retreat",       # RetreatManager fold-back (Stage 3H sole X)
    "retreat_freeze_pin",        # frozen defender pin
    "retreat_to_lane",           # width-lane retreat (lane = ruck+offset)
    "retreat_non_compliant",     # anchor-compliance retreat
    "struct_marker_frozen",      # frozen marker pin (OPE)
    "fullback_geometry",         # fullback anchor
    "fullback",                  # structured/unified fullback anchor
    "marker_frozen",             # unified-mode marker pin
    "freeze_timer",              # freeze-timer pin
    "timing_locked",             # timing-locked pin
    # -- DH PRE-POSITION (2026-09-16) --
    # The DH is driven toward bc.x - DH_BEHIND_DISTANCE_M for the whole tackle
    # by OpenPlayEngine._drive_dh_preposition(), so its approach is complete
    # before RELEASE begins.  The DH is an ATTACKER, not a defender, so it
    # never enters the defender geometry pipeline; this tag exists so the
    # write is attributable in the target-write log.
    "dh_preposition",
    # -- TES DEPTH AUTHORITY (STAGE 2, 2026-09-18) --
    # The depth-discharge successor to the TES Depth Obligation: a
    # non-exempt line body inside the TES chamber whose depth debt exceeds
    # the obligation tolerance is granted X-closure authority toward its
    # canonical role depth target (gap-proportional convergence).  It is a
    # legitimate TES-chamber writer: it runs inside update_tes_drive over
    # the same body pool the structure hold owns.
    "tes_depth_authority",
})

# POST_RUCK_OPEN_PLAY: retreat/collapse + depth/width only.  No close /
# commit / open-play line-break writer may run.
_POST_RUCK_OWNED_SOURCES = frozenset({
    "retreat_to_lane",
    "retreat_non_compliant",
    "fullback_geometry",
    "fullback",
    "retreat_freeze_pin",
    "freeze_timer",
})

# OPEN_PLAY: depth/width + collapse (structured pipeline) + the single
# BC-close authority.  Pursuit is XOR-structured and never runs here in
# normal structured play; lane logic marks roles only, so the only
# legacies that may set target_x are the close/commit/collapse writers.
_OPEN_PLAY_OWNED_SOURCES = frozenset({
    "structured_open_play",      # step-7 depth/width geometry
    "retreat_to_lane",           # step-9 retreat-to-lane
    "retreat_non_compliant",     # step-9 anchor compliance
    "structured_close",          # step-10 BC-close (collapse) — PRIMARY lane
    "tackler_commit",            # step-10a1 BC-forward commit (single close authority)
    "line_break_force",          # force reaction (line-break)
    "engaged_non_lane_chase",    # step-7a engaged non-lane chase
    "line_break_collapse",       # step-7a PASSIVE collapse
    "fullback_geometry",
    "fullback",
    "retreat_freeze_pin",
    "freeze_timer",
    # ── PRE-TACKLE CORRIDOR SOURCES (2026-09-15) ──
    # SPEED-AUTHORITY CONSOLIDATION.  The pre-tackle authority and the two
    # post-writer recovery guards all stamp ``target_source`` in OPEN_PLAY,
    # but none of their names was listed here.  Before the corridor that was
    # near-harmless (few bodies carried them); the corridor now owns EVERY
    # committed tackler across the whole slowdown band, so an unlisted
    # source would make this audit flag the normal approach as a
    # phase-authority violation.  These are legitimate OPEN_PLAY writers:
    #   pre_tackle               -- pre_tackle_authority (_slow_step/_freeze_stance)
    #   pretackle                -- the same authority's legacy source name
    #   tackle_engage_guard      -- radial+facing engage bypass stamp
    #   line_break_static_guard  -- last-X recovery guard
    #   seed_hold                -- stale-seed lockdown
    #   catch_x_authority        -- post-catch X authority (approach writer)
    "pre_tackle",
    "pretackle",
    "tackle_engage_guard",
    "line_break_static_guard",
    "seed_hold",
    "catch_x_authority",
    # -- DH PRE-POSITION (2026-09-16) --
    # Set by OpenPlayEngine._drive_dh_preposition() while a TES chamber owns
    # the DH.  Listed in the OPEN_PLAY table as well so the audit accepts the
    # tag on the frames the same body carries into open play.
    "dh_preposition",
    # MMDL movement-state attribution (movement_state, not target geometry)
    "mmdl_RETREAT", "mmdl_SLIDE", "mmdl_SHOOT",
    "mmdl_CHASE", "mmdl_DRIFT", "mmdl_HOLD",
    # legacy unified-mode decision attribution
    "decision_HOLD", "decision_SHOOT", "decision_CHASE",
    "decision_COMPRESS", "decision_SLIDE", "decision_DRIFT",
    "decision_RETREAT",
})

LEGAL_TARGET_SOURCES: dict[str, frozenset] = {
    "TES": _TES_OWNED_SOURCES,
    "POST_RUCK_OPEN_PLAY": _POST_RUCK_OWNED_SOURCES,
    "OPEN_PLAY": _OPEN_PLAY_OWNED_SOURCES,
}

# Sources that are acceptable transient placeholders — a freshly-seeded or
# mid-transition body may briefly carry these without being a violation.
_ACCEPTABLE_VOID_SOURCES = frozenset({
    "", "none", "structured", "fallback",
    "pursuit_no_bc", "structured_no_bc", "pursuit",
    "fullback", "role_governs",
})

# OPEN_PLAY target_source PREFIXES.  Some writers stamp a composite source
# of the form ``<writer>:<resolved-state>`` (e.g. the pre-tackle band
# resolve stamps ``band:COMMIT`` / ``band:SLOW``).  The prefix identifies
# the writer family; the suffix is the state it resolved to.  These are
# legitimate OPEN_PLAY writers, so the phase audit matches on the prefix.
_OPEN_PLAY_OWNED_PREFIXES = frozenset({"band"})


# ─────────────────────────────────────────────────────────────────────
# EXCEPTION
# ─────────────────────────────────────────────────────────────────────

class PhaseAuthorityViolation(AssertionError):
    """Raised to flag a phase-authority violation (caller decides to raise)."""


# ─────────────────────────────────────────────────────────────────────
# PHASE RESOLUTION
# ─────────────────────────────────────────────────────────────────────

def phase_of_engine(engine) -> str:
    """Return the engine's current phase string, uppercased.

    Accepts an OpenPlayEngine (reads ``_phase``) or a WorldState
    (reads ``sub_state`` mapped to an uppercase phase label).
    """
    label = getattr(engine, "_phase", None)
    if label is None:
        sub = getattr(engine, "sub_state", None)
        label = getattr(sub, "value", sub) if sub is not None else "OPEN_PLAY"
    return str(label).upper()


def legal_sources_for_phase(phase: str) -> frozenset:
    """Return the set of target_source values owned by *phase*."""
    p = str(phase).upper()
    if p in _TES_PHASES:
        return _TES_OWNED_SOURCES
    if p == "POST_RUCK_OPEN_PLAY":
        return _POST_RUCK_OWNED_SOURCES
    return _OPEN_PLAY_OWNED_SOURCES


# ─────────────────────────────────────────────────────────────────────
# AUDIT — phase-scoped target_source ownership
# ─────────────────────────────────────────────────────────────────────

def report_phase_authority(world, phase: str, defenders) -> list[str]:
    """Return every violation of the phase→authority table.

    A defender is in violation when its ``target_source`` is neither
    owned by the phase nor an acceptable transient/void source.  The
    dynamic fullback is exempt (it has its own backfield anchor writer in
    every phase).  Log-only by contract — callers decide whether to raise.
    """
    legal = legal_sources_for_phase(phase)
    violations: list[str] = []
    for d in defenders or ():
        # Duck-typed: any defensive body carrying a target_source is
        # auditable (covers real Players and test/stand-in bodies).
        if d is None or not hasattr(d, "target_source"):
            continue
        if getattr(d, "fullback_role_active", False):
            continue
        src = getattr(d, "target_source", None) or ""
        src_norm = str(src).strip().lower()
        if src in legal or src_norm in _ACCEPTABLE_VOID_SOURCES:
            continue
        # Composite sources: match the writer-family prefix.
        if src_norm.split(":", 1)[0] in _OPEN_PLAY_OWNED_PREFIXES:
            continue
        if src_norm in _OPEN_PLAY_OWNED_SOURCES:
            continue
        violations.append(
            f"PHASE_AUTHORITY phase={phase} "
            f"id={getattr(d, 'player_id', '?')} "
            f"role={getattr(getattr(d, 'defender_role', None), 'name', None)} "
            f"target_source={src!r} not owned by phase"
        )
    return violations


# ─────────────────────────────────────────────────────────────────────
# RECOVERY-WINDOW BAND AUTHORITY — recovery roles must never override bands
# ─────────────────────────────────────────────────────────────────────

# Valid DR10 width-bands (A/B/C/D/CENTRE/WINGER).  A defender holding one
# of these during RELEASE / PTB / POST_RUCK_OPEN_PLAY retains it verbatim;
# the recovery role (IDR10) may only seed a defender that had no band.
_VALID_WIDTH_BANDS = frozenset({"A", "B", "C", "D", "CENTRE", "WINGER"})


def report_recovery_band_authority(before_bands: dict, defenders) -> list[str]:
    """Return recovery-window band-preservation violations (log-only audit).

    *before_bands* is the ``{player_id: depth_role}`` snapshot taken when
    the tackle-finish recovery window began (RELEASE entry).  A defender
    that already held a valid width-band must retain EXACTLY that band for
    the whole RELEASE → PTB → POST_RUCK window; the recovery role (IDR10)
    may only seed a defender that had NO band.  A banded defender whose
    band changed (or vanished) mid-window is a recovery-role override of
    established spacing authority.

    Keyed by ``player_id`` with an ``id(d)`` fallback so both engine
    snapshots (stable ids) and test stamps (fresh objects) are auditable.
    """
    violations: list[str] = []
    for d in defenders or ():
        if d is None or not hasattr(d, "depth_role"):
            continue
        pid = getattr(d, "player_id", None)
        before = (before_bands or {}).get(pid)
        if before is None:
            before = (before_bands or {}).get(id(d))
        after = getattr(d, "depth_role", None)
        if before in _VALID_WIDTH_BANDS and after != before:
            violations.append(
                f"RECOVERY_BAND_OVERRIDE id={pid} "
                f"band before={before!r} after={after!r}"
            )
    return violations


def report_post_ruck_band_authority(before_bands: dict, defenders) -> list[str]:
    """Backward-compatible alias of ``report_recovery_band_authority``.

    POST_RUCK_OPEN_PLAY is one of the recovery-window phases; the post-ruck
    audit reports the same band-preservation contract.
    """
    return report_recovery_band_authority(before_bands, defenders)


# ─────────────────────────────────────────────────────────────────────
# ATTACK ISOLATION — attack must never touch defender geometry
# ─────────────────────────────────────────────────────────────────────

# Defender fields the attack pipeline must never mutate.
_PROTECTED_DEFENDER_FIELDS = (
    "target_x", "target_y", "movement_state", "move_speed", "frozen",
)


def attack_isolated_from_defenders(before: dict, after: dict) -> list[str]:
    """Return violations where the attack pipeline mutated a defender.

    *before* / *after* are ``{player_id: (target_x, target_y,
    movement_state, move_speed, frozen)}`` snapshots taken immediately
    before and after the attack update.  Returns empty when the attack
    pipeline left every defender field byte-identical (the conductor
    model's "attack logic must be prohibited from touching defender
    geometry in all phases" contract).
    """
    violations: list[str] = []
    for pid, b in (before or {}).items():
        a = (after or {}).get(pid)
        if a is None:
            continue
        # Compare the five protected fields positionally.
        for idx, (bv, av) in enumerate(zip(b, a)):
            if bv != av:
                violations.append(
                    f"ATTACK_TOUCHED_DEFENDER id={pid} "
                    f"field={_PROTECTED_DEFENDER_FIELDS[idx]} "
                    f"before={bv!r} after={av!r}"
                )
    return violations
