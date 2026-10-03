"""
defensive_split_spacing.py — Jon's split-design defensive spacing (Task E, 2026-08-09).

PURPOSE
-------
The FINAL spacing system.  Spacing is built from the SAME stable units the
rest of the pipeline uses:

    stable A_left / A_right   — the persistent depth_side anchor pair
    stable winger width       — WINGER role anchor on each side
    stable centre width       — CENTRE role anchor on each side
    stable anchors            — role_anchor_y (defensive_width.py)
    stable geometry           — ruck_x_m / ruck_y_m / governed width
    stable width curve        — ROLE_WIDTH_OFFSETS
    stable governor width     — defensive_width.governed_width_m

Spacing includes:
  - left-side spacing chain (fixed anchored roles ordered by side)
  - right-side spacing chain
  - proportional split (short_side_width_m / open_side_width_m overrides)
  - sideline override clamp (no anchor may cross the touchline margin)
  - fullback last-man logic (left to fullback_role.py; this module only
    excludes the actor so the chains get the correct N-1 player count)

The spacing output is the GEOMETRY TARGET GENERATOR (Task F): the
structured OPEN_PLAY pipeline reads ``compute_targets()`` for each
defender's Y (X stays role depth).  Smooth-damp movement and the
OPEN_PLAY gate are owned by the pipeline / Player.

SPACING CHAIN (WINGER-INWARD)
-----------------------------
Spacing runs from the WINGER inward (WINGER → CENTRE → D → C → B → A)
so interior defenders NEVER drift outward or collapse onto the sideline.

    WINGER → stable outer anchor (ruck_y ± half_width)
    CENTRE  → interpolated inward from WINGER
    D       → interpolated inward (may repeat for surplus defenders)
    C       → interpolated inward
    B       → interpolated inward
    A       → innermost anchor (ruck_y ± 2 m)

The WINGER is the stable outer anchor.  All interior lanes are
interpolated INWARD toward A, guaranteeing perfectly even spacing on
both sides regardless of defender count.  Interior defenders can never
drift outward because they are always anchored between WINGER (outer)
and A (inner).

SINGLE OFFSET CONTRACT
----------------------
The chain construction uses exactly ONE offset per defender:

    y_raw = ruck_y + side_sign * compute_spacing_offset(role, side_width)

where ``compute_spacing_offset`` is the role's canonical fraction of the
side's half-width (``side_width * 0.5 * role_width_offset(role)`` —
itself ``role_width_anchor * width_curve_factor`` with NO hidden 0.8
since 2026-08-17).  ``ruck_y`` is the shared base anchor — it is NEVER
additionally offset
by ``role_anchor_y()`` inside the chain (no double offset).  The two
clamp stages are ``governor.apply_width_clamp`` (when the governor is
wired AND active) and ``clamp_to_sideline`` (always, idempotent).
"""

import math

from game.defence.defence_lvl_1.defensive_width import (
    depth_side_of,
    role_width_offset,
    clamp_to_sideline,
)
from game.defence.defence_lvl_1.anchor_authority import (
    defensive_line_anchor_m as _anchor_m,
    SpacingDepthViolation,
)
# SPAN AUTHORITY (Stage 2, 2026-09-13): lane generation consumes an explicit
# span contract.  Widthless ladder construction is no longer supported — a
# builder handed no span raises SpanContractError instead of silently falling
# back to defender_gap_m / count-driven width reconstruction.
from game.defence.defence_lvl_1.span_contract import (
    SpanContract,
    SpanContractError,
    publish_span_contract,
    require_span,
)
# JITTER AUDIT (diagnostic, env-gated): logs the per-frame spacing snapshot
# so consecutive frames can be diffed and each target_y jump attributed to
# the state / side-population change that caused it.  NO-OP unless armed
# (world._jitter_audit or RLM_JITTER_AUDIT=1) — mirrors the chase-probe pattern.
from game.debug.jitter_audit import (
    audit_on as _jitter_audit_on,
    record_compute_targets as _jitter_record,
)

# ─────────────────────────────────────────────────────────────────────
# REQUIRED CONSTANTS (spec Task E)
# ─────────────────────────────────────────────────────────────────────

# The dynamic fullback (fullback_role_active) occupies NO spacing chain
# slot.  It is excluded from the chains and pinned to its own default
# anchor instead (spec step 4).
FULLBACK_EXCLUDED: bool = True

# Depth roles that participate in the spacing chain.  Defenders whose
# ``depth_role`` is NOT one of these (markers, dummy-half, tacklers,
# DEF_CHASE, etc.) are excluded from the side defender lists so they
# cannot steal a slot or inflate ``n_side`` in the interpolation.
_SPACING_ROLES = frozenset(("A", "B", "C", "D", "CENTRE", "WINGER"))

# Defender roles that NEVER occupy a spacing-chain slot (they play tight to
# the ruck / are engaged in the tackle).  Used by _world_side_defenders so
# the per-side live list reflects the FULL line population the split stamped
# (including not-yet-role-seeded bodies), rather than dropping them and
# forcing spacing_target_y into the canonical 6-lane fallback (which maps a
# lone role-carrying defender onto the sideline-clamped winger band ~66.4,
# doubling with the true winger).
_SPACING_EXCLUDED_ROLES = frozenset({
    "MARKER_A", "MARKER_B", "DUMMY_HALF",
    "TACKLER_1", "TACKLER_2", "TACKLER_3",
    "DEF_CHASE", "KICK_CHASE",
})

# Roles that can NEVER be the spacing hinge (the inner interpolation
# boundary).  Markers / DH / tacklers / chase / DEF_RETREAT_10 play tight
# to the ruck or are retreating; their live Y is not a line pivot.  Using
# one as the hinge fanned the short side out too wide during RELEASE/PTB
# (2026-09-03).  Applied in BOTH the chain-eligible pass and the full-team
# fallback pass of _side_hinge_body (belt-and-suspenders).
_HINGE_EXCLUDED_ROLES = frozenset({
    "MARKER_A", "MARKER_B", "DUMMY_HALF",
    "TACKLER_1", "TACKLER_2", "TACKLER_3",
    "DEF_CHASE", "KICK_CHASE", "DEF_RETREAT_10",
})

# ── ROLE MICRO-OFFSET (pure side-index lane model, 2026-08-29) ──────
# The spacing lane is assigned PURELY by side index (positional slot in
# the side-sorted list).  Roles only nudge the lane by a small signed
# micro-offset — they never select the lane.  The value is applied
# signed-by-side (positive = outward toward the nearest touchline,
# negative = inward toward the ruck) so the nudge is mirror-symmetric.
# WINGER/A are the boundary anchors and stay exact (micro 0.0).
#
# STAGE 3H (2026-08-30): all micro-offsets neutralised to 0.00 —
# the "offset-from-A" behaviour is eliminated.  B/C/D now sit EXACTLY on
# the even hinge lane measured from the ruck-adjacent A-pivot
# (ruck_y ∓/± 2.0 m -> even-slot interpolation to the clamped WINGER),
# so no interior defender is nudged off its hinge step.  This restores
# proper NRL-style hinge geometry: the line pivots on A and fans out at
# clean, uniform gaps.  A and WINGER remain the exact boundary anchors.
ROLE_MICRO_OFFSET_M = {
    "WINGER": 0.00,
    "CENTRE": 0.00,
    "D":      0.00,
    "C":      0.00,
    "B":      0.00,
    "A":      0.00,
}


def _role_micro_offset(role) -> float:
    """Return the small lateral micro-offset (m) for *role* (default 0.0)."""
    return float(ROLE_MICRO_OFFSET_M.get(role, 0.0))

# The canonical spacing chain has 6 role slots per side (WINGER → CENTRE
# → D → C → B → A).  D is now an explicit chain role — there are no
# surplus D's that append after the chain.
SPACING_CHAIN_LENGTH: int = 6

# Side signs — shared with the depth-role system.
LEFT: int = -1
RIGHT: int = +1

# ── SIDELINE BUFFER (2026-08-19; RETIRED AS OUTER-LANE AUTHORITY,
#    STAGE 3G GD-1, 2026-08-25) ──────────────────────────────────────
# Former behaviour: the winger was unconditionally clamped inward to
# margin+buffer / field_max-margin-buffer.  That silently REDEFINED any
# interpolated width above ~58 m (centred ruck) and split the outer lane
# between TWO authorities: structural anchors honoured the width
# (margin-only clamp) while the chain narrowed it (buffer clamp).
#
# Stage 3G: the 3-point width tuners are the SINGLE width source, so a
# width-feasible winger stands exactly where the width puts it, clamped
# only by the ABSOLUTE sideline margins (clamp_to_sideline) — the same
# clamp every structural anchor uses.  The constant is retained as a
# documented tactical tuner for future short-side work that must never
# fight the width authority.
SIDELINE_BUFFER_M: float = 3.0

# Canonical chain roles (outermost → innermost, WINGER-INWARD).
# Spacing runs from the stable outer anchor (WINGER) inward to the
# ruck-adjacent anchor (A).  Interior defenders never drift outward.
CHAIN_ROLE_ORDER = ("WINGER", "CENTRE", "D", "C", "B", "A")

# STABLE SIDE-INDEX RANK (2026-08-29): the pure side-index lane model.
# The chain slot (0 = outermost/WINGER, N-1 = innermost/A) is derived
# from the PERSISTENT depth_role in this canonical order — NOT from raw
# live Y.  This is why the ordering never jitters: roles are
# hysteresis-protected, so a defender keeps its slot across frames even
# as its Y crosses a neighbour's.  Unknown/None roles sort after the
# known chain (interior) and are broken by player_id.
_CHAIN_ROLE_RANK = {role: i for i, role in enumerate(CHAIN_ROLE_ORDER)}

# ── TASK H (2026-08-11) — TARGET FIELDS SPEED NEVER TOUCHES ─────────
# When a defender carries any of these CHASE target fields, the spacing
# chain must NOT produce a target for it (S3): the chase writer owns that
# defender outright.  No production defender carries these attributes
# today — this is the in-chain contract guard for any future edit that
# introduces real chase-target fields.
#
# TASK IV (2026-08-14): ``mmdl_target_x`` / ``mmdl_target_y`` are REMOVED
# from the skip set.  They are POST-CONSUMPTION records written by the MMDL
# step AFTER spacing runs each frame and persisted across frames — a
# defender carrying them is NOT chase-owned.  Including them here made
# compute_targets skip every defender on every frame after the first
# (the previous frame's stamps were still present), emptying the spacing
# dict, so the structured-close writer fell back to raw role-anchor Y and
# the TASK IV engagement-band pull never reached the movement pipeline.
# The S3 contract still protects the fields from mutation (SpacingGuard
# snapshots them), only the skip trigger is chase-specific now.
_CHASE_TARGET_FIELDS = (
    "chase_target_x",
    "chase_target_y",
)


def _has_chase_targets(d) -> bool:
    """True when *d* carries chase / MMDL target fields (S3 skip)."""
    return any(hasattr(d, field) for field in _CHASE_TARGET_FIELDS)


# ─────────────────────────────────────────────────────────────────────
# SIDE CHAINS
# ─────────────────────────────────────────────────────────────────────

def side_chain(right_defenders, left_defenders, side: int):
    """Return the spacing chain for one side, canonical role order.

    Args:
        right_defenders / left_defenders: per-side eligible defender lists
            (already split by depth_side); only the matching side is used.
        side: +1 for the RIGHT chain, -1 for the LEFT chain.

    Returns a list of defenders in CANONICAL WINGER-INWARD order
    (outside→inside): WINGER → CENTRE → D → C → B → A.  Interior roles
    sit OUTSIDE A (A is the innermost ruck-adjacent anchor), so the rank
    progression WINGER(0) → CENTRE(1) → D(2) → C(3) → B(4) → A(5) is
    monotonically non-decreasing along the returned list — the single
    chain-order law every verifier asserts (Stage 3G, GD-2).
    """
    pool = right_defenders if side == RIGHT else left_defenders

    ordered = []
    for role in CHAIN_ROLE_ORDER:
        for d in pool:
            if getattr(d, "depth_role", None) == role:
                ordered.append(d)
                break
    # Any defender not matched by the canonical roles (should not happen
    # after assign_depth_roles) appends at the end.
    matched = set(id(d) for d in ordered)
    for d in pool:
        if id(d) not in matched:
            ordered.append(d)
    return ordered


# ─────────────────────────────────────────────────────────────────────
# PROPORTIONAL SPLIT WIDTH
# ─────────────────────────────────────────────────────────────────────

def resolve_short_side(world, tuners, ruck_y: float | None = None) -> int:
    """Return LEFT/RIGHT of the SHORT SIDE (nearest sideline to the ruck).

    SINGLE AUTHORITY for short-side determination (2026-08-31): the side
    whose distance to the nearest usable sideline is smaller is the short
    side.  Mirrors the field-geometry math in ``required_side_counts`` so
    ``side_widths``, the count split, and the edge-release ball_side all
    agree on which side is short — eliminating the hardcoded
    ``left = short`` assumption that inverted the width band (and therefore
    the WINGER edge anchor + spacing target) the moment the ruck/ball
    flipped to the OTHER half of the field.

    Falls back to LEFT on stub worlds with no ruck geometry.

    Args:
        world: WorldState (or stub) providing ruck_y_m.
        tuners: DefenceTuners (or stub) providing sideline_margin_m +
            field_width_m.
        ruck_y: Optional override so callers (e.g. ``required_side_counts``
            with an explicit ruck_y) resolve the SAME short side as
            ``side_widths`` even when ``world.ruck_y_m`` differs.

    Returns:
        LEFT (-1) when ruck_y is closer to the left touchline,
        RIGHT (+1) otherwise.
    """
    # ── STAGE 6 SINGLE WIDTH AUTHORITY (2026-09-13) ──
    # The short-side decision is owned by ``formation_width_authority`` — the
    # same module that owns the per-side widths — so the split, the width and
    # the count can never disagree.  This function is a thin re-export; the
    # legacy body is UNREACHABLE (retained for grep-based verifiers).
    from game.defence.defence_lvl_1.formation_width_authority import (
        resolve_short_side as _authority_short_side,
    )
    return _authority_short_side(world, tuners, ruck_y=ruck_y)

    margin = float(getattr(tuners, "sideline_margin_m", 3.0))
    field_max = float(getattr(tuners, "field_width_m", 70.0)) - margin
    if ruck_y is None:
        ruck_y = float(getattr(world, "ruck_y_m", 35.0))
    dist_to_left = max(0.0, ruck_y - margin)
    dist_to_right = max(0.0, field_max - ruck_y)
    return LEFT if dist_to_left <= dist_to_right else RIGHT


