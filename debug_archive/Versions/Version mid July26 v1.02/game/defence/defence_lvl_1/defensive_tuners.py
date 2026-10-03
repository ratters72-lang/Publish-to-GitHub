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
"""


class DefenceTuners:
    """Tuners controlling STEP‑1 geometry + STEP‑2 depth offsets."""

    def __init__(self):
        # ─────────────────────────────────────────────────────────
        # FIELD DIMENSIONS (metres)
        # ─────────────────────────────────────────────────────────
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
        self.width_at_own_tryline_m = 50.0
        self.width_at_halfway_m = 55.0
        self.width_at_opp_tryline_m = 60.0

        # ─────────────────────────────────────────────────────────
        # STEP‑1 COMPRESSION / SPACING / WIDTH CURVE
        # Used by DefensiveGeometry to compute lane_y targets.
        # ─────────────────────────────────────────────────────────
        # How strongly ball X position compresses (<1) or expands (>1)
        # the defensive line width.  0 = no effect, 0.5 = moderate.
        self.compression_factor: float = 0.1

        # Base spacing (metres) between adjacent defenders at neutral
        # compression (factor = 1.0).  Tighter = more compact line.
        self.spacing_m: float = 5.0

        # Per-depth-role multiplier on lane offset.
        # Inner defenders (A/B) typically <1.0 (tighter).
        # Outer defenders (C/D/WINGER) typically >1.0 (wider).
        self.width_curve_factor: dict[str, float] = {
            "A": 0.8,
            "B": 0.9,
            "C": 1.0,
            "D": 1.1,
            "CENTRE": 1.0,
            "WINGER": 1.2,
        }

        # ─────────────────────────────────────────────────────────
        # STEP‑1 BASELINE DEPTH
        # Distance behind the ruck the defensive line anchors.
        # Attack runs RIGHT → defenders retreat RIGHT → ruck_x + depth.
        # ─────────────────────────────────────────────────────────
        self.def_line_depth_m = 30.0

        # Width clamps
        self.min_def_width_m = 30.0
        self.max_def_width_m = 65.0

        # ─────────────────────────────────────────────────────────
        # STEP‑2 DEPTH ROLE TABLE
        #
        # Depth roles (A/B/C/D) override positional roles.
        # Positional roles (CENTRE/WINGER) apply only if no depth role.
        #
        # Example tuning:
        #   A = 0m   (closest to BC)
        #   B = 0m
        #   C = 3m
        #   D = 5m   (furthest from BC)
        #
        #   CENTRE = 5m   (fallback depth)
        #   WINGER = 10m  (fallback depth)
        #
        # These values are applied as offsets to target_x in STEP‑2.
        # ─────────────────────────────────────────────────────────
        self.ROLE_DEPTH_M = {
            "A": 0.0,
            "B": 0.0,
            "C": 3.0,
            "D": 5.0,
            "CENTRE": 5.0,
            "WINGER": 10.0,
        }

        # ─────────────────────────────────────────────────────────
        # DEPTH CLASSIFICATION THRESHOLDS (metres)
        #
        # classify_defender_depth() uses these to tag defenders:
        #   PRIMARY:   |d.x - expected_x| <= primary_depth_threshold_m
        #   SECONDARY: |d.x - expected_x| <= secondary_depth_threshold_m
        #   PASSIVE:   otherwise
        #
        # These MUST be large enough to account for the gap between
        # formation positions at kickoff (~62m) and ball (~20m).
        # On a 100m field, expected_x = ball_x - ROLE_DEPTH_M[role]
        # can easily be 40-50m behind the defender, so thresholds
        # of 5m will lock everyone in PASSIVE permanently.
        #
        # Recommended:
        #   primary   = 50m — covers kickoff gap
        #   secondary = 80m — covers extreme gaps
        # ─────────────────────────────────────────────────────────
        self.primary_depth_threshold_m: float = 50.0
        self.secondary_depth_threshold_m: float = 80.0

        # ─────────────────────────────────────────────────────────
        # TACKLE RADIUS (global tuner — single unified value)
        # Distance at which a defender initiates a tackle.
        # Must match OpenPlayConfig.tackle_radius_m (1.5).
        # ─────────────────────────────────────────────────────────
        self.tackle_radius_m = 1.5

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
        self.MARKER_A_FREEZE_AFTER_PTB: float = 1.0
        self.MARKER_B_FREEZE_AFTER_PTB: float = 1.0

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

    # ─────────────────────────────────────────────────────────────
    # RADIUS HIERARCHY ASSERTION
    # ─────────────────────────────────────────────────────────────
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
