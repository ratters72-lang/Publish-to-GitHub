"""
player/arrival_controller.py — Arrival predictor for defensive movement.

Replaces _accelerate_toward_target() for defenders during TES/RELEASE/PTB
phases.  Velocity is scaled proportional to remaining distance to target,
eliminating overshoot and oscillation.

Three regimes:
  - Outside slow_radius:  full speed toward target (max_speed)
  - Inside slow_radius:   speed = max_speed * (error / slow_radius)
  - Inside stop_radius:   snap to target, zero velocity, mark reached
"""

import math


def arrival_predictor(
    player,
    stop_radius: float = 0.1,
    slow_radius: float = 1.5,
) -> None:
    """Set vx/vy proportional to distance from target_x/target_y.

    Parameters
    ----------
    player : Player
        The defender to move.  Reads target_x, target_y, max_speed_mps
        (or max_speed fallback).  Writes vx, vy, and may set x, y.
    stop_radius : float
        Distance below which the player is snapped to target and velocity
        is zeroed (metres).
    slow_radius : float
        Distance below which speed scales linearly with remaining error
        (metres).  Must be >= stop_radius.
    """
    dx = player.target_x - player.x
    dy = player.target_y - player.y
    dist = math.hypot(dx, dy)

    speed = getattr(player, "max_speed_mps", None) or getattr(player, "max_speed", 6.5)

    if dist < stop_radius:
        # Arrived: snap to target, zero velocity
        player.vx = 0.0
        player.vy = 0.0
        player.x = player.target_x
        player.y = player.target_y
        player.reached_target = True
    elif dist < slow_radius:
        # Approach: scale speed proportional to remaining distance
        scale = dist / slow_radius
        player.vx = (dx / dist) * speed * scale
        player.vy = (dy / dist) * speed * scale
        player.reached_target = False
    else:
        # Far away: full speed toward target
        player.vx = (dx / dist) * speed
        player.vy = (dy / dist) * speed
        player.reached_target = False
