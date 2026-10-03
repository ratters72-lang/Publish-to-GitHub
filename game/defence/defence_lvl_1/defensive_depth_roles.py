"""
defensive_depth_roles.py — Persistent, hysteresis-protected depth-role assignment.

PURPOSE
-------
The defensive engine has two independent role systems:

  1. Engagement roles (PRIMARY / SECONDARY / PASSIVE) — set by
     DefensiveBehaviourUnified.classify_depth_all().  These govern
     chase/close/retreat behaviour and do NOT define depth or spacing.

  2. Depth roles (A/B/C/D/CENTRE/WINGER) — consumed by
     DefensiveGeometry (role width anchors, role depth) and by
     ROLE_STEER_FACTOR (chase steering).  These DO define depth,
     spacing, width curve, and defensive shape.

This module is the SINGLE source of the depth-role system — ruck-driven,
geometry-driven, width-driven role assignment (issue spec 2026-08-08,
revised 2026-08-09), with BANDED BC-RELATIVE interior assignment
(2026-08-12, depth-role misassignment fix).

BANDED A/B/C/D (2026-08-12)
---------------------------
The previous interior assignment sorted A/B/C/D by raw X-distance to the
ruck (``abs(d.x - ruck_x)``).  On a flat defensive line every defender
sits on nearly the same X, so that sort was arbitrary and produced
unstable patterns (A, D, C) instead of the intended bands — and the
wrong roles then fed wrong spacing Y, wrong width, wrong target_y, and
wrong tackler selection ("tacklers go to the wrong spot").

Interior roles are now assigned by Y-distance to the BALL CARRIER:

    A band: closest middle defender on each side
    B band: next middle defender on each side
    C/D:    outer structure

The band reference is ``bc.y`` (fallback ``ruck_y_m`` when there is no
ball carrier, e.g. kickoff/catch bootstrap).  This guarantees the
defenders closest to the ball-carrier's lateral position — the ones who
must engage at first contact — are the A/B defenders, and the spacing /
width / target_y chain computes from the correct roles on the catch→
OPEN_PLAY frames BEFORE the first tackle.

TASK A (2026-08-09) — STABILISED ROLE ASSIGNMENT
-------------------------------------------------
The previous revision recomputed every defender's role EVERY call from
live positions:

    WINGER/CENTRE = sorted by abs(d.y - ruck_y, descending)
    A/B/C         = sorted by abs(d.x - ruck_x, ascending)
    side          = d.y < ruck_y

That made roles (and therefore width anchors / geometry targets) flip
whenever defenders crossed ruck_y or ruck_x, with no hysteresis and no
persistence.  Task A replaces positional role assignment with PERSISTENT
STRUCTURAL role assignment:

  - Every defender owns a persistent ``depth_role`` + ``depth_side``
    pair stored on the Player object (``player.depth_role`` /
    ``player.depth_side``).  The pair survives across frames and tackle
    cycles and is independent of defender ordering.
  - Side is role-LOCKED: a LEFT defender flips to RIGHT only when it
    crosses ruck_y by more than ``SIDE_SWAP_HYSTERESIS_M`` (2.0 m); the
    opposite direction uses the same threshold.
  - Roles re-assign only under hysteresis: a defender whose band-track
    gap exceeds ``ROLE_SWAP_HYSTERESIS_M`` (3.0 m) is eligible for a
    vacant slot; settled defenders are never reassigned.  When every slot
    is occupied, roles are immutable.
  - Role anchors are STRUCTURAL and ruck-relative: they are computed
    once per ruck position (``ruck_x_m``, ``ruck_y_m``,
    ``def_line_width_m`` — cached) using the SAME formula
    DefensiveGeometry uses:

        anchor_y = ruck_y + side * half_width
                   * role_width_anchor[role]
                   * width_curve_factor[role]

    Anchors never depend on defender positions or defender ordering.

BOOTSTRAP MODE
--------------
On the first call for a line (no defender has a seeded assignment yet),
there is no history to persist — so the legacy positional algorithm runs
ONCE to seed the initial (role, side) pair for every eligible defender.
After that, all calls run persistent maintenance.  This keeps
creation-time behaviour deterministic and makes the initial line
formation deterministic.

The bootstrap interior ordering is Y-distance to the ball carrier
(2026-08-12) so the initial formation — including the kickoff-catch
re-seed — immediately produces the correct A/A/B/B band structure.

MODE SELECTION
--------------
    unseeded = any(d._depth_seeded is False for eligible defenders)

If ANY eligible defender is unseeded, the whole eligible set is
re-bootstrapped positionally (players are only newly-constructed at
kickoff, so this happens once per kickoff).  Otherwise persistent
maintenance runs and no positional sorting occurs.

EDGE RELEASE — CENTRE/WINGER → INTERIOR CONVERSION (2026-08-26, TASK XI)
------------------------------------------------------------------------
When the ball carrier is deep enough toward ONE side of the ruck
(|bc.y − ruck_y| ≥ ``collapse_edge_release_offset_m``, hysteresis-
protected via ``resolve_edge_release``), positional identity no longer
matters there: the CENTRE/WINGER slots ON THE BALL SIDE dissolve and
their holders are reinserted into the interior depth stack by pure
Y-axis ordering — closest-to-ruck becomes A, the next B, then C and D.
The weak side keeps its canonical edge structure.  Both allocation
paths honour the mode:

  - bootstrap: the released side seeds NO WINGER/CENTRE — the whole
    side sorts into A/B/C/D lanes.
  - maintenance: stale W/C holders are demoted to D (collapse-locked
    holders exempt until they exit the lock radius), interior vacancies
    fill as usual, and the ordered output omits W/C.

The mode is derived INSIDE ``assign_depth_roles`` (single writer in
ball_side_collapse.resolve_edge_release), so every caller — per-frame
preamble, dynamic refit, reband, geometry chokepoint — sees ONE unified
depth-role model.  Mode transitions invalidate exactly the affected
side's seeds, triggering the deterministic whole-line re-bootstrap.

API
---
``assign_depth_roles(defenders, world)`` — assigns persistent depth
roles from the ruck/geometry + ball-carrier band and returns the ordered
per-side width-curve input (defender objects, single-capacity roles then
D's):

    {"LEFT":  [WINGER_L, CENTRE_L, A_L, B_L, C_L] + D_L,
     "RIGHT": [WINGER_R, CENTRE_R, A_R, B_R, C_R] + D_R}
"""

import math

from player.player import DefenderRole, Player
# TASK XI (2026-08-26): collapse-lock exemption for edge-release
# conversion — a defender engaged at contact (within COLLAPSE_LOCK_
# RADIUS_M of the BC) keeps its role until it exits the lock radius,
# identical principle to the Task X dynamic-refit guard.
from player.collapse_controls import depth_role_locked
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_width import (
    role_anchor_y_for,
    depth_side_of,
    governed_width_m,
    is_drive_phase,
)
from game.defence.defence_lvl_1.anchor_authority import (
    defensive_line_anchor_m as _anchor_m,
)
# TASK VIII (2026-08-21): COUNT-DRIVEN SIDE ASSIGNMENT — the split runs
# BEFORE any role seeding.  defensive_split_spacing imports only
# defensive_width + anchor_authority, so this import cannot cycle.
from game.defence.defence_lvl_1.defensive_split_spacing import (
    assign_split_sides,
    resolve_short_side,
    side_widths,
)

# Ruck position fallbacks when the caller's world stub omits the ruck
# attributes (verification scripts / unit tests).
RUCK_X_FALLBACK: float = 60.0
RUCK_Y_FALLBACK: float = 35.0
DEF_LINE_WIDTH_FALLBACK: float = 40.0

