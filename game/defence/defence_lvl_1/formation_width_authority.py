"""
formation_width_authority.py — the SINGLE line-width (lateral span) authority
(Stage 6, 2026-09-13).

WHY THIS MODULE EXISTS
----------------------
Stage 5 unified the line POPULATION (membership, count, side) under one
authority and proved it on every frame.  The remaining architectural leak was
WIDTH.  Measured before this module existed, the lateral span was derived or
modified by SEVERAL systems, each computing its own copy:

    width tuners       ``defensive_width.interpolated_defensive_width``
    governor           ``DefensiveLineGovernor.compute_line_state`` ->
                       ``world.def_line_width_m`` (the only writer)
    governed accessor  ``defensive_width.governed_width_m`` AND
                       ``DefensiveLineGovernor.governed_width_m`` (two
                       implementations — they diverge when the governor is
                       inactive; Stage 1 named GOVERNED_ACCESSOR_DIVERGENCE)
    per-side split     ``defensive_split_spacing.side_widths`` (recomputed at
                       ten call sites, and it discarded its own ``ruck_y``
                       argument for the room cap — Stage 1
                       RUCK_Y_ARG_IGNORED)
    lane builder       ``side_lanes`` / ``lane_gap_m`` (the derived step is
                       floored by ``min_defender_gap_m``, so below a span
                       threshold the SPAN no longer owns the ladder — Stage 1
                       LANE_STEP_FLOOR)
    band fit           ``_fit_lanes_to_band`` (GOVERNOR_BAND_FIT)
    band pull          ``_band_spacing_pull`` + anticipation (BAND_PULL)
    TES census         ``tes_drive_structure._census_side_ladder``
    macro governor     ``DefensiveLineGovernor.apply_macro_width_governor``
    geometry manager   ``defensive_geometry`` / ``defence_manager`` /
                       ``catch_system`` (each reading governed width itself)
    X-depth "width"    ``recompute_width_x`` + ``world.frame_width_targets``
                       (TWO publishers: ``defensive_pipeline`` and
                       ``defensive_behaviour_unified``)

This module is the ONE place that computes and publishes the frame's lateral
width, and every other system becomes a CONSUMER of the publication.

THE PUBLICATION
---------------
``publish_formation_width(world, tuners, ruck_y=None)`` computes, once per
(frame, scope), and publishes:

    commanded_m      the 3-point width-tuner interpolation at the live bc.x
                     (``None`` when there is no carrier — NO_BALL_CARRIER)
    governed_m       the clamped/field-capped width the line actually uses
    left_m/right_m   the PER-SIDE SPAN — the governed total width distributed
                     across the two ruck-anchored halves under the physical
                     sideline safety clamp only
    short_side       the short side the split was applied to (LEFT/RIGHT)
    span_contract    a ``SpanContract`` carrying left_m/right_m
    reducers         the named metre deltas between the command and the
                     published span (SPLIT_FACTOR / CLAMP_MIN / CLAMP_MAX /
                     ROOM_CAP / NO_BALL_CARRIER), so the verifier can prove
                     ``published_mean == commanded + sum(named reducers)``
                     EXACTLY, with no UNATTRIBUTED remainder.  The deltas are
                     ADDITIVE to the command; they are not subtracted from it.
    lane_step        the per-side uniform lane step a count would be spaced at
    lane_step_floor_binds  True when ``min_defender_gap_m`` floors that step

The publication is keyed by
``(frame_count, ruck_y, ruck_x, bc_x, governor state, tuner signature)``.
A second request in the same frame for the SAME scope returns the cached
publication WITHOUT re-solving, so the width is a single per-frame fact no
matter how many subsystems ask.

SCOPED PUBLICATIONS
-------------------
The TES drive chamber latches its band reference at drive entry
(``tes_drive_structure.resolve_band_ref_y``), which can differ from the live
``world.ruck_y_m``.  A caller asking for an explicitly different ruck is
asking about a DIFFERENT, explicitly-scoped geometry (the chamber's latched
ladder), so the authority keeps one publication per scope:

    world.formation_width            the PRIMARY publication (world ruck)
    world._formation_width_by_scope  {key: publication} for every solve

Every solve is recorded in ``world.formation_width_solves`` (a list of
``(key, left_m, right_m)``) so a verifier can prove that no two DIFFERENT
width values were ever published for the SAME scope in one frame.

PURE W.R.T. POSITIONS: this module writes only the ``world.*`` publication
fields.  It never writes x / y / target_* / movement.

CONSUMERS (all delegate here)
-----------------------------
``defensive_split_spacing.side_widths`` / ``.resolve_short_side``,
``span_contract.publish_span_contract``, ``population_authority``,
``defensive_width.governed_width_m`` (single accessor),
``DefensiveLineGovernor.governed_width_m``, ``defensive_geometry``,
``defence_manager``, ``catch_system``, ``tes_drive_structure``.
"""

