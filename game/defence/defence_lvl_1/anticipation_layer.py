"""
anticipation_layer.py — Phase-gated, role-aware defensive anticipation layer
(Stage 9, 2026-09-05).

PURPOSE
-------
The defensive line currently reacts to the ball carrier only AFTER the
carrier has already moved laterally (the spacing chain / band pull follow
the live position).  This module adds a CONTROLLED LATERAL PRE-SHIFT toward
the PREDICTED ball / threat lane during OPEN_PLAY and TES, so the line gets
EARLY movement without destabilising spacing or causing winger drift.

    Phase-gated        : only applies during the drive
                         (``is_drive_phase`` — OPEN_PLAY + every TES phase).
    Role-aware         : each depth role (A/B/C/D/CENTRE/WINGER) carries a
                         per-role gain; the interior engageable band (A/B)
                         shifts furthest, WINGER barely moves (0.15).
    Band-limited       : only defenders whose base spacing Y is within
                         ``anticipation_band_m`` of the threat lane shift at
                         all; the shift falls off linearly to zero at the
                         band edge.
    Slot-hierarchy     : the shift is clamped to the midpoint between the
                         defender's base lane and its adjacent outer/inner
                         lane, so no defender ever crosses its neighbour
                         (no role inversion, no WINGER drifting past CENTRE).

SPACING AUTHORITY CONTRACT
--------------------------
The anticipation layer is a Y-only DECORATION of the split-spacing Y.  It
reads the spacing dict that ``compute_targets`` produced and returns a new
dict with a bounded, ordering-preserving lateral delta applied.  It never
writes ``target_x`` / ``target_y`` (the pipeline / movement writers own
those), and it never mutates the spacing chain itself — the chain/lane
model stays the pure single-offset authority.  Width (X) is untouched.

WIRING
------
``defensive_pipeline.run_defensive_pipeline`` calls
``apply_anticipation_pre_shift`` immediately after ``compute_targets`` +
``SpacingGuard.verify`` and before the width recompute + publish, so the
shifted Y flows into ``frame_spacing_targets`` / ``d.frame_spacing_y`` —
the single Y authority every downstream consumer (retreat, close, MMDL,
movement) reads.

THE PREDICTED THREAT LANE
-------------------------
``threat_lane_y`` projects the ball carrier's lateral motion over a short
horizon:

    threat_y = bc.y + bc.vy * anticipation_lead_time_s

clamped to the sideline margins.  This is deterministic, always available
in OPEN_PLAY / TES (a live carrier is required by ``is_drive_phase``), and
independent of the dark-safe (OFF by default) attack-anticipation system.
"""

from __future__ import annotations

import math

from game.defence.defence_lvl_1.defensive_width import (
    depth_side_of,
    clamp_to_sideline,
    is_drive_phase,
)

# The depth roles that participate in the anticipation chain — the same
# roles the spacing chain lays out (A/B/C/D/CENTRE/WINGER).  Markers / DH /
# tacklers / fullback are not line bodies and never carry a chain slot.
_ANTICIPATION_ROLES = frozenset(("A", "B", "C", "D", "CENTRE", "WINGER"))

# MINIMUM LANE SEPARATION (2026-09-12).  The slot-hierarchy clamp stops a
# defender crossing its neighbour, but the two ADJACENT defenders share
# the SAME midpoint: the outer body may approach to ``(base_i +
# base_{i+1}) / 2`` and the inner body may approach to the same value, so
# a pair shifting toward the threat lane simultaneously COLLAPSED onto one
# lane (identical Y -> short-side overlap / duplication, and the outer
# pair compressed to a ~0.1 m gap).  The approach is therefore limited to
# the midpoint MINUS half this floor, so two neighbours can never end up
# closer than ``ANTICIPATION_MIN_LANE_SEP_M``.
ANTICIPATION_MIN_LANE_SEP_M: float = 0.5

