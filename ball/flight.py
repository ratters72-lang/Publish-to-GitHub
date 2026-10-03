"""One integrator for the ball's flight, shared by the sim and the predictor.

The ball's published landing point (``landing_x_m`` / ``landing_y_m``) is what
the kick-chase AI runs to, and it is also a claim about where the ball will
actually be.  If the two are produced by different code they drift apart and
the catcher arrives at the wrong place.

So the airborne physics lives here, once.  :func:`step` is the single
integrator, :meth:`Ball.update` runs it live, and :func:`predict_landing`
runs the *same* function forward on a scratch copy to work out where the
flight ends.  There is no second trajectory model to fall out of sync.

The step deliberately preserves the original ball's drag law and integration
order - drag, gravity, position - and layers the aerodynamic accelerations
and the attitude integration on top, so a default kick still flies the way it
always did while carrying an orientation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ball.attitude import BallAttitude
from ball.aerodynamics import (
    AeroState,
    DeterministicWobble,
    apply_horizontal_drag,
    step_air,
)
from ball.kick_types import KickProfile

#: Fixed timestep used to predict a flight.  Matches the game's nominal
#: 60 Hz frame so the prediction tracks the live integration closely.
PREDICT_DT: float = 1.0 / 60.0

#: Longest flight the predictor will integrate before giving up (s).
PREDICT_MAX_TIME: float = 9.0


@dataclass
class FlightState:
    """Everything :func:`step` needs: position, velocity and attitude."""

    x: float
    y: float
    z: float
    vx: float
    vy: float
    vz: float
    t: float
    ref_speed: float
    profile: KickProfile
    attitude: BallAttitude
    wobble: DeterministicWobble
    last_aero: AeroState = field(default_factory=AeroState)

    def clone(self) -> "FlightState":
        """A scratch copy, safe to integrate without touching the live ball."""
        return FlightState(
            x=self.x,
            y=self.y,
            z=self.z,
            vx=self.vx,
            vy=self.vy,
            vz=self.vz,
            t=self.t,
            ref_speed=self.ref_speed,
            profile=self.profile,
            attitude=clone_attitude(self.attitude),
            wobble=self.wobble,
        )


def clone_attitude(attitude: BallAttitude) -> BallAttitude:
    """Copy a :class:`BallAttitude` field by field."""
    clone = BallAttitude()
    clone.pitch = attitude.pitch
    clone.roll = attitude.roll
    clone.yaw = attitude.yaw
    clone.pitch_rate = attitude.pitch_rate
    clone.roll_rate = attitude.roll_rate
    clone.yaw_rate = attitude.yaw_rate
    return clone


def step(state: FlightState, dt: float, gravity: float) -> AeroState:
    """Advance one airborne frame.  Mutates ``state`` in place.

    Order matters and is preserved from the original ball: the horizontal
    drag bleed first, then gravity, then the position update.  The
    aerodynamic accelerations and the attitude integration are inserted
    between gravity and the position update.
    """
    attitude = state.attitude
    profile = state.profile

    aero = step_air(
        state.vx,
        state.vy,
        state.vz,
        attitude.long_axis(),
        attitude.spin_vector(),
        attitude.spin_magnitude(),
        profile,
        state.wobble,
        state.t,
        state.ref_speed,
    )

    state.vx, state.vy = apply_horizontal_drag(
        state.vx, state.vy, state.vz, dt, profile.drag_gain
    )

    state.vz -= gravity * dt
    state.vx += aero.ax * dt
    state.vy += aero.ay * dt
    state.vz += aero.az * dt

    attitude.step(
        dt,
        pitch_extra=aero.pitch_rate_extra,
        yaw_extra=aero.yaw_rate_extra,
        spin_decay_per_s=profile.spin_decay_per_s,
    )

    state.x += state.vx * dt
    state.y += state.vy * dt
    state.z += state.vz * dt
    state.t += dt
    state.last_aero = aero
    return aero
@dataclass
class LandingPrediction:
    """Where and when the flight ends."""

    x: float
    y: float
    time: float
    speed: float


def predict_landing(
    state: FlightState,
    gravity: float,
    dt: float = PREDICT_DT,
    max_time: float = PREDICT_MAX_TIME,
) -> LandingPrediction:
    """Integrate the flight forward until the ball reaches the ground.

    Runs on a scratch copy, so the live ball is untouched.  The final
    position is linearly interpolated onto ``z = 0`` so the answer does not
    depend on where the discrete steps happened to land.
    """
    scratch = state.clone()

    previous_z = scratch.z
    previous_x = scratch.x
    previous_y = scratch.y
    previous_t = scratch.t

    while scratch.t < max_time:
        previous_z = scratch.z
        previous_x = scratch.x
        previous_y = scratch.y
        previous_t = scratch.t

        step(scratch, dt, gravity)

        if scratch.z <= 0.0 and scratch.vz < 0.0:
            span = previous_z - scratch.z
            if span > 1e-9:
                frac = previous_z / span
                frac = 0.0 if frac < 0.0 else (1.0 if frac > 1.0 else frac)
            else:
                frac = 1.0
            return LandingPrediction(
                x=previous_x + (scratch.x - previous_x) * frac,
                y=previous_y + (scratch.y - previous_y) * frac,
                time=previous_t + (scratch.t - previous_t) * frac,
                speed=math.hypot(math.hypot(scratch.vx, scratch.vy), scratch.vz),
            )

    # Never came down (should be impossible with gravity on): report the
    # furthest point reached rather than inventing a landing.
    return LandingPrediction(
        x=scratch.x,
        y=scratch.y,
        time=scratch.t,
        speed=math.hypot(math.hypot(scratch.vx, scratch.vy), scratch.vz),
    )


def ballistic_landing(
    x: float,
    y: float,
    z: float,
    vx: float,
    vy: float,
    vz: float,
    gravity: float,
) -> LandingPrediction:
    """The drag-free projectile answer, kept for comparison and diagnostics.

    This is what the ball used to publish.  It is no longer the published
    landing point (the real flight has lift, drag and wobble), but the
    verifier uses it to measure how far the modelled flight departs from a
    plain projectile.
    """
    if gravity <= 0.0:
        return LandingPrediction(x, y, 0.0, math.hypot(vx, vy))
    disc = vz * vz + 2.0 * gravity * z
    if disc < 0.0:
        disc = 0.0
    t = (vz + math.sqrt(disc)) / gravity
    return LandingPrediction(
        x=x + vx * t,
        y=y + vy * t,
        time=t,
        speed=math.hypot(vx, vy),
    )
