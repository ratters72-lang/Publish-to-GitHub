"""
defensive_role_spacing.py — STEP‑03A‑10 Role-based X/Y spacing computation.

PURPOSE
-------
Encapsulates all target_x/target_y computation for OPEN_PLAY defence,
extracting it from the inline logic in _apply_open_play_unified().

This module sits between timing (which sets token flags) and movement
(which applies velocity).  It is called ONCE per defender per frame,
immediately after timing gates and before speed scaling.

DESIGN
------
- ``DefensiveRoleSpacing`` is stateless between frames — all per-frame
  context is snapshotted via ``prepare(world)`` at the top of the loop.
- ``compute_role_spacing()`` returns (target_x, target_y) given a
  defender, ball_carrier, role, lane_index, and optional modifiers
  from the timing system.
- The caller sets ``d.target_x`` and ``d.target_y`` from the return value.
- No side-effects on defender objects (except the return value is
  assigned by the caller).
"""

from player.player import DefenderRole


class DefensiveRoleSpacing:
    """Computes BC-relative (target_x, target_y) for OPEN_PLAY defenders.

    Uses a LEAD POINT projection (bc.x + bc.vx * lead_time) so defenders
    aim ahead of the ball carrier, intercepting their path rather than
    chasing the current position.

    Usage in DefensiveBehaviourUnified._apply_open_play_unified():

        self.def_role_spacing.prepare(world)
        for d in eligible_defenders:
            tx, ty = self.def_role_spacing.compute_role_spacing(
                defender=d,
                ball_carrier=bc,
                role=d.role,
                lane_index=getattr(d, "lane_index", 0),
                compression_factor=self.def_timing.get_compression_factor(d),
                lateral_state=getattr(d, "lateral_hesitation", 0.0),
                retreat_state=getattr(d, "retreat_delay", 0.0),
            )
            d.target_x = tx
            d.target_y = ty
    """

    def __init__(self, tuners):
        self.tuners = tuners

        # ── Per-frame context (set by prepare()) ──
        self._anchor_x: float = 0.0
        self._ruck_x: float = 0.0
        self._ruck_y: float = 0.0
        self._def_line_width_m: float = 40.0

        # ── GLOBAL DEFENSIVE ENGINE CONTRACT — LAW 3 ──
        # When True, ALL role spacing computations are bypassed.
        # Set/cleared by DefensiveBehaviourUnified during pursuit mode.
        self.disabled: bool = False

    # ─────────────────────────────────────────────────────────────
    # PER-FRAME CONTEXT
    # ─────────────────────────────────────────────────────────────

    def prepare(self, world):
        """Snapshot world context for the current frame.

        Must be called ONCE per frame before any compute_role_spacing()
        calls, to avoid redundant repeated lookups.
        """
        self._anchor_x = getattr(world, "defensive_line_anchor", 0.0)
        self._ruck_x = getattr(world, "ruck_x_m", 0.0)
        self._ruck_y = getattr(world, "ruck_y_m", 0.0)
        # ── STAGE 6 SINGLE WIDTH AUTHORITY (2026-09-13) ──
        # This role-spacing geometry helper must consume the PUBLISHED frame
        # width, not the raw ``world.def_line_width_m`` storage slot.  The raw
        # slot is the governor's own un-governed field: it survives an
        # inactive / reset governor UN-clamped, so reading it directly made
        # this helper a second, divergent width source (the
        # GOVERNED_ACCESSOR_DIVERGENCE class Stage 6 removes).
        # ``formation_width_value`` returns the published governed width and
        # falls back to the same single governed chain when no publication
        # exists for the frame yet.  Imported locally so module load order
        # can never introduce a cycle.
        try:
            from game.defence.defence_lvl_1.formation_width_authority import (
                formation_width_value as _published_width,
            )
            self._def_line_width_m = float(_published_width(world))
        except Exception:
            self._def_line_width_m = getattr(world, "def_line_width_m", 40.0)
        # MARKER AUTHORITY LOCK (2026-09-04): hold the live world reference so
        # compute_role_spacing can route marker targets through the single
        # ruck-pin geometry authority.
        self._world = world
        # AI_ENGINE_FIX: No bc.x, bc.y, fr.y stored — only anchor/ruck.
        # All targets are computed from anchor_x for line defenders and
        # ruck_x/ruck_y for markers/DH, via get_defence_target().
        # bc/fr positions are NEVER used for defensive target computation.

    # ─────────────────────────────────────────────────────────────
    # PER-DEFENDER COMPUTATION
    # ─────────────────────────────────────────────────────────────

    def compute_role_spacing(
        self,
        defender,
        ball_carrier,
        role: str | None,
        lane_index: int = 0,
        compression_factor: float = 0.0,
        lateral_state: float = 0.0,
        retreat_state: float = 0.0,
    ) -> tuple[float, float]:
        """Return (target_x, target_y) for *defender* based on its role.

        Parameters
        ----------
        defender : Player
            The defender being positioned (used for depth_class, role checks).
        ball_carrier : Player | None
            The current ball carrier (may be None in edge cases).
        role : str | None
            The defender's positional role string (e.g. "first_receiver",
            None for most defenders).  Used for special-case mapping.
        lane_index : int
            Y-sort index from lane computation (centre=0, edges=±1,±2...).
        compression_factor : float
            0.0 = fully wide offset active.
            1.0 = fully compressed.
        lateral_state : float
            > 0 → defender is in lateral hesitation (damp Y movement).
        retreat_state : float
            > 0 → defender is in retreat hesitation (hold X, no backpedal).

        Returns
        -------
        (target_x, target_y) as floats.

        AI_ENGINE_FIX: ALL targets are computed from get_defence_target().
        No bc.x, bc.y, fr.x, fr.y, or ball_x/y are used.
        Markers/DH use ruck_x/ruck_y. Line defenders use anchor_x/lane_y.
        """
        # ── Extract per-defender attributes ──
        depth_class = getattr(defender, "depth_class", "PASSIVE")
        def_role = getattr(defender, "defender_role", None)
        is_marker = def_role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B)

        # ── Derive defender index for get_defence_target() ──
        # Offset: 0=marker1, 1=marker2, 2=DH, 3+=line defenders
        base_idx = abs(lane_index) + 1
        if is_marker:
            def_index = 0 if def_role == DefenderRole.MARKER_A else 1
        elif def_role == DefenderRole.DUMMY_HALF:
            def_index = 2
        else:
            def_index = 3 + base_idx

        # ── Role-based target computation ──
        # AI_ENGINE_FIX: Direct computation from stored anchor/ruck values.
        # Replicates get_defence_target() logic without needing world arg.
        # MARKER AUTHORITY LOCK (2026-09-04): markers route through the
        # single ruck-pin geometry authority (ruck_x + depth, ruck_y) —
        # never the legacy (ruck_x, ruck_y - 0.5/-1.0) Y-offset.
        if def_index == 0:
            from game.defence.defence_lvl_1.marker_authority import marker_geometry_target as _mt
            tx, ty = _mt(self._world, defender)
        elif def_index == 1:
            from game.defence.defence_lvl_1.marker_authority import marker_geometry_target as _mt
            tx, ty = _mt(self._world, defender)
        elif def_index == 2:
            tx = self._ruck_x
            ty = self._ruck_y
        else:
            # Line defenders: anchor_x + width-based Y
            # AI_ENGINE_FIX: Use def_line_width_m as authoritative span
            # centred on ruck_y_m, replacing hardcoded lane_width=2.0/base_y=30.0.
            # A single-index call cannot know total defender count, so we use
            # a fixed DEFAULT_NUM_LANES=7 for consistent even spacing.
            lane_idx = def_index - 3
            half_width = self._def_line_width_m * 0.5
            DEFAULT_NUM_LANES = 7
            slot_count = max(1, DEFAULT_NUM_LANES - 1)  # 6 intervals
            step = self._def_line_width_m / slot_count
            ty = self._ruck_y - half_width + min(lane_idx, slot_count) * step
            tx = self._anchor_x

        # ── Retreat hesitation: hold current X if retreat delay active ──
        if retreat_state > 0.0:
            tx = defender.x

        # ── Lateral hesitation: hold current Y if lateral delay active ──
        if lateral_state > 0.0:
            ty = defender.y

        return (tx, ty)

    # ═══════════════════════════════════════════════════════════════════
    # LINE GOVERNOR DELTA — adjust_for_role()
    #
    # Called by _apply_open_play_unified() AFTER the governor computes
    # the shared line state.  Each defender starts from the shared
    # depth_x/width_m/compression and this method applies role-specific
    # deltas on top.
    #
    # Called in the governor loop:
    #     tx, ty = self.def_role_spacing.adjust_for_role(d, tx, ty, world, line_state)
    # ═══════════════════════════════════════════════════════════════════

    def adjust_for_role(
        self,
        defender,
        base_tx: float,
        base_ty: float,
        world,
        line_state: dict,
    ) -> tuple[float, float]:
        """Apply role-specific deltas on top of the shared line state.

        AI_ENGINE_FIX: ALL targets are anchor/ruck-based. No bc.x, bc.y,
        fr.x, or fr.y are used.  Markers/DH use ruck_x/ruck_y with Y
        offsets.  PRIMARY/SECONDARY/PASSIVE all use the governor's
        depth_x which is already anchor-aligned.

        The governor's depth_x is the single source of truth for line X.
        No bc-relative clamps are applied — the governor already handles
        the appropriate depth.

        Also honours timing flags for retreat hesitation (hold X) and
        lateral hesitation (hold Y), applied *after* the role delta.

        Args:
            defender: The Player object being positioned.
            base_tx: Shared line depth_x from the governor.
            base_ty: Shared lane Y from the governor.
            world: Current game world state.
            line_state: The governor's output dict (depth_x, width_m,
                       compression, speed_mps, step_up).

        Returns:
            (target_x, target_y) with role-specific adjustments.
        """
        depth_class = getattr(defender, "depth_class", "PASSIVE")
        def_role = getattr(defender, "defender_role", None)
        is_marker = def_role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B)

        tx = base_tx
        ty = base_ty

        # ── Markers and DUMMY_HALF: tight to ruck ──
        # AI_ENGINE_FIX: Use ruck_x/ruck_y, NOT bc.x/bc.y.
        ruck_x = getattr(world, "ruck_x_m", 60.0)
        ruck_y = getattr(world, "ruck_y_m", 35.0)
        if is_marker or def_role == DefenderRole.DUMMY_HALF:
            if def_role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                # MARKER AUTHORITY LOCK (2026-09-04): marker target = the
                # single ruck-pin geometry authority (ruck_x + depth, ruck_y).
                from game.defence.defence_lvl_1.marker_authority import (
                    marker_geometry_target as _mt,
                )
                tx, ty = _mt(world, defender)
            elif def_role == DefenderRole.DUMMY_HALF:
                tx = ruck_x
                ty = ruck_y
            return (tx, ty)

        # ── PRIMARY / SECONDARY / PASSIVE ──
        # AI_ENGINE_FIX: No bc-relative clamps.  The governor's depth_x
        # is already the correct anchor-based line depth.  PRIMARY and
        # SECONDARY depth classes are handled by the timing system and
        # the governor's step-up state, not by bc-relative offsets.
        # The governor also defines max_lane_index for per-defender Y
        # which is already in base_ty from the calling loop.

        # ── Timing flags are VELOCITY-ONLY holds (2026-09-04) ──
        # retreat_delay / lateral_hesitation no longer pin the target to the
        # current position — that would override the geometry authority.  The
        # structured caller sets move_speed = 0.0 for a timing-held defender,
        # and Player._accelerate_toward_target halts on move_speed <= 0 for a
        # defensive-ceiling carrier.  The role-delta target (base_tx/base_ty)
        # stays the geometry authority.

        return (tx, ty)
