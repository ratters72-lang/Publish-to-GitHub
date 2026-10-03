"""Asymmetric ground behaviour of a rugby ball.

A rugby ball is not a sphere, and this module is where that matters most.

WHY A RUGBY BOUNCE IS NOT A BOUNCE
----------------------------------
A football (round ball) bounces the same way every time: the vertical
velocity is scaled by a restitution constant and reversed.  A rugby ball
cannot do that, because *what it lands on* changes every impact:

* landing on a POINT (``end_on`` near 1.0) deforms the leather and dies -
  the low, skidding, holding bounce that makes a kick-chaser's life easy;
* landing BROADSIDE (``end_on`` near 0.0) is the lively bounce;
* the ball is also still SPINNING, and a spinning ball carries tangential
  speed at its contact point - ``omega x r`` - which is converted into
  forward or sideways linear speed by the turf friction.  That is the
  "squirt" that sends a grubber shooting forward, or checking back into the
  in-goal.

THE ASYMMETRY
-------------
For a ball that is not spinning, the bounce is mirror-symmetric: reflect the
incoming velocity and the rebound reflects with it.  Here it is NOT, and the
reason is physical, not arbitrary.  The spin vector is an absolute quantity
in the world.  Reflect the ball's path but keep its spin, and the tangential
contact velocity ``omega x r`` does not reflect with it, so the rebound does
not mirror its launch.  A rugby ball genuinely behaves this way: the same
kick landing in the same place off the same boot squirts a different way
purely because of which way it was spinning.

ROLLING
-------
Once the bounce dies, the ball rolls.  Which rotation it uses depends on its
attitude: a ball rolling along its own long axis spins about that axis,
while a ball lying across its path tumbles end over end, once per
circumference, at ``speed / ROLL_RADIUS_M`` radians per second.  That
tumbling is the most recognisable piece of ground behaviour in the game and
is modelled explicitly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ball.attitude import BallAttitude
from ball.kick_types import BASELINE_BOUNCE, BASELINE_ROLL_FRICTION, KickProfile

#: Half the length of a Steeden - the radius it rolls/tumbles about (m).
ROLL_RADIUS_M: float = 0.14

#: Turf friction coefficient applied to the spin-driven contact slip.
SURFACE_FRICTION: float = 0.35

#: Hard ceiling on the tangential speed one impact may scrub off (m/s).
MAX_SQUIRT_MPS: float = 4.0

#: How strongly a point-first landing rebounds along the ball's own axis.
#: At 0.0 every impact would push straight up - the old sphere model.  At 1.0
#: an end-on ball would rebound purely horizontally.  0.8 is a prolate
#: spheroid that springs back the way it was compressed.
AXIS_DEFLECT: float = 0.80

#: Ceiling on the rolling tumble rate carried by the attitude (rad/s).
#: A ball rolling at 20 m/s turns over roughly 22 times a second, which no
#: eye and no 60 Hz renderer can follow - above about four turns a second it
#: simply strobes, so the visible attitude rate is clamped here.
MAX_ROLL_TUMBLE_RAD_S: float = 26.0

#: Fraction of the contact slip that is scrubbed off the ball's spin.
CONTACT_SPIN_LOSS: float = 0.55

#: Below this speed the ball stops tumbling and settles flat (m/s).
MIN_TUMBLE_SPEED_MPS: float = 0.35

#: Below this speed a rolling ball is treated as stationary (m/s).
SLEEP_SPEED_MPS: float = 0.02

#: How quickly the spiral (axial) spin scrubs off once the ball is on turf.
GROUND_ROLL_DECAY_PER_S: float = 2.2

#: Airborne spin decay while grounded (tumble keeps going a little/longer).
GROUND_TUMBLE_DECAY_PER_S: float = 0.25


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else (high if value > high else value)


def per_frame_to_second(per_frame_factor: float) -> float:
    """Convert the legacy per-frame friction multiply into a per-second rate.

    The original ball bled velocity with a bare ``vx *= 0.98`` every frame,
    which is frame-rate dependent.  Running the same factor at the game's
    60 Hz gives ``0.98 ** 60`` per second; inverting that gives the
    equivalent exponential rate used here, so the *feel* is preserved while
    the grind stops depending on the frame rate.
    """
    if per_frame_factor <= 0.0:
        return 0.0
    if per_frame_factor >= 1.0:
        return 0.0
    return -math.log(per_frame_factor) * 60.0


#: Exponential ground-drag rate equivalent to the legacy per-frame friction.
GROUND_DRAG_PER_S: float = per_frame_to_second(BASELINE_ROLL_FRICTION)


@dataclass
class BounceResult:
    """The outcome of one turf impact."""

    vx: float = 0.0            # rebound velocity, world x
    vy: float = 0.0            # rebound velocity, world y
    vz: float = 0.0            # rebound velocity, world z
    end_on: float = 0.0        # 1.0 = landed on a point, 0.0 = broadside
    incidence_deg: float = 0.0  # approach angle above the ground
    restitution: float = 0.0   # effective vertical restitution used
    squirt_mps: float = 0.0    # tangential speed scrubbed off by the spin
    squirt_x: float = 0.0      # the squirt resolved into world x
    squirt_y: float = 0.0      # the squirt resolved into world y
    axis_push_mps: float = 0.0  # rebound speed delivered along the ball's axis
    kick_on_mps: float = 0.0   # net change in speed along the original travel


def resolve_bounce(
    vx: float,
    vy: float,
    vz: float,
    attitude: BallAttitude,
    profile: KickProfile,
    bounce_base: float = BASELINE_BOUNCE,
) -> BounceResult:
    """Resolve one impact with the turf.

    Consumes the incoming velocity and attitude, and returns the rebound
    velocity along with the diagnostics the verifier asserts on.  The caller
    owns the attitude update (see :func:`apply_bounce_to_attitude`).
    """
    horizontal = math.hypot(vx, vy)
    descent = -vz
    incidence = math.degrees(math.atan2(descent, max(horizontal, 1e-6)))

    end_on = attitude.end_on_fraction(vx, vy, vz)

    # ── Restitution: lively broadside, dead on the point ──
    flat_rest = bounce_base * profile.bounce_liveliness
    point_rest = profile.end_on_restitution
    blend = end_on * end_on
    restitution = flat_rest * (1.0 - blend) + point_rest * blend
    # A steep landing stuffs more energy into the leather.
    restitution *= 0.72 + 0.28 * math.cos(math.radians(incidence))

    # ── Rebound direction: the ball springs back along its own axis ──
    # The leather compresses along the ball's long axis and pushes back the
    # way it was compressed, so a ball landing on a point rebounds along that
    # point instead of straight up.  Which way the nose happens to face at
    # the instant of contact therefore decides whether the ball kicks on or
    # checks back - which is exactly why a rugby league bounce is famously
    # unreadable.  The axis is folded to point upwards so the rebound always
    # leaves the ground.
    axis_x, axis_y, axis_z = attitude.long_axis()
    if axis_z < 0.0:
        axis_x, axis_y, axis_z = -axis_x, -axis_y, -axis_z

    deflect = end_on * AXIS_DEFLECT
    dir_x = axis_x * deflect
    dir_y = axis_y * deflect
    dir_z = (1.0 - deflect) + axis_z * deflect
    dir_len = math.sqrt(dir_x * dir_x + dir_y * dir_y + dir_z * dir_z)
    if dir_len < 1e-9:
        dir_x, dir_y, dir_z, dir_len = 0.0, 0.0, 1.0, 1.0
    dir_x /= dir_len
    dir_y /= dir_len
    dir_z /= dir_len

    # ── Spin-driven tangential squirt: friction OPPOSES the contact slip ──
    # Contact point sits one radius below the centre; the tangential velocity
    # carried there is the spin cross the contact offset, plus the ball's own
    # horizontal velocity.  A ball rolling without slipping has zero slip and
    # therefore takes no tangential impulse at all.
    sx, sy, sz = attitude.spin_vector()
    contact_x = vx + (-sy * ROLL_RADIUS_M)
    contact_y = vy + (sx * ROLL_RADIUS_M)
    slip = math.hypot(contact_x, contact_y)

    squirt_x = squirt_y = 0.0
    if slip > 1e-6:
        gain = SURFACE_FRICTION * (0.35 + 0.65 * profile.bounce_liveliness)
        # Friction can slow the ball but must never fling it backwards, so the
        # impulse is also capped against the speed it arrived with.
        magnitude = min(gain * slip, MAX_SQUIRT_MPS, 0.5 * horizontal + 1.5)
        squirt_x = -magnitude * contact_x / slip
        squirt_y = -magnitude * contact_y / slip

    rebound_speed = descent * restitution
    out_vx = vx + squirt_x + rebound_speed * dir_x
    out_vy = vy + squirt_y + rebound_speed * dir_y
    out_vz = rebound_speed * dir_z

    # Net change in speed along the original line of travel: positive means
    # the ball kicked on, negative means it checked back and lost pace.
    delta_x = out_vx - vx
    delta_y = out_vy - vy
    if horizontal > 1e-6:
        kick_on = (delta_x * vx + delta_y * vy) / horizontal
    else:
        kick_on = 0.0

    return BounceResult(
        vx=out_vx,
        vy=out_vy,
        vz=out_vz,
        end_on=end_on,
        incidence_deg=incidence,
        restitution=restitution,
        squirt_mps=math.hypot(squirt_x, squirt_y),
        squirt_x=squirt_x,
        squirt_y=squirt_y,
        axis_push_mps=rebound_speed * math.hypot(dir_x, dir_y),
        kick_on_mps=kick_on,
    )


def apply_bounce_to_attitude(
    attitude: BallAttitude,
    result: BounceResult,
    profile: KickProfile,
    incoming_horizontal_speed: float,
) -> None:
    """Update the attitude for the impact described by ``result``.

    Three things happen to the ball on contact:

    * the turf converts some axial spin into tumble - the ball starts to go
      end over end (strongly for a grubber, weakly for a spiral punt);
    * the slip scrubs spin off all three axes - an impact costs spin;
    * the nose is kicked towards the line of travel, because the rebound has
      just become the new direction of motion and the ball weathercocks into
      it.
    """
    horizontal = math.hypot(result.vx, result.vy)

    # 1. Turf picks up end-over-end tumble.
    tumble = profile.ground_tumble_gain * (0.55 + 0.45 * profile.pitch_rev_s / 9.0)
    attitude.pitch_rate += tumble * incoming_horizontal_speed * 0.9

    # 2. Friction scrubs spin off everything.
    slip = result.squirt_mps
    if slip > 1e-6:
        lost = _clamp(slip / max(incoming_horizontal_speed, 1e-6), 0.0, 1.0)
        keep = 1.0 - CONTACT_SPIN_LOSS * lost
        attitude.roll_rate *= keep
        attitude.pitch_rate *= keep
        attitude.yaw_rate *= keep

    # 3. Weathercock the nose into the new line of travel.
    if horizontal > 0.05:
        heading = math.atan2(result.vy, result.vx)
        delta = (heading - attitude.yaw + math.pi) % math.tau - math.pi
        attitude.yaw += delta * 0.55


def step_ground(
    vx: float,
    vy: float,
    attitude: BallAttitude,
    profile: KickProfile,
    dt: float,
    drag_rate_per_s: float = GROUND_DRAG_PER_S,
) -> tuple[float, float, bool]:
    """Advance a grounded ball by ``dt`` seconds.

    Returns ``(vx, vy, asleep)``.  Mutates ``attitude`` so a rolling ball
    visibly tumbles end over end at ``speed / ROLL_RADIUS_M`` rad/s, slowing
    with the ball until it settles lying flat.

    ``drag_rate_per_s`` lets the caller supply the grind rate, so the ball can
    keep honouring a tuned ``roll_friction``.
    """
    speed = math.hypot(vx, vy)

    if speed < SLEEP_SPEED_MPS:
        # Settled: bleed the spin off and let the ball lie flat.
        attitude.decay_spin(dt, GROUND_ROLL_DECAY_PER_S)
        attitude.pitch_rate *= 0.5
        attitude.pitch += (0.0 - attitude.pitch) * 0.25
        return 0.0, 0.0, True

    # ── friction ──
    bleed = math.exp(-drag_rate_per_s * dt)
    vx *= bleed
    vy *= bleed

    # ── rolling / tumbling ──
    if speed > MIN_TUMBLE_SPEED_MPS:
        heading = math.atan2(vy, vx)
        delta = (heading - attitude.yaw + math.pi) % math.tau - math.pi
        attitude.yaw += delta * min(1.0, 6.0 * dt)

        # Rolling without slipping: turn at speed / radius about the axis
        # perpendicular to the direction of travel.  The rate is clamped for
        # legibility - see MAX_ROLL_TUMBLE_RAD_S.
        target_rate = min(speed / ROLL_RADIUS_M, MAX_ROLL_TUMBLE_RAD_S)
        attitude.pitch_rate += (target_rate - attitude.pitch_rate) * min(1.0, 8.0 * dt)
        attitude.roll_rate *= math.exp(-GROUND_ROLL_DECAY_PER_S * dt * 0.5)
    else:
        # Too slow to tumble: settle flat.
        attitude.pitch_rate *= math.exp(-GROUND_TUMBLE_DECAY_PER_S * dt * 4.0)
        attitude.roll_rate *= math.exp(-GROUND_ROLL_DECAY_PER_S * dt)
        attitude.pitch += (0.0 - attitude.pitch) * min(1.0, 3.0 * dt)

    attitude.decay_spin(dt, 0.0)  # rates already handled above
    attitude.pitch = (attitude.pitch + attitude.pitch_rate * dt) % math.tau
    attitude.roll = (attitude.roll + attitude.roll_rate * dt) % math.tau
    attitude.yaw = (attitude.yaw + attitude.yaw_rate * dt) % math.tau

    return vx, vy, False
