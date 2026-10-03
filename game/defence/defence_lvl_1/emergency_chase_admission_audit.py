"""emergency_chase_admission_audit.py — THE ADMISSION LEDGER for the fullback
emergency-chase path (2026-09-30).

THE QUESTION THIS ANSWERS
-------------------------
The last defender is admitted to the tackle economy by ONE gate --
``fullback_role.fullback_emergency_defender_active`` -- which is a conjunction
of an IDENTITY half (the candidacy lever, the fullback tag, tackle authority,
not frozen, no live freeze timer) and a LINE-BREAK half (the canonical DEPTH
leg OR the 2D LATERAL leg, each of them additionally gated on the anchor being
a line the defence actually occupies).

When the gate refuses, the referee sees only ``False``.  There is nothing on
the frame that says WHICH conjunct refused, how far the carrier actually was
from the line, how wide the defensive line was standing, or how far the
nearest defender was from the carrier -- so every refusal has had to be
re-derived afterwards by a bespoke probe.

This module is the ledger of those refusals.  Every rejected line-break
evaluation is recorded ONCE, with:

    * a DEFENSIVE LINE WIDTH            -- the measured width of the live
                                           defensive line (with the commanded
                                           ``world.def_line_width_m`` as the
                                           declared fallback),
    * a GAP SIZE                        -- the carrier's signed depth gap to
                                           the line the break test evaluated
                                           against, plus that gap's surplus
                                           over the break margin,
    * a NEAREST DEFENDER DISTANCE       -- the nearest defending body to the
                                           carrier, and its id,
    * a REJECTION REASON                -- the exact conjunct that refused,
                                           from a closed vocabulary.

WHAT IT IS, AND WHAT IT IS NOT
------------------------------
IT IS OBSERVATIONAL.  It reads ``world`` / the two bodies and appends a record.
It never writes a body field, never writes a world field the engine reads
(only its own sink, and only when armed), never changes a predicate's answer
and never changes the frame.

IT IS A DECLARED LEDGER, NOT A DECISION.  The gate's verdict is still taken
solely by ``fullback_role``; this module is handed a reason the gate has
already reached.  It cannot admit a tackle, refuse one, or move a body.

IT IS OFF BY DEFAULT, AND FREE WHEN OFF.  Armed by
``RLM_FB_ADMISSION_AUDIT=1``, or by a caller installing a ledger on the world
(``world.emergency_chase_admission_audit`` -- the probe / verifier pattern).
With neither present ``record_rejection`` / ``record_admission`` return
immediately and the production engine is byte-identical and allocates nothing.

WHY THE LEDGER IS BOUNDED AND DEDUPLICATED
------------------------------------------
The gate is consulted MANY times per frame for the same body -- the lane
selector, the pre-tackle pool, the corridor and the manager trigger all read
it, and the two break legs are read again by the ownership-loss predicate and
the live-break establishment.  Recording one row per CALL would report the
same evaluation a dozen times and blow the ledger's budget on a single frame.

A row is therefore keyed on ``(frame, leg, player, reason, admitted)``: the
FIRST evaluation of that key in a frame creates the row, and every repeat
evaluation of the same key INCREMENTS ITS ``repeat_count``.  So "every rejected
line-break evaluation" is preserved as (distinct row, repeat_count) rather
than as duplicate rows, and the ledger's cost stays proportional to the number
of DISTINCT verdicts per frame.  A hard record ceiling
(``RLM_FB_ADMISSION_AUDIT_MAX``, default ``MAX_AUDIT_RECORDS``) bounds memory;
rows that would exceed it are counted in ``ledger.dropped`` so a truncated
ledger can never be mistaken for a complete one.

WIRING (all three sites are in ``fullback_role``)
-------------------------------------------------
    ``fullback_emergency_defender_active``  LEG_ADMISSION  -- the identity
                                            half's refusals, and the ADMITTED
                                            rows that name the admitting leg.
    ``fullback_depth_break_active``         LEG_DEPTH_BREAK
    ``fullback_lateral_break_active``       LEG_LATERAL_BREAK

The break legs are instrumented AT THE LEG rather than at the gate so a
refusal is attributed to the leg that actually refused, with that leg's own
reason -- the gate itself performs no break arithmetic of its own.

Import safety: module scope is stdlib only; every project reach is
function-local, because ``fullback_role`` imports this module and a top-level
project import here would close the cycle.
"""

from __future__ import annotations

import csv
import math
import os
from dataclasses import dataclass
from enum import Enum

