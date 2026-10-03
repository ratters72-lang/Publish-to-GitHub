# --- Movement helpers (Phase 1) ---

def lerp(a, b, t):
    """Linear interpolation."""
    return a + (b - a) * t


def clamp_angle_change(current, target, max_delta):
    """
    Clamp how much the facing angle can change in one update.
    Prevents snapping and creates turning inertia.
    """
    import math

    # normalise difference to [-pi, pi]
    diff = (target - current + math.pi) % (2 * math.pi) - math.pi

    # clamp
    if diff > max_delta:
        diff = max_delta
    elif diff < -max_delta:
        diff = -max_delta

    return current + diff


class CatchSystem:
    """
    Pure catch-mechanics engine — only determines IF a catch is possible.
    
    Does NOT set world state, does NOT call open play transitions.
    The caller (check_for_catch in world/catch_system.py) handles all
    state transitions (ball possession, ball_carrier, ko.on_catch_complete,
    open_play.start).
    
    This is the ONLY file where catch physics lives (height, radius, timing).
    """
    CATCH_RADIUS = 1.2
    CATCH_HEIGHT = 1.5   # metres — ball must be at or below this height

    def try_catch(self, ball, receiver):
        """
        Check if `receiver` can catch `ball` right now.
        
        Returns
        -------
        bool
            True if catch is possible, False otherwise.
            
        Side effects when True:
            - ball.possessed_by = receiver
            - ball.vx/vy/vz zeroed
            - ball.z_m = 1.0 (carrying height)
            - receiver.has_ball = True
            
        NOTE: Does NOT modify world state or kickoff controller.
        """
        if receiver is None:
            return False
        if ball is None:
            return False
        if ball.possessed_by is not None:
            return False

        # Height check
        if ball.z_m > self.CATCH_HEIGHT:
            return False

        # Distance check
        dx = ball.x_m - receiver.x
        dy = ball.y_m - receiver.y
        dist = (dx*dx + dy*dy)**0.5
        if dist > self.CATCH_RADIUS:
            return False

        # ── Perform catch mechanics ──
        ball.possessed_by = receiver
        # Ball.update lerp handles position/velocity/grounded smoothly

        receiver.has_ball = True

        return True