from __future__ import annotations

import math

from game.defence.defence_lvl_1.span_contract import (
    SpanContract,
    SpanContractError,
)

__all__ = [
    "FORMATION_WIDTH_ATTR",
    "FORMATION_WIDTH_SOLVES_ATTR",
    "REDUCER_SPLIT_FACTOR",
    "REDUCER_CLAMP_MIN",
    "REDUCER_CLAMP_MAX",
    "REDUCER_ROOM_CAP",
    "REDUCER_NO_BALL_CARRIER",
    "REDUCER_LANE_STEP_FLOOR",
    "ALL_REDUCERS",
    "resolve_short_side",
    "distribute_total_span",
    "publish_formation_width",
    "published_formation_width",
    "published_side_widths",
    "published_span_contract",
    "width_publication_is_fresh",
    "formation_width_value",
    "width_solves_for_frame",
    "conflicting_width_solves",
]


# ─────────────────────────────────────────────────────────────────────
# WORLD PUBLICATION FIELDS
# ─────────────────────────────────────────────────────────────────────

FORMATION_WIDTH_ATTR = "formation_width"
FORMATION_WIDTH_SOLVES_ATTR = "formation_width_solves"
_FORMATION_WIDTH_SCOPE_ATTR = "_formation_width_by_scope"
FORMATION_WIDTH_FRAME_ATTR = "formation_width_frame"
FORMATION_WIDTH_PUBLISH_COUNT_ATTR = "formation_width_publish_count"

# Side signs — shared with the depth-role / split-spacing systems.
LEFT: int = -1
RIGHT: int = +1

# Fallbacks mirroring the shipped split-spacing constants so a stub world
# (verifier) computes the same geometry it always did.
RUCK_Y_FALLBACK: float = 35.0
FIELD_WIDTH_FALLBACK: float = 70.0
SIDELINE_MARGIN_FALLBACK: float = 3.0
SHORT_SIDE_BUFFER_FALLBACK: float = 3.0
# WIDTH RAILS REMOVED (2026-09-14, Option B): there is no minimum or maximum
# defensive-width tuner.  The 3-point width curve is the sole width
# authority; the only bound is the physical field cap derived from
# FIELD_WIDTH_FALLBACK and SIDELINE_MARGIN_FALLBACK.

# ─────────────────────────────────────────────────────────────────────
# NAMED REDUCERS — every metre between the command and the published span
# ─────────────────────────────────────────────────────────────────────
REDUCER_SPLIT_FACTOR = "SPLIT_FACTOR"          # short/open split factor
# CLAMP_MIN is the RETIRED width floor.  The tuner it named is GONE
# (2026-09-14, Option B), so this entry is published at exactly 0.0 and is
# asserted so by ``verify_width_curve_sole_authority.py``.  Keeping it named
# is deliberate: any future edit that re-introduces a width floor turns this
# number non-zero on the very next solve and fails the verifier.
REDUCER_CLAMP_MIN = "CLAMP_MIN"                # RETIRED width floor (always 0.0)
REDUCER_CLAMP_MAX = "CLAMP_MAX"                # physical field cap
REDUCER_ROOM_CAP = "ROOM_CAP"                  # physical available room
REDUCER_NO_BALL_CARRIER = "NO_BALL_CARRIER"    # command undefined
REDUCER_LANE_STEP_FLOOR = "LANE_STEP_FLOOR"    # min_defender_gap_m floor

ALL_REDUCERS = (
    REDUCER_SPLIT_FACTOR,
    REDUCER_CLAMP_MIN,
    REDUCER_CLAMP_MAX,
    REDUCER_ROOM_CAP,
    REDUCER_NO_BALL_CARRIER,
    REDUCER_LANE_STEP_FLOOR,
)