# Diagnostic stamps written on each processed defender.
_ANTICIPATION_STAMP_ATTRS = (
    "anticipation_shift_m",
    "anticipation_threat_lane_y",
    "anticipation_active",
    "anticipation_target_y",
    "anticipation_authority",
    "_anticipation_since_frame",
    "_anticipation_since_time_s",
)


# ─────────────────────────────────────────────────────────────────────
# PREDICTED THREAT LANE
# ─────────────────────────────────────────────────────────────────────

def threat_lane_y(world, bc, tuners) -> float:
    """Return the predicted ball / threat lane Y (velocity lead projection).

    ``threat_y = bc.y + bc.vy * anticipation_lead_time_s``, clamped to the
    sideline margins.  The horizon is a tuner so a sprinting / laterally
    jinking carrier pulls the threat lane ahead of its current Y, giving
    the line something to pre-shift toward.

    When the tuner or the BC fields are absent (verifier stubs), the lead
    degrades safely to the carrier's current Y (lead 0.0).
    """
    lead = float(getattr(tuners, "anticipation_lead_time_s", 0.35) or 0.0)
    bc_y = float(getattr(bc, "y", 0.0) or 0.0)
    bc_vy = float(getattr(bc, "vy", 0.0) or 0.0)
    return clamp_to_sideline(bc_y + bc_vy * lead, tuners)


# ─────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────

def _clear_anticipation_stamps(defenders) -> None:
    """Reset the anticipation diagnostic stamps on every defender."""
    for d in defenders:
        if d is None:
            continue
        d.anticipation_shift_m = 0.0
        d.anticipation_threat_lane_y = None
        d.anticipation_active = False
        d.anticipation_target_y = None
        d.anticipation_authority = False
        d._anticipation_since_frame = None
        d._anticipation_since_time_s = None


def _stamp(d, shift: float, threat_y: float, active: bool,
           target_y: float | None = None, authority: bool = False,
           frame=None, time_s: float | None = None) -> None:
    """Write the anticipation diagnostic stamps on *d*.

    ``target_y`` is the authoritative anticipated Y (unclamped by any
    downstream writer); ``authority`` marks the defender as inside the
    anticipation band (the boundary writers must not snap it off);
    ``frame`` / ``time_s`` record WHEN it was stamped so the authority
    window can decay.
    """
    d.anticipation_shift_m = float(shift)
    d.anticipation_threat_lane_y = float(threat_y)
    d.anticipation_active = bool(active)
    d.anticipation_target_y = float(target_y) if target_y is not None else None
    d.anticipation_authority = bool(authority)
    d._anticipation_since_frame = frame
    d._anticipation_since_time_s = (
        float(time_s) if time_s is not None else None
    )


def _side_defenders(defenders, ruck_y: float, side: int) -> list:
    """Return the anticipation-chain defenders on *side* of the ruck."""
    return [
        d for d in defenders
        if d is not None
        and getattr(d, "depth_role", None) in _ANTICIPATION_ROLES
        and depth_side_of(d, ruck_y) == side
    ]


def _order_outer_inner(side_defs, ruck_y: float, base_spacing: dict) -> list:
    """Order *side_defs* OUTER→INNER (WINGER…A) by base spacing distance.

    The outer boundary is the lane furthest from ruck_y (WINGER); the
    inner boundary is the lane closest to ruck_y (A).  The slot-hierarchy
    clamp needs this order so each defender's ``outer`` / ``inner``
    neighbour lanes are unambiguous.  ``base_spacing`` is the UN-shifted
    spacing dict (the original ``compute_targets`` output), so the order
    never depends on the shift itself.
    """
    def _key(d):
        base_y = float(base_spacing.get(
            id(d), getattr(d, "y", ruck_y),
        ))
        return (abs(base_y - float(ruck_y)), getattr(d, "player_id", 0))
    return sorted(side_defs, key=_key, reverse=True)


