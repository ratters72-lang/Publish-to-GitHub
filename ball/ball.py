"""The rugby league ball: flight physics, kicking model and presentation.

This is the ball the match knows.  It keeps the public surface every other
module already depends on - ``x_m``/``y_m``/``z_m``, ``vx``/``vy``/``vz``,
``grounded``, ``possessed_by``, ``true_x_m``/``true_y_m``, ``landing_x_m``/
``landing_y_m``, ``flight_time``, ``can_be_caught()``, ``spawn_kickoff()``,
``update(dt)`` and ``draw(screen)`` - and adds a real physical model behind
it.

WHAT CHANGED AND WHY
--------------------
The old ball was a point mass with one scalar bounce.  It could not carry
spin, so it could not tumble, could not squirt off the turf, and could not
wobble; and it was drawn as a fixed white ellipse that never even rotated.

The ball now owns:

* an ATTITUDE - three independent axes (pitch, roll, yaw) in
  :mod:`ball.attitude`, so a spiral and an end-over-end tumble are genuinely
  different motions rather than one "spin" number;
* a KICK TYPE - a spin, loft and stiffness profile in
  :mod:`ball.kick_types`, so a spiral punt, a bomb, a grubber and a floater
  land, bounce and tumble differently;
* AERODYNAMICS - drag, Magnus lift, and a wobble that is born late in the
  flight as the airspeed decays, in :mod:`ball.aerodynamics`;
* ASYMMETRIC TURF BEHAVIOUR - point-first bounces that die, broadside
  bounces that live, and a spin-driven squirt that does not mirror its
  launch, in :mod:`ball.bounce`;
* a genuine 3D SILHOUETTE - the ball is painted as a brown Steeden whose
  on-screen shape follows its orientation, in :mod:`ball.ball_renderer`.

The trajectory integrator is shared with the landing predictor
(:mod:`ball.flight`), so the published landing point is a real prediction of
the modelled flight rather than a drag-free approximation of a different one.

COMPATIBILITY NOTES
-------------------
* ``drag``, ``bounce_factor`` and ``roll_friction`` are still honoured;
  a kick type multiplies the baseline drag, and the bounce and roll still use
  the values on the instance.
* ``rotation`` is still published, now sourced from the ball's real roll.
* ``frozen`` exists as a plain attribute because ``world.world_state`` sets
  it on the ball.
"""

from __future__ import annotations

import math

from ball.attitude import BallAttitude
from ball.aerodynamics import AeroState, make_wobble
from ball.bounce import (
    BounceResult,
    GROUND_DRAG_PER_S,
    apply_bounce_to_attitude,
    per_frame_to_second,
    resolve_bounce,
    step_ground,
)
from ball.ball_renderer import draw_ball, draw_shadow, build_shape
from ball.flight import FlightState, LandingPrediction, predict_landing, step
from ball.kick_types import (
    BASELINE_BOUNCE,
    BASELINE_DRAG,
    BASELINE_ROLL_FRICTION,
    KICK_DEFAULT,
    KickProfile,
    KickType,
    profile_with,
    resolve_profile,
)

#: How far off the flight line the nose sits when a profile does not point it
#: fully into the wind (degrees).  Gives a bomb or a floater a visible skew
#: the aerodynamic restoring moment then fights against.
MUZZLE_SKEW_DEG: float = 34.0

#: Airspeed used as the instability reference before any kick (m/s).
DEFAULT_REF_SPEED: float = 25.0

#: Horizontal speed below which the ball is considered stopped (m/s).
STOP_SPEED_MPS: float = 0.01

#: Wobble seed stride, so consecutive kicks wobble differently but reproducibly.
WOBBLE_SEED_STRIDE: int = 7919

#: Nose elevation while the ball is carried in two hands (radians).
CARRY_PITCH_RAD: float = 0.30