__all__ = [
    "ADMISSION_AUDIT_ATTR",
    "ADMISSION_AUDIT_CSV_NAME",
    "ADMISSION_AUDIT_ENV_VAR",
    "ADMISSION_AUDIT_MAX_ENV_VAR",
    "CSV_COLUMNS",
    "MAX_AUDIT_RECORDS",
    "LEG_ADMISSION",
    "LEG_DEPTH_BREAK",
    "LEG_LATERAL_BREAK",
    "LEG_NAMES",
    "WIDTH_SOURCE_COMMANDED",
    "WIDTH_SOURCE_MEASURED",
    "WIDTH_SOURCE_UNAVAILABLE",
    "AdmissionLedger",
    "AdmissionRecord",
    "AdmissionVerdict",
    "admitted_records",
    "audit_armed",
    "audit_enabled_by_env",
    "condition_counts",
    "defensive_line_width_m",
    "depth_gap_m",
    "depth_margin_m",
    "format_admission_record",
    "install_ledger",
    "is_known_verdict",
    "is_line_break_verdict",
    "ledger_for",
    "nearest_defender_distance_m",
    "reason_counts",
    "record_admission",
    "record_rejection",
    "records_for",
    "rejection_records",
    "reset_admission_audit",
    "summarise_admission_audit",
    "write_admission_csv",
]

# ─────────────────────────────────────────────────────────────────────
# THE LEVER
# ─────────────────────────────────────────────────────────────────────
# Default OFF.  ``RLM_FB_ADMISSION_AUDIT=1`` arms the in-memory ledger (and
# is the documented rollback: unset it and the ledger is never touched).
ADMISSION_AUDIT_ENV_VAR: str = "RLM_FB_ADMISSION_AUDIT"

# Optional record ceiling override, read once when a ledger is created.
ADMISSION_AUDIT_MAX_ENV_VAR: str = "RLM_FB_ADMISSION_AUDIT_MAX"

# The world attribute a caller installs to capture the ledger WITHOUT the
# environment lever (the probe / verifier pattern -- exactly the shape
# ``game/debug/target_write_log`` uses for its write log).  Its value is an
# ``AdmissionLedger``; ``install_ledger`` on the world is this module's job.
ADMISSION_AUDIT_ATTR: str = "emergency_chase_admission_audit"

# The explicit export target for ``write_admission_csv``.
ADMISSION_AUDIT_CSV_NAME: str = "emergency_chase_admission_audit.csv"

# The hard record ceiling.  A distinct ``(frame, leg, player, reason)`` verdict
# costs one row; a full seeded rollout of 6000 frames carrying the tagged
# fullback produces well under this, and a sweep of many seeds can raise it.
MAX_AUDIT_RECORDS: int = 50000

_ENV_TRUTHY = ("1", "true", "yes", "on")

# ─────────────────────────────────────────────────────────────────────
# THE LEGS (which evaluation refused)
# ─────────────────────────────────────────────────────────────────────
# The identity half of the gate.  A refusal here is an ADMISSION refusal: the
# break was never evaluated because the body was not a candidate for it.
LEG_ADMISSION: str = "admission"

# The two line-break legs.  A refusal here IS a rejected line-break
# evaluation, with that leg's own reason.
LEG_DEPTH_BREAK: str = "depth_break"
LEG_LATERAL_BREAK: str = "lateral_break"

LEG_NAMES: tuple = (LEG_ADMISSION, LEG_DEPTH_BREAK, LEG_LATERAL_BREAK)

# ─────────────────────────────────────────────────────────────────────
# THE DEFENSIVE-LINE WIDTH SOURCES
# ─────────────────────────────────────────────────────────────────────
# MEASURED: the live Y span of the defensive LINE (role-filtered, fullback
# excluded) -- what the line is actually standing at this frame.
WIDTH_SOURCE_MEASURED: str = "measured"

# COMMANDED: no readable line to measure, so the declared width the line is
# being governed to (``world.def_line_width_m``) is reported instead.
WIDTH_SOURCE_COMMANDED: str = "commanded"

WIDTH_SOURCE_UNAVAILABLE: str = "unavailable"

# Roles that are NOT part of the defensive line's width: the marker pair and
# the dummy half play tight to the ruck, the TES tacklers belong to the
# tackle, and the kick-chase body has its own contract.  This is the SAME
# exempt vocabulary ``line_break_static_guard`` and the MMDL use, declared as
# NAMES so a body carrying the string form is matched too.
_NON_LINE_ROLE_NAMES = frozenset({
    "MARKER_A",
    "MARKER_B",
    "DUMMY_HALF",
    "TACKLER_1",
    "TACKLER_2",
    "TACKLER_3",
    "KICK_CHASE",
})


# ─────────────────────────────────────────────────────────────────────
# THE REJECTION VOCABULARY (the whole answer to "why was it refused")
# ─────────────────────────────────────────────────────────────────────

