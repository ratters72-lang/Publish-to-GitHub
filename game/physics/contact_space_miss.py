"""contact_space_miss.py — THE CONTACT-SPACE MISS PATH (CONTACT_SPACE_MISS).

THE QUESTION THIS MODULE ANSWERS
--------------------------------
``OpenPlayEngine._check_pre_tackle`` grades a defender's APPROACH — the angle of
its heading to the carrier (60% weight) and its relative speed (40%) — and
returns one of ``MISS`` / ``ARM_GRAB`` / ``GLANCING`` / ``CONTACT``.  It reads
the separation between the bodies but NEVER puts it in the score, so a defender
0.5 m from the carrier (surfaces overlapping) can be graded exactly like one
1.5 m away.

``probe_miss_contact_space.py`` (``project_info__87.md``) measured the
consequence: a minority of ``MISS`` resolutions occur while the two bodies are
INSIDE the collision radius (``r_defender + r_carrier`` ~ 1.10 m at the default
0.55 m body radius).  Those are genuine body-to-body contacts, yet ``MISS``
currently mutates NOTHING, so the collision resolves as a non-event.

WHAT THIS MODULE DOES
---------------------
It names that subset — a ``MISS`` whose separation is ``<=`` the combined body
radius is a ``CONTACT_SPACE_MISS`` — and resolves it as a COLLISION THAT DID
NOT STICK: the carrier and the tackler are driven APART along the contact
normal, in proportion to how hard they were closing.

WHY THE REBOUND IS A SCRIPTED ARC, NOT A VELOCITY IMPULSE
---------------------------------------------------------
The obvious implementation — hand the bodies a separation impulse and let the
engine carry them apart — DOES NOT WORK in this engine, and the measurement is
what settled it:

  ``player/player.py::_accelerate_toward_target`` re-damps velocity every frame
  toward the AI's commanded velocity at ``factor = accel * dt * 60`` (accel
  0.70, dt 1/60 -> factor 0.70), and then hard-clamps the speed to
  ``move_speed``.  A 10 m/s impulse therefore decays 10 -> 3.0 -> 0.9 -> 0.27
  and is gone in about three frames, having moved the body
  ~10 * (1/60) * (1 + 0.3 + 0.09) ~ 0.12 m.  Invisible.

So the rebound is AUTHORED: each body receives a short, deterministic,
BOUNDED ballistic arc (``Rebound``) whose direction is the contact normal and
whose TOTAL LENGTH is proportional to the closing speed at impact.  While the
arc is live the body's AI seeking is suspended (see ``Player.update``), so the
displacement is the arc's and nothing else fights it.

  * the CARRIER is deflected AWAY from the tackler and LOSES SPEED — a
    shoulder-to-shoulder contact that does not stick takes something off the
    ball carrier;
  * the DEFENDER is thrown the other way, harder (the beaten body travels
    further), and is briefly staggered so it cannot instantly re-chase.

MAGNITUDE IS A FUNCTION OF THE IMPACT, NOT A CONSTANT
-----------------------------------------------------
The impact magnitude is the RELATIVE SPEED of the two bodies along the contact
normal, not its projection sign:

    speed  = max(hypot(def.v - car.v), CONTACT_IMPACT_FLOOR_MPS)
    travel = TRAVEL_MAX * clamp(speed * (1 + restitution)
                                / CLOSING_SPEED_REF_MPS, 0, 1)

so a light brush barely moves anyone and a genuine collision reaches the cap
(defender 1.5 m, carrier 0.9 m).  The cap is a hard ceiling on the arc's total
length — never a per-frame step — so the bound holds however fast the contact.

The SIGNED closing speed alone was the first implementation and it was WRONG:
``max(0.0, v_close)`` scores zero when the carrier is running PAST a stationary
defender, so a reaction would report itself ``applied`` while arming no arc.
Two bodies already inside the combined radius are in contact whatever the sign,
so the size is the relative motion, and it always carries the floor.

DETERMINISM
-----------
Every value is a pure function of the two bodies' current x / y / vx / vy /
facing / radius.  There is no ``random``, no set or dict iteration order, and no
frame-counter dependence beyond the per-frame idempotence guard (which makes a
body updated twice in one frame step once).  The reaction normal falls back to
the attack axis on a degenerate (coincident) separation.

BOUNDS
------
The arc is split over a fixed number of frames as an arithmetic series whose
sum is EXACTLY the declared travel, so the total is capped by construction and
eased out (largest step first).  Each step is applied inside ``Player.update``
immediately before ``_clamp_position``, so the field bound still holds.  The
carrier's speed loss is capped.  The defender's stagger is a short
``freeze_timer`` (the same mechanism ``pretackle_machine.apply_outcome`` uses).

READ-PURITY OF THE PREDICATE
----------------------------
``is_contact_space_miss`` / ``separation`` / ``combined_body_radius`` write to
NOTHING — they are safe to call at any time, including from a caller that only
wants to classify a miss.

IMPORT SAFETY
-------------
Module-scope imports are stdlib (``dataclasses``, ``math``, ``os``) plus the
field bounds from ``player.player`` — the same base-layer import
``game.physics.collision_system`` already makes, so this module closes no cycle
and can be reached from ``contact_authority`` without a top-level project
reach.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

from player.player import FIELD_MIN_X, FIELD_MAX_X, FIELD_MIN_Y, FIELD_MAX_Y

__all__ = [
    "CONTACT_SPACE_MISS",
    "CONTACT_SPACE_MISS_ENV_VAR",
    "DEFAULT_BODY_RADIUS_M",
    "SEPARATION_EPS_M",
    # tunables
    "REBOUND_WINDOW_S",
    "REBOUND_FRAMES",
    "REBOUND_RESTITUTION",
    "CLOSING_SPEED_REF_MPS",
    "CONTACT_IMPACT_FLOOR_MPS",
    "DEFENDER_TRAVEL_MAX_M",
    "CARRIER_TRAVEL_MAX_M",
    "CARRIER_SPEED_LOSS_MAX",
    "DEFENDER_STAGGER_S",
    "VELOCITY_DELTA_MAX_MPS",
    "DT_FRAME_S",
    # api
    "contact_space_miss_enabled",
    "body_radius",
    "combined_body_radius",
    "separation",
    "is_contact_space_miss",
    "apply_contact_space_miss",
    "ContactSpaceMissResult",
    "Rebound",
]

# ── THE PATH LABEL ───────────────────────────────────────────────────
# The name of the new resolution: a MISS whose bodies were inside the
# combined body radius.  Stamped on the result and carried on the contact
# decision so the event is auditable.
CONTACT_SPACE_MISS: str = "CONTACT_SPACE_MISS"

# ── GATE ─────────────────────────────────────────────────────────────
# Default ON.  ``RLM_CONTACT_SPACE_MISS=0`` disarms the path and restores the
# pre-change engine (a MISS then mutates nothing, exactly as before) without a
# code revert — the A/B lever the surrounding code uses for every recent stage.
CONTACT_SPACE_MISS_ENV_VAR: str = "RLM_CONTACT_SPACE_MISS"

# ── GEOMETRY ─────────────────────────────────────────────────────────
# Mirror of ``player.collision_body.CollisionBody.RADIUS_M`` (the fallback when
# a body carries no explicit radius).
DEFAULT_BODY_RADIUS_M: float = 0.55

# Below this separation the defender->carrier direction is undefined and the
# declared fallback (the attack axis) is used.
SEPARATION_EPS_M: float = 1e-9

# The engine's fixed simulation step (the arc is sized in frames at 60 Hz).
DT_FRAME_S: float = 1.0 / 60.0

# ── THE REBOUND (provisional — tune separately) ──────────────────────
# The arc lasts this long.  0.45 s is ~27 frames: long enough to read as a
# knock-back, short enough that the body returns to play promptly.
REBOUND_WINDOW_S: float = 0.45
REBOUND_FRAMES: int = max(1, int(round(REBOUND_WINDOW_S / DT_FRAME_S)))

# How much of the impact is returned (the collision system's own restitution
# convention).  It scales the CLOSING SPEED that sets the arc length.
REBOUND_RESTITUTION: float = 0.6

# The closing speed (m/s) at which a body reaches its travel cap.  At or above
# this the contact is a genuine full-force collision.
CLOSING_SPEED_REF_MPS: float = 8.0

# THE FLOOR UNDER THE MEASURED IMPACT (m/s).  A CONTACT_SPACE_MISS is by
# definition a pair whose surfaces already intersect, so the reaction must
# always RESOLVE that overlap.  Taking the magnitude of the relative velocity
# alone could still be zero for two momentarily co-moving bodies, which would
# report a reaction that moves nobody; the floor guarantees a bounded,
# non-zero separation whenever the predicate fires.
CONTACT_IMPACT_FLOOR_MPS: float = 0.75

# HARD CEILING on the arc's TOTAL length, per body.  The beaten defender is
# thrown further than the carrier that beat it.
DEFENDER_TRAVEL_MAX_M: float = 1.5
CARRIER_TRAVEL_MAX_M: float = 0.9

# The carrier of a tackle that did not stick loses up to this fraction of its
# speed, scaled by how hard the contact was.
CARRIER_SPEED_LOSS_MAX: float = 0.25

# The beaten defender is staggered after the arc lands.
DEFENDER_STAGGER_S: float = 0.35

# Hard cap on any single reported velocity delta this module declares.
VELOCITY_DELTA_MAX_MPS: float = 8.0


# ── THE BALLISTIC ARC ────────────────────────────────────────────────

@dataclass
class Rebound:
    """One body's short, deterministic knock-back arc.

    The arc is an arithmetic series over ``frames`` frames whose weights decay
    linearly (largest step first), so the steps SUM EXACTLY to ``total_m``:
    ``step_i = total_m * remaining / (N(N+1)/2)``.  That makes the declared
    cap a true ceiling on the whole arc rather than on one frame, and gives the
    ease-out that reads as a body being thrown and settling.

    ``step`` is idempotent within a frame: the engine may call ``Player.update``
    more than once per frame for the same body (the defence pass plus the
    retreat pass), and a double step would double the travel.
    """

    dir_x: float
    dir_y: float
    total_m: float
    frames: int = REBOUND_FRAMES
    vx: float = field(default=0.0, init=False)
    vy: float = field(default=0.0, init=False)
    travelled_m: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        self.frames = max(1, int(self.frames))
        # Sum of the weight series 1 + 2 + ... + N.
        self._series_sum: int = self.frames * (self.frames + 1) // 2
        self._remaining: int = self.frames
        self._last_frame: int = -1

    @property
    def done(self) -> bool:
        """True when the arc has no frames left."""
        return self._remaining <= 0

    def step(self, dt: float, frame):
        """Advance one frame; return ``(dx, dy)`` for this frame.

        Returns ``(0.0, 0.0)`` when the arc is finished or when this body has
        already been stepped on *frame* (the idempotence guard).

        *frame* may be ``None`` when no frame identity is available (a caller
        with no world attached).  The guard is then skipped and the arc always
        advances: the arc must still complete, so a missing identity degrades
        to no guard rather than to a permanent stall.
        """
        if self._remaining <= 0:
            return 0.0, 0.0
        if frame is not None:
            if frame == self._last_frame:
                return 0.0, 0.0
            self._last_frame = frame
        # Linear decay: the largest step first, the smallest last.
        length = self.total_m * self._remaining / float(self._series_sum)
        self._remaining -= 1
        self.travelled_m += length
        dx = self.dir_x * length
        dy = self.dir_y * length
        # The instantaneous speed this frame represents, reported so facing and
        # the momentum readout follow the throw instead of the AI's command.
        speed = (length / dt) if dt > 0.0 else 0.0
        self.vx = self.dir_x * speed
        self.vy = self.dir_y * speed
        return dx, dy

    def first_step_length(self) -> float:
        """The length of the first frame's step (for audit/verification)."""
        return self.total_m * self.frames / float(self._series_sum)


