"""fb_chase_target_axis.py — PURE classification of a fullback emergency-chase target.

THE QUESTION
------------
The tagged fullback's emergency-chase command is generated from the ball
carrier's live position.  Is that command the carrier's CURRENT POSITION, a
fixed X-ONLY TRAIL behind it, an X-ONLY LEAD ahead of it, or a TWO-AXIS
INTERCEPT projection — and does the command, or the body that integrates it,
cross AHEAD of the carrier's lane while the line break is being recovered?

This module is the SINGLE, TESTABLE DEFINITION of those classifications.  It
takes measured numbers and returns labels / counts.  It never reads the
engine, never mutates anything, and imports nothing outside the standard
library, so a verifier can drive it with hand-built frames and a probe can
drive it with live telemetry.

AXIS CONVENTION (the engine's own fixed directional rule)
---------------------------------------------------------
Attack runs +X and the defending tryline is the +X end, so "ahead of the
carrier" means a LARGER X.  ``is_ahead_of_lane_x`` is that attack-axis test
and is what the audit reports.  ``is_ahead_of_heading`` is the stricter,
heading-relative twin (forward of the carrier along its own velocity); it is
reported alongside so a carrier running back towards its own line cannot be
mis-read.
"""

from __future__ import annotations

import math

__all__ = [
    "AXIS_EPS_M",
    "CROSS_EPS_M",
    "TAU_GRID",
    "TARGET_CURRENT_POSITION",
    "TARGET_INTERCEPT_XY",
    "TARGET_LEAD_X",
    "TARGET_TRAIL_X",
    "TARGET_UNKNOWN",
    "AxisSummary",
    "CrossingStats",
    "best_fit_lead_horizon",
    "classify_target_axis",
    "crossing_stats",
    "is_ahead_of_heading",
    "is_ahead_of_lane_x",
    "lead_explains_y",
    "overshoot_margin_m",
    "y_axis_utilisation",
]

# ── Tolerances ────────────────────────────────────────────────────────
# A target coordinate within this of the carrier's own coordinate carries NO
# command on that axis: it is a mirror of the live position.  5 cm is well
# below any real command (the smallest authored offset in the chase family is
# ``FB_CHASE_TRAIL_M`` = 1.0 m) and well above float noise.
AXIS_EPS_M: float = 0.05

# A body offset within this of the carrier counts as COINCIDENT, not crossed.
CROSS_EPS_M: float = 0.01

# ── Candidate lead horizons (seconds) ─────────────────────────────────
# The grid the best-fit y-lead search sweeps.  0.0 is the "no lead"
# hypothesis (the live mirror); 0.35 is the engine's own
# ``anticipation_lead_time_s`` default; 0.6 is a generous running horizon.
TAU_GRID: tuple = (0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.45, 0.60)

# ── Labels ────────────────────────────────────────────────────────────
TARGET_UNKNOWN: str = "UNKNOWN"
TARGET_CURRENT_POSITION: str = "CURRENT_POSITION"
TARGET_TRAIL_X: str = "TRAIL_X"
TARGET_LEAD_X: str = "LEAD_X"
TARGET_INTERCEPT_XY: str = "INTERCEPT_XY"


def _finite(*values) -> bool:
    """True when every value is a finite real number."""
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(number):
            return False
    return True


def classify_target_axis(
    target_x,
    target_y,
    bc_x,
    bc_y,
    axis_eps: float = AXIS_EPS_M,
) -> str:
    """Classify a command point against the carrier's live position.

    Returns one of:

      * ``CURRENT_POSITION`` — both axes within ``axis_eps``: a pure mirror of
        the live carrier.
      * ``INTERCEPT_XY`` — the Y axis carries a command (``|dy| > axis_eps``),
        i.e. a two-axis projection rather than a lane mirror.
      * ``LEAD_X`` — Y carries no command and the point sits at a LARGER X
        than the carrier (ahead of it along the attack direction).
      * ``TRAIL_X`` — Y carries no command and the point sits at a SMALLER X
        (goal-side of it).
      * ``UNKNOWN`` — non-finite input, reported honestly, never guessed.

    Pure and total: never raises, never mutates.
    """
    if not _finite(target_x, target_y, bc_x, bc_y):
        return TARGET_UNKNOWN
    dx = float(target_x) - float(bc_x)
    dy = float(target_y) - float(bc_y)
    eps = float(axis_eps)
    if abs(dx) <= eps and abs(dy) <= eps:
        return TARGET_CURRENT_POSITION
    if abs(dy) > eps:
        return TARGET_INTERCEPT_XY
    return TARGET_LEAD_X if dx > 0.0 else TARGET_TRAIL_X


