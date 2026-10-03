"""
defensive_pipeline.py — Un-gated per-frame defensive geometry preamble (2026-08-12).

PURPOSE
-------
The Depth-Role -> Spacing -> Width -> Target_Y -> Movement invariant sheet
requires every defensive frame to recompute structural depth roles BEFORE any
spacing / width / target_Y writer consumes them — in EVERY phase (OPEN_PLAY,
TES, RELEASE, PTB, POST_RUCK, KICKOFF).  Previously ``assign_depth_roles``
only ran inside the OPEN_PLAY dispatch methods, so PTB-era retreat writers,
the POST_RUCK -> OPEN_PLAY target kick, and the kickoff/catch paths consumed
depth roles anchored to an OLD ruck.

This module splits COMPUTE from WRITE:

  - ``run_defensive_pipeline`` is a pure frame preamble:
        assign_depth_roles (structural, hysteresis-protected)
        -> spacing compute (compute_targets + SpacingGuard)
        -> width recompute  (recompute_width_x + WidthGuard)
    It NEVER writes target_x/target_y.  The guards are the SAME
    SpacingGuard / WidthGuard the OPEN_PLAY pipeline uses, so the Y-only /
    X-only authority contracts hold on every frame, in every phase.

  - The WRITE stays phase-owned: KMC in KICKOFF, the OPE phase handlers in
    RELEASE / PTB / POST_RUCK, and the structured / unified pipelines in
    OPEN_PLAY.  Those writers consume the frame outputs published here:

        world.frame_geometry_ready   (bool)
        world.frame_geometry_frame   (int — the frame_count the dict is for)
        world.frame_spacing_targets  ({id(d): spacing_y})
        world.frame_width_targets    ({id(d): width_x})

``frame_spacing_y(world, d, tuners, governor=None)`` is the single
frame-authoritative Y read: it prefers the fresh frame dict and falls back to
the structural ``spacing_y`` helper (identical formula) when the dict is
absent or not fresh, or when *d* carries no depth role (markers / DH).

``force_reseed_depth_roles`` hard-resets the persistent depth-role seed and
re-bootstraps positionally — used at kickoff catch so the post-catch line
re-seeds BC-relative instead of entering persistent maintenance against the
old flight-era ruck (invariant 1.3 / 5.4 / 6.4).

``clear_frame_geometry`` drops the published frame stamps (used on kickoff
reset so a dict from a previous sequence can never read as fresh).
"""

from __future__ import annotations

import math

from player.player import Player
from game.defence.defence_lvl_1.defensive_depth_roles import (
    assign_depth_roles,
    reset_depth_role_state,
    stamp_drive_depth_x,
)
from game.defence.defence_lvl_1.defensive_tuners import post_tes_settle_active
from game.defence.defence_lvl_1.defensive_split_spacing import (
    compute_targets,
    ruck_adjacent_anchor,
    spacing_y as _spacing_y,
)
from game.defence.defence_lvl_1.spacing_authority import SpacingGuard
from game.defence.defence_lvl_1.width_authority import WidthGuard
from game.defence.defence_lvl_1.defensive_width import (
    depth_side_of,
    governed_width_m,
    recompute_width_x,
    role_anchor_y,
    role_anchor_y_side,
)
# STAGE 9 (2026-09-05): phase-gated, role-aware anticipation pre-shift
# toward the predicted ball / threat lane (OPEN_PLAY + TES).  Y-only,
# band-limited, slot-clamped — imported lazily-safe (defensive_width is
# already imported above, no cycle).
from game.defence.defence_lvl_1.anticipation_layer import (
    apply_anticipation_pre_shift,
)
# STAGE 2 (2026-09-06): SECONDARY width-eligibility grace.  A ruck-adjacent
# defender promoted to PRIMARY/SECONDARY keeps its width lane for a short
# window so fallback spacing cannot pull that interior defender into a
# fallback micro-gap during the marker lock window.  Dependency-light; it
# never imports this module, so no cycle.
from game.defence.defence_lvl_1.secondary_width_grace import (
    advance_secondary_grace,
    arm_secondary_grace,
)

# Valid depth roles — the ONLY defenders that receive a spacing-chain Y.
_ROLE_Y_CONSUMERS = frozenset(("A", "B", "C", "D", "CENTRE", "WINGER"))


# ─────────────────────────────────────────────────────────────────────
# STAGE 11 (2026-09-09): ENGAGED-ONLY NO-TACKLE / NO-DEFENCE GATE
# ─────────────────────────────────────────────────────────────────────
# The full defensive pipeline is expensive (depth-role band maintenance,
# split-spacing chain, width recompute + guards, anticipation pre-shift,
# and the TES / pre-tackle wiring).  From kickoff onwards the engine ran
# that ENTIRE machinery every OPEN_PLAY frame even when the ball carrier
# was completely isolated and no tackle could possibly form — the frames
# where the defensive line is far off the carrier and the framerate
# collapses.  This gate suppresses the whole defensive pipeline (spacing,
# width, depth-lock, investigations, TES/pretackle wiring) whenever:
#
#   * the game is in OPEN_PLAY,
#   * no body is in physical contact, and
#   * no defender is within ``tuners.no_tackle_engagement_radius_m`` of the
#     ball carrier (the engaged-only "zero-tackles" idle frames).
#
# The moment ANY defender closes into engagement range (or a tackle starts),
# the gate releases and the full pipeline resumes — so the chase itself is
# never suppressed, only the wasted work on genuinely idle frames.
#
# Canary-gated by ``tuners.no_tackle_no_defence_enabled`` (default False =
# byte-identical legacy behaviour).


