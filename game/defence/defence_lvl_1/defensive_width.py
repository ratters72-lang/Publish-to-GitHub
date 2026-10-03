"""
defensive_width.py — SINGLE source of defensive-line width (2026-08-09).

PURPOSE
-------
Jon's split-design spec: width comes ONLY from role anchors + ruck_y.

    role_anchor_y = ruck_y_m + ROLE_WIDTH_OFFSETS[depth_role] * depth_side

where ``ROLE_WIDTH_OFFSETS`` is the canonical per-role width offset (a
fraction of the defensive line HALF-WIDTH = ``role_width_anchor[role] *
width_curve_factor[role]`` — NO hidden global multiplier, 2026-08-17),
and ``depth_side`` is the PERSISTENT role-locked side stored by
defensive_depth_roles — NEVER the live ``d.y < ruck_y`` sign.

Width must NOT come from:
    lane-index, fallback width, midfield width, positional ordering,
    pursuit lanes, scramble lanes, containment lanes, timing lanes.

Width MUST come from:
    ruck_y_m, depth_side, role_width_offset, width_curve, governor_width.

Every subsystem that positions a defender laterally (geometry, catch,
scramble, pursuit, containment, structured close, role spacing) delegates
to this module so there is exactly ONE width formula in the codebase.

DEPTH_SIDE LOCK (Task A completion)
-----------------------------------
``depth_side_of(d, ruck_y)`` returns the PERSISTENT depth_side stored on
the Player (``-1`` LEFT / ``+1`` RIGHT).  It falls back to the live sign
``-1 if d.y < ruck_y else 1`` ONLY for a defender that has never been
seeded by the depth-role system (defender missing the field).  Live sign
is never consulted for seeded defenders, so hovering across ruck_y can no
longer flip a defender's width anchor.

TASK C (2026-08-09) — structured-close width preservation
---------------------------------------------------------
``get_role_anchor_y()`` is the spec-named public entry point
(``canonical_width_module.get_role_anchor_y(player, world.ruck_y_m)``)
used by structured_close so its lateral width is PURE role-anchor —
never BC-relative, never blended, never fallback.  ``clamp_to_sideline``
is the public sideline clamp (spec ``clamp_to_sideline(final_width_y)``);
governor + sideline clamps remain tuner-gated at the call site.
"""

from player.player import Player
from game.defence.defence_lvl_1.defensive_tuners import (
    DefenceTuners,
    post_tes_settle_active,
)

# ── DRIVE-DEPTH PHASES (2026-09-06) ──────────────────────────────────
# The depth-role X base is the LIVE BALL CARRIER (bc.x + WIDTH_OFFSETS
# [depth_role]) during OPEN_PLAY and every TES tackle phase, so the line
# tracks the driven carrier.  After the drive (RELEASE / PTB / POST_RUCK)
# it reverts to the ruck anchor.  This is the SINGLE drive-phase law the
# depth-role stamping (defensive_depth_roles._depth_role_target_x) shares.
_DRIVE_DEPTH_PHASES = frozenset({
    "open_play", "tackle_init", "tackle_contact",
    "tackle_drive", "tackle_dominance",
})


def is_drive_phase(world) -> bool:
    """True during the drive: OPEN_PLAY with a live BC, or any TES phase.

    The drive is the window where ``role_depth_target_x`` is anchored to
    the LIVE ball carrier (``bc.x + WIDTH_OFFSETS[depth_role]``).  A live
    BC is required (kickoff in-flight / no carrier is never a drive).
    """
    bc = getattr(world, "ball_carrier", None)
    if bc is None:
        return False
    sub = getattr(world, "sub_state", None)
    sub_name = str(getattr(sub, "value", sub)).lower()
    return sub_name in _DRIVE_DEPTH_PHASES


# ─────────────────────────────────────────────────────────────────────
# DEPTH SIDE — persistent role-locked side
# ─────────────────────────────────────────────────────────────────────

# Unseeded-fallback side dead-band (2026-08-29).  Mirrors
# SIDE_SWAP_HYSTERESIS_M in defensive_depth_roles.py (1.2 m) so the
# LIVE-SIGN fallback in depth_side_of behaves identically to the
# hysteresis-protected side system: a defender within this band of
# ruck_y keeps its previous side (d.frame_side / d.depth_side memory),
# and only a genuine crossing beyond the dead-band flips it.  A tiny
# Y-jiggle at the ruck line can no longer re-classify a defender (e.g.
# a left-side A drifting a hair over ruck_y) onto the other side.
SIDE_DEAD_BAND_M: float = 1.2