def is_ahead_of_lane_x(fb_x, bc_x, eps: float = CROSS_EPS_M) -> bool:
    """True when *fb_x* is past the carrier's X by more than *eps*.

    THE AUDIT'S PRIMARY CROSSING TEST.  Attack runs +X, so a larger X than the
    carrier is "ahead of the carrier" in the direction the play is going.  A
    body level with the carrier (within ``eps``) is NOT counted as ahead.
    """
    if not _finite(fb_x, bc_x):
        return False
    return float(fb_x) - float(bc_x) > float(eps)


def is_ahead_of_heading(fb_x, fb_y, bc_x, bc_y, bc_vx, bc_vy,
                        eps: float = CROSS_EPS_M) -> bool:
    """True when (*fb_x*, *fb_y*) is FORWARD of the carrier along its heading.

    The heading-relative twin of :func:`is_ahead_of_lane_x`: the projection of
    the defender offset onto the carrier's UNIT velocity exceeds ``eps``.  A
    carrier slower than ``eps`` has no heading and False is returned — the
    caller should fall back to :func:`is_ahead_of_lane_x`.
    """
    if not _finite(fb_x, fb_y, bc_x, bc_y, bc_vx, bc_vy):
        return False
    vx = float(bc_vx)
    vy = float(bc_vy)
    speed = math.hypot(vx, vy)
    if speed <= eps:
        return False
    forward = (
        (float(fb_x) - float(bc_x)) * vx
        + (float(fb_y) - float(bc_y)) * vy
    ) / speed
    return forward > float(eps)


def overshoot_margin_m(fb_x, bc_x) -> float:
    """Signed attack-axis margin: positive means AHEAD of the carrier."""
    if not _finite(fb_x, bc_x):
        return 0.0
    return float(fb_x) - float(bc_x)


def y_axis_utilisation(target_y, bc_y) -> float:
    """``|target_y - bc_y|`` — how much lateral command the target carries.

    Zero (or float noise) is the measured signature of "the command does not
    use the y axis at all": the lateral destination IS the carrier's live lane,
    so the defender is asked to mirror, never to intercept.
    """
    if not _finite(target_y, bc_y):
        return 0.0
    return abs(float(target_y) - float(bc_y))


def best_fit_lead_horizon(
    target_y,
    bc_y,
    bc_vy,
    tau_grid: tuple = TAU_GRID,
) -> tuple:
    """The lead horizon ``tau*`` that best explains *target_y* from the carrier.

    Tests every horizon in *tau_grid* and returns ``(tau, residual, mirror)``:

      * ``tau``      — the horizon minimising ``|target_y - (bc_y + bc_vy*tau)|``,
      * ``residual`` — that minimum absolute error, in metres,
      * ``mirror``   — True when the no-lead hypothesis (``tau == 0.0``) wins.

    Ties resolve to the SMALLEST horizon and the grid is ascending, so "no
    lead" is never displaced by an equally good projection.  Non-finite input
    returns ``(0.0, 0.0, True)`` — the conservative "no lead" reading.
    """
    if not _finite(target_y, bc_y, bc_vy):
        return 0.0, 0.0, True
    target = float(target_y)
    base = float(bc_y)
    vy = float(bc_vy)
    best_tau = 0.0
    best_residual = abs(target - base)
    for raw_tau in tau_grid:
        try:
            tau = float(raw_tau)
        except (TypeError, ValueError):
            continue
        residual = abs(target - (base + vy * tau))
        if residual < best_residual - 1e-12:
            best_tau = tau
            best_residual = residual
    return best_tau, best_residual, best_tau == 0.0


def lead_explains_y(
    target_y,
    bc_y,
    bc_vy,
    min_gain_m: float = 0.05,
    tau_grid: tuple = TAU_GRID,
) -> bool:
    """True when a NON-ZERO horizon explains the target Y better by *min_gain_m*.

    The gain floor stops float noise from reading as a projection.
    """
    if not _finite(target_y, bc_y, bc_vy):
        return False
    _tau, residual, mirror = best_fit_lead_horizon(
        target_y, bc_y, bc_vy, tau_grid,
    )
    if mirror:
        return False
    mirror_residual = abs(float(target_y) - float(bc_y))
    return (mirror_residual - residual) > float(min_gain_m)


