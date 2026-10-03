"""retreat_locomotion.py — DEBT-AWARE RETREAT LOCOMOTION (Retreat Geometry).

CONTRACT
--------
A defender that owes ground to its retreat target must travel there with a
gait that MATCHES the debt, not with one generic navigation law.  Retreat
is a compliance movement: the body is closing a known X deficit on a line
it must not overshoot, while its lateral (spacing) slot is owned by the
spacing authority.  One flat speed for a 0.3 m shuffle-back and an 8 m
emergency retreat is the defect this module removes.

The debt is the remaining X deficit to the COMMANDED retreat target:

    retreat_debt_m(d) = max(0.0, d.target_x - d.x)

which, on the compliance write (``apply_forced_retreat`` with
``force_anchor_x=True`` — every era's retreat path), IS the obligation
deficit the engine's arrival test discharges on:

    enforce_retreat_obligation -> if d.x >= anchor: role = DEF_LINE_READY

So debt == 0 is exactly the discharge condition: the gait converges on the
same milestone the authority gate uses, with no second threshold.

THE BACKPEDAL CONTRACT (the reason the facing gate is a latch, not a band)
-------------------------------------------------------------------------
Retreat has TWO facing regimes, and they are separated by ONE threshold —
``retreat_backpedal_max_debt_m`` (default 1.5 m, the small-debt band):

    BACKPEDAL        debt inside the threshold
                     eyes on the play; the body closes the remaining X
                     deficit by REVERSE MOVEMENT ALONG X (it travels
                     backwards relative to the way it is looking).  This is
                     a compliance shuffle: the last couple of metres onto
                     the line, watching the ball carrier / ruck.

    TURN AND RUN     debt clearly beyond the threshold
                     the body may turn its back on the play and sprint to
                     the line.  This is the only regime in which "travel"
                     facing is permitted.

Three properties make that contract real rather than aspirational:

  * PERMISSION, NOT EMERGENCE.  The travel facing is gated on the debt,
    so a band table edit can never silently move the point at which a
    defender is allowed to turn away from the play.

  * DEDICATED HYSTERESIS.  The facing verdict has its OWN margin
    (``retreat_facing_travel_hysteresis_m``, default 0.75 m) rather than
    inheriting the speed band's anti-chatter margin, and the verdict is
    LATCHED on the body (``retreat_facing_policy``).  Inside the hold zone
    [threshold, threshold + hysteresis) the previous verdict is kept, so a
    body hovering at the boundary cannot alternate backpedal/travel — which
    would ADD facing churn instead of removing it.

  * MONOTONE DEBT => ONE FLIP.  Inside an obligation run the debt only
    shrinks (the target is the fixed compliance line), so a body's facing
    trajectory is TRAVEL -> hold -> BACKPEDAL: it sprints back, then turns
    to face the play for the final settle.  At most one facing reversal per
    run, at the end, by construction.

THREE GAITS (one per debt band)
-------------------------------
    BACKPEDAL_SHUFFLE  debt < 1.5 m  the backpedal: X-dominant reverse
                                     travel, eyes on the play, the legacy
                                     0.85 x ceiling command so the X
                                     closure that discharges the obligation
                                     is provably untouched.
    RETREAT_GLIDE      1.5-5 m       turn-and-run: near-ceiling speed, the
                                     spacing slot still tracked.
    RETREAT_RUN        debt > 5 m    the full retreat run: ceiling speed,
                                     straight back, facing travel.

Each gait carries its own speed scale, accel/decel lerp factors, lateral
priority and facing policy, resolved from ``DefenceTuners`` (see
``retreat_mode_profiles``).  ``MovementState.RETREAT`` is deliberately KEPT
as the movement state: the gait is a finer-grained locomotion layer issued
on top of it, so ``VALID_TRANSITIONS``, the MMDL skip, the smooth-damp
exclusion and every audit that keys on the state name are untouched.

WHY THE BACKPEDAL BAND IS X-DOMINANT
------------------------------------
The backpedal carries the LOWEST lateral priority (0.0), not the highest.
Compliance is an X-only obligation, and the lateral blend re-aims the
heading every frame as the spacing slot moves; a body settling its slot
while closing its last metre swings its travel vector — measured as
travel-direction reversals.  Straight reverse travel along X cannot slow
the X closure (no speed is diverted into Y), so this is discharge-neutral
or better by construction.

ANTI-SPIN
---------
The generic navigation law steers straight at the target and lets facing
follow the velocity, so a body that overshoots the line reverses its travel
direction and spins about.  Four levers remove that:

  * arrival taper — below ``retreat_arrival_band_m`` the commanded speed
    scales with the remaining debt, so the body SETTLES instead of
    overshooting (no overshoot => no reversal);
  * lateral priority — the direction blend gives the gait its own X/Y
    weighting, so a spacing-Y adjustment no longer swings the heading;
  * no-reverse X clamp — while ground is still owed the body may never
    gain it back toward the ruck (``vx >= 0``); cleared once the debt is 0;
  * the held-backpedal facing latch above — the eyes stay on the play
    exactly while the body is still closing a small deficit, and the
    velocity -> facing coupling is suppressed for those frames so the held
    facing can never leak a small negative vx into an X closure that has to
    stay positive.

WHO STAMPS IT
-------------
``RetreatManager`` (the sole forced-retreat authority) is the ONLY writer.
It stamps the profile on every body it retreats and CLEARS it on every body
it does not, so the profile's lifetime is exactly the retreat write — no
second writer, no staleness, no drift.  ``Player`` consumes the profile and
clears it at the end of the frame, so a stale gait can never leak.

IMPORT SAFETY
-------------
No module-scope PROJECT imports (the anchor read is FUNCTION-LOCAL, exactly
like ``retreat_obligation.py``), so every defensive manager may import this
in any order with no cycle.  ``player/player.py`` never imports this module
— it reads the profile duck-typed, as it reads every other manager-issued
field.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum, auto

__all__ = [
    "RetreatLocomotionMode",
    "RetreatLocomotion",
    "FACE_THREAT",
    "FACE_TRAVEL",
    "DEFAULT_BACKPEDAL_MAX_DEBT_M",
    "DEFAULT_SHUFFLE_MAX_DEBT_M",
    "DEFAULT_GLIDE_MAX_DEBT_M",
    "DEFAULT_MODE_HYSTERESIS_M",
    "DEFAULT_FACING_TRAVEL_HYSTERESIS_M",
    "DEFAULT_MODE_MIN_DWELL_FRAMES",
    "DEFAULT_ARRIVAL_BAND_M",
    "DEFAULT_ARRIVAL_MIN_SCALE",
    "DEFAULT_FACING_RATE_DEG_S",
    "MODE_ORDER",
    "is_locomotion_enabled",
    "compliance_line_x",
    "retreat_debt_m",
    "backpedal_threshold_m",
    "is_backpedal",
    "threshold_mode",
    "select_retreat_locomotion_mode",
    "resolve_facing_policy",
    "resolve_mode_spec",
    "apply_retreat_locomotion",
    "clear_retreat_locomotion",
    "format_locomotion_report",
]


class RetreatLocomotionMode(Enum):
    """The three debt bands a retreating body may travel in."""

    BACKPEDAL_SHUFFLE = auto()   # debt < 1.5 m — the backpedal
    RETREAT_GLIDE = auto()       # 1.5-5 m     — turn and run
    RETREAT_RUN = auto()         # debt > 5 m  — get back, fast


#: Facing policy — keep the eyes on the play (the ball carrier / ruck).
FACE_THREAT: str = "threat"
#: Facing policy — face the direction of travel (sprinting away).
FACE_TRAVEL: str = "travel"

#: THE BACKPEDAL THRESHOLD, in metres of X debt.  At or below this the body
#: holds the eyes on the play and closes the deficit by reverse movement
#: along X; beyond it (plus the facing hysteresis below) it may turn its
#: back on the play and run.  This is the ONE threshold that separates the
#: two facing regimes, and it is also the small-debt band edge.
DEFAULT_BACKPEDAL_MAX_DEBT_M: float = 1.5

#: Deprecated alias of the backpedal threshold.  Kept because the tuner and
#: several stubs still carry the name; ``apply_retreat_locomotion`` reads
#: ``retreat_backpedal_max_debt_m`` first and only falls back to this when
#: the new name is absent, so there is exactly ONE effective edge.
DEFAULT_SHUFFLE_MAX_DEBT_M: float = DEFAULT_BACKPEDAL_MAX_DEBT_M
DEFAULT_GLIDE_MAX_DEBT_M: float = 5.0

# Anti-chatter margin for the SPEED band selection.
DEFAULT_MODE_HYSTERESIS_M: float = 0.35
# Anti-chatter margin for the FACING verdict.  Deliberately wider than the
# speed band's margin and independently tunable: the facing verdict is what
# the backpedal contract is about, and it also sets the realistic turn
# point (backpedal to threshold + margin, then turn and go).
DEFAULT_FACING_TRAVEL_HYSTERESIS_M: float = 0.75
DEFAULT_MODE_MIN_DWELL_FRAMES: int = 3

# Arrival taper: below this debt the commanded speed scales with the
# remaining debt, floored so the body never crawls to a standstill short of
# the line.  The band is deliberately SHALLOW and the floor HIGH: the
# overshoot that generated the old reversal spins is now handled by the
# no-reverse clamp, so the taper only has to smooth the final few
# centimetres — a deep taper would slow the X closure that discharges the
# obligation, which is exactly the discharge regression the first tuning
# pass produced (floor 0.35 over 0.6 m turned the last metre into a crawl).
DEFAULT_ARRIVAL_BAND_M: float = 0.4
DEFAULT_ARRIVAL_MIN_SCALE: float = 0.80

# Facing rotation ceiling while a gait holds the eyes on the threat (deg/s).
# Bounded so the facing can never snap; ~9 deg/frame at 60 Hz.
DEFAULT_FACING_RATE_DEG_S: float = 540.0

DEFAULT_MAX_DEFENSIVE_SPEED_MPS: float = 8.3

#: Canonical gait ordering (slowest/shortest-debt first).  Ordering is what
#: the hysteresis and dwell logic reasons about.
MODE_ORDER: tuple = (
    RetreatLocomotionMode.BACKPEDAL_SHUFFLE,
    RetreatLocomotionMode.RETREAT_GLIDE,
    RetreatLocomotionMode.RETREAT_RUN,
)

_MODE_ORDERS: dict = {mode: index for index, mode in enumerate(MODE_ORDER)}
_MODE_BY_NAME: dict = {mode.name: mode for mode in MODE_ORDER}

# Per-gait default locomotion profile.  ``DefenceTuners.retreat_mode_profiles``
# may override any subset of the keys per mode; the module merges on top of
# these defaults so a partial tuner entry can never produce a half-built gait.
DEFAULT_MODE_SPECS: dict = {
    # THE BACKPEDAL — debt inside the threshold.  0.85 == the legacy flat
    # retreat command, so this band is discharge-NEUTRAL: the backpedal
    # character lives in the FACING (eyes on the play) and the X-DOMINANT
    # heading (lateral_priority 0.0 -> the travel vector cannot swing with
    # the spacing slot), never in a low top speed.  A 0.40 scale here
    # measured as a 4x discharge regression, because most obligated frames
    # sit in this band.
    "BACKPEDAL_SHUFFLE": {
        "speed_scale": 0.85,
        "accel": 0.50,
        "decel": 0.65,                # crisp stop (short steps)
        "lateral_priority": 0.0,
        "facing_policy": FACE_THREAT,
    },
    # Turn and run: cover ground, spacing slot still tracked.  The band
    # DECLARES travel facing, but the facing gate still requires the debt to
    # have cleared the backpedal threshold by the facing hysteresis, so the
    # turn can never happen while the body is inside the backpedal band.
    "RETREAT_GLIDE": {
        "speed_scale": 0.95,
        "accel": 0.65,
        "decel": 0.50,
        "lateral_priority": 0.25,
        "facing_policy": FACE_TRAVEL,
    },
    # Large deficit — the full retreat run: ceiling speed, straight back
    # (low lateral priority), facing the direction of travel.
    "RETREAT_RUN": {
        "speed_scale": 1.00,
        "accel": 0.85,
        "decel": 0.40,
        "lateral_priority": 0.10,
        "facing_policy": FACE_TRAVEL,
    },
}

_SPEC_KEYS = ("speed_scale", "accel", "decel", "lateral_priority",
              "facing_policy")


@dataclass(frozen=True)
class RetreatLocomotion:
    """Immutable per-frame locomotion issue for one retreating body.

    Issued by the retreat authority and consumed by ``Player`` — the body
    integrates what it was issued, it never decides a gait itself.
    """

    mode_name: str
    debt_m: float
    speed_scale: float
    move_speed_mps: float
    accel: float
    decel: float
    lateral_priority: float
    facing_policy: str
    facing_rate_deg_s: float
    no_reverse: bool
    backpedal_active: bool

    @property
    def faces_threat(self) -> bool:
        """True when the gait holds the eyes on the play."""
        return self.facing_policy == FACE_THREAT


def _tuner(tuners, name: str, default):
    """Read a tuner value safely, falling back to *default* if absent."""
    if tuners is None:
        return default
    value = getattr(tuners, name, None)
    if value is None:
        return default
    return value


def _float_tuner(tuners, name: str, default: float) -> float:
    """Numeric tuner read that can never raise on a malformed value."""
    try:
        return float(_tuner(tuners, name, default))
    except (TypeError, ValueError):
        return float(default)


def _optional_float_tuner(tuners, name: str) -> float | None:
    """Numeric tuner read that reports ABSENCE rather than a default.

    Needed where a name must be distinguished from an explicit value: the
    backpedal threshold falls back to its deprecated alias only when the
    canonical name is genuinely missing.
    """
    value = _tuner(tuners, name, None)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def is_locomotion_enabled(tuners) -> bool:
    """True when debt-aware retreat locomotion is armed.

    Defaults to True so the gait law is live unless a tuner or the runtime
    lever explicitly turns it off (which restores the legacy flat
    ``0.85 x ceiling`` straight-at-target navigation).
    """
    import os
    if os.environ.get("RLM_RETREAT_LOCOMOTION", "") == "0":
        return False
    return bool(_tuner(tuners, "retreat_locomotion_enabled", True))


# ─────────────────────────────────────────────────────────────────────
# DEBT + THRESHOLD
# ─────────────────────────────────────────────────────────────────────

def compliance_line_x(world) -> float:
    """The engine's own retreat line — ``defensive_line_anchor_m(world)``.

    Single-sourced from the anchor authority (function-local import keeps
    this module free of project-scope imports).  Returns ``nan`` when no
    anchor is reachable, which callers treat as "no measurable debt".
    """
    if world is None:
        return float("nan")
    try:
        from game.defence.defence_lvl_1.anchor_authority import (
            defensive_line_anchor_m,
        )
        return float(defensive_line_anchor_m(world))
    except Exception:
        return float("nan")


def retreat_debt_m(world, d, target_x: float | None = None) -> float:
    """Remaining X deficit to the commanded retreat target, in metres.

    ``target_x`` defaults to the body's live ``target_x`` — which the
    retreat authority has already pinned to the compliance line on every
    retreat path, making this deficit identical to the obligation deficit
    the engine's arrival test discharges on.  Returns 0.0 (never negative)
    when the target is unreachable or the body has arrived.
    """
    if d is None:
        return 0.0
    try:
        x = float(getattr(d, "x", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if target_x is None:
        target_x = getattr(d, "target_x", None)
    if target_x is None:
        anchor = compliance_line_x(world)
        if not math.isfinite(anchor):
            return 0.0
        target_x = anchor
    try:
        tx = float(target_x)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(tx):
        return 0.0
    return max(0.0, tx - x)


def backpedal_threshold_m(tuners=None) -> float:
    """THE backpedal threshold, in metres of X debt.

    The canonical tuner is ``retreat_backpedal_max_debt_m``; the long-
    standing ``retreat_shuffle_max_debt_m`` is read only as a fallback when
    the canonical name is absent, so the two can never disagree in a live
    tuner object.
    """
    value = _optional_float_tuner(tuners, "retreat_backpedal_max_debt_m")
    if value is None:
        value = _optional_float_tuner(tuners, "retreat_shuffle_max_debt_m")
    if value is None:
        value = DEFAULT_BACKPEDAL_MAX_DEBT_M
    return max(0.0, value)


def is_backpedal(debt_m: float, tuners=None) -> bool:
    """True while *debt_m* is inside the backpedal threshold.

    The single predicate for "this body is still in the small-debt
    backpedal regime" — eyes on the play, reverse movement along X.
    """
    return float(debt_m) <= backpedal_threshold_m(tuners)


# ─────────────────────────────────────────────────────────────────────
# GAIT SELECTION (hysteresis + asymmetric dwell)
# ─────────────────────────────────────────────────────────────────────

def threshold_mode(debt_m: float, shuffle_max: float,
                   glide_max: float) -> RetreatLocomotionMode:
    """Band lookup with NO hysteresis — the raw classification.

    ``< shuffle_max``        -> BACKPEDAL_SHUFFLE
    ``[shuffle_max, glide_max)`` -> RETREAT_GLIDE
    ``>= glide_max``         -> RETREAT_RUN
    """
    if debt_m < shuffle_max:
        return RetreatLocomotionMode.BACKPEDAL_SHUFFLE
    if debt_m < glide_max:
        return RetreatLocomotionMode.RETREAT_GLIDE
    return RetreatLocomotionMode.RETREAT_RUN


def _boundary_between(prev: RetreatLocomotionMode,
                      candidate: RetreatLocomotionMode,
                      shuffle_max: float,
                      glide_max: float) -> float:
    """The debt boundary separating *prev* from *candidate*.

    Adjacent bands are separated by their shared edge; a non-adjacent pair
    (SHUFFLE <-> RUN) is separated by the FAR edge, so a jump across two
    bands must clear both.
    """
    highest = max(_MODE_ORDERS[prev], _MODE_ORDERS[candidate])
    return shuffle_max if highest == 1 else glide_max


def _stamp_mode_change(d, mode: RetreatLocomotionMode, frame: int) -> None:
    """Record the gait and the frame it was entered on (dwell clock)."""
    d.retreat_locomotion_mode = mode.name
    d.retreat_mode_changed_frame = int(frame)


def select_retreat_locomotion_mode(
    d,
    debt_m: float,
    shuffle_max: float = DEFAULT_SHUFFLE_MAX_DEBT_M,
    glide_max: float = DEFAULT_GLIDE_MAX_DEBT_M,
    hysteresis: float = DEFAULT_MODE_HYSTERESIS_M,
    min_dwell_frames: int = DEFAULT_MODE_MIN_DWELL_FRAMES,
    frame: int = 0,
) -> RetreatLocomotionMode:
    """Choose the gait for *debt_m*, with anti-chatter protection.

    A gait change requires the debt to clear the separating boundary by
    ``hysteresis`` metres, so a body hovering on an edge keeps its gait.
    Within that, an UPGRADE (faster gait) additionally honours
    ``min_dwell_frames`` since the last change while a DOWNGRADE always
    takes effect immediately — the settle gait must never be delayed.
    """
    candidate = threshold_mode(debt_m, shuffle_max, glide_max)

    prev_name = getattr(d, "retreat_locomotion_mode", None)
    prev = _MODE_BY_NAME.get(prev_name) if prev_name else None
    if prev is None:
        _stamp_mode_change(d, candidate, frame)
        return candidate
    if candidate is prev:
        return prev

    cand_order = _MODE_ORDERS[candidate]
    prev_order = _MODE_ORDERS[prev]
    boundary = _boundary_between(prev, candidate, shuffle_max, glide_max)

    if cand_order > prev_order:
        # UPGRADE — must clear the boundary by the hysteresis margin.
        if debt_m < boundary + hysteresis:
            return prev
        changed = getattr(d, "retreat_mode_changed_frame", None)
        if (min_dwell_frames > 0 and changed is not None
                and (int(frame) - int(changed)) < int(min_dwell_frames)):
            return prev
    else:
        # DOWNGRADE — must fall clear of the boundary; never held.
        if debt_m > boundary - hysteresis:
            return prev

    _stamp_mode_change(d, candidate, frame)
    return candidate


# ─────────────────────────────────────────────────────────────────────
# THE FACING GATE (the backpedal contract)
# ─────────────────────────────────────────────────────────────────────

def resolve_facing_policy(
    d,
    debt_m: float,
    band_policy: str,
    backpedal_max: float = DEFAULT_BACKPEDAL_MAX_DEBT_M,
    hysteresis: float = DEFAULT_FACING_TRAVEL_HYSTERESIS_M,
) -> str:
    """THE backpedal gate: may this body turn its back on the play?

    Two facing regimes, one threshold, with a hold zone and a latch:

        debt <  backpedal_max
            BACKPEDAL — the eyes stay on the play.  Forced, never latched:
            a body inside the threshold must be looking at the ball.

        debt >= backpedal_max + hysteresis
            TURN AND RUN permitted — provided the BAND also permits travel
            (``band_policy == FACE_TRAVEL``).  A band that declares threat
            facing therefore keeps the eyes on the play even at large debt.

        otherwise (the hold zone)
            the PREVIOUS verdict is kept, so a body hovering at the
            boundary cannot alternate backpedal/travel frame-to-frame.

    The verdict is latched on ``d.retreat_facing_policy`` so the hold zone
    is honest across frames.  A body with no prior verdict inside the hold
    zone defaults to THREAT: the contract's conservative direction is that
    a defender may not turn away from the play until the debt has CLEARLY
    exceeded the backpedal threshold.
    """
    prev = getattr(d, "retreat_facing_policy", None)
    if prev not in (FACE_THREAT, FACE_TRAVEL):
        prev = None

    if band_policy != FACE_TRAVEL:
        # The band itself refuses to turn away from the play.
        policy = FACE_THREAT
    elif float(debt_m) >= float(backpedal_max) + max(0.0, float(hysteresis)):
        policy = FACE_TRAVEL
    elif float(debt_m) < float(backpedal_max):
        policy = FACE_THREAT
    else:
        policy = prev or FACE_THREAT

    d.retreat_facing_policy = policy
    return policy


# ─────────────────────────────────────────────────────────────────────
# PROFILE RESOLUTION
# ─────────────────────────────────────────────────────────────────────

def resolve_mode_spec(mode: RetreatLocomotionMode, tuners=None) -> dict:
    """Full locomotion spec for *mode*: defaults merged with tuner overrides.

    A partial ``retreat_mode_profiles`` entry (or a malformed one) can never
    produce a half-built gait — every key is filled from the module defaults
    and every numeric field is coerced, with the default restored on any
    value that will not parse.
    """
    spec = dict(DEFAULT_MODE_SPECS[mode.name])
    overrides = _tuner(tuners, "retreat_mode_profiles", None)
    if isinstance(overrides, dict):
        entry = overrides.get(mode.name)
        if isinstance(entry, dict):
            for key in _SPEC_KEYS:
                if key not in entry:
                    continue
                value = entry[key]
                if key == "facing_policy":
                    if value in (FACE_THREAT, FACE_TRAVEL):
                        spec[key] = value
                    continue
                try:
                    spec[key] = float(value)
                except (TypeError, ValueError):
                    pass
    return spec


def _arrival_taper(debt_m: float, band_m: float, min_scale: float) -> float:
    """Speed multiplier that settles the body onto the line.

    Full command at or beyond the band; scaled linearly with the remaining
    debt inside it, floored at ``min_scale`` so the body never crawls to a
    standstill short of the line.
    """
    if band_m <= 0.0:
        return 1.0
    if debt_m >= band_m:
        return 1.0
    scale = debt_m / band_m
    return max(float(min_scale), min(1.0, scale))


# ─────────────────────────────────────────────────────────────────────
# THE STAMP
# ─────────────────────────────────────────────────────────────────────

def apply_retreat_locomotion(world, d, tuners=None,
                             base_speed: float | None = None,
                             target_x: float | None = None):
    """Issue the debt-aware gait for *d* this frame.  Returns the profile.

    Writes exactly five things on the body:

        ``retreat_locomotion``      the immutable profile Player integrates
        ``retreat_locomotion_mode`` the gait name (audit + dwell clock)
        ``retreat_facing_policy``   the latched backpedal / turn-and-run
                                    verdict (the facing gate's memory)
        ``retreat_debt_m``          the debt the gait was chosen from
        ``move_speed``              the tapered, gait-scaled commanded speed

    The target geometry, the movement state and the ``target_source`` stamp
    stay owned by the retreat writer — this function never touches them, so
    the authority contract is unchanged.

    Returns ``None`` when locomotion is disabled or no debt is measurable;
    callers that need the legacy behaviour get it by disabling the lever.
    """
    if d is None:
        return None
    if not is_locomotion_enabled(tuners):
        clear_retreat_locomotion(d)
        return None

    debt = retreat_debt_m(world, d, target_x=target_x)

    # THE BACKPEDAL THRESHOLD is the small-debt band edge, single-sourced:
    # the canonical tuner, the deprecated alias only when the canonical name
    # is absent, then the module default.  There is no second edge.
    shuffle_max = backpedal_threshold_m(tuners)
    glide_max = _float_tuner(tuners, "retreat_glide_max_debt_m",
                             DEFAULT_GLIDE_MAX_DEBT_M)
    if glide_max < shuffle_max:
        shuffle_max, glide_max = glide_max, shuffle_max
    hysteresis = _float_tuner(tuners, "retreat_mode_hysteresis_m",
                              DEFAULT_MODE_HYSTERESIS_M)
    facing_hysteresis = _float_tuner(tuners,
                                     "retreat_facing_travel_hysteresis_m",
                                     DEFAULT_FACING_TRAVEL_HYSTERESIS_M)
    try:
        dwell = int(_tuner(tuners, "retreat_mode_min_dwell_frames",
                           DEFAULT_MODE_MIN_DWELL_FRAMES))
    except (TypeError, ValueError):
        dwell = DEFAULT_MODE_MIN_DWELL_FRAMES
    frame = int(getattr(world, "frame_count", 0) or 0)

    mode = select_retreat_locomotion_mode(
        d, debt,
        shuffle_max=shuffle_max, glide_max=glide_max,
        hysteresis=hysteresis, min_dwell_frames=dwell, frame=frame,
    )
    spec = resolve_mode_spec(mode, tuners)

    # THE FACING GATE.  The band's facing_policy is a PERMISSION, not the
    # verdict: travel facing is only granted once the debt has cleared the
    # backpedal threshold by the facing hysteresis.
    facing_policy = resolve_facing_policy(
        d, debt, spec["facing_policy"],
        backpedal_max=shuffle_max, hysteresis=facing_hysteresis,
    )
    backpedal_active = bool(debt < shuffle_max) or facing_policy == FACE_THREAT

    ceiling = _float_tuner(tuners, "max_defensive_speed_mps",
                           DEFAULT_MAX_DEFENSIVE_SPEED_MPS)
    if base_speed is None:
        try:
            base = min(float(getattr(d, "max_speed_mps", ceiling) or ceiling),
                       ceiling)
        except (TypeError, ValueError):
            base = ceiling
    else:
        try:
            base = float(base_speed)
        except (TypeError, ValueError):
            base = ceiling

    taper = _arrival_taper(
        debt,
        _float_tuner(tuners, "retreat_arrival_band_m", DEFAULT_ARRIVAL_BAND_M),
        _float_tuner(tuners, "retreat_arrival_min_scale",
                     DEFAULT_ARRIVAL_MIN_SCALE),
    )
    speed = base * spec["speed_scale"] * taper

    profile = RetreatLocomotion(
        mode_name=mode.name,
        debt_m=debt,
        speed_scale=spec["speed_scale"],
        move_speed_mps=speed,
        accel=spec["accel"],
        decel=spec["decel"],
        lateral_priority=spec["lateral_priority"],
        facing_policy=facing_policy,
        facing_rate_deg_s=_float_tuner(
            tuners, "retreat_spin_facing_rate_deg_s",
            DEFAULT_FACING_RATE_DEG_S,
        ),
        no_reverse=bool(debt > 0.0)
        and bool(_tuner(tuners, "retreat_no_reverse", True)),
        backpedal_active=backpedal_active,
    )

    d.retreat_locomotion = profile
    d.retreat_locomotion_mode = mode.name
    d.retreat_debt_m = debt
    d.move_speed = speed
    return profile


def clear_retreat_locomotion(d) -> None:
    """Drop every locomotion field so no stale gait can survive.

    Called by the retreat authority for every body it does NOT retreat, and
    on kickoff reset.  ``retreat_locomotion`` is additionally cleared by
    ``Player`` at the end of each frame, so the profile is frame-scoped by
    construction.  The FACING LATCH goes with it: the backpedal verdict
    belongs to the obligation that produced it, and a body that has
    arrived (or was never retreating) must re-earn its turn-and-run
    permission from a fresh debt.
    """
    if d is None:
        return
    d.retreat_locomotion = None
    d.retreat_locomotion_mode = None
    d.retreat_debt_m = None
    d.retreat_mode_changed_frame = None
    d.retreat_facing_policy = None


def format_locomotion_report(defenders) -> str:
    """Compact one-line rendering of the live gaits (logging only)."""
    rows = []
    for d in (defenders or ()):
        if d is None:
            continue
        mode_name = getattr(d, "retreat_locomotion_mode", None)
        if not mode_name:
            continue
        rows.append(
            "id=%s %s%s debt=%.2f face=%s"
            % (
                getattr(d, "player_id", "?"),
                mode_name,
                " BACKPEDAL"
                if getattr(d, "retreat_facing_policy", None) == FACE_THREAT
                else "",
                float(getattr(d, "retreat_debt_m", 0.0) or 0.0),
                getattr(d, "retreat_facing_policy", "-"),
            )
        )
    if not rows:
        return "retreat_locomotion=idle"
    return "retreat_locomotion=%d [%s]" % (len(rows), ", ".join(rows))
