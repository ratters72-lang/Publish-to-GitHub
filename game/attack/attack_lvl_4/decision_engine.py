"""DecisionEngine — deprecated, no longer used by OpenPlayEngine.

BC intent is now driven by RunLogic geometry blending in AttackController.
"""


class DecisionEngine:
    """Deprecated.  Kept as a no-op for backward compatibility."""

    def __init__(self, world):
        self.world = world

    def update(self, dt: float) -> None:
        """No-op — intent flipping removed per BC steering rewrite."""
        pass