def _slot_clamp(base_y: float, raw_shift: float, base_lanes: list, i: int) -> float:
    """Clamp *raw_shift* so the candidate cannot cross a neighbour midpoint.

    ``base_lanes`` is the side's UN-shifted lane values in OUTER→INNER
    order (index 0 = WINGER, last = A).  For defender at index *i*, the
    outer neighbour lane is ``base_lanes[i-1]`` (if any) and the inner
    neighbour lane is ``base_lanes[i+1]`` (if any).  The shift is
    prevented from crossing the midpoint between the defender's base lane
    and whichever neighbour lies on the shift's side of *base_y* — so the
    defender can move TOWARD the threat but never PAST its neighbour
    (no role inversion / no winger drift past CENTRE).

    Returns the clamped ``base_y + clamped_shift``.
    """
    candidate = base_y + raw_shift
    # SEPARATION HALO (2026-09-12): the midpoint is SHARED by the two
    # adjacent defenders, so a bare midpoint clamp let BOTH of them settle
    # on the same value — identical Y, the short-side overlap /
    # duplication.  Reserve half the floor on each side of the midpoint so
    # two neighbours can approach no closer than
    # ANTICIPATION_MIN_LANE_SEP_M.  The guard keeps the candidate between
    # the base lane and the haloed midpoint, so the shift never reverses.
    halo = float(ANTICIPATION_MIN_LANE_SEP_M) * 0.5
    for nb in (None if i <= 0 else base_lanes[i - 1],
               None if i >= len(base_lanes) - 1 else base_lanes[i + 1]):
        if nb is None:
            continue
        m = (base_y + float(nb)) * 0.5
        if raw_shift > 0.0 and m > base_y:
            candidate = min(candidate, max(base_y, m - halo))
        elif raw_shift < 0.0 and m < base_y:
            candidate = max(candidate, min(base_y, m + halo))
    return candidate


# ─────────────────────────────────────────────────────────────────────
# ANTICIPATION AUTHORITY WINDOW (2026-09-08)
# ─────────────────────────────────────────────────────────────────────
# The anticipation pre-shift lands in the frame spacing dict as a Y-only
# decoration, but the boundary writers that follow (tackler-commit,
# pursuit-commit, pre-tackle authority / depth-lock) re-write ``target_y``
# over it — the "lean then snap" symptom.  These helpers give the lean a
# dedicated AUTHORITY WINDOW so it survives until the defender genuinely
# commits or TES engages.

def _anticipation_elapsed_s(world, d) -> float | None:
    """Return the wall-clock (or frame-derived) seconds since the stamp.

    Prefers the world's ``sim_time_s`` clock (tick-rate independent); falls
    back to the frame counter × dt for verifier stubs that only carry a
    frame count.  Returns None when neither clock is available.
    """
    since_time = getattr(d, "_anticipation_since_time_s", None)
    sim_t = getattr(world, "sim_time_s", None)
    if since_time is not None and sim_t is not None:
        return max(0.0, float(sim_t) - float(since_time))
    since_frame = getattr(d, "_anticipation_since_frame", None)
    frame_now = getattr(world, "frame_count", None)
    if since_frame is not None and frame_now is not None:
        dt = float(getattr(world, "dt", 1.0 / 60.0) or 1.0 / 60.0)
        return max(0.0, (float(frame_now) - float(since_frame)) * dt)
    return None


