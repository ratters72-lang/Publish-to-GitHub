"""Solid collision resolver — prevents overlap between solid players.

Used by OpenPlayEngine each frame during non-tackle, non-jump gameplay phases.
Only resolves pairs where BOTH players have solid=True. Pure positional
separation — no impulse, no friction, no momentum transfer.

Design:
  - Each solid player has a body radius (CollisionBody.RADIUS_M ≈ 0.55m).
  - Overlap = combined_radius - distance.  If > 0, push both apart
    proportionally to inverse mass (lighter player moves more).
  - Fractional correction (0.3 per frame) avoids teleport.
  - Frozen players are skipped entirely — they cannot be pushed.
  - Resolution is O(n²) per frame — fine for 26 players.
"""

import math

from player.collision_body import CollisionBody


class SolidCollisionResolver:
    """Resolves positional overlap between solid players.

    Usage::
        resolver = SolidCollisionResolver()
        resolver.resolve_all(world.attacking_team + world.defending_team)
    """

    # Fraction of overlap resolved per frame.
    # 0.3 → after 10 frames overlap is 0.7^10 ≈ 0.028 of original.
    CORRECTION_FACTOR = 0.3

    # Minimum combined radius for separation (safety floor).
    # Prevents degenerate 0-radius bodies from causing NaNs.
    MIN_COMBINED_RADIUS = 0.1

    def resolve_all(self, players: list) -> None:
        """Resolve all solid-solid overlaps in a player list.

        Only pairs where BOTH players have ``solid=True`` are resolved.
        Frozen players are skipped.
        """
        n = len(players)
        for i in range(n):
            a = players[i]
            if not a.solid or a.frozen:
                continue
            for j in range(i + 1, n):
                b = players[j]
                if not b.solid or b.frozen:
                    continue
                self._resolve_pair(a, b)

    def _resolve_pair(self, a, b) -> None:
        """Separate two solid players if they overlap.

        Uses mass-weighted positional correction along the collision
        normal.  A fraction of the overlap is resolved each frame to
        avoid teleport/spawn-separation artefacts.
        """
        # Compute distance and normal
        dx = a.x - b.x
        dy = a.y - b.y
        dist = math.hypot(dx, dy)

        # Combined radius (minimum safe value)
        combined = max(
            getattr(a.body, 'radius', CollisionBody.RADIUS_M)
            + getattr(b.body, 'radius', CollisionBody.RADIUS_M),
            self.MIN_COMBINED_RADIUS,
        )

        overlap = combined - dist
        if overlap <= 0:
            return  # no overlap

        # Degenerate case: exactly on top of each other
        if dist < 1e-8:
            nx, ny = 1.0, 0.0
            overlap = combined
        else:
            nx = dx / dist
            ny = dy / dist

        # Fractional correction per frame
        correction = overlap * self.CORRECTION_FACTOR

        # Mass-weighted ratio (lighter moves more)
        mass_a = max(getattr(a, 'mass', 1.0), 0.01)
        mass_b = max(getattr(b, 'mass', 1.0), 0.01)
        total_mass = mass_a + mass_b

        ratio_a = mass_b / total_mass
        ratio_b = mass_a / total_mass

        # Push apart
        a.x += nx * correction * ratio_a
        a.y += ny * correction * ratio_a
        b.x -= nx * correction * ratio_b
        b.y -= ny * correction * ratio_b

        # Sync collision bodies after position change
        if hasattr(a, 'body') and a.body is not None:
            a.body.sync()
        if hasattr(b, 'body') and b.body is not None:
            b.body.sync()
