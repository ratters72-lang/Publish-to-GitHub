"""
defensive_line_governor.py — Shared-state defensive line coordination.

Replaces isolated per-defender decision-making with a single frame-consistent
line state that ALL defenders inherit.  This includes:

  - line_depth (X position of the defensive line)
  - line_width (Y spread of the defensive line)
  - compression_factor (how tightly compressed toward the FR lane)
  - line_speed (shared movement speed for the whole line)
  - step_up_state (0=hold, 1=full step, continuous lerp)

Usage in DefensiveBehaviourUnified._apply_open_play_unified():

    line_state = self.line_governor.compute_line_state(world, defenders)
    for d in eligible_defenders:
        tx = line_state["depth_x"]
        half_width = line_state["width_m"] * 0.5
        lane_step = half_width / max(1, self.t.max_lane_index)
        lane_idx = getattr(d, "lane_index", 0)
        ty = fr_y + lane_idx * lane_step * (1.0 - line_state["compression"])
        d.max_speed_mps = line_state["speed_mps"]
        tx, ty = self.def_role_spacing.adjust_for_role(d, tx, ty, world, line_state)
        d.target_x, d.target_y = tx, ty
"""

import math
from world.world_state import GameWorldState
from game.defence.defence_lvl_1.defensive_width import (
    interpolated_defensive_width,
)