def anticipation_authority_active(world, d, tuners) -> bool:
    """True when *d* is still under the anticipation authority window.

    The lean is authoritative only while ALL of these hold:
      - the master gate + drive phase are live (``is_drive_phase``)
      - ``d`` carries the ``anticipation_authority`` stamp
      - the decay window has not elapsed
      - ``d`` is NOT a committed tackler (``_tackle_commit_active``)
      - ``d`` is NOT pursuit-committed (``_pursuit_commit_active``)
      - ``d`` is NOT a TES-managed / depth-locked body

    Yields COMPLETELY once pre-tackle or TES depth-lock engages (the
    tackle chamber owns lateral), per the 2026-09-08 directive.
    """
    if not bool(getattr(tuners, "anticipation_enabled", True)):
        return False
    if not is_drive_phase(world):
        return False
    if not bool(getattr(d, "anticipation_authority", False)):
        return False
    if getattr(d, "_tackle_commit_active", False):
        return False
    if getattr(d, "_pursuit_commit_active", False):
        return False
    # ── ARMING YIELD (2026-09-10) ──
    # Once the defender breaches the pre-tackle trigger radius, the lean must
    # yield: the lean re-blends target_y toward the predicted lane, but the
    # slot gate that ARMS the tackle reads target_y.  A leaned target_y drags
    # the body out of the slot band and suppresses the tackle ("anticipation
    # not arming").  Yield so target_y reverts to the un-leaned reference the
    # instant arming is imminent — this is the reset the boundary writers
    # need before the slot gate reads the slot reference.
    _bc = getattr(world, "ball_carrier", None)
    if _bc is not None:
        _trigger = float(getattr(tuners, "pre_tackle_trigger_radius_m", 2.0))
        if _trigger > 0.0:
            _dist = math.hypot(
                float(getattr(d, "x", 0.0) or 0.0)
                - float(getattr(_bc, "x", 0.0) or 0.0),
                float(getattr(d, "y", 0.0) or 0.0)
                - float(getattr(_bc, "y", 0.0) or 0.0),
            )
            if _dist <= _trigger:
                return False
    if getattr(d, "frozen", False):
        return False
    if float(getattr(d, "freeze_timer", 0.0) or 0.0) > 0.0:
        return False
    # TES / pre-tackle / depth-lock yield (tackle chamber owns lateral).
    if getattr(world, "tes_active", False):
        return False
    if getattr(d, "depth_locked", False):
        return False
    try:
        from game.defence.defence_lvl_1.pre_tackle_authority import (
            is_pre_tackle_managed as _pta_managed,
        )
        if _pta_managed(d):
            return False
    except Exception:
        pass
    # Decay window.
    elapsed = _anticipation_elapsed_s(world, d)
    if elapsed is None:
        return False
    decay_s = float(getattr(tuners, "anticipation_authority_decay_s", 0.5))
    if decay_s > 0.0 and elapsed > decay_s:
        return False
    # A stale target is never authoritative.
    target = getattr(d, "anticipation_target_y", None)
    if target is None:
        return False
    return True


def apply_anticipation_authority(world, d, tuners, dt: float | None = None) -> bool:
    """Reassert the anticipation lean on *d*'s ``target_y``.

    Blends ``target_y`` toward ``anticipation_target_y`` with a bounded
    per-frame delta (mirrors the ``FINALISE_BLEND`` / ``FINALISE_MAX_DELTA_M``
    pattern) so the boundary writers cannot instantly snap the lean off.
    Called AFTER the step-10a1 commit/pursuit writers and BEFORE the
    pre-tackle authority, so the lean is the final Y the movement layer
    integrates for an authority-active, non-committed defender.

    Returns True when the authority applied a blend this frame.
    """
    if not anticipation_authority_active(world, d, tuners):
        return False
    target_y = float(getattr(d, "anticipation_target_y"))
    # Stable, finite, sideline-legal target only.
    if not math.isfinite(target_y):
        return False
    target_y = clamp_to_sideline(target_y, tuners)

    blend = float(getattr(tuners, "anticipation_authority_blend", 0.4))
    blend = max(0.0, min(1.0, blend))
    max_delta = float(getattr(
        tuners, "anticipation_authority_max_delta_m", 0.4,
    ) or 0.0)

    current = float(getattr(d, "target_y", target_y))
    desired = current + (target_y - current) * blend
    delta = desired - current
    if max_delta > 0.0 and abs(delta) > max_delta:
        delta = math.copysign(max_delta, delta if abs(delta) > 1e-12 else 1.0)
    d.target_y = current + delta
    d.anticipation_authority_applied = True

    # ── ANGLE LOCK (2026-09-09) ──
    # The defender LEANS toward the predicted threat lane (target_y) but the
    # downstream writers re-face it toward the LIVE ball carrier frame-to-frame,
    # so the engage angle (pretackle_machine.approach_angle_from_facing) and the
    # visible posture wobble.  While the authority is live, lock the facing
    # toward the PREDICTED threat point (bc.x, target_y) — the point the line
    # leaned toward — so the head-on/side/rear band is stable through the lean
    # and matches the movement direction.
    bc = getattr(world, "ball_carrier", None)
    if bc is not None:
        _ref_x = float(getattr(bc, "x", 0.0) or 0.0)
        _ref_y = target_y
        _angle = math.atan2(
            _ref_y - float(getattr(d, "y", 0.0) or 0.0),
            _ref_x - float(getattr(d, "x", 0.0) or 0.0),
        )
        try:
            d.set_facing(_angle)
            d.facing_override = True
        except Exception:
            pass
    return True