def side_widths(world, tuners, n_total=None, ruck_y=None, *,
                ball_side: int | None = None,
                ball_side_bias: int = 0) -> tuple:
    """Return (left_width, right_width) after the proportional split.

    The default is the effective (governed) width scaled per side by the
    split-design factors (Task D), applied to the SHORT/OPEN sides
    (resolved dynamically via ``resolve_short_side``):
        short width = governed_width_m() * tuner_short_side_factor
        open  width = governed_width_m() * tuner_open_side_factor

    Both factors default to 1.0 → symmetric split (both sides = governed
    width).  Tuning a factor above/below 1.0 biases the SHORT side.

    NO WORLD OVERRIDES (2026-09-04, unification directive): the width is
    derived purely from the deterministic factor × governed width, then
    capped by the available room.  ``world.short_side_width_m`` /
    ``world.open_side_width_m`` overrides and the
    ``_post_retreat_open_play_frames`` gate are REMOVED — the same
    neutral count-lane geometry is read across TES/PTB/POST_RUCK/OPEN_PLAY.

    Each side inherits the governed width and is bounded only by the
    PHYSICAL field geometry via defensive_width.governed_width_m()'s
    field cap (field_width_m - 2 * sideline_margin_m).  There is NO
    width-floor / width-ceiling tuner band any more: the 3-point
    width curve is the sole width authority (2026-09-14, Option B).
    """
    # ── STAGE 6 SINGLE WIDTH AUTHORITY (2026-09-13) ──
    # The per-side span is no longer solved here.  ``formation_width_authority``
    # computes and publishes the ONE width fact for the frame (per-side span,
    # short side, named reducers, span contract); every caller of this function
    # was re-deriving its own copy.  This is now a thin consumer.  The legacy
    # body below is UNREACHABLE (retained for grep-based verifiers), the same
    # pattern ``assign_split_sides`` / ``required_side_counts`` already use.
    from game.defence.defence_lvl_1.formation_width_authority import (
        publish_formation_width as _publish_formation_width,
    )
    _pub = _publish_formation_width(world, tuners, ruck_y=ruck_y)
    return float(_pub["left_m"]), float(_pub["right_m"])

    # WIDTH-AUTHORITY RESTORED (2026-09-13): the GOVERNED WIDTH — the
    # 3-point width tuner interpolation (width_left_tryline_m /
    # width_halfway_m / width_right_tryline_m by ball X, published to
    # world.def_line_width_m by DefensiveLineGovernor) — is the SPAN
    # source for each side.  The COUNT authority decides only HOW MANY
    # defenders share the span (decide_side_counts), never how wide it is;
    # side_lanes then spaces exactly that count across this width via
    # lane_gap_m(), so moving the width tuners moves the realised line
    # width proportionally.
    #
    # DEFECT THIS FIXES (2026-09-13): the previous body DERIVED the width
    # from the count-lane model (count → flat defender_gap_m lanes →
    # 2*|lanes[-1]-ruck_y|) and never read the governed width at all.  The
    # width tuners only entered as a one-way inward clamp, so a large tuner
    # change produced no change in the realised width — the "width tuners
    # look under-responsive" report.
    #
    # ``n_total`` / ``ball_side`` / ``ball_side_bias`` are retained for
    # call-site compatibility: the side SPLIT they influence is consumed
    # through decide_side_counts by the callers that need it, and the
    # per-side WIDTH here is purely governed-width × split-factor.
    from game.defence.defence_lvl_1.defensive_width import governed_width_m
    effective = governed_width_m(world, tuners)

    short_factor = float(getattr(tuners, "tuner_short_side_factor", 1.0))
    open_factor = float(getattr(tuners, "tuner_open_side_factor", 1.0))

    if ruck_y is None:
        ruck_y = float(getattr(world, "ruck_y_m", 35.0))
    short_side = resolve_short_side(world, tuners, ruck_y=ruck_y)
    # NO WORLD OVERRIDES (2026-09-04): width is deterministic —
    # factor × governed width, capped by the available room below.  No
    # phase or world field (short_side_width_m / open_side_width_m /
    # _post_retreat_open_play_frames) may change it.
    short_w = effective * short_factor
    open_w = effective * open_factor

    # ── SHORT-SIDE AVAILABLE-ROOM CAP (2026-08-31) ──
    # The short side's usable width is limited by the distance from the
    # ruck to the nearest sideline (minus the winger buffer).  A ruck near
    # the touchline cannot place a winger at the full half-width, so the
    # side narrows to 2 * (dist_to_usable_sideline - buffer).  This is the
    # "real league geometry" short-side collapse: the winger hugs the
    # touchline with a buffer instead of sitting at the margin-clamped
    # theoretical half-width (which would spread the interior chain too
    # wide).  The OPEN side keeps the governed width.
    margin = float(getattr(tuners, "sideline_margin_m", 3.0))
    field_max = float(getattr(tuners, "field_width_m", 70.0)) - margin
    buffer = float(getattr(tuners, "short_side_buffer_m", 3.0))
    ruck_y = float(getattr(world, "ruck_y_m", 35.0))

    dist_to_left_usable = max(0.0, ruck_y - (margin + buffer))
    dist_to_right_usable = max(0.0, field_max - buffer - ruck_y)


    # Available-room caps — the SHORT side may never place a winger beyond
    # the buffer from the touchline; the OPEN side keeps the governed width.
    max_short_w = (
        dist_to_left_usable * 2.0 if short_side == LEFT
        else dist_to_right_usable * 2.0
    )
    max_open_w = (
        dist_to_right_usable * 2.0 if short_side == LEFT
        else dist_to_left_usable * 2.0
    )

    # Apply the short/open widths to the RESOLVED short side, not to a
    # hardcoded left/right.  A ruck on the right half flips is_left_short
    # to False, so the short width must land on the RIGHT side.
    if short_side == LEFT:
        left_w, right_w = short_w, open_w
    else:
        left_w, right_w = open_w, short_w

    # ── NO WIDTH RAILS (2026-09-14, Option B) ──
    # The width-band clamp that used to run here is REMOVED with the
    # tuners themselves.  The governed width is the sole
    # span authority and the only bound left is the physical field cap
    # already applied inside ``governed_width_m``.  (This body is the
    # unreachable legacy copy retained for grep-based verifiers.)
    #
    # ── SHORT-SIDE CAP = THE ONLY PER-SIDE REDUCER ──
    # The available-room cap runs LAST so the short side may collapse when
    # the ruck is near the touchline: the winger then hugs the touchline
    # with the 3 m buffer instead of sitting at the margin-clamped
    # theoretical half-width.  A single deterministic width source.
    if short_side == LEFT:
        left_w = min(left_w, max_short_w)
        right_w = min(right_w, max_open_w)
    else:
        right_w = min(right_w, max_short_w)
        left_w = min(left_w, max_open_w)

    # Sideline safety: each side's half-width must fit inside the usable
    # field (margin → field_max).
    margin = float(getattr(tuners, "sideline_margin_m", 3.0))
    field_max = float(getattr(tuners, "field_width_m", 70.0)) - margin
    max_half = field_max - margin
    left_w = min(left_w, max_half * 2.0)
    right_w = min(right_w, max_half * 2.0)

    return left_w, right_w


# ─────────────────────────────────────────────────────────────────────
# RUCK-ADJACENT EVEN SIDE SPACING (WINGER-INWARD, 2026-08-18)
# ─────────────────────────────────────────────────────────────────────
# Spacing runs from the WINGER inward (outside→inside) so interior
# defenders NEVER drift outward or collapse onto the sideline.
#
#     WINGER   -> ruck_y ± half_width   (stable outer anchor)
#     CENTRE   -> interpolated inward from WINGER toward A
#     D/C/B    -> interpolated inward (evenly spaced)
#     A        -> ruck_y ± RUCK_ADJACENT_A_Y_OFFSET_M (2.0 m, inner anchor)
#
# The WINGER is the stable outer anchor.  Every interior lane is
# interpolated INWARD toward A, guaranteeing perfectly even spacing on
# both sides regardless of defender count.  Interior defenders can never
# drift outward because they are always anchored between WINGER (outer)
# and A (inner).
#
# A and WINGER are PINNED anchors — the Task IV engagement-band pull is
# skipped for them so the ruck-adjacent pair stays exactly on rule.
# ─────────────────────────────────────────────────────────────────────

# Outside→inside radial order (touchline to ruck).  0 = WINGER (outermost
# anchor), 5 = A (innermost anchor).  Used by _role_radial_rank for slot
# assignment and by compute_spacing_offset for canonical fallback spacing.
_RADIAL_ROLE_ORDER = ("WINGER", "CENTRE", "D", "C", "B", "A")

# Canonical full-side defender count used ONLY when no live side list is
# available (verifier stubs calling spacing_target_y standalone).
_CANONICAL_SIDE_COUNT: int = 6


def _role_radial_rank(role: str) -> int:
    """Return the outside→inside radial index for *role* (0 = WINGER)."""
    return _RADIAL_ROLE_ORDER.index(role) if role in _RADIAL_ROLE_ORDER else 3


# ── RUCK-ADJACENT ANCHOR LAW (2026-08-22) ───────────────────────────
# Pure single source of truth for the ruck-adjacent A anchor:
#
#     ruck_adjacent_anchor(ruck_y, side) == ruck_y - 2.0 m  (LEFT / -1)
#                                        ruck_y + 2.0 m  (RIGHT / +1)
#
# EXACT and IMMUTABLE: no tuner, no width factor, no interpolation,
# no count-split, no chase target, and no sideline clamp may modify
# this value.  Every A-anchor computation in this module
# (compute_targets, required_side_counts, side_band_centre,
# spacing_target_y, _even_side_lane_y, compute_spacing_offset) MUST
# call this function — the anchor is applied BEFORE any interpolation
# and serves as the interpolation boundary itself, so interpolation
# can never move it.
#
# The legacy tuner DefenceTuners.RUCK_ADJACENT_A_Y_OFFSET_M is
# DEPRECATED and inert: it has ZERO live readers.
RUCK_ADJACENT_ANCHOR_M: float = 2.0
# HINGE DEADBAND (2026-09-01): below this distance from the structural A
# anchor (ruck_y ± 2.0 m), the spacing interpolation boundary is pinned to
# the STRUCTURAL anchor instead of the jittering live Y of the nearest-to-
# ruck body.  Kills the DEF_LINE_READY "gap-clamp oscillation" where a few
# cm of hinge movement re-paces every interior lane each frame.
HINGE_DEADBAND_M: float = 0.5
# HINGE STRADDLE DEADBAND (2026-09-01): a body this close to ruck_y is a
# ruck-tight / marker-adjacent body that may straddle the ruck line and
# flip LEFT/RIGHT frame-to-frame.  Excluding it from the hinge scan stops
# the "nearest body flips sides -> wrong-side hinge -> short-side collapse".
HINGE_STRADDLE_DEADBAND_M: float = 0.75

# ── BAND-PULL LANE-STEP BOUND (2026-09-12) ──────────────────────────
# The engagement-band spacing offsets are FIXED metres (0.50 / 0.75 /
# 1.25) tuned against a full-width line (~4 m lanes).  When the governed
# band narrows the published lane step, an unclamped fixed offset eats a
# large fraction of a lane, and because the offset is applied to the
# interior roles but NOT to the pinned A / WINGER anchors, A stays put
# while B moves toward it — the pair closes and the far-edge winger ends
# up compressed against its neighbour (symptom S2).  Bound the pull to
# this fraction of the PUBLISHED lane step so the band may bias the line
# but can never replace the count-lane geometry.
BAND_PULL_STEP_FRACTION: float = 0.25


def ruck_adjacent_anchor(ruck_y: float, side: int) -> float:
    """Return the ruck-adjacent A anchor — EXACTLY ruck_y ∓/± 2.0 m.

    Pure function of (ruck_y, side):

        side == LEFT  (-1) -> ruck_y - 2.0 m
        side == RIGHT (+1) -> ruck_y + 2.0 m

    LAW: the returned value may not be modified by tuners, width
    factors, interpolation, count-split logic, chase targets, or
    sideline clamps.  Callers treat it as an immutable boundary.
    """
    return (
        float(ruck_y) - RUCK_ADJACENT_ANCHOR_M
        if int(side) == LEFT
        else float(ruck_y) + RUCK_ADJACENT_ANCHOR_M
    )


# ─────────────────────────────────────────────────────────────────────
# SINGLE GLOBAL EVEN LADDER — THREE-POINT WIDTH IS THE SOLE AUTHORITY
# (2026-09-15)
# ─────────────────────────────────────────────────────────────────────
# The three-point width curve (width_left_tryline_m / width_halfway_m /
# width_right_tryline_m, interpolated by ball X → one scalar W) is the ONE
# authority for the defensive line's TOTAL width.  Every line defender is
# laid out on ONE uniform ladder spanning W with IDENTICAL gaps
# ``W / (n - 1)``; the ladder is centred on the ruck Y and SLID (never
# compressed) to fit inside the sideline margins.  There is NO left/right
# split, NO per-side count, NO per-side span, NO room redistribution, NO
# compression and NO lateral band pull that can make one gap differ from
# another.

def global_lanes(n_side: int, ruck_y: float, tuners, span) -> list:
    """Return *n_side* EVENLY SPACED Y lanes spanning *span*, about ruck_y.

    lane[0] = leftmost, lane[n_side-1] = rightmost, and every gap is
    exactly ``span / (n_side - 1)``.  The whole ladder is SLID (not
    squeezed) so it fits inside the sideline margins, which preserves both
    the commanded width and the equal gaps.
    """
    n = int(n_side) if n_side else 0
    if n <= 0:
        return []
    if n == 1:
        return [clamp_to_sideline(float(ruck_y), tuners)]
    full = require_span(span, caller="global_lanes")
    margin = float(getattr(tuners, "sideline_margin_m", 3.0))
    field_max = float(getattr(tuners, "field_width_m", 70.0)) - margin
    half = full * 0.5
    lo = margin + half
    hi = field_max - half
    if lo <= hi:
        centre = min(max(float(ruck_y), lo), hi)
    else:
        # Span wider than the field's usable width: centre it (the physical
        # field cap already forbids this in the live engine).
        centre = 0.5 * (margin + field_max)
    gap = full / float(n - 1)
    start = centre - half
    return [start + float(i) * gap for i in range(n)]


def global_line_targets(world, ruck_y: float, tuners,
                        defenders=None) -> tuple:
    """Return ``({id(d): y}, [lanes])`` — the single even ladder across W.

    The span is the governed width W (``side_widths`` returns W for both
    sides after the even-split change).  Defenders are placed on lanes in
    ascending live Y so the line keeps its natural left→right order.  This
    is the ONE Y authority: both ``compute_targets`` and ``spacing_y`` use
    it, so no two writers can produce different spacing.
    """
    from game.defence.defence_lvl_1.population_authority import is_line_body
    if defenders is None:
        defenders = getattr(world, "defending_team", None) or []
    line = [d for d in defenders if d is not None and is_line_body(d)]
    n = len(line)
    if n == 0:
        return {}, []
    _lw, _rw = side_widths(world, tuners, ruck_y=ruck_y)
    span = float(_lw)
    lanes = global_lanes(n, ruck_y, tuners, span)
    order = sorted(
        line,
        key=lambda d: (float(getattr(d, "y", ruck_y)),
                       getattr(d, "player_id", 0)),
    )
    return {id(d): float(lanes[k]) for k, d in enumerate(order)}, lanes


