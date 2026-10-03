"""
defensive_timers.py — STEP‑03A‑10 Timing tuners appended to DefenceTuners.

All timing-related tuners are defined here as a mixin-style block.
They are merged into DefenceTuners via the TIMING_TUNERS dict.

Usage:
    from defensive_tuners import DefenceTuners
    from defensive_timers import TIMING_TUNERS

    class DefenceTunersWithTiming(DefenceTuners):
        def __init__(self):
            super().__init__()
            for k, v in TIMING_TUNERS.items():
                setattr(self, k, v)

Or, simpler: just add them directly to DefenceTuners.__init__()
"""

TIMING_TUNERS = {
    # ── 1. REACTION DELAY ───────────────────────────────────────
    "reaction_base_s": 0.15,           # base reaction delay (seconds)
    "reaction_role_factor": {          # multiplier on reaction_base_s per role
        "MARKER": 0.3,
        "PRIMARY": 0.5,
        "SECONDARY": 0.7,
        "PASSIVE": 1.0,
    },
    "reaction_lane_offset_s": 0.05,    # extra seconds per |lane_index| step
    "reaction_depth_offset_s": {       # extra seconds per depth_class
        "PRIMARY": 0.0,
        "SECONDARY": 0.1,
        "PASSIVE": 0.2,
    },
    "reaction_position_change_m": 2.0,  # BC position change (X) that triggers reaction
    "reaction_lateral_change_m": 1.5,   # BC lateral change (Y) that triggers reaction
    "reaction_accel_threshold_mps": 3.0,# BC acceleration/deceleration that triggers reaction

    # ── 2. STAGGERED STEP-UP ────────────────────────────────────
    "step_stagger_lane_factor": 0.03,     # seconds per |lane_index| step
    "step_stagger_depth_offset": {        # extra seconds per depth_class
        "PRIMARY": 0.0,
        "SECONDARY": 0.05,
        "PASSIVE": 0.1,
    },
    "step_stagger_activation_range_m": 15.0,  # BC must be within this X distance to activate
    "step_stagger_deactivation_m": 20.0,      # BC beyond this X distance deactivates

    # ── 3. RETREAT HESITATION ──────────────────────────────────
    "retreat_delay_s": 0.3,           # seconds of hesitation before backpedal
    "retreat_threshold_m": 2.0,       # BC must be anchor_x + this much to trigger

    # ── 4. LATERAL HESITATION (ANTI-OVERCOMMIT) ─────────────────
    "lateral_small_threshold_m": 0.5,     # ignore BC lateral changes below this
    "lateral_moderate_threshold_m": 2.0,  # above this → immediate reaction
    "lateral_hesitation_s": 0.15,         # hesitation duration for moderate changes

    # ── 5. COMPRESSION TIMING ──────────────────────────────────
    "compression_activation_distance_m": 15.0,  # BC within this X of anchor activates
    "compression_duration_s": 0.5,              # seconds to fully converge
    "compression_threat_window_m": 10.0,        # FR must be within this X of BC

    # ── 6. MARKER & DUMMY HALF DELAYS ──────────────────────────
    "marker_stationary_hesitation_s": 0.05,      # extra hesitation when BC is stopped
    "dh_defender_ptb_delay_s": 0.2,              # DH defender delay after PTB
    "bc_stationary_speed_threshold_mps": 0.5,    # BC speed below this = stationary

    # ── 7. POST-RUCK QUIET WINDOW (STAGE 3F) ──────────────────
    # Wall-clock seconds after a PTB completion during which the whole
    # SECONDARY+PASSIVE line keeps its timing locks cleared.  Replaces the
    # old frame-count gate `frames_since_ptb < 30` (30 frames @ 60fps),
    # which drifted with tick rate and made spacing/pacing inherit
    # nondeterministic frame drift.
    "post_ruck_quiet_window_s": 0.5,             # 30 frames @ 60fps
}
