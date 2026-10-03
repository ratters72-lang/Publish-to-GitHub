"""Camera projection maths for the ball sprite.

The match is drawn as a tilted top-down view, and that tilt is baked into
:class:`world.field.Field` already: the field is drawn with
``scale`` pixels per metre across the ground, while height is drawn with a
much smaller scale (the render loop lifts the ball by ``z_m * 10`` pixels).
That mismatch is not a bug - it is the camera's pitch.  Reconstructing it
lets the ball be drawn as a genuine 3D object rather than a fixed ellipse.

The projection used here is the orthographic map

    screen_x = x * scale
    screen_y = y * scale - z * Z_PX_PER_M

which is exactly what the existing render loop does.  Given that map, the
silhouette of the ball - a prolate spheroid with half-length ``a`` along its
long axis and radius ``r`` across it - is obtained from the spheroid's
support function:

    h(d) = sqrt( a^2 (u . P^T d)^2 + r^2 (|P^T d|^2 - (u . P^T d)^2) )

evaluated along the screen direction of the projected long axis and along
the perpendicular screen direction.  Those two values ARE the semi-axes of
the drawn ellipse, so the ball's on-screen shape follows its real
orientation:

* axis broadside to the camera -> the full prolate rugby shape;
* axis pointing up and away   -> a foreshortened stub;
* axis aimed at the camera    -> a near-perfect circle.

That is what makes the four kick types tell themselves apart on screen even
when two of them are travelling at the same speed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: Pixels of screen height per metre of ball height.  Must match the lift the
#: render loop applies to the ball (``z_m * 10``) so the ball sits exactly
#: where the rest of the renderer thinks it is.
Z_PX_PER_M: float = 10.0


@dataclass(frozen=True)
class BallScreenShape:
    """The ball's on-screen silhouette for one frame."""

    centre_x: float          # screen centre, after the height lift
    centre_y: float
    along_x: float           # unit screen vector along the projected long axis
    along_y: float
    half_length_px: float    # semi-axis along that direction
    half_width_px: float     # semi-axis across it
    angle_deg: float         # rotation to apply to a local-space sprite
    aspect: float            # half_length / half_width (1.0 = seen end-on)


def project_axis(
    axis: tuple[float, float, float],
    scale: float,
) -> tuple[float, float]:
    """Project a world direction onto the screen, in pixels per unit."""
    ax, ay, az = axis
    return ax * scale, ay * scale - az * Z_PX_PER_M


def _support(
    axis: tuple[float, float, float],
    screen_dir: tuple[float, float],
    scale: float,
    half_length: float,
    radius: float,
) -> float:
    """Spheroid support function along one screen direction."""
    dx, dy = screen_dir
    # P^T d  (d is a screen direction; P maps world -> screen)
    ptx = dx * scale
    pty = dy * scale
    ptz = -dy * Z_PX_PER_M

    ux, uy, uz = axis
    u_dot = ux * ptx + uy * pty + uz * ptz
    p_norm_sq = ptx * ptx + pty * pty + ptz * ptz

    inside = p_norm_sq - u_dot * u_dot
    if inside < 0.0:
        inside = 0.0
    return math.sqrt(half_length * half_length * u_dot * u_dot + radius * radius * inside)


def ball_screen_shape(
    x_m: float,
    y_m: float,
    z_m: float,
    axis: tuple[float, float, float],
    scale: float,
    metres_to_px,
    half_length_m: float,
    radius_m: float,
) -> BallScreenShape:
    """Compute the ball's silhouette for the current position and attitude.

    ``metres_to_px`` is :meth:`world.field.Field.metres_to_px`, used for the
    ground position; the height lift is then applied with
    :data:`Z_PX_PER_M` so the ball sits above its shadow exactly as before.
    """
    ground_x, ground_y = metres_to_px(x_m, y_m)
    centre_x = float(ground_x)
    centre_y = float(ground_y) - z_m * Z_PX_PER_M

    proj_x, proj_y = project_axis(axis, scale)
    proj_len = math.hypot(proj_x, proj_y)
    if proj_len < 1e-6:
        # Long axis aimed straight at the camera: the ball reads as a circle.
        along_x, along_y = 1.0, 0.0
    else:
        along_x, along_y = proj_x / proj_len, proj_y / proj_len

    perp_x, perp_y = -along_y, along_x

    half_length_px = _support(
        axis, (along_x, along_y), scale, half_length_m, radius_m
    )
    half_width_px = _support(
        axis, (perp_x, perp_y), scale, half_length_m, radius_m
    )

    # Never let the sprite collapse to nothing on screen.
    half_length_px = max(half_length_px, 1.5)
    half_width_px = max(half_width_px, 1.5)

    angle_deg = math.degrees(math.atan2(along_y, along_x))
    aspect = half_length_px / half_width_px

    return BallScreenShape(
        centre_x=centre_x,
        centre_y=centre_y,
        along_x=along_x,
        along_y=along_y,
        half_length_px=half_length_px,
        half_width_px=half_width_px,
        angle_deg=angle_deg,
        aspect=aspect,
    )


def shadow_geometry(
    z_m: float,
    scale: float,
) -> tuple[float, float, float, float, float]:
    """Return ``(offset_x, offset_y, width, height, alpha)`` for the shadow.

    A ball high in the air casts a big, soft, offset shadow; a ball about to
    be caught casts a small, hard one.  The offset uses the same camera pitch
    as everything else, so the shadow slides *away* from the ball as it rises
    rather than just growing.
    """
    height = max(0.0, z_m)
    # Shadow recedes toward the bottom of the screen as the ball climbs.
    offset_x = height * 0.55
    offset_y = 4.0 + height * 1.9
    grow = 1.0 + height * 0.28
    width = max(6.0, scale * 1.25 * grow)
    height_px = max(2.5, scale * 0.48 * grow)
    # Fades out with height.
    alpha = max(0.16, 1.0 - height * 0.075)
    return offset_x, offset_y, width, height_px, alpha
