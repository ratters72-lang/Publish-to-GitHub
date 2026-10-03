"""Draws the ball as a brown rugby league football.

The ball is a Steeden: dark tan leather, two curved seams running tip to
tip, a cream lace band across the middle, and a glossy surface that catches
the light.  This module draws all of that procedurally, at whatever size and
shape the current attitude implies.

HOW THE SHAPE IS DECIDED
------------------------
:mod:`ball.ball_projection` turns the ball's 3D orientation into an on-screen
ellipse.  This module takes that ellipse - a centre, a rotation and two
semi-axes - and paints the leather into it.  A ball seen broadside is long
and thin; a ball whose nose is pointing at the camera is nearly round; a
tumbling grubber sweeps through every shape in between roughly three times a
second.  Nothing here decides the shape - it only paints it.

HOW SPIN IS SHOWN
-----------------
Two separate cues, because one is not enough to read a ball in flight:

* The LACE BAND slides across the ball as it rolls about its own long axis
  and fades as it turns away from the camera.  This is the spiral: a
  seven-revolution-per-second punt makes the lace flicker visibly.
* The leather SHADING is baked into the sprite, so it turns with the ball
  and reads as the surface rotating.

A third cue - a small specular glint - is drawn in *screen* space after the
sprite is placed, because a real highlight belongs to the light and the
camera, not to the ball, and pinning it still is what makes the leather look
wet and glossy rather than flat.

PERFORMANCE
-----------
There are only :data:`LACE_PHASES` pre-rendered sprites (one per lace
position around the ball) and they are built lazily on first use, so a match
pays for at most a dozen small surfaces no matter how long it runs or how
fast the ball spins.
"""

from __future__ import annotations

import math

import pygame

from ball.ball_projection import (
    BallScreenShape,
    ball_screen_shape,
    shadow_geometry,
)
from ball.attitude import BallAttitude

# ── Visual size ────────────────────────────────────────────────────────
# A real Steeden is 0.28 m long and 0.15 m across, which at this camera
# scale is under four pixels.  The ball is therefore drawn at an exaggerated
# size so it stays readable, while holding the real 1.87:1 proportion.
VIS_HALF_LENGTH_M: float = 0.60
VIS_RADIUS_M: float = 0.32

# ── Palette ────────────────────────────────────────────────────────────
LEATHER_DARK = (74, 34, 16)
LEATHER_BASE = (132, 63, 32)
LEATHER_MID = (150, 76, 40)
LEATHER_LIGHT = (183, 108, 64)
LEATHER_GLINT = (214, 156, 108)
SEAM_CREAM = (238, 228, 205)
SEAM_SHADOW = (86, 40, 18)
LACE_CREAM = (245, 238, 220)
SHADOW_RGB = (28, 40, 20)

# ── Sprite template geometry (local space, long axis horizontal) ────────
TEMPLATE_LEN: int = 132
TEMPLATE_THICK: int = 72
TEMPLATE_PAD: int = 3

#: How many distinct lace positions are pre-rendered around the ball.
LACE_PHASES: int = 16

#: Depth of the bow in the seams, as a fraction of the half-thickness.
SEAM_BOW: float = 0.62

#: How far the lace band slides across the ball as it rolls.
LACE_SLIDE: float = 0.66


def _ellipse(
    surface: pygame.Surface,
    centre: tuple[float, float],
    half_len: float,
    half_thick: float,
    colour: tuple[int, int, int] | tuple[int, int, int, int],
) -> None:
    """Fill an axis-aligned ellipse on ``surface``."""
    rect = pygame.Rect(
        int(round(centre[0] - half_len)),
        int(round(centre[1] - half_thick)),
        max(1, int(round(half_len * 2))),
        max(1, int(round(half_thick * 2))),
    )
    pygame.draw.ellipse(surface, colour, rect)