# ── TASK A-2: ROLE-DEPTH X OFFSET (metres) ──────────────────────────
# The defensive-line depth anchor for the depth-role system is ALWAYS
# ruck_x + ROLE_DEPTH_X_OFFSET_M (= ruck_x + 10).  It is structural and
# ruck-relative — NEVER recomputed from BC.x, NEVER drifting behind BC.
# Stamped as ``player.role_depth_target_x`` once per assign_depth_roles()
# call (spec Task A-2 step 8: computed from the stable role; no
# recomputation mid-frame or after spacing).
ROLE_DEPTH_X_OFFSET_M: float = 10.0

# ── TASK A HYSTERESIS THRESHOLDS (metres) ─────────────────────────────
# A defender crosses to the other side only when it passes ruck_y by more
# than SIDE_SWAP_HYSTERESIS_M.  Minor positional drift never changes side.
#
# TASK IV (2026-08-14): reduced 2.0 -> 1.2 m (×0.60) so the defensive line
# reacts faster to a ball carrier crossing the ruck lateral line — side
# swaps and therefore spacing-Y updates happen earlier after a catch
# (spacing updates earlier due to reduced hysteresis).
SIDE_SWAP_HYSTERESIS_M: float = 1.2

# A defender may be considered for a different role only when it has moved
# more than ROLE_SWAP_HYSTERESIS_M from its assigned structural anchor.
# Settled defenders are never reassigned.
ROLE_SWAP_HYSTERESIS_M: float = 3.0

# Valid roles + the single-capacity roles (one slot each per side).
VALID_ROLES = frozenset({"A", "B", "C", "D", "CENTRE", "WINGER"})
CANONICAL_SINGLE_ROLES = ("WINGER", "CENTRE", "A", "B", "C")

# Roles that never receive a depth role.  They play tight to the ruck
# (markers/DH) or are engaged in the tackle (tacklers) and must not be
# redistributed into the defensive line shape.
# STAGE 5 (2026-09-13): the canonical line-exclusion set now lives in
# ``population_authority.LINE_EXCLUDED_ROLES`` — the SINGLE definition the
# count, side, spacing and observer layers all read.  It is the historical
# set PLUS ``DEF_CHASE`` (the count previously omitted DEF_CHASE while the
# spacing authority excluded it, so a DEF_CHASE body was counted but never
# spaced — one of the four-set divergences Stage 4 measured).  Re-exported
# under the historical name so existing importers (defensive_rebanding,
# ball_side_collapse, verifiers) keep working against ONE source.
from game.defence.defence_lvl_1.population_authority import (
    LINE_EXCLUDED_ROLES as EXCLUDED_ROLES,
)

# Structural ruck-anchor cache.  Keyed by the ruck geometry + the tuner
# tables that generated the anchors; rebuilt only when the ruck moves or
# the tuner values change.  Independent of defender positions/ordering.
_ANCHOR_CACHE: dict = {}


# ─────────────────────────────────────────────────────────────────────
# STRUCTURAL RUCK-RELATIVE ANCHORS
# ─────────────────────────────────────────────────────────────────────

def _resolve_tuners(world):
    """Return the live DefenceTuners (engine singleton when available)."""
    ope = getattr(world, "open_play_engine", None)
    tuners = getattr(ope, "def_tuners", None) if ope is not None else None
    if tuners is None:
        tuners = DefenceTuners()
    return tuners


def _tuner_identity(tuners) -> tuple:
    """Immutable identity of the anchor-relevant tuner tables."""
    return (
        tuple(sorted(
            (k, float(v)) for k, v in getattr(tuners, "role_width_anchor", {}).items()
        )),
        tuple(sorted(
            (k, float(v)) for k, v in getattr(tuners, "width_curve_factor", {}).items()
        )),
        float(getattr(tuners, "sideline_margin_m", 3.0)),
        float(getattr(tuners, "field_width_m", 70.0)),
    )


def _anchor_cache_key(ruck_x: float, ruck_y: float,
                      left_width: float, right_width: float,
                      tuners) -> tuple:
    return (
        round(ruck_x, 3),
        round(ruck_y, 3),
        round(left_width, 3),
        round(right_width, 3),
        _tuner_identity(tuners),
    )


def _build_role_anchors(ruck_y: float, left_width: float,
                        right_width: float, tuners) -> dict:
    """Compute structural anchors {side: {role: anchor_y}} for one ruck.

    Delegates to the CANONICAL width formula in defensive_width.py — the
    exact same ``role_anchor_y_for`` used by DefensiveGeometry at runtime:

        anchor_y = role_anchor_y_for(role, side, ruck_y,
                                     side_width, tuners)

    ``side_width`` is the PER-SIDE width (left_width for LEFT, right_width
    for RIGHT) from ``side_widths()`` — the SAME per-side width the spacing
    chain consumes.  Previously a single ``def_line_width`` (FULL governed
    width) was used for BOTH sides, so the short-side role anchors (WINGER
    at the margin, e.g. 3.0) disagreed with the spacing chain's WINGER
    target (margin+buffer, e.g. 6.0) — two different side_width values in
    the chain.  Using the per-side widths makes the depth-role anchors and
    the spacing targets provably consistent (2026-09-02).

    Anchors are pure functions of ruck geometry + per-side width + tuners
    — never of defender positions or ordering.
    """
    anchors = {}
    for side in (-1, 1):
        side_anchors = {}
        side_width = left_width if side == -1 else right_width
        for role in VALID_ROLES:
            side_anchors[role] = role_anchor_y_for(
                role, side, ruck_y, side_width, tuners
            )
        anchors[side] = side_anchors
    return anchors


def _get_role_anchors(ruck_x: float, ruck_y: float,
                      left_width: float, right_width: float,
                      tuners) -> dict:
    """Return the structural anchors for the current ruck (cached)."""
    key = _anchor_cache_key(ruck_x, ruck_y, left_width, right_width, tuners)
    cached = _ANCHOR_CACHE.get(key)
    if cached is None:
        cached = _build_role_anchors(ruck_y, left_width, right_width, tuners)
        _ANCHOR_CACHE[key] = cached
    return cached


# ─────────────────────────────────────────────────────────────────────
# BOOTSTRAP — one-time legacy positional seed (creation only)
# ─────────────────────────────────────────────────────────────────────

