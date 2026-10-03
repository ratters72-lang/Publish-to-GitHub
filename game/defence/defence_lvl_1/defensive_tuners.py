"""
DEFENCE TUNERS — SINGLE SOURCE OF TRUTH
=======================================

This module defines ALL tunable parameters for the defensive line.

It supports the new 2‑layer defensive architecture:

    STEP‑1 (Geometry)
        Tuners define PRIMARY WIDTH.
        PRIMARY WIDTH defines SPACING.
        SPACING defines ALL defender Y positions.

        No hardcoded spacing.
        No role-based spacing.
        No compression/expansion.
        No sliding.
        No band logic.

    STEP‑2 (Behaviour)
        Depth roles (A/B/C/D) and positional roles (CENTRE/WINGER)
        map to depth offsets via ROLE_DEPTH_M.

        Depth roles ALWAYS override positional roles.

Everything in this file is in metres.

LINE-SHAPE TUNABLES LIVE IN ONE FILE
------------------------------------
Every field-width and compression control (the 3-point width curve, the
goal-line compression knobs, the governor width / compression ramp, the
role-width anchors, the lane-span rails, the macro width governor and the
engagement-band lateral pull) is defined in
``defensive_line_shape_tuners.py``.  ``DefenceTuners`` below merely
INHERITS it, so a future width or compression adjustment is a single-file
edit with no code tracing.
"""

from game.defence.defence_lvl_1.defensive_line_shape_tuners import (
    DefensiveLineShapeTuners,
)

# ─────────────────────────────────────────────────────────────────────
# POST-TES DRIVE-END SETTLE WINDOW PHASES (2026-09-07)
# ─────────────────────────────────────────────────────────────────────
# The settlement window is the post-drive reform span: it begins at
# DOMINANCE→RELEASE (the ruck forms) and stays active through RELEASE,
# the PTB handover and POST_RUCK_OPEN_PLAY, disengaging on the first
# true OPEN_PLAY frame.  While active the ruck-relative snap writers
# (ruck-pin position snap, spacing-Y / width-X re-pin) are gated to
# target-only / hold-current so bodies GLIDE to the ruck target instead
# of teleporting.  The OPE arms ``world.post_tes_settle_enabled`` at
# DOMINANCE→RELEASE (tuner > 0) and clears it on OPEN_PLAY entry.

POST_TES_SETTLE_PHASES = frozenset({
    "RELEASE", "TACKLE_RELEASE",
    "PTB_READY", "PTB_PREP", "PTB_EXEC", "PTB",
    "POST_PTB_RESET", "POST_RUCK_OPEN_PLAY",
})


def post_tes_settle_active(world) -> bool:
    """True while the post-TES drive-end settle window is active.

    Covers the whole post-drive reform span (DOMINANCE→RELEASE through
    POST_RUCK_OPEN_PLAY).  The phase set is the authoritative window; the
    ``world.post_tes_settle_frames`` counter is retained as the legacy
    burn path for the initial frames.  ``world.post_tes_settle_enabled``
    is the "armed this cycle" latch (set at DOMINANCE→RELEASE when the
    tuner > 0), which preserves the documented ``0 = disabled`` semantics:
    a disabled window never arms, so the phase set is never consulted.
    """
    if getattr(world, "post_tes_settle_frames", 0) > 0:
        return True
    if not getattr(world, "post_tes_settle_enabled", False):
        return False
    phase = str(getattr(world, "_ope_phase_label", "") or "")
    return phase in POST_TES_SETTLE_PHASES