def _clamp_hinge_y(live_hinge: float, ruck_y: float, side: int,
                   tuners) -> float:
    """Clamp the live spacing hinge to the ruck-adjacent A-anchor band.

    The hinge model uses the LIVE nearest-to-ruck body as the inner
    interpolation boundary.  When that A-pivot isn't in the line yet
    (line still deep/retreating, nearest body far from ruck_y, or on the
    far side of the ruck), the live Y is a deep/far boundary and the
    lanes fan out from the wrong anchor.  Clamp the hinge so it never
    sits DEEPER than the structural A-anchor, and never crosses the ruck:

        LEFT  (-1): a_y = max(min(live, ruck_y), ruck_adjacent_anchor)
        RIGHT (+1): a_y = min(max(live, ruck_y), ruck_adjacent_anchor)

    When the live hinge is within the band it is used unchanged; when the
    A-pivot hasn't arrived (or is on the far side), the hinge snaps to the
    structural anchor so lanes always build ruck-anchored geometry.
    """
    _struct = ruck_adjacent_anchor(ruck_y, side)
    # HINGE DEADBAND (2026-09-01): when the live nearest-to-ruck body sits
    # within a small deadband of the structural A anchor (ruck_y ± 2.0 m),
    # pin the interpolation boundary to the STRUCTURAL anchor instead of the
    # jittering live Y.  The live hinge re-reads the nearest body's position
    # each frame, so a few cm of step/retreat would re-pace every interior
    # lane (the DEF_LINE_READY gap-clamp oscillation).  Within the deadband
    # the boundary is stable; only a genuinely-far hinge (line still forming)
    # follows the live body.
    if abs(float(live_hinge) - float(_struct)) <= HINGE_DEADBAND_M:
        return float(_struct)
    if int(side) == LEFT:
        return max(min(float(live_hinge), float(ruck_y)), _struct)
    return min(max(float(live_hinge), float(ruck_y)), _struct)


def _clamped_winger_y(side_width: float, ruck_y: float, side: int,
                       tuners) -> float:
    """WINGER anchor Y — RETIRED-IN-PLACE (Stage 3, 2026-09-03).

    DOCUMENTED-DEAD WRAPPER: legacy width-driven WINGER anchor
    (``ruck_y ± side_width * 0.5``); NOT called by the live pipeline
    (count → lanes → spacing owns the WINGER).  Retained for historical
    / verifier reference; Stage 7 removes it.  Prior semantics:

    SINGLE OUTER-LANE LAW: the 3-point width tuners are the SINGLE width
    source ("no other subsystem may redefine width"), so the winger lands
    at EXACTLY ``ruck_y ± side_width * 0.5`` whenever that point is
    inside the absolute sideline margins — clamped by the SAME
    margin-only ``clamp_to_sideline`` the structural role anchors use.
    This restores the winger-span == interpolated-width invariant at
    every physically feasible width and removes the two-authority split
    where structural anchors honoured the width while the chain silently
    narrowed it.

    ``SIDELINE_BUFFER_M`` no longer narrows a FEASIBLE width-derived
    position; it is retained as a documented tactical tuner for future
    short-side work that must never fight the width authority.
    """
    half = float(side_width) * 0.5
    theoretical = ruck_y + side * half
    return clamp_to_sideline(theoretical, tuners)


def _even_side_lane_y(
    d,
    slot,
    n_side,
    side: int,
    ruck_y: float,
    side_width: float,
    tuners,
    *,
    clamped_winger_y: float | None = None,
    clamped_a_y: float | None = None,
    is_short_side: bool = False,
) -> float:
    """Even side-lane Y for *d* at *slot* — RETIRED-IN-PLACE (Stage 3).

    DOCUMENTED-DEAD WRAPPER: legacy width-driven even-side interpolation;
    NOT called by the live pipeline (count → lanes → spacing owns Y).
    Retained for historical / verifier reference; Stage 7 removes it.
    Prior semantics:

    WINGER (slot 0) -> the clamped winger boundary; A (last slot) -> the
    clamped A anchor.  Every other defender interpolates linearly between
    the two anchors (INWARD from WINGER toward A):

        lane = a_y + (winger_y - a_y) * (1.0 - slot / (n_side - 1))

    The WINGER is the stable outer anchor.  Interior defenders are
    interpolated INWARD, so they can never drift outward or collapse
    onto the sideline.

    When *clamped_winger_y* and *clamped_a_y* are provided, they are
    used directly as the two interpolation boundaries.  ``clamped_a_y``
    is the HINGE — the live position of the nearest-to-ruck defender on
    that side (spec 2026-08-30), NOT the fixed ruck_y ± 2.0 m A-role
    anchor.  When omitted (verifier stubs calling without full context)
    the canonical half-width / RUCK_ADJACENT_A_Y_OFFSET anchors are used.
    """
    role = getattr(d, "depth_role", "D") or "D"
    half = float(side_width) * 0.5

    # Determine the two interpolation boundaries.
    if clamped_a_y is not None:
        # FIXED ANCHOR (2026-09-01): the inner interpolation boundary is
        # ALWAYS the structural ruck_adjacent_anchor (ruck_y +/- 2.0), never
        # a live-hinge body's Y.  This makes the side fan evenly from the
        # fixed anchor to the governed width regardless of a ruck-straddling
        # or wrong-side hinge body.
        a_y = ruck_adjacent_anchor(ruck_y, side)
    else:
        # ANCHOR LAW: exact ruck_y ± 2.0 m — never tuner-derived.
        a_y = ruck_adjacent_anchor(ruck_y, side)

    if clamped_winger_y is not None:
        winger_y = clamped_winger_y
    else:
        winger_y = ruck_y + side * half

    # ANCHOR LAW (2026-08-22, HINGE 2026-08-30): PURE slot interpolation
    # — no role special-casing.  The NEAREST defender on the side (last
    # slot, t=1.0) lands EXACTLY on a_y — the live HINGE (clamped_a_y =
    # nearest body's actual y) when a side list is present, or the
    # canonical ruck_y ± 2.0 m fallback when it isn't.  The outermost
    # (slot 0, t=0.0) lands on the winger boundary.  This removes any
    # dependence on A/B/C/D depth roles for the hinge position: on EACH
    # side the nearest-to-ruck body is the anchor, and every other
    # defender paces outward from it.
    if n_side is not None and n_side >= 2:
        t = float(slot) / float(n_side - 1)
    elif is_short_side:
        # SHORT-SIDE FALLBACK (2026-08-31): the short side has NO canonical
        # 6-lane ladder — the numbers-based collapse puts the WINGER at the
        # outer anchor (t=0) and A at the hinge (t=1).  This runs when the
        # side list is incomplete (n_side < 2); forcing t=1.0 for every
        # role flung the WINGER onto the hinge (a_y), overriding the capped
        # width.  Interior roles (CENTRE/B/C/D) are not present on a
        # 2-lane short side; if one appears mid-transition, interpolate by
        # its canonical radial fraction.
        if role == "WINGER":
            t = 0.0
        elif role == "A":
            t = 1.0
        else:
            rank = min(_role_radial_rank(role), _CANONICAL_SIDE_COUNT - 1)
            t = float(rank) / float(_CANONICAL_SIDE_COUNT - 1)
    else:
        # Canonical full-side fallback (WINGER..A = 6 lanes).
        rank = min(_role_radial_rank(role), _CANONICAL_SIDE_COUNT - 1)
        t = float(rank) / float(_CANONICAL_SIDE_COUNT - 1)
    # WINGER-inward: slot 0 (WINGER) → t=0 → (1-t)=1 → winger_y;
    # last slot (A) → t=1 → (1-t)=0 → a_y.
    raw = a_y + (winger_y - a_y) * (1.0 - t)
    # ROLE MICRO-OFFSET (2026-08-29): the lane is index-driven; the role
    # only nudges it by a small signed offset (positive = outward).  The
    # two boundary lanes (slot 0 = winger anchor, last slot = A anchor)
    # stay pinned exactly on rule — only interior lanes receive the nudge.
    is_outer = (slot == 0)
    is_inner = (n_side is not None and n_side >= 2 and slot == n_side - 1)
    if not (is_outer or is_inner):
        raw += side * _role_micro_offset(role)
    return clamp_to_sideline(raw, tuners)


def _side_inside_out(side_defs, side: int, ruck_y: float) -> list:
    """Order one side's defenders outside→inside, role-aware slot0.

    RUCK-ADJACENT WINGER-ANCHOR LAW (2026-09-02): slot 0 is the WINGER
    anchor on the side.  It must be the REAL WINGER-role body, never a
    random defender who happened to drift wide during the reform window
    (RELEASE/PTB) — a live-Y drifter at slot 0 was "stretching one side
    too far" because it got placed at the clamped half-width anchor.
    Selection:

        1. real WINGER  (depth_role == "WINGER")   -> slot0
        2. else widest CENTRE (depth_role == "CENTRE") -> slot0
        3. else live-Y fallback      (no W/C role yet — line still forming)

    Every remaining defender is ordered outside→inside by live
    |y - ruck_y| DESCENDING, so slot 0 lands on the WINGER boundary, the
    last slot lands on the A-anchor (physically-nearest body), and the
    interior lanes stay evenly spaced + hinge-stable.  *side* is retained
    for call-site signature stability; the ordering is side-agnostic
    because slot 0 is ALWAYS the WINGER anchor and the last slot is ALWAYS
    the A anchor, on both the LEFT and RIGHT chains.

    WHY ROLE-AWARE, NOT PURE LIVE-Y (2026-09-02): pure live-Y made slot0
    whoever drifted widest.  During RELEASE/PTB a wide-drifting D (not the
    real WINGER) captured slot0 and was placed at half-width — one side
    "stretched too far".  Preferring the real WINGER (else widest CENTRE)
    keeps the outer anchor on the genuine wide body; live-Y still orders
    the REST (interior lanes + the physically-nearest A-hinge), so the
    hinge stays correct even when the whole side is D.
    """
    def _key(d):
        return (-abs(float(d.y) - float(ruck_y)), getattr(d, "player_id", 0))

    # Role-aware outer anchor (slot0 / WINGER): real WINGER first.
    winger = next(
        (d for d in side_defs if getattr(d, "depth_role", None) == "WINGER"),
        None,
    )
    if winger is not None:
        rest = [d for d in side_defs if d is not winger]
        return [winger] + sorted(rest, key=_key)

    # No WINGER: slot0 = WIDEST CENTRE (the canonical inner-outer anchor).
    centres = [
        d for d in side_defs if getattr(d, "depth_role", None) == "CENTRE"
    ]
    if centres:
        widest_centre = max(
            centres,
            key=lambda d: (
                abs(float(d.y) - float(ruck_y)),
                -getattr(d, "player_id", 0),
            ),
        )
        rest = [d for d in side_defs if d is not widest_centre]
        return [widest_centre] + sorted(rest, key=_key)

    # Neither WINGER nor CENTRE (line still forming / all-D): live-Y order.
    return sorted(side_defs, key=_key)


def _world_side_defenders(world, ruck_y: float, side: int) -> list:
    """Eligible live defenders on *side* of the ruck (spacing_y helper).

    Reads ``world.defending_team`` (the engine population) excluding the
    dynamic fullback actor, mirroring the frame preamble's line filter.
    Returns [] when the world has no defender list (verifier stubs) —
    the caller then falls back to the canonical slot math.

    SIDE-LIST COMPLETENESS (2026-08-28): the per-side list is built by
    EXCLUSION (markers / dummy-half / tacklers / chase bodies never occupy
    a chain slot) rather than by INCLUSION (``depth_role in _SPACING_ROLES``).
    The inclusive gate silently dropped any line defender whose ``depth_role``
    was not yet stamped mid-transition (RELEASE/PTB/TACKLE_DRIVE), leaving the
    per-side list short.  ``spacing_target_y`` then fell into the canonical
    6-lane fallback (``_even_side_lane_y`` ``n_side`` None/<2), mapping a lone
    role-carrying defender onto the sideline-clamped WINGER band (~66.4) and
    doubling it with the true winger.  The split stamps ``depth_side`` on every
    body, so a complete side list is always derivable; only the excluded
    tight-to-ruck / engaged roles are dropped here.
    """
    defenders = getattr(world, "defending_team", None)
    if not defenders:
        return []
    result = []
    for o in defenders:
        if o is None:
            continue
        if getattr(o, "fullback_role_active", False):
            continue
        # Exclusion gate: tight-to-ruck / engaged bodies never join the chain.
        _def_role = getattr(o, "defender_role", None)
        _def_role_name = getattr(_def_role, "name", _def_role)
        if _def_role_name in _SPACING_EXCLUDED_ROLES:
            continue
        # TACKLE-STATE CHAIN GATE (2026-09-01): a tackle-state CHAIN body
        # (A/B/C/D/CENTRE/WINGER) keeps its even role spacing — the ruck-side
        # A pivot / interior lanes must stay in the chain even mid-tackle.
        # Only non-chain tight bodies (markers / DH / tacklers / chase, already
        # excluded above) leave the pool for tackle-state; a chain-but-tackle-
        # state body is NOT dropped (mirrors the spacing_y chain gate).
        if (getattr(o, "tackle_state_active", False)
                and getattr(o, "depth_role", None) not in _SPACING_ROLES):
            continue
        if depth_side_of(o, ruck_y) != side:
            continue
        result.append(o)
    return result


def _side_hinge_body(world, ruck_y: float, side: int,
                     fallback_defenders=None):
    """Return the physically-nearest defender on *side* of the ruck.

    HINGE SOURCE (2026-08-31, ``tmp_hinge_fix.md``): the spacing hinge is
    the inner interpolation boundary every interior lane fans out from.
    It MUST be the body physically nearest the ruck on that side —
    scanned from the FULL ``world.defending_team`` (excluding only
    ``None`` and ``fullback_role_active``), REGARDLESS of
    ``tackle_state_active`` or role exclusion.

    The previous hinge was derived from the spacing-ELIGIBLE side pool
    (``left_defs`` / ``right_defs`` / ``_world_side_defenders``), which
    excludes ``tackle_state_active`` bodies (markers, ex-tacklers still
    inside the tackle radius) and ``_SPACING_EXCLUDED_ROLES``.  During
    RELEASE/PTB most defenders are still tackle-state, so a side's
    eligible pool can be empty/short and the physically-nearest body is
    dropped — the hinge falls to a farther eligible body or the fixed
    ``ruck_y ± 2.0m`` fallback, misplacing the ruck-adjacent defender
    and cascading spacing errors outward (winger → sideline, blown
    interior gap).

    Returns the nearest body (min ``|y - ruck_y|``, ties by
    ``player_id``) or ``None`` when no body exists on the side (the
    only case the canonical ``ruck_adjacent_anchor`` fallback applies).

    ``fallback_defenders`` is used ONLY when ``world.defending_team`` is
    empty/missing (verifier stubs / direct calls passing ``defenders``
    to ``compute_targets``/``spacing_y``).  It lets the stub hinge fall
    back to the passed-in chain pool, so a verifier world with no
    ``defending_team`` still produces the same hinge it did before the
    full-team scan was introduced.  The real engine always has a
    populated ``world.defending_team``, so the fallback is never used
    there.
    """
    defenders = getattr(world, "defending_team", None)
    if not defenders:
        defenders = fallback_defenders
    if not defenders:
        return None

    # ── CHAIN-ELIGIBLE HINGE PREFERENCE (2026-09-01) ──
    # The interior interpolation boundary must be the nearest CHAIN body on
    # the side — never a tight-to-ruck body excluded from the chain (marker /
    # DH / tackler / chase).  A body excluding the chain physically nearest
    # the ruck pulls the ruck-adjacent A lane off the structural
    # ruck_y ± 2.0 m anchor.  Prefer the nearest chain-eligible body; the
    # full-team scan below is only the fallback when no chain body exists on
    # the side (line still forming / verifier stub), so RELEASE/PTB hinge
    # sourcing still works — the chain pool now includes chain bodies even
    # when they are tackle-state, so it is never empty for a formed side.
    def _nearest(pool, chain_only):
        best = None
        best_dist = None
        for o in pool:
            if o is None:
                continue
            if getattr(o, "fullback_role_active", False):
                continue
            if depth_side_of(o, ruck_y) != side:
                continue
            # HARD HINGE EXCLUSION (2026-09-03): markers / DH / tacklers /
            # chase / DEF_RETREAT_10 can never be the inner boundary —
            # regardless of the chain_only flag.  This kills the marker-as-
            # hinge path that fanned the short side out too wide during
            # RELEASE/PTB.
            _o_role_name = getattr(
                getattr(o, "defender_role", None), "name",
                getattr(o, "defender_role", None),
            )
            if _o_role_name in _HINGE_EXCLUDED_ROLES:
                continue
            if chain_only:
                if getattr(o, "depth_role", None) not in _SPACING_ROLES:
                    continue
            # RUCK-STRADDLE DEADBAND (2026-09-01): a body this close to ruck_y
            # straddles the ruck line and can flip sides frame-to-frame,
            # becoming a wrong-side hinge.  Exclude it so the hinge only comes
            # from a body clearly on one side.
            if abs(float(o.y) - float(ruck_y)) <= HINGE_STRADDLE_DEADBAND_M:
                continue
            dist = abs(float(o.y) - float(ruck_y))
            if best is None or (dist < best_dist) or (
                dist == best_dist
                and getattr(o, "player_id", 0) < getattr(best, "player_id", 0)
            ):
                best = o
                best_dist = dist
        return best

    chain_hinge = _nearest(defenders, chain_only=True)
    if chain_hinge is not None:
        return chain_hinge
    return _nearest(defenders, chain_only=False)


