"""
defensive_line_shape_tuners.py — SINGLE FILE FOR LINE WIDTH & COMPRESSION
=========================================================================

THE ONE PLACE TO TUNE THE DEFENSIVE LINE'S SHAPE
-------------------------------------------------
Every field-width, lateral-span and compression control for the defensive
line lives HERE and nowhere else.  Any future adjustment to how wide the
line stands, how it squeezes at the goal line, or how it compresses as a
carrier closes is made by editing this file alone — no code tracing
across ``defensive_tuners`` / ``defensive_width`` / ``defensive_line_governor``
/ ``defensive_geometry`` / ``formation_width_authority`` is required.

HOW IT WIRES IN (AUTHORITY CHAINS PRESERVED — NOTHING REROUTED)
---------------------------------------------------------------
This module defines :class:`DefensiveLineShapeTuners`, a base class whose
``__init__`` sets every line-shape attribute.  ``DefenceTuners``
(``defensive_tuners.py``) simply INHERITS it:

    class DefenceTuners(DefensiveLineShapeTuners):
        def __init__(self):
            super().__init__()      # line-shape tuners set here
            ...                      # every other defence tuner

Because the attributes are still plain instance attributes on the
``DefenceTuners`` singleton, every existing consumer reads them exactly
as before — the authority chains are untouched:

    * ``defensive_width.interpolated_defensive_width(tuners, bc.x)``
      still reads ``width_left_tryline_m`` / ``width_halfway_m`` /
      ``width_right_tryline_m`` and the two goal-line compression knobs.
    * ``DefensiveLineGovernor.compute_line_state()`` still publishes the
      interpolated width to ``world.def_line_width_m`` /
      ``world.governor_line_width_m`` and reads ``compression_trigger_m``.
    * ``defensive_geometry`` / ``formation_width_authority`` /
      ``defensive_split_spacing`` still consume ``ROLE_WIDTH_OFFSETS`` /
      ``role_width_anchor`` / ``width_curve_factor`` / ``field_width_m`` /
      ``sideline_margin_m``.

MOVE-ONLY CONTRACT
------------------
This is a pure RELOCATION.  Every value is byte-identical to the value
that previously lived in ``defensive_tuners.py``; the ORDER of assignment
is preserved (goal-line compressions are assigned with the width curve,
before anything that derives from it).  Behaviour therefore remains
unchanged until a tuner value here is intentionally modified.

GOAL-LINE COMPRESSION NAMES
---------------------------
The two goal-line squeeze knobs are stored as
``defending_tryline_compression`` and ``defending_10m_compression`` (the
canonical names the shipped verifiers read).  They are ALSO exposed under
the descriptive aliases ``goal_line_compression_tryline`` and
``goal_line_compression_10m`` as bidirectional properties, so either name
reads and writes the SAME single value (mirroring the existing
``width_defending_tryline_m`` ↔ ``width_right_tryline_m`` alias pattern).
"""

from __future__ import annotations


# ─────────────────────────────────────────────────────────────────────
# REGISTRY — every line-shape tuner by name
# ─────────────────────────────────────────────────────────────────────
# A discoverable index of the tuners owned by this module.  Kept beside
# the definitions so a reader (or a diagnostic) can enumerate the whole
# line-shape surface without grepping.  ``verify_line_shape_tuners.py``
# asserts each listed name exists on the live DefenceTuners singleton.
LINE_SHAPE_TUNER_NAMES = (
    # Field geometry the width maths is built on.
    "field_width_m",
    "field_length_m",
    "sideline_margin_m",
    # 3-point width curve (the SOLE width authority).
    "width_left_tryline_m",
    "width_halfway_m",
    "width_right_tryline_m",
    # Legacy aliases of the 3-point curve (same values).
    "width_at_own_tryline_m",
    "width_at_halfway_m",
    "width_at_opp_tryline_m",
    # Goal-line compression (defending tryline region + two squeeze knobs).
    "width_defending_tryline_m",
    "defending_tryline_compression",
    "defending_10m_compression",
    "goal_line_compression_tryline",
    "goal_line_compression_10m",
    # Role width curve + ruck-adjacent spread.
    "compression_factor",
    "spacing_m",
    "width_curve_factor",
    "def_line_depth_m",
    "WIDTH_OFFSETS",
    "ROLE_DEPTH_M",
    "role_width_anchor",
    "ROLE_WIDTH_OFFSETS",
    # Governor width activation / structured-close width gates.
    "governor_width_enabled",
    "governor_width_active",
    "structured_close_width_blend",
    "structured_close_clamp_active",
    # Per-side split factors + short-side buffer.
    "tuner_short_side_factor",
    "tuner_open_side_factor",
    "short_side_buffer_m",
    # Lane-span rails (physical sanity, not tactical squeezes).
    "defender_gap_m",
    "max_defender_gap_m",
    "min_defender_gap_m",
    # Governor line width + compression ramp.
    "base_line_width_m",
    "min_line_width_m",
    "compression_trigger_m",
    "compression_smooth_rate",
    "line_width_max_delta_per_frame_m",
    # Step-up + line speed.
    "step_up_trigger_m",
    "step_up_buffer_m",
    "step_up_rate",
    "step_down_rate",
    "base_line_speed_mps",
    "line_speed_gain_mps",
    "speed_trigger_m",
    "max_lane_index",
    # Macro width governor.
    "DEF_LINE_TARGET_WIDTH_M",
    "DEFENDER_SPACING_M",
    "DEPTH_ROLE_LATERAL_OFFSET_M",
    "width_governor_alpha",
    "compression_curve_enabled",
    "compression_curve_strength",
    "macro_width_governor_enabled",
    # Engagement-band lateral spacing pull.
    "ENGAGEMENT_SPACING_Y_OFFSET",
    # Behavioural compression controls (chase band + compression timing).
    "compression_band_width_m",
    "compression_strength",
    "compression_activation_distance_m",
    "compression_duration_s",
    "compression_threat_window_m",
)