def no_tackle_no_defence_active(world, tuners, phase=None) -> bool:
    """Return True when the defensive pipeline should be SUPPRESSED.

    The predicate is the engaged-only (OPTION B) form: OPEN_PLAY, no body
    in contact, AND no defender within ``no_tackle_engagement_radius_m`` of
    the ball carrier.  When True the caller skips the expensive
    spacing / width / depth-lock / investigation / TES-pretackle machinery
    so the framerate recovers; the chase still engages the instant any
    defender closes into range.

    Canary-gated: ``tuners.no_tackle_no_defence_enabled`` must be True.  The
    default (False) returns False unconditionally, so legacy behaviour is
    byte-identical until the canary is flipped.
    """
    if not getattr(tuners, "no_tackle_no_defence_enabled", False):
        return False
    # Phase gate: only OPEN_PLAY.  Callers pass the live phase; fall back to
    # the engine's published per-frame phase label for external readers.
    if phase is None:
        phase = str(getattr(world, "_ope_phase_label", "") or "")
    if phase != "OPEN_PLAY":
        return False
    # No body in contact.  ``in_contact`` lives on the BC (attacking side)
    # and the engaged defenders (defending side); check those guaranteed
    # collections explicitly (belt-and-suspenders over world.players) so a
    # live tackle or pre-tackle attempt always closes the gate.
    for p in getattr(world, "defending_team", None) or []:
        if getattr(p, "in_contact", False):
            return False
    for p in getattr(world, "attacking_team", None) or []:
        if getattr(p, "in_contact", False):
            return False
    # A live ball carrier is required (kickoff in-air frames run defence).
    bc = getattr(world, "ball_carrier", None)
    if bc is None:
        return False
    if getattr(bc, "in_contact", False):
        return False
    # Engaged-only: if ANY defender is within the engagement radius of the
    # BC, the line is "engaged" and the full pipeline must run.
    radius = float(getattr(tuners, "no_tackle_engagement_radius_m", 12.0))
    for d in getattr(world, "defending_team", None) or []:
        if d is bc:
            continue
        dist = math.hypot(
            float(getattr(d, "x", 0.0)) - float(getattr(bc, "x", 0.0)),
            float(getattr(d, "y", 0.0)) - float(getattr(bc, "y", 0.0)),
        )
        if dist <= radius:
            return False
    return True


# ─────────────────────────────────────────────────────────────────────
# FRAME OUTPUTS
# ─────────────────────────────────────────────────────────────────────

def clear_frame_geometry(world) -> None:
    """Drop the published frame-geometry stamps.

    Called on kickoff reset so a spacing/width dict from a previous
    sequence can never be read as fresh (invariant 1.3 / 5.4).
    """
    world.frame_geometry_ready = False
    world.frame_geometry_frame = None
    world.frame_spacing_targets = None
    world.frame_width_targets = None
    world.frame_side_frame = None
    # STAGE 5: the non-chain ruck-Y pins published by compute_targets.
    world.frame_pin_targets = None


# ─────────────────────────────────────────────────────────────────────
# STAGE 4B — FRAME-GEOMETRY PUBLISH PROVENANCE (audit instrumentation)
# ─────────────────────────────────────────────────────────────────────
# WHY: the exit-boundary audit must be able to answer "how many times was
# THIS frame's spacing/width geometry solved?"  There are two independent
# producers of world.frame_spacing_targets / world.frame_width_targets —
# run_defensive_pipeline (the un-gated preamble, which also re-runs as the
# release slot-invariant repair) and the structured OPEN_PLAY publisher in
# defensive_behaviour_unified.  Both stamp
# world.frame_geometry_frame = frame_count, so BOTH read as fresh for the
# frame and a later reader cannot tell which solve it consumed.
#
# This records the evidence ADDITIVELY: no value written here is read by
# any production path, and the recorded list is a pure observation of the
# publishes that already happened.  The counter is keyed by frame_count, so
# it self-resets on the first publish of a new frame (no per-frame reset
# call is required, and clear_frame_geometry deliberately does NOT touch
# it — a de-publish is a separate fact from a publish).

def stamp_frame_geometry_publish(world, source: str) -> None:
    """Record one frame-geometry publication for the current frame.

    ``source`` is a short stable writer label ("preamble" for
    run_defensive_pipeline, "structured_open_play" for the unified
    publisher, "repair" for the release slot-invariant repair re-run).
    """
    frame = getattr(world, "frame_count", None)
    if getattr(world, "frame_geometry_publish_frame", None) != frame:
        world.frame_geometry_publish_frame = frame
        world.frame_geometry_publish_count = 0
        world.frame_geometry_publish_sources = []
    count = int(getattr(world, "frame_geometry_publish_count", 0)) + 1
    world.frame_geometry_publish_count = count
    sources = getattr(world, "frame_geometry_publish_sources", None)
    if not isinstance(sources, list):
        sources = []
        world.frame_geometry_publish_sources = sources
    sources.append(source)


