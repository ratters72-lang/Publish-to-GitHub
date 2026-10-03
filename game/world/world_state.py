"""
Top-level world state for the rugby league simulation.
This file unifies:
- GameWorldState (global phase machine)
- WorldState (full world snapshot)
- OpenPlay (stub for open-play logic)
"""

from dataclasses import dataclass, field
from enum import Enum


# ---------------------------------------------------------
# GLOBAL PHASE MACHINE
# ---------------------------------------------------------

class GameWorldState(Enum):
    KICKOFF = "KICKOFF"
    OPEN_PLAY = "OPEN_PLAY"
    RESTART = "RESTART"


# ---------------------------------------------------------
# FULL WORLD SNAPSHOT
# ---------------------------------------------------------

@dataclass
class WorldState:
    """
    Holds the complete state of the simulated world each frame.
    This is the authoritative world snapshot used by:
    - kickoff controller
    - catch system
    - open play
    - tackle pipeline
    - rendering
    """

    # Ball state
    ball_x_m: float = 60.0
    ball_y_m: float = 35.0
    ball_z_m: float = 0.0
    ball_vx: float = 0.0
    ball_vy: float = 0.0
    ball_vz: float = 0.0
    ball_live: bool = False          # ball is catchable / playable
    ball_in_play: bool = False       # ball is legally in play (not dead)

    # Ruck / tackle state
    ruck_x_m: float = 60.0
    ruck_y_m: float = 35.0
    tackle_count: int = 0
    ptb_in_progress: bool = False
    ptb_timer: float = 0.0

    # Team orientation
    defending_left: bool = True
    attacking_direction: int = 1     # +1 = attacking right, -1 = attacking left

    # Global phase
    game_state: GameWorldState = GameWorldState.KICKOFF

    # Optional: store references to ball + players
    ball: object = None
    attacking_team: list = field(default_factory=list)
    defending_team: list = field(default_factory=list)


# ---------------------------------------------------------
# OPEN PLAY SUBSYSTEM (stub)
# ---------------------------------------------------------

class OpenPlayEngine:
    """
    Minimal open-play subsystem.
    Kickoff → catch system calls open_play.start(ball_carrier)
    """

    def __init__(self, world_state: WorldState):
        self.world = world_state
        self.ball_carrier = None

    def start(self, ball_carrier):
        """Called when kickoff transitions to OPEN_PLAY."""
        self.ball_carrier = ball_carrier
        self.world.game_state = GameWorldState.OPEN_PLAY
        self.world.ball_live = True
        self.world.ball_in_play = True

        # Set ruck position to ball carrier
        self.world.ruck_x_m = ball_carrier.x
        self.world.ruck_y_m = ball_carrier.y

        # Reset tackle count
        self.world.tackle_count = 0

    def update(self, dt: float):
        """
        Called every frame during open play.
        Later this will handle:
        - tackle detection
        - ruck formation
        - defensive line movement
        - possession switching
        - knock-ons
        - kicks in general play
        """
        if not self.ball_carrier:
            return

        # Update ruck position to follow ball carrier
        self.world.ruck_x_m = self.ball_carrier.x
        self.world.ruck_y_m = self.ball_carrier.y