def _bootstrap_side_roles(side_defs, side: int, ruck_y: float, anchors: dict,
                          edge_release: bool = False,
                          is_short_side: bool = False) -> list:
    """Seed depth roles for one side using the positional fit.

    RULE 1 — WIDTH DETERMINES WINGER & CENTRE (reserved, assigned first).
    RULE 2 — LANE ORDER (ruck Y proximity) DETERMINES A/B/C/D.

    ``ruck_y`` is the side-split reference used for the WINGER/CENTRE
    outer-structure sort (widest defenders from the ruck centre).  The
    interior A/B/C/D order is sorted by ``abs(d.y - ruck_y)`` — the
    closest-to-ruck defender becomes A, the next B, and so on.  This
    guarantees that the defender whose lane is closest to the ruck
    always holds the A role, which anchors at ruck_y ± 2 m.

    EDGE RELEASE (Task XI): with ``edge_release=True`` this side is the
    RELEASED BALL SIDE — RULE 1 is skipped entirely, so NO defender
    receives WINGER/CENTRE and the entire side seeds into the pure
    Y-order interior stack (closest-to-ruck → A, next → B, then C,
    surplus → D).

    Returns the ordered per-side width-curve input:
        canonical:   [WINGER, CENTRE, A, B, C] + D*
        edge-release: [A, B, C] + D*   (no positional slots)

    This runs ONLY on the first assignment for a line (no persistence
    exists yet).  After this call all eligible defenders are seeded and
    every later call uses persistent maintenance.
    """
    side_sorted_by_width = sorted(
        side_defs, key=lambda d: abs(d.y - ruck_y), reverse=True
    )

    n_side = len(side_defs)

    # ── EDGE RELEASE (Task XI): the released ball side seeds NO outer
    # positional slots — every defender enters the interior lane order,
    # so the stack is pure Y-axis ordering end to end.
    if not edge_release:
        # ── COUNT-AWARE W/C LADDER (2026-09-07) ──
        # Every side guarantees the ruck-adjacent A pivot FIRST, regardless
        # of whether it is the short or open side.  The W/C ladder collapses
        # by count so the A pivot always exists:
        #   n=1 -> A (single ruck-adjacent pivot, no WINGER)
        #   n=2 -> WINGER + A (skip CENTRE; the pivot wins)
        #   n>=3 -> WINGER + CENTRE + A (+ B, C, D by lane order)
        # This removes the far/open-side defects: an n=1 side previously
        # seeded WINGER-only (no A) and an n=2 open side seeded
        # WINGER+CENTRE (no A via _ensure_exactly_one_a's empty non-W/C
        # pool), leaving the far side without its hinge pivot whenever the
        # ball-side bias dropped it to <=2 — the far-side width loss.
        if n_side >= 2:
            side_sorted_by_width[0].depth_role = "WINGER"
        if n_side >= 3:
            side_sorted_by_width[1].depth_role = "CENTRE"

    remaining = [d for d in side_defs if getattr(d, "depth_role", None) is None]
    # LANE ORDER: Y-distance to the ruck centre (ruck_y), NOT the ball
    # carrier.  The closest-to-ruck defender is always A.
    remaining_sorted_by_lane = sorted(
        remaining, key=lambda d: abs(d.y - ruck_y)
    )

    for role in ("A", "B", "C"):
        if remaining_sorted_by_lane:
            d = remaining_sorted_by_lane.pop(0)
            d.depth_role = role

    # All others become D (0..many, depending on line size).
    for d in remaining_sorted_by_lane:
        d.depth_role = "D"

    # ── SLOT INVARIANTS (2026-08-24) ──
    # Mixed seeded/unseeded bootstraps (rebalance flipper /
    # force-innermost reseed) can leave PRE-EXISTING role holders
    # alongside the fresh positional seed — two A's on one side
    # (stacking at the ruck-adjacent band) or zero A's (outer-lane
    # gap).  Enforce the SAME single-capacity + one-A-per-side
    # post-condition the maintenance path guarantees, BEFORE the
    # ordered width-curve output is built.
    _dedupe_single_capacity_claims(side_defs, side, anchors, ruck_y)
    _ensure_exactly_one_a(side_defs, ruck_y)

    # ── Ordered width-curve output (CANONICAL order + D surplus;
    #    EDGE RELEASE omits W/C on the released ball side) ──
    ordered = []
    out_roles = ("A", "B", "C") if edge_release else CANONICAL_SINGLE_ROLES
    for role in out_roles:
        for d in side_defs:
            if d.depth_role == role:
                ordered.append(d)
                break
    for d in side_defs:
        if d.depth_role == "D":
            ordered.append(d)
    return ordered


# ─────────────────────────────────────────────────────────────────────
# PERSISTENT MAINTENANCE — hysteresis-protected, structural
# ─────────────────────────────────────────────────────────────────────

def _is_unsettled(d, side: int, role: str, anchors: dict, ruck_y: float) -> bool:
    """True when *d* is more than ROLE_SWAP_HYSTERESIS_M from its role.

    For WINGER / CENTRE the target is the structural width anchor
    (outer-structure roles are width-fixed).  For A / B / C the target
    is the ruck Y — the defender is "settled" while it stays within
    hysteresis of the ruck lateral line, and becomes eligible for a
    role change only when it has moved more than ROLE_SWAP_HYSTERESIS_M
    away (genuine lane crossing).

    ONLY the lane/anchor gap matters here — never d.x, d.y ordering,
    lane index, or raw distance-to-ruck ordering.
    """
    if role in ("A", "B", "C"):
        return abs(d.y - ruck_y) > ROLE_SWAP_HYSTERESIS_M
    target = anchors[side].get(role, anchors[side]["D"])
    return abs(d.y - target) > ROLE_SWAP_HYSTERESIS_M


# ─────────────────────────────────────────────────────────────────────
# SLOT-ALLOCATOR INVARIANTS (2026-08-24) — shared by BOTH paths
# ─────────────────────────────────────────────────────────────────────
# The depth-role geometry invalidation at tackle release and new-anchor
# events exposed the remaining allocator defect: the bootstrap path had
# NO single-capacity de-dup and NO one-A guarantee, and the maintenance
# fill guard could strand freshly released holders away from their
# interior band.  These helpers make the post-condition of EVERY
# allocation path identical:
#
#   1. At most ONE occupant per single-capacity slot (W/C/A/B/C) per
#      side  ->  no stacking at the ruck-adjacent band.
#   2. EXACTLY one A per side whenever a non-WINGER/CENTRE defender
#      exists on it  ->  no outer-lane / interior-band gap.

def _side_best_fit(pool, role: str, side: int, anchors: dict, ruck_y: float):
    """Lane best-fit for one single-capacity role.

    Smallest Y-distance to ruck_y for A/B/C; structural anchor proximity
    for WINGER/CENTRE; ``player_id`` breaks ties deterministically.
    Shared by the maintenance fill loop and the slot-invariant pass so
    every allocation path ranks candidates identically.
    """
    if role in ("A", "B", "C"):
        return min(
            pool,
            key=lambda d: (
                abs(d.y - ruck_y),
                d.player_id,
            ),
        )
    return min(
        pool,
        key=lambda d: (
            abs(d.y - anchors[side].get(role, anchors[side]["D"])),
            d.player_id,
        ),
    )


def _dedupe_single_capacity_claims(side_defs, side: int, anchors: dict,
                                   ruck_y: float) -> None:
    """De-duplicate single-capacity role claims on one side.

    With multiple claimants for WINGER/CENTRE/A/B/C the best-fit
    claimant keeps the role and the others fall to D.  Runs in BOTH
    allocation paths (maintenance and the bootstrap slot-invariant
    pass) so a mixed seeded/unseeded bootstrap can never leave two
    occupants on one single-capacity slot.
    """
    for role in CANONICAL_SINGLE_ROLES:
        claimants = [d for d in side_defs if d.depth_role == role]
        if len(claimants) > 1:
            keep = _side_best_fit(claimants, role, side, anchors, ruck_y)
            for d in claimants:
                if d is not keep:
                    d.depth_role = "D"


def _ensure_exactly_one_a(side_defs, ruck_y: float) -> None:
    """ONE-A-PER-SIDE GUARANTEE (Task VII, 2026-08-18; slot allocator
    2026-08-24; hardener 2026-09-07).

    Every side must hold EXACTLY one A defender whenever any defender
    exists on it.  If no A exists after de-duplication/filling, the
    closest-to-ruck defender is promoted to A — the defender whose lane
    is closest to ruck_y always holds the A role.  Enforced by BOTH
    allocation paths (maintenance and the bootstrap slot-invariant pass).

    HARDENER (2026-09-07, far-side fix): the previous version only
    promoted from the non-WINGER/CENTRE pool, so an n<=2 side that was
    entirely WINGER/CENTRE (e.g. the open/far side with n=2 = WINGER +
    CENTRE after the ball-side bias dropped it) could NEVER be given an A
    — the hinge pivot was missing and spacing collapsed.  When no
    non-W/C candidate exists, demote the closest-to-ruck W/C to A so the
    ruck-adjacent hinge always exists.
    """
    has_a = any(d.depth_role == "A" for d in side_defs)
    if not has_a and side_defs:
        non_wc = [
            d for d in side_defs
            if d.depth_role not in ("WINGER", "CENTRE")
        ]
        candidates = non_wc or [
            d for d in side_defs
            if d.depth_role in ("WINGER", "CENTRE")
        ]
        if candidates:
            closest_to_ruck = min(
                candidates,
                key=lambda d: (abs(d.y - ruck_y), d.player_id),
            )
            closest_to_ruck.depth_role = "A"