class AdmissionVerdict(Enum):
    """The closed vocabulary of admission outcomes, one value per conjunct.

    ADMITTED is the only value that is not a refusal; it is recorded so the
    ledger is an ADMISSION ledger rather than a refusal log, and so a
    rejection RATE can be read off the same table.
    """

    # Terminal (the gate opened).
    ADMITTED = "admitted"

    # Identity half (LEG_ADMISSION).
    LEVER_OFF = "lever_off"
    NOT_TAGGED = "not_tagged"
    NO_TACKLE_AUTHORITY = "no_tackle_authority"
    FROZEN = "frozen"
    FREEZE_TIMER = "freeze_timer"

    # Degenerate input (either leg).
    NO_WORLD = "no_world"
    NO_DEFENDER = "no_defender"
    NO_CARRIER = "no_carrier"

    # DEPTH leg (LEG_DEPTH_BREAK).
    BELOW_DEPTH_MARGIN = "below_depth_margin"

    # LATERAL leg (LEG_LATERAL_BREAK).
    DEPTH_BREAK_CLASSIFIED = "depth_break_classified"
    LINE_INTACT_LEVEL_EXCLUSION = "line_intact_level_exclusion"
    LANE_INTACT = "lane_intact"
    OUT_OF_REACH = "out_of_reach"

    # Either break leg: the break classified, but the anchor is not a line the
    # defence occupies (the kick-return / reception-seeded anchor).
    LINE_UNOCCUPIED = "line_unoccupied"


# The refusals that are the IDENTITY half -- a body refused here was never a
# candidate for a line-break evaluation, which is why the gate only records
# them for a body that carries the fullback tag (see ``record_rejection``).
_IDENTITY_VERDICTS = frozenset({
    AdmissionVerdict.LEVER_OFF,
    AdmissionVerdict.NOT_TAGGED,
    AdmissionVerdict.NO_TACKLE_AUTHORITY,
    AdmissionVerdict.FROZEN,
    AdmissionVerdict.FREEZE_TIMER,
})

# The refusals that ARE rejected LINE-BREAK evaluations.  This is the set the
# task's "log every rejected line-break evaluation" is about, and the set
# ``rejection_records`` reports by default.
_LINE_BREAK_VERDICTS = frozenset({
    AdmissionVerdict.BELOW_DEPTH_MARGIN,
    AdmissionVerdict.DEPTH_BREAK_CLASSIFIED,
    AdmissionVerdict.LINE_INTACT_LEVEL_EXCLUSION,
    AdmissionVerdict.LANE_INTACT,
    AdmissionVerdict.OUT_OF_REACH,
    AdmissionVerdict.LINE_UNOCCUPIED,
})


def is_line_break_verdict(verdict) -> bool:
    """True when *verdict* is a rejected LINE-BREAK evaluation.

    THE SELECTOR for the ledger's headline set.  Total: an unreadable value
    reports False (an unknown verdict is never counted as a break refusal).
    """
    try:
        resolved = _verdict_of(verdict)
    except Exception:                                    # noqa: BLE001
        return False
    return resolved in _LINE_BREAK_VERDICTS


def is_known_verdict(verdict) -> bool:
    """True when *verdict* names a member of the closed vocabulary.

    THE VALIDATION the wiring sites use before filing a row: the gate labels a
    refusal with a plain string, so an unknown label must be caught at the
    source rather than raising ``ValueError`` inside a movement predicate.
    Total: never raises.
    """
    try:
        _verdict_of(verdict)
    except Exception:                                    # noqa: BLE001
        return False
    return True


def _verdict_of(verdict) -> AdmissionVerdict:
    """Coerce *verdict* to an ``AdmissionVerdict`` (raises on junk)."""
    if isinstance(verdict, AdmissionVerdict):
        return verdict
    if isinstance(verdict, str):
        return AdmissionVerdict(verdict)
    raise TypeError(f"not a verdict: {verdict!r}")


# ─────────────────────────────────────────────────────────────────────
# THE RECORD
# ─────────────────────────────────────────────────────────────────────

@dataclass
class AdmissionRecord:
    """ONE distinct admission verdict on ONE frame, with its diagnostics.

    ``repeat_count`` counts how many times the SAME verdict was re-evaluated
    on the same frame (the gate is consulted by several consumers); ``1`` is a
    single evaluation.  The record is deliberately NOT frozen: the repeat
    counter is the ledger's dedup bookkeeping, not a measured fact.

    The three diagnostics the ledger exists for:

        ``defensive_line_width_m``      the measured (or commanded) width of
                                        the defensive line on this frame,
        ``gap_m``                       the carrier's signed depth gap to the
                                        line the break test consumed,
        ``nearest_defender_distance_m`` the nearest defending body to the
                                        carrier, with ``nearest_defender_id``.

    ``None`` on a diagnostic means NOT MEASURABLE, and is reported honestly
    rather than defaulted to zero -- a zero gap and an unmeasurable one are
    different facts and must not be confused.
    """

    frame: int
    leg: str
    verdict: str
    admitted: bool
    player_id: int | None
    defensive_line_width_m: float | None
    defensive_line_width_source: str
    gap_m: float | None
    depth_margin_m: float | None
    nearest_defender_distance_m: float | None
    nearest_defender_id: int | None
    anchor_x: float | None
    anchor_source: str
    bc_x: float | None
    bc_y: float | None
    line_occupied: bool | None = None
    latched: bool | None = None
    repeat_count: int = 1

    @property
    def verdict_name(self) -> str:
        """The verdict as a plain string (the ledger's own vocabulary)."""
        return str(self.verdict)

    def as_row(self) -> dict:
        """A flat column-keyed view (the CSV / digest shape)."""
        return {
            "frame": self.frame,
            "leg": self.leg,
            "verdict": self.verdict,
            "admitted": int(bool(self.admitted)),
            "player_id": self.player_id,
            "defensive_line_width_m": self.defensive_line_width_m,
            "defensive_line_width_source": self.defensive_line_width_source,
            "gap_m": self.gap_m,
            "depth_margin_m": self.depth_margin_m,
            "nearest_defender_distance_m": self.nearest_defender_distance_m,
            "nearest_defender_id": self.nearest_defender_id,
            "anchor_x": self.anchor_x,
            "anchor_source": self.anchor_source,
            "bc_x": self.bc_x,
            "bc_y": self.bc_y,
            "line_occupied": (None if self.line_occupied is None
                              else int(bool(self.line_occupied))),
            "latched": (None if self.latched is None
                        else int(bool(self.latched))),
            "repeat_count": self.repeat_count,
        }