class AxisSummary:
    """Aggregate of one measurement population (one rollout leg).

    Mutable on purpose: the probe accumulates frame by frame.  Every field is
    a plain number so the object serialises into a report line directly.
    """

    __slots__ = (
        "absolute_target_error_sum",
        "best_fit_tau_sum",
        "crossing_x_body_events",
        "crossing_y_lane_events",
        "frames",
        "target_ahead_frames",
        "worst_ahead_margin_m",
        "worst_lateral_margin_m",
        "y_axis_mirror_frames",
        "y_axis_utilisation_sum",
    )

    def __init__(self) -> None:
        self.frames: int = 0
        self.target_ahead_frames: int = 0
        self.y_axis_mirror_frames: int = 0
        self.y_axis_utilisation_sum: float = 0.0
        self.absolute_target_error_sum: float = 0.0
        self.best_fit_tau_sum: float = 0.0
        self.worst_ahead_margin_m: float = 0.0
        self.worst_lateral_margin_m: float = 0.0
        self.crossing_x_body_events: int = 0
        self.crossing_y_lane_events: int = 0

    @property
    def y_axis_mirror_share(self) -> float:
        """Share of frames whose Y command is the live lane (no lead)."""
        return self.y_axis_mirror_frames / self.frames if self.frames else 0.0

    @property
    def target_ahead_share(self) -> float:
        """Share of frames whose X command sits AHEAD of the carrier."""
        return self.target_ahead_frames / self.frames if self.frames else 0.0

    @property
    def mean_y_axis_utilisation_m(self) -> float:
        """Mean ``|target_y - bc.y|`` across the population."""
        return (
            self.y_axis_utilisation_sum / self.frames if self.frames else 0.0
        )

    @property
    def mean_absolute_target_error_m(self) -> float:
        """Mean 2D distance between the command and the live carrier."""
        return (
            self.absolute_target_error_sum / self.frames if self.frames else 0.0
        )

    @property
    def mean_best_fit_lead_s(self) -> float:
        """Mean best-fit lead horizon: the projection the command actually has."""
        return self.best_fit_tau_sum / self.frames if self.frames else 0.0


class CrossingStats:
    """Per-window crossing record produced by :func:`crossing_stats`."""

    __slots__ = (
        "frames",
        "x_ahead_frames",
        "x_ahead_peak_m",
        "x_lane_crossings",
        "y_lane_crossings",
        "y_lateral_peak_m",
    )

    def __init__(self) -> None:
        self.frames: int = 0
        self.x_ahead_frames: int = 0
        self.x_lane_crossings: int = 0
        self.y_lane_crossings: int = 0
        self.x_ahead_peak_m: float = 0.0
        self.y_lateral_peak_m: float = 0.0

    @property
    def x_ahead_share(self) -> float:
        """Share of window frames with the body ahead of the carrier in X."""
        return self.x_ahead_frames / self.frames if self.frames else 0.0

    @property
    def crossed(self) -> bool:
        """True when the body crossed ahead in X or across the lane in Y."""
        return self.x_lane_crossings > 0 or self.y_lane_crossings > 0


def crossing_stats(samples, eps: float = CROSS_EPS_M) -> CrossingStats:
    """Count X and Y lane crossings of the defender relative to the carrier.

    *samples* is an ordered iterable of frames, each a mapping (or any object)
    exposing ``fb_x``, ``bc_x``, ``fb_y``, ``bc_y``.  Signed offsets are
    ``sx = fb_x - bc_x`` and ``sy = fb_y - bc_y``; a CROSSING is a sign change
    of the offset between consecutive frames, so

      * ``x_lane_crossings`` counts the defender going from behind to ahead
        (or the reverse) in X — the attack-axis pass, and
      * ``y_lane_crossings`` counts it crossing from one lateral side of the
        carrier's lane to the other — the "crosses the carrier lane" event.

    A frame whose offset is within ``eps`` is COINCIDENT: it neither creates
    nor breaks a crossing (a body standing exactly on the lane has not passed
    through it), and the pending side survives until the offset leaves the
    band again, so a jink dithering across the lane counts once per real pass.

    Pure: reads only, returns a fresh :class:`CrossingStats`.
    """
    stats = CrossingStats()
    prev_sx = None
    prev_sy = None
    for sample in samples or ():
        try:
            fb_x = float(getattr(sample, "fb_x", sample["fb_x"]))
            bc_x = float(getattr(sample, "bc_x", sample["bc_x"]))
            fb_y = float(getattr(sample, "fb_y", sample["fb_y"]))
            bc_y = float(getattr(sample, "bc_y", sample["bc_y"]))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        if not _finite(fb_x, bc_x, fb_y, bc_y):
            continue
        stats.frames += 1
        sx = fb_x - bc_x
        sy = fb_y - bc_y
        if sx > eps:
            stats.x_ahead_frames += 1
            stats.x_ahead_peak_m = max(stats.x_ahead_peak_m, sx)
        stats.y_lateral_peak_m = max(stats.y_lateral_peak_m, abs(sy))

        if abs(sx) > eps:
            if prev_sx is not None and prev_sx * sx < 0.0:
                stats.x_lane_crossings += 1
            prev_sx = sx
        if abs(sy) > eps:
            if prev_sy is not None and prev_sy * sy < 0.0:
                stats.y_lane_crossings += 1
            prev_sy = sy
    return stats
