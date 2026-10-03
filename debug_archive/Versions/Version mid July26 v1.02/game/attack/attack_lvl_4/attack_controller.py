class AttackController:
    """Applies BC movement vector to actual physics."""

    def apply_vector(self, bc, vx, vy, dt):
        speed = bc.max_speed_mps
        # inertia smoothing
        alpha = 0.2  # smoothing factor (0.1–0.3 recommended)
        bc.vx = bc.vx * (1 - alpha) + (vx * speed) * alpha
        bc.vy = bc.vy * (1 - alpha) + (vy * speed) * alpha

        bc.x += bc.vx * dt
        bc.y += bc.vy * dt