def _build_template(lace_phase: float) -> pygame.Surface:
    """Render one ball sprite in local space, long axis horizontal.

    ``lace_phase`` is the ball's roll angle in radians.  It sets where the
    lace band sits across the ball and how brightly it shows, which is the
    whole visual signal for axial spin.
    """
    surface = pygame.Surface((TEMPLATE_LEN, TEMPLATE_THICK), pygame.SRCALPHA)
    cx = TEMPLATE_LEN / 2.0
    cy = TEMPLATE_THICK / 2.0
    half_len = cx - TEMPLATE_PAD
    half_thick = cy - TEMPLATE_PAD

    # 1. Dark rim: the silhouette edge, slightly larger so it survives the
    #    downscale to a 16-pixel sprite.
    _ellipse(surface, (cx, cy), half_len, half_thick, LEATHER_DARK)

    # 2. Body of leather.
    _ellipse(surface, (cx, cy), half_len - 1.2, half_thick - 1.2, LEATHER_BASE)

    # 3. Diffuse shading.  Offset up-left toward the light; because it is
    #    baked into the sprite it travels with the ball and reads as spin.
    _ellipse(
        surface,
        (cx - half_len * 0.10, cy - half_thick * 0.20),
        half_len * 0.78,
        half_thick * 0.74,
        LEATHER_MID,
    )
    _ellipse(
        surface,
        (cx - half_len * 0.18, cy - half_thick * 0.34),
        half_len * 0.52,
        half_thick * 0.50,
        (*LEATHER_LIGHT, 205),
    )
    _ellipse(
        surface,
        (cx - half_len * 0.24, cy - half_thick * 0.42),
        half_len * 0.28,
        half_thick * 0.30,
        (*LEATHER_GLINT, 120),
    )

    # 4. The two seams: curves running tip to tip, bowing outwards.  Drawn
    #    as one closed lens so they stay crisp at small sizes.
    for sign in (1.0, -1.0):
        points: list[tuple[int, int]] = []
        for step in range(25):
            t = -1.0 + 2.0 * step / 24.0
            # Ellipse-ish bow: widest mid-ball, meeting at both tips.
            offset = SEAM_BOW * math.sqrt(max(0.0, 1.0 - t * t))
            px = cx + t * (half_len - 1.0)
            py = cy + sign * offset * (half_thick - 1.5)
            points.append((int(round(px)), int(round(py))))
        # Shadow side first, then the cream seam on top of it.
        pygame.draw.aalines(surface, SEAM_SHADOW, False, [(x, y + 1) for x, y in points])
        pygame.draw.aalines(surface, SEAM_CREAM, False, points)

    # 5. Lace band across the middle, sliding with the roll and dimming as
    #    it turns away from the camera.
    lace_v = math.sin(lace_phase) * LACE_SLIDE
    lace_v = max(-0.86, min(0.86, lace_v))
    face = 0.5 + 0.5 * math.cos(lace_phase)          # 1.0 near side, 0.0 far
    # Foreshorten the band as it approaches the silhouette edge.
    squeeze = math.sqrt(max(0.12, 1.0 - lace_v * lace_v))
    band_centre_y = cy + lace_v * (half_thick - 2.0)
    band_half = (half_thick - 3.0) * squeeze * 0.30
    band_colour = tuple(
        int(SEAM_SHADOW[i] + (LACE_CREAM[i] - SEAM_SHADOW[i]) * (0.35 + 0.65 * face))
        for i in range(3)
    )
    band_rect = pygame.Rect(
        int(cx - half_len * 0.16),
        int(round(band_centre_y - band_half)),
        max(2, int(half_len * 0.32)),
        max(2, int(round(band_half * 2))),
    )
    pygame.draw.rect(surface, band_colour, band_rect, border_radius=2)

    # Stitches across the lace, only worth drawing when it faces us.
    if face > 0.25 and band_rect.height >= 3:
        stitch = tuple(
            int(SEAM_SHADOW[i] + (LEATHER_DARK[i] - SEAM_SHADOW[i]) * 0.5)
            for i in range(3)
        )
        for k in range(1, 4):
            sx = band_rect.x + int(band_rect.width * k / 4.0)
            pygame.draw.line(
                surface,
                stitch,
                (sx, band_rect.y),
                (sx, band_rect.bottom),
                1,
            )

    return surface


_TEMPLATES: dict[int, pygame.Surface] = {}


def _base_template(phase_index: int) -> pygame.Surface:
    """Lazily build (and cache) the sprite for one lace phase."""
    sprite = _TEMPLATES.get(phase_index)
    if sprite is None:
        angle = math.tau * (phase_index % LACE_PHASES) / LACE_PHASES
        sprite = _build_template(angle)
        _TEMPLATES[phase_index] = sprite
    return sprite


def _lace_phase_index(attitude: BallAttitude) -> int:
    return int(attitude.roll / math.tau * LACE_PHASES) % LACE_PHASES


def clear_sprite_cache() -> None:
    """Drop the cached sprites (used by tests and on a display restart)."""
    _TEMPLATES.clear()


def build_shape(ball, field) -> BallScreenShape:
    """Public helper: the on-screen silhouette for the current ball state."""
    return ball_screen_shape(
        ball.x_m,
        ball.y_m,
        ball.z_m,
        ball.attitude.long_axis(),
        field.scale,
        field.metres_to_px,
        VIS_HALF_LENGTH_M,
        VIS_RADIUS_M,
    )


