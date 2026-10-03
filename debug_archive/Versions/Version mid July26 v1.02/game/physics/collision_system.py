# game/physics/collision_system.py
# COLLISION BODY FOUNDATION + KICKOFF COLLISION GUARD (Rules 1 & 15)
# PHASE 2: Fractional positional correction + impulse-based pushback + friction

import math


class CollisionSystem:
    # ═══════════════════════════════════════════════════════
    # TUNING PARAMETERS
    # ═══════════════════════════════════════════════════════
    # Fraction of overlap applied per frame as positional
    # correction.  0.3 means 30% each frame → after ~10
    # frames overlap is reduced to 0.7^10 ≈ 0.028 of
    # original.  This avoids teleport while converging
    # quickly enough to avoid visible interpenetration.
    CORRECTION_FACTOR = 0.3

    def __init__(self):
        # You can add spatial partitioning later (grid, quadtree, buckets)
        pass

    def check_collision(self, body_a, body_b):
        """Return True if two bodies overlap."""
        return body_a.is_overlapping(body_b)

    # ═══════════════════════════════════════════════════════
    # RESOLVE PAIR (Phase 2)
    # ═══════════════════════════════════════════════════════

    def resolve_pair(self, body_a, body_b):
        """
        Phase 2 deterministic collision resolution.

        Steps (applied per frame):
          1. Compute overlap and collision normal.
          2. Fractional positional correction (no teleport).
          3. Impulse along normal (mass-weighted pushback).
          4. Friction along tangent (Coulomb-limited).

        All values are written to the player's velocity fields
        via body.vx / body.vy setters so both vel_x/vx and
        vel_y/vy are updated simultaneously.
        """
        dx = body_a.x - body_b.x
        dy = body_a.y - body_b.y
        dist = math.hypot(dx, dy)

        if dist < 1e-8:
            # Degenerate case: exactly on top of each other.
            # Push along a random-ish axis.
            dx, dy = 1.0, 0.0
            dist = 1.0

        overlap = (body_a.radius + body_b.radius) - dist
        if overlap <= 0:
            return  # no contact

        # Collision normal (pointing from B toward A)
        nx = dx / dist
        ny = dy / dist

        # ── 1. FRACTIONAL POSITIONAL CORRECTION ──
        # Apply a fraction of the overlap per frame to avoid teleport.
        correction = overlap * self.CORRECTION_FACTOR

        total_mass = body_a.mass + body_b.mass
        if total_mass <= 0:
            return

        ratio_a = body_b.mass / total_mass  # lighter body moves more
        ratio_b = body_a.mass / total_mass

        body_a.player.x += nx * correction * ratio_a
        body_a.player.y += ny * correction * ratio_a
        body_b.player.x -= nx * correction * ratio_b
        body_b.player.y -= ny * correction * ratio_b

        # Sync collision body positions after correction
        body_a.sync()
        body_b.sync()

        # ── 2. IMPULSE ALONG NORMAL (pushback) ──
        # Compute relative velocity along collision normal.
        rv_x = body_a.vx - body_b.vx
        rv_y = body_a.vy - body_b.vy
        vn = rv_x * nx + rv_y * ny

        # Only apply impulse if bodies are approaching (vn < 0).
        if vn < 0:
            inv_mass_a = 1.0 / body_a.mass if body_a.mass > 0 else 0.0
            inv_mass_b = 1.0 / body_b.mass if body_b.mass > 0 else 0.0
            inv_mass_sum = inv_mass_a + inv_mass_b

            if inv_mass_sum > 0:
                e = body_a.restitution if body_a.mass > 0 else body_b.restitution
                # Impulse magnitude (normal bounce)
                j = -(1.0 + e) * vn / inv_mass_sum

                body_a.vx += (j * inv_mass_a) * nx
                body_a.vy += (j * inv_mass_a) * ny
                body_b.vx -= (j * inv_mass_b) * nx
                body_b.vy -= (j * inv_mass_b) * ny

                # ── 3. FRICTION ALONG TANGENT ──
                # Tangent vector = perpendicular to normal.
                tx = -ny
                ty = nx
                vt = rv_x * tx + rv_y * ty

                # Friction impulse, Coulomb-limited by |j|.
                cf = body_a.friction if body_a.mass > 0 else body_b.friction
                jt = -cf * abs(j)  # max friction impulse magnitude

                # Clamp friction to avoid reverse slip.
                if abs(vt) < abs(jt) * inv_mass_sum:
                    jt = -vt / inv_mass_sum  # static friction: kill tangential velocity

                body_a.vx += (jt * inv_mass_a) * tx
                body_a.vy += (jt * inv_mass_a) * ty
                body_b.vx -= (jt * inv_mass_b) * tx
                body_b.vy -= (jt * inv_mass_b) * ty

    # ═══════════════════════════════════════════════════════
    # RESOLVE ALL
    # ═══════════════════════════════════════════════════════

    def resolve_all(self, players, world=None):
        """
        Resolve collisions for all players where collision
        is enabled.  Naive O(n²) — fine for Phase 2.

        ═══════════════════════════════════════════════════════
        KICKOFF COLLISION GUARD (Rules 1 & 15)
        ═══════════════════════════════════════════════════════
        Rule  1: During KICKOFF, skip collision resolution entirely.
        Rule 15: After leaving KICKOFF, delay collision resolution
                 for KICKOFF_COLLISION_DELAY_S seconds.
        ═══════════════════════════════════════════════════════
        """
        # ── Rule 1: Complete disable during KICKOFF ──
        if world is not None:
            from world.world_state import GameWorldState
            if world.game_state == GameWorldState.KICKOFF:
                return  # Rule 1

            # ── Rule 15: Delayed start after leaving KICKOFF ──
            timer = getattr(world, 'just_left_kickoff_timer', 0.0)
            if timer > 0:
                return  # Rule 15

        # ── Phase 2: skip bodies that have collisions disabled ──
        enabled = [p.body for p in players if getattr(p.body, 'collision_enabled', False)]
        if len(enabled) < 2:
            return

        n = len(enabled)
        for i in range(n):
            for j in range(i + 1, n):
                self.resolve_pair(enabled[i], enabled[j])

    def query_nearby(self, players, player, radius):
        """
        Return list of players within 'radius' of 'player'.
        """
        result = []
        for other in players:
            if other is player:
                continue
            if player.body.distance_to(other.body) <= radius:
                result.append(other)
        return result
