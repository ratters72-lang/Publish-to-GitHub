"""Kick-type profiles for the rugby league ball.

Each profile describes HOW a kick was struck: the spin it carries, how much
loft it is given, how much aerodynamic lift it generates, how quickly it
destabilises in flight and how it behaves when it hits the ground.

Four kick families are modelled, matching the kicks seen in NRL play:

    SPIRAL_PUNT  long, flat, heavy axial spin — the ball stays "on a string"
    BOMB         steep, high, hanging — bleeds spin and wobbles late
    GRUBBER      driven along the ground, tumbling end over end
    FLOATER      attacking kick, low spin — maximum late-flight instability

Units and conventions
---------------------
* angles in the profiles are DEGREES (readability); the Ball converts to radians
* rotational rates are REVOLUTIONS PER SECOND, converted with ``TAU``
* ``*_gain`` fields are dimensionless multipliers, not SI quantities

``KICK_DEFAULT`` is the profile used when a caller does not name a kick type.
It is the spiral punt, which is what the existing kickoff models.
"""

from dataclasses import dataclass, replace
from enum import Enum
from math import tau


class KickType(str, Enum):
    """The four modelled kick families."""

    SPIRAL_PUNT = "spiral_punt"
    BOMB = "bomb"
    GRUBBER = "grubber"
    FLOATER = "floater"


@dataclass(frozen=True)
class KickProfile:
    """The physical fingerprint of one kick type.

    Spin axes are independent: ``roll_rev_s`` spins the ball about its own
    long axis (the spiral), ``pitch_rev_s`` tumbles it end over end, and
    ``yaw_rev_s`` precesses that long axis around the vertical.
    """

    kick_type: KickType
    label: str

    # ── attitude at the moment of contact ──
    muzzle_pitch_deg: float          # nose elevation relative to horizontal
    nose_into_wind: float            # 1.0 = long axis locked to the flight azimuth
    loft_bias_deg: float             # added to the caller's launch angle
    loft_cap_deg: float              # hard ceiling on the launch angle

    # ── spin (independent axes) ──
    roll_rev_s: float                # about the long axis   (spiral)
    pitch_rev_s: float               # about the lateral axis (end-over-end tumble)
    yaw_rev_s: float                 # about the vertical axis (precession)
    spin_decay_per_s: float          # exponential spin decay in the air

    # ── aerodynamics ──
    drag_gain: float                 # multiplier on the baseline drag
    lift_gain: float                 # Magnus strength (curve / float)
    wobble_gain: float               # amplitude of the low-speed oscillation
    instability_gain: float          # how fast stability collapses with speed

    # ── ground behaviour ──
    bounce_liveliness: float         # 1.0 = lively rebound, 0.0 = dead drop
    end_on_restitution: float        # rebound when it lands on a point
    ground_tumble_gain: float        # end-over-end rotation picked up from turf

    def roll_rad_s(self) -> float:
        return self.roll_rev_s * tau

    def pitch_rad_s(self) -> float:
        return self.pitch_rev_s * tau

    def yaw_rad_s(self) -> float:
        return self.yaw_rev_s * tau

    def clamped_loft_deg(self, requested_deg: float) -> float:
        """Apply the profile's loft bias and hard ceiling to a launch angle."""
        return min(requested_deg + self.loft_bias_deg, self.loft_cap_deg)


# ── Baseline constants shared by every profile ─────────────────────────
BASELINE_DRAG: float = 0.01          # unchanged from the original ball
BASELINE_BOUNCE: float = 0.55        # unchanged from the original ball
BASELINE_ROLL_FRICTION: float = 0.98  # unchanged from the original ball


SPIRAL_PUNT = KickProfile(
    kick_type=KickType.SPIRAL_PUNT,
    label="spiral punt",
    muzzle_pitch_deg=14.0,
    nose_into_wind=1.0,
    loft_bias_deg=0.0,
    loft_cap_deg=48.0,
    roll_rev_s=7.0,
    pitch_rev_s=0.0,
    yaw_rev_s=0.0,
    spin_decay_per_s=0.06,
    drag_gain=1.0,
    lift_gain=0.35,
    wobble_gain=0.30,
    instability_gain=0.85,
    bounce_liveliness=0.85,
    end_on_restitution=0.20,
    ground_tumble_gain=0.30,
)