# ── THE RESULT (what was applied, for audit + verification) ───────────

@dataclass(frozen=True)
class ContactSpaceMissResult:
    """The declared outcome of one CONTACT_SPACE_MISS resolution.

    ``applied`` is False (and every delta zero) whenever the pair was NOT in
    contact space, so a caller can always tell whether a body was written.

    ``defender_dx`` / ``carrier_dx`` / ``defender_dvx`` / ``carrier_dvx`` report
    the FIRST frame of each body's arc — the instantaneous signature of the
    throw.  ``defender_travel_m`` / ``carrier_travel_m`` are the arc TOTALS.
    """

    path: str
    applied: bool
    separation_m: float
    combined_radius_m: float
    normal_x: float
    normal_y: float
    closing_mps: float
    defender_travel_m: float
    carrier_travel_m: float
    carrier_speed_loss: float
    carrier_dx: float
    carrier_dy: float
    defender_dx: float
    defender_dy: float
    carrier_dvx: float
    carrier_dvy: float
    defender_dvx: float
    defender_dvy: float
    defender_stagger_s: float


def _not_applied(separation_m: float, combined_radius_m: float
                 ) -> ContactSpaceMissResult:
    """The declared no-op result (nothing was written)."""
    return ContactSpaceMissResult(
        path=CONTACT_SPACE_MISS,
        applied=False,
        separation_m=separation_m,
        combined_radius_m=combined_radius_m,
        normal_x=0.0, normal_y=0.0,
        closing_mps=0.0,
        defender_travel_m=0.0, carrier_travel_m=0.0,
        carrier_speed_loss=0.0,
        carrier_dx=0.0, carrier_dy=0.0,
        defender_dx=0.0, defender_dy=0.0,
        carrier_dvx=0.0, carrier_dvy=0.0,
        defender_dvx=0.0, defender_dvy=0.0,
        defender_stagger_s=0.0,
    )


