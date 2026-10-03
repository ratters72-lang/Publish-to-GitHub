import math
import pygame


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
        self.drag = 0.01
        self.bounce_factor = 0.55
        self.roll_friction = 0.98

        # Visual spin/wobble
        self.spin_rate = 260.0
        self.wobble_rate = 3.2
        self.wobble_amount = 0.4

        self.rotation = 0.0
        self.time_airborne = 0.0

        # State
        self.grounded = True
        self.kicked = False

        # TRUE ball position (snaps instantly to carrier — never eased)
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


    # ------------------------------------------------------------
    # KICK
    # ------------------------------------------------------------
    def kick(self, power, angle_deg, direction_deg):
        angle = math.radians(angle_deg)
        direction = math.radians(direction_deg)

        speed = power

        self.vx = speed * math.cos(angle) * math.cos(direction)
        self.vy = speed * math.cos(angle) * math.sin(direction)
        self.vz = speed * math.sin(angle)

        # Predict landing (no drag)
        self.flight_time = (2 * self.vz) / self.gravity
        self.landing_x_m = self.x_m + self.vx * self.flight_time
        self.landing_y_m = self.y_m + self.vy * self.flight_time

        self.grounded = False
        self.kicked = True
        self.time_airborne = 0.0


    # ------------------------------------------------------------
    # UPDATE
    # ------------------------------------------------------------
    def update(self, dt):

        # TRUE ball position snaps instantly to carrier (no easing)
        if self.possessed_by is not None:
            self.true_x_m = self.possessed_by.x
            self.true_y_m = self.possessed_by.y

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
            return

        # TRUE position tracks visual when not possessed
        self.true_x_m = self.x_m
        self.true_y_m = self.y_m

        # Grounded → skid / roll
        if self.grounded:
            self.vx *= self.roll_friction
            self.vy *= self.roll_friction

            if abs(self.vx) < 0.01:
                self.vx = 0.0
            if abs(self.vy) < 0.01:
                self.vy = 0.0

            self.x_m += self.vx * dt
            self.y_m += self.vy * dt
            return

        # Airborne
        self.time_airborne += dt

        # Reduce drag near apex to prevent horizontal stall
        if abs(self.vz) < 1.0:
            effective_drag = self.drag * 0.25
        else:
            effective_drag = self.drag

        self.vx *= (1.0 - effective_drag * dt)
        self.vy *= (1.0 - effective_drag * dt)

        # Gravity
        self.vz -= self.gravity * dt

        # Position
        self.x_m += self.vx * dt
        self.y_m += self.vy * dt
        self.z_m += self.vz * dt

        # Ground collision
        if self.z_m <= 0.0:
            self.z_m = 0.0

            if abs(self.vz) > 1.0:
                self.vz = -self.vz * self.bounce_factor
            else:
                self.vz = 0.0
                self.grounded = True


    # ------------------------------------------------------------
    # CAN BE CAUGHT?
    # ------------------------------------------------------------
    def spawn_kickoff(self):
        """Position the ball at the halfway line for kickoff.

        Resets all velocity and state so the ball is ready
        for a kickoff at the centre of the field.
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

    def can_be_caught(self):
        """Ball is only catchable when below 1.5m and not possessed."""
        if self.possessed_by is not None:
            return False
        if self.grounded:
            return True  # grounded ball can always be picked up
        return self.z_m <= self.catch_height_m


    # ------------------------------------------------------------
    # DRAW
    # ------------------------------------------------------------
    def draw(self, screen):
        px, py = self.field.metres_to_px(self.x_m, self.y_m)

        # Validate px/py
        if not isinstance(px, (int, float)) or not isinstance(py, (int, float)):
            return
        if math.isnan(px) or math.isnan(py):
            return
        if math.isinf(px) or math.isinf(py):
            return

        # Shadow
        if not self.grounded:
            shadow_px, shadow_py = self.field.metres_to_px(self.x_m, self.y_m)

            if isinstance(shadow_px, (int, float)) and isinstance(shadow_py, (int, float)):
                try:
                    pygame.draw.ellipse(
                        screen,
                        (40, 40, 40),
                        pygame.Rect(
                            int(shadow_px - 8),
                            int(shadow_py + 18),
                            16,
                            6
                        )
                    )
                except Exception:
                    pass

        # Wobble removed — caused jagged apex/catch due to pixel-space int() truncation

        # Spin
        self.rotation = (self.rotation + self.spin_rate * 0.016) % 360

        # Ball sprite size
        width = int(self.field.scale * 1.2)
        height = int(self.field.scale * 0.6)

        # Height offset
        if not self.grounded:
            py = py - int(self.z_m * 10)

        # Build safe rect
        try:
            rect = pygame.Rect(
                int(px - width // 2),
                int(py - height // 2),
                int(width),
                int(height)
            )
        except Exception:
            return

        # Draw ball
        try:
            pygame.draw.ellipse(screen, (220, 220, 220), rect)
        except Exception:
            return
