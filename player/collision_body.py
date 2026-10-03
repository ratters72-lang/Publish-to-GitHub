# player/collision_body.py
# COLLISION BODY FOUNDATION (Phase 2) + KICKOFF COLLISION PROPERTIES (Rules 10-14)

import math


class CollisionBody:
    # Realistic rugby‑player collision radius (~0.55m = half of shoulder width).
    # The old default of 12m caused every player pair within 24m to be treated
    # as overlapping, generating massive penetration pushes (up to 12m per
    # pair per frame) that blew players off the field.
    RADIUS_M = 0.55

    def __init__(self, player, radius=None):
        self.player = player
        self.radius = radius if radius is not None else self.RADIUS_M

        # Position mirrors player.x / player.y
        self.x = player.x
        self.y = player.y

        # ═══════════════════════════════════════════════════════
        # PHASE 2: COLLISION ENABLED FLAG
        # ═══════════════════════════════════════════════════════
        # When False, CollisionSystem skips this body entirely.
        # True by default so body-level collision resolution
        # (impulse, friction, pushback) runs every frame.
        # OpenPlayEngine._update_solid_states() keeps this in
        # sync with the per-player p.solid flag for phase-specific
        # overlap exceptions.
        self.collision_enabled = True

        # ═══════════════════════════════════════════════════════
        # COLLISION RESOLUTION STATE (Rules 10-14)
        #
        # These fields track penetration and are used during
        # collision resolution. They MUST be reset at kickoff
        # so stale penetration doesn't corrupt the first frame.
        # ═══════════════════════════════════════════════════════
        self.penetration = 0.0
        self.normal_x = 0.0
        self.normal_y = 0.0
        self.prev_x = player.x
        self.prev_y = player.y

        # ── Physical properties for collision response ──
        self.mass = 1.0
        self.restitution = 0.2
        self.friction = 0.3

        # ── Kickoff collision properties (Rules 11-14) ──
        # During KICKOFF, these are overridden so players
        # become effectively immovable and non-reactive.
        self._normal_mass = 1.0
        self._normal_restitution = 0.2
        self._normal_friction = 0.3

    # ═══════════════════════════════════════════════════════
    # VELOCITY ACCESSORS — read from whichever velocity
    # the player is currently using (new movement: vel_x/vel_y,
    # legacy: vx/vy).
    # ═══════════════════════════════════════════════════════

    @property
    def vx(self):
        """Primary velocity X — check new engine first, fall back to legacy."""
        p = self.player
        if hasattr(p, 'vel_x') and (p.vel_x != 0.0 or getattr(p, 'vx', 0.0) == 0.0):
            return p.vel_x
        return getattr(p, 'vx', 0.0)

    @vx.setter
    def vx(self, value):
        """Set both vel_x and vx so whichever engine reads next gets the update."""
        p = self.player
        p.vel_x = value
        p.vx = value

    @property
    def vy(self):
        p = self.player
        if hasattr(p, 'vel_y') and (p.vel_y != 0.0 or getattr(p, 'vy', 0.0) == 0.0):
            return p.vel_y
        return getattr(p, 'vy', 0.0)

    @vy.setter
    def vy(self, value):
        p = self.player
        p.vel_y = value
        p.vy = value

    # ─────────────────────────────────────────────────────────
    # KICKOFF COLLISION PROPERTIES (Rules 11-14)
    # ─────────────────────────────────────────────────────────

    def apply_kickoff_properties(self):
        """Override body properties during KICKOFF to prevent
        collision resolution from shoving players apart.

        Rule 11: Keep normal collision radius (do NOT shrink it).
                 Collision resolution is already blocked during KICKOFF
                 by Rules 1 & 15 — no need to change the radius.
        Rule 12: Increase mass during kickoff (immovable)
        Rule 13: Zero restitution during kickoff
        Rule 14: Zero friction during kickoff
        """
        from world.world_state import GamePhase
        world = getattr(self.player, '_world_ref', None)
        if world is not None and world.phase == GamePhase.KICKOFF:
            # Save normal values if not already overridden
            if getattr(self, 'mass', 1.0) < 999000:
                # DON'T save/change radius — keep normal (Rule 11 is NOP)
                self._normal_mass = getattr(self, 'mass', 1.0)
                self._normal_restitution = getattr(self, 'restitution', 0.2)
                self._normal_friction = getattr(self, 'friction', 0.3)

            # Rule 11: NOP — keep normal radius
            # Rule 12: make immovable
            self.mass = 999999
            # Rule 13: zero restitution
            self.restitution = 0.0
            # Rule 14: zero friction
            self.friction = 0.0
        else:
            # Restore normal values (radius was never changed)
            self.mass = getattr(self, '_normal_mass', 1.0)
            self.restitution = getattr(self, '_normal_restitution', 0.2)
            self.friction = getattr(self, '_normal_friction', 0.3)

    # ─────────────────────────────────────────────────────────
    # RESTORE (for catch/OPEN_PLAY entry)
    # ─────────────────────────────────────────────────────────

    def restore_normal_properties(self):
        """Restore normal collision body properties.

        Called when leaving KICKOFF (e.g. on catch) so that
        collision resolution resumes with normal mass,
        restitution, and friction.

        Radius was never changed (Rule 11 is NOP), so no
        radius restore needed.
        """
        self.mass = getattr(self, '_normal_mass', 1.0)
        self.restitution = getattr(self, '_normal_restitution', 0.2)
        self.friction = getattr(self, '_normal_friction', 0.3)

    # ─────────────────────────────────────────────────────────
    # RESET (Rule 10)
    # ─────────────────────────────────────────────────────────

    def reset_collision_state(self):
        """Reset all collision resolution fields at kickoff.

        Rule 10: Reset collision bodies at kickoff
        - penetration = 0
        - normal_x = 0, normal_y = 0
        - prev_x = player.x, prev_y = player.y
        - update_bounds()
        """
        self.penetration = 0.0
        self.normal_x = 0.0
        self.normal_y = 0.0
        self.prev_x = self.player.x
        self.prev_y = self.player.y

    # ─────────────────────────────────────────────────────────
    # SYNC
    # ─────────────────────────────────────────────────────────

    def sync(self):
        """Sync body position with player position."""
        self.x = self.player.x
        self.y = self.player.y

    # ─────────────────────────────────────────────────────────
    # DISTANCE
    # ─────────────────────────────────────────────────────────

    def distance_to(self, other):
        """Distance between two collision bodies."""
        dx = other.x - self.x
        dy = other.y - self.y
        return math.hypot(dx, dy)

    def distance_to_point(self, px, py):
        """Distance from this body's centre to a point (px, py)."""
        dx = px - self.x
        dy = py - self.y
        return math.hypot(dx, dy)

    # ─────────────────────────────────────────────────────────
    # OVERLAP / PENETRATION
    # ─────────────────────────────────────────────────────────

    def is_overlapping(self, other):
        """Check if two bodies overlap."""
        return self.distance_to(other) < (self.radius + other.radius)

    def penetration_vector(self, other):
        """Return the vector pushing this body out of the other."""
        dx = self.x - other.x
        dy = self.y - other.y
        dist = math.hypot(dx, dy)

        if dist == 0:
            return (0, 0)

        overlap = (self.radius + other.radius) - dist
        if overlap <= 0:
            return (0, 0)

        nx = dx / dist
        ny = dy / dist
        return (nx * overlap, ny * overlap)
