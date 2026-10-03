"""
defensive_line_manager.py — Unified interface for defensive line state.

Consolidates anchor, spacing, and retreat management into a single
object that OpenPlayEngine delegates to.  This avoids inline
scatter across multiple open-play phase handlers.

Key responsibilities:
  - set_anchor(x)       → writes world.defensive_line_anchor = x
  - set_spacing_center(y) → stores the Y-centre for defensive spacing
  - reset_retreat_state()  → resets retreat timer + active flag (D4)
"""


class DefensiveLineManager:
    """Manages defensive line anchor, retreat state, and spacing centre.

    Intended to be owned by OpenPlayEngine as ``self.defensive_line_manager``
    and called during PTB_EXEC->PTB transition (D-series patches).
    """

    def __init__(self, world):
        self.world = world

        # D4: Retreat state — timer + active flag
        self.retreat_timer: float = 0.0
        self.retreat_active: bool = False

    # ─────────────────────────────────────────────────────────────────
    # D3 — Anchor update
    # ─────────────────────────────────────────────────────────────────

    def set_anchor(self, anchor_x: float) -> None:
        """Set the defensive line anchor to *anchor_x*.

        TASK G ANCHOR AUTHORITY (Layer 1): every anchor write is enforced
        by the authority — the value MUST equal ``ruck_x_m + 10`` while a
        ruck exists, and a locked anchor may only be re-published with the
        identical value.  A violating write raises
        ``AnchorAuthorityViolation`` immediately instead of silently
        overwriting the locked anchor.
        """
        from game.defence.defence_lvl_1.anchor_authority import set_anchor as authority_set_anchor
        authority_set_anchor(self.world, anchor_x)

    # ─────────────────────────────────────────────────────────────────
    # D4 — Retreat state reset
    # ─────────────────────────────────────────────────────────────────

    def reset_retreat_state(self) -> None:
        """Reset retreat timer and active flag.

        Called at PTB_EXEC->PTB transition (D4) so that all defenders
        begin retreating with a fresh timer rather than carrying over
        stale hesitation from the previous tackle cycle.
        """
        self.retreat_timer = 0.0
        self.retreat_active = False

    # ─────────────────────────────────────────────────────────────────
    # D6 — Spacing centre
    # ─────────────────────────────────────────────────────────────────

    def set_spacing_center(self, y_center: float) -> None:
        """Set the Y-centre for defensive spacing.

        *y_center* is typically the ruck Y-position (ruck_y_m) so that
        the defensive line spreads symmetrically around the new ruck.
        """
        if hasattr(self.world, "defensive_spacing_center_y"):
            self.world.defensive_spacing_center_y = y_center