def frame_geometry_publish_sources(world) -> tuple[int, list]:
    """Return ``(count, sources)`` for the CURRENT frame's publications.

    Returns ``(0, [])`` when no publish has happened this frame (e.g. the
    engaged-only gate suppressed the preamble and the structured publisher
    did not run).
    """
    if getattr(world, "frame_geometry_publish_frame", None) != getattr(
        world, "frame_count", None
    ):
        return 0, []
    count = int(getattr(world, "frame_geometry_publish_count", 0))
    sources = getattr(world, "frame_geometry_publish_sources", None)
    return count, list(sources) if isinstance(sources, list) else []


def depth_roles_assigned_this_frame(world) -> bool:
    """Return True when the un-gated preamble already assigned depth roles
    for the CURRENT frame.

    TASK 2 (2026-08-14) — SINGLE-ASSIGN-PER-FRAME CONTRACT:
    ``run_defensive_pipeline`` is the sole depth-role / spacing / width
    authority for every OPE frame.  It stamps
    ``world.depth_roles_frame == frame_count`` immediately after
    ``assign_depth_roles`` completes, BEFORE the spacing dict is computed,
    so the frame spacing targets are built from the SAME roles every
    consumer reads later in the frame.

    The downstream pipelines (``_apply_structured_open_play`` step 2b,
    ``_apply_open_play_unified`` step 1, ``apply_pursuit_mode``,
    ``_handle_line_break``) previously re-called ``assign_depth_roles``
    a SECOND time per frame.  When that second call mutated the bands
    (re-fit / boostrap), ``world.frame_spacing_targets[id(d)]`` — computed
    from the FIRST assignment — silently disagreed with the post-second-
    call ``role_depth_target_y`` and the ``spacing_y()`` helper, producing
    ``stale_depth_role`` / ``stale_target_y`` drift > 3.0 m and spelling
    the dual-source spacing divergence.

    The guard makes the second call a NO-OP: the preamble owns roles for
    the frame; positional re-bootstrap still works because fresh frames
    start with the stamp cleared (update() runs the preamble first).
    """
    return (
        getattr(world, "depth_roles_frame", None)
        == getattr(world, "frame_count", None)
    )


def frame_geometry_is_fresh(world) -> bool:
    """Return True when the published geometry belongs to THIS frame."""
    if not bool(getattr(world, "frame_geometry_ready", False)):
        return False
    return (
        getattr(world, "frame_geometry_frame", None)
        == getattr(world, "frame_count", None)
    )


def frame_spacing_covers(world, defenders) -> bool:
    """True when the fresh frame dict covers EVERY defender in *defenders*.

    TASK 8 (2026-08-21) — RECOMPUTE-SKIP PREDICATE: the single shared
    gate every consumer uses to decide whether the frame dict already
    covers the current defender set.  When True:

      - ``_apply_structured_open_play`` step 6 consumes
        ``dict(world.frame_spacing_targets)`` directly (no second
        compute_targets call),
      - ``DefensiveGeometry.compute_role_targets`` reads Y via
        ``_frame_spacing_y`` instead of recomputing the even side-lane
        chain,
      - ``frame_spacing_y`` never falls back to the structural helper.

    Coverage = frame_geometry_is_fresh AND a non-empty dict AND every
    non-fullback defender id present (the fullback actor is excluded
    from the published dicts by contract).
    """
    if not frame_geometry_is_fresh(world):
        return False
    dct = getattr(world, "frame_spacing_targets", None)
    if not dct:
        return False
    for d in defenders:
        if d is None or getattr(d, "fullback_role_active", False):
            continue
        if id(d) not in dct:
            return False
    return True


def apply_frame_spacing(world, defenders) -> None:
    """Stamp the applied per-frame spacing position on every line defender.

    TASK 8 (2026-08-21) — APPLIED SPACING BEFORE EVENT LOGIC: after the
    preamble publishes the frame dicts it stamps each line defender with

        d.frame_spacing_y = frame_spacing_targets[id(d)]

    This is the per-frame APPLIED-POSITION field — NOT target_y.  The
    movement writers remain the sole owners of target_x/target_y; the
    preamble stays compute-only (verify_defensive_pipeline's contract).
    Event logic (retreat / close / settle / role movement / decision /
    MMDL consumers) reads the applied field instead of re-deriving
    structural targets.

    Fullback actors are skipped (their anchor is owned by fullback_role).
    """
    dct = getattr(world, "frame_spacing_targets", None)
    if not dct:
        return
    for d in defenders:
        if d is None or getattr(d, "fullback_role_active", False):
            continue
        y = dct.get(id(d))
        if y is not None:
            d.frame_spacing_y = float(y)