def depth_side_of(d, ruck_y: float) -> int:
    """Return the persistent depth_side (-1 LEFT / +1 RIGHT) for *d*.

    Uses ``d.depth_side`` when the depth-role system has seeded the
    defender (the normal case in steady state).  Falls back to the live
    position sign ONLY when the defender has never been seeded (fresh
    Player before the first assign_depth_roles call) so bootstrap does
    not explode.

    The live ``d.y < ruck_y`` sign is NEVER used for seeded defenders —
    that is the side-lock contract (Task A).  For the unseeded fallback,
    a DEAD-BAND + hysteresis memory is applied via ``d.frame_side``: a
    defender within SIDE_DEAD_BAND_M of ruck_y keeps its previous side
    (the same hysteresis-protected flag line defenders receive), so a
    tiny Y-jiggle at the ruck line never flips the side (2026-08-29).
    """
    side = getattr(d, "depth_side", None)
    if side in (-1, 1):
        return int(side)
    # Unseeded hysteresis fallback (2026-08-29): never flip within the
    # dead-band; prefer the prior frame_side as the side memory.  Only a
    # genuine crossing beyond SIDE_DEAD_BAND_M resolves to the live sign.
    prev = getattr(d, "frame_side", None)
    if prev in (-1, 1):
        if abs(float(d.y) - float(ruck_y)) <= SIDE_DEAD_BAND_M:
            return int(prev)
    return -1 if d.y < ruck_y else 1


# ─────────────────────────────────────────────────────────────────────
# ROLE WIDTH OFFSETS — canonical per-role width fraction
# ─────────────────────────────────────────────────────────────────────

def role_width_offset(role: str, tuners: DefenceTuners) -> float:
    """Return the canonical per-role width offset (fraction of half-width).

    This is the spec's ``ROLE_WIDTH_OFFSETS[depth_role]`` — read directly
    from the canonical table exposed on DefenceTuners (see
    defensive_tuners.py).  The table is the product

        role_width_anchor[role] * width_curve_factor[role]

    with NO hidden global multiplier: the governed width is the PRIMARY
    width and WINGER sits at exactly 1.00 × half-width (2026-08-17).

    Because the tuner table is the single source of truth, tuning the
    lateral spread means editing ``role_width_anchor`` /
    ``ROLE_WIDTH_OFFSETS`` — never a duplicated formula here.
    """
    table = getattr(tuners, "ROLE_WIDTH_OFFSETS", None)
    if table:
        return float(table.get(role, table.get("D", 0.8)))
    # Fallback for tuner objects that predate the canonical table: derive
    # from the anchor × curve product (no 0.8 — the compression is gone).
    width_anchor = getattr(tuners, "role_width_anchor", None) or {}
    width_curve = getattr(tuners, "width_curve_factor", None) or {}
    anchor = width_anchor.get(role, width_anchor.get("D", 0.8))
    curve = width_curve.get(role, 1.0)
    return anchor * curve


# ─────────────────────────────────────────────────────────────────────
# ROLE ANCHOR Y — the single width formula
# ─────────────────────────────────────────────────────────────────────

def role_anchor_y(
    d,
    ruck_y: float,
    def_line_width_m: float,
    tuners: DefenceTuners,
) -> float:
    """Return the role-slot width anchor Y for *d* — VERIFIER-ONLY (Stage 3).

    NOTE (2026-09-03): this width-INPUT form is a VERIFIER-ONLY helper.
    The live pipeline consumes the count-derived width via
    ``role_anchor_y_side`` (and the count-lane spacing model); it does
    NOT pass a raw ``def_line_width_m`` into a role-anchor formula.  This
    function is retained for verifier greps/compat and scheduled for
    removal in Stage 7 (full width-code removal).  Prior semantics:

    Formula (spec Section 3):

        role_anchor_y = ruck_y + half_width
                        * role_width_offset[depth_role]
                        * depth_side

    where half_width = def_line_width_m * 0.5, depth_side = persistent
    side from depth_side_of(), and the product is clamped to the sideline
    margins so no defender can be placed off-field.

    The role-agnostic form is ``role_anchor_y_for(role, side, ...)`` —
    this per-defender variant reads the defender's persistent depth_role
    and depth_side itself.
    """
    role = getattr(d, "depth_role", "D") or "D"
    side = depth_side_of(d, ruck_y)
    return role_anchor_y_for(role, side, ruck_y, def_line_width_m, tuners)