# ─────────────────────────────────────────────────────────────────────
# ENGAGEMENT-BAND SPACING-Y PULL (TASK IV, 2026-08-14)
# ─────────────────────────────────────────────────────────────────────

def _band_spacing_pull(world, tuners, d, y: float,
                       max_abs_m: float | None = None) -> float:
    """Apply the engagement-band spacing-Y pull to *y* (default 0.0).

    The AGGRESSIVE / BALANCED / PASSIVE engagement band is computed from
    the BALL CARRIER's X relative to the defensive-line anchor via
    ``defensive_engagement_state.desired_engagement_role`` (bc < anchor -
    AGGRESSIVE_BAND_M -> AGGRESSIVE; bc > anchor + PASSIVE_BAND_M ->
    PASSIVE; else BALANCED).  Before Task IV the band had NO influence on
    spacing-Y, so PASSIVE defenders sat structurally deep and the first
    three defenders "missed" the BC by 3-5 m.

    IMPORTANT (2026-08-14): the pull deliberately uses the DESIRED band
    from live BC.x rather than the defender's ``role_depth_class`` stamp.
    In the real engine the un-gated frame preamble (run_defensive_pipeline)
    builds the spacing dict BEFORE ``classify_depth_all`` stamps
    ``role_depth_class``, so reading the stamp there would make the frame
    dict un-pulled while the ``spacing_y`` helper was pulled — a dual-
    source divergence.  Computing from (bc_x, anchor) makes every spacing
    consumer in the frame see the SAME pulled Y regardless of ordering,
    and reacts directly to BC threat exactly as the band intends.

    This pull is applied AFTER the single-offset chain Y (and after the
    governor clamp) so the chain stays the pure single-offset authority.
    When there is no ball carrier (kickoff/catch bootstrap, verifier
    stubs) the pull is 0.0 — no band to react to:

        PASSIVE    -1.25 m   collapse toward the BC / ruck lane
        BALANCED   -0.75 m   compress width earlier
        AGGRESSIVE -0.50 m   shoot earlier on threat

    The offset table lives in DefenceTuners.ENGAGEMENT_SPACING_Y_OFFSET
    (single source of truth for every band->spacing parameter).
    """
    bc = getattr(world, "ball_carrier", None)
    bc_x = getattr(bc, "x", None) if bc is not None else None
    if bc_x is None:
        return y
    # Lazy import: defensive_engagement_state pulls anchor_authority +
    # player only (no cycle back into this module); keeping the import
    # local mirrors the module's existing lazy pattern.
    from game.defence.defence_lvl_1.defensive_engagement_state import (
        desired_engagement_role,
    )
    band = desired_engagement_role(float(bc_x), _anchor_m(world))
    table = getattr(tuners, "ENGAGEMENT_SPACING_Y_OFFSET", None) or {}
    pull = table.get(band, 0.0)
    if pull == 0.0:
        return y
    # LANE-STEP CLAMP (2026-09-12): the band offsets are FIXED metres tuned
    # against a full-width line (~4 m lanes).  On a narrow governed band the
    # published lane step shrinks, and an unclamped fixed offset then eats
    # half a lane — the far-edge winger was pulled onto its neighbour and two
    # interior bodies onto one lane.  Bound the pull to the caller-supplied
    # fraction of the published lane step so the band may bias the line but
    # never replace the count-lane geometry.
    if max_abs_m is not None:
        pull = math.copysign(
            min(abs(float(pull)), max(0.0, float(max_abs_m))),
            float(pull),
        )
    return y + float(pull)


# ─────────────────────────────────────────────────────────────────────
# SPACING OFFSET — the single per-role lateral offset
# ─────────────────────────────────────────────────────────────────────

def compute_spacing_offset(role: str, side_width: float, tuners) -> float:
    """Return the lateral spacing offset — RETIRED-IN-PLACE (Stage 3).

    DOCUMENTED-DEAD WRAPPER: legacy width-driven per-role lateral offset;
    NOT called by the live pipeline.  Retained for historical / verifier
    reference; Stage 7 removes it.  Prior semantics:

    WINGER-INWARD spacing (2026-08-18): the offset is the even side-lane
    position measured from ruck_y, produced by the canonical 6-lane
    side distribution WINGER..A:

        WINGER       -> side_width * 0.5             (stable outer anchor)
        every other  -> A + (WINGER - A) * (1 - rank/5)  (inward)
        A            -> RUCK_ADJACENT_ANCHOR_M       (ruck_y ± 2 m, LAW)

    The WINGER is the stable outer anchor.  Interior defenders are
    interpolated INWARD, so they can never drift outward or collapse
    onto the sideline.
    """
    rank = _role_radial_rank(role)
    half = float(side_width) * 0.5
    a_pos = RUCK_ADJACENT_ANCHOR_M  # ANCHOR LAW: exact 2.0 m offset
    winger = half
    if role == "A":
        return a_pos + _role_micro_offset(role)
    if role == "WINGER":
        return winger + _role_micro_offset(role)
    # Interior: even interpolation, WINGER-inward.
    rank = min(rank, _CANONICAL_SIDE_COUNT - 1)
    t = float(rank) / float(_CANONICAL_SIDE_COUNT - 1)
    return (a_pos + (winger - a_pos) * (1.0 - t)) + _role_micro_offset(role)


# ─────────────────────────────────────────────────────────────────────
# PER-DEFENDER SPACING TARGET
# ─────────────────────────────────────────────────────────────────────

def _fit_lanes_to_band(lanes, ruck_y: float, half_width: float) -> list:
    """Re-space *lanes* AS A UNIT into ``ruck_y ± half_width``.

    GOVERNED-BAND UNIT FIT (2026-09-12).  A pure per-lane min/max clamp
    merges every overrunning lane onto the band edge: two or more
    defenders are handed an IDENTICAL Y (short-side overlap /
    duplication) and the outer pair compresses to a 0.0 m gap — the
    exact symptom the count-lane model exists to prevent.

    The ladder therefore keeps its COUNT and its immutable inner A
    pivot, and the lanes are re-spaced evenly out to the band edge — the
    same unit-clamp law ``side_lanes`` already applies to the sideline.
    The input is never mutated.

    ``lanes`` is ordered innermost (A pivot, index 0) -> outermost.  The
    band edge is the governed half-width on the ladder's own side of the
    ruck, so the fit is side-agnostic.
    """
    n = len(lanes)
    if n < 2 or half_width <= 0.0:
        return list(lanes)
    inner = float(lanes[0])
    outer = float(lanes[-1])
    direction = 1 if outer >= inner else -1
    bound = float(ruck_y) + float(direction) * float(half_width)
    # The immutable A pivot may not be moved by a width band: if the
    # pivot itself lies outside the band there is nothing safe to fit.
    if direction * (bound - inner) <= 0.0:
        return list(lanes)
    # Already inside the band -> byte-identical to the pre-fix result.
    if direction * (outer - bound) <= 1e-9:
        return list(lanes)
    span = bound - inner
    denom = float(n - 1)
    return [inner + span * (float(i) / denom) for i in range(n)]


def _governed_band_lanes(lanes, ruck_y: float, governor, world, tuners,
                         side_span: float | None = None) -> list:
    """Fit *lanes* into this side's PUBLISHED span when the governor is live.

    Gated on the SAME condition as the per-spacing width clamp
    (``tuners.governor_width_active and world.governor_width_active``), so
    a disabled governor leaves the ladder untouched.

    ``side_span`` is the ladder's OWN published per-side span.  GOVERNED
    WIDTH IS THE AUTHORITATIVE TOTAL SPAN (2026-09-14): the band must be
    that published span, never a symmetric ``governed_width_m / 2`` — the
    symmetric form re-declared the governed width as a PER-SIDE value and
    re-narrowed the far side of an off-centre ruck, breaking the
    ``realised span == governed width`` law.  The symmetric fallback
    survives only for a caller with no published span at all (stub worlds).
    """
    if governor is None:
        return lanes
    if not bool(getattr(tuners, "governor_width_active", False)):
        return lanes
    if not bool(getattr(world, "governor_width_active", False)):
        return lanes
    if side_span is not None and float(side_span) > 0.0:
        half_width = float(side_span) * 0.5
    else:
        half_width = float(governor.governed_width_m(world)) * 0.5
    return _fit_lanes_to_band(lanes, ruck_y, half_width)


def spacing_y(world, d, tuners, governor=None) -> float:
    """Return the split-spacing Y for *d* — COUNT-LANE model (Stage 3).

    Task F global rule: every defensive movement writer reads
    ``spacing_targets[player].y``.  This per-defender helper is the shared
    call for writers outside the structured pipeline (scramble, chase,
    containment, retreat, line-break, RELEASE/PTB retreats, kickoff
    formation).

    COUNT → LANES → SPACING (2026-09-03, Stage 3): "count is the single
    boss."  The per-side defender count is read from the live eligible
    side list (``_world_side_defenders``), ``side_lanes`` lays out EXACTLY
    that many even lanes from the ruck-adjacent A pivot outward at the
    uniform ``defender_gap_m``, and *d* is placed on its slot's lane
    (slot 0 = WINGER/outermost, last = A/innermost).  Width is never an
    input — it is an output of the lane model (``derive_side_width_m``).

    STUB FALLBACK (no ``world.defending_team``): the canonical 6-lane
    distribution is used, with the role's radial rank mapping to a lane
    index (WINGER -> outermost lane, A -> A-pivot lane).  This keeps
    verifier stubs deterministic.

    Non-chain defenders (markers, DH, tacklers, DEF_RETREAT_10, etc.)
    still return ruck_y.
    """
    ruck_y = getattr(world, "ruck_y_m", 35.0)
    role = getattr(d, "depth_role", None)
    # EXCLUSION GATE (2026-08-29): chain Y is withheld only for
    # tight-to-ruck / engaged bodies (markers, DH, tacklers, chase) and
    # tackle-state holders — NOT for a None-role line defender who is
    # mid-transition (RELEASE/PTB/TACKLE_DRIVE).  Mirrors
    # _world_side_defenders (2026-08-28).
    if (getattr(d, "tackle_state_active", False)
            and getattr(d, "depth_role", None) not in _SPACING_ROLES):
        return float(ruck_y)
    _def_role = getattr(d, "defender_role", None)
    _def_role_name = getattr(_def_role, "name", _def_role)
    if _def_role_name in _SPACING_EXCLUDED_ROLES:
        return float(ruck_y)

    # ── SINGLE GLOBAL EVEN LADDER — THREE-POINT WIDTH IS THE SOLE AUTHORITY ──
    # (2026-09-15) The governed width W is laid out as ONE uniform ladder
    # across the whole line (identical gaps W/(n-1)); this defender takes
    # the lane for its position in the line's left→right order.  No
    # per-side split, count, room cap or band pull can make one gap differ
    # from another.  Matches compute_targets exactly (same helper), so the
    # frame dict and this fallback can never disagree.
    _gt, _ = global_line_targets(world, ruck_y, tuners)
    if id(d) in _gt:
        return clamp_to_sideline(float(_gt[id(d)]), tuners)

    side = depth_side_of(d, ruck_y)
    side_defs = _world_side_defenders(world, ruck_y, side)
    n_side = len(side_defs)

    if n_side >= 1:
        # COUNT-LANE MODEL: exactly n_side even lanes, slot from ordering.
        ordered = _side_inside_out(side_defs, side, ruck_y)
        _sw_l, _sw_r = side_widths(world, tuners, ruck_y=ruck_y)
        lanes = side_lanes(
            n_side, ruck_y, side, tuners,
            span=(_sw_r if int(side) == RIGHT else _sw_l),
        )
        slot = next(
            (i for i, o in enumerate(ordered) if o is d), None,
        )
        if slot is None:
            # Defender not in the eligible side list -> physical-Y slot
            # (farthest-from-ruck first, so slot 0 = WINGER boundary).
            _d_dist = abs(float(d.y) - float(ruck_y))
            slot = sum(
                1 for o in ordered
                if abs(float(o.y) - float(ruck_y)) > _d_dist
            )
            slot = min(slot, n_side - 1)
        ty = lanes[n_side - 1 - slot]
        ty = clamp_to_sideline(ty, tuners)
    else:
        # STUB / EMPTY SIDE — canonical 6-lane fallback by role radial rank.
        _canonical = _CANONICAL_SIDE_COUNT
        _stub_lw, _stub_rw = side_widths(world, tuners, ruck_y=ruck_y)
        lanes = side_lanes(
            _canonical, ruck_y, side, tuners,
            span=(_stub_rw if int(side) == RIGHT else _stub_lw),
        )
        lanes = _governed_band_lanes(
            lanes, ruck_y, governor, world, tuners,
            side_span=(_stub_rw if int(side) == RIGHT else _stub_lw),
        )
        rank = min(_role_radial_rank(role), _canonical - 1)
        ty = lanes[_canonical - 1 - rank]
        ty = clamp_to_sideline(ty, tuners)

    if (
        governor is not None
        and bool(getattr(tuners, "governor_width_active", False))
        and bool(getattr(world, "governor_width_active", False))
    ):
        ty = governor.apply_width_clamp(ty, world)
    # TASK IV (2026-08-14): engagement-band spacing-Y pull — PASSIVE /
    # BALANCED / AGGRESSIVE collapse/compress/shoot earlier after a catch.
    # The A / WINGER ruck-adjacent anchors are PINNED — they never receive
    # the band pull so the ruck-adjacent pair stays exactly on rule.
    if role in ("A", "WINGER"):
        return ty
    return _band_spacing_pull(world, tuners, d, ty)