def frame_spacing_y(world, d, tuners, governor=None) -> float:
    """Frame-authoritative spacing Y for *d* (STAGE 3C: dict-only).

    Reads ``world.frame_spacing_targets`` when it is fresh for the
    current frame and covers *d*.  Every runtime Y reader (retreat,
    structured-close, role movement, post-ruck) calls THIS — it is the
    single shared Y source.  There is NO structural recompute fallback:
    a covered but stale/absent dict can never trigger a second
    ``compute_targets``/``spacing_y`` structural read (that was the
    dual-source divergence).  Non-covered defenders fall back to the
    documented non-chain pin ``ruck_y`` (the explicit constant, not a
    chain recompute).

    Invariant 2.1/2.2 — spacing is the sole authoritative Y source.

    TACKLE-STATE CHAIN FALLBACK (2026-09-01): a body NOT in the fresh
    frame dict (e.g. a tackle-state ruck-adjacent pivot that
    ``compute_targets`` excluded from the eligible pool) must NOT be
    collapsed onto ``ruck_y``.  Give chain bodies their role-anchor width
    — the ruck-adjacent A pivot uses the ANCHOR LAW (``ruck_y ± 2.0 m``)
    so the line fans evenly from both ruck-side pivots.  Non-chain tight
    bodies (markers / DH / tacklers / chase) stay pinned to ``ruck_y``.
    """
    def _lane(y: float) -> float:
        """SLOT-TIGHT SPACING (2026-09-07) — spacing-only slot-band clamp.

        The SLOT_OUT histogram identified the spacing-Y authority
        (``frame_spacing_y``, stamped ``target_source="spacing"``) as the
        dominant writer collapsing the slot band: a radially-close engaged
        PRIMARY/SECONDARY body can be spaced far from ``bc.y``, so
        ``is_slot_tackle_eligible`` fails and the tackle is suppressed
        (slot-OUT).  This surgical fix keeps ONLY that engaged, radially-close
        case's spacing Y inside the tackle slot band so the slot gate stays
        live.  It never touches the deep / PASSIVE line, markers, fullback,
        the outer WINGER, or the dict/stamped fast paths for non-engaged
        bodies.  Gated by ``tuners.slot_tight_spacing_enabled`` (default True)
        so the change is a controlled, single-authority spacing-only fix.
        """
        bc = getattr(world, "ball_carrier", None)
        if bc is None or not getattr(tuners, "slot_tight_spacing_enabled", True):
            return float(y)
        # ── ANTICIPATION LEAN AUTHORITY (2026-09-09) ──
        # The slot-tight pull re-pins a radially-close engaged PRIMARY/SECONDARY
        # toward bc.y, which would overwrite the anticipation pre-shift (the
        # lean toward the predicted lane) in the dict every downstream consumer
        # reads.  While the anticipation authority window is live, the lean is
        # the single spacing authority — this closure passes the shifted Y
        # through untouched.
        try:
            from game.defence.defence_lvl_1.anticipation_layer import (
                anticipation_authority_active as _ant_active,
            )
            if _ant_active(world, d, tuners):
                return float(y)
        except Exception:
            pass
        if getattr(d, "depth_class", None) not in ("PRIMARY", "SECONDARY"):
            return float(y)
        _tr = float(getattr(tuners, "tackle_radius_m", 1.5))
        if _tr <= 0.0:
            return float(y)
        if math.hypot(
            float(getattr(d, "x", 0.0)) - float(getattr(bc, "x", 0.0)),
            float(getattr(d, "y", 0.0)) - float(getattr(bc, "y", 0.0)),
        ) >= _tr:
            return float(y)
        _bcy = float(getattr(bc, "y", 0.0))
        _slot = float(getattr(tuners, "tackle_slot_threshold_m", 0.3))
        _y = float(y)
        if abs(_bcy - _y) < _slot:
            return _y
        # Pull the spacing Y to the slot-band edge toward the BC lane.
        _side = 1.0 if _y > _bcy else -1.0
        return _bcy + _side * (_slot * 0.5)

    # ── POST-TES DRIVE-END SETTLE WINDOW (2026-09-06; phase-aware 2026-09-07) ──
    # While the drive-end settle window is active (DOMINANCE→RELEASE through
    # PTB_* and POST_RUCK_OPEN_PLAY) hold the LAST-APPLIED spacing Y (the
    # drive-era settle slot) instead of consuming a freshly recomputed
    # ruck-relative spacing dict.  The line keeps its drive-era shape for the
    # window and the defenders settle naturally (glide via the movement
    # integrator) before retreat/reform re-targets them — no instant spacing
    # re-pin to the new ruck.  The window disengages on the first OPEN_PLAY
    # frame, where the fresh ruck-relative spacing authority resumes.
    if post_tes_settle_active(world):
        _stamped_settle = getattr(d, "frame_spacing_y", None)
        if _stamped_settle is not None:
            return _lane(float(_stamped_settle))

    # ── OUT-OF-RADIUS HOLD GATE (2026-09-07) ──
    # During the TES drive a defender beyond R_tackle that is NOT a
    # registered TES tackler must NEVER receive an outward / recomputed
    # spacing Y.  The width fallback (role_anchor_y_side) consumes the
    # TES-perturbed count/width and pushes an outer WINGER/CENTRE OUTWARD
    # (the lateral blow-out).  Hold the body's current Y (stationary /
    # inward only) so the out-of-band motion gate holds laterally too.
    # Local import: avoids a module-load-order dependency on
    # tackle_state_gate (which imports nothing from this module).
    from game.defence.defence_lvl_1.tackle_state_gate import (
        is_tes_drive_hold as _is_tes_drive_hold,
    )
    if _is_tes_drive_hold(world, d):
        return _lane(float(getattr(d, "y", 0.0)))

    if not getattr(d, "fullback_role_active", False):
        if frame_geometry_is_fresh(world):
            dct = getattr(world, "frame_spacing_targets", None)
            if dct and id(d) in dct:
                return _lane(float(dct[id(d)]))
    # HOLD-LINE GUARD (2026-09-06): when the frame dict is stale/absent
    # (e.g. during the TES drive, when the defensive pipeline is not
    # republished), fall back to the LAST-APPLIED frame-spacing Y rather
    # than recomputing the per-side width (role_anchor_y_side).  The width
    # recompute consumes a TES-perturbed defender COUNT / ruck / width and
    # pushes the outer WINGER/CENTRE slots OUTWARD — the outward "lateral
    # infection" on the line.  A body that was part of the last OPEN_PLAY
    # line carries d.frame_spacing_y (stamped by apply_frame_spacing); hold
    # that exact spaced slot.  Only unstamped bodies (e.g. tackle-state
    # ruck-adjacent pivots that compute_targets excluded) fall through to
    # the structural fallback below.
    # STAGE 5 PIN READ (2026-09-13): a non-line body's ruck-Y pin now lives
    # in ``world.frame_pin_targets`` (the spacing dict stays exactly the line
    # population).  Consult the pin before the last-applied / structural
    # fallback so the pinned Y is unchanged.
    if not getattr(d, "fullback_role_active", False):
        pins = getattr(world, "frame_pin_targets", None)
        if pins and id(d) in pins:
            return _lane(float(pins[id(d)]))
    stamped = getattr(d, "frame_spacing_y", None)
    if stamped is not None:
        return _lane(float(stamped))
    ruck_y = float(getattr(world, "ruck_y_m", 35.0))
    role = getattr(d, "depth_role", None)
    if role in ("A", "B", "C", "D", "CENTRE", "WINGER"):
        side = depth_side_of(d, ruck_y)
        if role == "A":
            # Ruck-adjacent pivot — ANCHOR LAW (ruck_y +/- 2 m).
            return _lane(ruck_adjacent_anchor(ruck_y, side))
        # SINGLE WIDTH SOURCE (2026-09-03): use role_anchor_y_side so the
        # fallback consumes the SAME per-side width (short-side box squeeze /
        # available-room cap) as the spacing chain.  The old
        # role_anchor_y(d, ruck_y, world.def_line_width_m, tuners) passed the
        # FULL governed width to BOTH sides, putting the short-side WINGER at
        # the bare margin (3.0) while the spacing chain used margin+buffer
        # (6.0) — the two-authority short-side blowout.
        return _lane(role_anchor_y_side(d, ruck_y, world, tuners))
    return _lane(ruck_y)