def get_role_anchor_y(d, ruck_y: float, def_line_width_m: float = 40.0,
                      tuners: DefenceTuners | None = None) -> float:
    """Spec-named role-anchor width for *d* — VERIFIER-ONLY (Stage 3).

    NOTE (2026-09-03): verifier-only alias of ``role_anchor_y`` — the
    width-INPUT form.  The live pipeline uses ``role_anchor_y_side``
    (count-derived).  Retained for verifier greps; scheduled for removal
    in Stage 7.  Prior semantics:

    ``canonical_width_module.get_role_anchor_y(player, world.ruck_y_m)``
    per the structured-close contract.  This is the SAME single width
    formula as ``role_anchor_y()`` — a pure alias so structured_close
    reads width from the canonical role-anchor module under the spec
    name and nothing else.

    ``def_line_width_m`` defaults to the raw stored width (40.0) and
    ``tuners`` to a default tuner set so the two-argument spec call form
    works for callers without explicit width/governor context.  Callers
    with a governed width (structured_close, pursuit, force reaction)
    pass it explicitly.
    """
    if tuners is None:
        tuners = DefenceTuners()
    return role_anchor_y(d, ruck_y, def_line_width_m, tuners)


def role_anchor_y_for(
    role: str,
    side: int,
    ruck_y: float,
    def_line_width_m: float,
    tuners: DefenceTuners,
) -> float:
    """Role-anchor Y for an explicit (role, side) pair — VERIFIER-ONLY (Stage 3).

    NOTE (2026-09-03): this width-INPUT form is a VERIFIER-ONLY helper.
    The live pipeline derives width from the count-lane model and routes
    through ``role_anchor_y_side``; this form is retained for verifier
    greps and scheduled for removal in Stage 7 (full width-code removal).
    Prior semantics:

    Used by the depth-role bootstrap/anchors and verification so both
    systems provably share the SAME formula.
    """
    half_width = def_line_width_m * 0.5
    offset = role_width_offset(role, tuners)
    raw = ruck_y + side * half_width * offset
    return clamp_to_sideline(raw, tuners)


def role_anchor_y_side(d, ruck_y: float, world, tuners: DefenceTuners) -> float:
    """Role-anchor Y using the PER-SIDE width from ``side_widths()``.

    SINGLE WIDTH SOURCE (2026-09-03): the spacing chain
    (``defensive_split_spacing.side_widths``) applies the proportional
    short/open split AND the available-room cap, producing a per-side
    ``(left_width, right_width)`` pair that can differ — the short side is
    squeezed so its WINGER hugs the touchline with the buffer instead of
    sitting at the margin-clamped theoretical half-width.  This helper
    resolves the SAME per-side widths and feeds the side's width into the
    canonical ``role_anchor_y_for`` formula, so the role-anchor system and
    the spacing chain provably consume ONE short-side factor /
    available-room squeeze.

    The historical ``role_anchor_y``/``get_role_anchor_y`` take a SINGLE
    ``def_line_width_m`` applied to BOTH sides; any caller that passed the
    full governed width put the short-side WINGER at the bare margin (3.0)
    while the spacing chain put it at margin+buffer (6.0) — the two-
    authority short-side blowout.  This helper is the fix: every canonical
    anchor call site that has a ``world`` should route through it.

    ``side_widths`` is lazy-imported to avoid the module cycle
    (split_spacing already imports ``depth_side_of`` / ``role_width_offset``
    / ``clamp_to_sideline`` from this module at top level).
    """
    from game.defence.defence_lvl_1.defensive_split_spacing import (
        side_widths,
    )
    # COUNT-BOSS (2026-09-03): derive width from the COUNT authority when
    # the world carries a populated defending team.  The eligible count is
    # the non-fullback, non-excluded defender count (mirrors the line
    # filter); when absent (verifier stub) the wide fallback (proportional
    # split) is used so direct calls stay deterministic.
    _n_total = None
    _team = getattr(world, "defending_team", None)
    if _team:
        from game.defence.defence_lvl_1.defensive_depth_roles import (
            EXCLUDED_ROLES,
        )
        _n_total = sum(
            1 for o in _team if o is not None
            and getattr(o, "defender_role", None) not in EXCLUDED_ROLES
            and not getattr(o, "fullback_role_active", False)
        )
    left_width, right_width = side_widths(world, tuners, n_total=_n_total,
                                          ruck_y=ruck_y)
    side = depth_side_of(d, ruck_y)
    side_width = right_width if side == 1 else left_width
    role = getattr(d, "depth_role", "D") or "D"
    return role_anchor_y_for(role, side, ruck_y, side_width, tuners)


