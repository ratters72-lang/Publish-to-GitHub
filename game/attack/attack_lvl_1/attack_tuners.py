class AttackTuners:
    """BC personality parameters."""

    def __init__(self):
        self.run_aggression = 1.0
        self.lateral_agility = 1.0
        self.sideline_avoidance = 1.0
        self.gap_preference = 1.0
        self.pass_preference = 1.0
        self.kick_preference = 1.0


class AttackStructureTuners:
    """Attack-geometry structure parameters (Task XII, activated Task XIII).

    Single-writer authority tuners for the attacking formation: lane law
    (ruck_y-anchored), depth/stagger law (behind the BC), sweep/support
    identity persistence, and the early-settle window.

    TASK XIII ACTIVATION: ``structure_enabled = True`` is now the live
    default — authored lanes are the production support-runner authority.
    The legacy naive BC-chase remains in AttackManager as the rollback
    path; set ``structure_enabled = False`` on an instance (or subclass)
    to exercise it byte-stably.  ``steering_enabled`` gates the Task XIII
    BC steering stabiliser (bc_steering_authority) the same way.
    """

    def __init__(self):
        # Master gate: False => AttackManager runs the legacy BC-chase
        # verbatim (byte-stable).  True => structured lanes + dumps.
        # FLIPPED ON by Task XIII ("Structure Activation") after the full
        # verifier suite passed with the authored lanes live.
        self.structure_enabled = True

        # ── TASK XIII: BC steering stabilisation (bc_steering_authority) ──
        # Master steering gate: False => AttackController consumes raw
        # geometry byte-identically.  True => defender-cone hysteresis,
        # lane-drift deadband, gap-pair commit window, and the escape-exit
        # ramp stabilise the run vector's inputs.
        self.steering_enabled = True

        # ── STAGE 2 REBUILD: ANTICIPATION GATE (reconnect anticipation) ──
        # Master anticipation gate: False (default) => AttackController /
        # AttackManager consume pure geometry byte-identically; the
        # perception/intent objects are never invoked.  True => perception
        # (AttackPerception) + intent (BallCarrierIntent / SupportRunnerIntent
        # / AttackIntent) run each OPEN_PLAY frame, stamp state on the BC /
        # support runners / world, and bias BC steering (SCOOT escape).
        # Dark-safe OFF by default so the stable geometry path is untouched.
        self.anticipation_enabled = False

        # Anticipation sensitivity (metres / tryline) — BC intent thresholds.
        self.anticip_scoot_dist_m = 3.0    # inside this -> SCOOT (escape/offload)
        self.anticip_pass_dist_m = 5.0     # inside this -> PASS (offload window)
        self.anticip_kick_dist_m = 10.0    # inside this -> KICK
        self.anticip_kick_tryline_m = 20.0 # tryline distance below which KICK is considered

        # Cone hysteresis (metres): a challenger must be closer than the
        # incumbent nearest-defender BY this margin before the avoidance
        # cone re-targets.  Kills near-tie nearest-defender flips.
        self.cone_hysteresis_m = 0.6

        # Lane deadband (metres): the committed lane-drift direction only
        # flips when |bc.y - centre_y| exceeds this band, so a carrier
        # straddling midfield cannot oscillate the drift vector.
        self.lane_deadband_m = 1.5

        # Gap commit window (wall-clock seconds): the selected gap-defender
        # pair is held through x-order churn; expiry or member loss refits
        # deterministically by (x, player_id).
        self.gap_commit_window_s = 0.75

        # Escape-exit ramp (wall-clock seconds): after the post-PTB escape
        # window ends, the forced-scoot weight tapers linearly from its
        # blend value to zero over this duration instead of snapping in
        # one frame.
        self.escape_exit_ramp_s = 0.30

        # Vector-flip audit threshold (degrees): a run-vector rotation
        # larger than this between frames emits a VECTOR_FLIP macro event
        # into the Task XII dump chain (audit-only).
        self.vector_flip_threshold_deg = 60.0

        # Early-settle window (wall-clock seconds, Stage-3F determinism).
        # Mirrors the post-PTB marker freeze so both sides settle together.
        self.settle_window_s = 1.0

        # Identity hysteresis (metres): a FIRST_RECEIVER/SWEEP holder keeps
        # its slot while within this distance of its lane anchor and has
        # not crossed sides of the ruck.  Prevents frame-to-frame flips on
        # small perturbations.
        self.identity_hysteresis_m = 2.0

        # Lane-read event threshold (metres): a LANE_READ macro event is
        # emitted whenever a support runner's issued lane target moves by
        # more than this between frames.
        self.lane_read_delta_m = 1.0

        # Depth law: target_x = bc.x - depth_m (attack runs +X, so depth
        # is BEHIND the ball carrier).  SWEEP is the DEEPEST outlet (the
        # widest runner sits furthest behind the BC to offer a passing
        # lane), so its depth exceeds the interior supports.
        self.depth_fr_m = 3.0
        self.depth_support_b_m = 6.0
        self.depth_support_c_m = 9.0
        self.depth_sweep_m = 12.0

        # Lane law: target_y = ruck_y + open_side * lane_m.  Anchoring to
        # ruck_y (NOT bc.y) is what locks lane drift.
        self.lane_fr_m = 2.0
        self.lane_support_b_m = 6.0
        self.lane_support_c_m = 10.0
        self.lane_sweep_m = 16.0
        self.lane_weak_support_m = -6.0

        # Minimum Y separation between adjacent authored lanes (metres).
        self.stagger_min_m = 2.0

        # Support-runner movement: same speed as the legacy chase for
        # continuity, scaled for lane arrival.
        self.support_speed_mps = 4.0