# ─────────────────────────────────────────────────────────────────────
# LINE DEFENDER FILTER
# ─────────────────────────────────────────────────────────────────────

def _line_defenders(defenders) -> list:
    """Return the non-fullback defenders (the depth/spacing/width line).

    Mirrors the exclusion contract: the dynamic fullback actor never
    occupies a depth-role / spacing-chain / width slot (fullback_role.py /
    assign_depth_roles).  ``compute_targets`` pins an excluded fullback to
    ``world.fullback_default_*`` when one is passed in, so the actor is
    removed HERE so the frame dict never carries a default-pinned FB Y that
    could fight the kickoff/OPEN_PLAY anchor.
    """
    # STAGE 5 (2026-09-13): the line filter is the SINGLE canonical
    # population predicate — the same set the count / side / spacing layers
    # use (excluded roles incl. DEF_CHASE, the fullback actor, and every
    # TACKLE-STATE holder).  Delegating here removes the last hand-rolled
    # copy of the exclusion contract from the pipeline.
    from game.defence.defence_lvl_1.population_authority import (
        line_population as _line_population,
    )
    return _line_population(defenders)


# ─────────────────────────────────────────────────────────────────────
# MAIN PREAMBLE — Depth-Role -> Spacing -> Width (ungated, pure)
# ─────────────────────────────────────────────────────────────────────