def clamp_to_sideline(y: float, tuners: DefenceTuners) -> float:
    """Clamp *y* inside the field sideline margins (public, Task C).

    Spec name ``clamp_to_sideline(final_width_y)`` — used by the
    structured_close width pipeline when
    ``tuners.structured_close_clamp_active`` is True.
    """
    margin = float(getattr(tuners, "sideline_margin_m", 3.0))
    field_max = float(getattr(tuners, "field_width_m", 70.0)) - margin
    return max(margin, min(field_max, y))


def _clamp_sideline(y: float, tuners: DefenceTuners) -> float:
    """Backward-compatible alias of ``clamp_to_sideline``."""
    return clamp_to_sideline(y, tuners)


# ─────────────────────────────────────────────────────────────────────
# GOVERNOR WIDTH — compression/expansion override (Task D)
# ─────────────────────────────────────────────────────────────────────

def governed_width_m(world, tuners: DefenceTuners) -> float:
    """Return the width the line should use this frame.

    When the governor's compression/expansion is active (Task D), the
    governed width OVERRIDES the raw ``world.def_line_width_m``.  When
    disabled (pursuit mode / no governor state), the raw width is used
    unchanged.

    ── THE WIDTH CURVE IS THE SOLE AUTHORITY (2026-09-14, Option B) ──
    There is NO tuner width band.  The former minimum-width FLOOR and the
    maximum-width CEILING tuners are REMOVED: they were a second,
    independent authority over the line's narrowest and widest state, and
    they silently outranked the 3-point width curve
    (``width_left_tryline_m`` / ``width_halfway_m`` /
    ``width_right_tryline_m``) — a tuner could request 20 m and the engine
    would realise 30 m with no signal anywhere in the chain.

    The governed width now EQUALS the commanded width for every
    ball-carrier X and every tuner triple, because the command is a convex
    combination of the three curve anchors and the only surviving bound is
    a physical geometry limit:

        governed == interpolated_defensive_width(tuners, bc.x)

    The ONE remaining bound is the PHYSICAL FIELD CAP
    (``field_width_m - 2 * sideline_margin_m``).  That is a geometry
    limit, not a tuning rail: a WINGER at full half-width may not stand
    off the field.  Any curve inside the field therefore realises exactly.

    ``CLAMP_MIN`` is retained in the formation-width reducer ledger and is
    asserted at exactly 0.0 by ``verify_width_curve_sole_authority.py``,
    so any future edit that re-introduces a width floor trips the verifier
    immediately.
    """
    raw = float(getattr(world, "def_line_width_m", 40.0))

    # ── PHYSICAL FIELD CAP (geometry, NOT a tuner rail) ──
    # field_width_m - 2 * sideline_margin_m: a WINGER at full offset is
    # exactly half_width from a centre ruck, and the hard cap keeps the
    # outermost anchor inside the usable field.
    margin = float(getattr(tuners, "sideline_margin_m", 3.0))
    field_cap = float(getattr(tuners, "field_width_m", 70.0)) - 2.0 * margin

    def _clamp(value: float) -> float:
        """The ONE width clamp: the physical field cap, and nothing else."""
        return max(0.0, min(float(value), field_cap))

    governor_enabled = getattr(
        world, "governor_width_active", False
    )
    if not governor_enabled:
        # ── SINGLE CLAMP CONTRACT (Stage 6, 2026-09-13; floored-band
        #    removal 2026-09-14) ──
        # The governor-inactive path is clamped by the SAME bound as the
        # governor-active path — the physical field cap alone.  It
        # previously returned ``raw`` UN-clamped, which (a) contradicted
        # this function's own documented contract and (b) made the module
        # accessor and ``governor.governed_width_m()`` return DIFFERENT
        # widths for the same world whenever the governor was not driving
        # the frame — the GOVERNED_ACCESSOR_DIVERGENCE class Stage 6
        # removes.  Both accessors now agree by construction, because the
        # governor accessor delegates here.
        return _clamp(raw)

    line_width = getattr(world, "governor_line_width_m", None)
    if line_width is None:
        return _clamp(raw)

    return _clamp(float(line_width))