class DefensiveLineShapeTuners:
    """Every field-width and compression control for the defensive line.

    Mixed into :class:`DefenceTuners` (see ``defensive_tuners.py``).  This
    class owns ONLY line-shape tuners — speed ceilings, radii, tackle
    physics, TES drive tuners etc. remain on ``DefenceTuners`` itself.
    """

    def __init__(self):
        # ═══════════════════════════════════════════════════════════
        # FIELD DIMENSIONS (metres)
        # ═══════════════════════════════════════════════════════════
        self.field_width_m = 70.0       # sideline → sideline
        self.field_length_m = 100.0     # tryline (10) → tryline (110)

        # ─────────────────────────────────────────────────────────
        # SIDELINE MARGIN
        # Minimum distance from the sideline for the outermost
        # defenders (wingers).  NRL teams typically keep ~3m.
        # ─────────────────────────────────────────────────────────
        self.sideline_margin_m = 3.0

        # ─────────────────────────────────────────────────────────
        # STEP‑1 WIDTH CURVE (PRIMARY WIDTH)
        # Interpolated based on ball X position:
        #   ball at own tryline  → width_at_own_tryline_m
        #   ball at halfway      → width_at_halfway_m
        #   ball at opp tryline  → width_at_opp_tryline_m
        # ─────────────────────────────────────────────────────────
        #
        # 2026-08-17 — RESTORED & ENFORCED 3-POINT WIDTH TUNERS:
        #
        #     tuners.width_left_tryline_m
        #     tuners.width_halfway_m
        #     tuners.width_right_tryline_m
        #
        # are the EXACT spec names consumed by the field-position
        # interpolation (defensive_width.interpolated_defensive_width).
        # The legacy names below (width_at_own_tryline_m /
        # width_at_halfway_m / width_at_opp_tryline_m) are KEPT as
        # aliases — same values, same single source of truth.  No other
        # subsystem may redefine width; the interpolated value flows to
        # world.def_line_width_m (the governor) and from there to
        # geometry / spacing / retreat / unified retreat / PTB / TES.
        # ─────────────────────────────────────────────────────────
        self.width_left_tryline_m = 30.0
        self.width_halfway_m = 45.0
        self.width_right_tryline_m = 60.0

        # Legacy aliases (keep — same numbers, spec task 8).
        self.width_at_own_tryline_m = self.width_left_tryline_m
        self.width_at_halfway_m = self.width_halfway_m
        self.width_at_opp_tryline_m = self.width_right_tryline_m

        # -------------------------------------------------------------
        # FOUR-ANCHOR COMPRESSION MODEL (2026-10-03)
        #
        # The width curve is interpolated between FOUR field-position
        # anchors (see defensive_width.ATTACKING_TRYLINE_X / HALFWAY_X /
        # DEFENDING_10M_X / DEFENDING_TRYLINE_X):
        #
        #   attacking tryline (x=10)   -> width_left_tryline_m
        #   halfway            (x=60)  -> width_halfway_m
        #   defending 10 m     (x=100) -> REGION * defending_10m_compression
        #   defending tryline  (x=110) -> REGION * defending_tryline_compression
        #
        # THE DEFENDING TRYLINE WIDTH REGION -- the ONE goal-line width
        # anchor -- is ``width_defending_tryline_m`` (a property alias of
        # ``width_right_tryline_m`` above, so the two names can never
        # diverge).  The Defending Tryline and Defending 10 m anchors are
        # INDEPENDENT COMPRESSIONS of that single region:
        #
        #   defending tryline width = region * defending_tryline_compression
        #   defending 10 m width    = region * defending_10m_compression
        #
        # There is deliberately NO ``width_defending_10m_m`` tuner: the
        # 10 m anchor is a COMPRESSION of the defending tryline region, so
        # the goal-line squeeze has TWO independent knobs while the width
        # on that end still has only ONE anchor.
        #
        # defending_tryline_compression: 1.0 (neutral -- the tryline anchor
        #   is exactly the region width; lower it to squeeze the line at
        #   the defending tryline).
        # defending_10m_compression: 0.95 -- the value that reproduces the
        #   LEGACY three-anchor linear curve at the 10 m line for the
        #   default region width:
        #       legacy 10 m width = halfway + 0.8 * (region - halfway)
        #                         = 45 + 0.8 * (60 - 45) = 57 m
        #       57 / 60 = 0.95
        #   So shipping the four-anchor model with these defaults leaves
        #   the realised width identical to the old three-anchor curve;
        #   lower it to squeeze the line at the defending 10 m line.
        #
        # These two are ALSO exposed as ``goal_line_compression_tryline``
        # and ``goal_line_compression_10m`` (properties below) so the
        # goal-line squeeze can be reached under either name.
        # -------------------------------------------------------------
        self.defending_tryline_compression: float = 0.6
        self.defending_10m_compression: float = 0.8

        # ─────────────────────────────────────────────────────────
        # STEP‑1 COMPRESSION / SPACING / WIDTH CURVE
        # Used by DefensiveGeometry to compute lane_y targets.
        # ─────────────────────────────────────────────────────────
        # How strongly ball X position compresses (<1) or expands (>1)
        # the defensive line width.  0 = no effect, 0.5 = moderate.
        self.compression_factor: float = 0.3

        # Base spacing (metres) between adjacent defenders at neutral
        # compression (factor = 1.0).  Tighter = more compact line.
        self.spacing_m: float = 5.0

        # Per-depth-role multiplier on lane offset.
        #
        # 2026-08-17: neutralised to 1.0 for EVERY role.  The previous
        # values (A 0.8 → WINGER 1.2) were tuned to compensate for the
        # now-removed global * 0.8 width compression.  With that hidden
        # multiplier gone, keeping them would push WINGER to 1.2 ×
        # half-width (the measured line ~20% wider than
        # world.def_line_width_m) and pull D (1.1) so close to CENTRE
        # they nearly overlap.  With all factors = 1.0 the anchors alone
        # (role_width_anchor) define the spread, WINGER sits at exactly
        # half-width, and the measured line width equals the governed
        # width.  If visual tuning is ever needed, adjust
        # role_width_anchor / ROLE_WIDTH_OFFSETS — never a global
        # multiplier.
        self.width_curve_factor: dict[str, float] = {
            "A": 1.0,
            "B": 1.0,
            "C": 1.0,
            "D": 1.0,
            "CENTRE": 1.0,
            "WINGER": 1.0,
        }

        # ─────────────────────────────────────────────────────────
        # STEP‑1 BASELINE DEPTH
        # Distance behind the ruck the defensive line anchors.
        # Attack runs RIGHT → defenders retreat RIGHT → ruck_x + depth.
        # ─────────────────────────────────────────────────────────
        self.def_line_depth_m = 10.0

        # ─────────────────────────────────────────────────────────
        # STEP‑1 WIDTH OFFSETS — ROLE-DRIVEN X DEPTH (2026-08-09)
        #
        # DEFENSIVE GEOMETRY IS DRIVEN BY DEPTH ROLES ONLY.  Each depth
        # role maps to an X offset BEHIND the driving position:
        #
        #     target_x = bc.x + WIDTH_OFFSETS[depth_role]  (during the drive)
        #     target_x = ruck_x + WIDTH_OFFSETS[depth_role] (after the drive)
        #
        # The drive is OPEN_PLAY with a live ball carrier PLUS every TES
        # tackle phase (TACKLE_INIT / CONTACT / DRIVE / DOMINANCE), so the
        # defensive line tracks the driven carrier and holds its depth
        # offset behind the LIVE bc.x.  After the drive (RELEASE / PTB /
        # POST_RUCK) the base reverts to the ruck (ruck_x + 10 for the
        # deepest WINGER) so the line retreats the 10m.
        #
        # A/B sit tight to the carrier, C/D push deeper, CENTRE is the
        # interior anchor depth, WINGER is the deepest.  This table is
        # the SINGLE authority for role depth — no Y-ordering, no
        # lane-index ordering, no proportional-gain geometry anywhere.
        # ─────────────────────────────────────────────────────────
        self.WIDTH_OFFSETS: dict[str, float] = {
            "A": 2.0,
            "B": 4.0,
            "C": 5.0,
            "D": 7.0,
            "CENTRE": 8.0,
            "WINGER": 10.0,
        }

        # STEP‑2 ROLE_DEPTH_M mirrors WIDTH_OFFSETS — the depth-role
        # system is geometry-driven and forward-only, so the legacy
        # STEP‑2 offset table is an alias of the same numbers.
        self.ROLE_DEPTH_M: dict[str, float] = self.WIDTH_OFFSETS

        # ─────────────────────────────────────────────────────────
        # STEP‑1 ROLE WIDTH ANCHOR — ROLE-DRIVEN Y SPREAD (2026-08-09,
        # revised 2026-08-17; EVEN-LANE RECONCILIATION 2026-08-24)
        #
        # Each depth role owns a fixed fraction of the defensive
        # line's HALF-WIDTH (world.def_line_width_m / 2), measured
        # from the ruck Y.  The geometry Y target is:
        #
        #     target_y = ruck_y + side_sign * half_width
        #                        * role_width_anchor[role]
        #                        * width_curve_factor[role]
        #
        # where side_sign = -1 on the LEFT of the ruck, +1 on the
        # RIGHT (mirrored via depth_side).  Per-side role order from
        # ruck-closest to touchline is:
        #
        #     A → B → C → D (extra D if needed) → CENTRE → WINGER
        #
        # D is INTERIOR to CENTRE, CENTRE is interior to WINGER
        # (WINGER is the full half-width).  There is NO lane-index
        # ordering and NO Y-sort anywhere in the geometry pipeline.
        #
        # EVEN-LANE RECONCILIATION (2026-08-24): the previous table
        # (A 0.12 / B 0.34 / C 0.56 / D 0.72 / CENTRE 0.80) was NOT
        # evenly spaced, so the spacing chain's even per-side lane
        # distribution (verify_ruck_adjacent_spacing §3) and the
        # canonical role-anchor formula (verify_role_width_order §4)
        # encoded TWO DIFFERENT geometry models that could never both
        # hold.  The table below IS the even progression between the
        # ruck-adjacent A anchor and the WINGER half-width for a full
        # 6-role side:
        #
        #     A=0.10 B=0.28 C=0.46 D=0.64 CENTRE=0.82 WINGER=1.00
        #     → offsets (width 40): 2.0 / 5.6 / 9.2 / 12.8 / 16.4 / 20.0
        #     → uniform 3.6 m gaps, exactly the even-lane law.
        #
        # A = 0.10 × half-width = 2.0 m == ruck_adjacent_anchor()
        # (the ANCHOR LAW), so the canonical table, the even-lane
        # chain, and the movement-layer pin are ONE value.  Single
        # source of truth restored: role_anchor_y_for(role) ==
        # even-lane Y == chain output for every role.
        # ─────────────────────────────────────────────────────────
        self.role_width_anchor: dict[str, float] = {
            "A": 0.10,
            "B": 0.28,
            "C": 0.46,
            "D": 0.64,
            "CENTRE": 0.82,
            "WINGER": 1.00,
        }

        # ─────────────────────────────────────────────────────────
        # ROLE WIDTH OFFSETS — CANONICAL WIDTH FRACTION (2026-08-09,
        # revised 2026-08-17)
        #
        # Spec Section 3: role_anchor_y = ruck_y + ROLE_WIDTH_OFFSETS[
        # depth_role] * depth_side.  ROLE_WIDTH_OFFSETS is the combined
        # per-role fraction of the defensive line HALF-WIDTH — exactly
        # the product role_width_anchor[role] * width_curve_factor[
        # role].  The global * 0.8 width compression has been REMOVED
        # (2026-08-17): the governed width is the PRIMARY width and the
        # line must respect it without hidden multipliers, so WINGER
        # lands at exactly 1.00 × half-width and the measured line
        # width equals world.def_line_width_m / the governor width.
        #
        # Exposing it under the spec name lets every downstream width
        # consumer (geometry, catch, scramble, pursuit, containment,
        # structured close, spacing) read the same canonical table via
        # defensive_width.role_width_offset().
        # ─────────────────────────────────────────────────────────
        self.ROLE_WIDTH_OFFSETS: dict[str, float] = {
            role: anchor * self.width_curve_factor.get(role, 1.0)
            for role, anchor in self.role_width_anchor.items()
        }

        # ─────────────────────────────────────────────────────────
        # GOVERNOR WIDTH ACTIVATION (Task D, 2026-08-09)
        #
        # When True, DefensiveLineGovernor's compression/expansion
        # writes world.def_line_width_m every OPEN_PLAY frame so
        # geometry/spacing consume live governed width instead of the
        # raw static 40.0.  Set False to restore raw-width behaviour
        # (verification of the override contract).
        # ─────────────────────────────────────────────────────────
        self.governor_width_enabled: bool = True

        # ─────────────────────────────────────────────────────────
        # STRUCTURED CLOSE WIDTH (Task C, 2026-08-09)
        #
        # pure role-anchor width for structured_close.  The width
        # MUST come ONLY from the canonical role-anchor module
        # (defensive_width.get_role_anchor_y) — NEVER BC-relative,
        # never fallback, never lane-index, never positional, never
        # midfield, never blended with any other source.
        #
        # structured_close_width_blend is the MANDATORY override gate:
        # it MUST be 0.0.  While 0.0, the structured_close writer
        # computes final_width_y = role_anchor_y with NO blend
        # arithmetic at all (no chase_y, no bc.y component).  Any
        # non-zero value is a spec violation; the writer treats
        # everything > 0.0 identically to 0.0 (pure anchor), so this
        # tuner is a contract flag rather than a blend.
        # ─────────────────────────────────────────────────────────
        self.structured_close_width_blend: float = 0.0

        # ─────────────────────────────────────────────────────────
        # STRUCTURED CLOSE CLAMP ACTIVE (Task C, 2026-08-09)
        #
        # Sideline clamp gate for the structured_close width:
        #
        #     if tuners.structured_close_clamp_active:
        #         final_width_y = clamp_to_sideline(final_width_y)
        #
        # The role anchor is ALREADY sideline-clamped by
        # defensive_width, so this gate is idempotent by default;
        # it exists so the contract's clamp step is explicit and
        # tunable.
        # ─────────────────────────────────────────────────────────
        self.structured_close_clamp_active: bool = True

        # ─────────────────────────────────────────────────────────
        # SPLIT-DESIGN PROPORTIONAL SPLIT FACTORS (Task D, 2026-08-09)
        #
        # When world.short_side_width_m / world.open_side_width_m are NOT
        # set, the split-spacing module derives each side's width from the
        # governed width times these factors:
        #
        #     short_side_width_m = governed_width_m() * tuner_short_side_factor
        #     open_side_width_m  = governed_width_m() * tuner_open_side_factor
        #
        # NEUTRALISED (2026-09-14) — THE GOVERNED WIDTH IS THE AUTHORITATIVE
        # TOTAL SPAN.  The short-side 0.65 squeeze made the realised line span
        # 0.825 x the governed width (the measured under-responsiveness), so
        # the factors are no longer span reducers: both default to 1.0 and
        # ``formation_width_authority._solve_side_widths`` publishes the
        # governed width for BOTH sides, reduced ONLY by the absolute sideline
        # safety clamp.  The attributes are retained for compatibility
        # (harness stubs and ``_tuner_signature`` read them); a value above or
        # below 1.0 no longer narrows or widens a side.
        # ─────────────────────────────────────────────────────────
        self.tuner_short_side_factor: float = 1.0
        self.tuner_open_side_factor: float = 1.0
        # ─────────────────────────────────────────────────────────
        # SHORT-SIDE BUFFER (2026-08-31)
        #
        # The winger on the short side keeps this buffer from the usable
        # sideline edge (margin + buffer), so the short side narrows to
        # 2 * max(0, dist_from_ruck_to_usable_sideline - short_side_buffer_m)
        # when the ruck is near the touchline.  This is the "real league
        # geometry" short-side collapse: the winger hugs the touchline with
        # a buffer instead of sitting at the margin-clamped theoretical
        # half-width (which would spread the interior chain too wide).
        # ─────────────────────────────────────────────────────────
        self.short_side_buffer_m: float = 3.0

        # ─────────────────────────────────────────────────────────
        # LANE-SPAN TUNERS (2026-09-03; WIDTH-AUTHORITY RESTORED
        # 2026-09-13) — count and width SHARE the authority:
        #
        #   * the COUNT decides HOW MANY defenders are on a side
        #     (decide_side_counts, room- and width-feasibility derived);
        #   * the 3-POINT WIDTH TUNERS (width_left_tryline_m /
        #     width_halfway_m / width_right_tryline_m → the governed
        #     width) decide HOW WIDE that side spans.
        #
        # Lanes are built from the ruck-adjacent A pivot (ruck_y ± 2.0)
        # outward at a uniform step derived from the side's width:
        #
        #     span = side_width * 0.5 - RUCK_ADJACENT_ANCHOR_M
        #     gap  = span / (n_side - 1)
        #
        # so the outermost lane (WINGER) lands exactly on the side's width
        # anchor and the whole ladder is evenly spaced across the governed
        # band.  Width is therefore an INPUT to the lane step again — see
        # defensive_split_spacing.lane_gap_m().
        #
        # defender_gap_m:
        #   FALLBACK uniform gap (m) used ONLY when no side width is
        #   supplied (verifier stubs calling side_lanes directly, and the
        #   no-world bootstrap).  It is NOT the live authority — the live
        #   step comes from the governed width.  It also caps the derived
        #   step via max_defender_gap_m.
        #
        # max_defender_gap_m:
        #   PHYSICAL SANITY RAIL on the width-derived lane step — NOT a
        #   tactical squeeze.  The width tuners OWN the span, so this value
        #   must sit ABOVE every physically reachable step (the largest
        #   possible step is the full half-span of a 2-man side:
        #   field_width/2 - 2*margin - 2.0 = 30.0 m).  At 30.0 it can never
        #   bind, so a wide tuner setting with a small count still realises
        #   the width it commands.  A lower value would silently re-narrow
        #   the line — exactly the "extreme tuner changes are absorbed"
        #   defect this authority restoration removes.  Exposed as a tuner
        #   only so the rail stays explicit and auditable.
        self.defender_gap_m: float = 4.0
        self.max_defender_gap_m: float = 30.0
        self.min_defender_gap_m: float = 3.0
        #
        # min_defender_gap_m:
        #   Physical minimum gap.  Two roles: (a) the count authority uses
        #   it to guarantee feasibility — n_side ≤ floor((half_width -
        #   2.0)/min_gap)+1 — so a count can never demand more defenders
        #   than the governed width can space; (b) it floors the derived
        #   lane step so adjacent lanes can never merge/overlap.
        # ─────────────────────────────────────────────────────────

        # ─────────────────────────────────────────────────────────
        # GOVERNOR WIDTH ACTIVE ADVISORY TUNER (Task C, 2026-08-09)
        #
        # Structured-close gate for the governor's lateral clamp:
        #
        #     if tuners.governor_width_active:
        #         final_width_y = governor.apply_width_clamp(final_width_y)
        #
        # NOTE: `governor_width_active` also exists as a WORLD flag
        # (written by DefensiveLineGovernor each frame).  This tuner
        # is the separate structured-close ADVISORY gate controlling
        # whether the governor clamp is applied to the close width.
        # ─────────────────────────────────────────────────────────
        self.governor_width_active: bool = True

        # ═══════════════════════════════════════════════════════════
        # DEFENSIVE LINE GOVERNOR TUNERS
        # ═══════════════════════════════════════════════════════════
        #
        # These tuners drive the shared line state computed by
        # DefensiveLineGovernor.compute_line_state().
        #
        # base_line_width_m:
        #   Width of the line when compression = 0 (BC far away).
        # min_line_width_m:
        #   Width of the line when compression = 1 (BC close).
        # compression_trigger_m:
        #   BC-to-anchor distance below which compression activates.
        #   Full compression (factor=1) when bc_to_anchor = 0.
        # ═══════════════════════════════════════════════════════════

        # ── LINE WIDTH / COMPRESSION ──
        self.base_line_width_m: float = 40.0
        # RAISED 20.0 -> 28.0 (2026-08-02): the line still compresses as the
        # set advances, but never "squashes" down to 10m-per-side.  Combined
        # with the governor's per-frame width delta clamp (1.0m/frame) the
        # squeeze is gradual and the visible squash + lateral shimmy that
        # accompanied the old aggressive ramp is gone.
        self.min_line_width_m: float = 28.0
        self.compression_trigger_m: float = 10.0

        # ── SMOOTHED COMPRESSION RAMP (2026-08-02) ──
        # How fast the governor's compression_factor approaches the raw
        # BC-proximity ramp each frame (0.15 = ~7 frames to converge).
        # Previously the factor jumped straight to its raw value, so a BC
        # approaching the line compressed the width 40->20m over a handful
        # of frames with per-frame jumps.
        self.compression_smooth_rate: float = 0.15
        # Max per-frame change in line_width_m (keeps lateral spacing
        # continuous; physical speed only moves defenders ~0.15m/frame).
        self.line_width_max_delta_per_frame_m: float = 1.0

        # ── STEP-UP ──
        self.step_up_trigger_m: float = 12.0
        # RAISED 1.0 -> 2.5 (2026-08-04): the defensive-line engine spec
        # requires a 2–4m step-up buffer so the WHOLE line visibly steps
        # forward as the BC approaches.  1.0m read as a subtle glide; 2.5m
        # is inside the spec band and still safe because the governor's
        # onside clamp keeps line_depth behind ruck_x + 1.0.
        self.step_up_buffer_m: float = 2.5
        # STAGE 3F: step-up rates are per-second, not per-frame.  The old
        # per-frame additive values made the press-up ramp scale inversely
        # with tick rate (0.1/frame = 1.0 in 10 frames = 0.167s @60fps but
        # 0.333s @30fps), so line pacing inherited nondeterministic frame
        # drift.  These are the 60fps equivalents of the legacy 0.1 / 0.05.
        self.step_up_rate: float = 6.0      # per second (was 0.1/frame @60fps)
        self.step_down_rate: float = 3.0    # per second (was 0.05/frame @60fps)

        # ── LINE SPEED ──
        self.base_line_speed_mps: float = 4.0
        # FLATTENED RAMP (2026-08-04): gain was 2.0, which pinned the shared
        # line speed to the 8.3 ceiling whenever BC was within ~12.9m of the
        # line — the visible "suddenly too fast" snap before vs after catch.
        # At 0.4 the ramp spans the full 4.0→8.3 range across ~11m of BC
        # approach, so the line accelerates gradually and only reaches the
        # ceiling when the BC is right on the line (tackle imminent).
        self.line_speed_gain_mps: float = 0.4
        self.speed_trigger_m: float = 15.0
        self.max_lane_index: int = 3       # used for lane_step computation in governor

        # ═══════════════════════════════════════════════════════════
        # MACRO WIDTH GOVERNOR — DEFENSIVE LINE WIDTH & SPACING (2026-08-17)
        #
        # Macro-level defensive-line width target and spacing distribution
        # (spec §1).  This system operates ONLY at macro scale on the
        # lateral (Y) axis:
        #
        #   DEF_LINE_TARGET_WIDTH_M:
        #       The lateral width (metres) the defensive line smoothly
        #       converges to.  The governor compares the MEASURED line
        #       width (max(d.y) - min(d.y)) against this target and adds a
        #       slow lateral correction each OPEN_PLAY frame.
        #
        #   DEFENDER_SPACING_M:
        #       Uniform reference spacing (metres) between adjacent
        #       defenders.  Per Decision 1 this is used ONLY to compute
        #       the uniform reference width pattern
        #       ((n-1) * DEFENDER_SPACING_M) for the width-error signal —
        #       it is NEVER written to target_y.  SpacingChain remains the
        #       sole Y authority.
        #
        #   DEPTH_ROLE_LATERAL_OFFSET_M:
        #       Per-depth-role lateral spacing offset, applied SIGNED by
        #       defender.depth_side (-1 LEFT / +1 RIGHT) per Decision 2 —
        #       unsigned offsets would shift the whole line laterally and
        #       break spacing/depth-role stability.  This defines the
        #       relative spacing pattern across the line.
        #
        #   width_governor_alpha:
        #       Slow macro convergence rate for the width correction
        #       (per-frame gain on the width error; the *dt term lives in
        #       the governor application, so this is effectively a
        #       per-second rate).
        #
        #   compression_curve_enabled / compression_curve_strength:
        #       Future compression-curve UI hook (spec §5).  The hook is a
        #       pure placeholder: while disabled the behaviour is
        #       byte-identical.  No other compression logic is permitted.
        # ═══════════════════════════════════════════════════════════
        self.DEF_LINE_TARGET_WIDTH_M: float = 40.0
        self.DEFENDER_SPACING_M: float = 6.0
        self.DEPTH_ROLE_LATERAL_OFFSET_M: dict[str, float] = {
            "A": 0.0,
            "B": 0.5,
            "C": 1.0,
            "CENTRE": 1.5,
            "D": 2.0,
            "WINGER": 2.5,
        }
        self.width_governor_alpha: float = 0.08
        self.compression_curve_enabled: bool = False
        self.compression_curve_strength: float = 0.0

        # ── MACRO WIDTH GOVERNOR LIVE GATE (2026-08-17) ──
        # The macro width governor (apply_macro_width_governor) rescales
        # target_y toward a UNIFORM-SPACING reference ((n-1) *
        # DEFENDER_SPACING_M) — a "governed width" that would REDEFINE the
        # line's actual winger-to-winger span.  The 3-POINT WIDTH TUNERS
        # (width_left_tryline_m / width_halfway_m / width_right_tryline_m)
        # are the SINGLE width source; NO other subsystem may redefine
        # width.  The macro governor is therefore OFF in the live OPEN_PLAY
        # pipeline by default (this gate, read by DefensiveBehaviourUnified
        # step 10a).  The method and its verifier (verify_macro_width_
        # governor.py) remain intact — the gate only stops the LIVE wiring
        # from calling it, so geometry/spacing/retreat/PTB/TES all see the
        # interpolated width exactly.
        self.macro_width_governor_enabled: bool = False

        # ─────────────────────────────────────────────────────────
        # ENGAGEMENT-BAND SPACING-Y PULL (TASK IV, 2026-08-14)
        # (definitions kept in the line-shape module because this is a
        #  LATERAL LINE-SHAPE control)
        # ─────────────────────────────────────────────────────────
        # ── EVEN-LADDER REWORK (2026-09-15) ──
        # The three-point width curve is the SOLE authority for the line's
        # total width; every defender is laid out on one uniform ladder.
        # The engagement-band pull applied a FIXED, side-UNSIGNED lateral
        # offset to the interior defenders (widening one side, narrowing the
        # other) — exactly the unequal spacing this rework removes.  All
        # offsets are 0.0 so the pull is a strict no-op.
        self.ENGAGEMENT_SPACING_Y_OFFSET: dict[str, float] = {
            "PASSIVE": 0.0,
            "BALANCED": 0.0,
            "AGGRESSIVE": 0.0,
        }

        # ─────────────────────────────────────────────────────────
        # CHASE / COMPRESSION TUNERS (OPEN_PLAY)
        # ---------------------------------------------------------
        # PASSIVE compression: defenders outside chase radius compress
        # their Y toward BC.y by compression_strength fraction.
        # AI_ENGINE_FIX: Reduced from 0.6→0.3 so the defensive line
        # width is not aggressively collapsed during passive chase.
        #
        # (The chase radius / count tuners themselves stay on
        # DefenceTuners — this module owns only the COMPRESSION controls.)
        # ─────────────────────────────────────────────────────────
        self.compression_band_width_m: float = 12.0
        self.compression_strength: float = 0.3

        # ── 5. COMPRESSION TIMING ──
        # BC must be within this X distance (metres) of anchor to activate compression.
        self.compression_activation_distance_m: float = 10.0
        # Seconds to fully converge lane spacing from wide to tight.
        self.compression_duration_s: float = 0.5
        # FR must be within this X distance (metres) of BC to count as threat.
        self.compression_threat_window_m: float = 10.0

    # ─────────────────────────────────────────────────────────────
    # GOAL-LINE WIDTH REGION — the ONE goal-line width anchor
    # ─────────────────────────────────────────────────────────────
    # ``width_defending_tryline_m`` is the DEFENDING TRYLINE WIDTH REGION:
    # the ONE goal-line width anchor (field position x = 110).  It is a
    # PROPERTY alias of the legacy ``width_right_tryline_m`` storage so the
    # two names can never diverge -- reading either returns the same number
    # and writing either sets the same number.  The defending tryline
    # anchor and the defending 10 m anchor are independent COMPRESSIONS of
    # this value (see the four-anchor block in __init__); there is no
    # separate ``width_defending_10m_m`` width tuner.
    @property
    def width_defending_tryline_m(self) -> float:
        """Defending tryline width region (alias of width_right_tryline_m)."""
        return self.width_right_tryline_m

    @width_defending_tryline_m.setter
    def width_defending_tryline_m(self, value: float) -> None:
        self.width_right_tryline_m = float(value)

    # ─────────────────────────────────────────────────────────────
    # GOAL-LINE COMPRESSION ALIASES
    # ─────────────────────────────────────────────────────────────
    # The two goal-line squeeze knobs are stored as
    # ``defending_tryline_compression`` / ``defending_10m_compression``
    # (the canonical names the shipped verifiers read).  These properties
    # expose the SAME values under the descriptive names
    # ``goal_line_compression_tryline`` / ``goal_line_compression_10m`` —
    # reading or writing either name moves the one stored value, so the
    # goal-line squeeze is reachable under both spellings.
    @property
    def goal_line_compression_tryline(self) -> float:
        """Goal-line tryline squeeze (alias of defending_tryline_compression)."""
        return self.defending_tryline_compression

    @goal_line_compression_tryline.setter
    def goal_line_compression_tryline(self, value: float) -> None:
        self.defending_tryline_compression = float(value)

    @property
    def goal_line_compression_10m(self) -> float:
        """Goal-line 10 m squeeze (alias of defending_10m_compression)."""
        return self.defending_10m_compression

    @goal_line_compression_10m.setter
    def goal_line_compression_10m(self, value: float) -> None:
        self.defending_10m_compression = float(value)
