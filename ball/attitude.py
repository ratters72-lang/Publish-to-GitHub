"""Ball attitude — three independent rotation axes.

A kicked rugby ball is not a point.  It has an orientation, and that
orientation has three genuinely independent degrees of freedom:

    PITCH  elevation of the long axis (nose up / nose down).
           Advancing pitch = the ball tumbling END OVER END.  This is the
           dominant motion of a grubber and of a badly-struck bomb.

    ROLL   rotation ABOUT the long axis.  This is the spiral.  It does not
           change where the nose points — it changes which panel of leather
           faces the camera, and it is what makes a spiral punt look tight
           and a punt without it look loose.  It also decides which way the
           ball squirts off the turf.

    YAW    azimuth of the long axis in the horizontal plane.  Advancing yaw
           is PRECESSION: the nose wandering around the vertical.  A ball
           that is well spun resists precession; as the flight slows the
           restoring moment fades and the yaw swings — the "death wobble".

Because the three axes are stored and integrated separately (and can be
driven independently by :mod:`ball.aerodynamics`), a spiral punt, a bomb, a
grubber and a floater produce visibly different attitude histories even when
their launch speeds are identical.

Units: radians and radians per second.  Angles are wrapped to ``[0, TAU)``
so a long match cannot accumulate unbounded floats.
"""

from __future__ import annotations

import math

TAU = math.tau

#: The world "up" axis.  z is height; x and y are the field plane.
UP = (0.0, 0.0, 1.0)


def wrap_angle(angle: float) -> float:
    """Wrap ``angle`` into ``[0, TAU)``."""
    return angle % TAU


