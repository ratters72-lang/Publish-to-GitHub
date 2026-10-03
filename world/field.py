import pygame


class Field:
    """Rugby league field — rendering (Pygame) + metre-space utility API.

    =============================
    FIELD LAYOUT (NON-NEGOTIABLE)
    =============================
    Deadball line      x = 0           (left edge of field)
    Left tryline       x = 10
    Right tryline      x = 110
    Deadball line      x = 120         (right edge of field)

    Top sideline       y = 0
    Bottom sideline    y = 70

    Attack runs RIGHT → tryline at x = 110.
    Defenders retreat RIGHT → behind the ball.

    length_m = 120  (deadball to deadball)
    width_m  = 70   (sideline to sideline)
    """

    def __init__(self, screen_width, screen_height):
        self.sw = screen_width
        self.sh = screen_height

        # --- FIELD DIMENSIONS (metres) ---
        self.length_m = 120
        self.width_m = 70

        self.halfway_m = self.length_m / 2     # 60
        self.centre_m = self.width_m / 2        # 35

        # --- Trylines in metre space ---
        self.TRYLINE_LEFT_X = 10
        self.TRYLINE_RIGHT_X = 110

        # --- PERFECT MARGINS FOR 1920×1080 ---
        self.left_margin = 60
        self.top_margin = 60
        self.ui_width = 200

        # --- FIELD BOUNDING BOX ---
        self.field_left = self.left_margin
        self.field_right = self.sw - self.ui_width
        self.field_box_width = self.field_right - self.field_left

        self.field_top_limit = self.top_margin
        self.field_box_height = self.sh - self.top_margin

        # --- SCALE (fit field inside box) ---
        scale_x = self.field_box_width / self.length_m
        scale_y = self.field_box_height / self.width_m
        self.scale = min(scale_x, scale_y)

        # --- PIXEL SIZE OF FIELD ---
        self.field_px_width = int(self.length_m * self.scale)
        self.field_px_height = int(self.width_m * self.scale)

        # --- CENTER FIELD VERTICALLY ---
        vertical_slack = self.field_box_height - self.field_px_height
        self.field_top = self.field_top_limit + vertical_slack // 2
        self.field_bottom = self.field_top + self.field_px_height

        # --- ORIGIN FOR METRE→PIXEL ---
        self.origin_x = self.field_left
        self.origin_y = self.field_top

        # FONT
        pygame.font.init()
        self.font = pygame.font.SysFont("Arial", int(self.scale * 2.2), bold=True)

        # --- GRASS TEXTURE ---
        self.grass = pygame.image.load("assets/grass3.png").convert()
        self.grass = pygame.transform.smoothscale(self.grass, (128, 128))

        # --- PRE-RENDER STATIC FIELD SURFACE ---
        # Everything that is part of the field (grass, lines, numbers,
        # triangles, dashes, goalposts) goes onto this surface ONCE.
        self._build_field_surface()

    # ----------------------------------------------------------------
    # METRE → FIELD-PIXEL (NO CAMERA — for pre-rendering)
    # ----------------------------------------------------------------
    def metres_to_field_px(self, xm, ym):
        """Convert world metres to pixels on the field surface.
        No camera offset — the surface IS the field.
        """
        return int(xm * self.scale), int(ym * self.scale)

    # ================================================================
    # METRE-SPACE FIELD API
    #
    # Pure metre-space helpers. Zero Pygame, zero pixels.
    # Every function is grounded in self.length_m = 120, self.width_m = 70.
    #
    # Field of play: x ∈ [10, 110]     (tryline to tryline)
    #                y ∈ [0,  70]      (sideline to sideline)
    # ================================================================

    # ----------------------------------------------------------------
    # 1. Clamp a point to the legal field of play
    # ----------------------------------------------------------------
    def clamp_point(self, xm: float, ym: float) -> tuple[float, float]:
        """Clamp (xm, ym) inside the field of play.

        x ∈ [10, 110], y ∈ [0, 70]
        """
        return (
            max(self.TRYLINE_LEFT_X, min(self.TRYLINE_RIGHT_X, xm)),
            max(0.0, min(self.width_m, ym)),
        )

    # ----------------------------------------------------------------
    # 2. Clamp only X (horizontal)
    # ----------------------------------------------------------------
    def clamp_x(self, xm: float) -> float:
        """Clamp xm inside [10, 110]."""
        return max(self.TRYLINE_LEFT_X, min(self.TRYLINE_RIGHT_X, xm))

    # ----------------------------------------------------------------
    # 3. Clamp only Y (vertical)
    # ----------------------------------------------------------------
    def clamp_y(self, ym: float) -> float:
        """Clamp ym inside [0, 70]."""
        return max(0.0, min(self.width_m, ym))

    # ----------------------------------------------------------------
    # 4. Legal 10m retreat target (RETREAT anchor)
    # ----------------------------------------------------------------
    def legal_retreat_x(self, ruck_x_m: float) -> float:
        """Return the legal retreat X for non-markers: ruck_x + 10, clamped.

        Attack runs RIGHT → defenders retreat right → target = ruck_x + 10.
        """
        return self.clamp_x(ruck_x_m + 10.0)

    # ----------------------------------------------------------------
    # 5. Short-side and open-side widths
    # ----------------------------------------------------------------
    def short_side_width(self, ruck_y_m: float) -> float:
        """Distance from ruck_y to the nearest sideline."""
        return min(ruck_y_m, self.width_m - ruck_y_m)

    def open_side_width(self, ruck_y_m: float) -> float:
        """Distance from ruck_y to the far sideline."""
        return max(ruck_y_m, self.width_m - ruck_y_m)

    # ----------------------------------------------------------------
    # 6. Usable defensive width
    # ----------------------------------------------------------------
    def usable_width(self, ruck_y_m: float) -> float:
        """The macro width the defensive band can use.

        If ruck_y is central → full width (~70m).
        If ruck_y is near touch → compressed width so defenders stay on-field.
        """
        near = self.short_side_width(ruck_y_m)
        # When within 10m of touch, usable width = full width minus the
        # dead zone on the short side plus a buffer.
        if near < 10.0:
            # Compress: full width minus (short-side shortfall * 2)
            shortfall = 10.0 - near
            return max(40.0, self.width_m - shortfall * 2.0)
        return self.width_m

    # ----------------------------------------------------------------
    # 7. Band centre line (vertical centre of the defensive line)
    # ----------------------------------------------------------------
    def band_centre_y(self, ruck_y_m: float) -> float:
        """Return the vertical centre of the defensive band.

        If ruck is central → centre at 35.
        If ruck is near touch → centre shifts toward the open side.
        """
        near = self.short_side_width(ruck_y_m)
        if near < 10.0:
            # Shift centre away from the touchline by the shortfall
            shortfall = 10.0 - near
            if ruck_y_m < self.centre_m:
                # Ruck is on the top side → shift down (toward open)
                return self.clamp_y(self.centre_m + shortfall * 0.5)
            else:
                # Ruck is on the bottom side → shift up (toward open)
                return self.clamp_y(self.centre_m - shortfall * 0.5)
        return self.centre_m

    # ----------------------------------------------------------------
    # 8. Check if a point is inside the field of play
    # ----------------------------------------------------------------
    def is_in_play(self, xm: float, ym: float) -> bool:
        """Return True iff (xm, ym) is in the legal field of play.

        x ∈ [10, 110], y ∈ [0, 70]
        """
        return (
            self.TRYLINE_LEFT_X <= xm <= self.TRYLINE_RIGHT_X
            and 0.0 <= ym <= self.width_m
        )

    # ----------------------------------------------------------------
    # 9. Legal TES contact point (same as clamp_point for now)
    # ----------------------------------------------------------------
    def legal_contact_point(self, xm: float, ym: float) -> tuple[float, float]:
        """Clamp a point to keep tackle contact on the field of play.

        Prevents tackles from pushing players off-field.  Same as clamp_point
        for the standard field — could be customised if the in-goal area
        has different contact rules.
        """
        return self.clamp_point(xm, ym)

    # ================================================================
    # PRE-RENDER STATIC FIELD SURFACE
    # ================================================================

    def _build_field_surface(self):
        """Render the entire static field (grass + markings + numbers + etc.)
        onto self.field_surface.  Called once during __init__.
        """
        self.field_surface = pygame.Surface(
            (self.field_px_width, self.field_px_height)
        )

        # --- TILE GRASS ---
        tile_w = self.grass.get_width()
        tile_h = self.grass.get_height()
        for y in range(0, self.field_px_height, tile_h):
            for x in range(0, self.field_px_width, tile_w):
                self.field_surface.blit(self.grass, (x, y))

        # --- COLOURS ---
        WHITE = (255, 255, 255)

        # Helper: metre coords → field-surface pixel coords (no camera)
        def fpx(xm, ym):
            return int(xm * self.scale), int(ym * self.scale)

        # --- SIDELINES (rect) ---
        left, top = fpx(0, 0)
        w = self.field_px_width
        h = self.field_px_height
        pygame.draw.rect(self.field_surface, WHITE, (left, top, w, h), 4)

        # --- TRYLINES ---
        for xm in (10, 110):
            x_px, _ = fpx(xm, 0)
            pygame.draw.line(
                self.field_surface, WHITE,
                (x_px, 0), (x_px, self.field_px_height), 4,
            )

        # --- HALFWAY ---
        x_px, _ = fpx(60, 0)
        pygame.draw.line(
            self.field_surface, WHITE,
            (x_px, 0), (x_px, self.field_px_height), 3,
        )

        # --- 10m LINES ---
        for xm in range(20, 101, 10):
            x_px, _ = fpx(xm, 0)
            colour = (255, 0, 0) if xm in (30, 50, 70, 90) else WHITE
            thickness = 2 if xm in (30, 50, 70, 90) else 1
            pygame.draw.line(
                self.field_surface, colour,
                (x_px, 0), (x_px, self.field_px_height), thickness,
            )

        # --- NRL NUMBERS + TRIANGLES ---
        BOTTOM_OFFSET_M = 15
        TRI_OFFSET_M = 3.5

        nrl_numbers = [
            (20, 10), (30, 20), (40, 30), (50, 40),
            (60, 50),
            (70, 40), (80, 30), (90, 20), (100, 10),
        ]

        for line_m, num in nrl_numbers:
            spaced = " ".join(str(num))
            text = self.font.render(spaced, True, WHITE)
            text_flip = pygame.transform.rotate(text, 180)

            # --- bottom row ---
            y_mid_m = self.width_m - BOTTOM_OFFSET_M
            x_px_num, _ = fpx(line_m, 0)
            y_px_num = int(y_mid_m * self.scale)
            self.field_surface.blit(
                text,
                text.get_rect(center=(x_px_num, y_px_num)),
            )

            # --- top row ---
            y_top_m = BOTTOM_OFFSET_M
            y_top_px = int(y_top_m * self.scale)
            self.field_surface.blit(
                text_flip,
                text_flip.get_rect(center=(x_px_num, y_top_px)),
            )

            # --- triangles bottom ---
            if num != 50:
                x_tri_m = line_m - TRI_OFFSET_M if line_m < 60 else line_m + TRI_OFFSET_M
                x_tri_px, y_tri_px = fpx(x_tri_m, y_mid_m)
                pointing_left = line_m < 60
                self._draw_triangle_on_surface(
                    self.field_surface, x_tri_px, y_tri_px, pointing_left,
                )
            else:
                x_left, y_left = fpx(line_m - TRI_OFFSET_M, y_mid_m)
                x_right, y_right = fpx(line_m + TRI_OFFSET_M, y_mid_m)
                self._draw_triangle_on_surface(
                    self.field_surface, x_left, y_left, True,
                )
                self._draw_triangle_on_surface(
                    self.field_surface, x_right, y_right, False,
                )

            # --- triangles top ---
            if num != 50:
                x_tri_m = line_m - TRI_OFFSET_M if line_m < 60 else line_m + TRI_OFFSET_M
                x_tri_px, y_tri_px = fpx(x_tri_m, y_top_m)
                pointing_left = line_m < 60
                self._draw_triangle_on_surface(
                    self.field_surface, x_tri_px, y_tri_px, pointing_left,
                )
            else:
                x_left, y_left = fpx(line_m - TRI_OFFSET_M, y_top_m)
                x_right, y_right = fpx(line_m + TRI_OFFSET_M, y_top_m)
                self._draw_triangle_on_surface(
                    self.field_surface, x_left, y_left, True,
                )
                self._draw_triangle_on_surface(
                    self.field_surface, x_right, y_right, False,
                )

        # --- 5m DASHES ---
        DASH_HALF_M = 2.5
        for xm in range(20, 101, 10):
            x_px, _ = fpx(xm, 0)
            dash_px = int(DASH_HALF_M * self.scale)
            for ym in (10, 20, self.width_m - 20, self.width_m - 10):
                _, y_px = fpx(0, ym)
                pygame.draw.line(
                    self.field_surface, WHITE,
                    (x_px - dash_px, y_px),
                    (x_px + dash_px, y_px), 2,
                )

        # --- GOALPOSTS + CROSSBAR ---
        upright_gap_m = 6
        upright_radius = max(3, int(self.scale * 0.6))
        crossbar_thickness = max(2, int(self.scale * 0.35))

        for xm in (10, 110):
            x_px, _ = fpx(xm, 0)
            y_centre_px = int(self.centre_m * self.scale)
            y_top_px = int((self.centre_m - upright_gap_m / 2) * self.scale)
            y_bottom_px = int((self.centre_m + upright_gap_m / 2) * self.scale)

            # Uprights
            pygame.draw.circle(
                self.field_surface, (0, 0, 0),
                (x_px, y_top_px), upright_radius + 1,
            )
            pygame.draw.circle(
                self.field_surface, (255, 255, 255),
                (x_px, y_top_px), upright_radius,
            )
            pygame.draw.circle(
                self.field_surface, (0, 0, 0),
                (x_px, y_bottom_px), upright_radius + 1,
            )
            pygame.draw.circle(
                self.field_surface, (255, 255, 255),
                (x_px, y_bottom_px), upright_radius,
            )

            # Crossbar
            pygame.draw.line(
                self.field_surface, (255, 255, 255),
                (x_px, y_top_px), (x_px, y_bottom_px),
                crossbar_thickness,
            )

    # ----------------------------------------------------------------
    # TRIANGLE HELPER (field-surface variant)
    # ----------------------------------------------------------------
    def _draw_triangle_on_surface(self, surface, x, y, pointing_left=True):
        size = int(self.scale * 1.32)
        if pointing_left:
            pts = [(x, y), (x + size, y - size // 2), (x + size, y + size // 2)]
        else:
            pts = [(x, y), (x - size, y - size // 2), (x - size, y + size // 2)]
        pygame.draw.polygon(surface, (255, 255, 255), pts)

    # ================================================================
    # RENDERING (Pygame)
    # ================================================================

    # --- METRE TO PIXEL (no camera — fixed origin) ---
    def metres_to_px(self, xm, ym):
        """Convert world metres to screen pixels. Camera-free: (0,0) → origin.
        """
        px = self.origin_x + xm * self.scale
        py = self.origin_y + ym * self.scale
        return int(px), int(py)

    # --- DRAW EVERYTHING ---
    def draw(self, screen):
        # --- Blit the pre-rendered field surface at fixed origin ---
        screen.blit(
            self.field_surface,
            (self.origin_x, self.origin_y),
        )
