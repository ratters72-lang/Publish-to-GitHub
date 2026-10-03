# game/tackle/contact_detector.py
# PHASE 3: CONTACT DETECTION (first-pass filter — fine-tuned by OpenPlayEngine)

import math
from world.world_state import GameSubState
from player.player import PlayerState


class ContactDetector:
    """
    First-pass proximity filter for tackle initiation.

    Uses a generous radius (8.0 m) so open-field chase scenarios are caught.
    The actual distance check with mode‑aware radii happens inside
    OpenPlayEngine._check_tackle_initiation(), which also handles TES setup,
    freezing non‑primary defenders, and sub‑state transitions.

    Persistence (3 consecutive frames) prevents noise from brief overlaps.
    """

    # Generous open-field radius — fine‑tuning happens in OpenPlayEngine.
    TACKLE_RADIUS = 8.0
    MIN_BC_SPEED = 1.0           # m/s
    PERSISTENCE_FRAMES = 3       # sustained contact frames

    def __init__(self):
        # Track consecutive frames of valid contact
        self.contact_counters = {}   # (def_id, bc_id) → frames

    def update(self, world, defenders, dt):
        """
        Returns:
            (True, tackler) if tackle triggered
            (False, None) otherwise
        """
        bc = world.ball_carrier

        # Reset if no BC or wrong state
        if bc is None or world.sub_state not in (
            GameSubState.OPEN_PLAY,
            GameSubState.POST_RUCK_OPEN_PLAY
        ):
            self.contact_counters.clear()
            return False, None

        active_tackler = None

        for d in defenders:
            key = (d.player_id, bc.player_id)

            # ─────────────────────────────────────────────
            # 1. Tackle radius check (generous first-pass filter)
            # ─────────────────────────────────────────────
            dx = bc.x - d.x
            dy = bc.y - d.y
            dist = math.hypot(dx, dy)

            if dist > self.TACKLE_RADIUS:
                self.contact_counters[key] = 0
                continue

            # ─────────────────────────────────────────────
            # 2. 0–180° IMPACT ANGLE RULE
            #    (front-on + side-on allowed, blindside rejected)
            # ─────────────────────────────────────────────

            # Tackler → BC direction
            impact_angle = math.atan2(dy, dx)

            # Tackler movement direction (shoulder angle)
            shoulder_angle = math.atan2(d.vy, d.vx)

            # Relative angle normalized to 0–180°
            angle_diff = abs(impact_angle - shoulder_angle)
            if angle_diff > math.pi:
                angle_diff = 2 * math.pi - angle_diff

            angle_deg = angle_diff * (180.0 / math.pi)

            # Classification
            if angle_deg < 30.0:
                angle_ok = True      # front-on
            elif angle_deg < 90.0:
                angle_ok = True      # side-on
            else:
                angle_ok = False     # blindside → reject

            if not angle_ok:
                self.contact_counters[key] = 0
                continue

            # ─────────────────────────────────────────────
            # 3. BC velocity threshold
            # ─────────────────────────────────────────────
            bc_speed = math.hypot(bc.vx, bc.vy)
            if bc_speed < self.MIN_BC_SPEED:
                self.contact_counters[key] = 0
                continue

            # ─────────────────────────────────────────────
            # 4. Persistence counter
            # ─────────────────────────────────────────────
            self.contact_counters[key] = self.contact_counters.get(key, 0) + 1

            if self.contact_counters[key] >= self.PERSISTENCE_FRAMES:
                active_tackler = d
                break

        # Clean counters occasionally
        if len(self.contact_counters) > 50:
            self.contact_counters = {
                k: v for k, v in self.contact_counters.items() if v > 0
            }

        if active_tackler is not None:
            self.contact_counters.clear()
            return True, active_tackler

        return False, None

    def reset(self):
        self.contact_counters.clear()