def spacing_target_y(
    d,
    side_width: float,
    ruck_y: float,
    tuners,
    side_defenders: list = None,
    is_short_side: bool = False,
    hinge_y: float | None = None,
) -> float:
    """Spacing Y for one defender — RETIRED-IN-PLACE (Stage 3, 2026-09-03).

    DOCUMENTED-DEAD WRAPPER: this is the LEGACY width-driven per-defender
    spacing target (``_even_side_lane_y`` + ``_clamped_winger_y``).  It
    is NOT called by the live pipeline anymore — spacing_y /
    compute_targets use the count-lane model.  Retained ONLY for
    historical reference / verifier greps; scheduled for removal in
    Stage 7.  Prior semantics:

    RUCK-ADJACENT EVEN SPACING WITH SIDELINE BUFFER (2026-08-19):

    1. The WINGER is clamped first: if its theoretical position
       (ruck_y + side * half_width) would place it closer than
       ``SIDELINE_BUFFER_M`` to the touchline, it is pulled inward.
    2. The usable side width is recomputed as the distance between the
       clamped WINGER and the HINGE — the live nearest-to-ruck body
       on that side (spec 2026-08-30), NOT the fixed ruck_y ± 2.0 m
       A-role anchor.
    3. All interior defenders (CENTRE, D, C, B) are distributed evenly
       inside this *real* usable side width, not the theoretical
       half-width.

    ``side_defenders`` is the live per-side defender list (the real
    side count -> the real even gaps).  When omitted (verifier stubs
    calling without a full side) the canonical 6-lane A..WINGER
    distribution is used — WINGER still lands exactly on half-width so
    the "wingers correct" behaviour is preserved everywhere.
    """
    role = getattr(d, "depth_role", "D") or "D"
    side = depth_side_of(d, ruck_y)

    # ── Step 1: clamp the WINGER first (short-side buffer) ──
    clamped_winger = _clamped_winger_y(side_width, ruck_y, side, tuners)

    # ── Step 2: the HINGE anchor — nearest-to-ruck body on this side ──
    # Spec (2026-08-30): the spacing hinge is the live position of the
    # defender physically closest to the ruck on each side (min
    # |y - ruck_y|), NOT the fixed ruck_y ± 2.0 m A-role anchor.
    # _side_inside_out orders the side outside->inside, so the LAST
    # element is the nearest body and its actual .y is the inner
    # interpolation boundary.  Fall back to the canonical ruck_y ± 2.0 m
    # anchor only when no live side list is present (verifier stubs).
    if side_defenders:
        ordered = _side_inside_out(side_defenders, side, ruck_y)
        n_side = len(ordered)
        if hinge_y is not None:
            # FULL-TEAM HINGE (2026-08-31): the hinge is the physically-
            # nearest body on the side, scanned from world.defending_team
            # by _side_hinge_body (regardless of tackle-state / role
            # exclusion) and passed in.  It overrides the eligible-pool
            # nearest body so a marker / ex-tackler near the ruck can
            # still define the inner interpolation boundary.
            a_y_clamped = hinge_y
        else:
            _live_hinge = float(getattr(
                ordered[-1], "y", ruck_adjacent_anchor(ruck_y, side),
            ))
            a_y_clamped = _clamp_hinge_y(_live_hinge, ruck_y, side, tuners)
        # PHYSICAL-Y SLOT (2026-09-01): the slot is ALWAYS the defender's
        # physical-Y index in the side list (farthest-from-ruck first,
        # nearest last) — never the legacy A/C/D/CENTRE role ordinal.
        slot = next((i for i, o in enumerate(ordered) if o is d), None)
        if slot is None:
            _d_dist = abs(float(d.y) - float(ruck_y))
            slot = sum(
                1 for o in ordered
                if abs(float(o.y) - float(ruck_y)) > _d_dist
            )
    else:
        n_side = None
        # PHYSICAL-Y SLOT (2026-09-01): no side list (stub/empty side) —
        # never use the legacy role ordinal.  Stage a singleton slot so the
        # canonical fallback in _even_side_lane_y uses this position.
        slot = 0
        a_y_clamped = (
            hinge_y if hinge_y is not None
            else ruck_adjacent_anchor(ruck_y, side)
        )

    y_raw = _even_side_lane_y(
        d, slot, n_side, side, ruck_y, side_width, tuners,
        clamped_winger_y=clamped_winger,
        clamped_a_y=a_y_clamped,
        is_short_side=is_short_side,
    )
    return clamp_to_sideline(y_raw, tuners)


# ─────────────────────────────────────────────────────────────────────
# COUNT-DRIVEN SPLIT (2026-08-21; ruck-geometry allocation 2026-08-28)
# ─────────────────────────────────────────────────────────────────────
# The per-side defender counts are dictated by the RUCK-GEOMETRY model
# (distance from ruck_y to the nearest sideline) BEFORE any defender is
# assigned to a side — see _compute_ruck_geometry_split /
# required_side_counts.  The governed width does NOT drive the count;
# it only feeds the lateral band centres below.  Defenders are then
# ASSIGNED nearest-first and ARE ALLOWED TO CROSS THE RUCK: the n_left
# defenders whose positions are cheapest to place on the left band
# (smallest |d.y - left_centre| - |d.y - right_centre|) go LEFT, the
# rest RIGHT.  This is the SINGLE authority for depth_side.
# assign_depth_roles stamps it BEFORE any role seeding;
# compute_targets / spacing_y / geometry consume it via depth_side_of.
# ─────────────────────────────────────────────────────────────────────

def side_band_centre(side: int, ruck_y: float, side_width: float,
                     tuners) -> float:
    """Midpoint of one side's usable band (A anchor <-> WINGER anchor).

    The band is the interval between the ruck-adjacent A anchor
    (ruck_adjacent_anchor: ruck_y ± 2.0 m) and the clamped WINGER anchor
    (ruck_y ± half-width).  Its centre is the reference point the
    nearest-first assignment measures each defender against.
    """
    a_y = ruck_adjacent_anchor(ruck_y, side)  # ANCHOR LAW: exact ± 2.0 m
    w_y = _clamped_winger_y(side_width, ruck_y, side, tuners)
    return 0.5 * (a_y + w_y)


def _compute_ruck_geometry_split(n_total: int, dist_to_short_side: float) -> int:
    """Return n_short from ruck geometry model (distance to nearest sideline).

    RUCK-GEOMETRY SPLIT MODEL (2026-08-28):
    ─────────────────────────────────────
    The split is driven purely by distance from ruck_y to the nearest sideline.
    The model is SCALED BY LINE SIZE: the short side receives a FRACTION of
    the line (≈10% → 50%) as the distance ramps from 5m to 35m, so it is
    correct for ANY defender count (6-man, 10-man, etc.) — not hardcoded to
    a 10-man 0-10 / 1-9 / 5-5 progression.

    0–5m from touch (blind-side dead space):
        distance ∈ [0, 5.0) m → n_short = 0 (all defenders on the open side;
        the blind side is true dead space and gets nobody).

    5–35m from touch (linear progression):
        distance ∈ [5.0, 35.0] m → linear ramp of the short-side FRACTION:
            frac = 0.10 + (distance - 5.0) / 30.0 * 0.40
            n_short = round(n_total * frac)
        at 5m:  frac ≈ 0.10 → n=10 gives 1-9, n=6 gives 1 (0.6→1)
        at 35m: frac = 0.50 → n=10 gives 5-5, n=6 gives 3-3

    Beyond 35m (open field):
        distance > 35.0 m → stable even split (n_short = n_total // 2).
        For n=10 this is the spec's "5-5"; for any n it is the most even
        defensible split.

    Blind-side DEAD SPACE intentionally returns 0 (no defender on the short
    side) — the "both sides ≥ 1" rule does NOT apply inside the dead space.
    When n_total < 2, return the most even split possible.
    """
    if n_total <= 0:
        return 0
    if n_total == 1:
        return 1  # All on one side

    dist = float(dist_to_short_side)

    if dist < 5.0:
        # Blind-side dead space: 0 on the short side (0-10 for a 10-man
        # line; 0-n for any other count).  The blind side is empty.
        n_short = 0
    elif dist <= 35.0:
        # Linear progression scaled by line size: short side gets ~10% at
        # 5m rising to ~50% at 35m of the line.  At 5m a 10-man line is
        # 1-9; at 35m it is 5-5.  For a 6-man line this is 1-5 and 3-3.
        # Once past the dead-space boundary the short side always holds at
        # least 1 defender (when n_total >= 2): the ramp begins at 1-9
        # (n=10) / 1-(n-1) for smaller lines.
        t = (dist - 5.0) / 30.0  # 0 at 5m, 1 at 35m
        frac = 0.10 + t * 0.40   # 0.10 -> 0.50
        n_short = round(n_total * frac)
        if n_total >= 2 and n_short < 1:
            n_short = 1
    else:
        # Beyond 35m: stable even split (n_total // 2).  5-5 for a 10-man
        # line; 3-3 for a 6-man line.  Never falls below 1 per side.
        return n_total // 2

    n_short = int(n_short)
    # Clamp to valid range: [0, n_total].
    n_short = max(0, min(n_total, n_short))
    if n_short == 0:
        # Blind-side dead space: short side deliberately empty.
        return 0
    if n_short >= n_total:
        # Pathological case; never put everyone on one side when n >= 2.
        return max(1, n_total - 1)
    return n_short


def kickoff_window_active(world) -> bool:
    """True during the kickoff-catch → first OPEN_PLAY window.

    Positive gate: no ruck yet AND we just left a kickoff.  `kickoff_side_sign`
    defaults to 1, so without the `just_left_kickoff_timer` marker a non-kickoff
    world (verifier stubs / normal ruck play) would over-fire the 6/5 bias.
    """
    return (
        not getattr(world, "ruck_established", True)
        and getattr(world, "just_left_kickoff_timer", 0.0) > 0.0
        and getattr(world, "kickoff_side_sign", None) in (-1, 1)
    )


# ─────────────────────────────────────────────────────────────────────
# COUNT AUTHORITY (2026-09-03) — count is the single boss
# ─────────────────────────────────────────────────────────────────────
# The per-side defender count is decided FIRST from physical room +
# tactical logic.  Lanes are then laid out for exactly that count
# (side_lanes), and width is DERIVED from the lanes (derive_side_width_m).
# No subsystem may feed a width back into the count — the count is the
# authority; width is an output.
#
# PHASE-NEUTRAL (unification directive 2026-09-04): no phase may bias the
# short-side fraction.  TES, PTB, POST_RUCK and OPEN_PLAY all read the
# SAME count-lane geometry with ZERO overrides — the count is purely
# room-derived.  The phase-bias machinery (_COUNT_PHASE_PROFILES /
# _count_phase_profile / resolve_count_phase) is documented-dead.
# ─────────────────────────────────────────────────────────────────────

# Phase-profile keys -> the count bias applied to the short-side fraction.
# DOCUMENTED-DEAD (2026-09-04): the count is now purely room-derived, so no
# phase biases the short-side fraction.  Retained ONLY for historical /
# verifier reference; not read by any live count rule.  Stage 7 removes it.
_COUNT_PHASE_PROFILES = {
    "TES":        {"bias": "release", "max_frac": "count_frac_max_short"},
    "RELEASE":    {"bias": "release", "max_frac": "count_frac_max_short"},
    "PTB":        {"bias": "ptb",     "max_frac": "count_frac_max_short"},
    "POST_RUCK":  {"bias": "post_ruck", "max_frac": "count_frac_max_short"},
    "OPEN_PLAY":  {"bias": "open_play", "max_frac": "count_frac_max_short"},
    "KICKOFF":    {"bias": "open_play", "max_frac": "count_frac_max_short"},
    "NEUTRAL":    {"bias": "open_play", "max_frac": "count_frac_max_short"},
}


def resolve_count_phase(world) -> str:
    """Return the count-phase profile key — DOCUMENTED-DEAD (2026-09-04).

    Maps the GameSubState enum / OPE phase label into one of the
    count-profile keys: TES / RELEASE / PTB / POST_RUCK / OPEN_PLAY /
    KICKOFF / NEUTRAL.  Unknown phases default to NEUTRAL (open-play
    profile) so the count rule never explodes on a stub world.

    DEAD FOR COUNT GEOMETRY: the count is now purely room-derived, so no
    phase may bias the short-side fraction.  Retained ONLY for the debug
    ``world.count_split_phase`` publication; Stage 7 removes it.
    """
    sub = getattr(world, "sub_state", None)
    if sub is not None:
        name = str(getattr(sub, "value", sub)).upper()
        if name in ("TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE",
                    "TACKLE_DOMINANCE"):
            return "TES"
        if name in ("TACKLE_RELEASE",):
            return "RELEASE"
        if name in ("PTB_READY", "PTB"):
            return "PTB"
        if name in ("POST_PTB_RESET", "POST_RUCK_OPEN_PLAY"):
            return "POST_RUCK"
        if name == "OPEN_PLAY":
            return "OPEN_PLAY"
        if name in ("KICKOFF_IN_AIR", "KICKOFF_START", "RESET_LINE"):
            return "KICKOFF"
    label = getattr(world, "_ope_phase_label", None)
    if label is not None:
        lab = str(label).upper()
        if lab in ("RELEASE", "TACKLE_RELEASE"):
            return "RELEASE"
        if lab in ("PTB_PREP", "PTB_EXEC", "PTB"):
            return "PTB"
        if lab in ("POST_RUCK_OPEN_PLAY", "POST_PTB_RESET"):
            return "POST_RUCK"
    return "NEUTRAL"


def _count_phase_profile(world, tuners, phase: str | None = None) -> dict:
    """Resolve the count-profile parameters — DOCUMENTED-DEAD (2026-09-04).

    Returns {"bias": float, "max_frac": float}.  The bias is the
    fractional nudge applied to the short-side count; max_frac caps the
    short-side fraction (never more than half the line by default).

    DEAD: the count is now purely room-derived (no phase bias block in
    ``decide_side_counts``), so this function has ZERO live callers.
    Retained for historical / verifier reference; Stage 7 removes it.
    """
    key = (phase or resolve_count_phase(world)).upper()
    profile = _COUNT_PHASE_PROFILES.get(key, _COUNT_PHASE_PROFILES["NEUTRAL"])
    bias_name = profile["bias"]
    bias_map = {
        "release": "count_phase_bias_release",
        "ptb": "count_phase_bias_ptb",
        "post_ruck": "count_phase_bias_post_ruck",
        "open_play": "count_phase_bias_open_play",
    }
    bias_value = float(getattr(
        tuners, bias_map[bias_name], 0.0,
    ))
    max_frac = float(getattr(
        tuners, profile["max_frac"], 0.50,
    ))
    return {"bias": bias_value, "max_frac": max_frac}


def side_room_m(world, tuners, side: int, ruck_y: float | None = None) -> float:
    """Usable half-width room (m) from the ruck to the outer lane limit.

    The outer lane limit is the sideline margin + short_side_buffer_m —
    the same usable distance ``side_widths`` caps the short side at.  This
    is the physical room the count decision must respect: it can never
    place more defenders than ``floor((room - 2.0) / min_gap) + 1``.

    Returns the room on *side* (LEFT/RIGHT) of the ruck.  Verifier stubs
    without a ruck fall back to a symmetric mid-field room so direct calls
    stay deterministic.
    """
    margin = float(getattr(tuners, "sideline_margin_m", 3.0))
    field_max = float(getattr(tuners, "field_width_m", 70.0)) - margin
    buffer = float(getattr(tuners, "short_side_buffer_m", 3.0))
    if ruck_y is None:
        ruck_y = float(getattr(world, "ruck_y_m", 35.0))
    if int(side) == LEFT:
        return max(0.0, float(ruck_y) - (margin + buffer))
    return max(0.0, field_max - buffer - float(ruck_y))