def run_defensive_pipeline(world, defenders, tuners, governor=None,
                           publish_source: str = "preamble"):
    """Run the un-gated per-frame defensive geometry in exact order.

    Order (invariant sheet):
        1. Depth roles   — assign_depth_roles (structural, recomputed from
                           the CURRENT ruck; hysteresis-protected).
        2. Spacing       — compute_targets (single Y authority) + SpacingGuard
                           (Y-only, anchor untouched).
        3. Width         — recompute_width_x (single X authority) + WidthGuard
                           (X-only, spacing/anchor untouched).

    Pure: never writes target_x / target_y / movement.  Publishers:
        world.frame_geometry_ready = True
        world.frame_geometry_frame = frame_count
        world.frame_spacing_targets = {id(d): y}
        world.frame_width_targets   = {id(d): x}

    Returns ``(spacing_targets, width_targets)``.

    Fullback actors are excluded from the line input so the dicts describe
    the line only (the FB anchor is owned by fullback_role / KMC).
    """
    # ── STAGE 5 SINGLE POPULATION PUBLICATION (2026-09-13) ──
    # Publish THE line population (its count and side split) ONCE for this
    # frame BEFORE any role/spacing writer runs.  Every later call in the
    # frame (the dynamic refit, the reband) reuses this publication, so the
    # count is a single per-frame fact.  The publication also clears the
    # stale depth_side on every body that left the line.
    from game.defence.defence_lvl_1.population_authority import (
        publish_line_population as _publish_line_population,
    )
    _publication = _publish_line_population(world, list(defenders), tuners)
    eligible = list(_publication["population"])

    # 1. DEPTH ROLES FIRST (invariant 1.2): finalise before any spacing/
    #    width/target writer.  Idempotent persistent maintenance; the
    #    structural anchors are rebuilt whenever the ruck moved.
    assign_depth_roles(eligible, world)

    # TASK 2 (2026-08-14) — SINGLE-ASSIGN-PER-FRAME STAMP.
    # Publish the assignment stamp BEFORE the spacing dict is computed so
    # every later consumer (depth_roles_assigned_this_frame) knows the
    # frame spacing targets were built from the SAME roles they read.
    world.depth_roles_frame = getattr(world, "frame_count", None)

    # ── DRIVE-AWARE DEPTH-X REFRESH (request: refresh every frame) ──
    # Re-stamp role_depth_target_x from the LIVE bc.x after the assignment
    # so the BC-anchored value is authoritative even when assign_depth_roles
    # took an early-return / mode-gated branch this frame.  Only X is
    # refreshed; Y is owned by assign_depth_roles in the same frame.
    stamp_drive_depth_x(world, eligible)

    # ── DEPTH-TARGET BOUNDARY INVARIANT (defence boundary, 2026-09-19) ──
    # THE OPEN FINDING: a non-fullback depth-role target derived from
    # ``bc.x + WIDTH_OFFSETS[depth_role]`` could be generated BEYOND the
    # defending team's own tryline (+X = 110) once the carrier was driven
    # past x = 100 (WINGER, offset 10.0) — a command into the team's own
    # in-goal, with the TES depth debt then measured against that illegal
    # target.  The post-tackle 10 m retreat protection has always been
    # tryline-clamped (``anchor_authority.defensive_line_anchor_m``); this
    # is the equivalent invariant for depth-role TARGETS and depth-role
    # POSITIONAL LOCKS, applied as a per-frame repair pass so the
    # guarantee holds even for a writer that bypassed the single law.
    #
    # ORDER: it runs IMMEDIATELY after the stamp and BEFORE the TES drive
    # chamber gate below — that gate returns early for the whole CONTACT /
    # DRIVE / DOMINANCE chamber, which is exactly the window the finding
    # names, so the repair must run first or it would never run during the
    # drive at all.
    #
    # The audit runs first (the DRIFT DETECTOR: it names any writer that
    # produced an illegal depth X) and the enforcement pass second, so the
    # audit still reports what was produced before the repair.  COMPLIANT
    # FRAMES ARE BIT-IDENTICAL: the pass writes only an attribute it found
    # outside the envelope and returns an empty list on every legal frame
    # (which is every frame whose carrier has not reached the tryline).
    from game.defence.defence_lvl_1.depth_boundary_guard import (
        apply_depth_boundary_guard as _apply_depth_boundary_guard,
        audit_depth_boundary as _audit_depth_boundary,
    )
    _boundary_violations = _audit_depth_boundary(world, eligible, tuners)
    if _boundary_violations:
        world.depth_boundary_violations_last = len(_boundary_violations)
        world.depth_boundary_violations_total = (
            int(getattr(world, "depth_boundary_violations_total", 0) or 0)
            + len(_boundary_violations)
        )
    _boundary_repaired = _apply_depth_boundary_guard(world, eligible, tuners)
    # WIRING PROOF (same convention as _tes_depth_authority_last): an empty
    # list across a whole rollout is EXPECTED — it means no writer produced
    # an illegal depth X.  A non-empty list names the bodies that were
    # repaired, i.e. the writers that let one through.
    world.depth_boundary_repaired_last = _boundary_repaired
    world.depth_boundary_repaired_total = (
        int(getattr(world, "depth_boundary_repaired_total", 0) or 0)
        + len(_boundary_repaired)
    )

    # ── TES DRIVE CHAMBER GATE (2026-09-11) ──
    # Inside CONTACT / DRIVE / DOMINANCE the held line's LATERAL geometry is
    # owned by ``tes_drive_structure`` (the locked lane seeded from the last
    # full-count OPEN_PLAY frame).  The spacing / width recompute below runs
    # on a DRIVE-DEPLETED defender count (tacklers, markers, DH and the
    # fullback are all excluded once the tackle starts), which widens the
    # per-body lane step and restamps ``d.frame_spacing_y`` with that widened
    # value — the source of the line blowing OUTWARD from each side of the
    # TES.  Publish nothing and leave the last OPEN_PLAY dict + stamps in
    # place for the whole chamber; depth roles and the BC-relative depth X
    # above are still refreshed every frame.
    from game.defence.defence_lvl_1.tes_drive_structure import (
        is_tes_drive_substate as _is_tes_drive_substate,
    )
    if _is_tes_drive_substate(world):
        return (
            getattr(world, "frame_spacing_targets", None) or {},
            getattr(world, "frame_width_targets", None) or {},
        )

    # ── STAGE 2 SECONDARY WIDTH-ELIGIBILITY GRACE ──
    # Advance the grace clock / invalidate (large ruck displacement,
    # explicit RELEASE), then arm fresh graces for newly-promoted
    # ruck-adjacent PRIMARY/SECONDARY defenders BEFORE the width
    # recompute, so the grace preserves the width lane and fallback
    # spacing cannot pull that interior defender into a fallback micro-gap
    # during the marker lock window.  Canary-gated by
    # tuners.secondary_grace_enabled (default False = no-op).
    _dt = float(getattr(world, "dt", 1.0 / 60.0))
    advance_secondary_grace(world, eligible, tuners)
    arm_secondary_grace(world, eligible, tuners)

    # ── STAGE 6 SINGLE WIDTH AUTHORITY (2026-09-13) ──
    # PUBLISH the frame's lateral width ONCE, before any consumer runs.
    # ``formation_width_authority`` solves the per-side span from the
    # governed 3-point width interpolation (short/open split factor, min/max
    # clamp, available-room cap), publishes it on the world, and exposes the
    # named reducer ledger.  Every downstream reader — ``side_widths`` (the
    # spacing chain's span source), ``publish_span_contract`` (the TES census
    # ladder), ``defensive_geometry``, the governor accessor — now consumes
    # this ONE fact instead of deriving its own copy.  The solve is cached by
    # (frame, scope), so the repeated lookups below are cache hits, not
    # recomputes.
    from game.defence.defence_lvl_1.formation_width_authority import (
        publish_formation_width as _publish_formation_width,
    )
    _publish_formation_width(world, tuners)

    # 2. SPACING — the single Y authority (grid-compute + read-back guard).
    spacing = compute_targets(world, eligible, tuners, governor=governor)
    spacing = SpacingGuard(world, eligible).verify(spacing)

    # ── STAGE 9: PHASE-GATED, ROLE-AWARE ANTICIPATION PRE-SHIFT ──
    # Apply the bounded lateral pre-shift toward the predicted ball / threat
    # lane during OPEN_PLAY + TES (phase-gated inside via is_drive_phase).
    # Y-only, band-limited to nearby defenders, role-scaled, and clamped by
    # slot hierarchy so it never destabilises spacing or drifts the WINGER.
    # The shifted spacing dict then flows into width (read-only), the frame
    # publish, and d.frame_spacing_y — the single Y authority every
    # downstream consumer (retreat / close / MMDL / movement) reads.
    if getattr(tuners, "anticipation_enabled", True):
        spacing = apply_anticipation_pre_shift(
            world, eligible, tuners, spacing,
        )

    # 3. WIDTH — the single X authority (pure recompute + read-back guard).
    # ``governed`` is READ from the Stage-6 publication rather than solved
    # again: the width the X recompute consumes is exactly the width the
    # spacing span was built from this frame, so the two can never disagree.
    # ``governed_width_m`` remains the single scalar accessor (it delegates
    # to the same governed chain), but reading the publication keeps the
    # provenance explicit: X consumes a PUBLISHED fact, not a recompute.
    from game.defence.defence_lvl_1.formation_width_authority import (
        formation_width_value as _formation_width_value,
    )
    governed = _formation_width_value(world, tuners)
    width = recompute_width_x(
        world,
        eligible,
        tuners,
        governor_width=governed,
        spacing_targets=spacing,
    )
    width = WidthGuard(
        world,
        eligible,
        tuners,
        governor_width=governed,
        spacing_outputs=spacing,
    ).verify(width)

    # STAGE 4B (audit-only): record whose solve this frame's geometry is.
    # A second entry in the same frame means this pipeline ran again as the
    # release slot-invariant repair (publish_source="repair").
    stamp_frame_geometry_publish(world, publish_source)
    world.frame_spacing_targets = spacing
    world.frame_width_targets = width
    world.frame_geometry_ready = True
    world.frame_geometry_frame = getattr(world, "frame_count", None)
    # FRAME-SIDE freshness stamp: compute_targets() already wrote
    # d.frame_side on every eligible defender (LEFT/RIGHT from the
    # optimal split) before the spacing loop.  This stamp lets
    # downstream consumers verify the side flag belongs to this frame.
    world.frame_side_frame = getattr(world, "frame_count", None)

    # ── TASK 8 (2026-08-21): APPLY THE SPACING TO WORLD POSITIONS ──
    # Stamp the per-frame applied-position field (d.frame_spacing_y) on
    # every line defender BEFORE any event logic runs this frame.  The
    # preamble stays compute-only (never writes target_x/target_y); the
    # applied field is what event logic reads instead of re-deriving
    # structural targets.
    apply_frame_spacing(world, eligible)

    return spacing, width