def draw_shadow(screen: pygame.Surface, ball, field, shape: BallScreenShape) -> None:
    """Draw the ball's shadow on the turf, sized and offset by its height."""
    if ball.grounded and ball.z_m <= 0.01:
        return
    offset_x, offset_y, width, height, alpha = shadow_geometry(ball.z_m, field.scale)

    pad = 4
    shadow = pygame.Surface(
        (int(width) + pad * 2, int(height) + pad * 2), pygame.SRCALPHA
    )
    pygame.draw.ellipse(
        shadow,
        (*SHADOW_RGB, int(round(255 * alpha * 0.55))),
        pygame.Rect(pad, pad, max(2, int(width)), max(2, int(height))),
    )
    screen.blit(
        shadow,
        (
            int(shape.centre_x - shadow.get_width() / 2.0 + offset_x),
            int(field.metres_to_px(ball.x_m, ball.y_m)[1] - shadow.get_height() / 2.0 + offset_y),
        ),
    )


def draw_ball(
    screen: pygame.Surface,
    ball,
    field,
    speed: float = 0.0,
) -> BallScreenShape | None:
    """Draw the ball and return the silhouette used.

    ``speed`` only affects the soft motion smear that trails a fast-moving
    ball, which is what makes a 25 m/s spiral punt read as fast while a
    dropping bomb reads as slow.
    """
    shape = build_shape(ball, field)
    cx = int(round(shape.centre_x))
    cy = int(round(shape.centre_y))

    # Never draw off-screen or with a nonsense size.
    if not _finite(shape.half_length_px) or not _finite(shape.half_width_px):
        return None

    # ── motion smear behind a fast ball ──
    if speed > 9.0:
        _draw_smear(screen, shape, speed)

    # ── the leather ──
    target_w = max(3, int(round(shape.half_length_px * 2.0)))
    target_h = max(3, int(round(shape.half_width_px * 2.0)))
    sprite = _base_template(_lace_phase_index(ball.attitude))
    scaled = pygame.transform.smoothscale(sprite, (target_w, target_h))
    rotated = pygame.transform.rotate(scaled, -shape.angle_deg)
    screen.blit(rotated, rotated.get_rect(center=(cx, cy)))

    # ── view-space gloss: belongs to the light, not the ball ──
    _draw_glint(screen, shape)
    return shape


def _draw_smear(screen: pygame.Surface, shape: BallScreenShape, speed: float) -> None:
    """A short translucent streak along the line of travel."""
    strength = min(1.0, (speed - 9.0) / 16.0)
    if strength <= 0.0:
        return
    length = 6.0 + 14.0 * strength
    tail_x = shape.centre_x - shape.along_x * length
    tail_y = shape.centre_y - shape.along_y * length
    pad = 6
    min_x = int(min(shape.centre_x, tail_x)) - pad
    min_y = int(min(shape.centre_y, tail_y)) - pad
    width = abs(int(tail_x - shape.centre_x)) + pad * 2
    height = abs(int(tail_y - shape.centre_y)) + pad * 2
    if width <= 0 or height <= 0:
        return
    smear = pygame.Surface((width, height), pygame.SRCALPHA)
    pygame.draw.line(
        smear,
        (*LEATHER_LIGHT, int(60 * strength)),
        (shape.centre_x - min_x, shape.centre_y - min_y),
        (tail_x - min_x, tail_y - min_y),
        max(2, int(shape.half_width_px * 0.9)),
    )
    screen.blit(smear, (min_x, min_y))


def _draw_glint(screen: pygame.Surface, shape: BallScreenShape) -> None:
    """A small specular highlight, fixed in screen space.

    Placed up and to the left of the ball's centre because that is where the
    light is, and clamped inside the silhouette so it never floats off the
    leather.
    """
    reach = min(shape.half_length_px, shape.half_width_px)
    if reach < 2.5:
        return
    size = max(1.0, reach * 0.34)
    glint = pygame.Surface((int(size * 2) + 2, int(size * 2) + 2), pygame.SRCALPHA)
    pygame.draw.ellipse(
        glint,
        (255, 250, 236, 150),
        pygame.Rect(0, 0, int(size * 2), int(size * 2)),
    )
    screen.blit(
        glint,
        (
            int(shape.centre_x - shape.half_length_px * 0.22 - size),
            int(shape.centre_y - shape.half_width_px * 0.34 - size),
        ),
    )


def _finite(value: float) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)