# ─────────────────────────────────────────────────────────────────────
# TUNER / WORLD ACCESS
# ─────────────────────────────────────────────────────────────────────

def _tuners(world, tuners=None):
    """Return the live DefenceTuners for *world* (or the passed tuners)."""
    if tuners is not None:
        return tuners
    resolved = getattr(world, "_defence_tuners", None)
    if resolved is not None:
        return resolved
    from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
    return DefenceTuners()


def _governed_width_m(world, tuners) -> float:
    """The governed width — delegated to the single shipped accessor.

    ``defensive_width.governed_width_m`` is the ONE implementation
    (GOVERNED_ACCESSOR_DIVERGENCE removal): the governor's method delegates
    here too, so the two can never disagree.
    """
    from game.defence.defence_lvl_1.defensive_width import governed_width_m
    return float(governed_width_m(world, tuners))


def _commanded_width_m(world, tuners):
    """The 3-point commanded width at the live carrier X, or ``None``.

    ``interpolated_defensive_width(tuners, bc.x)`` is the sole command.  With
    no carrier the command is UNDEFINED — reported as ``None`` and named
    ``NO_BALL_CARRIER`` rather than silently reusing a stale stored value.
    """
    bc = getattr(world, "ball_carrier", None)
    bc_x = getattr(bc, "x", None) if bc is not None else None
    if bc_x is None:
        return None
    from game.defence.defence_lvl_1.defensive_width import (
        interpolated_defensive_width,
    )
    return float(interpolated_defensive_width(tuners, float(bc_x)))


def _ruck_y(world, ruck_y) -> float:
    """The scope's ruck Y (explicit argument wins)."""
    if ruck_y is not None:
        return float(ruck_y)
    return float(getattr(world, "ruck_y_m", RUCK_Y_FALLBACK))
# ─────────────────────────────────────────────────────────────────────
# SHORT-SIDE AUTHORITY (moved here from defensive_split_spacing, Stage 6)
# ─────────────────────────────────────────────────────────────────────

def resolve_short_side(world, tuners, ruck_y: float | None = None) -> int:
    """Return LEFT/RIGHT of the SHORT SIDE (nearest sideline to the ruck).

    SINGLE AUTHORITY for short-side determination.  This lived in
    ``defensive_split_spacing``; Stage 6 moved the computation to the width
    authority so the per-side width, the count split and the edge-release
    ball side all read ONE resolver.  ``defensive_split_spacing`` re-exports
    this function unchanged for its existing callers.

    The side whose distance to the nearest usable sideline is smaller is the
    short side.  Falls back to LEFT on stub worlds with no ruck geometry.
    """
    margin = float(getattr(tuners, "sideline_margin_m",
                           SIDELINE_MARGIN_FALLBACK))
    field_max = float(getattr(tuners, "field_width_m",
                             FIELD_WIDTH_FALLBACK)) - margin
    scope_ruck_y = _ruck_y(world, ruck_y)
    dist_to_left = max(0.0, scope_ruck_y - margin)
    dist_to_right = max(0.0, field_max - scope_ruck_y)
    return LEFT if dist_to_left <= dist_to_right else RIGHT


# ─────────────────────────────────────────────────────────────────────
# THE PER-SIDE SPLIT SOLVE (the shipped split-spacing body, preserved)
# ─────────────────────────────────────────────────────────────────────
# The arithmetic below is the body that used to live in
# ``defensive_split_spacing.side_widths`` — short/open split factor, the
# physical available-room cap and the sideline safety cap.  The former
# width-band tuner clamp is REMOVED with the tuners (2026-09-14, Option B):
# the width curve is the sole span authority.  The ONE correction is that
# the room cap now uses the SCOPE'S ruck Y (the ``ruck_y`` argument or the
# world ruck), where the shipped body silently re-read ``world.ruck_y_m``
# and discarded its own ``ruck_y`` argument (Stage 1 RUCK_Y_ARG_IGNORED).
# Every shipped caller passes the world ruck, so this is behaviour-identical
# for all of them; only the TES chamber's latched band reference — the one
# caller that legitimately passes a different ruck Y — now gets a room cap
# consistent with the ladder it is building.

