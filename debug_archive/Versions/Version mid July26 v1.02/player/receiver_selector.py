"""
Kickoff receiver selection — timing-based approach.

Picks the player whose travel time to the predicted landing point
best matches the ball's flight time. This maximizes the chance of
catching the ball on the full (or as close as possible), rather
than simply sending the closest player.
"""

import math


def choose_kickoff_receiver(players, ball):
    """
    Select the best kickoff receiver from *players* (receiving team)
    based on synchronising arrival time with the ball's flight.

    Parameters
    ----------
    players : list[Player]
        All receiving-team players eligible to catch.
    ball : Ball
        Must have .landing_x_m, .landing_y_m, .flight_time populated
        (set by Ball.kick()).

    Returns
    -------
    Player | None
        The player best timed to catch the ball, or None if the list
        is empty.
    """
    if not players:
        return None

    lx = ball.landing_x_m
    ly = ball.landing_y_m
    t_flight = ball.flight_time

    best_p = None
    best_err = float("inf")

    for p in players:
        # Only receiving-team players are candidates for the catch
        # (the caller already passes the receiving team, but the
        #  guard ensures no accidental kicker assignment)
        if not hasattr(p, "approach_speed") or p.approach_speed <= 0:
            continue

        dx = lx - p.x
        dy = ly - p.y
        dist = math.hypot(dx, dy)

        # Travel time for player to reach the landing point
        t_player = dist / p.approach_speed

        # How far off the ball's flight time are they?
        err = abs(t_player - t_flight)

        if err < best_err:
            best_err = err
            best_p = p

    return best_p


class ReceiverSelector:
    """
    Drop-in class interface wrapping choose_kickoff_receiver.

    Used by code that expects an object with a choose_receiver method.
    """

    def __init__(self):
        self.receiver = None

    def choose_receiver(self, players, landing_x, landing_y, ball=None):
        """
        Unified entry point — accepts either:

        1.  Legacy signature: choose_receiver(players, landing_x, landing_y)
            → falls back to closest-player logic (no ball flight info).

        2.  Full signature: choose_receiver(players, landing_x, landing_y, ball)
            → uses the timing-based algorithm from choose_kickoff_receiver.
        """
        if ball is not None and hasattr(ball, "flight_time") and ball.flight_time > 0:
            self.receiver = choose_kickoff_receiver(players, ball)
        else:
            # Legacy fallback — closest to landing point
            if not players:
                self.receiver = None
            else:
                self.receiver = min(
                    players,
                    key=lambda p: (p.x - landing_x) ** 2 + (p.y - landing_y) ** 2,
                )

        return self.receiver

    def move_receiver(self, landing_x, landing_y, dt):
        """
        Set the receiver's target to the real-time landing point.
        The player's own update() handles the actual movement each frame.
        """
        if not self.receiver:
            return

        self.receiver.target_x = landing_x
        self.receiver.target_y = landing_y
