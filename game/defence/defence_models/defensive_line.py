"""
defensive_line.py — DefensiveLine and RuckUnit data models.

Provides proper dataclass-based structures for the defensive line and
ruck unit (DH + markers) so that line-level properties (depth, width,
spacing, stagger, compression, speed) and unit-level properties
(markers, DH) are tracked explicitly rather than ad-hoc via dicts.

All units in metres.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DefensiveLineState:
    """
    Shared defensive line state for one frame.

    Replaces the ad-hoc dict returned by DefensiveLineGovernor.compute_line_state().
    All defenders on the same team inherit the same base depth, width,
    compression, and line speed.  Role-specific adjustments are applied
    on top by DefensiveRoleSpacing.

    Fields
    ------
    depth_x : float
        X position of the defensive line anchor in world space.
        Typically ruck_x + 10.0 (or defensive_line_anchor).
    width_m : float
        Y spread of the defensive line (from edge to edge).  Full line
        width before compression.
    compression : float
        0.0 = fully wide (no compression)
        1.0 = fully compressed (converged on FR lane)
    speed_mps : float
        Shared movement speed for the whole line (m/s).
    step_up : float
        0.0 = hold position
        1.0 = full step-up toward BC
    max_lane_index : int
        Maximum absolute lane index (centre=0, edges=±max).
        Used for per-defender Y offset computation.
    spacing_m : float
        Derived spacing between adjacent defenders after compression.
    """

    depth_x: float = 60.0
    width_m: float = 40.0
    compression: float = 0.0
    speed_mps: float = 4.0
    step_up: float = 0.0
    max_lane_index: int = 3
    spacing_m: float = 5.0

    @classmethod
    def from_governor_dict(cls, d: dict) -> "DefensiveLineState":
        """Construct from a governor output dict (backward compat)."""
        n = d.get("max_lane_index", 3) * 2 + 1
        spacing = d.get("width_m", 40.0) / max(1, n - 1) if n > 1 else 5.0
        return cls(
            depth_x=d.get("depth_x", 60.0),
            width_m=d.get("width_m", 40.0),
            compression=d.get("compression", 0.0),
            speed_mps=d.get("speed_mps", 4.0),
            step_up=d.get("step_up", 0.0),
            max_lane_index=d.get("max_lane_index", 3),
            spacing_m=spacing,
        )

    def to_dict(self) -> dict:
        """Convert back to dict for legacy callers."""
        return {
            "depth_x": self.depth_x,
            "width_m": self.width_m,
            "compression": self.compression,
            "speed_mps": self.speed_mps,
            "step_up": self.step_up,
            "max_lane_index": self.max_lane_index,
        }


@dataclass
class RuckUnit:
    """
    The ruck unit = DH + two markers, positioned tight to the ruck.

    This unit stays at the ruck position through TACKLE → RELEASE → PTB
    phases and only dissolves once the PTB completes and the ball is
    live (POST_PTB_RESET expires).

    Fields
    ------
    dh : object | None
        The dummy half player (attacker who picks up from the ruck).
        Set during _prepare_release(), cleared at POST_PTB_RESET exit.
    marker_a : object | None
        Defender positioned tight to the ruck (MARKER_A role).
    marker_b : object | None
        Second defender positioned tight to the ruck (MARKER_B role).
    frozen : bool
        True while the ruck bubble is active (TES + PTB phases).
    """

    dh: Optional[object] = None
    marker_a: Optional[object] = None
    marker_b: Optional[object] = None
    frozen: bool = True

    @property
    def markers(self) -> list:
        """Return list of active markers."""
        return [m for m in (self.marker_a, self.marker_b) if m is not None]

    @property
    def all_players(self) -> list:
        """Return all players in the ruck unit (DH + markers)."""
        return [p for p in (self.dh, self.marker_a, self.marker_b) if p is not None]

    @property
    def engaged(self) -> bool:
        """True if the unit has at least one marker assigned."""
        return self.marker_a is not None or self.marker_b is not None

    def clear(self) -> None:
        """Clear all references and unfreeze."""
        self.dh = None
        self.marker_a = None
        self.marker_b = None
        self.frozen = False

    def freeze(self) -> None:
        """Freeze all players in the unit."""
        self.frozen = True
        for p in self.all_players:
            p.frozen = True
            p.vx = 0.0
            p.vy = 0.0

    def unfreeze(self) -> None:
        """Unfreeze all players in the unit."""
        self.frozen = False
        for p in self.all_players:
            p.frozen = False


@dataclass
class MarkerPair:
    """
    Tracks a marker pair explicitly for assignment/persistence/clearing.

    Used by the marker system to manage lifecycle independent of the
    players' defender_role attributes.

    Fields
    ------
    left : object | None
        The left-side marker player (closest to ruck Y - 0.5).
    right : object | None
        The right-side marker player (second closest, Y - 1.0).
    frozen_until : float
        Simulation time until which markers remain frozen after PTB.
    """

    left: Optional[object] = None
    right: Optional[object] = None
    frozen_until: float = 0.0

    @property
    def assigned(self) -> bool:
        return self.left is not None or self.right is not None

    def clear(self) -> None:
        self.left = None
        self.right = None
        self.frozen_until = 0.0