def resolve_widths(world, tuners: DefenceTuners) -> tuple:
    """Return (raw_def_line_width_m, effective_width_m).

    Convenience for callers that need both the raw stored width (for
    governor baseline) and the effective (governed) width.
    """
    raw = float(getattr(world, "def_line_width_m", 40.0))
    return raw, governed_width_m(world, tuners)


# ─────────────────────────────────────────────────────────────────────
# 3-POINT FIELD-POSITION WIDTH INTERPOLATION (2026-08-17)
#
# The defensive width is the winger-to-winger span, interpolated between
# three tuners by the BALL-CARRIER X:
#
#     tuners.width_left_tryline_m
#     tuners.width_halfway_m
#     tuners.width_right_tryline_m
#
#     if bc.x <= HALFWAY_X:
#         t = (bc.x - LEFT_TRYLINE_X) / (HALFWAY_X - LEFT_TRYLINE_X)
#         width = width_left_tryline_m * (1 - t) + width_halfway_m * t
#     else:
#         t = (bc.x - HALFWAY_X) / (RIGHT_TRYLINE_X - HALFWAY_X)
#         width = width_halfway_m * (1 - t) + width_right_tryline_m * t
#
# This is the SINGLE width source: every subsystem (geometry, spacing,
# retreat, unified retreat, PTB, TES) consumes it via world.def_line_width_m
# (the governor publishes the interpolated value there each OPEN_PLAY
# frame).  No other subsystem may redefine width.
#
# Attack runs RIGHT (+X), so LEFT_TRYLINE_X = own tryline = 10.0 and
# RIGHT_TRYLINE_X = opp tryline = 110.0 (matches tuners.field_length_m
# with trylines at 10 and 110).  bc_x is clamped into [10, 110] so a
# BC behind the tryline (kickoff/catch bootstrap) or past the line can
# never extrapolate the width outside the tuner band.
# ─────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────
# FOUR-ANCHOR COMPRESSION MODEL — field X positions of the anchors (metres)
# (2026-10-03)
#
# The defensive-line width is interpolated between FOUR field-position
# anchors instead of the original three:
#
#     ATTACKING_TRYLINE_X = 10    the tryline the ATTACK attacks
#     HALFWAY_X           = 60    halfway
#     DEFENDING_10M_X     = 100   the defending 10 m line
#     DEFENDING_TRYLINE_X = 110   the DEFENDING team's OWN tryline
#
# The engine's fixed directional rule is "the defence always retreats
# toward +X" (fullback_role / depth_boundary_guard), so the defending
# team's own tryline is the +X end of the field and its in-goal lies
# beyond x = 110.  The two anchors nearest that tryline — the tryline
# itself and the 10 m line — are the GOAL-LINE SQUEEZE anchors.
#
# THE 10 m ANCHOR IS A COMPRESSION, NOT A WIDTH (the design rule): there
# is exactly ONE goal-line width tuner — the DEFENDING TRYLINE WIDTH
# REGION (``defending_region_width_m``).  Both the defending tryline
# anchor and the defending 10 m anchor are independent COMPRESSIONS of
# that ONE region:
#
#     defending tryline width = region * defending_tryline_compression
#     defending 10 m width    = region * defending_10m_compression
#
# so the goal-line squeeze can be tuned independently at the tryline and
# at the 10 m line WITHOUT introducing a separate 10 m width anchor.
#
# Legacy aliases: LEFT_TRYLINE_X == ATTACKING_TRYLINE_X and
# RIGHT_TRYLINE_X == DEFENDING_TRYLINE_X, kept for the width verifiers
# and probes that import them.
# ─────────────────────────────────────────────────────────────────────
ATTACKING_TRYLINE_X: float = 10.0
HALFWAY_X: float = 60.0
DEFENDING_10M_X: float = 100.0
DEFENDING_TRYLINE_X: float = 110.0

# Backward-compatible aliases (same values — single source above).
LEFT_TRYLINE_X: float = ATTACKING_TRYLINE_X
RIGHT_TRYLINE_X: float = DEFENDING_TRYLINE_X