def distribute_total_span(total_m: float, room_left: float,
                          room_right: float) -> tuple:
    """Split a TOTAL span across the two ruck-anchored halves.

    The governed width is the authoritative TOTAL winger-to-winger span, and
    the lane ladder is built OUTWARD FROM THE RUCK on each side, so the two
    halves are not independent: the realised line spans
    ``half_left + half_right``, and each half is bounded by its own touchline
    margin (``room_left`` / ``room_right`` — the margin-only usable distance
    from the scope ruck).

    Starting from the even split, the balance is handed to whichever side
    still has room.  Only when BOTH halves are exhausted does the total fall
    below ``total_m``; that residual is the absolute sideline safety clamp and
    the ONE permitted reduction of the realised span.

    Returns ``(half_left, half_right)`` in metres.
    """
    total = max(0.0, float(total_m))
    room_l = max(0.0, float(room_left))
    room_r = max(0.0, float(room_right))
    half = total * 0.5
    half_l = min(half, room_l)
    half_r = min(half, room_r)
    shortfall = total - (half_l + half_r)
    if shortfall > 1e-12:
        if (room_l - half_l) >= (room_r - half_r):
            half_l = min(room_l, half_l + shortfall)
            half_r = min(room_r, total - half_l)
        else:
            half_r = min(room_r, half_r + shortfall)
            half_l = min(room_l, total - half_r)
    return float(half_l), float(half_r)


def _solve_side_widths(world, tuners, scope_ruck_y: float,
                       short_side: int) -> dict:
    """Return the split solve and its named reducer decomposition."""
    effective = _governed_width_m(world, tuners)

    # ── GOVERNED WIDTH IS THE AUTHORITATIVE TOTAL SPAN (2026-09-14) ──
    # Both sides carry EXACTLY the governed width.  The two reducers that
    # used to narrow a side below it are REMOVED:
    #   * the short/open SPLIT FACTOR (``tuner_short_side_factor`` 0.65);
    #   * the available-ROOM cap (2 x distance to the usable touchline).
    # Nothing below the absolute sideline safety clamp may make the realised
    # lane span smaller than the governed width, so the outermost left/right
    # lanes span exactly ``governed_width_m(world, tuners)`` — the
    # interpolated command at the live bc.x the governor published.
    margin = float(getattr(tuners, "sideline_margin_m",
                           SIDELINE_MARGIN_FALLBACK))
    field_max = float(getattr(tuners, "field_width_m",
                             FIELD_WIDTH_FALLBACK)) - margin

    # The governed width has already been through the module accessor — the
    # physical field cap and nothing else, since the width rails were removed
    # (2026-09-14, Option B) — so no per-side clamp is restated here: applied
    # to a half-span it would FIGHT the total
    # span on any line that has to slide toward one touchline.  Keeping
    # ``split_* == clamped_* == effective`` is what makes the reducer ledger's
    # SPLIT_FACTOR and clamp entries exactly 0.0, so the margin-only room
    # bound below is the ONLY named reducer left in the chain.
    split_left = float(effective)
    split_right = float(effective)
    clamped_left = float(split_left)
    clamped_right = float(split_right)

    # ── ABSOLUTE SIDELINE SAFETY CLAMP = THE ONLY PERMITTED REDUCER ──
    # Each side's half-span may never reach past its own touchline margin, so
    # the margin-to-margin usable distance from the scope ruck is the hard
    # room bound:
    #
    #     room_left  = ruck_y - margin
    #     room_right = (field_width - margin) - ruck_y
    #
    # The governed width is the TOTAL span, so it is DISTRIBUTED across the
    # two halves under those bounds: from the even split, whichever side
    # still has room absorbs the balance.  A side keeps its governed
    # half-span whenever its own room allows it, and the line slides toward
    # the roomier touchline instead of the short side collapsing while the
    # far winger overruns the field.  Because the ladder is ruck-anchored,
    # this room bound is the only reduction physically available.
    #
    # ``short_side`` is retained in the signature for call-site compatibility
    # — the per-side span no longer depends on which side is short.
    room_left = max(0.0, float(scope_ruck_y) - margin)
    room_right = max(0.0, field_max - float(scope_ruck_y))
    # ── EVEN SPLIT — THREE-POINT WIDTH IS THE SOLE AUTHORITY (2026-09-15) ──
    # The governed width W is the TOTAL span and is divided EVENLY: both
    # sides receive EXACTLY W/2.  The old ``distribute_total_span`` handed
    # the balance of the span to whichever side had more room, which made
    # the near-touchline side's span (and therefore its lane gap) differ
    # from the far side's — the open/short asymmetry.  That is REMOVED.
    # The lane builder now lays EVERY defender out on ONE even ladder
    # across W (defensive_split_spacing.global_lanes), so the per-side
    # halves below exist only so ``world.formation_width`` consumers cannot
    # divide by zero; they are equal by construction.
    half_left = float(effective) * 0.5
    half_right = float(effective) * 0.5
    left_w = half_left * 2.0
    right_w = half_right * 2.0

    return {
        "left_m": float(left_w),
        "right_m": float(right_w),
        "_effective_m": float(effective),
        "_split_left_m": float(split_left),
        "_split_right_m": float(split_right),
        "_clamped_left_m": float(clamped_left),
        "_clamped_right_m": float(clamped_right),
        "_room_left_m": float(room_left),
        "_room_right_m": float(room_right),
    }


