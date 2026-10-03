"""Functions for updating world state: ruck position, offside line, spacing, anchors."""

import math
from .world_state import WorldState


def update_ruck_position(state: WorldState, tackle_x_m: float, tackle_y_m: float) -> None:
    """Set the ruck position where the tackle occurred."""
    state.set_ruck(tackle_x_m, tackle_y_m, "WORLD_UPDATES")


def offside_line_x(state: WorldState) -> float:
    """Return the x-coordinate of the defensive offside line (metres).

    The defence must be behind this line at the PTB.
    """
    if state.defending_left:
        # defending left → the ruck is their offside line; must stay behind it
        # (i.e. further left than the ruck when defending left)
        return state.ruck_x_m - 10.0
    else:
        return state.ruck_x_m + 10.0


def defensive_spacing(num_defenders: int, field_width_m: float) -> list[float]:
    """Distribute defenders evenly across the field width.

    Args:
        num_defenders: number of players in the defensive line.
        field_width_m: total field width in metres (70m for NRL).

    Returns:
        List of y-coordinates (metres) for each defender, sorted.
    """
    if num_defenders <= 1:
        return [field_width_m / 2.0]

    gap = field_width_m / (num_defenders + 1)
    return [gap * (i + 1) for i in range(num_defenders)]


def anchor_points(state: WorldState) -> dict:
    """Compute key anchor positions on the field.

    Returns:
        dict with keys:
          - ruck: (x, y) of the ruck
          - left_marker: (x, y) of left marker (defender at PTB)
          - right_marker: (x, y) of right marker
          - dummy_half: (x, y) where dummy-half stands
          - first_receiver: (x, y) first receiver position
    """
    ruck_x = state.ruck_x_m
    ruck_y = state.ruck_y_m

    # Markers stand 1m either side of ruck, 1m back
    marker_offset_x = 0.0
    marker_offset_y = 1.0

    return {
        "ruck": (ruck_x, ruck_y),
        "left_marker": (ruck_x - marker_offset_x, ruck_y - marker_offset_y),
        "right_marker": (ruck_x - marker_offset_x, ruck_y + marker_offset_y),
        "dummy_half": (ruck_x, ruck_y + 2.0),
        "first_receiver": (ruck_x + 5.0, ruck_y),
    }