CSV_COLUMNS: tuple = (
    "frame", "leg", "verdict", "admitted", "player_id",
    "defensive_line_width_m", "defensive_line_width_source",
    "gap_m", "depth_margin_m",
    "nearest_defender_distance_m", "nearest_defender_id",
    "anchor_x", "anchor_source", "bc_x", "bc_y",
    "line_occupied", "latched", "repeat_count",
)


# ─────────────────────────────────────────────────────────────────────
# THE LEDGER
# ─────────────────────────────────────────────────────────────────────

class AdmissionLedger:
    """A bounded, deduplicated set of ``AdmissionRecord`` rows.

    ``index`` maps the dedup key ``(frame, leg, player_id, verdict, admitted)``
    to the row's position in ``records``, so a repeat evaluation increments
    ``repeat_count`` instead of appending.  ``dropped`` counts the DISTINCT
    verdicts the ceiling refused, so a truncated ledger is self-reporting.
    """

    __slots__ = ("records", "index", "dropped", "limit")

    def __init__(self, limit: int = MAX_AUDIT_RECORDS) -> None:
        self.records: list = []
        self.index: dict = {}
        self.dropped: int = 0
        self.limit: int = int(limit)

    def append(self, key, record: AdmissionRecord) -> AdmissionRecord | None:
        """File *record* under *key*, or increment the existing row.

        Returns the row that now represents the evaluation (the fresh row, or
        the existing one whose ``repeat_count`` was incremented), or ``None``
        when the ceiling refused a NEW key (``dropped`` is incremented then).
        """
        position = self.index.get(key)
        if position is not None:
            existing = self.records[position]
            existing.repeat_count += 1
            return existing
        if len(self.records) >= self.limit:
            self.dropped += 1
            return None
        self.index[key] = len(self.records)
        self.records.append(record)
        return record

    def clear(self) -> None:
        """Drop every row and the dedup index (keep the ceiling)."""
        self.records = []
        self.index = {}
        self.dropped = 0

    @property
    def size(self) -> int:
        """Number of DISTINCT rows held."""
        return len(self.records)

    @property
    def evaluations(self) -> int:
        """Number of EVALUATIONS represented (rows weighted by repeats)."""
        return sum(row.repeat_count for row in self.records)


# The module-level ledger, used when the audit is armed by the environment and
# no ledger is installed on the world (a world-less caller).
_MODULE_LEDGER: AdmissionLedger | None = None


def audit_enabled_by_env() -> bool:
    """True when ``RLM_FB_ADMISSION_AUDIT`` arms the ledger."""
    return os.environ.get(
        ADMISSION_AUDIT_ENV_VAR, ""
    ).strip().lower() in _ENV_TRUTHY


def _env_limit() -> int:
    """The record ceiling for a NEW ledger (env override, else the default)."""
    raw = os.environ.get(ADMISSION_AUDIT_MAX_ENV_VAR, "").strip()
    if not raw:
        return MAX_AUDIT_RECORDS
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return MAX_AUDIT_RECORDS
    return value if value > 0 else MAX_AUDIT_RECORDS


def audit_armed(world=None) -> bool:
    """True when the admission ledger is collecting.

    Armed by EITHER the environment lever OR a ledger installed on *world*.
    Deliberately cheap and total: this is the hot gate every logging site
    checks FIRST, so a disarmed audit costs one attribute read.
    """
    if world is not None and getattr(world, ADMISSION_AUDIT_ATTR, None) is not None:
        return True
    return audit_enabled_by_env()


def install_ledger(world, limit: int | None = None) -> AdmissionLedger:
    """Install a fresh ledger on *world* and return it.

    THE probe / verifier entry point: it arms the audit for one world without
    touching the environment, so a run can be measured in isolation and every
    other world (and the production engine) stays byte-identical.
    """
    ledger = AdmissionLedger(_env_limit() if limit is None else int(limit))
    setattr(world, ADMISSION_AUDIT_ATTR, ledger)
    return ledger


