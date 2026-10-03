"""The rugby league ball package.

``Ball`` is the object the match owns.  The rest of the package is the model
behind it, split so each part can be read, tested and reasoned about alone:

    kick_types      what kind of kick was struck (spiral, bomb, grubber, floater)
    attitude        the ball's three independent rotation axes
    aerodynamics    drag, Magnus lift, and the late-flight instability
    bounce          asymmetric turf behaviour and end-over-end rolling
    flight          the one integrator, shared by the sim and the predictor
    ball_projection camera maths for the on-screen silhouette
    ball_renderer   paints the brown Steeden with its seams and gloss
"""

from ball.ball import Ball
from ball.attitude import BallAttitude
from ball.kick_types import (
    BOMB,
    FLOATER,
    GRUBBER,
    KICK_DEFAULT,
    SPIRAL_PUNT,
    KickProfile,
    KickType,
    profile_with,
    resolve_profile,
)

__all__ = [
    "Ball",
    "BallAttitude",
    "KickType",
    "KickProfile",
    "resolve_profile",
    "profile_with",
    "KICK_DEFAULT",
    "SPIRAL_PUNT",
    "BOMB",
    "GRUBBER",
    "FLOATER",
]
