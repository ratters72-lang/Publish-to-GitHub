"""
AttackController — BC steering brain.

Geometry-driven, phase-gated, momentum-consistent.
No AttackerAI, no sine noise, no direct vx/vy writes.

Movement is applied by setting:
    - bc.target_x
    - bc.target_y
    - bc.move_speed

Player.update() handles acceleration, facing, and clamping.
"""

import math


class AttackController:
    """Applies stable BC steering using geometry, perception, and run logic.

    TASK XIII: an optional ``steer_tuners`` (AttackStructureTuners) arms
    the BC-steering stabiliser — defender-cone hysteresis, lane-drift
    deadband, gap-pair commit window, escape-exit ramp, and the vector-
    flip audit.  With ``steer_tuners=None`` (or ``steering_enabled``
    False) every statement below matches the legacy path byte-for-byte.
    """

    def __init__(self, geometry, perception, run_logic, steer_tuners=None):
        self.geometry = geometry
        self.perception = perception
        self.run_logic = run_logic
        self.steer_tuners = steer_tuners
        # STAGE 2: ballistic-carrier intent object, created lazily in
        # apply_vector() so the default (anticipation off) never imports it.
        self._ball_carrier_intent = None

    def _steering_on(self) -> bool:
        tuners = getattr(self, "steer_tuners", None)
        return bool(getattr(tuners, "steering_enabled", False)) if tuners else False

    def apply_vector(self, bc, world, dt):
        """Compute and apply a stable run vector to BC target."""
        from world.world_state import GameSubState

        # Gate: attack movement runs in open-play phases and PTB (DH run)
        # PTB is included so the DH/BC gets escape-window scoot + geometry
        # steering immediately after ball transfer, not after a long delay.
        if world.sub_state not in (
            GameSubState.OPEN_PLAY,
            GameSubState.POST_RUCK_OPEN_PLAY,
            GameSubState.PTB,
        ):
            return

        _steer_on = self._steering_on()

        # ── 1. Build geometry layers ──
        sideline = self.geometry.sideline_geometry(bc)
        forward = self.geometry.forward_corridor(bc)
        defenders = world.defending_team
        def_cone = self.geometry.defender_cone(bc, defenders)
        lane = self.geometry.lane_geometry(bc)
        gap = self.geometry.gap_geometry(bc, defenders)

        geom = {
            "sideline": sideline,
            "forward": forward,
            "def_cone": def_cone,
            "lane": lane,
            "gap": gap,
        }

        # ── 1b. TASK XIII: BC steering stabilisation hook ──
        # Cone hysteresis / lane deadband / gap-pair commit wrap the raw
        # geometry BEFORE RunLogic blends it.  Self-gated inside the
        # authority; with the gate off this import never runs.
        if _steer_on:
            from game.attack.attack_lvl_1.bc_steering_authority import (
                stabilise_geometry,
            )
            geom = stabilise_geometry(
                world, bc, geom, self.steer_tuners, dt
            )

        # ── STAGE 2: ANTICIPATION RECONNECT (dark-safe) ──
        # When AttackStructureTuners.anticipation_enabled is on, compute the
        # perception (AttackPerception) + BC intent (BallCarrierIntent) and
        # stamp them on the BC so the Stage 7 decision engine and verifiers
        # can consume them.  SCOOT forces the post-PTB escape branch; RUN is
        # the geometry default.  PASS/KICK are recorded for Stage 7 (no
        # pass/kick execution exists yet).  Default (off) is byte-identical.
        _anticip_on = bool(
            getattr(self.steer_tuners, "anticipation_enabled", False)
        ) if self.steer_tuners else False
        _anticip_scoot = False
        if _anticip_on:
            from game.attack.attack_lvl_2.ball_carrier_intent import BallCarrierIntent
            if self._ball_carrier_intent is None:
                self._ball_carrier_intent = BallCarrierIntent()
            per = self.perception.compute(bc)
            intent = self._ball_carrier_intent.decide(
                bc, per, tuners=self.steer_tuners
            )
            bc._perception = per
            bc._anticipation_intent = intent
            _anticip_scoot = (intent == "SCOOT")

        # ── 2. Compute base run vector (unit direction) ──
        rx, ry = self.run_logic.compute_run_vector(bc, geom)

        # ── 3. Post-PTB escape window — forced lateral scoot ──
        # When the DH/BC has just picked up the ball from PTB, the system
        # forces a strong lateral escape vector so the carrier clears the
        # ruck and avoids the frozen markers.  After the escape window
        # expires (~0.33s at 60fps), geometry-driven movement resumes.
        # STAGE 3F: the escape window is wall-clock seconds, not frame count.
        # The old `frames_since_ptb < escape_window_frames` gate made this
        # ~0.33s window stretch/shrink with tick rate, so the BC escaped at a
        # different duration each frame rate and downstream spacing/pacing
        # inherited nondeterministic frame drift.  Fall back to the frame
        # comparison only when the time-based field is absent (legacy tests).
        _escape_window_frames = getattr(world, "escape_window_frames", 20)
        _seconds_since_ptb = getattr(world, "seconds_since_ptb", None)
        # STAGE 2: a SCOOT intent forces the escape branch even outside the
        # PTB window (a pressured carrier must clear the ruck immediately).
        if _seconds_since_ptb is None:
            escape_window = (
                getattr(world, "frames_since_ptb", 0) < _escape_window_frames
            ) or _anticip_scoot
        else:
            escape_window = (
                _seconds_since_ptb < (_escape_window_frames / 60.0)
            ) or _anticip_scoot

        # TASK XII strong-scoot blend: 92% forced vector / 8% geometry.
        # Bound BEFORE the branch so the escape-exit ramp (which runs in the
        # NON-escape else-branch) can reference it even when the BC never
        # entered an escape window this frame (e.g. kickoff/catch frames).
        BLEND = 0.95

        if escape_window:
            # Determine which lateral direction to scoot
            ruck_y = getattr(world, "ruck_y_m", bc.y)
            lateral_dir = 1.0 if bc.y > ruck_y else -1.0

            # Enforce minimum escape angle of 60°
            angle_deg = max(world.escape_angle_deg, 60)
            angle_rad = math.radians(angle_deg)

            # Forced escape vector: strongly lateral, slightly forward
            forced_rx = math.cos(angle_rad)
            forced_ry = math.sin(angle_rad) * lateral_dir

            # Stronger scoot blend: 92% forced vector, 8% geometry
            rx = forced_rx * BLEND + rx * (1 - BLEND)
            ry = forced_ry * BLEND + ry * (1 - BLEND)

            # Harder scoot acceleration in escape window
            bc.move_speed = bc.max_speed_mps * 1.20
            # Task XII audit provenance (read-only stamp, no behaviour).
            bc._vector_source = "escape"
            # TASK XIII: arm the escape-exit ramp so the FIRST inactive
            # frame tapers instead of snapping.
            if _steer_on:
                from game.attack.attack_lvl_1.bc_steering_authority import (
                    note_escape_active,
                )
                note_escape_active(world)
        else:
            # Normal geometry-driven movement at 90% top speed
            bc.move_speed = bc.max_speed_mps * 0.9
            # Task XII audit provenance (read-only stamp, no behaviour).
            bc._vector_source = "geometry"
            # TASK XIII: ESCAPE-EXIT RAMP — taper the forced scoot vector
            # out over escape_exit_ramp_s of wall-clock sim time instead
            # of cutting to pure geometry in one frame.
            if _steer_on:
                from game.attack.attack_lvl_1.bc_steering_authority import (
                    escape_exit_blend,
                )
                _fw = escape_exit_blend(world, BLEND, dt, self.steer_tuners)
                if _fw > 0.0:
                    # Rebuild the SAME forced scoot vector the final
                    # escape frame used, then blend by the ramp weight.
                    ruck_y_r = getattr(world, "ruck_y_m", bc.y)
                    lateral_dir_r = 1.0 if bc.y > ruck_y_r else -1.0
                    angle_rad_r = math.radians(max(world.escape_angle_deg, 45))
                    frx = math.cos(angle_rad_r)
                    fry = math.sin(angle_rad_r) * lateral_dir_r
                    rx = frx * _fw + rx * (1 - _fw)
                    ry = fry * _fw + ry * (1 - _fw)
                    bc._vector_source = "escape_exit"

        # Re-normalise after lateral boost / escape blend
        mag = (rx * rx + ry * ry) ** 0.5
        if mag > 0:
            rx /= mag
            ry /= mag

        # TASK XIII: vector-flip macro audit (records the normalised run
        # vector; emits VECTOR_FLIP on large rotations — no behaviour).
        if _steer_on:
            from game.attack.attack_lvl_1.bc_steering_authority import (
                record_run_vector,
            )
            record_run_vector(world, rx, ry, self.steer_tuners)

        # ── 4. Convert run vector → target position ahead ──
        TARGET_DISTANCE = 7.0  # metres ahead (stronger projection)
        desired_x = bc.x + rx * TARGET_DISTANCE
        desired_y = bc.y + ry * TARGET_DISTANCE

        # ── 5. Smooth target to kill jitter (momentum-consistent) ──
        SMOOTH = 0.25
        bc.target_x = bc.target_x * (1 - SMOOTH) + desired_x * SMOOTH
        bc.target_y = bc.target_y * (1 - SMOOTH) + desired_y * SMOOTH

        # ── 6. Let Player.update() handle physics ──
        # (move_speed is already set so Player.update() uses it)