def _resolve_slowdown_tuners(world, tuners):
    """Resolve the defence tuners for the slowdown-anchor gate.

    ``intercept_tacklers.apply_pre_tackle_stance`` only holds the world and
    calls ``anticipation_slowdown_point`` with ``tuners=None``.  The anchor
    must still read the SAME tuner object the pipeline uses, otherwise the
    ``anticipation_enabled`` gate read below would silently default to True
    and the anchor would not follow the gate.

    Falls back to ``world.open_play_engine.def_tuners`` / ``world.open_play``
    (the canonical owners of the defence tuners) and returns None when
    neither is available -- the gate then defaults to enabled, preserving the
    pre-existing behaviour exactly.
    """
    if tuners is not None:
        return tuners
    ope = getattr(world, "open_play_engine", None)
    if ope is None:
        ope = getattr(world, "open_play", None)
    if ope is None:
        return None
    return getattr(ope, "def_tuners", None)


def anticipation_slowdown_point(world, bc, tuners=None, d=None) -> tuple[float, float]:
    """Return the (x, y) anchor for the modernised pre-tackle slowdown band.

    ``tuners.anticipation_slowdown_anchor`` (also published to
    ``world.pre_tackle_slowdown_anchor`` by the lane-tackler selector):
      - ``"bc_live"``      -> (bc.x, bc.y)  [legacy raw-BC behaviour]
      - ``"bc_predicted"`` -> (bc.x + bc.vx*lead, bc.y + bc.vy*lead)
                              [default — the point the line leaned toward]
      - ``"role_depth_x"`` -> (bc.x + WIDTH_OFFSETS[role], bc.y)
                              [the role-depth arrival cell]

    The Y is clamped to the sideline margins; the X is left un-clamped so a
    sprinting carrier can pull the point up-field.
    """
    anchor = getattr(world, "pre_tackle_slowdown_anchor", None)
    if anchor is None:
        anchor = getattr(tuners, "anticipation_slowdown_anchor", "bc_predicted")

    bx = float(getattr(bc, "x", 0.0) or 0.0)
    by = float(getattr(bc, "y", 0.0) or 0.0)

    # -- ANCHOR FOLLOWS THE GATE (2026-09-15) --
    # The projected anchors exist to measure the taper against "the point
    # the line LEANED TOWARD".  The anticipation layer is disabled by
    # default (anticipation_enabled False - the even-ladder rework), so
    # nothing leans and the projection is a phantom: the lead
    # (bc.v * anticipation_lead_time_s, ~2.5 m at running speed, and the
    # role-depth offset more still) is ADDED to the distance the taper
    # measures.  That consumes most of the slowdown band and collapses the
    # deceleration into the last fraction of a metre - the "no slowdown
    # before the tackle" symptom.
    #
    # With no lean, the honest distance signal is the LIVE carrier, which
    # restores the full-band taper.  When the layer is re-enabled
    # (anticipation_enabled True) the configured anchor is honoured again,
    # completely unchanged.
    #
    # Only the PROJECTED anchors are affected: an explicit "bc_live" already
    # returns the live carrier below, so this is an identity for it.
    _gate = _resolve_slowdown_tuners(world, tuners)
    if not bool(getattr(_gate, "anticipation_enabled", True)):
        if anchor != "bc_live":
            return bx, by

    if anchor == "bc_live":
        return bx, by
    if anchor == "role_depth_x" and d is not None:
        role = getattr(d, "depth_role", "D") or "D"
        offsets = getattr(tuners, "WIDTH_OFFSETS", None) or {}
        off = float(offsets.get(role, offsets.get("D", 5.0)))
        return bx + off, clamp_to_sideline(by, tuners)
    # default: bc_predicted
    lead = float(getattr(tuners, "anticipation_lead_time_s", 0.35) or 0.0)
    bvx = float(getattr(bc, "vx", 0.0) or 0.0)
    bvy = float(getattr(bc, "vy", 0.0) or 0.0)
    return bx + bvx * lead, clamp_to_sideline(by + bvy * lead, tuners)


