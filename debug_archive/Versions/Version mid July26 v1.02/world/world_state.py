"""
Top-level world state for the rugby league simulation.
This file unifies:
- GameWorldState (global phase machine)
- GameSubState (debug/logging sub-states)
- WorldState (full world snapshot)
"""

from dataclasses import dataclass, field
from enum import Enum

from .ruck_state import _RuckStateBackwardsCompat as _RuckStateCompat


# ---------------------------------------------------------
# GLOBAL PHASE MACHINE
# ---------------------------------------------------------

class GameWorldState(Enum):
    KICKOFF = "KICKOFF"
    OPEN_PLAY = "OPEN_PLAY"
    RESTART = "RESTART"


class GameSubState(Enum):
    """High-level game sub-state during open play — drives defence movement policy.
    
    KICKOFF_IN_AIR: ball is in the air after a kickoff, not yet catchable.
    OPEN_PLAY: normal play, defence can move, ruck follows BC.
    OPEN_PLAY_STARTUP: brief settling period after kickoff catch (0.5s).
                       No TES, spacing, DH, PTB, or tackle checks run.
    TACKLE_INIT: one-frame state when a tackle is initiated (freeze BC, snap defender, start TES).
    TACKLE_CONTACT: TES running (contact phase). Defence briefly blocked.
    TACKLE_DRIVE: TES drive phase.
    TACKLE_DOMINANCE: TES dominance/release phase.
    PTB: play-the-ball, DH pickup, markers hold.
    POST_RUCK_OPEN_PLAY: ball out, retreat completes, line reforms → OPEN_PLAY.
    RESET_LINE: explicit, slower rebuild (scrum, penalty, kickoff, 7-tackle set).
    """
    KICKOFF_IN_AIR = "kickoff_in_air"
    OPEN_PLAY = "open_play"
    TACKLE_INIT = "tackle_init"
    TACKLE_CONTACT = "tackle_contact"
    TACKLE_DRIVE = "tackle_drive"
    TACKLE_DOMINANCE = "tackle_dominance"
    RUCK_FORMING = "ruck_forming"
    PTB = "ptb"
    POST_RUCK_OPEN_PLAY = "post_ruck_open_play"
    RESET_LINE = "reset_line"


# ══════════════════════════════════════════════════════════════
# STEP‑03A‑06C — TES tackle-phase predicates
# ══════════════════════════════════════════════════════════════

def is_tackle_phase(world) -> bool:
    """Return True if TES tackle engagement is active.

    During these sub-states, only TES should move players inside the
    ruck bubble.  Other systems must freeze or avoid players within
    ~3.5m of the ball carrier.
    """
    return world.sub_state in (
        GameSubState.TACKLE_INIT,
        GameSubState.TACKLE_CONTACT,
        GameSubState.TACKLE_DRIVE,
        GameSubState.TACKLE_DOMINANCE,
    )


def is_in_ruck_bubble(player, bc, radius: float = 3.5) -> bool:
    """Return True if *player* is inside the ruck bubble around *bc*.

    Uses squared-distance comparison to avoid sqrt.  The default
    radius of 3.5 metres defines the ruck zone where players are
    close enough to interfere with the tackle engagement.
    """
    if player is None or bc is None:
        return False
    dx = player.x - bc.x
    dy = player.y - bc.y
    return (dx * dx + dy * dy) < (radius * radius)


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
    - open play engine
    - rendering
    """

    # All players (single source of truth for update loops)
    players: list = field(default_factory=list)

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

    # ── Legacy ruck_state field (kept for backwards compat) ──
    ruck_state: _RuckStateCompat = _RuckStateCompat.NONE

    # Old ruck_timer (kept for backwards compat)
    ruck_timer: float = 0.0

    # Team orientation
    defending_left: bool = True
    attacking_direction: int = 1     # +1 = attacking right, -1 = attacking left

    # Global phase
    game_state: GameWorldState = GameWorldState.KICKOFF

    # Open-play sub-state (drives defence movement policy)
    sub_state: GameSubState = GameSubState.KICKOFF_IN_AIR

    # Optional: store references to ball + players
    ball: object = None

    # Kickoff-phase teams (named by role — zero ambiguity)
    kicking_team: list = field(default_factory=list)
    receiving_team: list = field(default_factory=list)

    # Open-play teams (set from receiving/kicking after a legal kickoff)
    attacking_team: list = field(default_factory=list)
    defending_team: list = field(default_factory=list)

    # Ball carrier (the player currently in possession)
    ball_carrier: object = None

    # Predicted kickoff landing point (set by run.py after ball.kick())
    kickoff_landing_x: float = 0.0
    kickoff_landing_y: float = 0.0

    # Designated primary catcher for this kickoff (set by run.py after ball.kick())
    primary_catcher: object = None

    # Kickoff run-back controller (created on catch, used briefly after kickoff)
    kickoff_runback: object = None

    # Kickoff kick-chase controller (manages kicking-team chase lanes)
    kickoff_kickchase: object = None

    # Reference to the open-play engine (set by run.py)
    open_play: object = None

    # Reference to the open-play engine under the name defensive behaviour expects
    open_play_engine: object = None

    # Reference to the kickoff controller (set by run.py)
    kickoff_controller: object = None

    # One-frame catch immunity flag
    just_caught_this_frame: bool = False

    # Post-catch contact immunity counter
    disable_contact_frames: int = 0

    # ═══════════════════════════════════════════════════════════════
    # KICKOFF ISOLATION (Phase 5) — single source of truth
    # ═══════════════════════════════════════════════════════════════
    in_kickoff: bool = False

    # KICKOFF COLLISION GUARD (Rules 1 & 15)
    just_left_kickoff_timer: float = 0.0
    KICKOFF_COLLISION_DELAY_S: float = 0.2

    # Frame counter
    frame_count: int = 0

    # ═══════════════════════════════════════════════════════════════
    # TACKLE OWNERSHIP
    # ═══════════════════════════════════════════════════════════════
    primary_tackler_id: int | None = None
    secondary_tacklers: list[int] = field(default_factory=list)

    # ═══════════════════════════════════════════════════════════════
    # DEBUG METHODS
    # ═══════════════════════════════════════════════════════════════

    def set_sub_state(self, new_state, caller):
        """Set sub_state with debug trace."""
        print(f"[SUBSTATE] {caller} → {new_state}")
        self.sub_state = new_state

    def freeze_bc(self, caller):
        """Freeze ball carrier with debug trace."""
        print(f"[FREEZE BC] {caller}")
        if self.ball_carrier is not None:
            self.ball_carrier.frozen = True

    def freeze_ball(self, caller):
        """Freeze ball with debug trace."""
        print(f"[FREEZE BALL] {caller}")
        if self.ball is not None:
            self.ball.frozen = True

    def set_ruck(self, x, y, caller):
        """Set ruck position with debug trace."""
        print(f"[RUCK SET] {caller} → ({x:.2f},{y:.2f})")
        self.ruck_x_m = x
        self.ruck_y_m = y
