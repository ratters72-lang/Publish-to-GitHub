"""
secondary_width_grace.py — Stage 2 SECONDARY width-eligibility grace.

PURPOSE
-------
``width_authority.is_width_eligible`` exempts PRIMARY/SECONDARY defenders
from the width-lane authority (``_EXEMPT_DEPTH_CLASSES``), so a defender
promoted to SECONDARY while ruck-adjacent loses its width lane.  On the
next frame's pipeline the width recompute produces NO X target for it, and
the fallback spacing (the non-chain ruck_y pin, the ``frame_spacing_y``
HOLD-LINE fallback) can then pull that interior defender into a fallback
micro-gap during the marker lock window (Stage 1, ``MARKER_LOCK_S``).

This module applies a SHORT width-eligibility grace: when a defender is
promoted to PRIMARY/SECONDARY while ruck-adjacent, we preserve its
width-lane eligibility for ``secondary_grace_duration_s`` (default 0.5 s,
configurable) so fallback spacing cannot pull that interior defender into
a fallback micro-gap.

CONTRACTS (from the task directive)
-----------------------------------
  - GRACE PHASES: the grace is limited to DRIVE/PTB phases
    (``is_drive_phase`` styles: open_play + every TES phase, plus PTB_*).
  - INVALIDATION: the grace is invalidated on large ruck displacement
    (``secondary_grace_invalid_ruck_move_m`` in x or y) or explicit
    RELEASE, so markers and spacing remain responsive to legitimate rapid
    changes.
  - TELEMETRY: ``world.secondary_grace_applied_count`` increments per arm;
    ``world.secondary_grace_persistent_count`` increments when a grace
    expires but the defender is STILL PRIMARY/SECONDARY (persistent
    eligibility beyond the grace window).  A ``[SECONDARY-GRACE]`` audit
    line is printed per event.
  - CANARY: gated behind ``secondary_grace_enabled`` (default False).
    Flipping it off restores the byte-identical legacy width / spacing
    path.  The module is a pure additive decoration; no existing width
    predicate signature breaks.

STATE
-----
Every defender carries only ``_secondary_grace_*`` attributes (never
``depth_class`` / ``depth_role`` / ``depth_side`` / ``target_x`` /
``target_y``), so the width / spacing guards (which snapshot the
protected depth + X/Y fields) never see a mutation from this module.

    _secondary_grace_prev_class   last-seen depth_class (transition detect)
    _secondary_grace_remaining_s  grace timer (seconds)
    _secondary_grace_ruck_y       ruck_y at arm time (invalidation anchor)
    _secondary_grace_ruck_x       ruck_x at arm time (invalidation anchor)
    _secondary_grace_applied      True from arm until expiry/invalidation
    _secondary_grace_expired      True when grace expired while still engaged
"""

from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────
# PHASE SETS
# ─────────────────────────────────────────────────────────────────────

# DRIVE/PTB phases during which the grace is honoured.  Mirrors the
# drive-phase families used by defensive_width.is_drive_phase (OPEN_PLAY +
# every TES phase) extended with the PTB family, per the task directive.
_GRACE_PHASES: frozenset = frozenset({
    "open_play",
    "tackle_init", "tackle_contact", "tackle_drive", "tackle_dominance",
    "ptb_ready", "ptb_prep", "ptb_exec", "ptb",
})

# Explicit-RELEASE phases that invalidate the grace (legitimate rapid
# change — markers and spacing must respond immediately).
_RELEASE_PHASES: frozenset = frozenset({
    "release", "tackle_release",
})

# Roles that never hold a width-lane slot (markers / DH / tacklers /
# chase).  The grace never arms for these — their own writers own their
# targets, and they are exempt from width eligibility by role anyway.
_EXEMPT_ROLES: frozenset = frozenset({
    "MARKER_A", "MARKER_B", "DUMMY_HALF",
    "TACKLER_1", "TACKLER_2", "TACKLER_3",
    "DEF_CHASE", "KICK_CHASE",
})

# The engageable depth classes that trigger the grace exemption in
# ``width_authority.is_width_eligible``.
_ENGAGEABLE_CLASSES: frozenset = frozenset({"PRIMARY", "SECONDARY"})


# ─────────────────────────────────────────────────────────────────────
# PHASE HELPERS
# ─────────────────────────────────────────────────────────────────────

def _phase_names(world) -> frozenset:
    """Return the set of lower-cased phase names for *world*.

    Reads both ``world.sub_state`` (enum with a ``value``) and
    ``world._ope_phase_label`` (the granular OPE phase label), so a
    coarse sub-state and a granular label are BOTH considered.  This is
    the same dual-source projection ``width_authority.is_open_play`` /
    ``defensive_depth_roles`` use for the phase gate.
    """
    names: set = set()
    sub = getattr(world, "sub_state", None)
    if sub is not None:
        names.add(str(getattr(sub, "value", sub)).lower())
    label = getattr(world, "_ope_phase_label", None)
    if label is not None:
        names.add(str(label).lower())
    return frozenset(names)