# ─────────────────────────────────────────────────────────────────────
# FORCED RE-SEED (bootstrap) — kickoff catch / fresh-ruck transitions
# ─────────────────────────────────────────────────────────────────────

def force_reseed_depth_roles(world, defenders) -> list:
    """Hard-reset the persistent depth-role seed, then re-bootstrap.

    ``reset_depth_role_state`` clears depth_role / depth_side /
    _depth_seeded / role_depth_target_x / role_depth_target_y on every
    line defender, so the following ``assign_depth_roles`` runs the one-time
    legacy positional bootstrap relative to the CURRENT ruck (not persistent
    maintenance against an old ruck).

    Used at kickoff catch so the post-catch line re-seeds BC-relative
    (invariant 1.3: depth roles must never be carried from the previous
    tackle/sequence; invariant 6.4: kickoff lanes are immediately overridden
    by spacing).

    Returns the eligible (reseeded) defender list.
    """
    eligible = _line_defenders(list(defenders))
    for d in eligible:
        if isinstance(d, Player):
            reset_depth_role_state(d)
    assign_depth_roles(eligible, world)
    return eligible
# ─────────────────────────────────────────────────────────────────────
# FULL DEFENSIVE-STATE RESET (2026-09-07) — TACKLE_RELEASE → PTB → TACKLE_INIT
# ─────────────────────────────────────────────────────────────────────
# The stale STOP-GAP reset that runs between tackles.  The out-of-band
# motion verifier showed that depth-lock, spacing-frame, retreat-support and
# role-depth targets are NOT fully reset between tackles — the first tackle
# starts clean, but the second inherits the drive-path state (the leak only
# appears from tackle #2 onward).  This reset clears EVERY per-defender
# drive-path field so all defenders rejoin the line fresh before the next
# tackle.  The persistent depth-role bands (depth_role / depth_side /
# _depth_seeded) are PRESERVED — the RELEASE/PTB recovery-window seed and
# the POST_RUCK re-lane own band re-assignment; wiping bands here would fight
# them.