def ledger_for(world=None, create: bool = True) -> AdmissionLedger | None:
    """The ledger this world writes to, or ``None`` when the audit is off.

    Resolution order:

      1. a ledger installed on *world* (the probe / verifier path),
      2. otherwise, when the environment lever is armed: the module ledger --
         installed ON the world when there is one, so it is inspectable there
         as well as through ``records_for``,
      3. otherwise ``None``.
    """
    if world is not None:
        installed = getattr(world, ADMISSION_AUDIT_ATTR, None)
        if installed is not None:
            return installed
    if not audit_enabled_by_env():
        return None
    global _MODULE_LEDGER
    if _MODULE_LEDGER is None:
        _MODULE_LEDGER = AdmissionLedger(_env_limit())
    if world is not None and create:
        # Mirror the module ledger onto this world so a probe can read it off
        # the world it measured.  The SAME object, so the two views cannot
        # disagree and no row is recorded twice.
        setattr(world, ADMISSION_AUDIT_ATTR, _MODULE_LEDGER)
    return _MODULE_LEDGER


def reset_admission_audit(world=None) -> None:
    """Drop every recorded verdict (kickoff / fresh measurement).

    Clears the world's ledger when it has one, the module ledger, and the
    installed attribute itself when *world* is given, so the audit returns to
    its DISARMED state on that world.
    """
    global _MODULE_LEDGER
    if world is not None:
        installed = getattr(world, ADMISSION_AUDIT_ATTR, None)
        if installed is not None:
            installed.clear()
            try:
                delattr(world, ADMISSION_AUDIT_ATTR)
            except AttributeError:
                pass
    if _MODULE_LEDGER is not None:
        _MODULE_LEDGER.clear()
        _MODULE_LEDGER = None


def records_for(world=None) -> tuple:
    """Every recorded verdict, in recording order (pure read)."""
    ledger = ledger_for(world, create=False)
    if ledger is None:
        return ()
    return tuple(ledger.records)


def _select(world, predicate) -> tuple:
    """Rows of the ledger matching *predicate* (pure read)."""
    return tuple(row for row in records_for(world) if predicate(row))


def admitted_records(world=None) -> tuple:
    """The rows whose gate OPENED (the admission half of the ledger)."""
    return _select(world, lambda row: bool(row.admitted))


def rejection_records(world=None, line_break_only: bool = True) -> tuple:
    """The rejected rows -- by default ONLY the rejected line-break evaluations.

    ``line_break_only=False`` returns every refusal, including the identity
    half (lever / tag / tackle authority / freeze).
    """
    if not line_break_only:
        return _select(world, lambda row: not bool(row.admitted))
    return _select(
        world,
        lambda row: (not bool(row.admitted))
        and is_line_break_verdict(row.verdict),
    )


def reason_counts(world=None) -> dict:
    """``{verdict: evaluations}`` across the ledger, weighted by repeats."""
    counts: dict = {}
    for row in records_for(world):
        counts[row.verdict] = counts.get(row.verdict, 0) + row.repeat_count
    return counts


def condition_counts(world=None) -> dict:
    """``{leg: evaluations}`` across the ledger, weighted by repeats."""
    counts: dict = {}
    for row in records_for(world):
        counts[row.leg] = counts.get(row.leg, 0) + row.repeat_count
    return counts


def format_admission_record(record: AdmissionRecord) -> str:
    """One compact line for a record (the probe's per-row rendering)."""
    width = ("n/a" if record.defensive_line_width_m is None
             else f"{record.defensive_line_width_m:.2f}")
    gap = "n/a" if record.gap_m is None else f"{record.gap_m:+.2f}"
    margin = ("n/a" if record.depth_margin_m is None
              else f"{record.depth_margin_m:+.2f}")
    nearest = ("n/a" if record.nearest_defender_distance_m is None
               else f"{record.nearest_defender_distance_m:.2f}")
    return (
        f"[FB_ADMISSION] frame={record.frame} leg={record.leg} "
        f"verdict={record.verdict} admitted={int(bool(record.admitted))} "
        f"body={record.player_id} line_width={width}"
        f"({record.defensive_line_width_source}) gap={gap} "
        f"margin={margin} nearest={nearest} repeat={record.repeat_count}"
    )


def summarise_admission_audit(world=None) -> str:
    """A one-line digest of the ledger: rows, evaluations, refusals, reasons."""
    ledger = ledger_for(world, create=False)
    if ledger is None:
        return "fb_admission_audit=off"
    counts = reason_counts(world)
    top = ", ".join(
        f"{name}={count}"
        for name, count in sorted(counts.items(), key=lambda kv: -kv[1])[:6]
    )
    return (
        f"fb_admission_audit=on rows={ledger.size} "
        f"evaluations={ledger.evaluations} dropped={ledger.dropped} "
        f"admitted={len(admitted_records(world))} "
        f"line_break_refusals={len(rejection_records(world))} "
        f"reasons[{top}]"
    )


# ─────────────────────────────────────────────────────────────────────
# THE THREE DIAGNOSTICS (pure, total, and independently testable)
# ─────────────────────────────────────────────────────────────────────

