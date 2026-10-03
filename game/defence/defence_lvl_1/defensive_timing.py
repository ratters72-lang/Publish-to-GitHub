"""
defensive_timing.py  STEP03A10 Defensive Timing & Reaction Curves.

REDESIGN (ball_owner era): Tracks the universal defence target (from
get_defence_target()) instead of BC only.  This means defence timing
correctly responds to:
  - BC movement when ball_owner == "BC"
  - Ball movement during kicks (ball_owner == "KICK")
  - Loose ball movement (ball_owner == "LOOSE")

PURPOSE
-------
Modifies *when* defenders move, not *where* or *how*.
Runs BEFORE all movement computation in OPEN_PLAY.
Defenders hold position while any timing delay is active.

DESIGN
------
- Per-defender timing attributes (reaction_timer, retreat_delay, etc.)
  are stored via getattr/setattr on the defender Player objects,
  matching the existing pattern for lane_index, depth_class, etc.

- The ``timing_locked`` flag is the single output:
    True   defender must NOT move, must NOT update target_x/y.
    False  defender proceeds to normal movement rules.

- ``DefensiveTiming.update()`` is called ONCE per OPEN_PLAY frame,
  immediately after classification and lane_index computation,
  BEFORE any priority gates or target mapping.
"""

import math
from player.player import DefenderRole
from world.world_state import get_defence_target