# ─────────────────────────────────────────────────────────────────────
# GATE
# ─────────────────────────────────────────────────────────────────────

def contact_space_miss_enabled(tuners=None) -> bool:
    """True when the CONTACT_SPACE_MISS path is armed.

    A ``tuners`` object carrying ``contact_space_miss_enabled`` overrides the
    environment; otherwise ``RLM_CONTACT_SPACE_MISS`` decides (default ON).
    Disarming restores the pre-change engine exactly.
    """
    if tuners is not None and hasattr(tuners, "contact_space_miss_enabled"):
        return bool(getattr(tuners, "contact_space_miss_enabled"))
    value = os.environ.get(CONTACT_SPACE_MISS_ENV_VAR, "1")
    return str(value).strip() not in ("", "0")


# ─────────────────────────────────────────────────────────────────────
# PREDICATES — pure reads
# ─────────────────────────────────────────────────────────────────────

def body_radius(body) -> float:
    """A body's collision radius, with the declared fallback.

    Accepts a Player (reads ``body.radius``) or a CollisionBody (reads
    ``radius``).  Never raises; a non-finite or non-positive radius falls back
    to ``DEFAULT_BODY_RADIUS_M``.
    """
    direct = getattr(body, "radius", None)
    if direct is None:
        direct = getattr(getattr(body, "body", None), "radius", None)
    try:
        r = float(direct) if direct is not None else DEFAULT_BODY_RADIUS_M
    except (TypeError, ValueError):
        r = DEFAULT_BODY_RADIUS_M
    if not math.isfinite(r) or r <= 0.0:
        r = DEFAULT_BODY_RADIUS_M
    return r