def anticipation_facing_y(world, d) -> float | None:
    """Return the Y the defender should face under the anticipation authority.

    During the anticipation authority window the defender LEANS toward the
    predicted threat lane (``anticipation_target_y``), but the downstream
    writers re-face it toward the LIVE ball carrier frame-to-frame, so the
    engage angle (``pretackle_machine.approach_angle_from_facing``) and the
    visible posture wobble.  This helper returns the authoritative facing Y
    while the lean is live: the anticipated Y (clamped to the sideline).
    Returns None when the defender is not under the authority (the caller
    falls back to the live BC Y).
    """
    ant_y = getattr(d, "anticipation_target_y", None)
    if ant_y is None:
        return None
    if not bool(getattr(d, "anticipation_authority", False)):
        return None
    return float(ant_y)


# ─────────────────────────────────────────────────────────────────────
# MAIN ENTRY — apply the phase-gated pre-shift
# ─────────────────────────────────────────────────────────────────────

def apply_anticipation_pre_shift(
    world,
    defenders,
    tuners,
    spacing_targets: dict,
) -> dict:
    """Apply the phase-gated, role-aware lateral pre-shift to the spacing Y.

    PHASE GATE: the layer is a NO-OP (returns ``spacing_targets``
    unchanged, clears stamps) unless ``anticipation_enabled`` AND
    ``is_drive_phase(world)`` (OPEN_PLAY + TES) AND a live ball carrier is
    present.  Outside the drive the line is reforming / retreating and the
    anticipation is not armed.

    PER-DEFENDER SHIFT:
        threat_y   = threat_lane_y(world, bc, tuners)
        lat        = threat_y - base_y
        proximity  = max(0, 1 - |lat| / anticipation_band_m)   (band-limited)
        raw_shift  = sign(lat) * anticipation_max_gain_m
                     * anticipation_role_gain[depth_role]
                     * proximity
        shifted_y  = slot_clamp(base_y, raw_shift, side_lanes, side_idx)
        shifted_y  = clamp_to_sideline(shifted_y, tuners)

    The ``slot_clamp`` enforces the slot hierarchy: the shifted Y is kept
    between the midpoints to the defender's outer and inner lanes, so no
    two adjacent roles ever invert.  Bounded by ``anticipation_max_gain_m``
    and band-limited to ``anticipation_band_m``.

    Returns a NEW dict ``{id(d): shifted_y}``.  Also stamps
    ``anticipation_shift_m`` / ``anticipation_threat_lane_y`` /
    ``anticipation_active`` on each processed defender for the verifier.
    """
    bc = getattr(world, "ball_carrier", None)

    # ── PHASE GATE ──
    if not bool(getattr(tuners, "anticipation_enabled", True)):
        _clear_anticipation_stamps(list(defenders))
        return spacing_targets
    if bc is None or not is_drive_phase(world):
        _clear_anticipation_stamps(list(defenders))
        return spacing_targets

    ruck_y = float(getattr(world, "ruck_y_m", 35.0))
    threat_y = threat_lane_y(world, bc, tuners)
    band = float(getattr(tuners, "anticipation_band_m", 8.0) or 0.0)
    max_gain = float(getattr(tuners, "anticipation_max_gain_m", 1.5) or 0.0)
    gain_table = getattr(tuners, "anticipation_role_gain", None) or {}
    frame_now = getattr(world, "frame_count", None)
    time_now = getattr(world, "sim_time_s", None)

    # New dict; base lanes snapshot from the ORIGINAL spacing so the
    # slot-hierarchy clamp orders against the un-shifted geometry.
    new_spacing = dict(spacing_targets)
    for side in (-1, 1):
        side_defs = _side_defenders(defenders, ruck_y, side)
        if not side_defs:
            continue
        ordered = _order_outer_inner(side_defs, ruck_y, spacing_targets)
        base_lanes = [
            float(spacing_targets.get(id(d), getattr(d, "y", ruck_y)))
            for d in ordered
        ]
        for i, d in enumerate(ordered):
            base_y = base_lanes[i]
            lat = threat_y - base_y
            if abs(lat) <= 1e-9:
                # Exactly on the threat lane: no lean needed, but still
                # in-band (authority target == base lane).
                _stamp(d, 0.0, threat_y, True,
                       target_y=base_y, authority=True,
                       frame=frame_now, time_s=time_now)
                continue
            sign = 1.0 if lat > 0.0 else -1.0
            dist = abs(lat)
            # BAND-LIMIT: no shift beyond the band; linear falloff inside.
            if band > 0.0 and dist >= band:
                # Out-of-band: NO authority (the lean does not apply here).
                _stamp(d, 0.0, threat_y, True,
                       target_y=base_y, authority=False,
                       frame=frame_now, time_s=time_now)
                continue
            proximity = 1.0 if band <= 0.0 else max(0.0, 1.0 - dist / band)

            role = getattr(d, "depth_role", "D") or "D"
            role_gain = float(gain_table.get(role, 0.5))
            raw_shift = sign * max_gain * role_gain * proximity

            # ── GAP-PRESERVING CLOSURE CAP (2026-09-12) ──
            # The lean may close at most ``anticipation_max_closure_fraction``
            # of the base gap to the neighbour it leans TOWARD.  Two
            # neighbours leaning at each other therefore retain at least
            # ``1 - 2 * fraction`` of their count-lane gap, so the lean can
            # never halve the line spacing.  This is the mechanism behind the
            # far-edge winger compression / adjacent-defender overlap symptom:
            # the interior engageable band (A/B) leans furthest, and when the
            # threat lane sits BETWEEN two neighbours they converge from both
            # sides — a 4.0 m count-lane step closed to 1.27 m.  The cap
            # preserves the lean (early movement) without letting it replace
            # the count-lane geometry.
            closure = float(getattr(
                tuners, "anticipation_max_closure_fraction", 0.20,
            ) or 0.0)
            nb_y = None
            # NEIGHBOUR BY LANE VALUE, NOT ARRAY INDEX (2026-09-12): the
            # side list is ordered by DISTANCE FROM THE RUCK, so array index
            # order is not y order and it flips between the left and right
            # sides.  Selecting the neighbour by index therefore capped
            # against the wrong body and the pull never bound.  Pick the
            # adjacent lane that lies on the shift's side of base_y.
            for j in (i - 1, i + 1):
                if j < 0 or j >= len(base_lanes):
                    continue
                _nb = float(base_lanes[j])
                if sign > 0.0 and _nb > base_y:
                    nb_y = _nb
                    break
                if sign < 0.0 and _nb < base_y:
                    nb_y = _nb
                    break
            if nb_y is not None:
                gap = abs(nb_y - base_y)
                cap = gap * closure
                if abs(raw_shift) > cap:
                    raw_shift = math.copysign(cap, sign)

            # SLOT-HIERARCHY CLAMP (no neighbour crossing).
            shifted_y = _slot_clamp(base_y, raw_shift, base_lanes, i)
            shifted_y = clamp_to_sideline(shifted_y, tuners)
            applied_shift = shifted_y - base_y
            new_spacing[id(d)] = shifted_y
            # In-band + shifted: the authoritative anticipated Y is
            # ``shifted_y``; the defender is under the authority window.
            _stamp(d, applied_shift, threat_y, True,
                   target_y=shifted_y, authority=True,
                   frame=frame_now, time_s=time_now)

    return new_spacing