BOMB = KickProfile(
    kick_type=KickType.BOMB,
    label="bomb",
    muzzle_pitch_deg=38.0,
    nose_into_wind=0.85,
    loft_bias_deg=18.0,
    loft_cap_deg=68.0,
    roll_rev_s=2.6,
    # No end-over-end tumble: a bomb hangs on its nose.  Even 0.15 rev/s
    # carries the ball through most of a full revolution during a 4.2 s
    # flight, which leaves the nose pointing in an arbitrary direction at
    # impact - and the instability has to grow as the trajectory curves away
    # from a HELD attitude, not from a tumbling one.
    pitch_rev_s=0.0,
    yaw_rev_s=0.10,
    spin_decay_per_s=0.35,
    drag_gain=1.0,
    lift_gain=0.95,
    wobble_gain=1.00,
    instability_gain=1.05,
    bounce_liveliness=0.55,
    end_on_restitution=0.18,
    ground_tumble_gain=0.55,
)

GRUBBER = KickProfile(
    kick_type=KickType.GRUBBER,
    label="grubber",
    muzzle_pitch_deg=4.0,
    nose_into_wind=1.0,
    loft_bias_deg=-12.0,
    loft_cap_deg=7.0,
    roll_rev_s=0.8,
    pitch_rev_s=9.0,
    yaw_rev_s=0.0,
    spin_decay_per_s=0.45,
    drag_gain=1.15,
    lift_gain=0.0,
    wobble_gain=0.20,
    instability_gain=0.70,
    bounce_liveliness=0.35,
    end_on_restitution=0.12,
    ground_tumble_gain=1.0,
)

FLOATER = KickProfile(
    kick_type=KickType.FLOATER,
    label="floating attacking kick",
    muzzle_pitch_deg=22.0,
    nose_into_wind=0.70,
    loft_bias_deg=8.0,
    loft_cap_deg=52.0,
    roll_rev_s=1.5,
    # Same as the bomb: the floater hangs and hunts, it does not tumble.
    pitch_rev_s=0.0,
    yaw_rev_s=0.05,
    spin_decay_per_s=1.30,
    drag_gain=1.1,
    lift_gain=1.30,
    # A floater is the LOOSEST in the air but must not become a banana kick:
    # its looseness comes mostly from the low spin (so it destabilises early
    # and hard) rather than from raw wobble force, and the wobble footprint
    # verify_ball_physics.py measures must stay inside the 1.5 m catch radius.
    wobble_gain=0.80,
    instability_gain=1.25,
    bounce_liveliness=0.50,
    end_on_restitution=0.22,
    ground_tumble_gain=0.70,
)

_PROFILES: dict[KickType, KickProfile] = {
    KickType.SPIRAL_PUNT: SPIRAL_PUNT,
    KickType.BOMB: BOMB,
    KickType.GRUBBER: GRUBBER,
    KickType.FLOATER: FLOATER,
}

#: Profile applied when the caller does not name a kick type.
KICK_DEFAULT: KickType = KickType.SPIRAL_PUNT


def resolve_profile(kick_type: "KickType | str | None") -> KickProfile:
    """Return the profile for ``kick_type``, defaulting to the spiral punt.

    Accepts a :class:`KickType`, its string value, or ``None``.  Unknown
    values fall back to :data:`KICK_DEFAULT` rather than raising, so a stale
    call site can never crash the match loop.
    """
    if kick_type is None:
        return _PROFILES[KICK_DEFAULT]
    if isinstance(kick_type, str):
        try:
            kick_type = KickType(kick_type)
        except ValueError:
            return _PROFILES[KICK_DEFAULT]
    return _PROFILES.get(kick_type, _PROFILES[KICK_DEFAULT])


def profile_with(kick_type: "KickType | str | None", **overrides) -> KickProfile:
    """Return a profile with individual fields replaced (test/probe helper)."""
    return replace(resolve_profile(kick_type), **overrides)
