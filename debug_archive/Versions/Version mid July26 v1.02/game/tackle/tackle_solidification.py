# game/tackle/tackle_solidification.py
# PHASE 4: TACKLE SOLIDIFICATION
# Kills L1 spacing and AI movement, but does NOT freeze physics bodies.

class TackleSolidifier:
    """
    When TACKLE_CONTACT is active, this module overrides AI movement.
    It locks L1 spacing targets so players don't drift toward line_x.
    CRITICAL: It does NOT freeze physics bodies or zero out velocities.
    TES needs actual physics state (vx, x_m) to calculate momentum/impact.
    """
    def apply(self, tackler, ball_carrier, defenders):
        if not tackler or not ball_carrier:
            return

        # 1. LOCK TACKLER AI (KILL L1 SPACING)
        # ❌ DO NOT freeze tackler physics
        # ✔ ONLY freeze L1 spacing targets
        tackler.l1_frozen = True
        tackler.target_x = tackler.x
        tackler.target_y = tackler.y

        # 2. LOCK BALL CARRIER AI (KILL L1 SPACING)
        # ❌ DO NOT freeze BC physics
        # ✔ ONLY freeze L1 spacing targets
        ball_carrier.l1_frozen = True
        ball_carrier.target_x = ball_carrier.x
        ball_carrier.target_y = ball_carrier.y

        # 3. LOCK REST OF DEFENDERS AI (KILL L1 SPACING)
        # ❌ DO NOT freeze defenders physics
        # ✔ ONLY freeze L1 spacing targets
        for d in defenders:
            if d is tackler:
                continue
            d.l1_frozen = True
            # Overwrite L1 targets so they stay exactly where they are in physics space
            d.target_x = d.x
            d.target_y = d.y