def reset_defensive_drive_state(world) -> None:
    """Full defensive-state reset at TACKLE_RELEASE / PTB / TACKLE_INIT.

    Clears the stale drive-path state that survives one tackle into the
    next (the out-of-band leak source): drive-depth-lock, role-depth X/Y
    targets, frame-spacing stamps, retreat-support movement state, and the
    MMDL / authority bookkeeping.  The persistent depth-role bands and the
    anchor lock are handled by the existing reset authorities; this is the
    per-defender drive-path wipe.

    Idempotent — safe to call at ANY of the three transition points
    (RELEASE entry, PTB entry, TACKLE_INIT entry).
    """
    # 1. Drop the published frame-geometry stamps (stale spacing/width).
    clear_frame_geometry(world)

    # 2. Clear engagement/authority state so the next open-play phase
    #    re-bootstraps fresh (no stale PASSIVE lock).  Also unlocks the
    #    anchor authority so the anchor recomputes from the NEW ruck.
    from game.defence.defence_lvl_1.defensive_engagement_state import (
        reset_engagement_state as _reset_engagement_state,
    )
    _reset_engagement_state(world)

    # 3. Per-defender drive-path wipe.
    # ROLE-DEPTH TARGET: set to None (NOT the anchor).  This mirrors the
    # established _release_defender convention (role_depth_target_x = None)
    # and defers to the per-frame stamp.  Stamping the anchor here conflicted
    # with is_drive_phase()==True during OPEN_PLAY (the drive-aware depth law
    # _depth_role_target_x returns bc.x + WIDTH_OFFSETS[role]), so a freshly
    # peeled marker reached assert_anchor_consistency with a non-None anchor
    # stamp and crashed the second tackle (AnchorAuthorityViolation id=204).
    # None lets the check skip it until stamp_drive_depth_x re-stamps.
    for _p in (getattr(world, "defending_team", None) or []):
        if _p is None:
            continue
        # ── DEPTH-LOCK ──
        _p.depth_locked = False
        _p.depth_lock_x = float(getattr(_p, "x", 0.0) or 0.0)
        _p.depth_lock_y = None
        # ── ROLE-DEPTH TARGETS (defer to per-frame stamp; never the anchor) ──
        _p.role_depth_target_x = None
        _p.role_depth_target_y = None
        # ── SPACING-FRAME (applied) ──
        _p.frame_spacing_y = None
        # ── RETREAT-SUPPORT ──
        _p.movement_state = None
        _p.target_source = None
        _p.retreating = False
        _p._sd_vx = 0.0
        _p._sd_vy = 0.0
        # ── AUTHORITY / MMDL BOOKKEEPING ──
        _p.authoritative_until_frame = -1
        _p._mmdl_state_until_frame = -1
        _p._mmdl_moved = False
        _p._force_innermost_seed = False
        _p._flipped_in_release = False
        _p._defer_retreat_10 = False
        # ── PRE-TACKLE AUTHORITY STAMPS (belt-and-suspenders) ──
        from game.defence.defence_lvl_1.pre_tackle_authority import (
            clear_pre_tackle_state as _clear_pta,
        )
        try:
            _clear_pta(_p)
        except Exception:
            pass


def reset_defensive_drive_state_for_sequence(world, defenders) -> None:
    """Deprecated alias — kept for verifier back-compat; use the full reset."""
    reset_defensive_drive_state(world)