def _mean2(a: float, b: float) -> float:
    """Mean of a pair — the scalar span the reducer ledger accounts for."""
    return 0.5 * (float(a) + float(b))


def _reducer_ledger(commanded, solve: dict) -> dict:
    """Decompose ``commanded -> published`` into the NAMED reducers.

    Each entry is the signed metre delta the step ADDED to the running
    span, so the chain telescopes exactly:

        governed_mean  = commanded + CLAMP_MIN|CLAMP_MAX
        split_mean     = governed  + SPLIT_FACTOR
        clamped_mean   = split     + CLAMP_MIN|CLAMP_MAX
        published_mean = clamped   + ROOM_CAP

    The verifier therefore asserts
    ``published_mean == commanded + sum(reducers.values())`` EXACTLY — no
    UNATTRIBUTED remainder.

    GOVERNED WIDTH IS THE AUTHORITATIVE TOTAL SPAN (2026-09-14): the
    short/open SPLIT FACTOR and the available-ROOM cap are NO LONGER span
    reducers, so their entries are now published as EXACTLY 0.0 rather than
    being dropped.  Keeping them named and zero-valued is deliberate: the
    ledger stays a COMPLETE decomposition with no anonymous residue, and any
    future edit that re-introduces a span reducer has to move one of these
    numbers off zero — which the closure identity and the verifiers both
    catch immediately.

    With no carrier the command is undefined and the ledger carries the
    single zero-valued ``NO_BALL_CARRIER`` marker instead.
    """
    governed = solve["_effective_m"]
    split_mean = _mean2(solve["_split_left_m"], solve["_split_right_m"])
    clamped_mean = _mean2(solve["_clamped_left_m"], solve["_clamped_right_m"])
    published_mean = _mean2(solve["left_m"], solve["right_m"])

    if commanded is None:
        # The command is undefined (no carrier); nothing to decompose.  The
        # span is still published from the stored world width, and the
        # condition is named so the verifier can skip the identity.
        return {REDUCER_NO_BALL_CARRIER: 0.0}

    ledger: dict = {}

    # 1. GOVERNED delta — the physical field cap is the ONLY bound left (the
    #    width-band tuner clamp is gone), so a positive delta here means the
    #    wave had to be folded into the field.
    governed_delta = float(governed) - float(commanded)
    if governed_delta > 0.0:
        ledger[REDUCER_CLAMP_MIN] = governed_delta
    elif governed_delta < 0.0:
        ledger[REDUCER_CLAMP_MAX] = governed_delta

    # 2. SPLIT FACTOR — NEUTRALISED: the per-side span IS the governed
    #    width, so this delta is identically 0.0.
    ledger[REDUCER_SPLIT_FACTOR] = split_mean - float(governed)

    # 3. Per-side min/max clamp on the (already governed) side spans — an
    #    idempotent restatement of step 1, so normally 0.0.
    clamp_delta = clamped_mean - split_mean
    if abs(clamp_delta) > 1e-12:
        _acc(ledger, REDUCER_CLAMP_MIN if clamp_delta > 0.0
             else REDUCER_CLAMP_MAX, clamp_delta)

    # 4. ROOM CAP — NEUTRALISED: only the absolute sideline safety clamp may
    #    reduce the span, so this delta is identically 0.0 here.
    ledger[REDUCER_ROOM_CAP] = published_mean - clamped_mean

    return ledger


def _acc(ledger: dict, name: str, delta: float) -> None:
    """Accumulate one named delta into *ledger* (never storing a no-op)."""
    ledger[name] = ledger.get(name, 0.0) + float(delta)