def is_grace_phase(world) -> bool:
    """True during DRIVE/PTB phases (the grace honour window)."""
    return bool(_phase_names(world) & _GRACE_PHASES)


def is_explicit_release(world) -> bool:
    """True during an explicit RELEASE (invalidation event)."""
    return bool(_phase_names(world) & _RELEASE_PHASES)


# ─────────────────────────────────────────────────────────────────────
# RUCK-ADJACENCY
# ─────────────────────────────────────────────────────────────────────

def is_ruck_adjacent(d, world, tuners) -> bool:
    """True when *d* is within ``secondary_grace_ruck_adjacent_m`` of ruck_y.

    The ruck-adjacent band covers the ruck-adjacent A pivot (±2 m) plus
    the interior B lane, so a freshly-promoted interior defender that
    sits tight to the ruck is captured.
    """
    ruck_y = float(getattr(world, "ruck_y_m", 35.0))
    threshold = float(getattr(
        tuners, "secondary_grace_ruck_adjacent_m", 4.0,
    ))
    return abs(float(d.y) - ruck_y) <= threshold


# ─────────────────────────────────────────────────────────────────────
# ACTIVE GRACE PREDICATE — consulted by width_authority
# ─────────────────────────────────────────────────────────────────────

def secondary_grace_active(d, world, tuners) -> bool:
    """True when *d* holds an active width-eligibility grace.

    Requires ALL of:
      - ``tuners.secondary_grace_enabled`` (canary gate)
      - ``is_grace_phase(world)`` (DRIVE/PTB only)
      - ``_secondary_grace_remaining_s > 0`` (timer live)

    This is the predicate ``width_authority.is_width_eligible`` consults
    to waive the PRIMARY/SECONDARY depth-class exemption.
    """
    if d is None or world is None or tuners is None:
        return False
    if not bool(getattr(tuners, "secondary_grace_enabled", False)):
        return False
    if not is_grace_phase(world):
        return False
    remaining = float(getattr(d, "_secondary_grace_remaining_s", 0.0) or 0.0)
    return remaining > 0.0


# ─────────────────────────────────────────────────────────────────────
# ADVANCE — clock tick + invalidation
# ─────────────────────────────────────────────────────────────────────

def advance_secondary_grace(world, defenders, tuners) -> None:
    """Advance every grace clock by *dt*; invalidate on ruck move / RELEASE.

    When ``secondary_grace_enabled`` is False, EVERY grace timer is
    cleared immediately (flag off = no grace ever), so a stale positive
    timer can never resurrect on a later flag re-flip.

    Invalidation:
      - large ruck displacement: the ruck moved more than
        ``secondary_grace_invalid_ruck_move_m`` in x OR y from the arm
        anchor  ->  the grace is cleared (legitimate rapid change).
      - explicit RELEASE (``is_explicit_release``)  ->  cleared.

    Natural expiry: when the timer crosses 0 while the defender is STILL
    PRIMARY/SECONDARY, ``_secondary_grace_expired`` is stamped and
    ``world.secondary_grace_persistent_count`` is incremented (the
    persistent-eligibility telemetry).  The grace is NOT re-armed for
    that engagement.
    """
    if not bool(getattr(tuners, "secondary_grace_enabled", False)):
        for d in defenders or []:
            if d is not None:
                d._secondary_grace_remaining_s = 0.0
        return

    dt = float(getattr(world, "dt", 1.0 / 60.0))
    ruck_y = float(getattr(world, "ruck_y_m", 35.0))
    ruck_x = float(getattr(world, "ruck_x_m", 60.0))
    invalid = float(getattr(tuners, "secondary_grace_invalid_ruck_move_m", 2.0))
    release = is_explicit_release(world)

    for d in defenders or []:
        if d is None:
            continue
        remaining = float(getattr(d, "_secondary_grace_remaining_s", 0.0) or 0.0)
        if remaining <= 0.0:
            continue

        # ── INVALIDATION (large ruck displacement / explicit release) ──
        armed_y = getattr(d, "_secondary_grace_ruck_y", None)
        armed_x = getattr(d, "_secondary_grace_ruck_x", None)
        cleared = False
        if armed_y is not None and abs(ruck_y - float(armed_y)) > invalid:
            cleared = True
        elif armed_x is not None and abs(ruck_x - float(armed_x)) > invalid:
            cleared = True
        elif release:
            cleared = True
        if cleared:
            d._secondary_grace_remaining_s = 0.0
            d._secondary_grace_applied = False
            continue

        # ── NATURAL TICK ──
        new_remaining = max(0.0, remaining - dt)
        d._secondary_grace_remaining_s = new_remaining
        if new_remaining <= 0.0 and remaining > 0.0:
            # Grace just expired while this defender was still engaged —
            # persistent eligibility beyond the grace window.
            cur_class = getattr(d, "depth_class", "PASSIVE") or "PASSIVE"
            if cur_class in _ENGAGEABLE_CLASSES:
                d._secondary_grace_expired = True
                d._secondary_grace_applied = False
                world.secondary_grace_persistent_count = (
                    getattr(world, "secondary_grace_persistent_count", 0) + 1
                )
                print(
                    f"[SECONDARY-GRACE] f={getattr(world, 'frame_count', 0)} "
                    f"persistent-eligibility id={getattr(d, 'player_id', '?')} "
                    f"role={getattr(d, 'depth_role', None) or '-'} "
                    f"class={cur_class}"
                )