def _maintain_side(side_defs, side: int, anchors: dict, ruck_y: float,
                   fresh_ids: frozenset = frozenset(),
                   edge_release: bool = False,
                   locked_ids: frozenset = frozenset(),
                   is_short_side: bool = False) -> list:
    """Persistent role maintenance for one locked side.

    ``fresh_ids`` — ids whose depth_role was None on entry to this
    assign_depth_roles() call (tackle-release / new-anchor
    invalidation).  They are exempt from the ANCHOR-PROXIMITY GUARD
    when filling interior A/B/C slots on this single call, so a
    released holder can reclaim its band even when it exited the
    bubble beyond ROLE_SWAP_HYSTERESIS_M (slot allocator, 2026-08-24).

    ``edge_release`` (Task XI) — True when this side is the RELEASED
    BALL SIDE: no defender may hold WINGER/CENTRE there.
    ``locked_ids`` lists the collapse-locked defenders (within
    COLLAPSE_LOCK_RADIUS_M of the BC) who are exempt from the
    edge-release demotion until they exit the lock radius (identical
    guard principle to the Task X dynamic refit).

    1. Every eligible defender on this side holds a valid depth_role
       (D is the universal surplus/fallback role).
    2. Single-capacity claims (WINGER/CENTRE/A/B/C) are de-duplicated —
       with multiple claimants the one closest to that role's band
       (Y-distance to the ruck for A/B/C; structural anchor for
       WINGER/CENTRE) keeps it; the others fall to D.
    3. Vacant single-capacity slots are filled ONLY from unsettled D's
       (lane/anchor gap > ROLE_SWAP_HYSTERESIS_M), chosen by band
       best-fit.  Settled defenders are never reassigned.
    4. BAND-BOUNDARY CROSSINGS: when a defender moves far enough from
       ruck_y, a genuine band crossing flips roles — hysteresis only
       blocks flicker from sub-threshold drift.
    5. ONE-A-PER-SIDE GUARANTEE: after maintenance, if no defender holds
       A on this side, the closest-to-ruck non-WINGER/CENTRE defender
       is promoted to A.
    6. Returns the ordered width-curve input: WINGER/CENTRE/A/B/C then
       all D's.
    7. EDGE RELEASE (Task XI): when *edge_release* is True this side is
       the released ball side — WINGER/CENTRE holders are demoted to D
       (collapse-locked holders exempt via *locked_ids*), interior-only
       vacancy filling runs, and the ordered output omits W/C.

    LANE ORDER (Task VII, 2026-08-18): interior A/B/C bands are
    assigned by Y-distance to ruck_y (the ruck centre), NOT the ball
    carrier.  The closest-to-ruck defender on each side always holds A,
    anchoring at ruck_y ± RUCK_ADJACENT_A_Y_OFFSET_M (2.0 m).
    """
    # ── A. D invariant: every defender on this side has a valid role ──
    for d in side_defs:
        if getattr(d, "depth_role", None) not in VALID_ROLES:
            d.depth_role = "D"

    # ── A2. EDGE RELEASE DEMOTION (Task XI) ──
    # On the released ball side no defender may hold a positional edge
    # slot: any stale / locked-out WINGER/CENTRE holder falls back into
    # the interior competition as D.  Collapse-locked holders (within
    # COLLAPSE_LOCK_RADIUS_M of the BC, passed via *locked_ids*) keep
    # their role until they exit the lock radius — identical principle
    # to the Task X dynamic-refit guard — and convert on a later call.
    if edge_release:
        for d in side_defs:
            if (
                getattr(d, "depth_role", None) in ("WINGER", "CENTRE")
                and id(d) not in locked_ids
            ):
                d.depth_role = "D"

    # ── A3. COUNT-AWARE W/C DEMOTION (2026-08-31, generalised 2026-09-07) ──
    # Every side's W/C ladder collapses by count so the ruck-adjacent A
    # pivot always exists — never relying on _ensure_exactly_one_a:
    #   n=1 -> no WINGER/CENTRE (all become interior; A stamped)
    #   n=2 -> no CENTRE (WINGER + A only)
    # Previously gated on is_short_side, so the open/far side could keep
    # WINGER+CENTRE at n=2 (no-A far-side defect).  Now applies to ALL
    # sides so the hinge pivot is guaranteed everywhere.
    if not edge_release:
        _n_side = len(side_defs)
        if _n_side < 2:
            for d in side_defs:
                if (getattr(d, "depth_role", None) in ("WINGER", "CENTRE")
                        and id(d) not in locked_ids):
                    d.depth_role = "D"
        elif _n_side < 3:
            for d in side_defs:
                if (getattr(d, "depth_role", None) == "CENTRE"
                        and id(d) not in locked_ids):
                    d.depth_role = "D"

    # ── B. De-duplicate single-capacity claims ──
    # Shared slot-invariant helper (identical semantics to the former
    # inline closure; also enforced by the bootstrap path).
    _dedupe_single_capacity_claims(side_defs, side, anchors, ruck_y)

    # ── C. Fill vacant single-capacity slots from unsettled D's ──
    # ANCHOR-PROXIMITY GUARD: a vacant slot may only be filled by a
    # defender whose position is within ROLE_SWAP_HYSTERESIS_M of the
    # role's structural anchor.
    # EDGE RELEASE (Task XI): on the released ball side the WINGER/CENTRE
    # slots are dissolved, so the fill loop is interior-only (A/B/C).
    if edge_release:
        fill_roles = ("A", "B", "C")
    else:
        _n_side = len(side_defs)
        if _n_side == 1:
            fill_roles = ("A",)
        elif _n_side == 2:
            fill_roles = ("A", "WINGER")
        else:
            fill_roles = CANONICAL_SINGLE_ROLES
    for role in fill_roles:
        if any(d.depth_role == role for d in side_defs):
            continue
        movable = [
            d for d in side_defs
            if d.depth_role == "D"
            and _is_unsettled(d, side, "D", anchors, ruck_y)
        ]
        if not movable:
            continue
        best = _side_best_fit(movable, role, side, anchors, ruck_y)
        if role in ("A", "B", "C"):
            slot_anchor = ruck_y
        else:
            slot_anchor = anchors[side].get(role, anchors[side]["D"])
        # ANCHOR-PROXIMITY GUARD with FRESH-INVALIDATED EXEMPTION
        # (slot allocator, 2026-08-24): interior slots (A/B/C) only.
        # A defender released from TACKLE-STATE exits the bubble at
        # radius + hysteresis (+ peel lead) ~= 3.75-4.35 m from the
        # ruck point, so |y - ruck_y| routinely exceeds the 3.0 m
        # guard and the released holder could NEVER reclaim its
        # interior band — it lingered as a stacked D near the inner
        # lane while another defender was permanently promoted into
        # the vacancy.  On the single call it re-enters, a freshly
        # invalidated defender may take an interior slot by lane
        # best-fit regardless of the guard.  WINGER/CENTRE keep the
        # strict guard (their holders are rarely invalidated, and a
        # near-ruck body must never grab an outer anchor).
        if (
            abs(best.y - slot_anchor) > ROLE_SWAP_HYSTERESIS_M
            and not (role in ("A", "B", "C") and id(best) in fresh_ids)
        ):
            continue
        best.depth_role = role

    # ── C2. BAND-BOUNDARY CROSSING (adjacent-band hysteresis) ──
    moved = set()

    def _holder(role):
        return next(
            (d for d in side_defs if d.depth_role == role and id(d) not in moved),
            None,
        )

    def _swap(inner, outer):
        if inner is None or outer is None:
            return
        inner_gap = abs(inner.y - ruck_y)
        outer_gap = abs(outer.y - ruck_y)
        if inner_gap > outer_gap + ROLE_SWAP_HYSTERESIS_M:
            inner.depth_role, outer.depth_role = (
                outer.depth_role, inner.depth_role,
            )
            moved.add(id(inner))
            moved.add(id(outer))

    _swap(_holder("A"), _holder("B"))
    _swap(_holder("B"), _holder("C"))
    c_holder = _holder("C")
    if c_holder is not None and id(c_holder) not in moved:
        d_pool = [
            d for d in side_defs
            if d.depth_role == "D" and id(d) not in moved
        ]
        if d_pool:
            closest_d = min(
                d_pool,
                key=lambda d: (abs(d.y - ruck_y), d.player_id),
            )
            _swap(c_holder, closest_d)

    # ── C3. ONE-A-PER-SIDE GUARANTEE (Task VII, 2026-08-18) ──
    # Shared slot-invariant helper — identical semantics, also
    # enforced by the bootstrap path (slot allocator, 2026-08-24).
    _ensure_exactly_one_a(side_defs, ruck_y)

    # ── D. Ordered width-curve output (EDGE RELEASE omits W/C) ──
    ordered = []
    out_roles = ("A", "B", "C") if edge_release else CANONICAL_SINGLE_ROLES
    for role in out_roles:
        for d in side_defs:
            if d.depth_role == role:
                ordered.append(d)
                break
    for d in side_defs:
        if d.depth_role == "D":
            ordered.append(d)
    return ordered


