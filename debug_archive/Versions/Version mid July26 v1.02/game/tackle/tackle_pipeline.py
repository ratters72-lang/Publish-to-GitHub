"""STUB — All tackle lifecycle moved to world/open_play_engine.py.

This file exists for backwards compatibility only.
Do not instantiate or call execute().
"""
print("[DEPRECATED] tackle_pipeline.py imported — no-op")

class TacklePipeline:
    """STUB — all logic moved to OpenPlayEngine."""
    def __init__(self, rng_seed=None):
        self.tes = None
    def reset(self): pass
    def execute(self, ctx): return {}
    def initiate_tackle(self, ctx): pass
    def reset_for_open_play(self): pass