def _number(value):
    """Coerce *value* to a finite float, or ``None`` when unreadable.

    The ONE coercion every diagnostic uses: a missing attribute, a string, a
    NaN and an infinity all report ``None`` -- "not measurable" -- rather than
    a fabricated zero.  Pure and total.
    """
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _role_name(body) -> str | None:
    """``body.defender_role``'s NAME (string roles pass through)."""
    role = getattr(body, "defender_role", None)
    if role is None:
        return None
    name = getattr(role, "name", None)
    if name is not None:
        return str(name)
    return str(role)


def _team_bodies(world) -> tuple:
    """The defending population as a tuple (EMPTY when unreadable).

    Total on purpose: an absent ``defending_team``, a ``None``, or a value that
    is not iterable all report an empty population rather than raising.  Every
    diagnostic iterates through this, so a malformed world cannot make the
    ledger raise -- and a ledger that could raise INSIDE a movement predicate
    would change a frame, which is the one thing it must never do.
    """
    if world is None:
        return ()
    raw = getattr(world, "defending_team", None)
    if raw is None:
        return ()
    try:
        return tuple(raw)
    except TypeError:
        return ()


def defensive_line_width_m(world) -> tuple:
    """The defensive line's width this frame, as ``(metres, source)``.

    MEASURED BY DEFAULT.  The width of a defensive line IS the Y span its
    bodies stand across, so the primary measurement is
    ``max(y) - min(y)`` over the LIVE line: every defending body except the
    tagged fullback (a backfield sweeper, not a line body), the marker pair and
    the dummy half (tight to the ruck), the TES tacklers (the tackle's, not the
    line's) and any non-finite Y.  This is the SAME exempt vocabulary
    ``line_break_static_guard`` and the MMDL use, so the diagnostic and the
    engine agree on what a line body is.

    FALLBACK: fewer than two readable line bodies is not a measurable span, so
    the COMMANDED width the line is being governed to
    (``world.def_line_width_m``) is reported instead -- a declared number is
    still a better answer than none, and the row says which it got.

    Reports ``(None, "unavailable")`` when neither exists.  Pure and total:
    never raises, never mutates, never fabricates a span from one body.
    """
    ys: list = []
    for body in _team_bodies(world):
        if body is None:
            continue
        if getattr(body, "fullback_role_active", False):
            continue
        if _role_name(body) in _NON_LINE_ROLE_NAMES:
            continue
        y = _number(getattr(body, "y", None))
        if y is None:
            continue
        ys.append(y)
    if len(ys) >= 2:
        return (max(ys) - min(ys), WIDTH_SOURCE_MEASURED)

    commanded = _number(getattr(world, "def_line_width_m", None)) \
        if world is not None else None
    if commanded is not None and commanded > 0.0:
        return (commanded, WIDTH_SOURCE_COMMANDED)
    return (None, WIDTH_SOURCE_UNAVAILABLE)


def depth_gap_m(bc, anchor_x) -> float | None:
    """The carrier's SIGNED depth gap to the line: ``bc.x - anchor_x``.

    POSITIVE means the carrier is PAST the defensive line (attack runs +X and
    the defending tryline is the +X end), so a refused DEPTH leg reports a gap
    below the break margin.  ``None`` when either coordinate is unreadable.
    """
    bc_x = _number(getattr(bc, "x", None))
    anchor = _number(anchor_x)
    if bc_x is None or anchor is None:
        return None
    return bc_x - anchor


def _line_break_margin_m() -> float:
    """The break margin the gate itself applies (read from its single owner).

    ``fullback_role.FB_LINE_BREAK_MARGIN_M`` is the constant the admission gate
    and the canonical ``carrier_past_defensive_line`` predicate both consume,
    so the ledger must report the margin THAT LAW used rather than a copy.
    Read FUNCTION-LOCALLY: ``fullback_role`` imports this module, so a
    module-scope reach would close the cycle.
    """
    try:
        from game.defence.defence_lvl_1.fullback_role import (
            FB_LINE_BREAK_MARGIN_M,
        )
        value = float(FB_LINE_BREAK_MARGIN_M)
    except Exception:                                    # noqa: BLE001
        return 1.0
    return value if math.isfinite(value) else 1.0


def depth_margin_m(bc, anchor_x, margin: float | None = None) -> float | None:
    """The carrier's SURPLUS over the break margin: ``bc.x - (anchor + m)``.

    The DEPTH leg opens on ``surplus > 0``, so this is the exact distance by
    which a refused DEPTH evaluation fell short.  With *margin* omitted the
    live ``FB_LINE_BREAK_MARGIN_M`` is read from its owner.
    """
    gap = depth_gap_m(bc, anchor_x)
    if gap is None:
        return None
    resolved = _number(margin)
    if resolved is None:
        resolved = _line_break_margin_m()
    return gap - resolved


