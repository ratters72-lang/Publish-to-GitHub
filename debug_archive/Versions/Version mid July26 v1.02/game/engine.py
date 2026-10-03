"""
game.main — Top‑level game engine (BACKWARDS‑COMPATIBLE STUB).

This module originally contained the GameEngine class that created a
separate WorldState() instance.  GameEngine was never wired into the
interactive run.py path — it remained dead code.

The interactive simulation uses:
  run.py -> OpenPlayEngine(world)   (single WorldState shared everywhere)

GameEngine is kept here as a backwards‑compatible stub so that existing
imports in run.py and test scripts do not break.  It does NOT create
its own WorldState() - the original self.world = WorldState() in
__init__ was removed because it created a duplicate world that was
never synchronised with the interactive loop.
"""

from world.world_state import GameWorldState, GameSubState
from world.open_play_engine import OpenPlayEngine


class GameEngine:
    """
    [DEPRECATED] Stub kept for import compatibility.

    Do NOT instantiate.  Use OpenPlayEngine directly.
    GameEngine originally created an orphan WorldState() that was
    never shared with the match loop, leading to the PTB freeze bug
    pattern where the engine mutated one world while the logger
    read another.
    """

    def __init__(self, field=None, rng_seed: int | None = None):
        pass

    def update(self, dt: float):
        raise RuntimeError(
            "GameEngine is deprecated.  Use OpenPlayEngine instead.\n"
            "GameEngine.update() is not wired to the interactive world.\n"
            "See world/open_play_engine.py for the actual game loop controller."
        )