# ─────────────────────────────────────────────────────────────────────
# RECOVERY-WINDOW BAND-PRESERVATION GATE (Stage 3H conductor model)
# ─────────────────────────────────────────────────────────────────────
# During the tackle-finish recovery window (RELEASE / PTB / POST_RUCK) the
# recovery role (IDR10) must never override an existing DR10 width-band
# (A/B/C/D).  The un-gated preamble runs ``assign_depth_roles`` every frame,
# and a recovering body (a marker that just converted to DEF_LINE_READY, a
# flipped ex-tackler, or any unseeded defender) used to trigger the
# ``unseeded`` positional re-bootstrap, which re-stamped EVERY defender's
# band from live Y and collapsed the line.  These helpers make the recovery
# window preserve existing bands verbatim and only D-fill band-less
# defenders — the RELEASE authority gate ("defenders with a proper spot
# keep it; the clamp only moves band-less defenders").

# Phases in the tackle-finish / recovery window where the band set must be
# PRESERVED.  The line is "getting up" and clamping must only move band-less
# defenders — never re-band existing holders.
#
# STAGE 3H (2026-08-30): the set now covers the ENTIRE recovery window —
# every sub-state from TES hand-over through to the first OPEN_PLAY frame.
# Previously the upper-cased sub-state values "TACKLE_RELEASE", "PTB_READY"
# and "POST_PTB_RESET" were NOT in the set (only the OPE internal labels
# "RELEASE" / "PTB_PREP" / "PTB_EXEC" / "PTB" / "POST_RUCK_OPEN_PLAY" were),
# so the recovery-window gate silently skipped those sub-states and a
# mid-window side re-split / re-bootstrap could re-band the A-pivot off its
# ruck-adjacent A slot.  Adding them makes the A-pivot keep its band through
# the WHOLE recovery window (RELEASE -> PTB_READY -> PTB -> POST_PTB_RESET ->
# POST_RUCK_OPEN_PLAY), satisfying the re-anchor-off-A hinge contract.
_BAND_PRESERVATION_PHASES = frozenset({
    "RELEASE", "TACKLE_RELEASE",
    "PTB_READY", "PTB_PREP", "PTB_EXEC", "PTB", "POST_PTB_RESET",
    "POST_RUCK_OPEN_PLAY",
})


def _is_band_preservation_phase(world) -> bool:
    """True when the engine phase is in the tackle-finish recovery window.

    Covers RELEASE, PTB_PREP, PTB_EXEC, PTB and POST_RUCK_OPEN_PLAY — the
    phases where defenders are standing up / regrouping after a tackle and
    the clamp writer must not re-band an existing width-band.  Reads
    ``world.sub_state`` (GameSubState) or the OPE phase label with the same
    projection ``phase_authority_guard.phase_of_engine`` uses, so the
    depth-role gate and the conductor-model audit agree on the phase.
    """
    sub = getattr(world, "sub_state", None)
    if sub is not None:
        name = str(getattr(sub, "value", sub)).upper()
        if name in _BAND_PRESERVATION_PHASES:
            return True
    label = getattr(world, "_ope_phase_label", None)
    if label is not None and str(label).upper() in _BAND_PRESERVATION_PHASES:
        return True
    return False


# ── POST-RUCK RESTORE BOUNDARY (Stage 3H, 2026-08-30) ────────────────
# The PTB->POST_RUCK boundary where the recovery window ends and the line
# must re-form a FULL per-side A/B/C/D/CENTRE/WINGER ladder from settled
# positions.  During early RELEASE/PTB the preserve-first recovery seed
# keeps existing bands verbatim (a recovering body mid-window is never
# re-banded).  Once play reaches POST_PTB_RESET / POST_RUCK_OPEN_PLAY the
# line's settled positions are re-laned instead — the closest-to-ruck body
# on each side becomes A (the hinge-pivot origin), the next B then C, and
# the widest become CENTRE/WINGER — so the blind side always gets an
# A-pivot and no side collapses to A/D/D/D-only.  Without this the blind
# side A-pivot was never restored after RELEASE/PTB and hinge spacing had
# no origin, so B/C/D collapsed into D-only.
_POST_RUCK_RESTORE_PHASES = frozenset({
    "POST_PTB_RESET", "POST_RUCK_OPEN_PLAY",
})


def _is_post_ruck_restore_phase(world) -> bool:
    """True when the engine phase is at the PTB->POST_RUCK restore boundary.

    Covers POST_PTB_RESET and POST_RUCK_OPEN_PLAY — the sub-states where the
    line is standing up from the tackle/PTB and must rebuild its full
    A/B/C/D/CENTRE/WINGER ladder.  Reads ``world.sub_state`` (GameSubState)
    or the OPE phase label with the same projection ``_is_band_preservation_
    phase`` uses, so the recovery gate and this restore gate agree on phase.
    """
    sub = getattr(world, "sub_state", None)
    if sub is not None:
        name = str(getattr(sub, "value", sub)).upper()
        if name in _POST_RUCK_RESTORE_PHASES:
            return True
    label = getattr(world, "_ope_phase_label", None)
    if label is not None and str(label).upper() in _POST_RUCK_RESTORE_PHASES:
        return True
    return False


def _post_ruck_recovery_seed(eligible, ruck_y, anchors) -> dict:
    """Recovery-window seed — preserve existing width-bands verbatim.

    Used across RELEASE / PTB / POST_RUCK_OPEN_PLAY.  Existing banded
    defenders (A/B/C/D/CENTRE/WINGER) keep their ``depth_role`` exactly;
    band-less defenders (the recovering IDR10 bodies) fall to ``D`` only.
    The slot invariants (single-capacity de-dup, one-A-per-side) are NOT
    enforced here — that belongs to the OPEN_PLAY / rebanding authority
    once play resumes, so a recovery event can never mutate an established
    band mid-phase.

    Returns the ordered per-side width-curve input (canonical then D).
    """
    result: dict = {"LEFT": [], "RIGHT": []}
    for side_defs, key in (
        ([d for d in eligible if getattr(d, "depth_side", None) == -1], "LEFT"),
        ([d for d in eligible if getattr(d, "depth_side", None) == 1], "RIGHT"),
    ):
        if not side_defs:
            continue
        for d in side_defs:
            if getattr(d, "depth_role", None) not in VALID_ROLES:
                d.depth_role = "D"
        ordered = []
        for role in CANONICAL_SINGLE_ROLES:
            holder = next(
                (d for d in side_defs if d.depth_role == role),
                None,
            )
            if holder is not None:
                ordered.append(holder)
        for d in side_defs:
            if d.depth_role == "D":
                ordered.append(d)
        result[key] = ordered
    return result