def _base_short_count(n_total: int, room_short: float) -> int:
    """Base short-side count from the usable physical room.

    Fraction ramps linearly from ~0.10 (no room) to 0.50 (full centred
    room ~29 m).  This is the STABLE ruck-position rule; phase only biases
    it (never flips it).  ``n_total == 1`` is a corner case that always
    returns 1 (a lone defender goes to the ruck-adjacent slot).
    """
    if n_total <= 0:
        return 0
    if n_total == 1:
        return 1
    room_max = 29.0  # canonical centred half-room (unit: metres)
    r = min(max(float(room_short), 0.0), room_max)
    frac = 0.10 + (r / room_max) * 0.40   # 0.10 -> 0.50
    n_short = round(n_total * frac)
    if n_total >= 2 and n_short < 1:
        n_short = 1
    return n_short


def decide_side_counts(world, tuners, n_total: int,
                       ruck_y: float | None = None, *,
                       phase: str | None = None,
                       ball_side: int | None = None,
                       ball_side_bias: int = 0) -> tuple:
    """THE count authority: decide (n_left, n_right) from room only.

    SINGLE AUTHORITY (2026-09-03; PHASE-NEUTRAL 2026-09-04):
      1. BASE rule — the short-side count comes from the usable physical
         room (``side_room_m``), never from a width formula.
      2. FEASIBILITY — the count can NEVER exceed what the room supports:
             n_side ≤ floor((room - 2.0) / min_defender_gap_m) + 1
         so "3+ defenders into 1–2 of room" is impossible by construction.
      3. BALL-SIDE / edge-release tactical bias on top.
      4. SPLIT-SIDE GUARANTEE — both sides ≥ 1 when n_total ≥ 2.
      5. HYSTERESIS — a new split is adopted only when it differs from the
         previous frame's split by more than ``count_split_hysteresis``
         (for the same ruck), preventing any residual phase-jump from
         re-partitioning the line frame-to-frame.

    PHASE-NEUTRAL (unification directive 2026-09-04): ``phase`` is an
    INERT parameter — no phase (TES/PTB/POST_RUCK/OPEN_PLAY) may bias the
    short-side fraction.  The count is purely room-derived, so all phases
    read byte-identical count-lane geometry.

    Returns (n_left, n_right).
    """
    if n_total <= 0:
        return 0, 0
    if n_total == 1:
        short_side = resolve_short_side(world, tuners, ruck_y=ruck_y)
        return (1, 0) if short_side == LEFT else (0, 1)

    if ruck_y is None:
        ruck_y = float(getattr(world, "ruck_y_m", 35.0))
    min_gap = float(getattr(tuners, "min_defender_gap_m", 3.0))
    short_side = resolve_short_side(world, tuners, ruck_y=ruck_y)

    room_short = side_room_m(world, tuners, short_side, ruck_y)
    room_open = side_room_m(world, tuners, -short_side, ruck_y)

    # 1. BASE count from room (stable rule).  PURELY ROOM-DERIVED: no
    # phase bias, no width input — the count is the single boss.
    n_short = _base_short_count(n_total, room_short)
    n_open = n_total - n_short

    # 2. FEASIBILITY guarantee (count never exceeds room).
    max_fit_short = max(
        1, int(math.floor((room_short - RUCK_ADJACENT_ANCHOR_M) / min_gap)) + 1
    )
    max_fit_open = max(
        1, int(math.floor((room_open - RUCK_ADJACENT_ANCHOR_M) / min_gap)) + 1
    )

    # 2a. WIDTH-FEASIBILITY (2026-09-13): the governed width must also be
    # able to SPACE the count it is handed.  side_lanes() derives its step
    # as span/(n_side-1) and floors it at min_defender_gap_m, so a count
    # that needs more than the width can give would either overlap adjacent
    # lanes or push the outermost lane past the width tuner.  Bound the
    # count by the governed width exactly as it is bounded by the physical
    # room:
    #     n_side <= floor((width/2 - 2.0) / min_gap) + 1
    # The governed width is thereby a genuine constraint on the count as
    # well as the span — the two authorities can no longer disagree.
    _w_left, _w_right = side_widths(world, tuners, ruck_y=ruck_y)
    _width_half_short = max(
        0.0, (_w_left if short_side == LEFT else _w_right) * 0.5
    )
    _width_half_open = max(
        0.0, (_w_right if short_side == LEFT else _w_left) * 0.5
    )
    max_width_short = max(
        1,
        int(math.floor(
            (_width_half_short - RUCK_ADJACENT_ANCHOR_M) / min_gap
        )) + 1,
    )
    max_width_open = max(
        1,
        int(math.floor(
            (_width_half_open - RUCK_ADJACENT_ANCHOR_M) / min_gap
        )) + 1,
    )

    n_short = min(n_short, max_fit_short, max_width_short)
    n_open = min(n_open, max_fit_open, max_width_open)
    # Re-normalise so the total still sums to n_total when one side was
    # clamped (move the surplus to the other side).
    if n_short + n_open != n_total:
        if n_short < n_total:
            n_open = n_total - n_short
        else:
            n_short = n_total - n_open

    # Assign short/open to left/right (single short-side authority).
    if short_side == LEFT:
        n_left, n_right = n_short, n_open
    else:
        n_left, n_right = n_open, n_short

    # 3. BALL-SIDE / edge-release tactical bias (donor keeps >= 1).
    bias = max(0, int(ball_side_bias))
    if ball_side == RIGHT and bias > 0:
        for _ in range(bias):
            if n_left - 1 >= 1:
                n_left -= 1
                n_right += 1
    elif ball_side == LEFT and bias > 0:
        for _ in range(bias):
            if n_right - 1 >= 1:
                n_right -= 1
                n_left += 1

    # 4. SPLIT-SIDE GUARANTEE.
    if n_total >= 2:
        if n_left == 0:
            n_left = 1
            n_right = n_total - 1
        elif n_right == 0:
            n_right = 1
            n_left = n_total - 1

    # 5. HYSTERESIS against violent phase-boundary flips.
    _hys = max(0, int(getattr(tuners, "count_split_hysteresis", 1)))
    if _hys > 0:
        _prev = getattr(world, "_last_count_split", None)
        if _prev is not None and bool(getattr(world, "ruck_established", True)):
            _prev_left, _prev_right, _prev_ruck = _prev
            # STAGE 5 TOTAL INTEGRITY (2026-09-13): the previous split may
            # only be adopted when it was solved for the SAME population
            # total.  Without this check the hysteresis kept the OLD total
            # while the eligible population changed by one body — the
            # measured published-count lag (Stage 4 D1/D3C).  A split that
            # does not sum to n_total is not a valid predecessor.
            _prev_total_ok = (
                int(_prev_left) + int(_prev_right)
            ) == int(n_total)
            if (abs(float(ruck_y) - float(_prev_ruck)) <= 0.5
                    and _prev_total_ok):
                # Same ruck AND same total: adopt only when the change
                # exceeds hysteresis.
                if abs(n_left - _prev_left) <= _hys and abs(n_right - _prev_right) <= _hys:
                    n_left, n_right = _prev_left, _prev_right

    # ── HARD TOTAL INTEGRITY (Stage 5) ──
    # The published split ALWAYS sums to n_total: no hysteresis adoption,
    # ball-side bias or feasibility clamp may leave a count that disagrees
    # with the population it was solved for.
    n_left = max(0, min(int(n_left), int(n_total)))
    n_right = int(n_total) - n_left

    # Publish the frame count split for the hysteresis / debug readers.
    world._last_count_split = (int(n_left), int(n_right), float(ruck_y))
    world.count_split_left = int(n_left)
    world.count_split_right = int(n_right)
    world.count_split_phase = resolve_count_phase(world)
    return int(n_left), int(n_right)


# ─────────────────────────────────────────────────────────────────────
# LANE MATHS — even lanes for exactly that count (2026-09-03)
# ─────────────────────────────────────────────────────────────────────
# The count is the boss.  Given exactly n_side defenders on a side, build
# that many even lanes from the ruck-adjacent A pivot (ruck_y ± 2.0)
# outward at a uniform gap.  No role special-casing, no width
# interpolation.  The outermost lane IS the WINGER; width is derived from
# it AFTER the lanes exist.
# ─────────────────────────────────────────────────────────────────────

def lane_gap_m(span, n_side: int, tuners) -> float:
    """Return the uniform lane step (m) for one side.

    WIDTH-AUTHORITY RESTORED (2026-09-13).  The 3-point width tuners (via
    ``governed_width_m`` → ``side_widths``) own the SPAN of a side; the
    count owns HOW MANY defenders share it.  The step is therefore
    DERIVED from the span, not tuned directly:

        span = side_width * 0.5 - RUCK_ADJACENT_ANCHOR_M
        gap  = span / (n_side - 1)

    so the outermost lane lands exactly on the side's width anchor and the
    interior lanes are evenly spaced outward from the immutable A pivot
    (``ruck_adjacent_anchor``, ruck_y ± 2.0 m).

    GOVERNED WIDTH = AUTHORITATIVE TOTAL SPAN (2026-09-14): the derived step
    is NOT clamped.  ``min_defender_gap_m`` (FLOOR) and
    ``max_defender_gap_m`` (CEILING) are REMOVED because both are span
    REDUCERS:

      * the FLOOR pinned the step ABOVE ``span/(n_side-1)``, so the outer
        lane overran the span, hit the sideline clamp and was re-spaced
        INWARD — the ladder stopped spanning the published width;
      * the CEILING silently re-narrowed a wide span the same way.

    The count authority still bounds HOW MANY defenders a side may hold
    (the width-feasibility bound in ``decide_side_counts``), but that bound
    governs MULTIPLICITY, never span.  Removing both reducers makes
    ``lane[n_side-1] == ruck_y +/- span / 2`` exact.

    SPAN MANDATORY (Stage 2, 2026-09-13): *span* is the side's full span in
    metres (or a ``SpanContract``).  There is NO widthless fallback: a call
    with no span raises ``SpanContractError`` from ``require_span`` rather
    than silently reconstructing the step from ``defender_gap_m`` or from the
    defender count.  Widthless lane generation is not a supported
    architecture.

    The flat ``tuners.defender_gap_m`` ladder survives ONLY in the
    explicitly-named ``side_lanes_widthless`` helper, which test/verifier
    stubs must call by name — production never reaches it.
    """
    side_span = require_span(span, caller="lane_gap_m")
    if n_side is None or int(n_side) < 2:
        # Degenerate count: a single-defender side has no interior step.
        # This is NOT a width fallback - the span was still required above,
        # so a widthless call still fails fast.
        return 0.0
    half = abs(float(side_span)) * 0.5
    usable = half - RUCK_ADJACENT_ANCHOR_M
    if usable <= 0.0:
        # Degenerate span: the whole side fits inside the ruck-adjacent
        # anchor band, so every lane collapses onto the immutable A pivot.
        # The span still owns the ladder — there is simply no interior step.
        return 0.0
    # ── GOVERNED WIDTH = AUTHORITATIVE TOTAL SPAN (2026-09-14) ──
    # The step is the PURE span-derived value.  ``min_defender_gap_m``
    # (FLOOR) and ``max_defender_gap_m`` (CEILING) are NO LONGER applied:
    #   * the FLOOR pinned the step above ``span/(n_side-1)``, so the outer
    #     lane overran the span and was re-spaced INWARD by the sideline
    #     clamp — the ladder stopped spanning the published width
    #     (LANE_STEP_FLOOR);
    #   * the CEILING silently re-narrowed a wide span the same way.
    # Both are span REDUCERS; neither may make the realised lane span
    # smaller than the governed width.  Removing them makes
    # ``lane[n_side-1] == ruck_y +/- span / 2`` exact.
    return usable / float(int(n_side) - 1)


def side_lanes(n_side: int, ruck_y: float, side: int, tuners,
               span) -> list:
    """Return EXACTLY *n_side* even Y lanes on *side* of the ruck.

    lane[i] (i = 0 innermost .. n_side-1 outermost):
        ruck_y + side * (RUCK_ADJACENT_ANCHOR_M + i * gap)
    where gap = lane_gap_m(side_width, n_side, tuners) — the WIDTH-derived
    uniform step when a side width is supplied, else the flat
    ``tuners.defender_gap_m`` fallback.  lane[0] is EXACTLY
    ruck_adjacent_anchor(ruck_y, side)
    (the ruck-adjacent A pivot, ANCHOR LAW); lane[n_side-1] is the
    outermost WINGER lane.

    Returns [] for n_side <= 0.

    SPAN MANDATORY (Stage 2, 2026-09-13): *span* is the side's full span in
    metres, or a ``SpanContract`` (the side's own half is then selected via
    ``SpanContract.span_m(side)``).  The parameter has NO default: omitting
    it is a ``TypeError`` at the call, and passing ``None`` raises
    ``SpanContractError`` from ``lane_gap_m``.  Every production caller must
    therefore supply an explicit span contract.

    ── NO LANE COLLAPSE (2026-09-12) ─────────────────────────────────
    The count authority is supposed to guarantee (n_side-1)*gap fits the
    room, but the LIVE per-side count can exceed the room the split
    allotted (a deep phase, a ruck that drifted toward a touchline, a
    side list that grew as hold/branch state changed).  The pre-fix body
    clamped EACH lane independently through ``clamp_to_sideline``, so
    every lane that overran the sideline collapsed onto the SAME
    boundary value: two or more defenders were handed an IDENTICAL Y.

    That is the short-side overlap/duplication — two bodies on one lane,
    and the outer pair compressed to a 0.0 m gap.  ``compute_targets``
    then clamped that already-degenerate value twice more (its own
    ``clamp_to_sideline`` and the governor band), so nothing downstream
    could recover the lost separation.

    The ladder is now clamped AS A UNIT:

      * if the uniform ladder fits, it is returned unchanged (byte-
        identical to the pre-fix result for every in-feasible case);
      * if the OUTERMOST lane overruns, only that endpoint is clamped and
        the interior lanes are re-spaced EVENLY from the immutable A
        pivot to the clamped WINGER.

    Invariants preserved: lane[0] is EXACTLY the ANCHOR LAW pivot
    (``ruck_adjacent_anchor``, never moved), lane[n_side-1] is the
    sideline-clamped WINGER, the ladder is monotone, and every lane is
    DISTINCT whenever the feasible span is non-zero.  The lane COUNT is
    never reduced — the count authority stays the boss.
    """
    if n_side <= 0:
        return []
    a_y = ruck_adjacent_anchor(ruck_y, side)
    if n_side == 1:
        return [clamp_to_sideline(a_y, tuners)]
    gap = lane_gap_m(_resolve_side_span(span, side), n_side, tuners)

    raw = [
        float(ruck_y) + int(side) * (RUCK_ADJACENT_ANCHOR_M + i * gap)
        for i in range(n_side)
    ]

    outer_raw = raw[-1]
    outer_clamped = clamp_to_sideline(outer_raw, tuners)
    if abs(float(outer_clamped) - float(outer_raw)) < 1e-9:
        # In-feasible: the uniform ladder already fits the field.  Every
        # lane is inside the margins, so the per-lane clamp is a no-op
        # (kept as the physical belt-and-suspenders it is documented to
        # be).
        return [clamp_to_sideline(y, tuners) for y in raw]

    # Overrun: re-space evenly between the FIXED inner anchor and the
    # clamped outer lane.  Every result lies on the closed interval
    # between two in-bounds endpoints, so no further clamp is applied —
    # clamping here is exactly what merged lanes in the first place.
    span = float(outer_clamped) - float(a_y)
    denom = float(n_side - 1)
    return [
        float(a_y) + span * (float(i) / denom)
        for i in range(n_side)
    ]