def combined_body_radius(carrier, defender) -> float:
    """The contact-space threshold: ``r_carrier + r_defender``."""
    return body_radius(carrier) + body_radius(defender)


def separation(defender, carrier) -> float:
    """Centre-to-centre distance between *defender* and *carrier*."""
    dx = float(getattr(carrier, "x", 0.0) or 0.0) \
        - float(getattr(defender, "x", 0.0) or 0.0)
    dy = float(getattr(carrier, "y", 0.0) or 0.0) \
        - float(getattr(defender, "y", 0.0) or 0.0)
    return math.hypot(dx, dy)


def is_contact_space_miss(defender, carrier) -> bool:
    """True when the two bodies' surfaces are touching or overlapping.

    THE predicate that separates a CONTACT_SPACE_MISS from an ordinary miss:
    ``separation <= r_carrier + r_defender``.  A pure read; writes nothing.
    """
    if defender is None or carrier is None:
        return False
    return separation(defender, carrier) <= combined_body_radius(
        carrier, defender
    )


def _clamp(v: float, lo: float, hi: float) -> float:
    """Clamp *v* into ``[lo, hi]``."""
    return lo if v < lo else (hi if v > hi else v)


def _clamp01(v: float) -> float:
    """Clamp *v* into ``[0, 1]``."""
    return _clamp(v, 0.0, 1.0)