def _width_tuner(tuners, new_name: str, legacy_name: str, default: float) -> float:
    """Read a 3-point width tuner, falling back to its legacy alias.

    ``tuners.width_left_tryline_m`` (spec name) is authoritative; the
    legacy ``tuners.width_at_own_tryline_m`` alias is consumed only when
    the new name is missing (older stub tuner objects / verification
    namespaces).  The DefenceTuners singleton carries both, identical.
    """
    value = getattr(tuners, new_name, None)
    if value is None:
        value = getattr(tuners, legacy_name, None)
    if value is None:
        value = default
    return float(value)


# ─────────────────────────────────────────────────────────────────────
# ANCHOR WIDTH READERS — the four anchors' widths, SINGLE-SOURCED
# ─────────────────────────────────────────────────────────────────────
# Each reader resolves the LIVE tuner value for its anchor, so the whole
# curve reads ONE value per anchor.  The three WIDTH anchors (attacking
# tryline / halfway / defending tryline region) are plain tuners; the two
# GOAL-LINE anchors (defending tryline / defending 10 m) are COMPRESSIONS
# of the defending tryline width region (see the constant block above).

def attacking_tryline_anchor_width_m(tuners: DefenceTuners) -> float:
    """Width at the ATTACKING TRYLINE anchor (bc.x = 10)."""
    return _width_tuner(tuners, "width_left_tryline_m",
                        "width_at_own_tryline_m", 30.0)


def halfway_anchor_width_m(tuners: DefenceTuners) -> float:
    """Width at the HALFWAY anchor (bc.x = 60)."""
    return _width_tuner(tuners, "width_halfway_m",
                        "width_at_halfway_m", 45.0)


def defending_region_width_m(tuners: DefenceTuners) -> float:
    """THE DEFENDING TRYLINE WIDTH REGION — the ONE goal-line width anchor.

    Resolved from the spec name ``width_defending_tryline_m`` when the
    tuner object exposes it (``DefenceTuners`` exposes it as an alias of
    the legacy ``width_right_tryline_m``), else from the legacy name.
    Every goal-line width is a COMPRESSION of this single value.
    """
    value = getattr(tuners, "width_defending_tryline_m", None)
    if value is None:
        value = getattr(tuners, "width_right_tryline_m", None)
    if value is None:
        value = getattr(tuners, "width_at_opp_tryline_m", None)
    if value is None:
        value = 60.0
    return float(value)


# The fraction of the way from HALFWAY_X toward DEFENDING_TRYLINE_X at
# which the defending 10 m line sits: (100 - 60) / (110 - 60) = 0.8.
LEGACY_10M_FRACTION: float = (
    (DEFENDING_10M_X - HALFWAY_X) / (DEFENDING_TRYLINE_X - HALFWAY_X)
)


def defending_tryline_anchor_width_m(tuners: DefenceTuners) -> float:
    """Width at the DEFENDING TRYLINE anchor — the region, COMPRESSED.

    ``region * defending_tryline_compression`` (compression default 1.0,
    so the anchor IS the region width unless the goal-line squeeze is
    tuned).
    """
    region = defending_region_width_m(tuners)
    compression = getattr(tuners, "defending_tryline_compression", None)
    if compression is None:
        return region
    return region * float(compression)


def defending_10m_anchor_width_m(tuners: DefenceTuners) -> float:
    """Width at the DEFENDING 10 m anchor — the region, COMPRESSED.

    This is the DERIVED compression anchor: there is deliberately NO
    ``width_defending_10m_m`` tuner.  The 10 m width is the defending
    tryline width region scaled by ``defending_10m_compression``, so the
    tryline and the 10 m line share ONE width anchor while exposing
    INDEPENDENT goal-line squeeze knobs.

    When the tuner object carries no ``defending_10m_compression`` (an
    older tuner / a verifier stub) the anchor falls back to the LEGACY
    linear value at the 10 m line — ``halfway + 0.8 * (region - halfway)``
    — exactly the width the original three-anchor curve placed there, so
    the four-anchor model is a strict generalisation of the old curve.
    """
    region = defending_region_width_m(tuners)
    compression = getattr(tuners, "defending_10m_compression", None)
    if compression is None:
        mid = halfway_anchor_width_m(tuners)
        return mid + LEGACY_10M_FRACTION * (region - mid)
    return region * float(compression)