class DefenceTuners(DefensiveLineShapeTuners):
    """Tuners controlling STEP‑1 geometry + STEP‑2 depth offsets.

    LINE-SHAPE TUNABLES LIVE IN ``defensive_line_shape_tuners.py``.  This
    class INHERITS :class:`DefensiveLineShapeTuners` and calls its
    ``__init__`` first, so every field-width / compression tuner is defined
    in ONE file.  Adjust the line's width or compression THERE, never here.
    """

    def __init__(self):
        # ── LINE-SHAPE TUNERS (SINGLE SOURCE) ──
        # Width curve, goal-line compression, governor width/compression
        # ramp, role-width anchors, lane-span rails, macro width governor
        # and the engagement-band lateral pull are ALL defined in
        # defensive_line_shape_tuners.py.  This call sets them on the
        # instance so every existing consumer reads them unchanged.
        super().__init__()

        # ═══════════════════════════════════════════════════════════
        # GLOBAL DEFENSIVE TOP-SPEED CEILING — SINGLE SOURCE OF TRUTH
        #
        # One authoritative maximum speed (m/s) for EVERY defensive
        # movement writer across ALL phases of play:
        #
        #   - kickoff chase        = max_defensive_speed_mps
        #   - pursuit mode         ≤ max_defensive_speed_mps
        #   - collapse             ≤ max_defensive_speed_mps
        #   - retreat compliance   ≤ max_defensive_speed_mps
        #   - SDM retreat          ≤ max_defensive_speed_mps
        #   - depth classification ≤ max_defensive_speed_mps
        #   - structured OPEN_PLAY ≤ max_defensive_speed_mps
        #   - governor line speed  ≤ max_defensive_speed_mps (shapes only)
        #
        # No subsystem may define its own independent top speed.  If a
        # behaviour needs more speed, adjust the behaviour — never this
        # ceiling.  Default 8.3 m/s (2026-08-03): the unified ceiling was
        # raised from 5.0 so defenders on the AGGRESSIVE depth override can
        # actually close the gap on a sprinting BC (>7 m/s) and reach the
        # tackle-acquisition windows.  Still below the per-defender physical
        # max (9.2 m/s), so the ceiling keeps defenders from overshooting
        # the ball carrier.
        #
        # The POST-CATCH defensive line clamp is the ONE exception: it uses
        # its own ceiling (post_catch_cap_mps = 8.0), NOT the unified chase
        # ceiling.  8.3 m/s is only for open-play chase; the kickoff-catch
        # window clamps defenders to a sane line speed so the line folds
        # and reforms instead of exploding to full chase speed.
        # ═══════════════════════════════════════════════════════════════
        self.max_defensive_speed_mps: float = 8.3

        # ─────────────────────────────────────────────────────────
        # POST-CATCH DEFENSIVE LINE CLAMP CEILING (m/s)
        #
        # Separate from max_defensive_speed_mps.  While the post-catch
        # clamp is armed (kickoff-catch → first stable OPEN_PLAY), defender
        # speed is capped at THIS value so the line folds/reforms at a
        # visually sane pace instead of exploding to full chase speed
        # (8.3 m/s) immediately after the catch.  Open-play chase still
        # uses max_defensive_speed_mps.
        #
        # 2026-08-04: raised 5.0 → 8.0 so the kickoff chase band and the
        # post-catch line clamp run at the SAME speed (kickoff_engine's
        # _kickoff_retreat_speed_mps() returns this value directly).  The
        # pre-catch chase, the post-catch clamp, and the fully-blended
        # governor line speed are now all within a few tenths of each
        # other, eliminating the visible speed snap at the catch boundary.
        # ─────────────────────────────────────────────────────────
        self.post_catch_cap_mps: float = 8.0

        # Line-shape tuners (field dimensions, sideline margin, width
        # curve, goal-line compression, role-width anchors, lane-span
        # rails, governor width/compression, macro width governor) are
        # defined in defensive_line_shape_tuners.py via super().__init__().

        # STEP-1 WIDTH CURVE (width_left_tryline_m / width_halfway_m /
        # width_right_tryline_m + legacy aliases) moved to
        # defensive_line_shape_tuners.py.

        # WIDTH OFFSETS / ROLE_DEPTH_M / role_width_anchor and
        # ROLE_WIDTH_OFFSETS all moved to defensive_line_shape_tuners.py.

        # GOVERNOR WIDTH ACTIVATION, the structured close gates, the
        # split-design factors, short_side_buffer_m and the lane-span
        # rails all moved to defensive_line_shape_tuners.py.

        # ─────────────────────────────────────────────────────────
        # COUNT DECISION PHASE BIAS (2026-09-03) — INERT (2026-09-04).
        # The Phase-Count-Geometry-Unification directive removed the
        # phase-bias block from decide_side_counts: the count is PURELY
        # room-derived, so NO phase may nudge the short-side fraction.
        # These tuners are retained for backward compatibility but are
        # all 0.0 (INERT) — changing them has NO live effect.  The count
        # is a deterministic function of (room, n_total) only.
        #
        # count_frac_max_short:
        #   Hard cap on the short-side fraction (0.50 = never more than
        #   half the line on the short side).  Still read by
        #   _count_phase_profile (documented-dead in split_spacing) but
        #   never by the live count rule.
        #
        # count_split_hysteresis:
        #   Per-side count delta hysteresis.  A new split is adopted only
        #   when it differs from the previous CONSECUTIVE frame's split by
        #   more than this (for the same ruck).  Still LIVE.
        # ─────────────────────────────────────────────────────────
        self.count_phase_bias_release: float = 0.0  # INERT (2026-09-04)
        self.count_phase_bias_ptb: float = 0.0      # INERT (2026-09-04)
        self.count_phase_bias_post_ruck: float = 0.0  # INERT (2026-09-04)
        self.count_phase_bias_open_play: float = 0.0  # INERT (2026-09-04)
        self.count_frac_max_short: float = 0.50
        self.count_split_hysteresis: int = 1

        # governor_width_active (structured-close advisory gate) moved to
        # defensive_line_shape_tuners.py.

        # ─────────────────────────────────────────────────────────
        # STEP‑1 GEOMETRY SMOOTH TIME (seconds)
        #
        # Unity-style smooth_damp smoothing time for geometry-driven
        # defensive movement during OPEN_PLAY.  Higher = slower/softer
        # convergence onto the role-slot target (no proportional-gain
        # snapping).  CHASE/SHOOT pursuit movement is unaffected.
        #
        # WIRED LIVE (2026-08-16): OpenPlayEngine stamps this value onto
        # every defender's ``_geometry_smooth_time_s`` every OPEN_PLAY
        # frame (alongside the max_defensive_speed_mps ceiling stamp), so
        # this tuner is the SINGLE source of truth for the smooth-damp
        # time used by Player._accelerate_toward_target().  Do NOT force
        # 0.0 — PTB-era suppression is handled by the phase gate in
        # Player._uses_smooth_damp(), not by zeroing this tuner.
        # ─────────────────────────────────────────────────────────
        self.geometry_smooth_time_s: float = 0.12

        # ─────────────────────────────────────────────────────────
        # PRE-TACKLE SLOWDOWN BAND (2026-08-16)
        #
        # Committed PRIMARY/SECONDARY lane tacklers decelerate inside
        # this Euclidean distance (metres) of the ball carrier before
        # TES, so the approach visibly tapers into the bite instead of
        # charging at line speed then snapping to a stop at the
        # 0.8 m pre-tackle stance.
        #
        #   - Inside pre_tackle_slowdown_band_m of the BC, the close
        #     writer scales move_speed down toward:
        #         speed = base * (speed_scale + (1 - speed_scale)
        #                          * dist_to_bc / band)
        #     so a 1.5 m gap runs at ~half speed and the final metre
        #     into the stance radius crawls.
        #   - The scale floors at pre_tackle_slowdown_scale so the
        #     defender still satisfies try_pre_tackle's closing/speed
        #     windows (bc_spd - 3 < def_spd < bc_spd + 2).
        #   - Distance-to-BC (NOT role-depth) is the signal: wingers
        #     are 10 m deep behind the ruck and must only slow once
        #     the carrier is genuinely close.
        # ─────────────────────────────────────────────────────────
        self.pre_tackle_slowdown_band_m: float = 4.0
        self.pre_tackle_slowdown_scale: float = 0.32

        # ─────────────────────────────────────────────────────────
        # TARGET STABILISER — GAP-PROPORTIONAL ALPHA (2026-08-16)
        #
        # The post-PTB role-depth settle (flat ruck+10 -> A/B/C/D
        # role depths) can be up to ~10 m.  At the stabiliser's fixed
        # alpha = 0.15 that takes ~1.2 s to reach 98% — the visible
        # "98% done -> jittery crawl".  When the geometry error is
        # large, alpha ramps to stabiliser_max_alpha so the settle
        # converges in a few frames; it decays back to the base
        # alpha near the target, preserving sub-30 cm jitter
        # filtering.
        # ─────────────────────────────────────────────────────────
        self.stabiliser_base_alpha: float = 0.15
        self.stabiliser_max_alpha: float = 0.8
        self.stabiliser_fast_band_m: float = 2.5

        # WIDTH RAILS REMOVED (2026-09-14, Option B): the 3-point width
        # curve is the sole width authority. See defensive_width.governed_width_m.

        # ─────────────────────────────────────────────────────────
        # STEP‑2 DEPTH ROLE TABLE — ALIASED TO WIDTH_OFFSETS (2026-08-09)
        #
        # Depth roles (A/B/C/D) override positional roles.  ROLE_DEPTH_M is
        # the legacy name for the geometry-driven WIDTH_OFFSETS table above;
        # both consumers read the same numbers.  These values are applied as
        # offsets to target_x in STEP‑2 / role movement.
        # ─────────────────────────────────────────────────────────

        # ─────────────────────────────────────────────────────────
        # POST-PTB RESET TUNERS (STEP‑03A‑08)
        #
        # Controls defensive line rebuild after a PTB completes.
        #
        # post_ptb_retreat_distance_m:
        #   Distance behind ruck_x for the flat blanket retreat phase.
        #   All defenders retreat to ruck_x + this value in Phase 1.
        #
        # post_ptb_phase1_duration_s:
        #   Duration of the blanket retreat phase (all defenders go to
        #   same depth behind the ruck).  During this phase, the line
        #   is a flat wall, preserving shape but not using role depths.
        #
        # post_ptb_phase2_duration_s:
        #   Duration of the settle phase where defenders spread from
        #   blanket distance into their role-depth positions
        #   (A/B closer to ruck, D/WINGER deeper).
        #
        # post_ptb_depth_buffer_m:
        #   Extra buffer added to role-depth offset during Phase 2
        #   settle, so defenders don't encroach on the ruck zone.
        #   The settle target is:
        #       ruck_x + ROLE_DEPTH_M[role] + depth_buffer
        # ─────────────────────────────────────────────────────────
        # NOTE (2026-08-16): post_ptb_retreat_distance_m and the two
        # phase-duration tuners are inert spec relics (no live consumers —
        # kept intentionally).  post_ptb_depth_buffer_m = 1.0 reduces the
        # ruck-encroachment buffer for the (rarely reached) role-depth
        # fallback writer; the visible post-PTB "accordion" is fixed by the
        # fast settle + PTB-era smooth-damp gate, not by this buffer.
        self.post_ptb_retreat_distance_m: float = 10.0
        self.post_ptb_phase1_duration_s: float = 0.5
        self.post_ptb_phase2_duration_s: float = 0.5
        self.post_ptb_depth_buffer_m: float = 1.0

        # ─────────────────────────────────────────────────────────
        # LATERAL (Y-AXIS) PRIMARY CLASSIFICATION THRESHOLD
        #
        # When world.ball_carrier is not None, classify_defender_depth()
        # uses this lateral distance on the Y-axis to classify defenders:
        #   PRIMARY:   abs(d.y - bc.y) <= PRIMARY_LATERAL_THRESHOLD
        #   SECONDARY: |dy| > threshold but depth check passes
        #   PASSIVE:   otherwise
        #
        # REDUCED July 2026 — was 5.0m, which still classified 3+ defenders
        # as PRIMARY (excessive).  With defenders typically 3–8m behind BC
        # in structured open play, a 5.0m lateral band captured too many.
        # At 3.0m, only the 1–2 defenders directly in front of BC qualify.
        #
        # Combined with SECONDARY_DEPTH_THRESHOLD=3.5, this yields the
        # intended distribution: 1 PRIMARY / 2 SECONDARY / rest PASSIVE.
        # PASSIVE defenders are the only ones who follow geometry
        # (lane_index, spacing_m, ROLE_DEPTH_M, anchor_x), so restoring
        # PASSIVE classification is critical for line shape.
        # ─────────────────────────────────────────────────────────
        self.PRIMARY_LATERAL_THRESHOLD: float = 3.0

        # ─────────────────────────────────────────────────────────
        # SECONDARY DEPTH (X-AXIS) THRESHOLD
        #
        # Used in classify_defender_depth() to restore PASSIVE classification.
        # Defenders beyond this X distance from BC are PASSIVE, not SECONDARY.
        # Combined with PRIMARY_LATERAL_THRESHOLD (Y-axis), this creates the
        # decision diamond: near laterally = PRIMARY, near in depth = SECONDARY,
        # otherwise = PASSIVE.
        #
        # REDUCED July 2026 — was 10.0m, which still captured all defenders
        # behind the BC (typical dx 3–8m in structured open play).  At 3.5m,
        # only the 2 closest defenders in X depth qualify as SECONDARY.
        #
        # Combined with PRIMARY_LATERAL_THRESHOLD=3.0 (Y-axis), this creates
        # the decision diamond: near laterally = PRIMARY, near in depth =
        # SECONDARY, beyond both = PASSIVE.  PASSIVE defenders are the ones
        # who follow geometry pipeline (anchor_x, lane_y, spacing) and hold
        # the defensive line shape.
        # ─────────────────────────────────────────────────────────
        self.SECONDARY_DEPTH_THRESHOLD: float = 3.5

        # ─────────────────────────────────────────────────────────
        # TACKLE RADIUS (global tuner — single unified value)
        # Distance at which a defender initiates a tackle.
        # Must match OpenPlayConfig.tackle_radius_m (1.5).
        # ─────────────────────────────────────────────────────────
        self.tackle_radius_m = 1.5

        # TACKLE-STATE GATE radius (2026-08-23).  Must match
        # OpenPlayEngine.RUCK_BUBBLE_RADIUS_M (3.5) — the ruck bubble covers
        # both markers standing at ruck_x+1 / ruck_x+2.  Single source lives
        # in game/defence/defence_lvl_1/tackle_state_gate.py.
        self.tackle_state_radius_m = 3.5

        # ─────────────────────────────────────────────────────────
        # DIVE TACKLE RADIUS (metres)
        # Distance at which a defender may attempt a diving tackle.
        # Must be >= tackle_radius_m (diving is a last-ditch option
        # from further out).
        # ─────────────────────────────────────────────────────────
        self.dive_radius_m = 3.0

        # ─────────────────────────────────────────────────────────
        # DEFENDER ACTIVATION RADIUS (metres)
        # Any defender within this distance of the BALL is considered
        # ACTIVE and may engage in HOLD/STEP_UP/CHASE behaviour.
        # Defenders outside this radius remain in passive retreat.
        # This prevents early/late engagement during kicks, passes,
        # and broken play.
        # ─────────────────────────────────────────────────────────
        self.defender_activation_radius_m: float = 20.0

        # ─────────────────────────────────────────────────────────
        # MARKER FREEZE AFTER PTB (seconds)
        # Markers are frozen in place for this long after DH picks up.
        # This prevents markers from collapsing the ruck before DH
        # has time to pass/run.
        # ─────────────────────────────────────────────────────────
        # NOTE (2026-08-16): these seed the RELEASE-entry marker freeze
        # timers (informational — RELEASE treats markers as legal movers).
        # The ACTUAL post-PTB marker lock is world.marker_lock_duration_s
        # + OpenPlayEngine.MARKER_FREEZE_DURATION_S (set to 0.3s).
        self.MARKER_A_FREEZE_AFTER_PTB: float = 0.3
        self.MARKER_B_FREEZE_AFTER_PTB: float = 0.3

        # ─────────────────────────────────────────────────────────
        # ENGAGEMENT STEERING (Phase D.2)
        # ─────────────────────────────────────────────────────────
        self.engagement_offset_m: float = 2.0
        self.ROLE_STEER_FACTOR = {
            "A": 1.2,
            "B": 1.1,
            "C": 1.0,
            "D": 0.8,
            "CENTRE": 1.0,
            "WINGER": 0.9,
        }

        # ─────────────────────────────────────────────────────────
        # CHASE / COMPRESSION TUNERS (OPEN_PLAY)
        # ---------------------------------------------------------
        # PRIMARY chasers: up to chase_max_primary defenders within
        # chase_primary_radius_m of the BC get target_x = bc.x - 1.0
        # and full chase speed toward BC.
        self.chase_primary_radius_m: float = 8.0
        self.chase_max_primary: int = 2

        # SECONDARY chasers: from remaining eligible defenders, up to
        # chase_max_secondary within chase_secondary_radius_m get
        # target_x = bc.x + 1.0..3.0 with moderate speed toward BC.
        self.chase_secondary_radius_m: float = 14.0
        self.chase_max_secondary: int = 3

        # compression_band_width_m / compression_strength (passive chase
        # Y compression) moved to defensive_line_shape_tuners.py.

        # Speed scale applied to passive defenders' movement speed
        # during OPEN_PLAY (slower retreat than active chasers).
        self.passive_retreat_speed_scale: float = 0.7

        # ─────────────────────────────────────────────────────────
        # RUCK-ADJACENT A/Y-OFFSET TUNER — DEPRECATED & INERT
        # (2026-08-22, ANCHOR LAW)
        #
        # SUPERSEDED by the pure function
        # defensive_split_spacing.ruck_adjacent_anchor(ruck_y, side),
        # which returns EXACTLY ruck_y ∓/± 2.0 m and may not be modified
        # by tuners, width factors, interpolation, count-split logic,
        # chase targets, or sideline clamps.
        #
        # This attribute is kept ONLY for backward compatibility with
        # old verifier stubs that read it via getattr(..., 2.0).  It has
        # ZERO live readers in the engine — changing it has NO effect on
        # the ruck-adjacent anchor.
        # ─────────────────────────────────────────────────────────
        self.RUCK_ADJACENT_A_Y_OFFSET_M: float = 2.0  # DEPRECATED / inert
        # ENGAGEMENT_SPACING_Y_OFFSET moved to
        # defensive_line_shape_tuners.py.

        # ─────────────────────────────────────────────────────────
        # MOVEMENT MODE DECISION LAYER (MMDL) THRESHOLDS
        #
        # Thresholds driving the MovementState decision tree in
        # movement_mode_decision.py (structured OPEN_PLAY only).
        #
        # engage_distance_m:
        #   PRIMARY/SECONDARY defenders within this Euclidean distance of
        #   the BC enter the commit-window branch (SLIDE vs SHOOT vs CHASE).
        # commit_distance_m:
        #   Within this distance AND laterally aligned, the defender SHOOTs
        #   (explosive forward jam) toward the BC.
        # lateral_slide_threshold_m:
        #   |d.y - bc.y| above this (within engage distance) -> SLIDE to
        #   maintain the tackle angle instead of committing.
        # deep_passive_distance_m:
        #   PASSIVE defender with bc.x - d.x beyond this -> HOLD the line
        #   instead of drifting laterally.
        # ─────────────────────────────────────────────────────────
        self.engage_distance_m: float = 20.0
        self.commit_distance_m: float = 8.0
        self.lateral_slide_threshold_m: float = 3.0
        self.deep_passive_distance_m: float = 15.0
        # PURSUIT/COMMIT HARD OVERRIDE (2026-09-02): any non-marker,
        # non-fullback, non-tackle-state, non-TES line defender within this
        # Euclidean distance of the ball carrier is forced onto a BC-relative
        # pursuit/commit target + CHASE/SHOOT state + full ceiling speed,
        # overriding all spacing/role/retreat/fallback logic until CONTACT
        # (TES start) fires.  Mirrors the 12m strike distance used by
        # _should_be_aggressive; exposed here so both the MMDL gate and the
        # target override share ONE radius.
        self.pursuit_commit_radius_m: float = 12.0

        # Forward line speed for PASSIVE defenders in OPEN_PLAY.
        # Drives vx = (target_x - x) * scale each frame, pulling
        # PASSIVE defenders forward toward their role-depth baseline.
        # Over multiple frames this reduces dx until the defender
        # enters SECONDARY/PRIMARY chase radius.
        self.passive_line_speed_scale: float = 0.25

        # ─────────────────────────────────────────────────────────
        # PRE-TACKLE TRIGGER TUNERS (OPEN_PLAY)
        # ---------------------------------------------------------
        # Distance from BC at which a PRIMARY defender can start a tackle.
        self.pre_tackle_trigger_radius_m: float = 2.0
        # Max distance for SECONDARY assist tackle (slightly further out).
        self.pre_tackle_secondary_radius_m: float = 2.5
        # Minimum defender speed (m/s) to count as an active tackle attempt.
        self.pre_tackle_min_speed_mps: float = 1.0

        # ─────────────────────────────────────────────────────────
        # INTERCEPT BEHAVIOUR TUNERS (2026-08-15, spec §1/§3)
        # ---------------------------------------------------------
        # pre_tackle_stance_radius_m: PRIMARY lane tacklers within this
        # distance of the BC stop moving and square up (pre-tackle stance).
        # lane_tackler_switch_hysteresis_m: an incumbent PRIMARY keeps the
        # lane as long as the new minimum-X candidate is within this margin
        # (prevents frame-to-frame PRIMARY flip when two defenders are
        # level on X).
        self.pre_tackle_stance_radius_m: float = 0.8
        self.lane_tackler_switch_hysteresis_m: float = 0.5
        # lane_side_hysteresis_m: the per-side lane split is `d.y` vs
        # `bc.y`.  A defender whose Y sits within this margin of the
        # carrier's lane is effectively ON the lane, and a centimetre of BC
        # Y drift flips its side — which flips the per-side PRIMARY owner
        # and re-roles the whole side to PASSIVE frame-to-frame.  A side is
        # therefore held from the previous frame unless the defender has
        # clearly (beyond this margin) crossed the carrier's lane.  Mirrors
        # the persistent-side law the depth-role system already uses
        # (defensive_depth_roles.SIDE_SWAP_HYSTERESIS_M) rather than
        # re-deriving the side from live position every frame.
        self.lane_side_hysteresis_m: float = 0.35


        # ─────────────────────────────────────────────────────────
        # TES CONTACT PHYSICS (BC FAVOURITISM)
        # These tuners control how many metres per second the BC
        # gains forward (or the defender is dragged backward) during
        # the TES contact phase.
        #
        # Typical tackle_duration_s = 1.0-2.0s (neutral).
        #   bc_contact_gain_mps * duration ≈ 3-4m  (target)
        #   def_contact_drag_mps * duration ≈ 0.5-1m (separates pair)
        #
        # Net effect: BC makes ~3-4 post-contact metres.
        # ─────────────────────────────────────────────────────────
        self.bc_contact_gain_mps: float = 2.5    # forward metres/sec for BC
        self.def_contact_drag_mps: float = 0.8   # backward drag on defender

        # ─────────────────────────────────────────────────────────
        # TES DRIVE / DOMINANCE TUNERS
        # ─────────────────────────────────────────────────────────
        self.drive_waveform_scale: float = 1.0      # scale for DRIVE waveform forces
        self.dominance_decay_rate: float = 0.5      # rate dominance_score falls under pressure

        # ─────────────────────────────────────────────────────────
        # TES DRIVE-DEPTH TRACK CANARY (2026-09-06)
        #
        # When True (canary; DEFAULT now ON 2026-09-08-revert), during the
        # TES drive (CONTACT / DRIVE /
        # DOMINANCE) the depth-locked line bodies (DEF_LINE_READY /
        # DEF_CHASE / DEF_RETREAT_10) track the LIVE ball carrier in DEPTH
        # (X = bc.x + WIDTH_OFFSETS[depth_role], refreshed each frame) so the
        # defensive line moves back with the driven carrier.  DEF_LINE_READY /
        # DEF_CHASE also hold their LATERAL (Y) at drive entry (depth_lock_y)
        # so the line keeps its shape (no lateral blow-out); DEF_RETREAT_10 keeps
        # Y free so the wall can slide.
        #
        # When False, the v2 hard-freeze is restored: DEF_LINE_READY /
        # DEF_CHASE stay hard-frozen in all TES phases (the line holds its
        # exact depth and lateral position), and only DEF_RETREAT_10 remains
        # depth-locked with a static X pin.  Read via
        # world.rules.movement_contracts.drive_depth_track_enabled().
        # ─────────────────────────────────────────────────────────
        # DEFAULT ON (2026-09-08-revert): the depth-track experiment is the
        # live default again — DEF_LINE_READY / DEF_CHASE are DEPTH-LOCKED
        # (not hard-frozen) during the TES drive, so they track the driven
        # carrier.  The DH + line jitter is suppressed by the TES drive
        # authority (the depth-lock glide as the sole integrator) instead of
        # hard-freezing the line.  The flag stays a dark-safe A/B canary;
        # flipping it False restores the v2 hard-freeze.
        self.tes_drive_depth_track_enabled: bool = True

        # ─────────────────────────────────────────────────────────
        # POST-TES DRIVE-END SETTLE WINDOW (2026-09-06)
        #
        # After the TES drive ends (TACKLE_DOMINANCE → RELEASE) the ruck
        # forms at (ruck_x, ruck_y) and the post-TES writers (ruck-pin
        # position snap, spacing/width clamps) would snap bodies back to
        # ruck-relative targets on the very first RELEASE frame — the
        # teleporting-around-the-ruck symptom (the drive-era lateral /
        # depth positions were previously masked by the old hard-freeze).
        #
        # ``post_tes_settle_frames`` gates those snaps from DOMINANCE→RELEASE
        # through POST_RUCK_OPEN_PLAY (the whole post-drive reform span) so
        # defenders settle naturally (glide toward the ruck-relative target
        # via the movement integrator) instead of teleporting.  The window
        # is phase-aware: the three snap writers consult
        # ``post_tes_settle_active(world)``, which is active during
        # RELEASE / PTB_* / POST_RUCK_OPEN_PLAY and disengages on the first
        # OPEN_PLAY frame.  Any non-zero value = enabled; 0 = disabled
        # (legacy behaviour, byte-identical).  Read via
        # world.post_tes_settle_frames (armed at DOMINANCE→RELEASE) and
        # world.post_tes_settle_enabled (the "armed this cycle" latch).
        # ─────────────────────────────────────────────────────────
        self.post_tes_settle_frames: int = 5

        # ─────────────────────────────────────────────────────────
        # MULTI-TACKLER TUNERS
        # ─────────────────────────────────────────────────────────
        self.multi_tackler_join_delay: float = 0.2   # min time before extra tacklers can join
        self.multi_tackler_force_scale: float = 0.7  # extra force from additional tacklers

        # ─────────────────────────────────────────────────────────
        # RELEASE PEEL SPEED
        # ─────────────────────────────────────────────────────────
        self.release_peel_speed: float = 2.0  # speed tacklers peel off in RELEASE

        # ─────────────────────────────────────────────────────────
        # PRE-TACKLE SUCCESS / BREAK TUNERS (Step‑02A‑03)
        # ─────────────────────────────────────────────────────────
        self.normal_tackle_success_factor: float = 0.70
        self.dive_success_factor: float = 0.40
        self.bc_fend_skill_factor: float = 0.15
        self.bc_slip_skill_factor: float = 0.15
        self.speed_diff_slip_factor: float = 0.10
        self.behind_angle_penalty: float = 0.20
        self.front_angle_bonus: float = 0.10
        self.missed_tackle_recovery_time: float = 1.0
        self.dive_fail_recovery_time: float = 1.5

        # ─────────────────────────────────────────────────────────
        # STEP‑02A‑10 — BC lateral slip offset on break (metres)
        # ─────────────────────────────────────────────────────────
        self.break_slip_offset_m: float = 0.10  # 10 cm sidestep

        # ─────────────────────────────────────────────────────────
        # RADIUS HIERARCHY (single source of truth)
        # Must satisfy:
        #   collision_radius < bc_arrival_radius < tackle_radius
        #   tackle_radius <= containment_radius <= retreat_radius <= dh_arrival_radius <= ruck_radius
        # ─────────────────────────────────────────────────────────
        self.collision_radius_m: float = 0.55
        self.bc_arrival_radius_m: float = 0.80
        # tackle_radius_m is defined above as 1.5
        self.containment_radius_m: float = 1.50
        self.retreat_radius_m: float = 1.50
        self.dh_arrival_radius_m: float = 1.50
        self.ruck_radius_m: float = 2.00

        # ═══════════════════════════════════════════════════════════════
        # STEP‑03A‑10 — DEFENSIVE TIMING & REACTION CURVES
        # ═══════════════════════════════════════════════════════════════

        # ── 1. REACTION DELAY ──
        # Base reaction delay (seconds) — defender waits this long before reacting
        # to BC/FR direction/position changes.
        self.reaction_base_s: float = 0.15
        # Role multiplier on reaction_base_s: markers fastest, PASSIVE slowest.
        self.reaction_role_factor: dict = {
            "MARKER": 0.3,
            "PRIMARY": 0.5,
            "SECONDARY": 0.7,
            "PASSIVE": 1.0,
        }
        # Extra seconds per |lane_index| step from centre.
        self.reaction_lane_offset_s: float = 0.05
        # Extra seconds per depth_class: PRIMARY has 0, PASSIVE has most.
        self.reaction_depth_offset_s: dict = {
            "PRIMARY": 0.0,
            "SECONDARY": 0.1,
            "PASSIVE": 0.2,
        }
        # BC position change (metres X) that triggers a full reaction delay.
        self.reaction_position_change_m: float = 2.0
        # BC lateral change (metres Y) that triggers a full reaction delay.
        self.reaction_lateral_change_m: float = 1.5
        # BC acceleration/deceleration (m/s^2) that triggers reaction.
        self.reaction_accel_threshold_mps: float = 3.0

        # ── 2. STAGGERED STEP-UP ──
        # Seconds per |lane_index| step — centre (0) has 0, edges have more.
        self.step_stagger_lane_factor: float = 0.03
        # Extra seconds per depth_class for step-up stagger.
        self.step_stagger_depth_offset: dict = {
            "PRIMARY": 0.0,
            "SECONDARY": 0.05,
            "PASSIVE": 0.1,
        }
        # BC must be within this X distance (metres) of anchor to activate staggering.
        self.step_stagger_activation_range_m: float = 15.0
        # BC beyond this X distance deactivates staggering.
        self.step_stagger_deactivation_m: float = 20.0

        # ── 3. RETREAT HESITATION ──
        # Seconds of hesitation before defenders can backpedal after BC breaks the line.
        self.retreat_delay_s: float = 0.3
        # BC must be anchor_x + this much (metres) to trigger retreat hesitation.
        self.retreat_threshold_m: float = 2.0

        # ── 4. LATERAL HESITATION (ANTI-OVERCOMMIT) ──
        # Ignore BC lateral changes below this (metres).
        self.lateral_small_threshold_m: float = 0.5
        # BC lateral change above this (metres) → immediate reaction, no hesitation.
        self.lateral_moderate_threshold_m: float = 2.0
        # Hesitation duration (seconds) for moderate BC lateral changes.
        self.lateral_hesitation_s: float = 0.15

        # ── 5. COMPRESSION TIMING (moved to
        #    defensive_line_shape_tuners.py) ──

        # ── 6. MARKER & DUMMY HALF DELAYS ──
        # Extra hesitation (seconds) when BC is stationary.
        self.marker_stationary_hesitation_s: float = 0.05
        # DH defender delay after PTB before stepping into line.
        self.dh_defender_ptb_delay_s: float = 0.2
        # BC speed (m/s) below this is considered stationary.
        self.bc_stationary_speed_threshold_mps: float = 0.5

        # ── 7. POST-RUCK QUIET WINDOW (STAGE 3F) ──
        # Wall-clock seconds after a PTB completion during which the whole
        # SECONDARY+PASSIVE line keeps its timing locks cleared.  Replaces
        # the old frame-count gate `frames_since_ptb < 30` (30 frames @ 60
        # fps), which drifted with tick rate and made spacing/pacing inherit
        # nondeterministic frame drift.
        self.post_ruck_quiet_window_s: float = 0.5

        # ─────────────────────────────────────────────────────────
        # BALL-SIDE COLLAPSE + DYNAMIC DEPTH ROLES (TASK X, 2026-08-26)
        #
        # At every ruck pivot (new tackle site) the depth-role system is
        # REFITTED from scratch off the ball-carrier side, the ruck
        # context and the settled positions — replacing the static
        # persistent-role inheritance that corrupted CENTRE/WINGER and
        # middle/edge lanes.  The count-driven side split additionally
        # collapses up to ``collapse_ball_side_bias`` extra defenders
        # onto the ball side whenever the carrier sits at least
        # ``collapse_min_bc_offset_m`` from ruck_y.
        #
        # pivot_ruck_move_threshold_m:
        #   Ruck-point movement (either axis) that counts as a NEW pivot
        #   and therefore re-arms the dynamic refit.  Sub-threshold
        #   jitter within one ruck never re-fires it.
        # collapse_ball_side_bias:
        #   Extra defenders shifted onto the ball side by the count
        #   split (0 disables ball-side collapse entirely).
        # collapse_min_bc_offset_m:
        #   Minimum |bc.y − ruck_y| before the bias applies; a centred
        #   carrier keeps the symmetric legacy split.
        # ─────────────────────────────────────────────────────────
        self.pivot_ruck_move_threshold_m: float = 0.5
        self.collapse_ball_side_bias: int = 1
        self.collapse_min_bc_offset_m: float = 4.0

        # ─────────────────────────────────────────────────────────
        # EDGE RELEASE — CENTRE/WINGER → A/B/C/D CONVERSION (TASK XI,
        # 2026-08-26)
        #
        # When the ball carrier is deep enough toward one side of the
        # ruck that positional identity no longer matters, the
        # CENTRE/WINGER slots ON THE BALL SIDE dissolve and their holders
        # are reinserted into the interior depth stack by pure Y-axis
        # ordering (closest-to-ruck → A, next B, then C/D).  The weak
        # side keeps its canonical edge structure.
        #
        # collapse_edge_release_enabled:
        #   Master gate (False restores the legacy always-locked edge).
        # collapse_edge_release_offset_m:
        #   Minimum |bc.y − ruck_y| before the ball-side edge releases
        #   (enter threshold; effectively floored at
        #   collapse_min_bc_offset_m so the release can never fire
        #   before the collapse itself is armed).
        # collapse_edge_release_hysteresis_m:
        #   Hysteresis margin — once active, the release persists until
        #   |bc.y − ruck_y| falls below (enter − hysteresis), so a
        #   carrier hovering around the threshold cannot flap the edge
        #   structure frame-to-frame.  A BC crossing to the other side
        #   while active is a transition: the old side restores its
        #   canonical W/C and the new side releases.
        # ─────────────────────────────────────────────────────────
        self.collapse_edge_release_enabled: bool = True
        self.collapse_edge_release_offset_m: float = 9.0
        self.collapse_edge_release_hysteresis_m: float = 2.0

        # ─────────────────────────────────────────────────────────
        # PRE-TACKLE ATTEMPT MODULE (TASK XIV, 2026-08-26)
        #
        # Master gate for the new pre-tackle attempt authority
        # (game/defence/defence_lvl_1/pretackle_attempt.py).
        # When True (LIVE / default), DefenceManager.update_tes_drive()
        # owns the non-tackler DEF_LINE_READY bodies with the authority:
        # they switch to stable tackle-phase anchors, freeze/slow
        # contracts and deterministic attempt states instead of chasing
        # the driven carrier — REPLACING the legacy BC-SHADOW re-anchor +
        # soft-follow (which is intentionally superseded).  When False
        # (override), the verbatim legacy BC-SHADOW path runs
        # byte-identical (verified by verify_pretackle_attempt PART 1).
        #
        # pretackle_slow_speed_mps — capped drift speed for SLOW/HOLD
        #   bodies converging to their frozen anchor.
        # pretackle_freeze_radius_m — arrival deadband: inside this the
        #   body freezes (one-way; no re-arm inside a drive).
        # pretackle_follow_smooth — EMA alpha on the slow-follow velocity
        #   (kills one-frame velocity discontinuity on eligibility toggles).
        # pretackle_commit_speed_mps — CLOSING speed for the per-side
        #   COMMIT attempt body toward its frozen commit point.
        # pretackle_plant_radius_m — distance from the frozen commit point
        #   at which a COMMIT body plants (freeze + square-up).
        # pretackle_slowdown_band_m — Euclidean band around the commit
        #   point in which the commit speed tapers toward the plant.
        # pretackle_slowdown_scale — floor multiplier inside the band.
        # pretackle_commit_depth_offset_m — fixed +X offset applied to
        #   the frozen commit point (BC position at DRIVE entry).
        # pretackle_commit_lateral_offset_m — per-side, the commit point
        #   is offset in Y by this * side so the LEFT and RIGHT COMMIT
        #   bodies never converge onto one point (anti-stack guarantee).
        # ─────────────────────────────────────────────────────────
        self.pretackle_attempt_enabled: bool = True
        # TACKLE-BEGIN GLIDE (2026-09-07): when TACKLE_INIT fires only
        # explicitly committed tacklers may snap into TES/tackle geometry.
        # Every other defender treats role depth as a DESTINATION and glides
        # toward it under its current movement mode, with per-frame movement
        # capped at TACKLE_BEGIN_GLIDE_SPEED_MPS * dt (role_depth_glide.py).
        # Feature gate: when False the authority is a strict no-op.
        self.tackle_begin_glide_enabled: bool = True

        # ─────────────────────────────────────────────────────────────
        # TWO-RADIUS DEFENSIVE-LINE ATTACH FIX (2026-09-07)
        #
        # The defensive-line blowout was driven by the TES window pushing
        # non-committed line defenders out of their tackle slot band.  The
        # fix introduces a two-radius model:
        #
        #   tackle_assist_radius_m (R_assist, default 5.0):
        #       A defender inside this radius RESERVES an attach slot and
        #       is smoothly nudged inward toward the slot / ball carrier —
        #       it is NOT yet committed (no snap).
        #
        #   tackle_snap_radius_m (R_tackle, default 2.0):
        #       The ONLY radius that permits a defender to SNAP / ATTACH
        #       into the TES tackle geometry.  Inside R_tackle a defender
        #       may snap; outside it (but inside R_assist) it only
        #       reserves + glides inward.
        #
        # This keeps the defensive line from fragmenting: a reserve-and-
        # nudge band means defendnenn approach the pile as a coherent line
        # rather than individual defenders teleporting onto slots while
        # width writers shove the rest of the line wide.
        #
        # attach_slot_reserve_enabled:
        #   Master gate for the slot-reservation pass.  When True a
        #   defender within R_assist claims a reserved attach slot via the
        #   frame-scoped registry so two defenders can never race one
        #   slot.  False = byte-identical legacy reservation behaviour.
        #
        # joiner_grace_s:
        #   Grace window (s, inside 0.05-0.2) granted to a freshly-joined
        #   tackler so it cannot be raced by another joiner for the same
        #   slot during the window.  Decremented per frame.
        #
        # defender_contact_damp:
        #   Defender-side velocity damping applied on first TES contact
        #   entry.  Neutralises the contact-spread spike that previously
        #   threw the defensive line wide at the moment of contact.
        #
        # anticipation_brace_enabled / anticipation_brace_gain_m:
        #   Anticipation-brace canary + gain.  The line prebraces toward
        #   the ball-carrier lane in the drive phases (OPEN_PLAY + TES)
        #   with a small role-scaled lateral shift so the TES contact-
        #   spread spike is absorbed before it lands.  Gated behind
        #   anticipation_brace_enabled (default True; False = no brace).
        # ─────────────────────────────────────────────────────────────
        self.tackle_assist_radius_m: float = 5.0
        self.tackle_snap_radius_m: float = 2.0
        self.attach_slot_reserve_enabled: bool = True
        self.joiner_grace_s: float = 0.1
        self.defender_contact_damp: float = 0.1
        self.anticipation_brace_enabled: bool = True
        self.anticipation_brace_gain_m: float = 0.5
        self.pretackle_slow_speed_mps: float = 2.5
        self.pretackle_freeze_radius_m: float = 0.15
        self.pretackle_follow_smooth: float = 0.6
        self.pretackle_commit_speed_mps: float = 4.0
        self.pretackle_plant_radius_m: float = 0.8
        self.pretackle_slowdown_band_m: float = 4.0
        self.pretackle_slowdown_scale: float = 0.32
        self.pretackle_commit_depth_offset_m: float = 1.0
        self.pretackle_commit_lateral_offset_m: float = 1.0

        # ─────────────────────────────────────────────────────────
        # SINGLE CANONICAL SLOWDOWN PAIR (2026-09-15)
        #
        # SPEED-AUTHORITY CONSOLIDATION.  Two near-identically-named
        # slowdown tuner pairs existed:
        #
        #   pre_tackle_slowdown_band_m / _scale   (underscore after "pre")
        #       -> published to world.pre_tackle_slowdown_* and read by
        #          intercept_tacklers.apply_pre_tackle_stance()
        #   pretackle_slowdown_band_m / _scale    (no underscore)
        #       -> read by pre_tackle_authority._phase_tuners()
        #
        # The last move_speed writer of the OPEN_PLAY frame read the FIRST
        # pair while the pre-tackle authority read the SECOND, so tuning
        # one pair could not move the other taper at all -- the "tuning
        # blind" defect.  There is now ONE canonical pair (``pretackle_*``,
        # matching pretackle_attempt.py and the unified authority) and the
        # ``pre_tackle_*`` names are ALIASES assigned from it below.  Never
        # introduce an independent value on the alias names.
        # ─────────────────────────────────────────────────────────
        self.pre_tackle_slowdown_band_m = self.pretackle_slowdown_band_m
        self.pre_tackle_slowdown_scale = self.pretackle_slowdown_scale

        # ─────────────────────────────────────────────────────────
        # PRE-TACKLE CORRIDOR (2026-09-15)
        #
        # The corridor is the span in which a COMMITTED tackler is owned by
        # pre_tackle_authority ALONE: it is the sole writer of
        # target_x/target_y, move_speed, vx/vy, movement_state and the plant
        # verdict, and the sole integrator of position.
        #
        # TWO-RADIUS MODEL (fixes "the tackle transitions before the
        # slowdown curve has time to act"):
        #
        #   pre_tackle_trigger_radius_m (2.0)  -- PROMOTE radius.
        #       A body may only BECOME the per-side COMMIT owner inside this
        #       distance (preserves the anti-early-commit / anti-wrong-side
        #       protection).
        #
        #   pretackle_corridor_radius_m (4.5)  -- HOLD radius.
        #       An ALREADY-committed body STAYS owned out to this distance,
        #       so the slowdown band is genuinely available before the
        #       tackle arms at tackle_radius_m (1.5).
        #
        # The effective radius is max(pretackle_corridor_radius_m,
        # pretackle_slowdown_band_m + pretackle_corridor_margin_m) so
        # widening the band automatically widens the window; the tuner is
        # the floor, never a silent cap.  assert_radii() enforces
        # plant < tackle_arm < corridor and band <= corridor.
        # ─────────────────────────────────────────────────────────
        self.pretackle_corridor_radius_m: float = 4.5
        # Margin (m) added to the slowdown band when deriving the corridor
        # radius, so the band is fully inside the corridor with room for the
        # approach to be observed.
        self.pretackle_corridor_margin_m: float = 0.5
        # Handoff gate: when True the primary's TES PlayerContext is built
        # from the corridor's stamped contact velocity
        # (pretackle_contact_vx/vy) instead of the live vx/vy.  False =
        # byte-identical legacy (TES reads live velocity).
        self.pretackle_handoff_enabled: bool = True
        # Corridor speed-ownership gate: when True the corridor is the sole
        # move_speed/target writer for corridor-owned bodies and the MMDL /
        # tackler-commit / pursuit-commit / stance writers yield to it.
        # False = byte-identical legacy fragmentation (A/B canary).
        self.pretackle_corridor_owns_speed: bool = True

        # ---------------------------------------------------------
        # FRONT-HOLD / FLAT-FOOTED CORRIDOR DEFENCE (2026-10-03)
        #
        # THE DEFECT THIS REMOVES ("defenders going in to tackle a ball
        # carrier running at them still come in too fast and do not get in
        # front of the carrier").  Two coupled behaviours:
        #
        #   (A) FRONT-HOLD GEOMETRY.  The BC-closing target for a defender
        #       the carrier is running TOWARD (the defender sits AHEAD of the
        #       carrier in X) used to be aimed BEHIND the carrier
        #       (bc.x - offset), so the body back-pedalled past the carrier
        #       instead of posting up in its path.  With front-hold ON an
        #       ahead defender HOLDS ITS GROUND in front of the carrier
        #       (tx = max(d.x, bc.x + front_offset) -- it never retreats
        #       behind the carrier and never charges past the stand-off) and
        #       slides LATERALLY onto the carrier's lane (ty = bc.y + bracket
        #       * side), so the pair of front defenders closes the carrier's
        #       corridor instead of being walked through.
        #
        #   (B) HEAD-ON FLAT-FOOT PLANT.  A defender holding in front of a
        #       carrier running at it plants (a genuine stationary square-up:
        #       zero velocity + HOLD) once the CARRIER ITSELF is inside
        #       pretackle_headon_plant_radius_m (chosen just outside the
        #       tackle arm) at the softer pretackle_headon_commit_speed_mps
        #       approach pace.  A genuinely planted body routes to
        #       SQUARE_UP_ENGAGE / COMMIT_PLANTED (CLEAN_ENGAGE) and SKIPS
        #       the closing-vector evasion checks in pretackle_classifier /
        #       pretackle_machine (BC-outrun / defender-overshoot / momentum),
        #       which is what "flat-footed raises the tackle chance" means in
        #       this codebase.  The body no longer arrives as a fast CHASE
        #       and loses the overshoot reject.
        #
        # MASTER CANARY: pretackle_front_hold_enabled = False restores the
        # byte-identical legacy geometry (ahead -> behind the carrier) and the
        # legacy plant / speed tuning.
        # ---------------------------------------------------------
        self.pretackle_front_hold_enabled: bool = True
        # Stand-off (m) the front defender holds AHEAD of the carrier: it
        # posts up at bc.x + this on the carrier's lane.  Attack runs +X.
        self.pretackle_front_offset_m: float = 0.6
        # Per-side lateral bracket (m) so the two per-side front defenders are
        # NOT stacked on one point (anti-stack law) while still reading as
        # "on the carrier's lane": they bracket bc.y +/- this.
        self.pretackle_front_lateral_offset_m: float = 0.3
        # HEAD-ON PLANT: soften the approach and plant just before contact.
        self.pretackle_headon_plant_enabled: bool = True
        self.pretackle_headon_commit_speed_mps: float = 2.0
        # Plant radius for the head-on case.  MUST exceed tackle_radius_m
        # (1.5) so the body is already planted when TES arms; otherwise TES
        # fires while the body is still a fast CHASE and the outrun / overshoot
        # evasion rejects the tackle.
        self.pretackle_headon_plant_radius_m: float = 1.8

        # ─────────────────────────────────────────────────────────
        # PHASE-GATED BEHAVIOURAL TUNING (Stage 5, 2026-09-04)
        #
        # The unified pre-tackle authority (pre_tackle_authority.py)
        # resolves the current game sub-state ONCE per frame and loads
        # that phase profile's behavioural tuner overrides instead of the
        # global scalars above.
        #
        # ``pretackle_phase_tuners`` is a dict keyed by canonical phase
        # key (OPEN_PLAY / TACKLE_INIT / TACKLE_CONTACT / TACKLE_DRIVE /
        # TACKLE_DOMINANCE / TACKLE_RELEASE / PTB / POST_RUCK_OPEN_PLAY /
        # DEFAULT).  Each entry is a dict of tuner overrides keyed by the
        # same attribute names as the scalars above.
        #
        # DEFAULT EMPTY: every phase profile falls back to the global
        # scalars, so behaviour is byte-identical until a phase profile is
        # explicitly set.  Stage 5 is the tuning ARCHITECTURE, not a
        # tuning change — no phase profile seeds new behaviour here.
        #
        # Example override (TACKLE_DRIVE):
        #   self.pretackle_phase_tuners = {
        #       "TACKLE_DRIVE": {
        #           "pretackle_commit_speed_mps": 3.0,
        #           "pretackle_commit_lateral_offset_m": 0.8,
        #       },
        #   }
        # ─────────────────────────────────────────────────────────
        self.pretackle_phase_tuners: dict[str, dict] = {}

        # ─────────────────────────────────────────────────────────
        # PHASE-GATED ROLE-AWARE ANTICIPATION LAYER (Stage 9, 2026-09-05)
        #
        # A controlled lateral pre-shift of the defensive line toward the
        # predicted ball / threat lane during OPEN_PLAY and TES.  The shift
        # is phase-gated (is_drive_phase), band-limited to nearby defenders,
        # role-scaled, and clamped by slot hierarchy so spacing is never
        # destabilised and the WINGER never drifts.
        #
        # anticipation_enabled:
        #   Master gate.  True = the anticipation layer runs inside
        #   run_defensive_pipeline (phase-gated by is_drive_phase so it only
        #   applies during OPEN_PLAY + TES).  False = byte-identical legacy
        #   spacing (no pre-shift).
        #
        #   LIVE (2026-09-05): flipped on after the full regression suite
        #   passed with the layer enabled.  The pre-shift is phase-gated by
        #   is_drive_phase (OPEN_PLAY + TES) so it only applies during the
        #   drive; it is band-limited, role-scaled, and slot-clamped so it
        #   never destabilises spacing or drifts the WINGER.  Flipping back
        #   to False restores the byte-identical legacy spacing path.
        #
        # anticipation_lead_time_s:
        #   Horizon (s) for the ball-carrier velocity lead projection that
        #   predicts the threat lane:  threat_y = bc.y + bc.vy * lead_time_s.
        #
        # anticipation_band_m:
        #   Lateral band (m) around the threat lane.  A defender whose base
        #   spacing Y is beyond this band receives NO shift (band-limited to
        #   nearby defenders); inside the band the shift falls off linearly
        #   to zero at the edge.
        #
        # anticipation_max_gain_m:
        #   Maximum lateral pre-shift magnitude (m) for the highest-gain
        #   role before the slot-hierarchy clamp.
        #
        # anticipation_role_gain:
        #   Per-depth-role multiplier on the pre-shift (role-aware).  A/B
        #   are the engageable interior band and shift furthest; WINGER is
        #   small (0.15) so the outermost defender barely moves toward the
        #   threat — and the slot-hierarchy clamp still forbids it crossing
        #   the CENTRE lane (no winger drift).
        # ─────────────────────────────────────────────────────────
        # ── EVEN-LADDER REWORK (2026-09-15) ──
        # The anticipation pre-shift leaned defenders toward the predicted
        # threat lane, which perturbs the uniform ladder.  DISABLED so the
        # three-point width is the only thing that sets spacing.
        self.anticipation_enabled: bool = False
        self.anticipation_lead_time_s: float = 0.35
        self.anticipation_band_m: float = 8.0
        self.anticipation_max_gain_m: float = 1.5
        self.anticipation_role_gain: dict[str, float] = {
            "A": 1.0,
            "B": 0.8,
            "C": 0.6,
            "D": 0.4,
            "CENTRE": 0.3,
            "WINGER": 0.15,
        }

        # ─────────────────────────────────────────────────────────────
        # ANTICIPATION AUTHORITY WINDOW (2026-09-08)
        #
        # The anticipation pre-shift (Stage 9) lands in the frame spacing
        # dict as a Y-only decoration, but the boundary writers that follow
        # (tackler-commit, pursuit-commit, pre-tackle authority / depth-lock)
        # re-write target_y over it — the "lean then snap" symptom.  These
        # tuners give the lean a dedicated AUTHORITY WINDOW so it survives
        # until the defender genuinely commits or TES engages.
        #
        # anticipation_authority_decay_s:
        #   Wall-clock window (s) after the pre-shift was stamped during
        #   which the anticipation Y remains authoritative for an in-band,
        #   non-committed defender.  Outside the window the lean is
        #   released back to the live pipeline.
        #
        # anticipation_authority_blend:
        #   Per-frame blend (0..1) pulling target_y toward
        #   anticipation_target_y while the authority is active.  A small
        #   value decays the lean gracefully instead of snapping it on/off.
        #
        # anticipation_authority_max_delta_m:
        #   Max per-frame delta (m) the authority blend may apply to
        #   target_y (mirrors the FINALISE_MAX_DELTA_M pattern) so the
        #   lean never teleports a defender.
        #
        # anticipation_slowdown_anchor:
        #   The distance anchor for the pre-tackle slowdown band.  The
        #   legacy behaviour decelerated on a raw Euclidean distance to the
        #   LIVE BC, which resolves to noise when the defender is leaning
        #   toward a predicted lane that differs from the live carrier.
        #   "bc_predicted" (default) measures against
        #   (bc.x + bc.vx*lead, bc.y + bc.vy*lead) — the point the line
        #   leaned toward; "role_depth_x" measures against the role-depth
        #   arrival cell (bc.x + WIDTH_OFFSETS[role], bc.y); "bc_live"
        #   restores the byte-identical legacy raw-BC behaviour.
        # ─────────────────────────────────────────────────────────────
        self.anticipation_authority_decay_s: float = 0.65
        self.anticipation_authority_blend: float = 0.55
        self.anticipation_authority_max_delta_m: float = 0.4
        self.anticipation_slowdown_anchor: str = "bc_predicted"

        # ─────────────────────────────────────────────────────────────
        # TES DRIVE STRUCTURE-HOLD (2026-09-11)
        #
        # During the TES drive chamber (TACKLE_CONTACT / TACKLE_DRIVE /
        # TACKLE_DOMINANCE) the defensive line holds its structure: a body
        # already at its depth role FREEZES, an out-of-position body keeps
        # JOGGING toward its depth role, ONLY tacklers inherit ball-carrier
        # movement, and every other defender ignores BC movement entirely.
        # The spacing band stays LOCKED with only a small bounded stretch,
        # and the anticipation micro-adjustment remains live on top of it.
        # At RELEASE the whole line unfreezes instantly and the
        # DEF_RETREAT_10 depth target switches from the drive-entry ruck_x
        # to the live ruck_x.  See
        # game/defence/defence_lvl_1/tes_drive_structure.py.
        #
        # tes_drive_structure_enabled:
        #   Master gate.  True = the structure-hold authority owns every
        #   non-tackler line body during the drive.  False = byte-identical
        #   legacy drive behaviour (no structure hold).
        #
        # tes_hold_freeze_tolerance_m:
        #   Arrival deadband (m) around the structure cell inside which the
        #   body FREEZES (HOLD, zero velocity).  Farther out it jogs in.
        #
        # tes_hold_jog_speed_mps:
        #   Capped speed (m/s) an out-of-position body jogs toward its
        #   depth role (a destination, never a snap).
        #
        # tes_band_stretch_max_m:
        #   Maximum lateral stretch (m) of the locked spacing band for the
        #   anticipation micro-adjustment — the band can only lean a small
        #   bounded amount, never re-open.
        # ─────────────────────────────────────────────────────────────
        self.tes_drive_structure_enabled: bool = True
        self.tes_hold_freeze_tolerance_m: float = 0.5
        self.tes_hold_jog_speed_mps: float = 2.5
        self.tes_band_stretch_max_m: float = 0.4

        # ── TES DEPTH OBLIGATION — TOLERANCE (Stage 1, 2026-09-18) ──
        # THE acceptable depth tolerance for the TES Depth Obligation.
        # A defender whose depth differs from its assigned role depth
        # (``bc.x + WIDTH_OFFSETS[depth_role]`` during the drive) by MORE
        # than this is outside the acceptable band and is therefore in the
        # formal TES Depth Obligation state.
        #
        # READ BY THE STAGE-1 PROBE ONLY (probe_tes_depth_debt.py) — it is
        # wired to NO behaviour.  Stage 1 is measurement-only: it defines
        # the debt, exposes the obligation verdict, and measures whether
        # the debt discharges.  Rollout Step 3 ("Define obligation
        # threshold") is the step that promotes this value into the live
        # authority; until then nothing in the engine consumes it.
        #
        # Defaults to the existing TES structure-hold arrival deadband
        # (``tes_hold_freeze_tolerance_m``, 0.5 m) so Stage 1 invents no
        # new number: the threshold it measures against is exactly the one
        # the live hold already uses to decide "in position / jog".
        self.tes_depth_obligation_tolerance_m: float = (
            self.tes_hold_freeze_tolerance_m
        )

        # ── TES DEPTH AUTHORITY — DISCHARGE CONVERGENCE (Stage 2, 2026-09-18) ──
        # Stage 2 is the first WRITER built on the Stage-1 obligation: a
        # non-exempt defender inside the TES chamber whose depth debt
        # exceeds ``tes_depth_obligation_tolerance_m`` is granted TEMPORARY
        # locomotion authority, and the depth-discharge system owns its
        # X-axis closure toward the canonical role depth target until the
        # debt is back inside tolerance.  The authority is a PURE per-frame
        # read of the Stage-1 verdict — it introduces no latch, hysteresis,
        # cooldown, target store or persistent attribute.
        #
        # See game/defence/defence_lvl_1/tes_depth_authority.py.  The
        # convergence law is:
        #
        #     speed = clamp(target_motion + gain * debt, floor, cap)
        #
        # tes_depth_authority_enabled:
        #   Master gate.  True (default) = the authority owns X closure for
        #   obligated line bodies during the drive chamber.  False (or the
        #   runtime lever ``RLM_TES_DEPTH_AUTHORITY=0``) restores the
        #   pre-Stage-2 behaviour byte-for-byte (the structure hold's flat
        #   jog speed).
        #
        # tes_depth_authority_gain_per_s:
        #   The GAP-PROPORTIONAL convergence gain: metres/second of closure
        #   commanded per metre of depth debt.  The body therefore
        #   decelerates smoothly into the role depth (no overshoot, no
        #   chatter) instead of chasing it at a flat speed.  This is the
        #   "gap-proportional convergence" the Stage-2 contract names.
        #
        # tes_depth_authority_track_target:
        #   Feed the CANONICAL TARGET'S OWN MOTION (the live carrier's X
        #   velocity) into the command.  The role depth is
        #   ``bc.x + WIDTH_OFFSETS[depth_role]`` — it MOVES — and a purely
        #   proportional term cannot track a moving target to zero error
        #   (it parks at ``carrier_speed / gain`` of debt and never
        #   discharges).  True (default) tracks it; False gives the
        #   pure-proportional variant as a one-flag A/B.  This is a
        #   per-frame read of the live carrier, NOT stored state.
        #
        # tes_depth_authority_min_speed_mps:
        #   The floor.  Defaults to the live structure-hold jog speed so
        #   the authority can never command a body SLOWER than the hold it
        #   replaces — Stage 2 is monotone (never worse) for small debts by
        #   construction.
        #
        # tes_depth_authority_max_speed_mps:
        #   The cap.  0.0 (default) = the authority has no cap of its own
        #   and the UNIFIED defensive ceiling (``max_defensive_speed_mps``)
        #   applies, so it obeys the same single top-speed law as every
        #   other defensive writer.
        self.tes_depth_authority_enabled: bool = True
        self.tes_depth_authority_gain_per_s: float = 1.5
        self.tes_depth_authority_track_target: bool = True
        self.tes_depth_authority_min_speed_mps: float = (
            self.tes_hold_jog_speed_mps
        )
        self.tes_depth_authority_max_speed_mps: float = 0.0

        # ── BOUNDED GLIDE SMOOTHING (2026-09-20) ──────────────────
        # Master gate for the bounded glide family
        # (game/defence/defence_lvl_1/glide_budget.py).
        #
        # THE DEFECT IT FIXES: the tackle-begin glide and the two-radius
        # attach band both wrote a non-contest defender's x/y directly and
        # then rescaled its velocity with ``vscale = CEILING / spd``, which
        # AMPLIFIES whenever spd < CEILING (a 1.0 m/s backpedal became
        # 6.0 m/s for the frame the clamp fired, then dropped again on its
        # release).  Both also ran an UNBOUNDED Y slot-band clamp AFTER the
        # per-frame ceiling, so a body metres outside its band was teleported
        # onto the band edge in one frame.  Measured symptom: "defenders not
        # involved in the contest re-target / correct too aggressively,
        # creating visible jumps during defensive retreat."
        #
        # WHEN True (default): the glide may only ever REDUCE speed to the
        # ceiling, every position correction is drawn from ONE per-frame
        # budget (ceiling * dt), the band correction CONVERGES over frames
        # instead of snapping, the attach nudge never exceeds the speed the
        # body's own movement authority commanded, and the target_x glide is
        # bounded from its very first frame.
        #
        # WHEN False (or the runtime lever ``RLM_GLIDE_SMOOTH=0``): every
        # caller takes its pre-fix path byte-for-byte — the amplifying
        # vscale, the unbounded band snap and the uncapped first-frame
        # target swing — so the change ships as a one-flag A/B.
        #
        # See game/defence/defence_lvl_1/glide_budget.py for the contract and
        # verify_tes_glide_smoothing_contract.py for the machine check.
        self.glide_smooth_enabled: bool = True

        # ── DEPTH-TARGET BOUNDARY INVARIANT (defence boundary, 2026-09-19) ──
        # THE OPEN FINDING: a defensive depth-role target is generated from
        #
        #     role depth X = bc.x + WIDTH_OFFSETS[depth_role]
        #
        # which is unbounded in ``bc.x``.  The engine's fixed directional rule
        # is "the defence always retreats toward +X" (fullback_role.py), so
        # the DEFENDING team's own tryline is the +X tryline (x = 110) and a
        # carrier driven past x = 100 produced targets BEYOND it — a WINGER
        # target of 110-120, i.e. inside the team's OWN in-goal, with the TES
        # depth debt then measured against that illegal target.  The
        # post-tackle 10 m retreat protection has always been tryline-clamped
        # (anchor_authority.defensive_line_anchor_m); this invariant is the
        # equivalent protection for the drive-era depth target.
        #
        # DEPTH-ROLE TARGETS AND DEPTH-ROLE POSITIONAL LOCKS are both in
        # scope, because both are generated from that one law
        # (``role_depth_target_x`` and ``depth_lock_x`` — the latter glided
        # into ``x`` by the movement integrator, so an unclamped lock walks a
        # non-fullback depth-role body past the team's own tryline).
        #
        # See game/defence/defence_lvl_1/depth_boundary_guard.py.
        #
        # depth_boundary_clamp_enabled:
        #   Master gate.  True (default) = every non-fullback depth target is
        #   clamped into the legal field envelope [10, 110].  False (or the
        #   runtime lever ``RLM_DEPTH_BOUNDARY_CLAMP=0``) restores the pre-fix
        #   behaviour byte-for-byte: the clamp is an identity function and the
        #   enforcement pass returns an empty list.
        #
        # depth_boundary_tryline_margin_m:
        #   Optional CLEARANCE inside the defending team's own tryline, in
        #   metres.  0.0 (default) = the tryline itself is legal and "beyond"
        #   means past it, which is exactly the finding's wording.  This is a
        #   geometry rail, never a tactical squeeze: raising it requires a
        #   deliberate, auditable clearance rather than a code edit.
        self.depth_boundary_clamp_enabled: bool = True
        self.depth_boundary_tryline_margin_m: float = 0.0
        # ── TES RECOVERY OWNERSHIP — SLOT TABLE (2026-09-12) ──
        # The drive hold's lateral cell is no longer the body's own
        # achieved Y ("lane-true").  It is the body's ASSIGNED SLOT,
        # established by a ONE-PASS CENSUS on the chamber-entry frame and
        # then immutable for the whole chamber:
        #
        #   * defenders already correctly slotted RETAIN their slot and
        #     hold shape (they freeze on it);
        #   * a displaced defender RECOVERS to its own originally latched
        #     slot and nothing else;
        #   * a slot whose owner leaves the line stays VACANT — it keeps
        #     its owner identity and is never reassigned;
        #   * there is NO chamber-wide rebalancing and NO dynamic spacing
        #     redistribution (the ladder is latched, never re-solved).
        #
        # tes_slot_ownership_enabled:
        #   Master gate for the census-owned slot table.  True (default)
        #   = the hold targets the latched slot.  False = the legacy
        #   lane-true hold (target the body's own drive-entry Y).
        #
        # tes_slot_recovery_tolerance_m:
        #   Arrival deadband (m) around the OWNED SLOT inside which the
        #   body RETAINS (freezes).  Farther out it recovers (jogs in).
        #
        # tes_slot_recovery_speed_mps:
        #   Capped speed (m/s) a displaced body recovers toward its own
        #   latched slot (a destination, never a snap).
        self.tes_slot_ownership_enabled: bool = True
        self.tes_slot_recovery_tolerance_m: float = 0.5
        self.tes_slot_recovery_speed_mps: float = 2.5

        # ── TES WIDTH OBLIGATION — TOLERANCE (Stage 3a, 2026-09-19) ──
        # THE acceptable LATERAL (Y) tolerance for the TES Width Obligation.
        # A defender whose lateral position differs from its assigned role
        # width slot (``d.role_depth_target_y`` — the structural anchor
        # ``ruck_y + side * half_width * role_width_offset``, published every
        # frame by ``defensive_depth_roles.assign_depth_roles``) by MORE than
        # this is outside the acceptable band and is therefore in the formal
        # TES Width Obligation state.
        #
        # Defaults to the existing TES slot-recovery deadband
        # (``tes_slot_recovery_tolerance_m``, 0.5 m) so Stage 3a invents no
        # new number: the threshold it measures against is exactly the one
        # the live lateral recovery already uses to decide "in slot /
        # recover".
        self.tes_width_obligation_tolerance_m: float = (
            self.tes_slot_recovery_tolerance_m
        )

        # ── TES HOLD — LINE-WIDTH CLAMP (2026-09-11) ──
        # The structure hold must RESPECT the defensive line width: the
        # held lateral cell may never sit outside ``ruck_y ±
        # governed_width_m()/2``.  The lane seeded from the last OPEN_PLAY
        # count-lane model can sit well outside the governed width (the
        # count-lane outermost = 2.0 + (n_side-1) * defender_gap_m), which
        # held the whole TES line fanned ~9 m too wide on the loaded side
        # (the reported outward Y push).  When enabled the cell Y (and the
        # held target) is clamped into the governed band so the held line
        # keeps the line width instead of the raw lane spread; a body
        # outside the band no longer freezes — it jogs IN to the band.
        # Set False to restore the raw-lane hold (legacy behaviour).
        self.tes_hold_clamp_line_width_enabled: bool = True
        self.tes_hold_line_width_tol_m: float = 0.5

        # ── TES LATERAL (Y) AUTHORITY — PHASE GATE (2026-09-11) ──
        # The DEPTH (X) hold and the LATERAL (Y) authority are SEPARATE
        # authorities.  Depth tracking (bc.x + WIDTH_OFFSETS[role]) is
        # always live during the drive chamber; the lateral authority —
        # the line-width clamp AND the anticipation micro-adjustment that
        # write the held cell's Y — is PHASE-GATED to this phase-key set.
        # Any phase outside the set leaves the held lateral cell on the
        # body's TRUE drive-entry lane, so no phase profile can leak a
        # lateral nudge into a phase that must hold its exact lane.
        #
        # Default = the three TES drive sub-states (unchanged behaviour).
        # Set to a narrower set (e.g. {"tackle_drive"}) to scope the
        # lateral authority to one phase; set to frozenset() to disable
        # the lateral authority entirely (the line holds its exact lane).
        self.tes_lateral_authority_phases: frozenset = frozenset({
            "tackle_contact", "tackle_drive", "tackle_dominance",
        })

        # ── TES LATERAL (Y) BAND REFERENCE — STABLE BAND (2026-09-11) ──
        # The defensive-width band the held line is clamped into must be
        # centred on a STABLE reference, not the live ruck Y: the engine
        # re-pins ``world.ruck_y_m = bc.y`` at the CONTACT -> DRIVE
        # transition, so a live band re-centres on a carrier-derived ruck
        # and squeezes the held line every frame (the in-lane-spacing
        # disruption).  When True the band reference Y is LATCHED once per
        # drive (dropped at RELEASE); when False the legacy live-ruck band
        # is restored (byte-identical to the pre-2026-09-11 clamp).
        self.tes_lateral_band_ref_latched: bool = True

        # ── TES LANE-TRUE RETREAT SEED (2026-09-11) ──
        # The held lateral cell must be seeded from each body's TRUE lane —
        # the lane the defender ACTUALLY holds on the drive-entry frame —
        # never from the re-derived count-lane chain value
        # (``d.frame_spacing_y``).  That chain value is computed against the
        # CURRENT ruck and the CURRENT (tackle-depleted) per-side count, so
        # on the last pre-drive frame it is anchored to a DIFFERENT ruck Y
        # and compressed onto the side-width cap.  Measured live, every held
        # body's seeded lane sat 1–15 m away from its actual Y, so nothing
        # was ever "in lane" (nothing froze) and the whole line was dragged
        # onto the wrong lanes across the chamber — the lateral drift and
        # pinch.  When True the lane seed is the body's achieved lane
        # (lane-true).  False restores the legacy ``frame_spacing_y`` seed
        # (byte-identical pre-2026-09-11 behaviour).
        self.tes_lane_true_seed_enabled: bool = True

        # ── TES HOLD — CHAMBER FRESHNESS (2026-09-11) ──
        # A structure-hold latch is only valid inside the drive chamber
        # that created it.  The authority runs EVERY frame of the chamber,
        # so a frame stamp older than the immediately preceding world
        # frame proves the engine left the chamber through a path that
        # never re-enters ``update_tes_drive`` (RELEASE / PTB / POST_RUCK
        # all skip it) and therefore never calls
        # ``clear_tes_drive_structure``.  The latched lane, the drive-entry
        # ruck and the band reference then describe the PREVIOUS tackle;
        # re-using them dragged the whole line back onto dead lanes at max
        # speed for the next chamber (measured: 10-18 m lateral drift, no
        # body ever freezing).  When True a chamber gap invalidates every
        # latch so the next chamber re-seeds LANE-TRUE from the positions
        # the line actually holds.  Set False to keep the latch forever
        # (legacy behaviour, byte-identical pre-2026-09-11).
        self.tes_hold_reseed_on_chamber_gap_enabled: bool = True

        # ---- TES SIDE-OWNERSHIP CLAMP -- RETIRED / INERT (2026-09-12) ----
        # HYPOTHESIS TESTED AND REJECTED.  The hypothesis was that
        # ``depth_side != sign(y - ruck_y)`` at TES chamber entry is a
        # side-ownership corruption caused by ``decide_side_counts``
        # deriving (n_left, n_right) from RUCK ROOM only, so the dictated
        # count exceeds the bodies physically on one side and bodies get
        # dictated ACROSS the ruck.
        #
        # A canary was implemented in ``assign_split_sides`` to clamp the
        # dictated counts to the physically-present bodies, then removed.
        # It is NOT shippable, and the second reason invalidates the
        # measurement that was originally used to judge it:
        #
        #   1. CONTRACT.  ``assign_split_sides``' own docstring states that
        #      assignment is nearest-first to each side's BAND CENTRE and
        #      that defenders ARE ALLOWED TO CROSS THE RUCK ("this is the
        #      SINGLE authority for depth_side").  Cross-ruck assignment is
        #      therefore DELIBERATE, so ``depth_side != sign(y - ruck_y)`` is
        #      not by itself an ownership bug -- the metric that framed it as
        #      one was measuring an intentional policy.  The band centres are
        #      also DERIVED from n_left/n_right (derive_side_width_m ->
        #      side_band_centre), so clamping the counts rewrites the
        #      ordering key itself.
        #
        #   2. MEASUREMENT IS NON-DETERMINISTIC.  probe_tes_side_clamp_proof.py
        #      was run repeatedly.  Two runs of IDENTICAL engine code (this
        #      canary removed and verified to have zero readers) produced
        #      different worlds:
        #
        #          run A:  8 chambers,  6 entry mismatches, 1128 frame mismatches
        #          run B:  9 chambers, 14 entry mismatches, 1780 frame mismatches
        #
        #      Seeding per run (random.seed(0)) did NOT fix it: the LEGACY and
        #      CLAMPED arms then still differed although the flag has no
        #      readers at all --
        #
        #          LEGACY : 8 chambers, 11 entry mismatches, 1202 frame mismatches
        #          CLAMPED: 8 chambers, 10 entry mismatches, 1919 frame mismatches
        #
        #      So the non-determinism does NOT come from the process-shared
        #      ``random`` state alone; there is a further source (object-id /
        #      dict-iteration ordering over defenders is the prime suspect).
        #      Run-to-run variance of that size swamps any candidate effect,
        #      so this harness could attribute NOTHING to the clamp -- neither
        #      a regression nor an improvement.  No conclusion about this or
        #      any other side-ownership candidate may be drawn from it until
        #      determinism is established.
        #
        # The clamp was REMOVED and the legacy room-derived split retained
        # unchanged.  This flag is kept only as a record of the tested
        # hypothesis.  It has ZERO live readers: setting it has no effect.
        self.tes_side_ownership_clamp_enabled: bool = False  # INERT (rejected)

        # ─────────────────────────────────────────────────────────────
        # 12-STATE PRE-TACKLE MACHINE (2026-09-09)
        #
        # The unified pre-TES physics block (pretackle_machine.py) runs
        # AFTER anticipation and jitter removal and replaces the binary
        # ``try_pre_tackle`` engage decision with a single MovementState-
        # driven posture classification.  It resolves each (defender, BC)
        # into one of 12 states, maps that onto one of 5 outcomes
        # (bounce-back / fall-over / stunned-upright / wrong-foot /
        # clean-engage), and feeds TES ONLY when contact geometry, closing
        # posture, planted predicate and wrong-foot delta ALL resolve into
        # a valid engage window.
        #
        # pretackle_machine_enabled:
        #   Master gate.  True = the machine is the TES engage authority.
        #   False = the machine classifies conservatively (never feeds
        #   TES) — the legacy ``pretackle_classifier`` path is retained
        #   verbatim for the diff.
        #
        # machine_engage_distance_m:
        #   Max distance between the defender and BC for a valid engage
        #   window (mirrors the legacy 2.0 m tackle trigger).
        #
        # machine_head_on_max_deg / machine_side_on_max_deg:
        #   Approach-angle slip bands measured from the tackler's FACING
        #   plane (Finding 5).  < 30 deg head-on (no penalty), 30-60 deg
        #   side-on (bounce-back), >= 60 deg rear (fall-over).
        #
        # machine_max_approach_angle_deg:
        #   Hard reject above this angle from the facing plane.
        #
        # machine_planted_velocity_eps:
        #   Velocity epsilon for a genuine stationary square-up (zero
        #   velocity + HOLD + _mmdl_moved).
        #
        # machine_wrong_foot_facing_delta:
        #   Per-frame BC wrong-foot facing delta (radians) threshold.  Above
        #   this the BC has wrong-footed the defender.
        #
        # machine_wrong_foot_base_chance / machine_wrong_foot_skill_scale:
        #   Wrong-foot outcome probability (scaled by tackle_skill).
        #
        # machine_base_random_fail:
        #   Base random engage failure (5% design intent).
        #
        # machine_bounce_back_push_m / machine_fall_over_stun_s /
        # machine_stunned_upright_stun_s / machine_wrong_foot_recoil_m:
        #   Physical outcome magnitudes (the machine *produces* the
        #   outcome, not just labels it).
        # ─────────────────────────────────────────────────────────────
        self.pretackle_machine_enabled: bool = True
        self.machine_engage_distance_m: float = 2.0
        self.machine_head_on_max_deg: float = 30.0
        self.machine_side_on_max_deg: float = 60.0
        self.machine_max_approach_angle_deg: float = 120.0
        self.machine_planted_velocity_eps: float = 0.01
        self.machine_wrong_foot_facing_delta: float = 0.3
        self.machine_wrong_foot_base_chance: float = 0.4
        self.machine_wrong_foot_skill_scale: float = 0.15
        self.machine_base_random_fail: float = 0.05
        self.machine_bounce_back_push_m: float = 0.25
        self.machine_fall_over_stun_s: float = 0.5
        self.machine_stunned_upright_stun_s: float = 0.2
        self.machine_wrong_foot_recoil_m: float = 0.15

        # ─────────────────────────────────────────────────────────────
        # SECONDARY WIDTH-ELIGIBILITY GRACE (Stage 2, 2026-09-06)
        #
        # A defender promoted to PRIMARY/SECONDARY while ruck-adjacent
        # loses width-lane eligibility (width_authority.is_width_eligible
        # exempts PRIMARY/SECONDARY).  That can let the fallback spacing
        # pull that interior defender into a fallback micro-gap during
        # the marker lock window (Stage 1).  This grace preserves the
        # width-lane eligibility for a short window so fallback spacing
        # cannot pull that interior defender inward.
        #
        # secondary_grace_enabled:
        #   Master gate (canary).  True = the grace runs; a freshly-
        #   promoted ruck-adjacent SECONDARY/PRIMARY keeps its width lane
        #   for secondary_grace_duration_s.  False = byte-identical legacy
        #   width / spacing (no grace).  Reversible by flipping the flag.
        #
        # secondary_grace_duration_s:
        #   Grace window (s) after promotion while ruck-adjacent.
        #
        # secondary_grace_ruck_adjacent_m:
        #   A defender within this |d.y - ruck_y| of the ruck is
        #   "ruck-adjacent" for arming the grace (covers the A-pivot at
        #   +-2 m and the interior B lane).
        #
        # secondary_grace_invalid_ruck_move_m:
        #   Ruck displacement (x or y) beyond this invalidates the grace —
        #   markers and spacing stay responsive to legitimate rapid changes.
        # ─────────────────────────────────────────────────────────────
        self.secondary_grace_enabled: bool = False

        # ── WRITER-CONFLICT PRIORITY BANDS (2026-09-09) ──
        # Lateral (Y, 0.3 m) + depth (X, 0.5 m) writer-conflict bands.  Per
        # frame, per defender, per axis: every writer stage registers a
        # proposal; the STRONGEST movement signal survives and any competing
        # writer whose value lies inside a band of the dominant is IGNORED
        # (last-writer-wins target-jitter kill).  False = legacy behaviour.
        # See game/defence/defence_lvl_1/writer_priority_bands.py.
        self.writer_priority_bands_enabled: bool = True
        self.secondary_grace_duration_s: float = 0.5
        self.secondary_grace_ruck_adjacent_m: float = 4.0
        self.secondary_grace_invalid_ruck_move_m: float = 2.0

        # ─────────────────────────────────────────────────────────────
        # MARKER ROLE-ASSIGNMENT GATE (Stage: Only form markers at end of
        # Drive, 2026-09-06)
        #
        # marker_assign_at_release_only:
        #   Master gate (canary).  True = markers are NOT role-assigned
        #   (defender_role = MARKER_A/B) during the TES drive — they are
        #   assigned ONLY at the canonical Drive→Release transition
        #   (DOMINANCE→RELEASE, the "tackle end").  False = byte-identical
        #   legacy behaviour: markers are also assigned at TACKLE_CONTACT so
        #   they exist through CONTACT/DRIVE/DOMINANCE.  Reversible by
        #   flipping the flag.
        # ─────────────────────────────────────────────────────────────
        self.marker_assign_at_release_only: bool = True

        # ═══════════════════════════════════════════════════════════════
        # DEFENSIVE LINE GOVERNOR TUNERS (base_line_width_m,
        # min_line_width_m, compression_trigger_m, compression_smooth_rate,
        # line_width_max_delta_per_frame_m, step_up_trigger_m,
        # step_up_buffer_m, step_up_rate, step_down_rate,
        # base_line_speed_mps, line_speed_gain_mps, speed_trigger_m and
        # max_lane_index) moved to defensive_line_shape_tuners.py.  The
        # POST-CATCH DEFENSIVE LINE CLAMP below stays here — it is a SPEED
        # clamp keyed off the unified ceiling, not a width control.

        # ── POST-CATCH DEFENSIVE LINE CLAMP (2026-08-02) ──
        # The clamp's ceiling is the GLOBAL DEFENSIVE TOP-SPEED CEILING
        # (max_defensive_speed_mps).  The clamp enforces the unified maximum
        # during the kickoff-catch → first stable OPEN_PLAY transition; it
        # does NOT choose its own independent maximum.  `post_catch_cap_mps`
        # is kept as a read-only alias for legacy consumers.
        # Stability window: depth_class must be unchanged for this many
        # consecutive OPEN_PLAY frames (with pursuit off) before the clamp
        # auto-disengages.
        self.post_catch_stability_frames: int = 10
        # STAGE 3F: time-based twin of post_catch_stability_frames (10
        # frames @ 60fps = 0.16667s).  The frame counter drifted the
        # post-catch clamp release point with tick rate, shoving pacing
        # of the catch->stable transition non-deterministically.
        self.post_catch_stability_s: float = 10.0 / 60.0

        # ═══════════════════════════════════════════════════════════════
        # MACRO WIDTH GOVERNOR tuners (DEF_LINE_TARGET_WIDTH_M,
        # DEFENDER_SPACING_M, DEPTH_ROLE_LATERAL_OFFSET_M,
        # width_governor_alpha, compression_curve_enabled,
        # compression_curve_strength and macro_width_governor_enabled)
        # moved to defensive_line_shape_tuners.py.

        # ═══════════════════════════════════════════════════════════════
        # BIG BLOCK DEFENCE — TACKLE ROLE OFFSETS (RULE 1)
        #
        # These offsets define defender positioning relative to BC
        # before the first tackle is made.  No line-based positioning,
        # no retreat/compression/step-up — just pure BC-relative lanes.
        #
        # Each defender's target_x = BC.x + offset_x
        # Each defender's target_y = BC.y + offset_y
        #
        # NOTE: offset_y values are ± because they are applied as
        # (lane_index * offset_y), giving symmetrical spread.
        # ═══════════════════════════════════════════════════════════════
        self.TACKLE_ROLE_OFFSETS: dict = {
            "PRIMARY":   {"offset_x": -1.0, "offset_y": 0.0},
            "SECONDARY": {"offset_x": -0.5, "offset_y": 1.5},
            "THIRD":     {"offset_x": 0.0,  "offset_y": 3.0},
            "EDGE":      {"offset_x": 3.0,  "offset_y": 8.0},
            "FULLBACK":  {"offset_x": 10.0, "offset_y": 0.0},
        }

        # ═══════════════════════════════════════════════════════════════
        # BIG BLOCK DEFENCE — TACKLE SELECTION THRESHOLDS (RULES 2-4)
        # ═══════════════════════════════════════════════════════════════
        self.tackle_primary_distance_m: float = 3.0    # RULE 2
        self.tackle_primary_angle_deg: float = 45.0
        self.tackle_primary_min_speed_mps: float = 1.0
        self.tackle_secondary_distance_m: float = 5.0  # RULE 3
        self.tackle_secondary_angle_deg: float = 60.0
        self.tackle_secondary_min_speed_mps: float = 1.0
        self.tackle_third_distance_m: float = 7.0      # RULE 4
        self.tackle_third_angle_deg: float = 75.0

        # ═══════════════════════════════════════════════════════════════
        # MANDATORY 10‑METRE RETREAT COMPLIANCE (DESIGN‑DOC BLOCK)
        #
        # After a play‑the‑ball, all defenders except markers must retreat
        # at least RETREAT_COMPLIANCE_DISTANCE metres from the ruck before
        # they are legally permitted to engage the ball carrier.
        #
        # retreat_compliance_distance_m:
        #   The minimum X distance behind ruck_x a defender must reach
        #   to be eligible for tackle initiation, step‑up, chase, and
        #   compression behaviour in OPEN_PLAY.  Defenders below this
        #   threshold are forced to retreat (target_x = ruck_x + distance).
        #
        # Markers (MARKER_A, MARKER_B) are always exempt regardless of
        # their position.
        # ═══════════════════════════════════════════════════════════════
        self.retreat_compliance_distance_m: float = 10.0

        # ── DEBT-AWARE RETREAT LOCOMOTION (Retreat Geometry Phase 1) ──
        # A defender that owes ground to its retreat target travels with a
        # gait that MATCHES the debt (game/defence/defence_lvl_1/
        # retreat_locomotion.py): BACKPEDAL_SHUFFLE below
        # retreat_shuffle_max_debt_m, RETREAT_GLIDE up to
        # retreat_glide_max_debt_m, RETREAT_RUN beyond it.  The debt is the
        # remaining X deficit to the commanded retreat target, which on the
        # compliance write IS the obligation deficit the engine's arrival
        # test discharges on — debt 0 == discharge.
        #
        # These tuners are the SINGLE source of the band edges, the
        # anti-chatter margins and the anti-spin taper; the module carries
        # the same values as fallbacks so a bare-namespace tuner object
        # degrades to identical behaviour.
        #
        # retreat_locomotion_enabled: master switch.  False (or the runtime
        #   lever RLM_RETREAT_LOCOMOTION=0) restores the legacy flat
        #   0.85 x ceiling straight-at-target navigation.
        # retreat_backpedal_max_debt_m: THE BACKPEDAL THRESHOLD (metres of X
        #   debt).  At or below it a retreating body holds the eyes on the
        #   play (ball carrier / ruck) and closes the remaining deficit by
        #   REVERSE MOVEMENT ALONG X; only beyond it — plus the facing
        #   hysteresis below — may the body turn its back on the play and
        #   run.  It is also the small-debt band edge, so one number defines
        #   both the gait band and the turn-and-run permission.
        # retreat_shuffle_max_debt_m: DEPRECATED ALIAS of the above.  Kept
        #   because older namespaces still carry it; the locomotion module
        #   reads the canonical name first and falls back to this only when
        #   it is absent, so there is exactly ONE effective edge.
        # retreat_glide_max_debt_m: the upper band edge (metres of X debt).
        # retreat_mode_hysteresis_m: a gait change requires the debt to clear
        #   the separating boundary by this margin, so a body hovering on an
        #   edge cannot alternate gaits frame-to-frame (which would ADD
        #   locomotion churn instead of removing it).
        # retreat_facing_travel_hysteresis_m: the FACING verdict's own
        #   margin, independent of the speed band's.  Travel facing is only
        #   granted once the debt reaches backpedal_max + this margin, and
        #   the verdict is latched between the two, so a body hovering at the
        #   backpedal boundary cannot flip backpedal/turn-and-run every
        #   frame.  It also sets the realistic turn point: backpedal the
        #   threshold plus this margin, then turn and go.
        # retreat_mode_min_dwell_frames: minimum frames an UPGRADE (faster
        #   gait) is held.  DOWNGRADES are always immediate, so the settle
        #   gait and the arrival taper can never be delayed.
        # retreat_arrival_band_m / retreat_arrival_min_scale: arrival taper —
        #   below the band the commanded speed scales with the remaining debt
        #   (floored at the min scale so the body never crawls short of the
        #   line).  This is the primary anti-spin mechanism: no overshoot, so
        #   no travel-direction reversal.
        # retreat_spin_facing_rate_deg_s: facing rotation ceiling for the
        #   gaits that hold the eyes on the threat (BACKPEDAL_SHUFFLE /
        #   RETREAT_GLIDE).  Bounded so facing can never snap.
        # retreat_no_reverse: while ground is still owed, the body may never
        #   gain it back toward the ruck (vx >= 0).  Cleared at debt 0.
        # retreat_mode_profiles: per-gait locomotion profile.  Any subset of
        #   the keys may be overridden; the module merges each entry on top
        #   of its own defaults, so a partial entry can never produce a
        #   half-built gait.
        #     speed_scale     fraction of the unified defensive ceiling
        #     accel / decel   accel-lerp factors (same units as Player.accel)
        #     lateral_priority 0 = X-only, 1 = Y-only
        #     facing_policy   "threat" (eyes on the play) | "travel"
        self.retreat_locomotion_enabled: bool = True
        # THE BACKPEDAL THRESHOLD (metres of X debt).  Inside it the body
        # holds the eyes on the play and closes the deficit by reverse
        # movement along X; beyond it (plus the facing hysteresis) it may
        # turn and run.  1.5 m keeps the backpedal to the last step-and-a-
        # half onto the line — the small-debt compliance shuffle — while
        # still covering the whole of the contract's 1-2 m band.
        self.retreat_backpedal_max_debt_m: float = 1.5
        # DEPRECATED ALIAS of the backpedal threshold.  Same value, kept so
        # older namespaces/stubs that still carry the old name keep working;
        # the locomotion module prefers the canonical name and only falls
        # back to this when it is absent, so there is exactly ONE edge.
        self.retreat_shuffle_max_debt_m: float = (
            self.retreat_backpedal_max_debt_m
        )
        self.retreat_glide_max_debt_m: float = 5.0
        self.retreat_mode_hysteresis_m: float = 0.35
        # FACING HYSTERESIS — the turn-and-run permission's own margin.
        # Travel facing engages at backpedal_max + this (2.25 m) and the
        # verdict is latched between the threshold and that point, so a body
        # hovering at the backpedal boundary cannot alternate
        # backpedal/turn-and-run frame-to-frame.
        self.retreat_facing_travel_hysteresis_m: float = 0.75
        self.retreat_mode_min_dwell_frames: int = 3
        # Shallow band / high floor: the anti-overshoot work is done by the
        # no-reverse clamp, so the taper only smooths the final centimetres.
        # A deep taper (0.6 m band, 0.35 floor — the first tuning pass)
        # slowed the X closure that discharges the obligation and stretched
        # the median discharge from 1.6 s to 6.3 s.
        self.retreat_arrival_band_m: float = 0.4
        self.retreat_arrival_min_scale: float = 0.80
        self.retreat_spin_facing_rate_deg_s: float = 540.0
        self.retreat_no_reverse: bool = True
        self.retreat_mode_profiles: dict = {
            # THE BACKPEDAL (debt inside retreat_backpedal_max_debt_m):
            # eyes on the play, closing the last metres by REVERSE MOVEMENT
            # ALONG X.  0.85 == the legacy flat retreat command, so this band
            # is discharge-NEUTRAL — the backpedal character lives in the
            # FACING (threat) and in the X-DOMINANT heading
            # (lateral_priority 0.0, so the travel vector cannot swing with
            # the spacing slot and the X closure cannot be diluted), never in
            # a low top speed (a 0.40 scale here measured as a 4x discharge
            # regression, because most obligated frames sit in this band).
            "BACKPEDAL_SHUFFLE": {
                "speed_scale": 0.85,
                "accel": 0.50,
                "decel": 0.65,
                "lateral_priority": 0.0,
                "facing_policy": "threat",
            },
            # TURN AND RUN (debt past the backpedal threshold): cover ground
            # while the spacing slot is still tracked.  The band DECLARES
            # travel facing, but the facing gate still requires the debt to
            # clear backpedal_max + retreat_facing_travel_hysteresis_m, so
            # the turn can never happen inside the backpedal band.
            "RETREAT_GLIDE": {
                "speed_scale": 0.95,
                "accel": 0.65,
                "decel": 0.50,
                "lateral_priority": 0.25,
                "facing_policy": "travel",
            },
            # Large deficit: the full retreat run — ceiling speed, driving
            # straight back (low lateral priority) and facing travel.
            "RETREAT_RUN": {
                "speed_scale": 1.00,
                "accel": 0.85,
                "decel": 0.40,
                "lateral_priority": 0.10,
                "facing_policy": "travel",
            },
        }

        # ═══════════════════════════════════════════════════════════════
        # PURSUIT MODE — X‑only spread, Y tight to BC
        #
        # Before the first tackle, defenders pursue in a tight band
        # around the ball carrier.  X spreads by lane_index * role_factor
        # so outer defenders hold wider lanes in depth.  Y stays at BC.y
        # with a small constant offset (0 = directly on BC.y).
        #
        # PURSUIT_X_OFFSET_BY_ROLE:
        #   Multiplier for lane_index when computing target_x offset.
        #   Higher values = wider lane spacing in depth (X).
        #
        # PURSUIT_Y_OFFSET:
        #   Small constant added to BC.y for the Y target.
        #   0.0 = all defenders target BC's exact Y (tight pursuit band).
        # ═══════════════════════════════════════════════════════════════
        self.PURSUIT_X_OFFSET_BY_ROLE: dict = {
            "PRIMARY": 0.0,
            "SECONDARY": 0.3,
            "THIRD": 0.5,
            "EDGE": 0.8,
            "FULLBACK": 1.2,
        }
        self.PURSUIT_Y_OFFSET: float = 0.0

        # ─────────────────────────────────────────────────────────────
        # ENGAGED-ONLY NO-TACKLE / NO-DEFENCE GATE (STAGE 11, 2026-09-09)
        #
        # The full defensive pipeline (depth-role band maintenance, split-
        # spacing chain, width recompute + guards, anticipation pre-shift,
        # and the TES / pre-tackle wiring) is expensive and ran every
        # OPEN_PLAY frame from kickoff onwards even when the ball carrier
        # was completely isolated — the "zero-tackles" idle frames where the
        # framerate collapses.  This gate suppresses the WHOLE defensive
        # pipeline whenever the game is in OPEN_PLAY, no body is in contact,
        # and no defender is within ``no_tackle_engagement_radius_m`` of the
        # ball carrier (Option B, engaged-only).  The moment any defender
        # closes into range (or a tackle starts) the gate releases and the
        # full pipeline resumes — so the chase is never suppressed, only the
        # wasted work on genuinely idle frames.
        #
        # no_tackle_no_defence_enabled:
        #   Master canary gate.  True = the pipeline is suppressed on idle
        #   frames (framerate recovery).  False = byte-identical legacy
        #   behaviour (full pipeline every OPEN_PLAY frame).  Reversible by
        #   flipping the flag; consumed by
        #   defensive_pipeline.no_tackle_no_defence_active().
        #
        # no_tackle_engagement_radius_m:
        #   The Euclidean distance (m) from the ball carrier within which ANY
        #   defender counts as "engaged".  If the nearest defender is inside
        #   this radius the gate is closed (full pipeline runs); if every
        #   defender is beyond it (BC isolated) the gate is open and the
        #   heavy pipeline is skipped.  Mirrors ``pursuit_commit_radius_m``
        #   (12 m) so the idle window matches the pursuit commit band.
        # ─────────────────────────────────────────────────────────────
        self.no_tackle_no_defence_enabled: bool = False
        self.no_tackle_engagement_radius_m: float = 12.0

    # ─────────────────────────────────────────────────────────────
    # RADIUS HIERARCHY ASSERTION
    # ─────────────────────────────────────────────────────────────
        # FOUR-ANCHOR COMPRESSION MODEL -- DEFENDING TRYLINE WIDTH REGION:
        # the ``width_defending_tryline_m`` property (alias of
        # width_right_tryline_m) moved to defensive_line_shape_tuners.py.

    def assert_radii(self) -> None:
        """Assert the radius hierarchy at startup.
        
        Raises AssertionError if any radii violate the ordering.
        """
        assert self.collision_radius_m < self.bc_arrival_radius_m < self.tackle_radius_m, (
            f"Radius violation: collision({self.collision_radius_m}) < "
            f"bc_arrival({self.bc_arrival_radius_m}) < "
            f"tackle({self.tackle_radius_m}) FAILED"
        )
        assert self.tackle_radius_m <= self.containment_radius_m <= self.retreat_radius_m <= self.dh_arrival_radius_m <= self.ruck_radius_m, (
            f"Radius violation: tackle({self.tackle_radius_m}) <= "
            f"containment({self.containment_radius_m}) <= "
            f"retreat({self.retreat_radius_m}) <= "
            f"dh_arrival({self.dh_arrival_radius_m}) <= "
            f"ruck({self.ruck_radius_m}) FAILED"
        )
        # ── PRE-TACKLE CORRIDOR HIERARCHY (2026-09-15) ──
        # The corridor must be wide enough that the slowdown band is
        # genuinely available before the tackle arms, and narrow enough that
        # it never reaches past the promote radius.  A violation here is the
        # "tackle transitions before the slowdown curve has time to act"
        # defect made structural: fail at startup instead of producing a
        # silent visual no-op.
        _plant = float(getattr(self, "pretackle_plant_radius_m", 0.8))
        _arm = float(getattr(self, "tackle_radius_m", 1.5))
        _corridor = float(getattr(self, "pretackle_corridor_radius_m", 4.5))
        _band = float(getattr(self, "pretackle_slowdown_band_m", 4.0))
        assert _plant < _arm, (
            f"Corridor violation: plant({_plant}) < "
            f"tackle_arm({_arm}) FAILED"
        )
        assert _arm < _corridor, (
            f"Corridor violation: tackle_arm({_arm}) < "
            f"corridor({_corridor}) FAILED -- a corridor narrower than the "
            f"tackle arm leaves no approach window"
        )
        assert _band <= _corridor, (
            f"Corridor violation: band({_band}) <= corridor({_corridor}) "
            f"FAILED -- the corridor must contain the whole slowdown band"
        )