# ─────────────────────────────────────────────────────────────────────
# THE SINGLE PER-(FRAME, SCOPE) PUBLICATION
# ─────────────────────────────────────────────────────────────────────

def _tuner_signature(tuners) -> tuple:
    """The width-relevant tuner values, so a mid-frame tuner edit re-solves.

    Only tuners the split / ladder / command actually read are included; an
    unrelated tuner change must not invalidate the frame's width fact.
    """
    # WIDTH-RAIL REMOVAL (2026-09-14, Option B): the minimum-floor and
    # maximum-ceiling width tuners, ``short_side_buffer_m`` and the two split
    # factors are GONE from this signature.  None of them can change a
    # published span any more, so including them let an unrelated tuner edit
    # invalidate the frame's width fact — pure investigation noise.  Only
    # tuners with a live span effect remain.
    # FOUR-ANCHOR COMPRESSION MODEL (2026-10-03): the defending-tryline
    # width REGION and the two goal-line COMPRESSION knobs are part of the
    # curve, so a goal-line squeeze edit must invalidate the frame's width
    # fact and re-solve it.  ``width_defending_tryline_m`` is the region
    # alias (a property on DefenceTuners) and the two ``*_compression``
    # values are the independent tryline / 10 m squeeze knobs.
    names = (
        "field_width_m",
        "sideline_margin_m",
        "width_left_tryline_m",
        "width_halfway_m",
        "width_right_tryline_m",
        "width_defending_tryline_m",
        "defending_tryline_compression",
        "defending_10m_compression",
        "min_defender_gap_m",
        "defender_gap_m",
    )
    return tuple(round(float(getattr(tuners, n, 0.0) or 0.0), 6)
                 for n in names)


def _publication_key(world, tuners, scope_ruck_y: float) -> tuple:
    """Immutable identity of one (frame, scope) width solve."""
    bc = getattr(world, "ball_carrier", None)
    bc_x = getattr(bc, "x", None) if bc is not None else None
    return (
        getattr(world, "frame_count", None),
        round(float(scope_ruck_y), 6),
        round(float(getattr(world, "ruck_x_m", 0.0) or 0.0), 6),
        None if bc_x is None else round(float(bc_x), 6),
        bool(getattr(world, "governor_width_active", False)),
        round(float(getattr(world, "governor_line_width_m", 0.0) or 0.0), 6),
        round(float(getattr(world, "def_line_width_m", 0.0) or 0.0), 6),
        _tuner_signature(tuners),
    )


def _primary_scope(world) -> float:
    """The scope ruck Y of the PRIMARY publication (the world ruck)."""
    return float(getattr(world, "ruck_y_m", RUCK_Y_FALLBACK))


def _record_solve(world, publication: dict) -> int:
    """Append the solve to the frame ledger; return its 1-based index.

    The ledger is reset whenever the frame advances, so
    :func:`conflicting_width_solves` can prove that no two DIFFERENT width
    values were ever published for the SAME scope in one frame.
    """
    frame = getattr(world, "frame_count", None)
    if getattr(world, "_formation_width_solve_frame", None) != frame:
        world.formation_width_solves = []
        world._formation_width_solve_frame = frame
    solves = getattr(world, "formation_width_solves", None)
    if not isinstance(solves, list):
        solves = []
        world.formation_width_solves = solves
    solves.append({
        "frame": frame,
        "key": publication["key"],
        "scope_ruck_y": publication["scope_ruck_y"],
        "left_m": publication["left_m"],
        "right_m": publication["right_m"],
        "reused": bool(publication.get("reused", False)),
    })
    return len(solves)