# ─────────────────────────────────────────────────────────────────────
# MAIN ENTRY
# ─────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────
# DRIVE-TIME DEPTH-X BASE (2026-09-05 / single-source 2026-09-06)
# ─────────────────────────────────────────────────────────────────────
# Depth-role X is stamped from the BALL CARRIER during the drive
# (OPEN_PLAY and every TES tackle phase) so the line tracks the carrier
# rightward (+X) with the role offset, and from the ruck anchor
# (ruck_x + 10) only after the drive ends (RELEASE / PTB / POST_RUCK) —
# the "based off the BC until drive ends then retreat the 10m" rule.
# Previously the stamp was ALWAYS _anchor_m(world) == ruck_x + 10, so the
# line anchored to the stale pre-drive ruck (the "continue past the ruck /
# go the wrong way" bug).
# The drive-phase law is SINGLE-SOURCED in defensive_width.is_drive_phase
# so the depth-role stamping and the width/recompute writers can never
# disagree on what "the drive" is.


def _bounded_depth_x(world, d, target_x, tuners=None) -> float:
    """Apply THE DEPTH BOUNDARY INVARIANT to a depth-role X.

    THE OPEN FINDING (defence boundary): the drive-era depth law
    ``bc.x + WIDTH_OFFSETS[depth_role]`` is unbounded in ``bc.x``, so a
    carrier driven to ``bc.x = 105`` produced a WINGER depth target of
    115 — 5 m beyond the defending team's own tryline (+X = 110), i.e.
    inside the team's own in-goal.  The post-tackle 10 m retreat
    protection has always been tryline-clamped
    (``anchor_authority.defensive_line_anchor_m``); the DEPTH target — the
    drive-era successor of the same geometry — never was.

    This wrapper is the SINGLE call site of the invariant inside the
    depth-role law, so every producer that derives a depth target from
    this module (the per-frame stamp, all three ``assign_depth_roles``
    branch tails, the obligation's fallback read and the drift assertion)
    inherits the clamp and stays mutually consistent by construction.

    The clamp is delegated to ``depth_boundary_guard`` — the ONE
    definition of the envelope, single-sourced from the same
    ``LEFT_TRYLINE_X`` / ``RIGHT_TRYLINE_X`` constants the retreat
    protection clamps on.  It is a no-op for the dynamic fullback (its
    backfield anchor is deliberately allowed slightly in-goal), for a body
    that owns no depth role, for an already-legal target, and when the
    tuner / runtime lever disarms the invariant — so every legal frame is
    byte-identical to the pre-fix engine.

    Imported FUNCTION-LOCALLY so this module keeps its stdlib-only
    module-scope import set (no cycle, importable in any order).
    """
    from game.defence.defence_lvl_1.depth_boundary_guard import (
        clamp_depth_target_x,
    )
    clamped = clamp_depth_target_x(world, d, target_x, tuners)
    if clamped is None:
        return float(target_x)
    return float(clamped)


def _depth_role_target_x(world, d) -> float:
    """Return the drive-aware depth-role X base for *d*.

    During the drive (OPEN_PLAY + TES tackle phases) returns
    ``bc.x + WIDTH_OFFSETS[depth_role]`` so the line sits RIGHT of the
    ball carrier.  After the drive ends (RELEASE/PTB/POST_RUCK) returns
    the ruck anchor (``ruck_x + 10``) so the line retreats the 10m.

    BOTH branches are legal-geometry bounded.  The post-drive branch is
    already tryline-clamped by the anchor authority (``_anchor_m``), so
    ``_bounded_depth_x`` is a verified no-op there; the DRIVE branch is
    where the clamp actually binds, because ``bc.x + WIDTH_OFFSETS[role]``
    can exceed the defending team's own tryline (+X = 110) once the
    carrier is driven past x = 100 (WINGER, offset 10.0).
    """
    bc = getattr(world, "ball_carrier", None)
    if bc is None or not is_drive_phase(world):
        return _anchor_m(world)
    tuners = _resolve_tuners(world)
    offsets = getattr(tuners, "WIDTH_OFFSETS", None) or {}
    role = getattr(d, "depth_role", None)
    if role is not None and role in offsets:
        raw = float(bc.x) + float(offsets[role])
    else:
        raw = float(bc.x) + float(offsets.get("D", 5.0))
    return _bounded_depth_x(world, d, raw, tuners)


def stamp_drive_depth_x(world, defenders) -> None:
    """Refresh ``role_depth_target_x`` from the LIVE BC every frame (drive).

    The depth-role X stamp is otherwise only refreshed inside
    ``assign_depth_roles`` branch tails, which has early-return / mode-gated
    paths (e.g. the pre-ruck return, the band-preservation seed) that can
    leave the stamp on a stale ``ruck_x + 10`` while the drive is alive.
    This helper re-stamps every eligible defender's ``role_depth_target_x``
    from ``_depth_role_target_x`` — the single drive-aware law
    (``bc.x + WIDTH_OFFSETS[role]`` during the drive, ``ruck_x + 10`` after)
    — so the value is authoritative for the frame.

    Runs from ``defensive_pipeline.run_defensive_pipeline`` (the un-gated
    per-frame preamble) immediately after ``assign_depth_roles``, so the
    stamp refreshes EVERY frame independent of the role-assignment mode.
    Only X is refreshed here; Y is owned by ``assign_depth_roles`` in the
    same frame.  Fullback actors and tackle-state holders are skipped
    (their X is owned elsewhere).
    """
    for d in defenders:
        if d is None:
            continue
        if getattr(d, "fullback_role_active", False):
            continue
        if getattr(d, "tackle_state_active", False):
            continue
        d.role_depth_target_x = _depth_role_target_x(world, d)