def nearest_defender_distance_m(world, bc, exclude=None) -> tuple:
    """The nearest defending body to the carrier, as ``(metres, player_id)``.

    MEASURED over the live defending team, so the row answers "how close was
    ANY body to the carrier when the last defender's chase was refused" -- the
    diagnostic that separates "the line had it covered" from "nobody did".

    The evaluated defender is INCLUDED (it is a defending body); pass it as
    *exclude* for the "nearest OTHER defender" reading.  The carrier itself is
    always skipped.  Reports ``(None, None)`` when nothing is measurable.
    Pure and total.
    """
    if world is None or bc is None:
        return (None, None)
    bc_x = _number(getattr(bc, "x", None))
    bc_y = _number(getattr(bc, "y", None))
    if bc_x is None or bc_y is None:
        return (None, None)
    best: float | None = None
    best_id: int | None = None
    for body in _team_bodies(world):
        if body is None or body is bc or body is exclude:
            continue
        x = _number(getattr(body, "x", None))
        y = _number(getattr(body, "y", None))
        if x is None or y is None:
            continue
        distance = math.hypot(x - bc_x, y - bc_y)
        if best is None or distance < best:
            best = distance
            player_id = getattr(body, "player_id", None)
            best_id = (int(player_id) if isinstance(player_id, (int, float))
                       else None)
    return (best, best_id)


# The anchor-source vocabulary: which line the row's gap was measured against.
#
#   "supplied"    the caller handed over the exact anchor its own evaluation
#                 consumed (every break-leg refusal does this);
#   "break_test"  the caller supplied nothing, so the line was read through
#                 the BREAK TEST reader -- the live anchor floored by any armed
#                 latch.  THIS is the reader the two break legs evaluate
#                 against, so an ADMITTED row measures the line its verdict
#                 was actually taken on rather than the live line, which a
#                 forward anchor relock can have moved out from under it;
#   "live"        no defender to read a latch from (the degenerate fallback);
#   "unavailable" the anchor could not be derived at all.
_ANCHOR_SOURCE_SUPPLIED: str = "supplied"
_ANCHOR_SOURCE_BREAK_TEST: str = "break_test"
_ANCHOR_SOURCE_LIVE: str = "live"
_ANCHOR_SOURCE_UNAVAILABLE: str = "unavailable"


def _anchor_for(world, anchor_x, defender=None):
    """The anchor a row reports, as ``(value, source)``.

    A caller that evaluated an anchor hands it over and it is reported
    verbatim.  With nothing supplied, the BREAK TEST reader is consulted first
    -- the same reader the two break legs consume -- so a row's gap is always
    measured against the line its verdict used, latch included.  Never raises.
    """
    supplied = _number(anchor_x)
    if supplied is not None:
        return (supplied, _ANCHOR_SOURCE_SUPPLIED)
    if world is None:
        return (None, _ANCHOR_SOURCE_UNAVAILABLE)
    if defender is not None:
        try:
            from game.defence.defence_lvl_1.fullback_role import (
                break_test_line_anchor_x,
            )
            latched = _number(break_test_line_anchor_x(world, defender))
        except Exception:                                # noqa: BLE001
            latched = None
        if latched is not None:
            return (latched, _ANCHOR_SOURCE_BREAK_TEST)
    try:
        from game.defence.defence_lvl_1.fullback_role import line_anchor_x
        value = _number(line_anchor_x(world))
    except Exception:                                    # noqa: BLE001
        value = None
    return (value,
            _ANCHOR_SOURCE_LIVE if value is not None
            else _ANCHOR_SOURCE_UNAVAILABLE)


def _latched_state(defender):
    """Whether *defender* carries an armed break latch (``None`` if unknown).

    The latch is the fact that makes a live chase immune to a forward anchor
    relock, so a refusal on a LATCHED body reads differently from one on an
    unlatched body.  Read through the gate's own reader, function-locally (the
    import cycle again), and never allowed to raise.
    """
    if defender is None:
        return None
    try:
        from game.defence.defence_lvl_1.fullback_role import (
            is_fullback_break_latched,
        )
        return bool(is_fullback_break_latched(defender))
    except Exception:                                    # noqa: BLE001
        return None


def _occupied_state(world, anchor_x, defender):
    """Whether the anchor is a line the defence occupies (``None`` if unknown).

    THE OCCUPANCY GATE'S OWN READER, delegated so the row states the gate's
    fact rather than a second opinion about it.  Never raises.
    """
    if world is None or anchor_x is None or defender is None:
        return None
    try:
        from game.defence.defence_lvl_1.fullback_role import (
            fullback_defensive_line_occupied,
        )
        return bool(fullback_defensive_line_occupied(
            world, anchor_x, defender,
        ))
    except Exception:                                    # noqa: BLE001
        return None


# ─────────────────────────────────────────────────────────────────────
# THE RECORDERS
# ─────────────────────────────────────────────────────────────────────