def publish_formation_width(world, tuners=None, *, ruck_y: float | None = None,
                            force: bool = False) -> dict:
    """Publish THE frame's line width for one scope — once.

    Args:
        world: WorldState (or stub).
        tuners: DefenceTuners (resolved from the world when omitted).
        ruck_y: the scope's ruck Y.  Omitted -> ``world.ruck_y_m`` (the
            PRIMARY scope).  A caller passing a DIFFERENT ruck (the TES
            chamber's latched band reference) gets that scope's own
            publication; the primary is never overwritten by it.
        force: re-solve even on a cache hit.

    Returns the publication dict::

        {key, frame, scope_ruck_y, commanded_m, governed_m, left_m, right_m,
         short_side, span_contract, reducers, lane_step_floor_m, reused,
         solve_index}

    Side-effects (the ONLY writes this module performs):
        ``world.formation_width`` / ``formation_width_frame`` /
        ``formation_width_publish_count`` for the PRIMARY scope;
        ``world._formation_width_by_scope`` — the scope cache;
        ``world.formation_width_solves`` — the per-frame solve ledger.
    """
    tuners = _tuners(world, tuners)
    scope_ruck_y = _ruck_y(world, ruck_y)
    key = _publication_key(world, tuners, scope_ruck_y)

    scope_map = getattr(world, _FORMATION_WIDTH_SCOPE_ATTR, None)
    if not isinstance(scope_map, dict):
        scope_map = {}
        setattr(world, _FORMATION_WIDTH_SCOPE_ATTR, scope_map)

    cached = scope_map.get(key)
    if cached is not None and not force:
        # Cache HIT: the width is already this frame's single fact for this
        # scope.  Record the request (reused) so the ledger shows the read.
        cached["reused"] = True
        _record_solve(world, cached)
        _mirror_primary(world, cached, scope_ruck_y)
        return cached

    commanded = _commanded_width_m(world, tuners)
    short_side = resolve_short_side(world, tuners, ruck_y=scope_ruck_y)
    solve = _solve_side_widths(world, tuners, scope_ruck_y, short_side)
    reducers = _reducer_ledger(commanded, solve)

    from game.defence.defence_lvl_1.span_contract import SPAN_SOURCE_PUBLISHED
    contract = SpanContract(
        left_m=float(solve["left_m"]),
        right_m=float(solve["right_m"]),
        source=SPAN_SOURCE_PUBLISHED,
    )

    publication = {
        "key": key,
        "frame": getattr(world, "frame_count", None),
        "scope_ruck_y": float(scope_ruck_y),
        "commanded_m": None if commanded is None else float(commanded),
        "governed_m": float(solve["_effective_m"]),
        "left_m": float(solve["left_m"]),
        "right_m": float(solve["right_m"]),
        "short_side": int(short_side),
        "span_contract": contract,
        "reducers": reducers,
        "lane_step_floor_m": float(
            getattr(tuners, "min_defender_gap_m", 3.0) or 0.0
        ),
        "reused": False,
        "solve_index": 0,
    }
    publication["solve_index"] = _record_solve(world, publication)

    scope_map[key] = publication
    _mirror_primary(world, publication, scope_ruck_y)
    return publication


def _mirror_primary(world, publication: dict, scope_ruck_y: float) -> None:
    """Expose *publication* as the frame's PRIMARY width when it is one.

    A scoped solve (the TES chamber's latched ruck) must NOT overwrite the
    primary fact, so the mirror is written only when the scope's ruck Y is
    the world ruck.
    """
    if abs(float(scope_ruck_y) - _primary_scope(world)) > 1e-9:
        return
    world.formation_width = publication
    world.formation_width_frame = getattr(world, "frame_count", None)
    world.formation_width_publish_count = int(publication["solve_index"])


# ─────────────────────────────────────────────────────────────────────
# READ-BACK — the API every consumer uses
# ─────────────────────────────────────────────────────────────────────

def published_formation_width(world) -> dict | None:
    """Return the PRIMARY publication, publishing it if the frame has none.

    Consumers that must reason about the frame's width read THIS rather than
    recomputing: a caller never sees a different value from the one the
    pipeline published.
    """
    publication = getattr(world, FORMATION_WIDTH_ATTR, None)
    if isinstance(publication, dict) and width_publication_is_fresh(world):
        return publication
    try:
        return publish_formation_width(world)
    except SpanContractError:
        return publication if isinstance(publication, dict) else None


def published_side_widths(world, tuners=None, *,
                          ruck_y: float | None = None) -> tuple:
    """Return the published ``(left_m, right_m)`` span pair for one scope."""
    publication = getattr(world, FORMATION_WIDTH_ATTR, None)
    if (ruck_y is None and isinstance(publication, dict)
            and width_publication_is_fresh(world)):
        return float(publication["left_m"]), float(publication["right_m"])
    publication = publish_formation_width(world, tuners, ruck_y=ruck_y)
    return float(publication["left_m"]), float(publication["right_m"])


