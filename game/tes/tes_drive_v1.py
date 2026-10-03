"""tes_drive_v1.py — TES DRIVE v1: THE FRAME-BY-FRAME CONTEST EVOLUTION
(Stage 3, 2026-09-21).

THE DECLARED OWNERSHIP BOUNDARY
-------------------------------
    OPEN PLAY          free movement, line geometry, spacing, width, retreat.
                       exit: a body is GRANTED tackle candidacy.

    CONTACT AUTHORITY  contest FORMATION: who may engage, when a contest
                       exists, at what geometry.

    TES                contest EVOLUTION: growth (Stage 2) and DRIVE
                       (Stage 3 — this module).          <-- OWNS THIS OBJECT.

    REFEREE            contest TERMINATION.               (not this stage)

    PTB                contest RESTART / next possession. (not this stage)

Sentence form:

    Open Play owns movement.  Contact Authority owns contest formation.
    TES owns contest evolution.  Referee owns termination.  PTB owns restart.

Stage 3 adds exactly one clause to that sentence.  It does not move an
ownership boundary; it gives the EVOLUTION leg a clock:

    TES owns contest evolution -- the persistent contest's GROWTH and its
    frame-by-frame DRIVE.

WHAT STAGE 3 IS
---------------
Stage 2 declared the persistent ``TackleState``: the object Contact Authority
creates at the moment of a declaration, hands to TES, and the engine closes at
a declared lifecycle boundary.  That object captures the INITIAL CONDITIONS of
a contest and then grows by declared attachment.  What it does not do is EVOLVE
on a clock: nothing in it is updated once per simulation cycle.

TES Drive v1 is that clock.  It is a NEW object, DERIVED FROM the Stage 2
TackleState, that is advanced exactly once per simulation cycle while a contest
is active.  Once per cycle it re-reads the contest's relative movement, its
momentum, its control/leverage balance, the carrier's forward progress from the
declared origin, and the contest's attachment state, and it relaxes its own
declared live fields toward what it measured.

IT CONSUMES; IT NEVER CREATES OR REPLACES
-----------------------------------------
This module does not form contests and cannot form one.  It reads the live
contest through the Stage 2 module's own public API — ``active_tackle_state`` —
and it never calls ``form_tackle_state``.  A cycle in which no contest is active
creates nothing and advances nothing.

It also never writes to the TackleState.  Carrier and defender identity, the
contact geometry, the frozen formation kinematics, the forward-progress origin,
the contact timestamp, the attack direction, the attachment list and the
ownership provenance are the CONTEST's facts, owned by Stage 2; Stage 3 reads
them and mirrors the ones it needs.  The TackleState is the immutable
historical evidence of what the contact was, and this stage cannot edit it.

WHAT STAGE 3 DELIBERATELY DOES NOT DO
-------------------------------------
A state remains an ACTIVE CONTEST throughout Stage 3 regardless of whether
control is strengthening or weakening.  Forming a drive:

  * does NOT decide or emit a terminal tackle result;
  * does NOT decide a tackle bust, a break, a grounding or a collapse;
  * does NOT award dominance to either party;
  * does NOT take a referee decision of any kind;
  * does NOT run or prepare a play-the-ball;
  * does NOT increment or read a tackle count;
  * does NOT return the engine to Open Play;
  * does NOT move, freeze, thaw or reposition ANY body.

Those are the next stage's questions.  Stage 3 drives a live contest; it never
finishes one.

THE EVOLUTION MODEL — A PER-CYCLE MAP, NOT AN INTEGRATOR
--------------------------------------------------------
``advance_tes_drive_v1`` applies a DECLARED, deterministic map once per cycle.
``dt`` is recorded on the drive for provenance and is never multiplied into a
field, so the same inputs produce byte-identical fields whether a cycle took
8 ms or 33 ms.  The model is pure arithmetic: no random number source, no wall
clock, no environment variable, no unordered iteration.

The two load-bearing properties are BOUNDEDNESS and SMOOTHNESS:

  * ``control`` is bounded in [0, 1] and ``leverage == 1.0 - control`` exactly,
    so the two always account for the whole contest;
  * ``control`` may move at most ``MAX_CONTROL_STEP_PER_CYCLE`` per cycle, so a
    contest cannot SNAP between "the carrier is winning" and "the defence is
    winning" — it relaxes.  This is what makes a control trend a trend.

``control`` is SEEDED, at creation, by the same map applied to the FROZEN
formation momentum the Stage 2 contest captured (``carrier_momentum_kgmps``
versus ``defender_momentum_kgmps``), so the first cycle already evolves from
the contest's own initial conditions rather than from a blank slate.

EXACTLY-ONCE PER CYCLE
----------------------
``world.frame_count`` is bumped once at the top of the engine's own
``update()``, so it IS the simulation cycle key.  The update is IDEMPOTENT PER
FRAME: a second call in the same cycle finds ``last_update_frame`` equal to the
current frame and returns the record unchanged.  That is what makes "exactly
one update per cycle" a measurable property of the record rather than a promise
about the caller.

THE LATCH
---------
``world.tes_drive_v1`` is the ONE published attribute and the LATCH.  It is
published when a drive is created for a contest and already carrying a
different contest is REPLACED, so at most one drive is ever live; the earlier
record stays on the bounded module ring as history and can never be advanced
again.  When the update runs and no contest is active, the latch is DROPPED —
the drive does not close a contest (the engine's own lifecycle boundary already
did that); it observes that there is no contest to drive.

READ PURITY OF THIS MODULE
--------------------------
Every attribute store in this module targets the ``TESDriveV1`` record it owns.
The only other world change is the single ``setattr`` / ``delattr`` of the
declared latch.  No body, no engine subsystem, no Tuner and no other world
attribute is ever written, so advancing a drive cannot perturb a frame.

IMPORT SAFETY
-------------
This module is a NEAR-LEAF.  Its imports are stdlib (``dataclasses``, ``enum``,
``math``, ``typing``) plus EXACTLY ONE project import — ``game.tes.tackle_state``
— which is the object this stage is declared to consume.  That module is itself
a pure leaf (stdlib only), so the edge is acyclic at every load order; and the
engine reaches THIS module function-locally, so Stage 3 stays out of the
engine's import graph exactly as Stage 2 does.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from game.tes.tackle_state import active_tackle_state

__all__ = [
    "TES_DRIVE_ATTR",
    "TES_DRIVE_ID_PREFIX",
    "TES_DRIVE_RING_LIMIT",
    "OWNER_TES",
    "CONTROL_NEUTRAL",
    "MIN_CONTROL",
    "MAX_CONTROL",
    "MAX_CONTROL_STEP_PER_CYCLE",
    "LEVERAGE_SCALE_KGMPS",
    "MAX_BACKWARD_M",
    "MAX_FORWARD_M",
    "DEFAULT_MASS_KG",
    "SEPARATION_EPS_M",
    "TREND_EPS",
    "ControlTrend",
    "TESDriveV1",
    "tes_drive_id_for",
    "target_control_for",
    "trend_for",
    "seed_control_for",
    "measure_tes_drive_v1",
    "create_tes_drive_v1",
    "advance_tes_drive_v1",
    "published_tes_drive_v1",
    "active_tes_drive_v1",
    "recent_tes_drives_v1",
    "reset_tes_drives_v1",
    "format_tes_drive_v1_report",
    "declared_evolution",
]


# ─────────────────────────────────────────────────────────────────────
# THE PUBLISHED SURFACE — the one attribute that IS the latch
# ─────────────────────────────────────────────────────────────────────
# Written with ``setattr`` (and dropped with ``delattr``) on the WORLD only.
# It is the live drive: the object the engine advances, the object a probe
# reads, and the object a future resolution stage will read instead of
# reconstructing what the contest was doing.
TES_DRIVE_ATTR: str = "tes_drive_v1"

# The identity scheme.  A drive's identity is derived from the CONTEST it
# drives — never from a measurement this stage took — so a drive and the
# TackleState it evolves can always be lined up without a second key.
TES_DRIVE_ID_PREFIX: str = "drive"

# The bounded in-memory ring of every drive this process has latched.  It
# exists so a probe can see the whole sequence of drives (a superseded drive
# is still evidence of what the contest was doing) and so "at most one LIVE
# drive" is a property of the world latch rather than a claim about the ring.
# Nothing in the live engine reads it.
TES_DRIVE_RING_LIMIT: int = 600

# ─────────────────────────────────────────────────────────────────────
# OWNERSHIP — the one owner a drive ever has
# ─────────────────────────────────────────────────────────────────────
# A drive is created BY TES and advanced BY TES.  There is no handover: the
# TackleState's own ownership provenance (contact_authority -> tes) is the
# contest's history and is READ here, never rewritten.
OWNER_TES: str = "tes"

# ─────────────────────────────────────────────────────────────────────
# THE MEASUREMENT AND BOUND CONSTANTS
# ─────────────────────────────────────────────────────────────────────
# The control axis is a BOUNDED ratio, so the two labels always partition the
# contest exactly: leverage == 1 - control.
MIN_CONTROL: float = 0.0
MAX_CONTROL: float = 1.0
# A contest opens NEUTRAL.  Nothing has happened yet at the declaration, so
# claiming an advantage on cycle zero would be a claim the geometry has not
# made.
CONTROL_NEUTRAL: float = 0.5

# The per-cycle Lipschitz bound on the control axis.  A cycle may move control
# toward its measured target by AT MOST this much, so a contest relaxes
# between "the carrier is winning" and "the defence is winning" instead of
# snapping.  This is the declared SMOOTHNESS rule.
MAX_CONTROL_STEP_PER_CYCLE: float = 0.10

# The momentum scale at which the control axis saturates.  A momentum balance
# of +/- this many kg*m/s maps to a target control of ~0.88 / ~0.12 through the
# declared squash; larger balances saturate smoothly toward 0 / 1 rather than
# clamping.
LEVERAGE_SCALE_KGMPS: float = 300.0

# The declared envelope the recorded forward progress is bounded to.  The
# backward bound mirrors the engine's own per-tackle backward cap (the value
# the drive phase already enforces on the carrier), so the drive can never
# report more backward travel than the engine permits, and the forward bound
# is the declared maximum a single contest can be recorded as having advanced.
MAX_BACKWARD_M: float = 2.0
MAX_FORWARD_M: float = 5.0

# Fallback body mass, mirroring ``game/tes/tackle_state.py`` and
# ``game/tes/tes.py`` so a massless body is measured on the same basis
# everywhere in the TES domain.
DEFAULT_MASS_KG: float = 90.0

# Below this carrier-to-defender separation the closing axis is undefined, so
# the declared fallback direction is the attack axis — the same degenerate
# rule the Stage 2 contact measurement uses.
SEPARATION_EPS_M: float = 0.01

# Below this control delta the trend is HOLDING rather than a movement.  It is
# a descriptive threshold for a LABEL, never a predicate: nothing in this
# module or the engine takes a decision from it.
TREND_EPS: float = 1e-9

# The module-level ring (probe/audit only — never consumed by behaviour).
_DRIVE_RING: list = []


# ─────────────────────────────────────────────────────────────────────
# THE CLOSED TREND VOCABULARY
# ─────────────────────────────────────────────────────────────────────

class ControlTrend(Enum):
    """WHICH WAY the control axis moved this cycle — a label, not a verdict.

    STRENGTHENING  control rose: the carrier's share of the contest grew.
    HOLDING        control did not move this cycle.
    WEAKENING      control fell: the defence's share of the contest grew.

    A contest is EQUALLY ACTIVE on all three.  This vocabulary exists so
    "what is happening to the contest" is a closed set of words rather than
    prose, and nothing anywhere takes a decision from it.
    """

    STRENGTHENING = "strengthening"
    HOLDING = "holding"
    WEAKENING = "weakening"


# ─────────────────────────────────────────────────────────────────────
# THE DRIVE — the declared live/evolving record
# ─────────────────────────────────────────────────────────────────────

@dataclass
class TESDriveV1:
    """THE frame-by-frame evolution of one formed contest.

    Created ONCE per contest by ``create_tes_drive_v1`` and advanced by
    ``advance_tes_drive_v1`` exactly once per simulation cycle until the
    contest stops being active.  The record is MUTABLE because TES owns its
    evolution; the IDENTITY/PROVENANCE block is written once at creation and
    is never rewritten, so a drive can always be traced back to the contest
    and to the frame the contest formed on.

    The record is a CONSUMER of the Stage 2 ``TackleState``: every value in
    the measurement blocks below was read from the contest or from a body the
    contest names.  Nothing in this record is ever written back to it.
    """

    # ── identity and provenance (written once, never rewritten) ──
    drive_id: str
    tackle_id: str
    carrier_id: int
    primary_defender_id: int
    origin_frame: int
    origin_time_s: float
    entry_frame: int
    owner: str = OWNER_TES

    # ── the forward-progress block (from the declared Stage 2 origin) ──
    # ``forward_progress_raw_m`` is the reading BEFORE the declared envelope,
    # kept beside the bounded value so the envelope is auditable rather than
    # invisible.  ``forward_best_m`` / ``forward_worst_m`` are the MONOTONE
    # envelope of everything the drive has seen.
    forward_progress_m: float = 0.0
    forward_progress_raw_m: float = 0.0
    forward_best_m: float = 0.0
    forward_worst_m: float = 0.0

    # ── the relative-movement block ──
    relative_vx: float = 0.0
    relative_vy: float = 0.0
    relative_speed_mps: float = 0.0
    separation_m: float = 0.0
    closing_speed_mps: float = 0.0

    # ── the momentum block ──
    carrier_speed_mps: float = 0.0
    defender_speed_mps: float = 0.0
    carrier_momentum_kgmps: float = 0.0
    defender_momentum_kgmps: float = 0.0
    momentum_balance_kgmps: float = 0.0

    # ── the control / leverage block ──
    control: float = CONTROL_NEUTRAL
    leverage: float = CONTROL_NEUTRAL
    control_delta: float = 0.0
    control_trend: ControlTrend = ControlTrend.HOLDING

    # ── the attachment block (a MIRROR of the contest's contact set) ──
    attachment_count: int = 0
    attached_count: int = 0
    contact_set_size: int = 1
    new_attachments: int = 0

    # ── the cycle bookkeeping block ──
    frames_active: int = 0
    update_count: int = 0
    last_update_frame: int | None = None
    last_update_time_s: float = 0.0
    cycle_dt_s: float = 0.0

    # ── derived readers ──

    @property
    def trend(self) -> str:
        """The control trend as its declared string value."""
        return self.control_trend.value

    @property
    def owned_by_tes(self) -> bool:
        """True for the one owner a drive ever has."""
        return self.owner == OWNER_TES

    @property
    def updated(self) -> bool:
        """True once the drive has been advanced at least one cycle."""
        return self.update_count > 0

    def control_partition_holds(self) -> bool:
        """True when control and leverage exactly account for the contest.

        The declared invariant: ``control + leverage == 1.0`` and each is in
        [MIN_CONTROL, MAX_CONTROL].  A pure read of the record.
        """
        return (
            MIN_CONTROL <= self.control <= MAX_CONTROL
            and MIN_CONTROL <= self.leverage <= MAX_CONTROL
            and abs((self.control + self.leverage) - 1.0) < 1e-9
        )
# ─────────────────────────────────────────────────────────────────────
# SMALL READS (never raise)
# ─────────────────────────────────────────────────────────────────────

def _frame_of(world) -> int:
    """The world's frame counter — the simulation cycle key — or 0."""
    try:
        return int(getattr(world, "frame_count", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _time_of(world) -> float:
    """The world's simulation clock in seconds, or 0.0."""
    try:
        return float(getattr(world, "sim_time_s", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _mass_of(body) -> float:
    """A body's mass in kg (the declared fallback when it has none)."""
    try:
        value = float(getattr(body, "mass_kg", DEFAULT_MASS_KG)
                      or DEFAULT_MASS_KG)
    except (TypeError, ValueError):
        return DEFAULT_MASS_KG
    return value if value > 0.0 else DEFAULT_MASS_KG


def _speed_of(body) -> float:
    """A body's horizontal speed (magnitude of its velocity)."""
    try:
        return math.hypot(float(getattr(body, "vx", 0.0) or 0.0),
                          float(getattr(body, "vy", 0.0) or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _number_of(body, name: str, fallback: float = 0.0) -> float:
    """A float attribute of *body* (never raises)."""
    try:
        return float(getattr(body, name, fallback) or fallback)
    except (TypeError, ValueError):
        return fallback


def _resolve_body(world, player_id: int):
    """The live body carrying *player_id* on *world*, or None.

    A pure read of the world's player list.  Used to resolve the bodies on the
    contest's contact set (the primary defender and every attachment) so the
    drive measures the CONTEST, not merely its primary pair — while the two
    DECLARED parties of the relative-movement block stay the carrier and the
    primary defender, exactly as the Stage 2 contract names them.
    """
    if world is None or player_id is None:
        return None
    try:
        wanted = int(player_id)
    except (TypeError, ValueError):
        return None
    for body in (getattr(world, "players", None) or ()):
        try:
            if int(getattr(body, "player_id", -1) or -1) == wanted:
                return body
        except (TypeError, ValueError):
            continue
    return None


def tes_drive_id_for(tackle_id) -> str:
    """The drive identifier for the CONTEST it drives."""
    return "%s-%s" % (TES_DRIVE_ID_PREFIX, str(tackle_id or ""))


# ─────────────────────────────────────────────────────────────────────
# THE DECLARED CONTROL MAP (pure arithmetic, no random source)
# ─────────────────────────────────────────────────────────────────────

def target_control_for(momentum_balance_kgmps) -> float:
    """The control the measured momentum balance calls for, in [0, 1].

    ``0.5`` is a level contest.  A POSITIVE balance is momentum the carrier
    has that the contact set does not, so it raises the carrier's share; a
    NEGATIVE balance lowers it.  The squash is bounded by construction, so a
    runaway balance saturates smoothly toward 1.0 / 0.0 rather than clamping,
    and the result is always inside the declared control band.
    """
    try:
        balance = float(momentum_balance_kgmps)
    except (TypeError, ValueError):
        balance = 0.0
    if not math.isfinite(balance):
        balance = 0.0
    raw = 0.5 * (1.0 + math.tanh(balance / LEVERAGE_SCALE_KGMPS))
    return max(MIN_CONTROL, min(MAX_CONTROL, raw))


def trend_for(control_delta) -> ControlTrend:
    """The declared trend label for a control movement.  Descriptive only."""
    try:
        delta = float(control_delta)
    except (TypeError, ValueError):
        return ControlTrend.HOLDING
    if delta > TREND_EPS:
        return ControlTrend.STRENGTHENING
    if delta < -TREND_EPS:
        return ControlTrend.WEAKENING
    return ControlTrend.HOLDING


def seed_control_for(state) -> float:
    """The control a contest opens on, from its OWN frozen initial conditions.

    The Stage 2 contest captured the carrier's and the primary defender's
    momentum AT THE DECLARATION.  Applying the declared map to that frozen
    pair means the first cycle of the drive already evolves from the contest's
    formation data rather than from a blank slate — and it reads only the
    frozen ``*_momentum_kgmps`` fields, so the seed cannot change as the
    contest moves.
    """
    carrier_momentum = _number_of(state, "carrier_momentum_kgmps", 0.0)
    defender_momentum = _number_of(state, "defender_momentum_kgmps", 0.0)
    return target_control_for(carrier_momentum - defender_momentum)


# ─────────────────────────────────────────────────────────────────────
# THE MEASUREMENT (pure reads of the live bodies the contest names)
# ─────────────────────────────────────────────────────────────────────

def measure_tes_drive_v1(world, state) -> "dict | None":
    """MEASURE one cycle of the contest.  A pure read; it writes to NOTHING.

    Reads the two DECLARED parties the Stage 2 contest latched by identity
    (its carrier and its primary defender) for the relative-movement block,
    and the whole CONTACT SET (the primary plus every attachment, resolved by
    the ids the contest carries) for the defender momentum aggregate.

    DECLARED DEFINITIONS
      relative_vx / relative_vy   carrier velocity minus primary velocity.
      separation_m                carrier-to-primary centre distance.
      closing_speed_mps           rate at which that separation is shrinking:
                                  -(relative velocity projected on the
                                  primary->carrier axis).  Positive = closing.
      carrier_speed_mps           the carrier's speed.
      defender_speed_mps          the PRIMARY defender's speed.
      carrier_momentum_kgmps      carrier mass x carrier speed.
      defender_momentum_kgmps     the contact set's aggregate: the sum of
                                  mass x speed over the primary and every
                                  attachment currently on the contest.
      momentum_balance_kgmps      carrier momentum minus that aggregate.
      forward_progress_raw_m      the carrier's advance from the DECLARED
                                  Stage 2 origin, signed by the attack
                                  direction, BEFORE the declared envelope.
      forward_progress_m          the same value clamped into the declared
                                  envelope [-MAX_BACKWARD_M, +MAX_FORWARD_M].
      attachment_count            the contest's own attachment-row count.
      attached_count              how many of those rows have PHYSICALLY
                                  attached.
      contact_set_size            1 + attachment_count.

    Returns None when the contest does not name both declared parties, so a
    degenerate state cannot be measured into a fictional drive.
    """
    if state is None:
        return None
    carrier = getattr(state, "carrier", None)
    primary = getattr(state, "primary_defender", None)
    if carrier is None or primary is None:
        return None

    attack_dir = 1
    try:
        raw_dir = int(getattr(state, "attack_dir", 1) or 1)
        attack_dir = -1 if raw_dir < 0 else 1
    except (TypeError, ValueError):
        attack_dir = 1

    cx = _number_of(carrier, "x")
    cy = _number_of(carrier, "y")
    cvx = _number_of(carrier, "vx")
    cvy = _number_of(carrier, "vy")
    px = _number_of(primary, "x")
    py = _number_of(primary, "y")
    pvx = _number_of(primary, "vx")
    pvy = _number_of(primary, "vy")

    gap_x = cx - px
    gap_y = cy - py
    separation = math.hypot(gap_x, gap_y)
    if separation > SEPARATION_EPS_M:
        ux, uy = gap_x / separation, gap_y / separation
    else:
        # Degenerate separation: the declared fallback axis is the attack
        # axis, so the closing rate is well defined rather than arbitrary.
        ux, uy = (1.0 if attack_dir >= 0 else -1.0), 0.0

    relative_vx = cvx - pvx
    relative_vy = cvy - pvy
    closing_speed = -(relative_vx * ux + relative_vy * uy)

    carrier_speed = math.hypot(cvx, cvy)
    primary_speed = math.hypot(pvx, pvy)

    carrier_momentum = _mass_of(carrier) * carrier_speed

    # The defender aggregate is the contest's CONTACT SET, so a joiner that
    # has joined the contest counts toward what the carrier is pushing
    # against.  The primary is always counted exactly once: it is resolved
    # from the contact set when it resolves there, and from the latched
    # identity otherwise.
    defender_momentum = 0.0
    defender_speed = primary_speed
    counted = []
    try:
        contact_ids = tuple(getattr(state, "contact_set_ids", ()) or ())
    except TypeError:
        contact_ids = ()
    for player_id in contact_ids:
        body = _resolve_body(world, player_id)
        if body is None:
            continue
        try:
            body_id = int(getattr(body, "player_id", -1) or -1)
        except (TypeError, ValueError):
            continue
        if body_id in counted:
            continue
        counted.append(body_id)
        defender_momentum += _mass_of(body) * _speed_of(body)
    try:
        primary_id = int(getattr(state, "primary_defender_id", -1) or -1)
    except (TypeError, ValueError):
        primary_id = -1
    if primary_id not in counted:
        defender_momentum += _mass_of(primary) * primary_speed

    balance = carrier_momentum - defender_momentum

    # ── THE FORWARD-PROGRESS ORIGIN IS THE CONTEST'S, NOT OURS ──
    # Read through the Stage 2 contest's own reader when it is present, so
    # the datum is the declared origin rather than a second opinion built
    # here.  The declared envelope is applied on top of that raw reading.
    reader = getattr(state, "forward_progress_m", None)
    if callable(reader):
        try:
            raw_progress = float(reader(cx))
        except (TypeError, ValueError):
            raw_progress = 0.0
    else:
        origin_x = _number_of(state, "forward_origin_x", cx)
        raw_progress = (cx - origin_x) * float(attack_dir)
    if not math.isfinite(raw_progress):
        raw_progress = 0.0
    bounded_progress = max(-MAX_BACKWARD_M, min(MAX_FORWARD_M, raw_progress))

    try:
        attachment_count = int(getattr(state, "attachment_count", 0) or 0)
    except (TypeError, ValueError):
        attachment_count = 0
    try:
        attached_count = len(tuple(getattr(state, "attached_ids", ()) or ()))
    except TypeError:
        attached_count = 0
    try:
        contact_set_size = int(getattr(state, "contact_set_size", 1) or 1)
    except (TypeError, ValueError):
        contact_set_size = 1 + attachment_count

    return {
        "sim_time_s": _time_of(world),
        "attack_dir": attack_dir,
        "relative_vx": relative_vx,
        "relative_vy": relative_vy,
        "relative_speed_mps": math.hypot(relative_vx, relative_vy),
        "separation_m": separation,
        "closing_speed_mps": closing_speed,
        "carrier_speed_mps": carrier_speed,
        "defender_speed_mps": defender_speed,
        "carrier_momentum_kgmps": carrier_momentum,
        "defender_momentum_kgmps": defender_momentum,
        "momentum_balance_kgmps": balance,
        "target_control": target_control_for(balance),
        "forward_progress_raw_m": raw_progress,
        "forward_progress_m": bounded_progress,
        "attachment_count": attachment_count,
        "attached_count": attached_count,
        "contact_set_size": contact_set_size,
    }


# ─────────────────────────────────────────────────────────────────────
# THE EVOLUTION MAP — applied once per cycle, deterministic
# ─────────────────────────────────────────────────────────────────────

def _apply_measurement(drive: TESDriveV1, measured: dict, dt: float,
                       frame: int) -> None:
    """Relax *drive*'s declared live fields toward one cycle's measurement.

    THE map.  It writes to the DRIVE RECORD ONLY — never to a body, never to
    the contest, never to another world attribute.  It is deterministic:
    every value is a pure function of the previous record and this cycle's
    measurement, and ``dt`` is recorded rather than multiplied in.

    The control axis relaxes by at most ``MAX_CONTROL_STEP_PER_CYCLE`` over
    the cycle, so a contest cannot snap between advantage states; and the
    forward-progress envelope is tracked with a monotone best/worst pair, so
    the cycle-by-cycle path of the carrier stays visible on the record.
    """
    previous_control = float(drive.control)
    target = float(measured.get("target_control", CONTROL_NEUTRAL))
    delta = target - previous_control
    step = max(-MAX_CONTROL_STEP_PER_CYCLE,
               min(MAX_CONTROL_STEP_PER_CYCLE, delta))
    new_control = max(MIN_CONTROL, min(MAX_CONTROL, previous_control + step))

    drive.control = new_control
    drive.leverage = MAX_CONTROL - new_control
    drive.control_delta = new_control - previous_control
    drive.control_trend = trend_for(drive.control_delta)

    drive.relative_vx = float(measured.get("relative_vx", 0.0))
    drive.relative_vy = float(measured.get("relative_vy", 0.0))
    drive.relative_speed_mps = float(measured.get("relative_speed_mps", 0.0))
    drive.separation_m = float(measured.get("separation_m", 0.0))
    drive.closing_speed_mps = float(measured.get("closing_speed_mps", 0.0))

    drive.carrier_speed_mps = float(measured.get("carrier_speed_mps", 0.0))
    drive.defender_speed_mps = float(measured.get("defender_speed_mps", 0.0))
    drive.carrier_momentum_kgmps = float(
        measured.get("carrier_momentum_kgmps", 0.0))
    drive.defender_momentum_kgmps = float(
        measured.get("defender_momentum_kgmps", 0.0))
    drive.momentum_balance_kgmps = float(
        measured.get("momentum_balance_kgmps", 0.0))

    # ── THE FORWARD-PROGRESS ENVELOPE (monotone best/worst) ──
    drive.forward_progress_raw_m = float(
        measured.get("forward_progress_raw_m", 0.0))
    progress = float(measured.get("forward_progress_m", 0.0))
    drive.forward_progress_m = progress
    if drive.update_count == 0:
        # The first cycle of a drive opens the envelope on its own reading,
        # so an opening position does not read as a movement from zero.
        drive.forward_best_m = progress
        drive.forward_worst_m = progress
    else:
        drive.forward_best_m = max(float(drive.forward_best_m), progress)
        drive.forward_worst_m = min(float(drive.forward_worst_m), progress)

    # ── THE ATTACHMENT STATE IS A MIRROR, NEVER AN EDIT ──
    # The contest's own attachment rows are owned by Stage 2; this record
    # reports what it saw, and how many joined since the previous cycle.
    attachment_count = int(measured.get("attachment_count", 0) or 0)
    drive.new_attachments = attachment_count - int(drive.attachment_count)
    drive.attachment_count = attachment_count
    drive.attached_count = int(measured.get("attached_count", 0) or 0)
    drive.contact_set_size = int(measured.get("contact_set_size", 1) or 1)

    # ── CYCLE BOOKKEEPING ──
    drive.update_count += 1
    drive.frames_active = drive.update_count
    drive.last_update_frame = int(frame)
    drive.last_update_time_s = float(measured.get("sim_time_s", 0.0))
    drive.cycle_dt_s = float(dt)


# ─────────────────────────────────────────────────────────────────────
# THE LATCH: publication, reads, and the one drop path
# ─────────────────────────────────────────────────────────────────────

def _publish(world, drive: TESDriveV1) -> TESDriveV1:
    """Latch *drive* as the live drive on *world* and ring it.

    Publication is the single ``setattr`` this module owns, on the WORLD only,
    so no body and no engine subsystem can be perturbed by a cycle.  The ring
    exists so a probe can see every drive this process latched; nothing in the
    live engine reads it.
    """
    if world is not None:
        try:
            setattr(world, TES_DRIVE_ATTR, drive)
        except Exception:
            pass

    _DRIVE_RING.append(drive)
    if len(_DRIVE_RING) > TES_DRIVE_RING_LIMIT:
        del _DRIVE_RING[:-TES_DRIVE_RING_LIMIT]

    return drive


def _unpublish(world) -> None:
    """Drop the latch (the record stays on the ring as history)."""
    if world is None:
        return
    try:
        delattr(world, TES_DRIVE_ATTR)
    except AttributeError:
        pass


def published_tes_drive_v1(world) -> "TESDriveV1 | None":
    """The drive currently latched on *world* (or None).

    The raw latch read: whatever this process last published.  A drive is
    never closed, so a latched record is either the live one or a record
    whose contest has since gone; ``active_tes_drive_v1`` is the question a
    consumer asks.
    """
    if world is None:
        return None
    return getattr(world, TES_DRIVE_ATTR, None)


def active_tes_drive_v1(world) -> "TESDriveV1 | None":
    """THE LIVE DRIVE: the latched record, but only while it IS the contest.

    Returns None when no contest is active, and None when the latched record
    drives a DIFFERENT contest than the one live now, so a consumer can never
    read a stale drive as the current contest's evolution.
    """
    drive = published_tes_drive_v1(world)
    if drive is None:
        return None
    state = active_tackle_state(world)
    if state is None:
        return None
    if str(getattr(drive, "tackle_id", "")) != str(
            getattr(state, "tackle_id", "") or ""):
        return None
    return drive


def recent_tes_drives_v1() -> tuple:
    """The in-memory ring of every drive latched this process (oldest first)."""
    return tuple(_DRIVE_RING)


def reset_tes_drives_v1(world=None) -> None:
    """Drop the whole ledger (a fresh sequence).

    Clears the ring and, when *world* is given, the published latch.  It
    introduces no engine state and is read by nothing in the live loop.
    """
    global _DRIVE_RING
    _DRIVE_RING = []
    _unpublish(world)


# ─────────────────────────────────────────────────────────────────────
# THE PER-CYCLE UPDATE — exactly once, per active contest
# ─────────────────────────────────────────────────────────────────────

def create_tes_drive_v1(world, state, *, dt: float = 0.0,
                        frame: "int | None" = None) -> "TESDriveV1 | None":
    """CREATE and LATCH the drive for a formed contest.  ONE creation.

    The identity/provenance block is read from the CONTEST — its identity, its
    two declared parties, the frame and time it was declared on — so a drive
    is always traceable back to the TackleState it evolves.  ``control`` opens
    on the map applied to the contest's OWN frozen formation momentum, and the
    creation cycle then applies exactly one live update, so a drive's first
    cycle is a cycle like every other one.

    It creates NOTHING when there is nothing to drive: a call without a world
    or without a contest returns None.  It does not touch the contest.
    """
    if world is None or state is None:
        return None

    current_frame = _frame_of(world) if frame is None else int(frame)
    tackle_id = str(getattr(state, "tackle_id", "") or "")
    drive = TESDriveV1(
        drive_id=tes_drive_id_for(tackle_id),
        tackle_id=tackle_id,
        carrier_id=int(_number_of(state, "carrier_id", -1.0)),
        primary_defender_id=int(_number_of(state, "primary_defender_id", -1.0)),
        origin_frame=int(_number_of(state, "frame", float(current_frame))),
        origin_time_s=_number_of(state, "contact_time_s", 0.0),
        entry_frame=int(current_frame),
        owner=OWNER_TES,
    )
    drive.control = max(MIN_CONTROL, min(MAX_CONTROL, seed_control_for(state)))
    drive.leverage = MAX_CONTROL - drive.control
    drive.control_delta = 0.0
    drive.control_trend = ControlTrend.HOLDING

    measured = measure_tes_drive_v1(world, state)
    if measured is not None:
        _apply_measurement(drive, measured, dt, current_frame)
    else:
        drive.update_count = 1
        drive.frames_active = 1
        drive.last_update_frame = int(current_frame)
        drive.last_update_time_s = _time_of(world)
        drive.cycle_dt_s = float(dt)

    return _publish(world, drive)


def advance_tes_drive_v1(world, dt: float = 0.0) -> "TESDriveV1 | None":
    """THE per-cycle update.  Exactly once per simulation cycle.

    Called once by the engine's own update, after the phase handlers have
    produced the frame's geometry.  In one call it either

      * finds NO active contest and drops the latch, creating and advancing
        nothing — it does not close a contest, it observes that there is no
        contest to drive;
      * finds a contest this process has no drive for and CREATES one, whose
        creation cycle IS this cycle's update; or
      * finds the drive for the live contest and applies exactly ONE step of
        the declared map.

    IDEMPOTENT PER CYCLE: a second call in the same cycle finds the drive
    already stamped with this frame and returns it byte-identical, so "one
    update per cycle" holds however many times a caller asks.
    """
    state = active_tackle_state(world)
    if state is None:
        _unpublish(world)
        return None

    frame = _frame_of(world)
    drive = published_tes_drive_v1(world)
    if drive is None or str(getattr(drive, "tackle_id", "")) != str(
            getattr(state, "tackle_id", "") or ""):
        return create_tes_drive_v1(world, state, dt=dt, frame=frame)

    if drive.last_update_frame == frame:
        return drive

    measured = measure_tes_drive_v1(world, state)
    if measured is None:
        return drive

    _apply_measurement(drive, measured, dt, frame)
    return drive


# ─────────────────────────────────────────────────────────────────────
# REPORTING
# ─────────────────────────────────────────────────────────────────────

def format_tes_drive_v1_report(world) -> str:
    """Compact one-line rendering of the live drive."""
    drive = published_tes_drive_v1(world)
    if drive is None:
        return "tes_drive_v1=no_drive"
    return (
        "tes_drive_v1=%s tackle=%s frame=%s cycles=%d control=%.3f "
        "leverage=%.3f trend=%s progress=%.3f attachments=%d"
        % (drive.drive_id, drive.tackle_id, drive.last_update_frame,
           drive.update_count, drive.control, drive.leverage,
           drive.control_trend.value, drive.forward_progress_m,
           drive.attachment_count)
    )


def declared_evolution() -> tuple:
    """The DECLARED evolving-field contract: what a drive evolves each cycle.

    Declarative only — nothing reads this at run time.  It exists so the
    contract is stated in code as well as in the document, and so the verifier
    can assert that every declared block is present as a FIELD of the record
    rather than only in prose — exactly as the Stage 2 contract declares its
    initial conditions.
    """
    return (
        ("identity_and_provenance",
         ("drive_id", "tackle_id", "carrier_id", "primary_defender_id",
          "origin_frame", "origin_time_s", "entry_frame", "owner")),
        ("forward_progress",
         ("forward_progress_m", "forward_progress_raw_m",
          "forward_best_m", "forward_worst_m")),
        ("relative_movement",
         ("relative_vx", "relative_vy", "relative_speed_mps",
          "separation_m", "closing_speed_mps")),
        ("momentum",
         ("carrier_speed_mps", "defender_speed_mps",
          "carrier_momentum_kgmps", "defender_momentum_kgmps",
          "momentum_balance_kgmps")),
        ("control_and_leverage",
         ("control", "leverage", "control_delta", "control_trend")),
        ("attachment_state",
         ("attachment_count", "attached_count", "contact_set_size",
          "new_attachments")),
        ("cycle_bookkeeping",
         ("frames_active", "update_count", "last_update_frame",
          "last_update_time_s", "cycle_dt_s")),
    )
