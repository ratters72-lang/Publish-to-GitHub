"""Aerodynamics of a kicked rugby ball: drag, Magnus lift and instability.

Three effects are modelled, and the third is the one that matters most for
the *look* of a real NRL kick.

1. DRAG - the ball decelerates in flight.  The baseline horizontal drag is
   the same law the original ball used, so the macro trajectory stays put
   for the default kick type.

2. MAGNUS LIFT - a spinning ball in flight is pushed sideways, because the
   relative wind on the spinning leather is faster on one side than the
   other.  The force is ``k * (omega x v)``: perpendicular to both the spin
   axis and the velocity.  This is why a spiral punt flies on a tight line
   while a floater hangs and swerves.

3. INSTABILITY / WOBBLE - the ball is kicked with its nose at a roughly
   fixed attitude, but its FLIGHT PATH is not fixed: a lofted kick leaves
   the boot climbing at 50 degrees and lands descending at 50 the other way.
   The angle between the nose and the relative wind therefore grows all
   through the flight, and that growing misalignment is what the wobble
   feeds on.  Meanwhile the aerodynamic moment that would realign the nose
   weakens as the ball slows, while the angular momentum keeping it rigid
   barely decays at all - so the ball cannot correct, and the nose starts to
   wander.  That is the late-flight oscillation seen on every bomb and
   floater.

   :func:`instability_index` quantifies that collapse.  It is small at the
   moment of the kick - the nose is in the wind and the restoring moment is
   strongest - and climbs through the descent, so the wobble is *born late*
   and grows, which is exactly the behaviour the eye reads as instability.
   A ball at the apex of its arc is NOT treated as unstable: it is briefly
   travelling with its nose, which is the stable configuration.

Determinism
-----------
The wobble is pseudo-random to the eye but strictly deterministic: it is a
sum of incommensurate sine waves whose phases come from an integer seed via
a hash-style mixing function, and it is a pure function of elapsed flight
time rather than of the integration step count.  The standard ``random``
module is deliberately NOT used, so the ball can never perturb the global
RNG stream the rest of the simulation depends on, a headless probe replays
an identical trajectory every run, and the ball's own landing point can be
predicted ahead of time by a separate integration.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ball.kick_types import BASELINE_DRAG, KickProfile

TAU = math.tau

#: Forward airspeed at which a kick counts as fully "fast" for the purpose
#: of how much restoring authority the airstream still has (m/s).
AERO_REF_SPEED: float = 25.0

#: Peak lateral wobble acceleration at full instability (m/s^2).
#: Deliberately small: the ball is a projectile first, and a kicked ball that
#: swings metres off its own line reads as a banana kick rather than a
#: wobble.  This is the ceiling on the late-flight wander and
#: ``verify_ball_physics.py`` asserts the resulting footprint stays inside the
#: 1.5 m the catch system uses as its horizontal catch radius.
WOBBLE_MAX_ACCEL: float = 1.6

#: Angular frequency of the wobble (rad/s) - a slow, heavy oscillation of
#: about 0.6 Hz, like a ball visibly hunting on the way down.
WOBBLE_FREQ: float = 3.9

#: Frequency of the vertical component (the ball dipping and floating).
WOBBLE_VERTICAL_FREQ: float = 2.6

#: Frequency of the slow precession drift that walks the nose around.
WOBBLE_DRIFT_FREQ: float = 0.9

#: Magnus coefficient at reference spin and speed.
MAGNUS_REF: float = 1.15e-3

#: Spin (rad/s) at which a ball counts as "half rigid" gyroscopically.
#: A well-spun spiral sits well above this; a floater sits well below.
RIGIDITY_SPIN_REF: float = 45.0

#: How much a rigidly spinning ball resists developing angle of attack.
RIGIDITY_RESISTANCE: float = 0.5

#: Fraction of the restoring authority lost at the launch airspeed.  The
#: aerodynamic moment that aligns the nose with the wind scales with the
#: airspeed, so the same misalignment wobbles far more late in the flight.
SLOW_AUTHORITY_LOSS: float = 0.55


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else (high if value > high else value)


# =====================================================================
# DETERMINISTIC NOISE
# =====================================================================
class DeterministicWobble:
    """Reproducible pseudo-random oscillation, driven by elapsed time.

    Every method is a pure function of ``(seed, t)`` - never of the number
    of integration steps taken - so the trajectory can be predicted ahead of
    time by a separate integration and the two agree.
    """

    __slots__ = ("_phases",)

    def __init__(self, seed: int = 0) -> None:
        self._phases = tuple(self._phase_for(seed, index) for index in range(4))

    @staticmethod
    def _phase_for(seed: int, index: int) -> float:
        """Map (seed, index) to a phase in [0, TAU) with no RNG involved."""
        mixed = (int(seed) * 2_654_435_761 + (index + 1) * 40_503) % 1_000_003
        return (mixed / 1_000_003.0) * TAU

    def lateral(self, t: float) -> float:
        """Signed lateral oscillation in [-1, 1] (across the line of flight)."""
        p0, p1, p2, _ = self._phases
        return 0.62 * math.sin(WOBBLE_FREQ * t + p0) + 0.38 * math.sin(
            WOBBLE_FREQ * 1.7 * t + p1
        ) * math.cos(WOBBLE_DRIFT_FREQ * t + p2)

    def vertical(self, t: float) -> float:
        """Signed vertical oscillation in [-1, 1] (the float / dip)."""
        _, _, p2, p3 = self._phases
        return 0.70 * math.sin(WOBBLE_VERTICAL_FREQ * t + p3) + 0.30 * math.sin(
            WOBBLE_DRIFT_FREQ * t + p2
        )

    def precession(self, t: float) -> float:
        """Signed yaw wander in [-1, 1] - the nose hunting around."""
        p0, _, p2, p3 = self._phases
        return 0.65 * math.sin(WOBBLE_DRIFT_FREQ * t + p2) + 0.35 * math.sin(
            WOBBLE_FREQ * 0.55 * t + p0 + p3
        )

    def bob(self, t: float) -> float:
        """Signed pitch wander in [-1, 1] - the nose bobbing up and down."""
        _, p1, _, p3 = self._phases
        return 0.6 * math.sin(WOBBLE_DRIFT_FREQ * 1.35 * t + p1) + 0.4 * math.sin(
            WOBBLE_VERTICAL_FREQ * 0.8 * t + p3
        )


# =====================================================================
# INSTABILITY
# =====================================================================
def angle_of_attack(
    long_axis: tuple[float, float, float],
    vx: float,
    vy: float,
    vz: float,
) -> float:
    """Angle between the ball's nose and the air it is flying through.

    Folded to ``[0, pi/2]``: a ball travelling tail-first is as
    aerodynamically aligned as one travelling nose-first, and both are stable
    configurations.  ``0`` means the nose is in the wind; ``pi/2`` means the
    ball is travelling broadside and the air is hitting it side-on.
    """
    speed = math.hypot(math.hypot(vx, vy), vz)
    if speed < 1e-6:
        return math.pi * 0.5
    ax, ay, az = long_axis
    dot = (ax * vx + ay * vy + az * vz) / speed
    return math.acos(_clamp(dot, -1.0, 1.0) if dot >= 0.0 else -_clamp(dot, -1.0, 1.0))


def instability_index(
    aoa_rad: float,
    forward_speed: float,
    spin_mag: float,
    profile: KickProfile,
    ref_speed: float,
) -> float:
    """How unstable the ball is *right now*, in [0, 1].

    Driven by the ANGLE OF ATTACK rather than by airspeed alone, because that
    is the misalignment the aerodynamic restoring moment actually has to
    fight.  A rugby ball is kicked with its nose at a roughly fixed attitude
    while its flight path swings through tens of degrees, so down the arc of
    a bomb the nose and the relative wind end up close to perpendicular.
    That misalignment is the whole source of the late waddle.

    Three factors decide how much of it shows:

    * the angle itself - 0 with the nose in the wind, 1 when broadside;
    * gyroscopic rigidity - a heavily spun ball resists being turned, which
      is why a tight spiral stays on a string;
    * the FORWARD airspeed, which is what carries the ball downfield.

    The forward speed is deliberately used rather than the total speed.  A
    ball at the apex of a bomb has almost no total airspeed (all of the
    vertical component is spent) but it is not unstable there - it is
    momentarily travelling with its nose, which is the stable configuration,
    and it still has all of its downfield pace.  Keying on the total speed
    would put the instability at the top of the arc instead of at the end of
    the descent, which is exactly backwards from what the eye sees.
    """
    if ref_speed <= 1e-6:
        return 0.0
    alignment_term = math.sin(_clamp(aoa_rad, 0.0, math.pi * 0.5))
    rigidity = spin_mag / (spin_mag + RIGIDITY_SPIN_REF) if spin_mag > 0.0 else 0.0
    slow = 1.0 - SLOW_AUTHORITY_LOSS * _clamp(
        forward_speed / ref_speed, 0.0, 1.0
    )
    value = (
        alignment_term
        * profile.instability_gain
        * slow
        * (1.0 - RIGIDITY_RESISTANCE * rigidity)
    )
    return _clamp(value, 0.0, 1.0)


# =====================================================================
# THE FORCE MODEL
# =====================================================================
@dataclass
class AeroState:
    """Everything the aerodynamic step computes for one frame."""

    ax: float = 0.0                # total aerodynamic acceleration, world x
    ay: float = 0.0                # total aerodynamic acceleration, world y
    az: float = 0.0                # total aerodynamic acceleration, world z
    lift_accel: float = 0.0        # magnitude of the Magnus contribution
    wobble_accel: float = 0.0      # magnitude of the wobble contribution
    instability: float = 0.0       # 0.0 stable .. 1.0 fully unstable
    aoa_deg: float = 0.0           # nose-vs-relative-wind angle (diagnostic)
    pitch_rate_extra: float = 0.0  # rad/s added to the end-over-end tumble
    yaw_rate_extra: float = 0.0    # rad/s added to the precession


def apply_horizontal_drag(
    vx: float,
    vy: float,
    vz: float,
    dt: float,
    drag_gain: float,
) -> tuple[float, float]:
    """The original ball's drag law, preserved exactly at ``drag_gain == 1``.

    Drag bleeds the horizontal components only, and is deliberately reduced
    near the apex so a lofted ball does not stall forwards in mid-air - the
    same rule the pre-rewrite ball used.
    """
    effective = BASELINE_DRAG * drag_gain
    if abs(vz) < 1.0:
        effective *= 0.25
    return vx * (1.0 - effective * dt), vy * (1.0 - effective * dt)


def magnus_acceleration(
    vx: float,
    vy: float,
    vz: float,
    spin: tuple[float, float, float],
    profile: KickProfile,
) -> tuple[float, float, float]:
    """``a = k * (omega x v)`` - the sideways force from the spinning ball."""
    k = MAGNUS_REF * profile.lift_gain
    if k == 0.0:
        return 0.0, 0.0, 0.0
    sx, sy, sz = spin
    return (
        k * (sy * vz - sz * vy),
        k * (sz * vx - sx * vz),
        k * (sx * vy - sy * vx),
    )


def wobble_acceleration(
    vx: float,
    vy: float,
    wobble: DeterministicWobble,
    t: float,
    instability: float,
    profile: KickProfile,
) -> tuple[float, float, float]:
    """Lateral + vertical wobble acceleration, perpendicular to travel.

    The lateral component acts across the line of flight (the ball swinging
    off the string); the vertical component makes it dip and float.
    """
    amp = WOBBLE_MAX_ACCEL * profile.wobble_gain * instability
    if amp <= 0.0:
        return 0.0, 0.0, 0.0

    horizontal = math.hypot(vx, vy)
    if horizontal < 1e-6:
        return 0.0, 0.0, amp * wobble.vertical(t) * 0.5

    # Left-hand perpendicular to the horizontal velocity.
    #
    # The vertical component is kept well below the lateral one: a vertical
    # force on a ball in flight changes its flight TIME, and therefore where
    # it lands downfield, whereas a lateral one only swings it off its line.
    # The lateral wander is the visible wobble; the vertical is the smaller
    # dip-and-float, and it must not be allowed to move the landing point the
    # kick-chase is running to.
    perp_x, perp_y = -vy / horizontal, vx / horizontal
    lateral = amp * wobble.lateral(t)
    vertical = amp * 0.35 * wobble.vertical(t)
    return perp_x * lateral, perp_y * lateral, vertical


def attitude_wander(
    wobble: DeterministicWobble,
    t: float,
    instability: float,
    profile: KickProfile,
) -> tuple[float, float]:
    """Yielded ``(pitch_rate_extra, yaw_rate_extra)`` in rad/s.

    These drive the *attitude* wobble rather than the path - the nose hunting
    around late in the flight.  Scaled so the wander is negligible at launch
    and unmistakable on the way down.
    """
    if instability <= 0.0:
        return 0.0, 0.0
    # ``wobble_gain`` is the profile's overall looseness, so it also scales
    # how much the NOSE is allowed to hunt: a tight spiral barely moves it
    # while a floater's wanders more.
    #
    # YAW is the precession, and it is the wander the eye actually reads: the
    # nose sweeping around the vertical while the ball hangs.  It barely
    # touches the angle of attack, because the relative wind is dominated by
    # the downfield velocity, so it can be generous.
    #
    # PITCH is the end-over-end bob, and it must stay tiny.  The weathercock
    # moment genuinely holds a kicked ball's nose near the elevation it was
    # struck at for the whole flight - which is precisely WHY the angle of
    # attack grows as the trajectory curves away beneath it.  A nose free to
    # bob would realign itself into the wind late on, collapse its own angle
    # of attack, and stop wobbling exactly when a real ball wobbles most.
    looseness = profile.wobble_gain if profile.wobble_gain < 1.0 else 1.0
    gain = instability * profile.instability_gain * looseness
    return gain * 0.12 * wobble.bob(t), gain * 0.60 * wobble.precession(t)


def step_air(
    vx: float,
    vy: float,
    vz: float,
    long_axis: tuple[float, float, float],
    spin: tuple[float, float, float],
    spin_mag: float,
    profile: KickProfile,
    wobble: DeterministicWobble,
    t: float,
    ref_speed: float,
) -> AeroState:
    """Compute one frame of aerodynamic acceleration.

    Gravity is NOT included here - the Ball owns gravity.  This returns only
    the aerodynamic contribution so the caller keeps a single integration
    loop and the flight stays auditable in one place.
    """
    forward = math.hypot(vx, vy)
    aoa = angle_of_attack(long_axis, vx, vy, vz)
    instability = instability_index(
        aoa, forward, spin_mag, profile, ref_speed
    )

    mx, my, mz = magnus_acceleration(vx, vy, vz, spin, profile)
    wx, wy, wz = wobble_acceleration(vx, vy, wobble, t, instability, profile)
    pitch_extra, yaw_extra = attitude_wander(wobble, t, instability, profile)

    return AeroState(
        ax=mx + wx,
        ay=my + wy,
        az=mz + wz,
        lift_accel=math.hypot(math.hypot(mx, my), mz),
        wobble_accel=math.hypot(math.hypot(wx, wy), wz),
        instability=instability,
        aoa_deg=math.degrees(aoa),
        pitch_rate_extra=pitch_extra,
        yaw_rate_extra=yaw_extra,
    )


def make_wobble(seed: int) -> DeterministicWobble:
    """Factory used by the Ball so the seed choice lives in one place."""
    return DeterministicWobble(seed)