def published_span_contract(world, tuners=None, *,
                            ruck_y: float | None = None) -> SpanContract:
    """Return the published :class:`SpanContract` for one scope."""
    publication = getattr(world, FORMATION_WIDTH_ATTR, None)
    if (ruck_y is None and isinstance(publication, dict)
            and width_publication_is_fresh(world)):
        return publication["span_contract"]
    publication = publish_formation_width(world, tuners, ruck_y=ruck_y)
    return publication["span_contract"]


def width_publication_is_fresh(world) -> bool:
    """True when the PRIMARY publication belongs to THIS frame."""
    publication = getattr(world, FORMATION_WIDTH_ATTR, None)
    if not isinstance(publication, dict):
        return False
    return publication.get("frame") == getattr(world, "frame_count", None)


def formation_width_value(world, tuners=None) -> float:
    """Return the governed width (m) — the scalar the line is built across."""
    publication = published_formation_width(world)
    if isinstance(publication, dict):
        return float(publication["governed_m"])
    return _governed_width_m(world, _tuners(world, tuners))


# ─────────────────────────────────────────────────────────────────────
# LANE STEP — the span-owned uniform step (LANE_STEP_FLOOR provenance)
# ─────────────────────────────────────────────────────────────────────

def lane_step_for(publication: dict, side: int, n_side: int,
                  tuners) -> float:
    """Return the uniform lane step (m) a *n_side* count gets on *side*.

    This is the DERIVED step of the published span —
    ``(span / 2 - RUCK_ADJACENT_ANCHOR_M) / (n_side - 1)`` — and it is NOT
    clamped, exactly mirroring ``defensive_split_spacing.lane_gap_m``.

    GOVERNED WIDTH = AUTHORITATIVE TOTAL SPAN (2026-09-14): the
    ``min_defender_gap_m`` FLOOR is REMOVED.  When it bound, the step was
    pinned above ``span/(n_side-1)`` so the outer lane overran the span, hit
    the sideline clamp and re-spaced INWARD — the ladder stopped spanning the
    published width.  The ``max_defender_gap_m`` CEILING is removed for the
    same reason on a wide span.  The published span now owns the ladder end
    to end, so ``lane[n_side-1] == ruck_y +/- span / 2`` exactly.
    """
    if n_side is None or int(n_side) < 2:
        return 0.0
    span = (float(publication["right_m"]) if int(side) > 0
            else float(publication["left_m"]))
    usable = abs(span) * 0.5 - 2.0
    if usable <= 0.0:
        return 0.0
    return usable / float(int(n_side) - 1)


def lane_step_floor_binds(publication: dict, side: int, n_side: int,
                          tuners) -> bool:
    """True when ``min_defender_gap_m`` FLOORS the span-derived step.

    GOVERNED WIDTH = AUTHORITATIVE TOTAL SPAN (2026-09-14): the floor is
    REMOVED from ``lane_gap_m`` / ``lane_step_for``, so it can no longer own
    any ladder.  The predicate is retained because diagnostics and verifiers
    read it, and it now truthfully reports False for every ladder: the step
    is never floored, at any span or count.
    """
    return False


# ─────────────────────────────────────────────────────────────────────
# VERIFIER HOOKS
# ─────────────────────────────────────────────────────────────────────

def width_solves_for_frame(world) -> list:
    """Return this frame's solve ledger (``[]`` when none was recorded)."""
    if getattr(world, "_formation_width_solve_frame", None) != getattr(
        world, "frame_count", None
    ):
        return []
    solves = getattr(world, FORMATION_WIDTH_SOLVES_ATTR, None)
    return list(solves) if isinstance(solves, list) else []


def conflicting_width_solves(world) -> list:
    """Return any (scope, two different spans) pair recorded this frame.

    A conflict is two ledger entries with the SAME scope ruck Y whose
    published span differs beyond float noise — the exact multi-publisher
    width signature Stage 6 removes.  Returns a list of
    ``(scope_ruck_y, (left_a, right_a), (left_b, right_b))``.
    """
    by_scope: dict = {}
    conflicts = []
    for entry in width_solves_for_frame(world):
        scope = round(float(entry["scope_ruck_y"]), 6)
        pair = (round(float(entry["left_m"]), 6),
                round(float(entry["right_m"]), 6))
        prior = by_scope.get(scope)
        if prior is None:
            by_scope[scope] = pair
        elif prior != pair:
            conflicts.append((scope, prior, pair))
    return conflicts