def assign_depth_roles(defenders, world, *,
                       ball_side: int | None = None,
                       ball_side_bias: int = 0) -> dict:
    """Assign persistent depth_role (A/B/C/D/CENTRE/WINGER) + depth_side.

    Role assignment guarantees (2026-08-09, Task A; 2026-08-12 banded):

      - Sides are PERSISTENT and hysteresis-protected: a defender changes
        side only when it crosses ruck_y by more than
        SIDE_SWAP_HYSTERESIS_M (2.0 m).
      - Roles are PERSISTENT: a defender keeps its role unless it moves
        more than ROLE_SWAP_HYSTERESIS_M (3.0 m) from its role track
        (the ball-carrier Y band for A/B/C; the structural width anchor
        for WINGER/CENTRE) AND a slot is vacant (or the role claim is
        contested).
      - The interior A/B/C/D bands are BALL-CARRIER-RELATIVE
        (2026-08-12): the two closest-to-carrier defenders are in A, the
        next two in B, the outer structure in C/D — so the defenders who
        must engage the carrier at first contact are always the A/B
        defenders, and spacing/width/target_y/tackler selection consume
        those correct roles on the catch frame before the first tackle.
      - Once every slot is occupied, roles are immutable — zero
        frame-to-frame flips from drift.
      - Role/side do NOT depend on defender ordering, lane index, or
        raw X-distance-to-ruck in steady state.
      - The first call for a line runs a one-time legacy positional
        bootstrap so creation behaviour is unchanged.

    Tacklers, markers, dummy-half, kick-chase and the fullback keep
    whatever depth_role/depth_side they had (usually None).

    Returns the ordered per-side width-curve input:

        {"LEFT":  [WINGER_L, CENTRE_L, A_L, B_L, C_L] + D_L,
         "RIGHT": [WINGER_R, CENTRE_R, A_R, B_R, C_R] + D_R}

    Args:
        defenders: Iterable of Player objects forming the defensive line.
        world: WorldState (or stub) providing ruck_x_m, ruck_y_m and
            def_line_width_m — the ruck is the authoritative centre.
    """
    ruck_x = getattr(world, "ruck_x_m", RUCK_X_FALLBACK)
    ruck_y = getattr(world, "ruck_y_m", RUCK_Y_FALLBACK)
    tuners = _resolve_tuners(world)

    # ── SHORT-SIDE AUTHORITY (2026-08-31) ──
    # SINGLE resolve_short_side call — the same function side_widths and
    # required_side_counts use, so the W/C ladder collapse, the side
    # widths, and the count split can NEVER disagree on which side is
    # short after a ball-side flip.  Passed as is_short_side to the
    # per-side bootstrap/maintenance so the short side collapses its
    # WINGER/CENTRE ladder by count.
    short_side = resolve_short_side(world, tuners)

    # ── STAGE 5 SINGLE LINE POPULATION (2026-09-13) ──
    # The eligible line is EXACTLY ``population_authority.line_population`` —
    # the one canonical set the count, side, spacing and observer layers all
    # use (excluded roles incl. DEF_CHASE, the fullback actor, and every
    # TACKLE-STATE holder).  This replaces the three hand-rolled filters here
    # so the count population can never drift from the spacing population.
    from game.defence.defence_lvl_1.population_authority import (
        line_population as _line_population,
    )
    eligible = _line_population(defenders)

    # COUNT-AUTHORITY WIDTH (Stage 3, 2026-09-03): the structural role
    # anchors are built from count-derived width — the per-side count is
    # the eligible line count (excluding roles / fullback / tackle-state),
    # and width is derived from the lane model via side_widths(n_total=...).
    # The old single-full-width / proportional-split width is retired from
    # the anchor construction path.
    left_width, right_width = side_widths(
        world, tuners, n_total=len(eligible), ruck_y=ruck_y,
        ball_side=ball_side, ball_side_bias=ball_side_bias,
    )

    anchors = _get_role_anchors(ruck_x, ruck_y, left_width, right_width, tuners)

    # ── TASK XI EDGE RELEASE (2026-08-26) ─────────────────────────────
    # Resolve whether the ball carrier is deep enough toward ONE side
    # that positional identity (CENTRE/WINGER) no longer matters there.
    # Lazy import: ball_side_collapse imports THIS module at its own top
    # level (EXCLUDED_ROLES / assign_depth_roles), so an eager import
    # here would create an import cycle — mirrors the quiet-window lazy
    # pattern used elsewhere.
    #
    # The resolved mode applies on EVERY call path (per-frame preamble,
    # dynamic refit, reband bootstrap, geometry chokepoint) because it
    # is derived here rather than passed by callers — the single unified
    # depth-role model.  A TRANSITION (activation / deactivation /
    # ball-side cross) invalidates the persistent seeds on exactly the
    # affected side(s) — collapse-locked holders exempt, same guard
    # principle as the dynamic refit — so the deterministic whole-line
    # re-bootstrap below re-lanes that side purely by Y-axis ordering.
    from game.defence.defence_lvl_1.ball_side_collapse import (
        last_edge_release as _last_edge_release,
        resolve_edge_release as _resolve_edge_release,
    )
    prev_rel_active, prev_rel_side = _last_edge_release(world)
    edge_release_active, edge_release_side = _resolve_edge_release(
        world, tuners,
    )

    edge_locked_ids: frozenset = frozenset()
    if edge_release_active:
        bc_lk = getattr(world, "ball_carrier", None)
        if bc_lk is not None:
            edge_locked_ids = frozenset(
                id(d) for d in eligible
                if depth_role_locked(
                    math.hypot(
                        float(d.x) - float(bc_lk.x),
                        float(d.y) - float(bc_lk.y),
                    )
                )
            )

    if (edge_release_active, edge_release_side) != (
        prev_rel_active, prev_rel_side
    ):
        affected_sides = set()
        if edge_release_active and edge_release_side in (-1, 1):
            affected_sides.add(edge_release_side)
        if prev_rel_active and prev_rel_side in (-1, 1):
            affected_sides.add(prev_rel_side)
        for d in eligible:
            if (
                getattr(d, "depth_side", None) in affected_sides
                and id(d) not in edge_locked_ids
            ):
                d.depth_role = None
                d._depth_seeded = False

    # ── FORCE INNERMOST RE-SEED (DOMINANCE→RELEASE flip) ──
    # Any defender flagged _force_innermost_seed was flipped to the
    # other side of the ruck at RELEASE entry.  Clear its depth role
    # and _depth_seeded so the unseeded bootstrap assigns it A
    # (innermost) on its new side.  MUST run BEFORE mode selection so
    # the fresh _depth_seeded=False triggers the full re-bootstrap
    # (preventing the one-frame lag where the defender enters
    # persistent maintenance with a stale side/role).  Single-use:
    # flag is cleared immediately.
    for d in eligible:
        if getattr(d, "_force_innermost_seed", False):
            d._force_innermost_seed = False
            d._depth_seeded = False
            d.depth_role = None   # will be assigned A by bootstrap
            # CLEAR SIDE: the defender has flipped to the other side of
            # the ruck; its old depth_side is stale.  The count-driven
            # split (TASK VIII) will re-assign it below.
            d.depth_side = None
            d.role_depth_target_x = _depth_role_target_x(world, d)
            ruck_y_local = getattr(world, "ruck_y_m", RUCK_Y_FALLBACK)
            d.role_depth_target_y = ruck_y_local  # A band: closest to ruck

    # ── FRESHLY INVALIDATED SET (slot allocator, 2026-08-24) ──
    # Defenders entering THIS call with depth_role None are the freshly
    # invalidated (tackle-release / new-anchor sweep).  They are exempt
    # from the maintenance ANCHOR-PROXIMITY GUARD for interior-slot
    # (A/B/C) fills on this single call, so a released holder can
    # reclaim its band even when it exited the bubble beyond
    # ROLE_SWAP_HYSTERESIS_M.  Derived per call — no persistent flag,
    # steady-state frames are unaffected.
    fresh_ids = frozenset(
        id(d) for d in eligible if getattr(d, "depth_role", None) is None
    )

    # ── COUNT-DRIVEN SPLIT (TASK VIII, 2026-08-21) ──
    # The side assignment runs BEFORE any role seeding and is the SINGLE
    # authority for depth_side.  required_side_counts derives n_left /
    # n_right from the RUCK POSITION + GOVERNED WIDTH (per-side usable band
    # between the clamped A and WINGER anchors, gap-equalised), then
    # assign_split_sides fills those counts NEAREST-FIRST — a defender MAY
    # BE REASSIGNED ACROSS THE RUCK when the dictated counts demand it, so
    # every side holds exactly the required number of defenders with the
    # smallest total movement.  This supersedes the positional
    # d.y < ruck_y seed and the SIDE_SWAP_HYSTERESIS_M flip block.
    #
    # TASK X BALL-SIDE COLLAPSE (2026-08-26): the optional *ball_side* /
    # *ball_side_bias* kwargs propagate the dynamic-refit ball-side bias
    # into the count split (defaults reproduce the legacy symmetric
    # counts).  The single side authority stays; only the counts it is
    # asked to satisfy may be ball-side-biased by the dynamic refit.
    assign_split_sides(
        world, eligible, tuners, ruck_y=ruck_y,
        ball_side=ball_side, ball_side_bias=ball_side_bias,
    )

    # ── RUCK-PIVOT GATE (Stage 3B) ──
    # Band formation (A/B/C/D/CENTRE/WINGER seeding + maintenance) occurs
    # ONLY once TES has entered TACKLE_INIT (world.ruck_established).
    # Before the first tackle there is NO ruck: sides are stamped by the
    # count-driven split above, but NO bands form — eliminating premature
    # A/B/C formation on CATCH / OPEN_PLAY.  Verification stubs that omit
    # the field default to True so direct verifier calls are unaffected.
    if not bool(getattr(world, "ruck_established", True)):
        return {"LEFT": [], "RIGHT": []}

    # ── MODE SELECTION ──
    # If any eligible defender has never been assigned, seed the line once
    # with the positional algorithm.  Players are freshly constructed at
    # kickoff, so this runs once per kickoff; afterwards all calls are
    # persistent maintenance.  Force-reseeded defenders (above) also
    # enter this path since _depth_seeded was cleared.
    unseeded = [d for d in eligible if not getattr(d, "_depth_seeded", False)]

    # ── RECOVERY-WINDOW BAND-PRESERVATION GATE (Stage 3H) ──
    # During RELEASE / PTB / POST_RUCK_OPEN_PLAY the recovery role (IDR10)
    # must not override an existing DR10 width-band.  A recovering body
    # (marker → DEF_LINE_READY, flipped ex-tackler, unseeded re-entry)
    # that shows up in the unseeded pool must NOT trigger the positional
    # re-bootstrap, which would re-stamp every defender's band.  Run the
    # recovery-seed: existing bands are preserved verbatim; band-less
    # defenders D-fill.  This implements the RELEASE authority gate —
    # defenders with a proper line spot keep it, the clamp only moves
    # band-less defenders.
    if _is_band_preservation_phase(world):
        # ── STAGE 3H (2026-08-30): blind-side A restoration / ladder
        # re-build at the PTB->POST_RUCK boundary.  Early RELEASE/PTB keep
        # the preserve-first seed (existing bands verbatim, D-fill the
        # rest).  Once play reaches POST_PTB_RESET / POST_RUCK_OPEN_PLAY
        # the line's SETTLED positions are re-laned into the full
        # A/B/C/D/CENTRE/WINGER ladder — the closest-to-ruck body on each
        # side becomes A (the hinge-pivot origin), the next B then C, and
        # the widest become CENTRE/WINGER.  Without this the blind side
        # A-pivot was never restored after RELEASE/PTB and hinge spacing
        # had no origin, so B/C/D collapsed into D-only (the
        # A/D/D/D-only spacing).  The released-ball side (edge_release)
        # stays interior-only (no W/C), mirroring the bootstrap.
        if _is_post_ruck_restore_phase(world):
            left_side = [d for d in eligible if d.depth_side == -1]
            right_side = [d for d in eligible if d.depth_side == 1]
            left_rel = bool(edge_release_active and edge_release_side == -1)
            right_rel = bool(edge_release_active and edge_release_side == 1)
            # CLEAR STALE ROLES FIRST: _bootstrap_side_roles re-lanes by
            # "depth_role is None", so a defender still carrying a stale
            # "D" (or any band) from the collapsed / preserve-first seed
            # would be invisible to the interior lane fill and stay D-only
            # (missing B/C).  Clearing the role makes the settled-position
            # re-lane rebuild the FULL ladder deterministically.
            for d in eligible:
                d.depth_role = None
            result = {
                "LEFT": _bootstrap_side_roles(
                    left_side, -1, ruck_y, anchors, edge_release=left_rel,
                    is_short_side=(short_side == -1)),
                "RIGHT": _bootstrap_side_roles(
                    right_side, 1, ruck_y, anchors, edge_release=right_rel,
                    is_short_side=(short_side == 1)),
            }
        else:
            result = _post_ruck_recovery_seed(eligible, ruck_y, anchors)
        for d in eligible:
            d._depth_seeded = True
        for d in eligible:
            d.role_depth_target_x = _depth_role_target_x(world, d)
            d.role_depth_target_y = anchors[d.depth_side].get(
                d.depth_role, anchors[d.depth_side]["D"]
            )
        return result

    if unseeded:
        # NOTE (TASK VIII, 2026-08-21): sides are NOT re-derived from
        # d.y here — the count-driven split above already stamped
        # depth_side on every eligible defender, and roles must be
        # seeded on those SPLIT sides (cross-ruck reassignment included).
        left_side = [d for d in eligible if d.depth_side == -1]
        right_side = [d for d in eligible if d.depth_side == 1]
        left_rel = bool(edge_release_active and edge_release_side == -1)
        right_rel = bool(edge_release_active and edge_release_side == 1)
        result = {
            "LEFT": _bootstrap_side_roles(left_side, -1, ruck_y, anchors,
                                          edge_release=left_rel,
                                          is_short_side=(short_side == -1)),
            "RIGHT": _bootstrap_side_roles(right_side, 1, ruck_y, anchors,
                                           edge_release=right_rel,
                                           is_short_side=(short_side == 1)),
        }
        for d in eligible:
            d._depth_seeded = True
        # ── TASK A-2 / TASK G: STABLE ROLE-DEPTH X/Y ──
        # X is stamped once per assignment from the ANCHOR AUTHORITY
        # (defensive_line_anchor_m == ruck_x + 10), never from a local
        # constant or BC/DH/spacing/width — the single depth source.
        for d in eligible:
            d.role_depth_target_x = _depth_role_target_x(world, d)
            d.role_depth_target_y = anchors[d.depth_side].get(
                d.depth_role, anchors[d.depth_side]["D"]
            )
        return result

    # ── PERSISTENT MAINTENANCE ──
    # NOTE (TASK VIII, 2026-08-21): the SIDE_HYSTERESIS flip block
    # (SIDE_SWAP_HYSTERESIS_M crossings) is REMOVED — the count-driven
    # split is the side authority and stamps depth_side for every
    # eligible defender above, so no defender can carry a stale side
    # into maintenance.  Only per-side ROLE maintenance runs here.
    left_side = [d for d in eligible if d.depth_side == -1]
    right_side = [d for d in eligible if d.depth_side == 1]

    left_rel = bool(edge_release_active and edge_release_side == -1)
    right_rel = bool(edge_release_active and edge_release_side == 1)
    result = {
        "LEFT": _maintain_side(left_side, -1, anchors, ruck_y, fresh_ids,
                               edge_release=left_rel,
                               locked_ids=edge_locked_ids,
                               is_short_side=(short_side == -1)),
        "RIGHT": _maintain_side(right_side, 1, anchors, ruck_y, fresh_ids,
                                edge_release=right_rel,
                                locked_ids=edge_locked_ids,
                                is_short_side=(short_side == 1)),
    }

    # ── TASK A-2 / TASK G: STABLE ROLE-DEPTH X/Y ──
    # X is stamped once per assignment from the ANCHOR AUTHORITY
    # (defensive_line_anchor_m == ruck_x + 10), never from a local
    # constant or BC/DH/spacing/width — the single depth source.
    for d in eligible:
        d.role_depth_target_x = _depth_role_target_x(world, d)
        d.role_depth_target_y = anchors[d.depth_side].get(
            d.depth_role, anchors[d.depth_side]["D"]
        )

    return result