def _velocity(body, axis: str) -> float:
    """A body's velocity on *axis* ("vx"/"vy"), tolerating a missing value."""
    try:
        return float(getattr(body, axis, 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def closing_speed(defender, carrier, nx: float, ny: float) -> float:
    """The relative closing speed along the contact normal (m/s).

    ``n`` points from the defender to the carrier, so a defender driving in
    (+n) and a carrier driving in (-n) both increase the value.  A separating
    pair returns a negative number.
    """
    return ((_velocity(defender, "vx") - _velocity(carrier, "vx")) * nx
            + (_velocity(defender, "vy") - _velocity(carrier, "vy")) * ny)


def _heading_unit(body) -> tuple:
    """A body's direction of travel (velocity), else its facing."""
    vx = _velocity(body, "vx")
    vy = _velocity(body, "vy")
    spd = math.hypot(vx, vy)
    if spd > 1e-6:
        return vx / spd, vy / spd
    facing = float(getattr(body, "facing", 0.0) or 0.0)
    return math.cos(facing), math.sin(facing)


def _sync_body(body) -> None:
    """Re-sync a body's CollisionBody mirror after a position write."""
    mirror = getattr(body, "body", None)
    if mirror is not None:
        try:
            mirror.sync()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────
# THE REACTION — a deterministic, bounded rebound
# ─────────────────────────────────────────────────────────────────────

def apply_contact_space_miss(world, carrier, defender, tuners=None
                             ) -> ContactSpaceMissResult:
    """Resolve a CONTACT_SPACE_MISS as a collision that did NOT stick.

    Attaches a bounded ballistic arc (:class:`Rebound`) to BOTH bodies, scaled
    by the relative speed of the two bodies along the contact normal (floored,
    so an in-contact pair is always separated), and takes a capped slice of the
    carrier's speed.  ``Player.update`` consumes and advances the arcs.

    When the pair is NOT in contact space the result is a declared no-op:
    ``applied`` is False and NOTHING on either body is touched, so the caller's
    existing miss handling proceeds byte-for-byte as before.

    DETERMINISTIC: no randomness, no ordering dependence.  BOUNDED: each arc's
    total is capped by construction (arithmetic series summing to the declared
    travel) and the carrier's speed loss is capped.
    """
    if carrier is None or defender is None:
        return _not_applied(0.0, 0.0)

    dist = separation(defender, carrier)
    combined = combined_body_radius(carrier, defender)
    if dist > combined:
        # Ordinary miss — write nothing.
        return _not_applied(dist, combined)

    # ── the collision normal: defender -> carrier ──
    dx = float(getattr(carrier, "x", 0.0) or 0.0) \
        - float(getattr(defender, "x", 0.0) or 0.0)
    dy = float(getattr(carrier, "y", 0.0) or 0.0) \
        - float(getattr(defender, "y", 0.0) or 0.0)
    if dist > SEPARATION_EPS_M:
        nx, ny = dx / dist, dy / dist
    else:
        # Degenerate (coincident) separation: the declared fallback direction
        # is the attack axis, so the reaction is well defined, not arbitrary.
        ad = float(getattr(world, "attacking_direction", 1) or 1)
        nx, ny = (1.0, 0.0) if ad >= 0 else (-1.0, 0.0)

    # ── how hard was the contact? ──
    # The same closing-speed measure the collision system uses, scaled by the
    # declared restitution.  It sets BOTH the arc lengths and the carrier's
    # speed loss, so a light brush barely moves anyone.
    # The normal gives the reaction its DIRECTION; the SIZE is the relative
    # speed of the two bodies, floored by CONTACT_IMPACT_FLOOR_MPS.
    #
    # NOT the signed closing speed.  Scaling by ``max(0.0, v_close)`` made the
    # magnitude depend on which way the carrier happened to be travelling ALONG
    # THE NORMAL: a carrier running PAST a stationary defender scored
    # ``max(0.0, -5.0) = 0``, so the reaction reported itself ``applied`` while
    # arming no arc and moving nobody.  Two bodies already inside the combined
    # radius are in contact whatever the sign, so the impact is the relative
    # MOTION and it always carries the floor.
    rel_vx = _velocity(defender, "vx") - _velocity(carrier, "vx")
    rel_vy = _velocity(defender, "vy") - _velocity(carrier, "vy")
    v_close = closing_speed(defender, carrier, nx, ny)
    impact = max(math.hypot(rel_vx, rel_vy), CONTACT_IMPACT_FLOOR_MPS) \
        * (1.0 + REBOUND_RESTITUTION)
    fraction = _clamp01(impact / CLOSING_SPEED_REF_MPS)

    def_travel = DEFENDER_TRAVEL_MAX_M * fraction
    car_travel = CARRIER_TRAVEL_MAX_M * fraction

    # ── the arcs: they separate, so the directions are opposed ──
    # Defender is thrown away from the carrier (-n); the carrier is deflected
    # away from the defender (+n).
    def_arc = None
    car_arc = None
    if def_travel > 1e-9:
        def_arc = Rebound(dir_x=-nx, dir_y=-ny, total_m=def_travel)
        defender._csm_rebound = def_arc
    if car_travel > 1e-9:
        car_arc = Rebound(dir_x=nx, dir_y=ny, total_m=car_travel)
        carrier._csm_rebound = car_arc

    # ── the carrier pays for the contact ──
    # A tackle that did not stick still takes something off the ball carrier.
    loss = CARRIER_SPEED_LOSS_MAX * fraction
    cvx = _velocity(carrier, "vx")
    cvy = _velocity(carrier, "vy")
    scale = _clamp01(1.0 - loss)
    carrier.vx = cvx * scale
    carrier.vy = cvy * scale
    _sync_body(carrier)

    # ── the defender is staggered once the arc lands ──
    stagger = 0.0
    if hasattr(defender, "freeze_timer"):
        stagger = DEFENDER_STAGGER_S
        try:
            existing = float(getattr(defender, "freeze_timer", 0.0) or 0.0)
        except (TypeError, ValueError):
            existing = 0.0
        defender.freeze_timer = max(existing, stagger)

    # ── report (first frame of each arc + the totals) ──
    def_step = def_arc.first_step_length() if def_arc is not None else 0.0
    car_step = car_arc.first_step_length() if car_arc is not None else 0.0
    def_dvx, def_dvy = (
        (-nx * def_step / DT_FRAME_S, -ny * def_step / DT_FRAME_S)
        if def_step > 0.0 else (0.0, 0.0)
    )
    return ContactSpaceMissResult(
        path=CONTACT_SPACE_MISS,
        applied=True,
        separation_m=dist,
        combined_radius_m=combined,
        normal_x=nx, normal_y=ny,
        closing_mps=v_close,
        defender_travel_m=def_travel,
        carrier_travel_m=car_travel,
        carrier_speed_loss=loss,
        carrier_dx=nx * car_step, carrier_dy=ny * car_step,
        defender_dx=-nx * def_step, defender_dy=-ny * def_step,
        carrier_dvx=carrier.vx - cvx, carrier_dvy=carrier.vy - cvy,
        defender_dvx=def_dvx, defender_dvy=def_dvy,
        defender_stagger_s=stagger,
    )