class BallAttitude:
    """Orientation and spin of the ball, on three independent axes."""

    __slots__ = ("pitch", "roll", "yaw", "pitch_rate", "roll_rate", "yaw_rate")

    def __init__(self) -> None:
        self.pitch: float = 0.0        # long-axis elevation
        self.roll: float = 0.0         # spin about the long axis
        self.yaw: float = 0.0          # long-axis azimuth

        self.pitch_rate: float = 0.0   # rad/s — end-over-end tumble
        self.roll_rate: float = 0.0    # rad/s — spiral
        self.yaw_rate: float = 0.0     # rad/s — precession

    # ---------------------------------------------------------------
    # SETUP
    # ---------------------------------------------------------------
    def set_muzzle(self, pitch_deg: float, yaw_rad: float) -> None:
        """Point the nose for the moment of contact (before the ball spins up)."""
        self.pitch = wrap_angle(math.radians(pitch_deg))
        self.yaw = wrap_angle(yaw_rad)
        self.roll = 0.0

    def set_spin(self, roll_rad_s: float, pitch_rad_s: float, yaw_rad_s: float) -> None:
        """Stamp the three independent spin rates at the moment of the kick."""
        self.roll_rate = roll_rad_s
        self.pitch_rate = pitch_rad_s
        self.yaw_rate = yaw_rad_s

    def clear_spin(self) -> None:
        self.pitch_rate = 0.0
        self.roll_rate = 0.0
        self.yaw_rate = 0.0

    # ---------------------------------------------------------------
    # INTEGRATION
    # ---------------------------------------------------------------
    def step(
        self,
        dt: float,
        pitch_extra: float = 0.0,
        yaw_extra: float = 0.0,
        spin_decay_per_s: float = 0.0,
    ) -> None:
        """Advance the attitude by ``dt`` seconds.

        ``pitch_extra`` / ``yaw_extra`` are angular *rates* (rad/s) supplied
        by the aerodynamic model — the wobble and precession that grow as the
        flight slows.  ``spin_decay_per_s`` bleeds the spin exponentially:
        a spinning ball slowly stops spinning, which is what eventually lets
        the precession take over.
        """
        if spin_decay_per_s > 0.0:
            factor = math.exp(-spin_decay_per_s * dt)
            self.roll_rate *= factor
            self.pitch_rate *= factor
            self.yaw_rate *= factor

        self.pitch = wrap_angle(self.pitch + (self.pitch_rate + pitch_extra) * dt)
        self.roll = wrap_angle(self.roll + self.roll_rate * dt)
        self.yaw = wrap_angle(self.yaw + (self.yaw_rate + yaw_extra) * dt)

    def decay_spin(self, dt: float, spin_decay_per_s: float) -> None:
        """Bleed spin without advancing the attitude (ground/rolling use)."""
        if spin_decay_per_s <= 0.0:
            return
        factor = math.exp(-spin_decay_per_s * dt)
        self.roll_rate *= factor
        self.pitch_rate *= factor
        self.yaw_rate *= factor

    # ---------------------------------------------------------------
    # AXES
    # ---------------------------------------------------------------
    def long_axis(self) -> tuple[float, float, float]:
        """Unit vector from the ball's centre to its nose.

        Elevation comes from :attr:`pitch`, azimuth from :attr:`yaw`.
        """
        cos_p = math.cos(self.pitch)
        return (cos_p * math.cos(self.yaw), cos_p * math.sin(self.yaw), math.sin(self.pitch))

    def lateral_axis(self) -> tuple[float, float, float]:
        """Unit vector the END-OVER-END tumble rotates about.

        Perpendicular to the long axis and horizontal, so pitch spin sweeps
        the nose through the vertical plane containing the flight azimuth.
        """
        ax, ay, _ = self.long_axis()
        horizontal = math.hypot(ax, ay)
        if horizontal < 1e-6:
            # Nose straight up: any horizontal axis will do.
            return (1.0, 0.0, 0.0)
        return (-ay / horizontal, ax / horizontal, 0.0)

    def spin_vector(self) -> tuple[float, float, float]:
        """Combined angular velocity vector (rad/s) in world axes.

        ``roll_rate`` acts along the long axis; ``pitch_rate`` acts about the
        lateral axis; ``yaw_rate`` acts about the vertical.  Keeping them
        separate here is what lets the aerodynamic model treat the spiral and
        the tumble differently.
        """
        ax, ay, az = self.long_axis()
        lx, ly, lz = self.lateral_axis()
        roll = self.roll_rate
        pitch = self.pitch_rate
        yaw = self.yaw_rate
        return (
            ax * roll + lx * pitch,
            ay * roll + ly * pitch,
            az * roll + lz * pitch + yaw,
        )

    def spin_magnitude(self) -> float:
        """Total angular speed (rad/s) across the three axes."""
        return math.sqrt(self.roll_rate ** 2 + self.pitch_rate ** 2 + self.yaw_rate ** 2)

    def spiral_fraction(self) -> float:
        """Share of the spin that is axial (0 = all tumble, 1 = pure spiral)."""
        total = self.spin_magnitude()
        if total < 1e-6:
            return 0.0
        return abs(self.roll_rate) / total

    def tumble_fraction(self) -> float:
        """Share of the spin that is end-over-end."""
        return 1.0 - self.spiral_fraction()

    # ---------------------------------------------------------------
    # DERIVED VISUAL / PHYSICAL QUERIES
    # ---------------------------------------------------------------
    def nose_elevation_deg(self) -> float:
        """Elevation of the nose in degrees, -90 (down) to +90 (up)."""
        return math.degrees(math.asin(max(-1.0, min(1.0, self.long_axis()[2]))))

    def end_on_fraction(self, vx: float, vy: float, vz: float) -> float:
        """How end-on the ball is to its own velocity: 1 = point-first.

        A ball landing 1.0 is landing on a point — the dead-cat bounce of
        rugby league.  0.0 means it lands broadside.
        """
        speed = math.hypot(math.hypot(vx, vy), vz)
        if speed < 1e-6:
            return abs(self.long_axis()[2])
        ax, ay, az = self.long_axis()
        dot = (ax * vx + ay * vy + az * vz) / speed
        return abs(dot)

    def snapshot(self) -> dict[str, float]:
        """Read-only view for audits and verification."""
        return {
            "pitch": self.pitch,
            "roll": self.roll,
            "yaw": self.yaw,
            "pitch_rate": self.pitch_rate,
            "roll_rate": self.roll_rate,
            "yaw_rate": self.yaw_rate,
        }