def reset_depth_role_state(player) -> None:
    """Clear the persistent depth-role/side seed on *player*.

    Called on kickoff reset so a reused Player object re-bootstraps for
    the new match (players are normally recreated at kickoff, so this is
    a safety net for reuse paths).
    """
    player.depth_role = None
    player.depth_side = None
    player._depth_seeded = False
    player.role_depth_target_x = None
    player.role_depth_target_y = None


def depth_role_tag(defender) -> str:
    """Return the defender's depth role as a short debug tag ('-' if none)."""
    return getattr(defender, "depth_role", None) or "-"


def audit_depth_roles(defenders) -> None:
    """Print a depth-role audit line for every defender (STEP-4 validation)."""
    print("\nDEPTH ROLE AUDIT")
    for d in defenders:
        side_str = {-1: "L", 1: "R"}.get(getattr(d, "depth_side", None), "-")
        print(
            f"ID {getattr(d, 'player_id', '?')}: y={d.y:.1f} "
            f"side={side_str} "
            f"role={getattr(d, 'defender_role', None)} "
            f"-> depth_role={getattr(d, 'depth_role', None)}"
        )


def debug_render_defensive_line(defenders) -> None:
    """
    Visualise depth_role + fullback exclusion in INPUT order.

    Renders each defender as ``[role]`` in the caller's list order — there
    is NO Y-sort and NO lane-index ordering in the depth-role system
    (2026-08-09).  The dynamic fullback is rendered as [FB] so the
    backfield actor is visible as its own slot.  Purely an opt-in debug
    tool — never imported by engine logic.
    """
    cells = []
    for d in defenders:
        role = getattr(d, "depth_role", "-")
        if getattr(d, "fullback_role_active", False):
            role = "FB"
        cells.append(f"[{role}]")

    print(" ".join(cells))