def _resolve_side_span(span, side: int) -> float:
    """Return the full side span (m) from a SpanContract or a raw float.

    A ``SpanContract`` carries BOTH sides; ``side`` selects the half.  A raw
    float is already side-specific.  Validation (None / non-finite /
    non-positive) is the caller's ``lane_gap_m`` -> ``require_span`` gate, so
    this helper only unwraps.
    """
    if hasattr(span, "span_m"):
        return span.span_m(side)
    return span


def side_lanes_widthless(n_side: int, ruck_y: float, side: int,
                         tuners) -> list:
    """LEGACY / TEST-ONLY: the flat ``defender_gap_m`` lane ladder.

    NOT a production entry point.  This is the pre-2026-09-13 widthless
    ladder (ruck-adjacent A pivot + i * ``tuners.defender_gap_m``) preserved
    ONLY so verifier stubs can still construct the historical geometry they
    assert against.  Production code must call ``side_lanes(..., span)``;
    ``verify_span_contract.py`` fails the build if any module under ``game/``
    reaches this function.

    Exists as a separate, explicitly-named symbol (never a default branch on
    ``side_lanes``) so that every widthless lane construction in the codebase
    is greppable and none can occur silently.
    """
    if n_side <= 0:
        return []
    gap = float(getattr(tuners, "defender_gap_m", 4.0))
    a_y = ruck_adjacent_anchor(ruck_y, side)
    if n_side == 1:
        return [clamp_to_sideline(a_y, tuners)]

    raw = [
        float(ruck_y) + int(side) * (RUCK_ADJACENT_ANCHOR_M + i * gap)
        for i in range(n_side)
    ]
    outer_raw = raw[-1]
    outer_clamped = clamp_to_sideline(outer_raw, tuners)
    if abs(float(outer_clamped) - float(outer_raw)) < 1e-9:
        return [clamp_to_sideline(y, tuners) for y in raw]
    stride = float(outer_clamped) - float(a_y)
    denom = float(n_side - 1)
    return [float(a_y) + stride * (float(i) / denom) for i in range(n_side)]


def required_side_counts(world, tuners, n_total: int,
                         ruck_y: float | None = None, *,
                         ball_side: int | None = None,
                         ball_side_bias: int = 0) -> tuple:
    """Return (n_left, n_right) dictated by ruck-geometry model.

    RUCK-GEOMETRY SPLIT ALLOCATION (2026-08-28):
    ─────────────────────────────────────────────
    Replaces the previous width-derived split with a pure ruck-geometry
    model.  The split is determined SOLELY by the distance from ruck_y
    to the nearest sideline:

      • 0–5m from touch → blind-side dead space (0-10 split)
      • 5–35m from touch → linear progression from (1-9) to (5-5)
      • Beyond 35m → maintain stable allocation

    This removes width compression artifacts, eliminates blind-side
    over-allocation, and removes winger/centre inversion in early tackles.

    Width governor and spacing tuners remain active but ONLY affect
    lateral spacing (role anchors, interior chain distribution), never
    the count-split itself.

    TASK X BALL-SIDE COLLAPSE (2026-08-26): when *ball_side* is LEFT/RIGHT
    and *ball_side_bias* > 0, up to that many extra defenders are shifted
    onto the ball side AFTER the ruck-geometry split — the donor side never
    drops below 1 defender (when feasible), so both bands stay defensible.
    """
    # COUNT AUTHORITY (2026-09-03): delegate to decide_side_counts.  The
    # count is the single boss; the old ruck-geometry body below is dead
    # code (kept only for historical reference / legacy verifier reading).
    return decide_side_counts(
        world, tuners, n_total, ruck_y=ruck_y,
        ball_side=ball_side, ball_side_bias=ball_side_bias,
    )
    # UNREACHABLE legacy body (retained for verifier greps):
    if n_total <= 0:
        return 0, 0
    if ruck_y is None:
        ruck_y = getattr(world, "ruck_y_m", 35.0)
    
    # ── RUCK-GEOMETRY SPLIT ──
    # Compute distance from ruck to nearest sideline (margin-protected).
    margin = float(getattr(tuners, "sideline_margin_m", 3.0))
    field_max = float(getattr(tuners, "field_width_m", 70.0)) - margin
    
    # Distance from ruck_y to the nearest usable sideline
    dist_to_left = max(0.0, float(ruck_y) - margin)
    dist_to_right = max(0.0, field_max - float(ruck_y))
    dist_to_short_side = min(dist_to_left, dist_to_right)
    
    # Determine which side is short-side (closer to touch) — SINGLE
    # authority resolve_short_side so the count split and side_widths can
    # NEVER disagree after a ball-side flip.
    is_left_short = (resolve_short_side(world, tuners, ruck_y=ruck_y) == LEFT)
    
    # Compute the split count for the short side
    n_short = _compute_ruck_geometry_split(n_total, dist_to_short_side)
    n_open = n_total - n_short
    
    # Assign to left/right based on which side is short
    n_left = n_short if is_left_short else n_open
    n_right = n_open if is_left_short else n_short

    # ── KICKOFF-CATCH WINDOW BIAS (2026-08-30) ──
    # Before the ruck forms the line is reforming off the kickoff.  Hold
    # the kickoff 6/5 split using kickoff_side_sign so the defence keeps
    # its kickoff-side geometry instead of collapsing across the field
    # onto the ruck-adjacent A-pivot.  Clamped: never 0/N, never negative.
    if kickoff_window_active(world):
        kick_side = 1 if world.kickoff_side_sign >= 0 else -1
        n_kick = min(6, max(1, n_total - 1))
        n_short = n_total - n_kick
        n_left, n_right = (n_short, n_kick) if kick_side == 1 else (n_kick, n_short)

    # ── BALL-SIDE COLLAPSE BIAS (Task X) ──
    # Deterministic post-split shift: each unit of bias moves one defender
    # from the open side to the ball side while the donor keeps >= 1.
    bias = max(0, int(ball_side_bias))
    if ball_side == RIGHT and bias > 0:
        for _ in range(bias):
            if n_left - 1 >= 1:
                n_left -= 1
                n_right += 1
    elif ball_side == LEFT and bias > 0:
        for _ in range(bias):
            if n_right - 1 >= 1:
                n_right -= 1
                n_left += 1

    # ── SPLIT-SIDE GUARANTEE (2026-08-30) ──
    # The spacing hinge is proximity-based over SPACING-ELIGIBLE defenders
    # ONLY (never tackle-state bodies).  Therefore every side must hold at
    # least ONE eligible defender so it has a valid hinge candidate before
    # spacing is applied.  This is the LAST word in the count computation:
    # it overrides the blind-side dead-space n_short=0 case (and any other
    # 0-side output of the ruck-geometry / kickoff-window / ball-side biases)
    # whenever the eligible pool is >= 2.  The n_total == 1 case is left
    # unchanged — a single eligible cannot populate both sides.
    if n_total >= 2:
        if n_left == 0:
            n_left = 1
            n_right = n_total - 1
        elif n_right == 0:
            n_right = 1
            n_left = n_total - 1

    return n_left, n_right


def assign_split_sides(world, defenders, tuners,
                       ruck_y: float | None = None, *,
                       ball_side: int | None = None,
                       ball_side_bias: int = 0) -> dict:
    """Assign depth_side to every defender — counts first, nearest-first.

    The required counts come from ``required_side_counts`` (ruck position
    — distance to the nearest sideline).  Assignment is NEAREST-FIRST to
    each side's usable band centre and MAY CROSS THE RUCK, so every side
    receives EXACTLY
    the required number of defenders with the smallest possible total
    movement:

        prefer_left = |d.y - left_band_centre| - |d.y - right_band_centre|

    sorted ascending (most left-preferring first); the first n_left go
    LEFT, the rest RIGHT.  ``player_id`` breaks ties deterministically.
    Because both band centres are ordered (left < right), prefer_left is
    monotone in d.y, so the greedy first-n is the optimal assignment.

    Stamps ``d.depth_side`` (LEFT/RIGHT) in place on every defender in
    *defenders* and returns {id(d): side}.
    """
    # ── STAGE 5 SINGLE POPULATION AUTHORITY (2026-09-13) ──
    # The side split is no longer solved here.  population_authority owns the
    # ONE line population and publishes its count + side split ONCE per frame;
    # this function is now a thin consumer that returns the published side map.
    # The legacy body below is UNREACHABLE (retained for verifier greps), the
    # same pattern required_side_counts uses.
    from game.defence.defence_lvl_1.population_authority import (
        publish_line_population as _publish_line_population,
    )
    _pub = _publish_line_population(
        world, list(defenders), tuners, ruck_y=ruck_y,
    )
    return dict(_pub["side_map"])

    # UNREACHABLE legacy body (retained for verifier greps):
    defenders = [d for d in defenders if d is not None]
    n = len(defenders)
    if n == 0:
        return {}
    if ruck_y is None:
        ruck_y = getattr(world, "ruck_y_m", 35.0)
    n_left, n_right = required_side_counts(
        world, tuners, n, ruck_y=ruck_y,
        ball_side=ball_side, ball_side_bias=ball_side_bias,
    )

    # ---- PHYSICAL-SIDE CLAMP: TESTED AND REJECTED (2026-09-12) ----
    # A canary was added here (tuners.tes_side_ownership_clamp_enabled) that
    # clamped the dictated counts to the bodies PHYSICALLY on each side, on
    # the theory that ``depth_side != sign(y - ruck_y)`` is a side-ownership
    # corruption.  The A/B harness (probe_tes_side_clamp_proof.py) showed it
    # makes the measurement WORSE, and reading this function's own contract
    # explains why on two counts:
    #
    #   1. ``depth_side != sign(y - ruck_y)`` is DELIBERATE, not corrupt.
    #      Assignment below is by nearest BAND CENTRE and the docstring
    #      states defenders ARE ALLOWED TO CROSS THE RUCK ("this is the
    #      SINGLE authority for depth_side").  The metric used to call it a
    #      defect was measuring an intentional policy.
    #   2. The band centres are DERIVED from n_left/n_right
    #      (derive_side_width_m -> side_band_centre), so clamping the counts
    #      changes the SORT KEY itself and reshuffles extra bodies across the
    #      ruck -- the opposite of the intent.
    #
    # Measured (probe_tes_side_clamp_proof.py, identical bootstrap):
    #     legacy : 6 entry mismatches / 8 chambers, 1128 frame mismatches
    #     clamped: 10 entry mismatches / 7 chambers, 980 frame mismatches
    # The legacy room-derived split is therefore retained unchanged.
    #
    # COUNT-LANE BAND CENTRES (Stage 3, 2026-09-03): the side band is the
    # midpoint between the ruck-adjacent A pivot and the outermost count-
    # driven lane (WINGER) — width is DERIVED from the lane model for each
    # side's decided count, never read from a width formula.  This keeps
    # the side assignment (nearest-first) consistent with the count-boss
    # lane geometry.
    # BAND CENTRES FROM THE GOVERNED WIDTH (2026-09-13): the assignment band
    # is the interval between the ruck-adjacent A pivot and the side's
    # WINGER anchor, so its centre must be measured from the SAME per-side
    # width the lanes are built across (governed width × split factor,
    # room-capped).  Deriving it from the flat-gap ladder instead measured
    # the band against a width the line no longer uses, which biased the
    # nearest-first assignment.  ``left_width``/``right_width`` here are the
    # lane-span widths, keeping assignment and lane geometry consistent.
    left_width, right_width = side_widths(world, tuners, ruck_y=ruck_y)
    centre_left = side_band_centre(LEFT, ruck_y, left_width, tuners)
    centre_right = side_band_centre(RIGHT, ruck_y, right_width, tuners)

    # QUANTISATION (2026-08-21): defenders beyond BOTH band centres on
    # the same side have mathematically identical preference scores
    # (|d.y - left_centre| - |d.y - right_centre| is constant there).
    # Raw float arithmetic makes those equal scores differ by ~1 ULP in
    # frame-to-frame random order, defeating the player_id tie-break and
    # oscillating the cross-ruck assignment.  Rounding to 1mm forces
    # equal scores to tie so player_id resolves the assignment
    # deterministically (cross-ruck reassignment stays stable).
    ordered = sorted(
        defenders,
        key=lambda d: (
            round(
                abs(float(d.y) - centre_left) - abs(float(d.y) - centre_right),
                6,
            ),
            getattr(d, "player_id", 0),
        ),
    )
    result = {}
    for i, d in enumerate(ordered):
        # FULLBACK LOCK (2026-08-27): the active fullback's depth_side is
        # owned by fullback_role / apply_fullback_geometry (jersey-1 slot).
        # A line split writer must never overwrite it or the FB slot drifts
        # off its locked deep-field side.  This is a defensive guard — the FB
        # is normally excluded from the line `defenders` list, but if it is
        # ever passed in, it is skipped so its locked side survives intact.
        if getattr(d, "fullback_role_active", False):
            continue
        side = LEFT if i < n_left else RIGHT
        d.depth_side = side
        result[id(d)] = side
    return result


# ─────────────────────────────────────────────────────────────────────
# MAIN ENTRY — spacing is the geometry target generator (Task F)
# ─────────────────────────────────────────────────────────────────────