def interpolated_defensive_width(tuners: DefenceTuners, bc_x: float) -> float:
    """Return the winger-to-winger defensive width for *bc_x* (metres).

    FOUR-ANCHOR COMPRESSION MODEL (2026-10-03): the width is the exact
    piecewise-linear interpolation between the four field-position anchors

        bc.x at ATTACKING_TRYLINE_X (10)  -> attacking tryline width
        bc.x at HALFWAY_X           (60)  -> halfway width
        bc.x at DEFENDING_10M_X     (100) -> defending 10 m  COMPRESSION
        bc.x at DEFENDING_TRYLINE_X (110) -> defending tryline COMPRESSION

    where the two goal-line anchors are independent COMPRESSIONS of the
    ONE defending tryline width region (``defending_region_width_m``) —
    the defending 10 m line has NO separate width tuner.  Linear between
    the anchors; clamped flat outside [10, 110].  This is THE width the
    governor publishes to ``world.def_line_width_m`` every OPEN_PLAY
    frame, which geometry / spacing / retreat / unified retreat / PTB /
    TES all consume.  No fallback curve, no hidden multiplier — the
    anchors are the only source.
    """
    attack = attacking_tryline_anchor_width_m(tuners)
    mid = halfway_anchor_width_m(tuners)
    ten_m = defending_10m_anchor_width_m(tuners)
    tryline = defending_tryline_anchor_width_m(tuners)

    x = float(bc_x)
    if x < ATTACKING_TRYLINE_X:
        x = ATTACKING_TRYLINE_X
    elif x > DEFENDING_TRYLINE_X:
        x = DEFENDING_TRYLINE_X

    if x <= HALFWAY_X:
        t = (x - ATTACKING_TRYLINE_X) / (HALFWAY_X - ATTACKING_TRYLINE_X)
        return attack * (1.0 - t) + mid * t
    if x <= DEFENDING_10M_X:
        t = (x - HALFWAY_X) / (DEFENDING_10M_X - HALFWAY_X)
        return mid * (1.0 - t) + ten_m * t
    t = (x - DEFENDING_10M_X) / (DEFENDING_TRYLINE_X - DEFENDING_10M_X)
    return ten_m * (1.0 - t) + tryline * t


# ─────────────────────────────────────────────────────────────────────
# WIDTH LANE X — the single X-lane authority (2026-08-13)
# ─────────────────────────────────────────────────────────────────────

def lane_x_of(d, world, tuners=None):
    """Return the X-axis width lane for *d*, or None when it holds none.

    WIDTH LANE DEFINITION (spec): width lanes are the X-axis lane
    positions defined by ``WIDTH_OFFSETS``:

        lane_x = ruck_x + WIDTH_OFFSETS[depth_role]

    Spacing controls Y only — width is exclusively X-axis behaviour.

    A defender holds a width lane when ``width_authority.is_width_eligible``
    is True (PASSIVE line holder; PRIMARY/SECONDARY closers, markers, DH,
    TES tacklers, DEF_CHASE, the dynamic fullback and frozen defenders are
    exempt — their own writers own their X) AND its depth role has an
    offset in the canonical ``WIDTH_OFFSETS`` table.  A PRIMARY/SECONDARY
    defender under the Stage 2 width-eligibility grace remains eligible
    (grace-aware predicate).

    Every retreat / anchor / MMDL writer that would otherwise re-pin a
    lane holder's X to ``ruck_x + compliance`` or ``anchor_x`` consults
    this single source, so the lane X always survives as the final
    horizontal target and the line's X-depth spread cannot collapse.

    Returns ``float`` lane X or ``None``.
    """
    if d is None:
        return None
    if tuners is None:
        tuners = DefenceTuners()
    from game.defence.defence_lvl_1.width_authority import is_width_eligible
    if not is_width_eligible(d, world, tuners):
        return None
    # A defender holds a lane ONLY once the depth-role system has assigned
    # its depth_role.  An unseeded defender (depth_role None) has no lane —
    # the retreat/anchor writers keep their compliance/anchor pins for it.
    # NOTE: this deliberately differs from recompute_width_x's "D" fallback,
    # which operates on the already-role-assigned line set inside the step-7
    # pipeline.  lane_x_of is consulted by late writers on the raw SAME
    # defender list, so the explicit role gate matters here.
    role = getattr(d, "depth_role", None)
    if role is None:
        return None
    offsets = getattr(tuners, "WIDTH_OFFSETS", None) or {}
    if role not in offsets:
        return None
    ruck_x = getattr(world, "ruck_x_m", 60.0)
    return float(ruck_x) + float(offsets[role])


# ─────────────────────────────────────────────────────────────────────
# WIDTH RECOMPUTE — pure X-only line-hold targets (Task I, 2026-08-11)
# ─────────────────────────────────────────────────────────────────────