class Ball:
    """Rugby league ball with correct height-based catch threshold (1.5m)."""

    def __init__(self, field):
        self.field = field

        # Position (metres)
        self.x_m = 60.0
        self.y_m = 35.0
        self.z_m = 1.0

        # Velocity (m/s)
        self.vx = 0.0
        self.vy = 0.0
        self.vz = 0.0

        # Physics
        self.gravity = 9.8
        self.drag = BASELINE_DRAG
        self.bounce_factor = BASELINE_BOUNCE
        self.roll_friction = BASELINE_ROLL_FRICTION

        # State
        self.grounded = True
        self.kicked = False
        self.frozen = False   # written externally by world.world_state

        # Published visual rotation (degrees), sourced from the ball's real
        # roll every frame.  The old fixed spin/wobble scalars are gone: the
        # attitude and the kick profile drive both now, so they had nothing
        # left to tune.
        self.rotation = 0.0
        self.time_airborne = 0.0

        # TRUE ball position (snaps instantly to carrier - never eased)
        self.true_x_m = 60.0
        self.true_y_m = 35.0

        # Landing prediction
        self.landing_x_m = 0.0
        self.landing_y_m = 0.0
        self.flight_time = 0.0

        # Possession
        self.possessed_by = None

        # Height threshold for catching
        self.catch_height_m = 1.5

        # ── Kicking model ──
        self.kick_type: KickType = KICK_DEFAULT
        self.profile: KickProfile = resolve_profile(KICK_DEFAULT)
        self.attitude = BallAttitude()
        self._kick_count: int = 0
        self._wobble_seed: int = 0
        self._wobble = make_wobble(0)
        self._ref_speed: float = DEFAULT_REF_SPEED

        # Diagnostics (read-only for probes and the verifier)
        self.last_aero = AeroState()
        self.last_bounce: BounceResult | None = None
        self.bounce_count: int = 0

    # ----------------------------------------------------------------
    # COMPATIBILITY PROPERTIES
    # ----------------------------------------------------------------
    @property
    def predicted_landing_x(self):
        return self.landing_x_m

    @property
    def predicted_landing_y(self):
        return self.landing_y_m

    @property
    def is_in_air(self) -> bool:
        """Ball is in the air if not grounded and not possessed."""
        return not self.grounded and self.possessed_by is None

    @property
    def speed_mps(self) -> float:
        """Total airspeed (m/s)."""
        return math.hypot(math.hypot(self.vx, self.vy), self.vz)

    @property
    def instability(self) -> float:
        """How far into the unstable part of the flight the ball is, 0..1."""
        return self.last_aero.instability

    # ----------------------------------------------------------------
    # KICK TYPE SELECTION
    # ----------------------------------------------------------------
    def set_kick_type(self, kick_type) -> KickProfile:
        """Choose the profile the next kick will use.  Returns the profile."""
        self.profile = resolve_profile(kick_type)
        self.kick_type = self.profile.kick_type
        return self.profile

    def set_wobble_seed(self, seed: int) -> None:
        """Pin the wobble phase (used by probes that need a fixed replay)."""
        self._wobble_seed = int(seed)
        self._wobble = make_wobble(self._wobble_seed)

    # ----------------------------------------------------------------
    # KICK
    # ----------------------------------------------------------------
    def kick(self, power, angle_deg, direction_deg, kick_type=None):
        """Launch the ball.

        ``kick_type`` is optional and defaults to the profile already set by
        :meth:`set_kick_type` (the spiral punt out of the box).  The launch
        angle is the caller's angle with the profile's loft bias applied and
        its ceiling respected, so naming a kick type is enough to ask for a
        flat grubber or a steep bomb.
        """
        profile = resolve_profile(kick_type) if kick_type is not None else self.profile
        self.profile = profile
        self.kick_type = profile.kick_type

        launch_deg = profile.clamped_loft_deg(angle_deg)
        angle = math.radians(launch_deg)
        direction = math.radians(direction_deg)

        speed = power

        self.vx = speed * math.cos(angle) * math.cos(direction)
        self.vy = speed * math.cos(angle) * math.sin(direction)
        self.vz = speed * math.sin(angle)

        # ── Attitude at the moment of contact ──
        # The nose is aimed down the flight line, skewed by however much this
        # kick type leaves it off the wind.  A spiral is dead straight; a
        # floater is noticeably off, which is what lets it drift.
        self._kick_count += 1
        self._ref_speed = max(1.0, math.hypot(math.hypot(self.vx, self.vy), self.vz))

        flight_azimuth = math.atan2(self.vy, self.vx)
        skew = math.radians(MUZZLE_SKEW_DEG) * (1.0 - profile.nose_into_wind)
        self.attitude.set_muzzle(profile.muzzle_pitch_deg, flight_azimuth + skew)
        self.attitude.set_spin(
            profile.roll_rad_s(),
            profile.pitch_rad_s(),
            profile.yaw_rad_s(),
        )

        # ── Deterministic wobble seed for this kick ──
        self._wobble_seed = (
            int(abs(round(self.x_m * 10.0)))
            + int(abs(round(self.y_m * 10.0))) * 31
            + int(abs(round(launch_deg * 10.0))) * 131
            + int(abs(round(direction_deg * 10.0))) * 613
            + int(abs(round(power * 10.0))) * 1_009
            + self._kick_count * WOBBLE_SEED_STRIDE
        )
        self._wobble = make_wobble(self._wobble_seed)

        # ── Published landing point ──
        # Integrated with the very same model the live ball flies, so the
        # kick-chase runs to where the ball is actually going.
        prediction = self.predict_landing()
        self.landing_x_m = prediction.x
        self.landing_y_m = prediction.y
        self.flight_time = prediction.time

        self.grounded = False
        self.kicked = True
        self.time_airborne = 0.0
        self.bounce_count = 0
        self.last_bounce = None

    def predict_landing(self) -> LandingPrediction:
        """Where and when the modelled flight will hit the ground."""
        return predict_landing(self._flight_state(), self.gravity)

    # ----------------------------------------------------------------
    # FLIGHT STATE BRIDGE
    # ----------------------------------------------------------------
    def _flight_state(self) -> FlightState:
        """Wrap the live ball in a :class:`FlightState`.

        The attitude object is passed by reference on purpose: the integrator
        advances it in place, so the ball's orientation is mutated exactly
        once per frame by exactly one code path.
        """
        profile = self.profile
        if self.drag != BASELINE_DRAG and BASELINE_DRAG > 0.0:
            # Honour an external tune of ``ball.drag`` by folding it into the
            # profile's drag multiplier.
            profile = profile_with(
                profile.kick_type,
                drag_gain=profile.drag_gain * (self.drag / BASELINE_DRAG),
            )

        return FlightState(
            x=self.x_m,
            y=self.y_m,
            z=self.z_m,
            vx=self.vx,
            vy=self.vy,
            vz=self.vz,
            t=self.time_airborne,
            ref_speed=self._ref_speed,
            profile=profile,
            attitude=self.attitude,
            wobble=self._wobble,
        )

    def _ground_drag_rate(self) -> float:
        """Per-second ground drag equivalent to the instance's roll_friction."""
        if self.roll_friction == BASELINE_ROLL_FRICTION:
            return GROUND_DRAG_PER_S
        return per_frame_to_second(self.roll_friction)

    # ----------------------------------------------------------------
    # UPDATE
    # ----------------------------------------------------------------
    def update(self, dt):
        """Advance the ball one frame.

        Phase order is unchanged from the original ball:

        1. possessed  - the true position snaps to the carrier, the visual
           ball eases onto it, and the ball is tucked for carrying;
        2. grounded   - turf friction plus rolling / end-over-end tumbling;
        3. airborne   - the shared flight integrator, then the turf impact.

        What is new is that the airborne branch carries an orientation and
        asymmetric aerodynamics, and the impact branch decides what the ball
        landed ON before deciding how it bounces.
        """
        if self.possessed_by is not None:
            self._update_possessed()
            return

        # TRUE position tracks visual when not possessed
        self.true_x_m = self.x_m
        self.true_y_m = self.y_m

        if self.grounded:
            self._update_grounded(dt)
            return

        self._update_airborne(dt)

    def _update_possessed(self):
        """Ball in hand: snap the truth, ease the picture, tuck the ball."""
        carrier = self.possessed_by

        self.true_x_m = carrier.x
        self.true_y_m = carrier.y

        # Visual ball position eases toward carrier
        self.x_m += (self.true_x_m - self.x_m) * 0.35
        self.y_m += (self.true_y_m - self.y_m) * 0.35

        # Ease height to carrying height
        self.z_m += (1.0 - self.z_m) * 0.35

        # Let velocity decay instead of instant zero
        self.vx *= 0.5
        self.vy *= 0.5
        self.vz = 0.0

        self.grounded = False

        # ── Carry attitude ──
        # Spinning stops the instant the ball is gathered; the ball is then
        # pointed the way the carrier is facing, with the nose slightly up
        # the way a ball is actually carried in two hands.
        self.attitude.clear_spin()
        facing = getattr(carrier, "facing", None)
        if facing is not None:
            delta = (facing - self.attitude.yaw + math.pi) % math.tau - math.pi
            self.attitude.yaw += delta * 0.25
        self.attitude.pitch += (CARRY_PITCH_RAD - self.attitude.pitch) * 0.2
        self.attitude.roll = 0.0
        self.rotation = math.degrees(self.attitude.roll)

    def _update_grounded(self, dt: float) -> None:
        """Rolling or resting on the turf."""
        self.vx, self.vy, asleep = step_ground(
            self.vx,
            self.vy,
            self.attitude,
            self.profile,
            dt,
            drag_rate_per_s=self._ground_drag_rate(),
        )
        if asleep:
            self.vx = 0.0
            self.vy = 0.0

        if abs(self.vx) < STOP_SPEED_MPS:
            self.vx = 0.0
        if abs(self.vy) < STOP_SPEED_MPS:
            self.vy = 0.0

        self.x_m += self.vx * dt
        self.y_m += self.vy * dt
        self.z_m = 0.0
        self.rotation = math.degrees(self.attitude.roll)

    def _update_airborne(self, dt: float) -> None:
        """In flight: the shared integrator, then the turf impact."""
        self.time_airborne += dt

        state = self._flight_state()
        aero = step(state, dt, self.gravity)

        # The integrator advanced the attitude in place; pull the new
        # position and velocity back onto the ball.
        self.x_m = state.x
        self.y_m = state.y
        self.z_m = state.z
        self.vx = state.vx
        self.vy = state.vy
        self.vz = state.vz
        self.last_aero = aero
        self.rotation = math.degrees(self.attitude.roll)

        self._resolve_ground_contact()

    def _resolve_ground_contact(self) -> None:
        """Decide what happens if the ball is at or below the turf.

        Two outcomes, and the threshold is the original ball's: a real impact
        (a meaningful downward speed) is resolved through the asymmetric
        bounce model, while a dribble down onto the grass simply settles.
        """
        if self.z_m > 0.0:
            return

        self.z_m = 0.0

        if abs(self.vz) <= 1.0:
            self.vz = 0.0
            self.grounded = True
            return

        incoming_horizontal = math.hypot(self.vx, self.vy)
        result = resolve_bounce(
            self.vx,
            self.vy,
            self.vz,
            self.attitude,
            self.profile,
            self.bounce_factor,
        )

        self.vx = result.vx
        self.vy = result.vy
        self.vz = result.vz

        apply_bounce_to_attitude(
            self.attitude, result, self.profile, incoming_horizontal
        )

        self.last_bounce = result
        self.bounce_count += 1
        self.rotation = math.degrees(self.attitude.roll)

    # ----------------------------------------------------------------
    # RESET
    # ----------------------------------------------------------------
    def spawn_kickoff(self):
        """Position the ball at the halfway line for kickoff.

        Resets all velocity and state so the ball is ready
        for a kickoff at the centre of the field.

        NOTE: This clears Ball.possessed_by (ball-internal field only).
        The world-level ball_owner / ball_carrier sync is handled
        externally by the kickoff engine / OPE.  These writes are
        deliberately NOT routed through set_ball_carrier() because
        this runs before any world ownership is established.
        """
        self.x_m = 60.0
        self.y_m = 35.0
        self.z_m = 1.0
        self.vx = 0.0
        self.vy = 0.0
        self.vz = 0.0
        self.grounded = False
        self.kicked = False
        self.possessed_by = None
        self.time_airborne = 0.0
        self.true_x_m = 60.0
        self.true_y_m = 35.0

        # Fresh attitude: no spin, nose level, pointing up-field.
        self.attitude = BallAttitude()
        self.rotation = 0.0
        self.bounce_count = 0
        self.last_bounce = None
        self.last_aero = AeroState()

    # ----------------------------------------------------------------
    # CAN BE CAUGHT?
    # ----------------------------------------------------------------
    def can_be_caught(self):
        """Ball is only catchable when below 1.5m and not possessed."""
        if self.possessed_by is not None:
            return False
        if self.grounded:
            return True  # grounded ball can always be picked up
        return self.z_m <= self.catch_height_m

    # ----------------------------------------------------------------
    # DIAGNOSTICS
    # ----------------------------------------------------------------
    def attitude_snapshot(self) -> dict[str, float]:
        """The three attitude axes and their rates, for probes and audits."""
        return self.attitude.snapshot()

    def flight_diagnostics(self) -> dict[str, float]:
        """A flat read-out of what the ball is doing right now."""
        return {
            "speed_mps": self.speed_mps,
            "instability": self.last_aero.instability,
            "lift_accel": self.last_aero.lift_accel,
            "wobble_accel": self.last_aero.wobble_accel,
            "spin_magnitude": self.attitude.spin_magnitude(),
            "spiral_fraction": self.attitude.spiral_fraction(),
            "nose_elevation_deg": self.attitude.nose_elevation_deg(),
            "bounces": float(self.bounce_count),
        }

    # ----------------------------------------------------------------
    # DRAW
    # ----------------------------------------------------------------
    def draw(self, screen):
        """Paint the shadow, then the ball, using its real orientation.

        The silhouette, the seam, the lace and the gloss all come from
        :mod:`ball.ball_renderer`; this method only validates that the ball
        is somewhere sane before anything is drawn, because a NaN position
        from the physics is the one failure that would otherwise take the
        render loop down.
        """
        px, py = self.field.metres_to_px(self.x_m, self.y_m)

        if not _finite(px) or not _finite(py):
            return
        if not _finite(self.z_m) or not _finite(self.attitude.pitch):
            return

        shape = build_shape(self, self.field)
        if shape is None:
            return
        if not _finite(shape.centre_x) or not _finite(shape.centre_y):
            return
        if not _finite(shape.half_length_px) or not _finite(shape.half_width_px):
            return

        draw_shadow(screen, self, self.field, shape)
        draw_ball(screen, self, self.field, self.speed_mps)


def _finite(value) -> bool:
    """True for a real, finite number."""
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)