def compute_targets(world, defenders, tuners, governor=None) -> dict:
    """Return {id(d): target_y} from the split-design spacing chains.

    COUNT-SPLIT PARTITION (2026-08-21):
        The left/right defender split is the COUNT-DRIVEN SPLIT stamp:
        every defender joins the side ``assign_depth_roles`` assigned it
        (depth_side_of reads the persistent split-side stamp).  The
        required per-side counts come from the ruck position + governed
        width and are filled NEAREST-FIRST, so a defender MAY be
        reassigned across the ruck when the counts demand it.  Each side
        is ordered outside→inside (slot 0 = WINGER anchor, last slot =
        A anchor), and every defender on each side is evenly spaced
        between that side's A-lane Y (inner anchor) and clamped winger
        Y (outer anchor), regardless of usable width.

    Args:
        world: WorldState (or stub) providing ruck_y_m and the governor
            activation flags.
        defenders: Eligible defenders (depth_role/depth_side already
            assigned; assign_depth_roles runs before this in the pipeline).
        tuners: DefenceTuners instance.
        governor: Optional DefensiveLineGovernor.  When supplied AND
            ``tuners.governor_width_active`` AND
            ``world.governor_width_active`` are all True, every spacing Y
            is additionally clamped into the governed width band via
            ``governor.apply_width_clamp(y)`` (Task D).

    Only role-anchored width is used — no lane-index, no fallback, no
    scramble/containment/timing lanes.
    """
    ruck_y = getattr(world, "ruck_y_m", 35.0)

    # ── TASK H — ANCHOR AUTHORITY CONSUMPTION ──
    _anchor_authority_consumed_m: float = _anchor_m(world)
    del _anchor_authority_consumed_m

    governor_clamp = (
        governor is not None
        and bool(getattr(tuners, "governor_width_active", False))
        and bool(getattr(world, "governor_width_active", False))
    )

    # ── Collect eligible defenders (EXCLUSION gate — RELEASE/PTB safe) ──
    # The chain population is built by EXCLUSION (markers / dummy-half /
    # tacklers / chase bodies and
    # tackle-state holders never occupy a chain slot) — NOT by INCLUSION
    # (`depth_role in _SPACING_ROLES`).  The old inclusive gate dropped
    # any line defender whose `depth_role` was not yet stamped
    # mid-transition (RELEASE/PTB/TACKLE_DRIVE), leaving the per-side list
    # short and forcing spacing_target_y into the canonical 6-lane fallback
    # (_even_side_lane_y n_side None/<2), which maps a lone role-carrying
    # defender onto the sideline-clamped WINGER band (~66.4) and doubles it
    # with the true winger.  `depth_side` is stamped on every body, so a
    # complete side list is always derivable; only the tight-to-ruck /
    # engaged roles are dropped here.  Mirrors _world_side_defenders
    # (2026-08-28).
    # STAGE 5 (2026-09-13): the spacing population is the SINGLE canonical
    # line population — byte-identical to the count / side authority in
    # ``population_authority`` (excluded roles incl. DEF_CHASE, and EVERY
    # tackle-state holder).  The old body here kept a tackle-state CHAIN
    # body that the count authority had already dropped — exactly the
    # count-vs-spacing set divergence Stage 4 measured.
    from game.defence.defence_lvl_1.population_authority import (
        is_line_body as _is_line_body,
    )
    eligible = []
    fullback_actor = None
    for d in defenders:
        if getattr(d, "fullback_role_active", False):
            if fullback_actor is None:
                fullback_actor = d
            continue
        if not _is_line_body(d):
            continue
        eligible.append(d)

    if not eligible:
        targets = {}
        world.frame_pin_targets = {}
        if FULLBACK_EXCLUDED and fullback_actor is not None:
            fb_y = getattr(world, "fullback_default_y", None)
            if fb_y is not None:
                targets[id(fullback_actor)] = float(fb_y)
        return targets

    # ── COUNT-SPLIT PARTITION (2026-08-21) ──
    # Each defender joins the SIDE STAMPED BY THE COUNT-DRIVEN SPLIT
    # (depth_side_of reads the persistent split-side stamp assigned by
    # assign_depth_roles BEFORE role seeding).  The split is the single
    # side authority — the required counts are dictated by ruck position
    # + governed width and filled nearest-first, so a defender MAY be
    # reassigned across the ruck when the counts demand it.  This
    # replaces the pre-2026-08-21 physical partition (which could leave
    # a short side empty — "short-side defenders don't retreat" defect).
    #
    # Each side is ordered OUTSIDE→INSIDE via _side_inside_out (the
    # same ordering spacing_y / defensive_geometry use): slot 0 is the
    # outermost defender (closest to its touchline) and receives the
    # WINGER anchor, slot n-1 is the innermost (closest to the ruck)
    # and receives the A anchor.  On the RIGHT this is DESCENDING Y —
    # the previous ascending order mapped the INNERMOST right defender
    # to the winger slot, mirroring the right half of the line (defender
    # cross-over when reforming).
    right_defs = _side_inside_out(
        [d for d in eligible if depth_side_of(d, ruck_y) == RIGHT],
        RIGHT, ruck_y,
    )
    left_defs = _side_inside_out(
        [d for d in eligible if depth_side_of(d, ruck_y) == LEFT],
        LEFT, ruck_y,
    )

    # ── COUNT-LANE WINGER AUDIT (Stage 3, 2026-09-03) ──
    # The WINGER anchor values fed to the jitter audit are the OUTERMOST
    # count-driven lane (side_lanes(n_side)[-1]) — width is fully derived
    # from the lane model, never read from a width formula.  An empty side
    # uses the ruck-adjacent A anchor as a neutral audit placeholder.
    # Per-side lane-span widths for THIS frame (governed width x split
    # factor, room-capped).  Computed once and shared by the jitter audit and
    # the count-lane loop below so both read byte-identical geometry.
    _side_lw, _side_rw = side_widths(world, tuners, ruck_y=ruck_y)
    _side_width_by = {LEFT: _side_lw, RIGHT: _side_rw}

    def _audit_winger_y(_side_defs, _side):
        _n = len(_side_defs)
        if _n <= 0:
            return ruck_adjacent_anchor(ruck_y, _side)
        _ls = side_lanes(_n, ruck_y, _side, tuners,
                         span=_side_width_by.get(int(_side)))
        return _ls[-1] if _ls else ruck_adjacent_anchor(ruck_y, _side)
    winger_y_left = _audit_winger_y(left_defs, LEFT)
    winger_y_right = _audit_winger_y(right_defs, RIGHT)

    # ── HINGE ANCHOR PER SIDE (spec 2026-08-30) ──
    # The spacing hinge is the LIVE position of the defender physically
    # closest to the ruck on each side (min |y-ruck_y|), NOT the fixed
    # ruck_y ± 2.0 m A-role anchor.  HINGE SOURCE (2026-08-31): the hinge
    # is scanned from the FULL world.defending_team via _side_hinge_body —
    # NOT the spacing-eligible pool (_side_inside_out of left_defs /
    # right_defs).  The eligible pool excludes tackle_state_active bodies
    # (markers / ex-tacklers) and _SPACING_EXCLUDED_ROLES, so during
    # RELEASE/PTB the physically-nearest body is dropped and the hinge
    # falls to a farther eligible body or the fixed ruck_y ± 2.0 m anchor —
    # misplacing the ruck-adjacent defender and cascading spacing errors
    # outward (winger -> sideline, blown interior gap).  Fall back to the
    # canonical ruck_y ± 2.0 m anchor ONLY when _side_hinge_body finds no
    # body on the side (a genuinely empty side / verifier stub).
    _hinge_left = _side_hinge_body(world, ruck_y, LEFT, defenders)
    _hinge_right = _side_hinge_body(world, ruck_y, RIGHT, defenders)
    # FIXED ANCHOR (2026-09-02): the inner interpolation boundary is ALWAYS
    # the structural ruck_adjacent_anchor (ruck_y +/- 2.0), never a live-hinge
    # body's Y.  Mirrors _even_side_lane_y's FIXED ANCHOR law (2026-09-01) --
    # compute_targets was the one path still fanning from the clamped live
    # hinge, so during RELEASE/PTB a wrong hinge body (DEF_RETREAT_10 at a
    # drifting Y in the A-band, a ruck-straddling body, a marker/ex-tackler)
    # spread the side and blew the spacing out.  The tuner-driven role-anchor
    # width is the SINGLE source: A at ruck_y +/- 2.0, WINGER at the clamped
    # width anchor.
    a_y_left = ruck_adjacent_anchor(ruck_y, LEFT)
    a_y_right = ruck_adjacent_anchor(ruck_y, RIGHT)

    # ── FRAME-SIDE FLAG (side flag from the count-split partition) ──
    # Every defender that will participate in spacing / movement / retreat
    # receives a deterministic ``frame_side`` stamp (LEFT or RIGHT) from
    # the count-split partition above.  This MUST be written BEFORE the
    # spacing loop, governor clamp, band pull, and before any downstream
    # retreat / movement / marker / close writer consumes it — the flag
    # is the frame-authoritative side identity for every movement writer.
    for d in left_defs:
        d.frame_side = LEFT
    for d in right_defs:
        d.frame_side = RIGHT
    if fullback_actor is not None:
        # FULLBACK SIDE (2026-08-29): the fullback is NEVER part of the
        # defensive line — it must not contribute to side counts, side
        # splits, or interior role assignment.  Its frame_side uses the
        # SAME hysteresis-protected depth_side_of() helper as line
        # defenders (the persistent depth_side / dead-banded fallback),
        # never the raw live y < ruck_y sign.  This stops a tiny Y-jiggle
        # at the ruck line from flipping the fullback's side and
        # destabilising any side count / split that groups by frame_side.
        fullback_actor.frame_side = depth_side_of(fullback_actor, ruck_y)

    # ── Evenly space each side via COUNT-DRIVEN LANES (2026-09-03) ──
    # The count is the boss: side_lanes(n_side, ...) lays out EXACTLY
    # n_side even lanes from the ruck-adjacent A pivot outward at a
    # uniform gap.  Width is derived from the outermost lane AFTER the
    # lanes exist — never the other way around.  This removes the
    # "cram count into width" contradiction: a short-side count can never
    # exceed the room (decide_side_counts guarantees it), so lanes are
    # always physically feasible and never jam/overlap/jitter.
    #
    # side_lanes returns [innermost=A, ..., outermost=WINGER].
    # _side_inside_out orders the side outside→inside (slot0=WINGER),
    # so defender i (outside→inside) takes lanes[n_side-1-i].
    targets = {}
    # LANE LADDER PUBLICATION (2026-09-12): the per-side ladder this frame
    # ACTUALLY used, AFTER the governed-band unit fit.  Consumers that must
    # reason about the geometry law (verifiers, diagnostics) read this
    # rather than re-deriving it: a re-derivation that misses the governor
    # flags computes the RAW step and reports a legitimately narrowed line
    # as compression.
    # ── SINGLE GLOBAL EVEN LADDER — THREE-POINT WIDTH IS THE SOLE AUTHORITY ──
    # (2026-09-15) The governed width W is laid out as ONE uniform ladder
    # across the whole line with IDENTICAL gaps W/(n-1).  No left/right
    # split, no per-side count, no room redistribution, no band pull: no
    # downstream stage may make one gap differ from another.  Defenders are
    # placed on lanes in ascending live Y (natural left→right order).
    lane_ladders = {}
    _global_targets, _global_lanes = global_line_targets(
        world, ruck_y, tuners, defenders=eligible,
    )
    lane_ladders[LEFT] = [float(v) for v in _global_lanes]
    lane_ladders[RIGHT] = [float(v) for v in _global_lanes]
    for _d in eligible:
        if _has_chase_targets(_d):
            continue
        if id(_d) in _global_targets:
            targets[id(_d)] = clamp_to_sideline(
                float(_global_targets[id(_d)]), tuners
            )

    # LEGACY PER-SIDE LOOP — DISABLED (three-point width is the sole
    # authority; the even ladder above owns every line defender's Y).
    for side_defs, side in ():
        n_side = len(side_defs)
        if n_side == 0:
            continue
        lanes = side_lanes(
            n_side, ruck_y, side, tuners,
            span=_side_width_by.get(int(side)),
        )
        # GOVERNED-BAND UNIT FIT (2026-09-12): the count ladder may span
        # wider than the governed width band.  Fitting it per lane merged
        # every overrunning lane onto the band edge — two or three bodies
        # handed an IDENTICAL Y (short-side overlap / duplication) and a
        # 0.0 m outer gap, then ``apply_width_clamp`` below could not
        # recover the lost separation.  Fit the ladder AS A UNIT (the same
        # law ``side_lanes`` already applies to the sideline) BEFORE any
        # body takes a slot, so every slot stays DISTINCT, the count is
        # preserved, and the immutable A pivot never moves.
        # GOVERNED WIDTH IS THE AUTHORITATIVE TOTAL SPAN (2026-09-14): the
        # band is this side's OWN published half-span, never a symmetric
        # ``governed_width / 2``.  The published per-side spans already carry
        # the total span distributed across the two ruck-anchored halves
        # under the margin-only room bound, so the unit fit is a restatement
        # and can no longer re-narrow the far side of an off-centre ruck.
        if governor_clamp:
            lanes = _fit_lanes_to_band(
                lanes, ruck_y,
                float(_side_width_by.get(int(side)) or 0.0) * 0.5,
            )
        lane_step = (
            abs(float(lanes[-1]) - float(lanes[0])) / float(n_side - 1)
            if n_side >= 2 else 0.0
        )
        band_pull_cap = lane_step * BAND_PULL_STEP_FRACTION
        lane_ladders[int(side)] = [float(v) for v in lanes]
        if n_side == 1:
            # Single defender → the A pivot lane exactly.
            targets[id(side_defs[0])] = clamp_to_sideline(lanes[0], tuners)
            continue
        for i, d in enumerate(side_defs):
            if _has_chase_targets(d):
                continue
            ty = lanes[n_side - 1 - i]
            ty = clamp_to_sideline(ty, tuners)
            if governor_clamp:
                ty = governor.apply_width_clamp(ty, world)
            role = getattr(d, "depth_role", None)
            if role not in ("A", "WINGER"):
                ty = _band_spacing_pull(world, tuners, d, ty, band_pull_cap)
            targets[id(d)] = ty

    # ── NON-CHAIN DEFENDERS — ruck-adjacent pin to ruck_y ──
    # Every non-fullback defender that was NOT processed by the chain
    # loop above must still receive a spacing_target_y.  Markers, DH,
    # tacklers, DEF_RETREAT_10, DEF_CHASE, DEF_LINE_READY etc. are
    # pinned to ruck_y — they do NOT use interpolated chain spacing.
    # This eliminates the fallback-to-d.y collapse path.
    #
    # S3 EXCEPTION (2026-08-22): a defender carrying genuine chase
    # target fields (chase_target_x/y) is OWNED BY THE CHASE WRITER —
    # it receives NO spacing target here (Task H S3 contract).  MMDL
    # stamps are post-consumption records, not chase ownership, so
    # mmdl-carrying defenders still get the ruck_y pin.
    # STAGE 5 (2026-09-13): the ruck-Y PINS for non-chain bodies are
    # published SEPARATELY (``world.frame_pin_targets``), NOT folded into
    # ``frame_spacing_targets``.  Folding them in made the spacing dict a
    # strict superset of the line population (Stage 4's ``P_CHAIN``
    # divergence).  ``frame_spacing_y`` reads the pin dict for non-line
    # bodies, so their Y is unchanged — only the SPACING POPULATION now
    # equals the line population.
    pins = {}
    for d in defenders:
        if getattr(d, "fullback_role_active", False):
            continue
        if id(d) in targets:
            continue
        if _has_chase_targets(d):
            continue
        pins[id(d)] = float(ruck_y)
    world.frame_pin_targets = pins

    # ── Fullback exclusion (spec step 4) ──
    if FULLBACK_EXCLUDED and fullback_actor is not None:
        fb_x = getattr(world, "fullback_default_x", None)
        fb_y = getattr(world, "fullback_default_y", None)
        if fb_x is not None and fb_y is not None:
            targets[id(fullback_actor)] = float(fb_y)

    # ── JITTER AUDIT (diagnostic, env-gated) ──
    # Capture the per-frame spacing snapshot (target_y, y, n_side, hinge,
    # side list, and every state flag) so consecutive frames can be diffed
    # and each target_y jump attributed to the state / side-population change
    # that caused it (game/debug/jitter_audit.py).  NO-OP unless armed.
    if _jitter_audit_on(world):
        _jitter_record(
            world,
            getattr(world, "frame_count", None),
            defenders, targets,
            left_defs, right_defs,
            _hinge_left, _hinge_right,
            a_y_left, a_y_right,
            winger_y_left, winger_y_right,
            fullback_actor=fullback_actor,
        )

    world.frame_side_lanes = lane_ladders
    return targets