def recompute_width_x(
    world,
    defenders,
    tuners: DefenceTuners,
    governor_width: float | None = None,
    spacing_targets: dict | None = None,
) -> dict:
    """Return ``{id(d): target_x}`` — the pure X-only width recompute.

    This is the Task I width recompute source.  It EXTRACTS the X half of
    the structured OPEN_PLAY step-7 apply loop into a pure, guardable
    function:

        target_x = ruck_x + WIDTH_OFFSETS[depth_role]

    for every WIDTH-ELIGIBLE, line-holding defender (the exact formula
    ``defensive_geometry.compute_role_targets`` feeds step 7 today — the
    role-driven X depth that is NEVER BC-relative, NEVER lane-ordered,
    and NEVER compressed).  Defenders with no role-depth stamp fall back
    to ``d.x`` (hold current position), precisely the step-7 fallback
    when a geometry target is missing.

    CONTRACTS (Task I):
      I0 — X-ONLY: this function produces ONLY ``{id(d): target_x}``.
           It never writes ANY defender or world attribute, never emits
           Y / depth / anchor / chase / MMDL keys, and never touches a
           Y field.  Spacing is Y-only; width is X-only.
      I2 — DETERMINISM: a pure function of (world ruck, WIDTH_OFFSETS,
           depth_role, governor_width).  Identical inputs -> identical
           output, across any frame count (verified over 200 frames).
      I3 — ANCHOR COMPLIANCE: reads the anchor authority
           (``defensive_line_anchor_m``) as a consumer, NEVER recomputes
           / shifts / clamps / nudges the anchor, and never writes it.
      I4 — SPACING COMPLIANCE: ``spacing_targets`` (the Task H spacing
           output dict) is a READ-ONLY authority input — consumed for
           dependency ordering, never modified, never returned.
      I5 — ROLE EXEMPTIONS: MARKER_A / MARKER_B / DUMMY_HALF / TACKLER_*
           / DEF_CHASE / FULLBACK / PRIMARY / SECONDARY / frozen
           defenders receive NO width target (their own writers own
           their X).

    ``governor_width`` is the governed width (Task D) the line uses this
    frame — consumed for the dependency chain (Width Governor -> spacing
    -> width recompute).  When omitted it falls back to
    ``governed_width_m(world, tuners)`` so the recompute stays a single
    source of truth even without an explicit governor feed.
    """
    # I3 — anchor authority CONSUMED (ruck_x + 10), never written.
    from game.defence.defence_lvl_1.anchor_authority import (
        defensive_line_anchor_m,
    )
    _anchor_consumed_m: float = defensive_line_anchor_m(world)

    # Task D governed-width consumption (dependency chain feed).
    if governor_width is None:
        governor_width = governed_width_m(world, tuners)

    # I4 — spacing outputs consumed as read-only authority inputs.
    _spacing_consumed: dict | None = spacing_targets

    # I5 — the SAME eligibility predicate spacing uses (single authority).
    from game.defence.defence_lvl_1.width_authority import is_width_eligible

    ruck_x = float(getattr(world, "ruck_x_m", 60.0))
    offsets = getattr(tuners, "WIDTH_OFFSETS", None) or {}

    targets: dict = {}
    for d in defenders:
        if d is None:
            continue
        if not is_width_eligible(d, world, tuners):
            continue
        # ── POST-TES DRIVE-END SETTLE WINDOW (2026-09-06; phase-aware 2026-09-07) ──
        # Hold the current position X (drive-era depth) so no width snap
        # lands the instant the drive ends; the bodies settle (glide via
        # the movement integrator) before retreat/reform re-pins them to
        # the new ruck width lane.  The window spans DOMINANCE→RELEASE
        # through PTB_* and POST_RUCK_OPEN_PLAY, disengaging on the first
        # OPEN_PLAY frame where the width recompute (ruck_x +
        # WIDTH_OFFSETS[depth_role]) resumes.
        if post_tes_settle_active(world):
            targets[id(d)] = float(getattr(d, "x", ruck_x))
            continue
        role = getattr(d, "depth_role", "D") or "D"
        if role in offsets:
            targets[id(d)] = ruck_x + float(offsets[role])
        else:
            # Missing depth-role stamp -> step-7 hold fallback.
            targets[id(d)] = float(getattr(d, "x", ruck_x))

    return targets
