"""
DEFENCE CONTEXT — STEP‑0 (TES + RUCK ONLY)
==========================================

This class stores all defensive context that is NOT part of:
    • STEP‑1 geometry (width → spacing → lanes)
    • STEP‑2 behaviour (roles → depth offsets)

It contains ONLY:
    • TES roles (primary, secondary, off‑axis)
    • Ruck roles (marker1, marker2, A/B/C, band)
    • Ruck position
    • Tackle outcome flags
    • One‑frame flags (catch immunity)
    • References to world + OpenPlayEngine

NO defensive movement logic lives here.
NO spacing, NO depth, NO geometry, NO band, NO FSM.
"""

class DefenceContext:
    def __init__(self):
        # ─────────────────────────────────────────────
        # MODE — set by OpenPlayEngine at state boundaries
        # "CONTAINMENT" | "SCRAMBLE" | "RETREAT" | "TACKLE"
        # ─────────────────────────────────────────────
        self.mode: str = "CONTAINMENT"

        # ─────────────────────────────────────────────
        # TES ROLES
        # ─────────────────────────────────────────────
        self.primary = None
        self.secondary = None
        self.off_axis_left = None
        self.off_axis_right = None

        # ─────────────────────────────────────────────
        # RUCK ROLES
        # ─────────────────────────────────────────────
        self.marker1 = None
        self.marker2 = None
        self.a = None
        self.b = None
        self.c = None
        self.band = []   # everyone else near ruck

        # ─────────────────────────────────────────────
        # RUCK POSITION
        # ─────────────────────────────────────────────
        self.ruck_x = 0.0
        self.ruck_y = 0.0

        # ─────────────────────────────────────────────
        # TACKLE OUTCOME
        # ─────────────────────────────────────────────
        self.tackle_outcome = None
        self.tackle_outcome_ready = False

        # ─────────────────────────────────────────────
        # ONE‑FRAME FLAGS
        # ─────────────────────────────────────────────
        self.just_caught_this_frame = False

        # ─────────────────────────────────────────────
        # EXTERNAL REFERENCES
        # ─────────────────────────────────────────────
        self.world = None
        self._open_play_engine = None

    # ─────────────────────────────────────────────────────────────
    # RESET
    # ─────────────────────────────────────────────────────────────
    def reset(self):
        self.mode = "CONTAINMENT"

        # TES
        self.primary = None
        self.secondary = None
        self.off_axis_left = None
        self.off_axis_right = None

        # Ruck roles
        self.marker1 = None
        self.marker2 = None
        self.a = None
        self.b = None
        self.c = None
        self.band = []

        # Ruck position
        self.ruck_x = 0.0
        self.ruck_y = 0.0

        # Tackle outcome
        self.tackle_outcome = None
        self.tackle_outcome_ready = False

        self.just_caught_this_frame = False

    # ─────────────────────────────────────────────────────────────
    # RUCK ROLE ASSIGNMENT (used by tackle_complete)
    # ─────────────────────────────────────────────────────────────
    def _assign_ruck_roles(self, defenders):
        """
        Assign ruck roles based purely on distance to the ruck.

        marker1, marker2 = two nearest defenders
        A, B, C          = next three nearest
        band             = everyone else

        No geometry, no spacing, no behaviour.
        Pure STEP‑0 ruck logic.
        """
        if not defenders:
            self.marker1 = None
            self.marker2 = None
            self.a = None
            self.b = None
            self.c = None
            self.band = []
            return

        # Sort by distance to ruck
        sorted_defs = sorted(
            defenders,
            key=lambda d: (d.x - self.ruck_x)**2 + (d.y - self.ruck_y)**2
        )

        # Assign roles
        self.marker1 = sorted_defs[0] if len(sorted_defs) > 0 else None
        self.marker2 = sorted_defs[1] if len(sorted_defs) > 1 else None

        remaining = sorted_defs[2:]

        self.a = remaining[0] if len(remaining) > 0 else None
        self.b = remaining[1] if len(remaining) > 1 else None
        self.c = remaining[2] if len(remaining) > 2 else None

        excluded = {d for d in (self.marker1, self.marker2, self.a, self.b, self.c) if d is not None}
        self.band = [d for d in defenders if d not in excluded]

    # ─────────────────────────────────────────────
    # TACKLE HELPERS (STEP‑0 safe)
    # ─────────────────────────────────────────────
    def get_tacklers(self):
        """
        STEP‑0 version:
        Returns the TES primary + secondary tacklers only.
        No behaviour, no geometry, no movement.
        """
        return [d for d in (self.primary, self.secondary) if d is not None]