def _build_record(world, defender, bc, leg, verdict, admitted: bool,
                  anchor_x=None) -> AdmissionRecord:
    """Assemble ONE row, measuring all three diagnostics exactly once."""
    player_id = getattr(defender, "player_id", None) if defender is not None \
        else None
    width_m, width_source = defensive_line_width_m(world)
    anchor_value, anchor_source = _anchor_for(world, anchor_x, defender)
    nearest_m, nearest_id = nearest_defender_distance_m(world, bc)
    return AdmissionRecord(
        frame=int(_number(getattr(world, "frame_count", None)) or 0)
        if world is not None else 0,
        leg=leg,
        verdict=verdict.value,
        admitted=bool(admitted),
        player_id=(int(player_id)
                   if isinstance(player_id, (int, float)) else None),
        defensive_line_width_m=width_m,
        defensive_line_width_source=width_source,
        gap_m=depth_gap_m(bc, anchor_value),
        depth_margin_m=depth_margin_m(bc, anchor_value),
        nearest_defender_distance_m=nearest_m,
        nearest_defender_id=nearest_id,
        anchor_x=anchor_value,
        anchor_source=anchor_source,
        bc_x=_number(getattr(bc, "x", None)) if bc is not None else None,
        bc_y=_number(getattr(bc, "y", None)) if bc is not None else None,
        line_occupied=_occupied_state(world, anchor_value, defender),
        latched=_latched_state(defender),
    )


def _file(world, key, record: AdmissionRecord) -> AdmissionRecord | None:
    """File *record* in this world's ledger (no-op when the audit is off)."""
    ledger = ledger_for(world)
    if ledger is None:
        return None
    return ledger.append(key, record)


def record_rejection(world, defender, bc, leg, verdict, anchor_x=None):
    """Record ONE REJECTED admission / line-break evaluation.

    *verdict* is the conjunct that refused, from ``AdmissionVerdict``; *leg*
    names which gate produced it (``LEG_ADMISSION`` / ``LEG_DEPTH_BREAK`` /
    ``LEG_LATERAL_BREAK``).  Returns the row that now represents the
    evaluation, or ``None`` when nothing was recorded -- the audit is off, the
    verdict is not a refusal, or the record ceiling refused a new key.

    NOT_TAGGED IS NOT RECORDED, deliberately.  A body that does not carry the
    fullback tag was never a candidate for a line-break evaluation: the gate
    answers "is this body the emergency defender" for it and performs no break
    arithmetic at all.  Filing those rows would report every line defender in
    the competition as a "rejected line-break evaluation" and bury the real
    refusals.  The value stays in the vocabulary so a caller that passes it
    gets an explicit no-op.

    The recoder cannot change a frame: it reads, it measures, it appends to its
    own sink, and it returns.  A measurement it cannot take is recorded as
    ``None`` rather than being guessed at.
    """
    try:
        resolved = _verdict_of(verdict)
    except (TypeError, ValueError):
        return None
    if resolved is AdmissionVerdict.ADMITTED:
        return None
    if resolved is AdmissionVerdict.NOT_TAGGED:
        return None
    record = _build_record(
        world, defender, bc, leg, resolved, False, anchor_x,
    )
    key = (record.frame, leg, record.player_id, record.verdict, False)
    return _file(world, key, record)


def record_admission(world, defender, bc, leg, anchor_x=None):
    """Record ONE ADMISSION (the gate opened) naming the admitting *leg*.

    The ledger's other half: without it a rejection COUNT has no denominator,
    and a rejection RATE -- the number that says whether the last defender is
    being admitted on breaks at all -- cannot be read off the table.  Returns
    the row, or ``None`` when the audit is off / the ceiling refused it.
    """
    record = _build_record(
        world, defender, bc, leg, AdmissionVerdict.ADMITTED, True, anchor_x,
    )
    key = (record.frame, leg, record.player_id, record.verdict, True)
    return _file(world, key, record)


# ─────────────────────────────────────────────────────────────────────
# EXPORT (explicit, so the hot path never touches the disk)
# ─────────────────────────────────────────────────────────────────────

def _rounded(value):
    """A CSV-printable value: a measured number, a label, or NOTHING.

    EXPLICIT because the column set mixes MEASUREMENTS with LABELS.  A finite
    number is rounded to 4 dp (so two rollouts' CSVs are diffable), a label (a
    leg, a reason, a width source) is passed through unchanged -- the export
    exists to carry those, so they must never be blanked -- and anything else
    (``None``, a NaN, an infinity, an unreadable object) becomes the EMPTY
    FIELD rather than a printed ``nan``/``inf`` that a reader could mistake
    for a measurement.
    """
    number = _number(value)
    if number is not None:
        return round(number, 4)
    if isinstance(value, str):
        return value
    return ""


def write_admission_csv(world=None, path: str | None = None) -> str | None:
    """Write the whole ledger to CSV and return the path (or ``None``).

    EXPLICIT ON PURPOSE.  The gate is consulted many times per frame, so the
    recording path is in-memory only; the file is written when a probe or a
    report step asks for it.  Returns ``None`` when the audit is off or the
    ledger is empty, so "no file" is never confused with "an empty file".
    """
    ledger = ledger_for(world, create=False)
    if ledger is None or not ledger.records:
        return None
    target = path or ADMISSION_AUDIT_CSV_NAME
    with open(target, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        for row in ledger.records:
            raw = row.as_row()
            writer.writerow({
                name: _rounded(raw.get(name)) for name in CSV_COLUMNS
            })
    return target