class DefensiveLineGovernor:
    """Computes shared defensive line state once per frame.

    All defenders inherit the same depth, width, speed, and compression,
    producing coordinated line behaviour.  Role-specific adjustments are
    applied by ``DefensiveRoleSpacing.adjust_for_role()`` *after* the
    shared state is assigned.
    """

    def __init__(self, tuners, geometry):
        """Initialise governor with tuners and geometry references.

        Args:
            tuners: DefenceTuners instance (centralised tunable parameters).
            geometry: DefensiveGeometry instance (field geometry helpers).
        """
        self.t = tuners
        self.geom = geometry

        # ── Smoothed state (continuous across frames) ──
        self.line_depth: float | None = None
        self.line_width: float | None = None
        self.line_speed: float | None = None
        self.compression_factor: float = 0.0
        self.step_up_state: float = 0.0  # 0.0 = hold, 1.0 = full step

        # ── Width delta clamp baseline (2026-08-02) ──
        # Previous frame's line_width; None until the first compute call so
        # the first frame seeds the baseline without clamping.
        self._prev_line_width: float | None = None

        # ── Last ruck Y seen (Task D, 2026-08-09) ──
        # Stored by compute_line_state (and the zero-state paths) so
        # apply_width_clamp() can centre the governed lateral band on the
        # CURRENT ruck.  Fallback 35.0 matches the engine's default ruck.
        self._last_ruck_y: float = 35.0

        # ── GLOBAL DEFENSIVE ENGINE CONTRACT — LAW 3 ──
        # When True, ALL line state computations are bypassed.
        # Set/cleared by DefensiveBehaviourUnified during pursuit mode.
        self.disabled: bool = False

        # ── MACRO WIDTH GOVERNOR INTEGRATOR (2026-08-17) ──
        # Persistent lateral (Y) width-correction offset (metres),
        # integrated at width_governor_alpha per second toward the §4
        # uniform-reference width.  Persists because the split-spacing
        # chain re-emits fresh target_y every OPEN_PLAY frame — a pure
        # per-frame "+=" on the target would be overwritten and could
        # never converge the line width.  Reset per set (PTB→OPEN_PLAY)
        # so a new ruck starts macro-neutral.
        self.macro_lateral_offset_m: float = 0.0

    # ═════════════════════════════════════════════════════════════════
    # SPEC-CONFORMANT NAMES (2026-08-04)
    #
    # The defensive-line engine spec refers to the state as
    # `line_depth_m`, `line_speed_mps`, `line_width_m`.  The internal
    # attributes are `line_depth`, `line_speed`, `line_width` (kept for
    # zero ripple).  These property aliases expose the spec names so any
    # spec-level reader/writer finds the exact fields.
    # ═════════════════════════════════════════════════════════════════

    @property
    def line_depth_m(self) -> float | None:
        """Spec name for the shared defensive line X depth (metres)."""
        return self.line_depth

    @line_depth_m.setter
    def line_depth_m(self, value: float) -> None:
        self.line_depth = value

    @property
    def line_speed_mps(self) -> float | None:
        """Spec name for the shared line speed (m/s)."""
        return self.line_speed

    @line_speed_mps.setter
    def line_speed_mps(self, value: float) -> None:
        # GLOBAL DEFENSIVE TOP-SPEED CEILING: the shared line speed may
        # never exceed max_defensive_speed_mps (LAW: min(requested, ceiling)).
        self.line_speed = min(value, self.t.max_defensive_speed_mps)

    @property
    def line_width_m(self) -> float | None:
        """Spec name for the horizontal spread of the line (metres)."""
        return self.line_width

    @line_width_m.setter
    def line_width_m(self, value: float) -> None:
        self.line_width = value

    # ═════════════════════════════════════════════════════════════════
    # SHARED RETREAT SPEED (SPEC 4.2 / 4.3)
    #
    # Every retreat writer (pre-catch, SDM retreat, step-9 mandatory
    # retreat) derives its speed from the governor's line speed × a
    # retreat factor — NEVER from the per-defender ceiling.  The engine
    # never applies its own cap here; the global ceiling is the only
    # clamp (final_speed = min(requested, max_defensive_speed_mps)).
    # ═════════════════════════════════════════════════════════════════

    RETREAT_SPEED_SCALE: float = 0.85

    def retreat_speed_mps(self, scale: float | None = None) -> float:
        """Return the shared retreat speed for line-shaped movement.

        Uses the current frame's ``line_speed`` when available; falls back
        to the base line speed ramp (no BC) so pre-catch / pre-open-play
        writers are still line-speed-derived.

        Always clamped by the global ceiling — never a separate cap.
        """
        factor = self.RETREAT_SPEED_SCALE if scale is None else scale
        base = self.line_speed
        if base is None:
            base = min(
                self.t.base_line_speed_mps,
                self.t.max_defensive_speed_mps,
            )
        return min(base * factor, self.t.max_defensive_speed_mps)

    def set_anchor(self, anchor_x: float) -> None:
        """Seed ``line_depth`` from a fresh anchor (e.g. new ruck + 10).

        Called on phase transitions where the new set's anchor is known
        (DOMINANCE → RELEASE).  Does NOT clear the other smoothed fields —
        call ``reset()`` first for a full per-set reset.
        """
        self.line_depth = anchor_x

    def seed_for_kickoff(self, anchor_x: float) -> None:
        """Seed the governor from the kickoff anchor (SPEC 2, pre-catch).

        During KICKOFF there is no ball carrier, so ``compute_line_state()``
        cannot ramp.  This seeds the depth, width and a baseline line speed
        from the kickoff anchor so the pre-catch retreat writer shares the
        line speed instead of inventing its own scale.  Idempotent — safe
        to call every kickoff frame.
        """
        self.line_depth = anchor_x
        self.line_width = self.t.base_line_width_m
        self.compression_factor = 0.0
        self.step_up_state = 0.0
        self.line_speed = min(
            self.t.base_line_speed_mps,
            self.t.max_defensive_speed_mps,
        )

    # ─────────────────────────────────────────────────────────────────
    # MAIN ENTRY — called once per frame in OPEN_PLAY
    # ─────────────────────────────────────────────────────────────────


    def compute_line_state(
        self, world: GameWorldState, defenders: list,
    ) -> dict:
        """Compute and return the shared defensive line state for this frame.

        Args:
            world: The current game world state (provides BC, anchor, etc.).
            defenders: List of defender Player objects (used for context
                       like nearest-defender checks if needed).

        Returns:
            dict with keys:
                "depth_x"      — shared X position of the line (metres)
                "width_m"      — shared Y spread of the line (metres)
                "compression"  — 0.0 (wide) → 1.0 (tight on FR lane)
                "speed_mps"    — shared movement speed (m/s)
                "step_up"      — 0.0 (hold) → 1.0 (full step-up)
        """
        # ═══════════════════════════════════════════════════════════════
        # GLOBAL DEFENSIVE ENGINE CONTRACT — LAW 3
        # When disabled, return zero state immediately.
        # Set by DefensiveBehaviourUnified during pursuit mode.
        # ═══════════════════════════════════════════════════════════════
        if self.disabled:
            anchor_x = getattr(world, "defensive_line_anchor", 60.0)
            return self._zero_state(world, anchor_x)

        # ═══════════════════════════════════════════════════════════════
        # PURSUIT MODE GUARD — no line state during pursuit.
        #
        # Uses world.in_pursuit_mode (set from first_tackle_not_made
        # by OPE.update() at the top of every frame) as the single
        # authoritative gate.  This is belt-and-suspenders on top of
        # the OPE._update_defence() early-return: even if the governor
        # is somehow called during pursuit, it returns a zero state.
        # ═══════════════════════════════════════════════════════════════
        if getattr(world, 'in_pursuit_mode', False):
            anchor_x = getattr(world, "defensive_line_anchor", 60.0)
            return self._zero_state(world, anchor_x)

        bc = world.ball_carrier
        anchor_x = getattr(world, "defensive_line_anchor", 60.0)

        # ── Bail if no ball carrier (cannot compute frame state) ──
        if bc is None:
            return self._zero_state(world, anchor_x)

        # ── Ruck Y stash (Task D) — apply_width_clamp centres the band ──
        self._last_ruck_y = float(getattr(world, "ruck_y_m", 35.0))

        bc_to_anchor = anchor_x - bc.x

        # ═══════════════════════════════════════════════════════════════
        # 1. STEP-UP STATE (continuous 0→1 ramp)
        # ═══════════════════════════════════════════════════════════════
        # STAGE 3F: the step-up ramp is dt-scaled so its shape (and thus
        # line pacing) is identical at any tick rate, not frame-counted.
        _dt = getattr(world, "dt", 1.0 / 60.0)
        self.step_up_state = self._smooth_step(
            self.step_up_state, bc_to_anchor, self.t.step_up_trigger_m,
            self.t.step_up_rate, self.t.step_down_rate, _dt,
        )

        # ═══════════════════════════════════════════════════════════════
        # 2. LINE DEPTH (shared X position)
        #
        # STEP-UP WIRE (2026-08-04): the step-up ramp computed above was
        # dead code — line_depth was pinned to anchor_x with an explicit
        # "never shift forward toward bc.x" comment.  That left every
        # non-closer defender parked on the ruck+10 line while only the
        # ranked PRIMARY/SECONDARY closers advanced, producing the
        # "line stuck, doesn't go forward to tackle" behaviour.
        #
        # Now the WHOLE line advances as one unit by
        #     step_up_state * step_up_buffer_m
        # as the BC approaches the line.  PASSIVE defenders inherit this
        # shift automatically through the geometry targets below.  The
        # onside clamp keeps the line behind ruck_x + 1.0 so it can never
        # push into the ruck zone or past the BC.
        # ═══════════════════════════════════════════════════════════════
        step_forward = self.step_up_state * getattr(
            self.t, "step_up_buffer_m", 1.0
        )
        onside_x = getattr(world, "ruck_x_m", 0.0) + 1.0
        self.line_depth = max(anchor_x - step_forward, onside_x)

        # ═══════════════════════════════════════════════════════════════
        # 3. COMPRESSION FACTOR (continuous 0→1)
        #
        # 0 = far from line (wide spacing)
        # 1 = close to line (tight convergence on FR lane)
        # ═══════════════════════════════════════════════════════════════
        # SMOOTHED COMPRESSION (2026-08-02): ease toward the raw BC-proximity
        # ramp instead of jumping straight to it.  This makes per-tackle width
        # compression gradual over ~7 frames, killing the hard per-frame line
        # squeeze that produced the visible "squash in" + lateral shimmy.
        raw_compression = self._compute_compression(bc_to_anchor)
        self.compression_factor = self.compression_factor + (
            raw_compression - self.compression_factor
        ) * getattr(self.t, "compression_smooth_rate", 1.0)
        self.compression_factor = max(0.0, min(1.0, self.compression_factor))


        # ═══════════════════════════════════════════════════════════════
        # 4. LINE WIDTH (Y spread) — 3-POINT FIELD-POSITION INTERPOLATION
        #
        # 2026-08-17 — RESTORED & ENFORCED 3-POINT WIDTH TUNERS:
        # The winger-to-winger width is interpolated between the three
        # width tuners by the BALL-CARRIER X:
        #
        #     tuners.width_left_tryline_m  (bc at own tryline, x=10)
        #     tuners.width_halfway_m       (bc at halfway,     x=60)
        #     tuners.width_right_tryline_m (bc at opp tryline, x=110)
        #
        # see ``defensive_width.interpolated_defensive_width`` — the
        # SINGLE width source.  The legacy compression ramp
        # (``_compute_line_width`` / ``base_line_width_m`` /
        # ``min_line_width_m``) is KEPT as unused legacy code below
        # (task 8: clean, never delete the architecture) but is NOT the
        # width source anymore.
        #
        # The per-frame width delta clamp below is RETAINED — it is
        # smoothing only (caps per-frame width change at 1.0 m), not a
        # width source.  Geometry/spacing/retreat/PTB/TES all consume
        # ``world.def_line_width_m`` (published in step 4a), so they all
        # see the interpolated width exactly.
        # ═══════════════════════════════════════════════════════════════
        self.line_width = interpolated_defensive_width(self.t, bc.x)

        # WIDTH DELTA CLAMP (2026-08-02): the shared line width may only
        # change by `line_width_max_delta_per_frame_m` per frame relative
        # to the previous frame (stored in `_prev_line_width` at the end of
        # the previous compute call).  This keeps lateral spacing changes
        # continuous — a fast BC advance can never snap the interpolated
        # width more than 1.0 m in a single frame.
        if self._prev_line_width is not None:
            width_delta = self.line_width - self._prev_line_width
            max_delta = getattr(self.t, "line_width_max_delta_per_frame_m", 1.0)
            if abs(width_delta) > max_delta:
                self.line_width = self._prev_line_width + math.copysign(max_delta, width_delta)
        self._prev_line_width = self.line_width

        # ═══════════════════════════════════════════════════════════════
        # 4a. GOVERNOR WIDTH ACTIVATION (Task D, 2026-08-09)
        #
        # The governed line width (compression ramp + per-frame delta
        # clamp) is published onto the WORLD so every downstream width
        # consumer (DefensiveGeometry role anchors, spacing, structured
        # close, pursuit, catch) reads the LIVE governed width instead of
        # the raw static `def_line_width_m`.  Gate:
        #   tuners.governor_width_enabled (default True)
        #   NOT disabled (LAW 3 mode gate)
        #   NOT in pursuit mode (pursuit must never re-shape the line)
        #   ball carrier exists (no BC → no meaningful width ramp)
        #
        # defensive_width.governed_width_m() is the single reader: it
        # applies the PHYSICAL FIELD CAP (field_width_m - 2 * sideline
        # margin) before geometry consumes it, so the raw publish here
        # stays unfiltered.  THE WIDTH CURVE IS THE SOLE AUTHORITY
        # (2026-09-14, Option B): the former width-floor / width-ceiling
        # tuner band is gone, so nothing can raise or lower the requested
        # width between here and the geometry.
        governor_width_enabled = getattr(
            self.t, "governor_width_enabled", True
        )
        if (governor_width_enabled
                and not self.disabled
                and not getattr(world, "in_pursuit_mode", False)):

            # Convert the internally-smoothed width (base → min ramp)
            # into the same coordinate space geometry consumes: the
            # raw `def_line_width_m` field.  The world raw width is the
            # baseline; the governor's compressed width is authoritative
            # while active.
            world.def_line_width_m = self.line_width

            # Publish the activation flags defensive_width reads.
            world.governor_width_active = True
            world.governor_line_width_m = self.line_width

        # ═══════════════════════════════════════════════════════════════
        # 5. SHARED LINE SPEED
        #
        # Faster as BC gets closer to the defensive line.
        # ═══════════════════════════════════════════════════════════════
        self.line_speed = self._compute_line_speed(bc_to_anchor)

        # ═══════════════════════════════════════════════════════════════
        # 6. MAX LANE INDEX (for Y offset computation)
        # ═══════════════════════════════════════════════════════════════
        n_defenders = len(defenders)
        # centre defender = 0, edges = ±(n-1)/2, round up for max absolute
        max_lane = max(1, int((n_defenders - 1) / 2.0))
        self.max_lane_index = max_lane

        return {
            "depth_x": self.line_depth,
            "width_m": self.line_width,
            "compression": self.compression_factor,
            "speed_mps": self.line_speed,
            "step_up": self.step_up_state,
            "max_lane_index": self.max_lane_index,
        }

    # ═══════════════════════════════════════════════════════════════════
    # INTERNAL COMPUTATIONS
    # ═══════════════════════════════════════════════════════════════════

    @staticmethod
    def _smooth_step(
        prev: float, bc_to_anchor: float,
        trigger_m: float, step_up_rate: float, step_down_rate: float,
        dt: float = 1.0 / 60.0,
    ) -> float:
        """Smooth 0→1→0 ramp based on BC proximity to anchor.

        As BC approaches (bc_to_anchor shrinks), state rises toward 1.
        As BC moves away, state decays toward 0.

        Returns float in [0.0, 1.0].
        """
        if bc_to_anchor <= trigger_m:
            # BC is close → step up
            return min(1.0, prev + step_up_rate * dt)
        # BC is far → step down
        return max(0.0, prev - step_down_rate * dt)

    def _compute_compression(self, bc_to_anchor: float) -> float:
        """Compute continuous compression factor [0..1].

        0 = BC far from anchor (wide line).
        1 = BC close to anchor (fully compressed line).

        Uses a linear ramp from compression_trigger_m distance down to 0.
        """
        trigger = self.t.compression_trigger_m
        if trigger <= 0:
            return 1.0
        return max(0.0, min(1.0, (trigger - bc_to_anchor) / trigger))

    def _compute_line_width(self, compression: float) -> float:
        """Compute line width as lerp between base width and min width.

        Args:
            compression: 0.0 (wide) → 1.0 (tight).

        Returns:
            Width in metres.
        """
        return self.t.base_line_width_m * (1.0 - compression) + self.t.min_line_width_m * compression

    def _compute_line_speed(self, bc_to_anchor: float) -> float:
        """Compute shared line speed based on BC distance.

        Base speed + gain when BC is within speed_trigger_m.
        GLOBAL DEFENSIVE TOP-SPEED CEILING: the governor SHAPES movement
        under the unified ceiling (max_defensive_speed_mps); it never
        redefines the maximum.  The output is clamped to the ceiling so a
        close BC can never make the line move faster than the global max.
        """
        gain = max(0.0, self.t.speed_trigger_m - bc_to_anchor) * self.t.line_speed_gain_mps
        raw = self.t.base_line_speed_mps + gain
        return min(raw, self.t.max_defensive_speed_mps)

    def _zero_state(self, world, anchor_x: float) -> dict:
        """Return a safe zero-state when no BC is available.

        Task D: the zero-state paths (disabled, pursuit, no BC) also
        CLEAR the world activation flags so ``governed_width_m()`` never
        sees a stale ``governor_width_active=True`` from a previous
        OPEN_PLAY frame.  The raw ``world.def_line_width_m`` is left
        untouched — the governor simply stops publishing.
        """
        self.line_depth = anchor_x
        self.line_width = self.t.base_line_width_m
        self.compression_factor = 0.0
        # GLOBAL DEFENSIVE TOP-SPEED CEILING: even the zero-state line speed
        # is clamped under the unified ceiling.
        self.line_speed = min(
            self.t.base_line_speed_mps,
            self.t.max_defensive_speed_mps,
        )
        # ── Ruck Y stash (Task D): keep the clamp band centred on the
        #    CURRENT ruck even in zero-state paths. ──
        self._last_ruck_y = float(getattr(world, "ruck_y_m", 35.0))
        # ── Truthful world flags: the governor is not governing width ──
        world.governor_width_active = False
        world.governor_line_width_m = None
        return {
            "depth_x": anchor_x,
            "width_m": self.t.base_line_width_m,
            "compression": 0.0,
            "speed_mps": self.line_speed,
            "step_up": 0.0,
            "max_lane_index": 1,
        }

    # ═══════════════════════════════════════════════════════════════════
    # GOVERNED WIDTH (Task D, 2026-08-09)
    # ═══════════════════════════════════════════════════════════════════

    def governed_width_m(self, world=None) -> float:
        """Return the clamped governed width for this frame (metres).

        Spec ``governor.governed_width_m()`` — the single live width the
        defensive line should use.  The raw candidate is the current
        ``world.def_line_width_m`` (written by the governor's compression
        ramp) or, when no world is supplied, the governor's own smoothed
        ``line_width`` / ``base_line_width_m`` fallback.

        Bound chain — ONE bound only:
          The PHYSICAL FIELD CAP: the total width may never exceed
          ``field_width_m - 2 * sideline_margin_m``, so a WINGER at full
          half-width can never cross the touchline.

        There is NO width-floor / width-ceiling tuner band.
        The 3-point width curve is the SOLE width authority (2026-09-14,
        Option B): a requested width below the old 30 m floor now realises
        exactly instead of being silently raised.

        ── STAGE 6 SINGLE WIDTH ACCESSOR (2026-09-13) ──
        This method USED to carry its own copy of the width rule, which
        disagreed with ``defensive_width.governed_width_m`` in two ways:
        it clamped UNCONDITIONALLY (the module accessor returns the raw
        stored width while no governor is active), and it clamped
        ``world.def_line_width_m`` where the module clamps
        ``world.governor_line_width_m``.  Stage 1 named that split
        GOVERNED_ACCESSOR_DIVERGENCE.  There is now ONE implementation:
        with a *world* supplied this delegates to the module accessor, so
        the value the spacing / split pipeline builds the line from is
        exactly the value this method reports.  The legacy body below is
        UNREACHABLE (retained for grep-based verifiers).
        """
        if world is not None:
            from game.defence.defence_lvl_1.defensive_width import (
                governed_width_m as _module_governed_width_m,
            )
            return float(_module_governed_width_m(world, self.t))

        candidate = self.line_width
        if candidate is None:
            candidate = getattr(self.t, "base_line_width_m", 40.0)
        raw = float(candidate)

        # PHYSICAL FIELD CAP ONLY — the width curve is the sole authority
        # (2026-09-14, Option B).  No width-floor / width-ceiling tuner
        # band may raise or lower the requested width; the only remaining
        # limit is the geometry of the field itself.
        margin = float(getattr(self.t, "sideline_margin_m", 3.0))
        field_cap = float(getattr(self.t, "field_width_m", 70.0)) - 2.0 * margin
        return max(0.0, min(raw, field_cap))

    def _published_side_spans(self, world):
        """Return this frame's published ``(left_m, right_m)`` spans, or None.

        Reads the width authority's publication WITHOUT triggering a solve:
        the publication is accepted only when it belongs to the CURRENT frame
        (``world.formation_width['frame'] == world.frame_count``), so a stale
        or absent publication falls back to the symmetric governed band.

        GOVERNED WIDTH IS THE AUTHORITATIVE TOTAL SPAN (2026-09-14): this is
        what lets every governor clamp site read the SAME per-side band the
        spacing ladder was built from, with no second width formula.
        """
        if world is None:
            return None
        publication = getattr(world, "formation_width", None)
        if not isinstance(publication, dict):
            return None
        if publication.get("frame") != getattr(world, "frame_count", None):
            return None
        try:
            return float(publication["left_m"]), float(publication["right_m"])
        except (KeyError, TypeError, ValueError):
            return None

    def apply_width_clamp(self, y: float, world=None) -> float:
        """Clamp a lateral target Y into the governed width band (Task D).

        Spec ``governor.apply_width_clamp(final_width_y)`` — the second
        clamp stage of the structured_close width pipeline (Task C gate:
        ``tuners.governor_width_active and world.governor_width_active``)
        and the per-spacing clamp for split-spacing.

        The allowed lateral band is the governed width centred on the
        CURRENT ruck Y:

            [ruck_y - governed/2, ruck_y + governed/2]

        intersected with the sideline margins, so a defender can never be
        placed at a Y the line width would not reach and never off-field.
        A pure min/max clamp — applying it twice never changes the result
        (idempotent).

        ``world`` is optional for the structured-close Task C call form
        ``apply_width_clamp(final_width_y)``: when omitted the band uses
        the governor's stored ``_last_ruck_y`` and smoothed width.
        """
        band_centre = float(getattr(world, "ruck_y_m", None)) if world is not None else None
        if band_centre is None:
            band_centre = self._last_ruck_y

        # ── GOVERNED WIDTH IS THE AUTHORITATIVE TOTAL SPAN (2026-09-14) ──
        # The band is the frame's PUBLISHED per-side span about the ruck —
        # ``[ruck_y - left/2, ruck_y + right/2]`` — not a SYMMETRIC
        # ``+/- governed_width / 2``.  The published spans already carry the
        # total span distributed across the two ruck-anchored halves under
        # the margin-only room bound, so a symmetric band re-declared the
        # governed width as a PER-SIDE value and re-narrowed the far side of
        # an off-centre ruck (breaking ``realised span == governed width``).
        # The symmetric form is used only when this frame has no publication
        # (stub worlds / pre-publish callers).
        published = self._published_side_spans(world)
        if published is None:
            half_width = self.governed_width_m(world) * 0.5
            band_min = band_centre - half_width
            band_max = band_centre + half_width
        else:
            band_min = band_centre - float(published[0]) * 0.5
            band_max = band_centre + float(published[1]) * 0.5

        # Sideline intersection (idempotent with the margin clamp below).
        margin = float(getattr(self.t, "sideline_margin_m", 3.0))
        field_max = float(getattr(self.t, "field_width_m", 70.0)) - margin
        band_min = max(band_min, margin)
        band_max = min(band_max, field_max)

        return max(band_min, min(band_max, y))

    # ═════════════════════════════════════════════════════════════════
    # MACRO WIDTH GOVERNOR — LINE WIDTH & SPACING (2026-08-17)
    #
    # Macro-level defensive line width governor (spec §1–§5).  Operates
    # ONLY at macro scale on the LATERAL axis.  In this engine the lateral
    # axis is Y (target_y); X is depth and belongs to the anchor / width-X
    # authorities.  The spec's "lateral (X) correction" (§2 / Decision 1)
    # is therefore applied to target_y here — a flat additive on target_x
    # would move the whole line in depth and could never change width.
    #
    # Contracts:
    #   - target_y ONLY.  Never writes target_x (depth), never modifies
    #     stabiliser alpha, geometry weights, smoothing, compression
    #     weights, slowdown band, or stance radius.
    #   - Macro-only: a slow DC integrator (width_governor_alpha per
    #     second), no micro-movement corrections.
    #   - §3 depth-role offsets are applied SIGNED by depth_side
    #     (Decision 2) so the line stays symmetric.
    #   - §4 baseline spacing is used ONLY as the error reference
    #     (Decision 1) — the uniform pattern is never written to
    #     target_y; SpacingChain remains the sole Y authority.
    #   - §5 compression-curve hook is inert by default.
    # ═════════════════════════════════════════════════════════════════

    # Sanity bound on the integrated macro lateral offset (metres).
    # The proportional feedback below is first-order (no oscillation);
    # the clamp only protects against pathological line states making
    # the integrator run away.
    MACRO_OFFSET_LIMIT_M: float = 15.0

    def uniform_spacing_reference_width(self, defenders) -> float:
        """Return the §4 uniform-reference width (n-1) * DEFENDER_SPACING_M.

        Decision 1 (2026-08-17): the §4 baseline-spacing pattern
        (``anchor_y + i * DEFENDER_SPACING_M``) is NOT written to
        target_y.  It is used ONLY as the macro width governor's error
        reference — the spread a uniformly-spaced line of *n* eligible
        defenders would have.
        """
        n = sum(1 for d in defenders if self._is_macro_eligible(d))
        spacing = float(getattr(self.t, "DEFENDER_SPACING_M", 6.0))
        return max(0.0, (n - 1) * spacing)

    def compute_line_width(self, defenders) -> float:
        """Return the current lateral line width (metres).

        Spec §2 ``compute_line_width(defenders)`` — the MACRO spread of
        the finalised line targets (the signal the width governor
        rescales): max(target_y) - min(target_y) over macro-eligible
        defenders.  Markers / fullback / frozen / committed tacklers /
        no-role defenders are excluded.  Returns 0.0 when fewer than
        two eligible targets exist.
        """
        ys = []
        for d in defenders:
            if not self._is_macro_eligible(d):
                continue
            ty = getattr(d, "target_y", None)
            if ty is None or not math.isfinite(float(ty)):
                continue
            ys.append(float(ty))
        if len(ys) < 2:
            return 0.0
        return max(ys) - min(ys)

    def _is_macro_eligible(self, d) -> bool:
        """Return True when *d* participates in the macro width governor.

        Excludes the dynamic fullback (own anchor writer), frozen /
        freeze-timer defenders, committed tacklers (their BC-forward
        collision targets are not line targets), and defenders without
        a depth role (markers / DUMMY_HALF / tacklers never receive a
        depth role from assign_depth_roles, so the role check covers
        them).
        """
        if d is None:
            return False
        if getattr(d, "fullback_role_active", False):
            return False
        if bool(getattr(d, "frozen", False)) or getattr(d, "freeze_timer", 0.0) > 0:
            return False
        if getattr(d, "_tackle_commit_active", False):
            return False
        role = getattr(d, "depth_role", None)
        return role in ("A", "B", "C", "D", "CENTRE", "WINGER")

    def _macro_centre_y(self, defenders) -> float:
        """Mean target_y of the macro-eligible defenders (line centre)."""
        ys = []
        for d in defenders:
            if not self._is_macro_eligible(d):
                continue
            ty = getattr(d, "target_y", None)
            if ty is None or not math.isfinite(float(ty)):
                continue
            ys.append(float(ty))
        if not ys:
            return 0.0
        return sum(ys) / len(ys)

    def _clamp_sideline_macro(self, y: float) -> float:
        """Clamp a macro-adjusted lateral target inside the field margins."""
        margin = float(getattr(self.t, "sideline_margin_m", 3.0))
        field_max = float(getattr(self.t, "field_width_m", 70.0)) - margin
        return max(margin, min(field_max, float(y)))

    def apply_macro_width_governor(self, world, defenders, dt) -> None:
        """Apply the macro line width + depth-role spacing governor.

        Runs ONCE per OPEN_PLAY frame, after every target writer, the
        finalise blend, and the anchor audit — and BEFORE lane-tackler
        assignment + MMDL (committed tacklers may legitimately overwrite
        their own Y later; this method never touches them).  Modifies
        ONLY ``target_y`` of macro-eligible line defenders.

        Steps (spec §2/§3/§4 + Decision 1/2):
          1. §3 depth-role spacing distribution — each eligible
             defender's target_y is offset by
             DEPTH_ROLE_LATERAL_OFFSET_M[depth_role] SIGNED by its
             persistent depth_side (-1/+1).  Signed offsets keep the
             line symmetric (Decision 2).
          2. §4/§2 width error — measured target spread vs the §4
             uniform reference (n-1) * DEFENDER_SPACING_M (Decision 1).
          3. §5 compression-curve hook — scales the lateral adjust when
             enabled; inert by default (no behavioural change).
          4. Integrate the correction into ``macro_lateral_offset_m``
             and apply it as a symmetric rescale about the line centre,
             so the centroid never drifts and the line converges
             smoothly (first-order, tau = 1/(2*alpha) seconds) to the
             reference width.

        Never touches stabiliser alpha / geometry weights / smoothing /
        compression weights / slowdown band / stance radius, and never
        introduces micro-movement corrections.
        """
        eligible = [d for d in defenders if self._is_macro_eligible(d)]
        if len(eligible) < 2:
            return
        if dt is None or not math.isfinite(float(dt)) or float(dt) <= 0.0:
            return
        for d in eligible:
            ty = getattr(d, "target_y", None)
            if ty is None or not math.isfinite(float(ty)):
                return

        ruck_y = float(getattr(world, "ruck_y_m", 35.0))

        # ── §3 DEPTH-ROLE SPACING DISTRIBUTION (Decision 2: signed) ──
        from game.defence.defence_lvl_1.defensive_width import depth_side_of
        role_offsets = getattr(self.t, "DEPTH_ROLE_LATERAL_OFFSET_M", None) or {}
        for d in eligible:
            role = getattr(d, "depth_role", "D") or "D"
            offset = float(role_offsets.get(role, 0.0))
            if offset == 0.0:
                continue
            side = depth_side_of(d, ruck_y)
            d.target_y = self._clamp_sideline_macro(
                float(d.target_y) + offset * side
            )

        # ── CLOSED-LOOP MEASURE + INTEGRATE + SINGLE APPLY ──
        # The spacing chain re-emits the raw §3-broadened targets EVERY
        # frame, so the stored offset is the ONLY memory of the previous
        # frame's correction.  The loop closes by measuring the CURRENT
        # EFFECTIVE width — the chain spread PLUS the already-integrated
        # offset applied symmetrically — on float copies (never mutating
        # target_y for the measurement), then integrating ONE small
        # correction, then writing the FINAL target exactly once from the
        # raw chain targets with the NEW offset.
        #
        #     effective(t) = chain_width + 2 * off(t)
        #     off(t+1)     = off(t) + alpha * (ref - effective(t)) * dt
        #
        #   => d(effective)/dt = 2*alpha*(ref - effective)  (first-order)
        #   => effective(t) -> ref, tau = 1/(2*alpha) sec, off -> (ref-chain)/2.
        #
        # Measuring WITHOUT the stored offset would make the error signal
        # constant (chain re-emits raw base every frame) — the integrator
        # would run open-loop, slam into MACRO_OFFSET_LIMIT_M and never
        # converge.  Applying the offset twice per frame would double the
        # loop gain and oscillate.
        y0 = [float(d.target_y) for d in eligible]          # chain + §3
        centre_y = sum(y0) / len(y0)
        w0 = max(y0) - min(y0)
        if w0 <= 0.0:
            return
        half0 = max(w0 * 0.5, 1e-6)
        ratios = [(y - centre_y) / half0 for y in y0]

        offset_now = self.macro_lateral_offset_m
        effective_width = w0 + 2.0 * offset_now

        reference_width = self.uniform_spacing_reference_width(eligible)
        width_error = reference_width - effective_width

        # ── §5 COMPRESSION-CURVE HOOK (inert by default) ──
        lateral_adjust = width_error * float(getattr(
            self.t, "width_governor_alpha", 0.08
        ))
        if bool(getattr(self.t, "compression_curve_enabled", False)):
            lateral_adjust *= float(getattr(
                self.t, "compression_curve_strength", 0.0
            ))

        # ── PER-SECOND INTEGRATION (measure -> integrate -> apply once) ──
        dt_val = float(dt)
        offset_next = offset_now + float(lateral_adjust) * dt_val
        limit = float(getattr(self, "MACRO_OFFSET_LIMIT_M", 15.0))
        offset_next = max(-limit, min(limit, offset_next))
        self.macro_lateral_offset_m = offset_next

        # ── SINGLE SYMMETRIC APPLICATION OF THE NEW OFFSET ──
        # Write the final target from the raw chain targets (y0) with the
        # new offset: outer defenders (|ratio| -> 1) move more than inner
        # (ratio -> 0), so the centroid never drifts and the width
        # converges smoothly to the §4 reference.
        for d, y, ratio in zip(eligible, y0, ratios):
            d.target_y = self._clamp_sideline_macro(
                y + offset_next * ratio
            )

    def reset(self):
        """Reset all smoothed state (call on phase transitions like PTB→OPEN_PLAY).

        2026-08-04: also clears `_prev_line_width` so the width delta clamp
        baseline does not leak across tackle sets.  The previous frame's
        width belongs to the old ruck; carrying it into a new set would
        clamp the fresh line width against a stale value.
        """
        self.line_depth = None
        self.line_width = None
        self.line_speed = None
        self.compression_factor = 0.0
        self.step_up_state = 0.0
        self._prev_line_width = None
        # ── Macro width governor: fresh set starts macro-neutral ──
        self.macro_lateral_offset_m = 0.0