class DefensiveTiming:
    """Computes per-defender timing delays for OPEN_PLAY defence.

    Uses the universal defence target (from get_defence_target())
    instead of BC-only tracking, so timing correctly handles kicks,
    loose balls, and all ownership transitions.

    AI_ENGINE_FIX: get_defence_target() returns (tx, ty) only.
    Entity info for velocity/type tracking is obtained directly
    from world.ball_carrier / world.ball.

    Usage in DefensiveBehaviourUnified._apply_open_play_unified():

        1. classify_defender_depth() for each defender
        2. _compute_lane_indices() for each defender
        3. self.def_timing.update(world, defenders, dt)
        4. priority gates (markers, freeze_timer, timing_locked)
        5. role-based target mapping
    """

    def __init__(self, tuners):
        self.tuners = tuners

        # Previous frame target state (universal, not BC-only)
        self._prev_target_x: float | None = None
        self._prev_target_y: float | None = None
        self._prev_target_vx: float | None = None

        # Previous frame entity type ("Player", "Ball", None)
        # Used to detect BCBall semantic transitions so we skip
        # movement-based triggers on handover frames.
        self._prev_target_type: str | None = None

    # Helper: get target entity (for velocity/type tracking)

    @staticmethod
    def _get_target_entity(world):
        """Return the current tactical target entity.

        Returns (target_x, target_y, entity) where entity is the
        ball carrier (if owned) or the ball object (if loose/kick).

        AI_ENGINE_FIX: Entity is NOT obtained from get_defence_target()
        (which now returns only (tx, ty)).  Instead we read directly
        from world.ball_carrier / world.ball.
        """
        bc = world.ball_carrier
        if bc is not None:
            return bc.x, bc.y, bc
        b = getattr(world, "ball", None)
        if b is not None:
            bx = getattr(b, "true_x_m", getattr(b, "x", 60.0))
            by = getattr(b, "true_y_m", getattr(b, "y", 35.0))
            return bx, by, b
        return 60.0, 35.0, None

    #
    # MAIN ENTRY - called every OPEN_PLAY frame
    #

    def update(self, world, defenders, dt):
        """Run ALL timing subsystems.

        Uses ``get_defence_target(world)`` to get the current target
        (BC when owned, ball when in flight/loose).  All timing delays
        key off the universal target.

        Order matters - later subsystems may override flags set by earlier
        ones.  The aggregate ``timing_locked`` flag is set last.
        """

        #
        # ROLE GOVERNANCE: all timing yielded - no reaction delays,
        # no hesitation, no compression, no retreat delays.
        #
        if getattr(world, 'role_governs', False):
            for d in defenders:
                d.timing_locked = False
                d.reaction_timer = 0.0
                d.retreat_delay = 0.0
                d.lateral_hesitation = 0.0
                d.step_stagger = 0.0
            #
            # CRITICAL: Track target state during role governance.
            #
            # Without this, the first frame AFTER governance ends will
            # see _prev_target_x/y/vx as None and trigger a first-frame
            # reaction delay on EVERY defender.  This freezes the entire
            # defensive line for 0.15-0.5s and blocks the pre-tackle
            # trigger because speed=0 fails the min-speed gate.
            #
            # By tracking target each governance frame, the transition
            # out of governance is seamless - no false reaction delay
            # fires, defenders keep moving, and tackles can be
            # initiated immediately.
            #
            tx, ty, te = self._get_target_entity(world)
            self._prev_target_x = tx
            self._prev_target_y = ty
            self._prev_target_vx = getattr(te, "vx", 0.0)
            self._prev_target_type = type(te).__name__
            return

        # Extract target info for this frame
        bc = world.ball_carrier

        #
        # POST-RUCK QUIET WINDOW - EARLY OPEN_PLAY SETTLING
        #
        # Immediately after a PTB the game enters early OPEN_PLAY while
        # attackers retarget support lines and defenders re-anchor /
        # retreat to depth.  Re-arming timing locks in this window keeps
        # the whole SECONDARY+PASSIVE line PASSIVE/locked and produces
        # the TIMING_LOCK_POST_RUCK / LATE_DEPTH_SWITCH floods seen in
        # the freeze/reactivity audits.  Clear ALL timing locks for the
        # first ~0.5s of simulated time after the PTB (Stage 3F: dt-based,
        # tick-rate independent) and keep the prev-target baseline fresh so
        # the grace exit is seamless (mirrors the role-governance path).
        #
        # STAGE 3F: the quiet window is wall-clock seconds, not a frame
        # count.  The old `frames_since_ptb < 30` gate made this 0.5s grace
        # window stretch to 1.0s @30fps / shrink to 0.25s @120fps, so timing
        # re-armed at a different BC position each tick rate and spacing/
        # pacing inherited nondeterministic frame drift.
        quiet_window_s = float(getattr(
            self.tuners, "post_ruck_quiet_window_s", 0.5
        ))
        frames_since_ptb = getattr(world, "frames_since_ptb", None)
        seconds_since_ptb = getattr(
            world,
            "seconds_since_ptb",
            None if frames_since_ptb is None else frames_since_ptb / 60.0,
        )
        if seconds_since_ptb is not None and seconds_since_ptb < quiet_window_s:
            for d in defenders:
                d.reaction_timer = 0.0
                d.retreat_delay = 0.0
                d.lateral_hesitation = 0.0
                d.step_stagger = 0.0
                d.step_stagger_active = False
                d.timing_locked = False
                d._retreat_hesitation_fired = False
            self._store_prev_target(world)
            return

        #
        # LINE-BREAK GATE - POST-RUCK LOCK SCOPE
        #
        # Once BC has advanced past the defensive line anchor, the
        # post-ruck / early-OPEN_PLAY timing locks (reaction_timer,
        # retreat_delay, lateral_hesitation, step_stagger) must NEVER be
        # re-armed.  A BC that has broken the line re-triggers
        # _update_reaction_delays / _update_staggered_step_up on nearly
        # every frame, producing a persistent timing_locked=True that
        # FREEZE_AUDIT and the reactivity audit read as
        # TIMING_LOCK_POST_RUCK even though the line has broken.
        # Line-break defence is owned by the chase/collapse pipeline,
        # not by timing locks - locks may only exist while BC is still
        # behind/at the line.
        #
        anchor_x = getattr(world, "defensive_line_anchor", 0.0)
        if bc is not None and anchor_x != 0.0 and bc.x > anchor_x:
            for d in defenders:
                d.reaction_timer = 0.0
                d.retreat_delay = 0.0
                d.lateral_hesitation = 0.0
                d.step_stagger = 0.0
                d.step_stagger_active = False
                d.timing_locked = False
            # Keep the prev-target baseline fresh so dropping back behind
            # the line does not fire a false first-frame reaction delay.
            self._store_prev_target(world)
            return

        fr = world.first_receiver

        # 1. Detect target changes - set reaction_timer
        self._update_reaction_delays(world, defenders, dt)

        # 2. Staggered step-up sequencing
        self._update_staggered_step_up(world, defenders, bc, dt)

        # 3. Retreat hesitation when BC breaks the line
        self._update_retreat_hesitation(world, defenders, bc, dt)

        # 4. Lateral hesitation (anti-overcommit)
        self._update_lateral_hesitation(world, defenders, dt)

        # 5. Compression timing (gradual tighten)
        self._update_compression_timing(world, defenders, bc, fr, dt)

        # 6. Marker & dummy half micro-delays
        self._update_marker_dh_timing(world, defenders, bc, dt)

        # 7. Set aggregate timing_locked flag (runs last - after all decays)
        self._update_timing_locked_flag(defenders)

        # 8. Store target state for next frame
        self._store_prev_target(world)

    #
    # 1. REACTION DELAY
    #

    def _store_prev_target(self, world):
        """Store the current universal defence target for next-frame comparison."""
        tx, ty, te = self._get_target_entity(world)
        self._prev_target_x = tx
        self._prev_target_y = ty
        self._prev_target_vx = getattr(te, "vx", 0.0)
        self._prev_target_type = type(te).__name__

    def _update_reaction_delays(self, world, defenders, dt):
        """Detect target direction/position changes and set reaction_timer
        for SECONDARY/PASSIVE defenders only.

        PRIMARY / MARKER / DUMMY_HALF defenders are never delayed
        by reaction_timer - they must stay live and responsive.

        A "significant" change is any of:
          - Target.vx changes sign (forward -> stopped, stopped -> forward)
          - Target position jumps > reaction_position_change_m in one frame
          - Target lateral movement > lateral_small_threshold

        When triggered, eligible defenders get reaction_timer = computed base.
        On subsequent frames, reaction_timer is decremented by dt.
        While reaction_timer > 0, the defender is timing_locked.
        """
        t = self.tuners

        # Decay existing timers regardless of trigger
        for d in defenders:
            rt = getattr(d, "reaction_timer", 0.0)
            if rt > 0:
                d.reaction_timer = max(0.0, rt - dt)

        # Get current universal target via get_defence_target (2 values)
        tx, ty = get_defence_target(world)
        _, _, te = self._get_target_entity(world)
        cur_type = type(te).__name__

        #
        # ENTITY-TYPE TRANSITION GUARD (Problem C)
        #
        # When the target entity type changed (e.g. PlayerBall on
        # kick, BallPlayer on catch), the position/velocity difference
        # represents the gap between two different objects - NOT genuine
        # target movement.  Skip ALL movement-based triggers.
        #
        # The baseline is already stored by _store_prev_target() at end
        # of update(), so next frame compares correct same-type values.
        #
        if (self._prev_target_type is not None
                and self._prev_target_type != cur_type):
            return

        # Detect target change trigger
        triggered = False
        if self._prev_target_x is not None and self._prev_target_y is not None:
            dx = abs(tx - self._prev_target_x)
            dy = abs(ty - self._prev_target_y)
            dvx = (getattr(te, "vx", 0.0) - self._prev_target_vx
                   if self._prev_target_vx is not None else 0.0)

            # Position jump
            if dx > t.reaction_position_change_m or dy > t.reaction_lateral_change_m:
                triggered = True

            # Direction change: vx crossed zero (sign change)
            prev_vx = self._prev_target_vx
            cur_vx = getattr(te, "vx", 0.0)
            if prev_vx is not None and prev_vx != 0:
                if cur_vx == 0 or (cur_vx > 0) != (prev_vx > 0):
                    triggered = True

            # Sudden deceleration/acceleration
            if abs(dvx) > t.reaction_accel_threshold_mps:
                triggered = True
        # else: first frame (no previous target) - observe only,
        #       do NOT trigger.  Baseline stored by _store_prev_target().

        if triggered:
            for d in defenders:
                # Role gate (Problem B)
                # Only SECONDARY and PASSIVE defenders get reaction
                # delays.  PRIMARY / MARKER / DUMMY_HALF stay live.
                depth_class = getattr(d, "depth_class", "PASSIVE")
                if depth_class not in ("SECONDARY", "PASSIVE"):
                    d.reaction_timer = 0.0
                    continue

                base = self._compute_reaction_base(d)
                #
                # ONE-SHOT GUARD: never refresh an active decaying
                # timer.  A moving BC re-triggers this block on nearly
                # every frame (acceleration/deceleration noise between
                # frames); refreshing reaction_timer = base every frame
                # permanently re-arms the delay and keeps the entire
                # SECONDARY+PASSIVE line timing_locked for the whole
                # approach (the TIMING_LOCK_POST_RUCK mass-lock seen
                # in the audit).  Only (re)arm when idle or when the
                # new base is meaningfully larger than the remaining
                # timer (a genuinely bigger reaction needed).
                #
                cur = getattr(d, "reaction_timer", 0.0)
                if cur <= 0.0 or base > cur + 0.05:
                    d.reaction_timer = base

    def _compute_reaction_base(self, d):
        """Compute reaction_base_s for a single defender.

        Factors (all additive):
          - role factor: MARKER_A/B fastest, PRIMARY, SECONDARY, PASSIVE slowest
          - lane_index offset: centre (0) fastest, edges (+/-3) slower
          - depth_class offset: PRIMARY fastest, PASSIVE slowest
        """
        t = self.tuners

        # Role factor
        role = getattr(d, "defender_role", None)
        if role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
            role_factor = t.reaction_role_factor.get("MARKER", t.reaction_base_s)
        elif role == DefenderRole.DUMMY_HALF:
            role_factor = t.reaction_role_factor.get("PRIMARY", t.reaction_base_s)
        else:
            depth_class = getattr(d, "depth_class", "PASSIVE")
            role_factor = t.reaction_role_factor.get(depth_class, t.reaction_base_s)

        # Lane index offset
        lane_idx = abs(getattr(d, "lane_index", 0))
        lane_offset = lane_idx * t.reaction_lane_offset_s

        # Depth class offset
        depth_class = getattr(d, "depth_class", "PASSIVE")
        depth_offset = t.reaction_depth_offset_s.get(depth_class, 0.2)

        return t.reaction_base_s * role_factor + lane_offset + depth_offset

    #
    # 2. STAGGERED STEP-UP
    #

    def _update_staggered_step_up(self, world, defenders, bc, dt):
        """Create wave-like step-up by staggering defender initiation.

        When BC is approaching the defensive line (bc.x is moving toward
        anchor_x + threshold), centre lanes step first, edges step last.

        PRIMARY step-up before SECONDARY, before PASSIVE.

        PRIMARY / MARKER / DUMMY_HALF defenders get zero stagger
        so they never get timing-locked from this subsystem.

        Each defender gets a ``step_stagger`` timer based on:
          step_stagger = |lane_index| * step_stagger_lane_factor
                         + depth_class_offset
        """
        t = self.tuners
        anchor_x = getattr(world, "defensive_line_anchor", 0.0)
        if anchor_x == 0:
            return

        # Only activate when BC is approaching within step-up range
        if bc is None:
            return
        bc_dist = anchor_x - bc.x
        if bc_dist > t.step_stagger_activation_range_m or bc_dist < -t.step_stagger_deactivation_m:
            # BC too far - decay any existing step_stagger timers and clear active flag
            for d in defenders:
                ss = getattr(d, "step_stagger", 0.0)
                if ss > 0:
                    d.step_stagger = max(0.0, ss - dt)
                d.step_stagger_active = False  # BC left range - allow re-trigger next time
            return

        # BC is in step-up range - set stagger timers if not already set
        for d in defenders:
            # PRIMARY / MARKER / DUMMY_HALF: no stagger (zero)
            depth_class = getattr(d, "depth_class", "PASSIVE")
            if depth_class == "PRIMARY" or self._is_marker(d):
                d.step_stagger = 0.0
                d.step_stagger_active = False
                continue

            # One-shot guard: once stagger has been set and is counting down,
            # don't re-trigger.  Without this, PASSIVE defenders get a new
            # stagger timer EVERY frame when BC is in range, keeping them
            # timing_locked permanently (the freeze that only the middle 4
            # defenders move while outer defenders are statues).
            if getattr(d, "step_stagger_active", False):
                d.step_stagger = max(0.0, getattr(d, "step_stagger", 0.0) - dt)
                continue

            # Don't re-trigger if stagger already active
            if getattr(d, "step_stagger", 0.0) > 0:
                d.step_stagger = max(0.0, getattr(d, "step_stagger", 0.0) - dt)
                continue

            # Only set stagger for non-frozen defenders
            if d.frozen:
                continue

            lane_idx = abs(getattr(d, "lane_index", 0))

            # Base stagger from lane offset (centre first)
            stagger = lane_idx * t.step_stagger_lane_factor

            # Depth class offset: PRIMARY first (0s), SECONDARY (+0.05s), PASSIVE (+0.1s)
            depth_offset = t.step_stagger_depth_offset.get(depth_class, 0.1)
            stagger += depth_offset

            # Mark as having fired (one-shot guard) even if stagger is 0
            # centre SECONDARY with 0 stagger should still be considered
            # "staggered" so the guard prevents re-trigger.
            d.step_stagger_active = True
            if stagger > 0:
                d.step_stagger = stagger

    #
    # 3. RETREAT HESITATION
    #

    def _update_retreat_hesitation(self, world, defenders, bc, dt):
        """Prevent instant backpedal when BC breaks the defensive line.

        When bc.x moves behind anchor_x + threshold:
          - Defender enters retreat_delay.
          - During retreat_delay: no backward movement, only lateral shuffle.
          - After retreat_delay expires: normal retreat permitted.

        "Behind" means BC is deeper (larger X) than the anchor line,
        i.e. the BC has pierced the defensive line.

        PRIMARY and SECONDARY defenders are EXEMPT from retreat_delay:
        they never hesitate - they press up / chase / tackle.  Only
        PASSIVE/BACKFIELD defenders should hesitate on retreat.
        Without this exemption, every defender freezes when BC crosses
        the anchor threshold, and the perpetual re-trigger (bc_past_anchor
        is still True when the timer expires) keeps them frozen forever.

        July 2026 FIX: When depth_class is PRIMARY or SECONDARY, clear ALL
        timing flags (reaction_timer, lateral_hesitation, step_stagger,
        step_stagger_active, timing_locked) so defenders who are promoted
        to these roles can immediately pursue the BC without delay.

        July 2026 FIX 2: One-shot guard (_retreat_hesitation_fired) prevents
        the perpetual re-trigger that kept PASSIVE defenders permanently
        timing_locked when BC is past the anchor.  Without this guard,
        every time the 0.3s retreat_delay expires while BC is still past
        anchor, the delay re-fires - creating a freeze loop.
        """
        t = self.tuners
        anchor_x = getattr(world, "defensive_line_anchor", 0.0)
        if anchor_x == 0:
            return

        if bc is None:
            return

        # BC is "behind the line" when bc.x is significantly past anchor_x
        bc_past_anchor = bc.x > anchor_x + t.retreat_threshold_m

        for d in defenders:
            depth_class = getattr(d, "depth_class", "PASSIVE")
            # PRIMARY/SECONDARY never retreat - they press up / chase.
            # Clear ALL timing flags so promoted defenders act immediately.
            if depth_class in ("PRIMARY", "SECONDARY"):
                d.retreat_delay = 0.0
                d.reaction_timer = 0.0
                d.lateral_hesitation = 0.0
                d.step_stagger = 0.0
                d.step_stagger_active = False
                d.timing_locked = False
                d._retreat_hesitation_fired = False
                continue

            rd = getattr(d, "retreat_delay", 0.0)
            hes_fired = getattr(d, "_retreat_hesitation_fired", False)

            if bc_past_anchor and rd <= 0 and not hes_fired:
                # Enter retreat delay (not currently in delay and BC just broke line)
                # ONE-SHOT GUARD: only fire once per line-break event.  Without this,
                # the delay re-triggers every time the timer expires while BC is still
                # past the anchor, creating a perpetual 0.3s freeze loop that locks
                # PASSIVE defenders at target_x = current_x.
                d.retreat_delay = t.retreat_delay_s
                d._retreat_hesitation_fired = True
            elif bc_past_anchor and rd > 0:
                # Already in retreat delay - count down
                d.retreat_delay = max(0.0, rd - dt)
            elif not bc_past_anchor:
                # BC is no longer behind the line - clear delay faster
                if rd > 0:
                    d.retreat_delay = max(0.0, rd - dt * 2.0)
                # Reset one-shot guard so the delay can fire again when BC next breaks
                d._retreat_hesitation_fired = False

    #
    # 4. LATERAL HESITATION (ANTI-OVERCOMMIT)
    #

    def _update_lateral_hesitation(self, world, defenders, dt):
        """Prevent defenders from magnetically tracking small target lateral movements.

        Threshold hierarchy:
          - Target lateral change < lateral_small_threshold_m  IGNORE (no timer)
          - Target lateral change < lateral_moderate_threshold_m  add hesitation
          - Target lateral change >= lateral_moderate_threshold_m  react IMMEDIATELY
        """
        t = self.tuners

        # Decay existing hesitation timers
        for d in defenders:
            lh = getattr(d, "lateral_hesitation", 0.0)
            if lh > 0:
                d.lateral_hesitation = max(0.0, lh - dt)

        # Detect target lateral change
        if self._prev_target_y is None:
            return

        tx, ty, _ = self._get_target_entity(world)

        lat_change = abs(ty - self._prev_target_y)

        # Ignore small movements
        if lat_change < t.lateral_small_threshold_m:
            return

        # Large change - no hesitation (immediate reaction)
        if lat_change >= t.lateral_moderate_threshold_m:
            # Clear any existing hesitation
            for d in defenders:
                d.lateral_hesitation = 0.0
            return

        # Moderate change - add hesitation
        for d in defenders:
            # Don't overwrite an active larger hesitation
            if getattr(d, "lateral_hesitation", 0.0) <= 0:
                d.lateral_hesitation = t.lateral_hesitation_s

    #
    # 5. COMPRESSION TIMING
    #

    def _update_compression_timing(self, world, defenders, bc, fr, dt):
        """Defence compresses based on BC proximity to anchor.

        Compression activates when:
          - BC is close enough to anchor (within compression_activation_distance_m)

        The FR threat gate has been removed - compression should respond to
        BC threat alone.  Previously, FR being >10m from BC would suppress
        compression entirely, causing the line to collapse late.

        Before activation: ``compression_active`` = False (lane spacing stays wide)
        After activation:  ``compression_active`` = True, timer lerps 0->1 over
                           compression_duration_s seconds.
        """
        t = self.tuners
        anchor_x = getattr(world, "defensive_line_anchor", 0.0)
        if anchor_x == 0:
            return

        if bc is None:
            return

        # Threat check: BC close to line (FR gate removed)
        # Compression used to require FR within compression_threat_window_m
        # of BC, which meant outer defenders never compressed when FR was
        # >10m from BC.  This collapsed the line late instead of early.
        # Now compression activates on BC proximity alone - the real threat.
        bc_to_anchor = anchor_x - bc.x
        bc_close = 0 < bc_to_anchor < t.compression_activation_distance_m
        should_compress = bc_close

        # Set/clear compression_active and decay timer
        for d in defenders:
            ca = getattr(d, "compression_active", False)
            ct = getattr(d, "compression_timer", 0.0)

            if should_compress:
                # Activate compression if not active
                if not ca:
                    d.compression_active = True
                    d.compression_timer = 0.0

                # Race timer forward
                if ct < 1.0:
                    d.compression_timer = min(1.0, ct + dt / t.compression_duration_s)
            else:
                # Deactivate compression
                if ca:
                    d.compression_active = False
                if ct > 0:
                    d.compression_timer = max(0.0, ct - dt * 2.0)  # decay faster

    #
    # 6. MARKER & DUMMY HALF TIMING
    #

    def _update_marker_dh_timing(self, world, defenders, bc, dt):
        """Apply marker and dummy-half-specific micro-delays.

        Markers:
          - Already handled in _update_reaction_delays via stationary check.

        DH defender (LINE role guarding the dummy half):
          - Adds dh_defender_ptb_delay_s delay before stepping into line.
          - This prevents the DH defender from over-committing to
            a PTB that hasn't completed.
        """
        t = self.tuners

        for d in defenders:
            role = getattr(d, "defender_role", None)

            # DH defender (LINE role): add delay after PTB
            if role == DefenderRole.LINE:
                # Check if we're in POST_PTB_RESET / early OPEN_PLAY
                dh_ptb = getattr(d, "dh_defender_ptb_active", False)
                if dh_ptb and getattr(d, "dh_defender_delay", 0.0) <= 0:
                    d.dh_defender_delay = t.dh_defender_ptb_delay_s
                    d.dh_defender_ptb_active = False  # one-shot

                # Decay delay
                dd = getattr(d, "dh_defender_delay", 0.0)
                if dd > 0:
                    d.dh_defender_delay = max(0.0, dd - dt)

    #
    # 7. TIMING-LOCKED FLAG
    #

    def _update_timing_locked_flag(self, defenders):
        """Set ``timing_locked`` = True if ANY timing delay is active.

        Timing delays that lock movement:
          - reaction_timer > 0           (reaction delay)
          - retreat_delay > 0            (retreat hesitation)
          - lateral_hesitation > 0       (lateral hesitation)
          - step_stagger > 0             (staggered step-up)

        Timing states that do NOT lock movement:
          - compression_active / compression_timer  (these modify Y only)

        When timing_locked = True:
            target_x = d.x
            target_y = d.y
            move_speed = 0.0
        """
        for d in defenders:
            locked = (
                getattr(d, "reaction_timer", 0.0) > 0
                or getattr(d, "retreat_delay", 0.0) > 0
                or getattr(d, "lateral_hesitation", 0.0) > 0
                or getattr(d, "step_stagger", 0.0) > 0
            )
            d.timing_locked = locked

    #
    # PUBLIC STATUS QUERIES
    #

    @staticmethod
    def is_timing_locked(d) -> bool:
        """Return True if *d* is currently held by a timing delay."""
        return getattr(d, "timing_locked", False)

    @staticmethod
    def get_compression_factor(d) -> float:
        """Return compression lerp factor [0..1] for *d*.

        0.0 = fully wide (pre-compression).
        1.0 = fully compressed (converged on FR lane).
        """
        if not getattr(d, "compression_active", False):
            return 0.0
        return getattr(d, "compression_timer", 0.0)

    #
    # INTERNAL HELPERS
    #

    @staticmethod
    def _is_marker(d):
        return getattr(d, "defender_role", None) in (
            DefenderRole.MARKER_A,
            DefenderRole.MARKER_B,
        )

    def reset(self):
        """Clear all timing state (call on phase transitions e.g. POST_PTB_RESET)."""
        self._prev_target_x = None
        self._prev_target_y = None
        self._prev_target_vx = None
        self._prev_target_type = None