# ─────────────────────────────────────────────────────────────────────
# ARM — detect fresh promotions while ruck-adjacent
# ─────────────────────────────────────────────────────────────────────

def arm_secondary_grace(world, defenders, tuners) -> None:
    """Arm a grace for freshly-promoted ruck-adjacent PRIMARY/SECONDARY.

    The arm scan runs in ``defensive_pipeline.run_defensive_pipeline``
    (the per-frame preamble) AFTER ``assign_depth_roles`` and BEFORE the
    spacing / width recompute.  It uses each defender's own
    ``_secondary_grace_prev_class`` snapshot to detect a fresh
    PASSIVE -> PRIMARY/SECONDARY transition:

      - first sight (``_secondary_grace_prev_class`` None): only record
        the snapshot, NEVER arm (canary-safe on flag flip — no immediate
        change; only new promotions going forward are graced).
      - leaving engagement: clear the grace + reset flags.
      - fresh promotion: arm ONLY when the flag is on, the phase is a
        grace phase, the defender is ruck-adjacent, AND the defender has
        not already expired a grace in this engagement (persistence
        suppression — no re-arm after expiry).
      - both engaged: keep the running timer (never re-arm).

    TELEMETRY: on each arm increments ``world.secondary_grace_applied_count``
    and prints ``[SECONDARY-GRACE] applied ...``.
    """
    if not bool(getattr(tuners, "secondary_grace_enabled", False)):
        return

    grace_phase = is_grace_phase(world)
    duration = float(getattr(tuners, "secondary_grace_duration_s", 0.5))
    ruck_y = float(getattr(world, "ruck_y_m", 35.0))
    ruck_x = float(getattr(world, "ruck_x_m", 60.0))

    for d in defenders or []:
        if d is None:
            continue
        if getattr(d, "fullback_role_active", False):
            continue
        role = getattr(d, "defender_role", None)
        role_name = getattr(role, "name", role)
        if role_name in _EXEMPT_ROLES:
            # Keep the snapshot fresh even for exempt bodies (they can
            # convert to line bodies later).
            d._secondary_grace_prev_class = (
                getattr(d, "depth_class", "PASSIVE") or "PASSIVE"
            )
            continue

        prev_class = getattr(d, "_secondary_grace_prev_class", None)
        cur_class = getattr(d, "depth_class", "PASSIVE") or "PASSIVE"

        # ── FIRST SIGHT: record snapshot, no arm ──
        if prev_class is None:
            d._secondary_grace_prev_class = cur_class
            continue

        was_engaged = prev_class in _ENGAGEABLE_CLASSES
        now_engaged = cur_class in _ENGAGEABLE_CLASSES

        if was_engaged and not now_engaged:
            # Left engagement — clear grace + reset the persistent flags.
            d._secondary_grace_remaining_s = 0.0
            d._secondary_grace_applied = False
            d._secondary_grace_expired = False
        elif (not was_engaged) and now_engaged:
            # FRESH PROMOTION.  Arm only when:
            #   - grace phase (DRIVE/PTB)
            #   - ruck-adjacent
            #   - not already expired in this engagement (no re-arm)
            if (
                grace_phase
                and not getattr(d, "_secondary_grace_expired", False)
                and is_ruck_adjacent(d, world, tuners)
            ):
                d._secondary_grace_remaining_s = duration
                d._secondary_grace_ruck_y = ruck_y
                d._secondary_grace_ruck_x = ruck_x
                d._secondary_grace_applied = True
                d._secondary_grace_expired = False
                world.secondary_grace_applied_count = (
                    getattr(world, "secondary_grace_applied_count", 0) + 1
                )
                print(
                    f"[SECONDARY-GRACE] f={getattr(world, 'frame_count', 0)} "
                    f"applied id={getattr(d, 'player_id', '?')} "
                    f"role={getattr(d, 'depth_role', None) or '-'} "
                    f"class={cur_class} t={duration:.3f}s"
                )
        # else (both engaged): keep the running timer; expired stays
        # suppressed (persistence suppression).

        d._secondary_grace_prev_class = cur_class
