"""tes_multi_body.py — THE MULTI-BODY WRAP MODEL (TES, 2026-09-23).

WHAT THIS FILE IS
-----------------
The PURE DATA + PURE MATHS of the TES multi-body stage.  It declares:

  * ``WrapContribution`` — ONE body's contribution to the collaborative wrap;
  * ``WrapModel``        — THE derived multi-body pressure on one contest:
    the primary's impact, every in-radius assist the decider never evaluated,
    the collaborative wrap force, the collaborative tackle duration, the
    tackle-break window and the offload window, all on one frozen object;
  * the consumption helpers the TES loop reads — ``wrap_delta_for``,
    ``collaborative_duration_scale``, ``break_drive_threshold``,
    ``offload_chance_with_window``.

WHY THE MODEL EXISTS
--------------------
Stage 1B owned contest declaration.  Stage 2A preserved the COMPLETE
declaration — including the in-radius bodies the decider's short-circuit threw
away.  Stage 2B made that declaration transportable, and the TES boundary now
carries the whole ``Contest``.  What no stage did was CONSUME the multi-body
part: TES received one winning defender and read one field of the object
carrying it, so a two-body and a six-body tackle resolved identically.

This stage consumes it.  Every field of the model is either a fact the
declaration already carried (the winner, the evaluated set, the in-radius set,
the radius, the failed-contact effects, the frame, the sequence) or a measured
kinematic of a body the declaration NAMED.  Nothing here re-measures a radius
to decide who is in the contest: membership is the RECORD's answer, because
re-deriving it would recreate exactly the loss Stages 2A and 2B removed.

THE MEMBERSHIP LAW (load-bearing)
---------------------------------
    EVALUATED_WINNER / ADMITTED_BY_CHANNEL_PREDICATE   -> the PRIMARY
    IN_RADIUS_NOT_EVALUATED                            -> a WRAP ASSIST
    ROLE_HOLDER_OUT_OF_RADIUS                          -> an OFFLOAD WATCH
    EVALUATED_REJECTED / NOT_A_CANDIDATE               -> nothing

A wrap assist is a body the authority never evaluated, so it can never be
described by the decider's own vocabulary — which is precisely why Stage 2A
had to declare its own.  The model is the consumer that vocabulary was
preserved for.

THE THREE COLLABORATIVE QUANTITIES
----------------------------------
1. WRAP FORCE — the SUM OF THE IMPACT VECTORS.  Each contributor's impact
   vector is ``mass * velocity`` (kg m/s); the collaborative wrap momentum is
   the closing component of that vector along the axis defender -> carrier,
   summed over the primary and every wrap assist.  The model exposes the raw
   vector sum, its magnitude, the closing (wrap) momentum, and
   ``wrap_force_n = wrap_momentum_kgmps / WRAP_IMPACT_WINDOW_S``.
   UNITS ARE DECLARED, NOT IMPLIED: momentum is a measurement, force needs a
   window, and the window is a named constant.

2. TACKLE DURATION — more collaborative wrap pressure resolves the contest
   SOONER.  ``collaborative_duration_scale`` returns a multiplier in
   ``[DURATION_SCALE_MIN, DURATION_SCALE_MAX]``; with no model it is exactly
   1.0, so the legacy draw passes through untouched.

3. BREAK / OFFLOAD WINDOWS — the two declaration-driven contest outcomes:
   an arm-grab that left the carrier with a SINGLE in-radius candidate opens
   the tackle-break window (the wrap was incomplete), and an unsupported role
   holder left outside the radius opens an explicit offload window.

4. THE FEND MOMENTUM CURVE (Stage 3.2) — the CARRIER's own side of the
   collision: ``Effective Carrier Momentum = Mass x Speed x fend_trait``.
   Every other quantity above measures a DEFENDER; this one measures the body
   they are trying to stop, off the trait slot the declaration already carries.
   With the neutral 1.0 the multiplication is the identity, so the legacy
   momentum score is reproduced bit for bit.

5. THE DEFENSIVE SKILL RESISTANCE (Stage 3.3) — the DEFENDERS' own side of
   the collision: ``Effective Defender Resistance = Mass x Closing Speed x
   clamp(tackle_skill_trait, 0.5, 2.0)``.  Applied to EVERY body that wraps —
   the primary and each pile-up assist alike — so a tackler's own skill
   scales the resistance they apply.  One contributor's effective wrap is
   that product, ``wrap_momentum_kgmps`` is the weighted sum of them, and
   ``baseline_wrap_momentum_kgmps`` keeps the Stage 2B quantity alongside it.
   With the neutral 1.0 the multiplication is again the identity, so a
   neutral declaration reproduces the legacy wrap bit for bit.

NOT GATED BY THE RECORDER
-------------------------
``RLM_CONTEST_PRESERVATION`` governs the Stage 2A RECORDER.  This module is
the CONSUMER, and it has its own declared A/B lever — ``RLM_TES_MULTI_BODY``,
opt-out, default ON — so the collaborative arm can be measured against the
single-body arm in-process.  With the lever OFF the model is never built, so
every consumer sees ``None`` and runs its unmodified legacy branch.

READ PURITY
-----------
This module has NO project imports and NO engine reach.  It performs ZERO
attribute stores on any body, world or engine object: the only mutation is the
bounded in-memory ring it owns (probe/audit only, never read by behaviour),
exactly as ``contest.py`` owns ``_CONTEST_RING``.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

__all__ = [
    "MULTI_BODY_ENV_VAR",
    "WRAP_MODEL_RING_LIMIT",
    "SEPARATION_EPS_M",
    "DEFAULT_MASS_KG",
    "MAX_ASSIST_COUNT",
    "WRAP_IMPACT_WINDOW_S",
    "WRAP_MOMENTUM_REFERENCE_KGMPS",
    "WRAP_FORCE_REFERENCE_N",
    "ASSIST_WRAP_WEIGHT",
    "PRIMARY_WRAP_WEIGHT",
    "WRAP_DELTA_MAX",
    "DURATION_SCALE_MIN",
    "DURATION_SCALE_MAX",
    "DURATION_ASSIST_SCALE",
    "BREAK_DURATION_SCALE",
    "BREAK_THRESHOLD_FACTOR",
    "BREAK_REASON_ARM_GRAB_SINGLE_CANDIDATE",
    "OFFLOAD_WINDOW_BONUS",
    "OFFLOAD_WINDOW_MAX_CHANCE",
    "OFFLOAD_REASON_UNSUPPORTED_ROLE_HOLDER",
    "DOMINANCE_ASSIST_MOMENTUM_WEIGHT",
    "DOMINANCE_ASSIST_COUNT_WEIGHT",
    "ROLE_PRIMARY",
    "ROLE_ASSIST",
    "ROLE_OFFLOAD_WATCH",
    "FEND_TRAIT_NEUTRAL",
    "FEND_TRAIT_MIN",
    "FEND_TRAIT_MAX",
    "TACKLE_SKILL_TRAIT_NEUTRAL",
    "TACKLE_SKILL_TRAIT_MIN",
    "TACKLE_SKILL_TRAIT_MAX",
    "COLLISION_MARGIN_THRESHOLD_KGMPS",
    "COLLISION_STATE_BUMP_OFF",
    "COLLISION_STATE_DOMINANT_SMASH",
    "COLLISION_STATE_PROGRESSIVE_DRAG",
    "COLLISION_STATES",
    "WrapContribution",
    "WrapModel",
    "multi_body_enabled",
    "derive_wrap_model",
    "primary_row",
    "assist_rows",
    "offload_watch_rows",
    "wrap_delta_for",
    "collaborative_duration_scale",
    "break_window_for",
    "break_drive_threshold",
    "offload_window_for",
    "offload_chance_with_window",
    "assist_momentum_kgmps",
    "fend_trait_of",
    "effective_carrier_momentum_kgmps",
    "carrier_momentum_multiplier",
    "tackle_skill_trait_of",
    "effective_defender_resistance_kgmps",
    "defender_resistance_multiplier",
    "resolve_collision_state",
    "recent_wrap_models",
    "reset_wrap_models",
    "format_wrap_model_report",
    "declared_content",
    "declared_gates",
]


# ─────────────────────────────────────────────────────────────────────
# THE A/B LEVER (opt-OUT, default ON)
# ─────────────────────────────────────────────────────────────────────
# ON is the feature.  ``RLM_TES_MULTI_BODY=0`` restores the single-body arm so
# the collaborative consumption can be MEASURED against it rather than
# asserted.  It is read in exactly one place (``multi_body_enabled``) and,
# with it off, ``derive_wrap_model`` returns None — so every consumer takes its
# unmodified legacy branch and the engine is bit-identical to the stage before.
MULTI_BODY_ENV_VAR: str = "RLM_TES_MULTI_BODY"

# The bounded in-memory ring of every model this process has derived.  It
# exists so a probe can see the whole sequence of contests; nothing in the
# live engine reads it.
WRAP_MODEL_RING_LIMIT: int = 600

# ─────────────────────────────────────────────────────────────────────
# MEASUREMENT CONSTANTS
# ─────────────────────────────────────────────────────────────────────
# Below this separation the defender->carrier direction is undefined, so the
# declared fallback axis is the attack axis (mirrors tackle_state.py).
SEPARATION_EPS_M: float = 0.01

# Fallback body mass, mirroring ``game/tes/tes.py::_make_ctx`` and
# ``tackle_state.py`` so no consumer invents a third convention.
DEFAULT_MASS_KG: float = 90.0

# At most three bodies can wrap a carrier in this engine: the primary plus up
# to two registered joiners (``TESContext.max_joiners``).  The model therefore
# caps its assist list at the same number, so a corrupt or over-populated
# record can never inflate the wrap force past the engine's own limit.
MAX_ASSIST_COUNT: int = 2

# The declared impact window a wrap happens over.  This is what turns a
# momentum SUM into a FORCE: F = dp/dt.  It is a DECLARED modelling constant,
# not a measurement, and it is named so it can be argued with rather than
# buried in an expression.
WRAP_IMPACT_WINDOW_S: float = 0.20

# The reference collaborative wrap momentum a fully-committed multi-body
# tackle produces (kg m/s).  Used to normalise the wrap term and the duration
# scale into [0, 1]; chosen so a two-assist pile at ~4 m/s closure sits near
# the top of the range.
WRAP_MOMENTUM_REFERENCE_KGMPS: float = 600.0

# The reference force that matches ``WRAP_MOMENTUM_REFERENCE_KGMPS``.  Stated
# separately so a reader never has to divide by the window to compare.
WRAP_FORCE_REFERENCE_N: float = (
    WRAP_MOMENTUM_REFERENCE_KGMPS / WRAP_IMPACT_WINDOW_S
)

# Weights of one contributor's impact in the collaborative sum.  A pure sum
# is the declared model: 1.0 for the primary and 1.0 for each genuine wrap
# assist.  They are named constants rather than literal 1.0s so a future
# tuning stage has one place to change and a verifier has one place to read.
PRIMARY_WRAP_WEIGHT: float = 1.0
ASSIST_WRAP_WEIGHT: float = 1.0

# The wrap term's contribution to the waveform's stability delta.  The legacy
# single-pair term in ``tes_dominance._compute_wrap_delta`` is bounded by
# +/-0.01, so the collaborative term is declared at the same order: a wrap can
# only ever help the defender as much as a wrap already could.
WRAP_DELTA_MAX: float = 0.01

# The collaborative duration band.  A pile can shorten the contest, never
# lengthen it, and never by more than this fraction.
DURATION_SCALE_MIN: float = 0.60
DURATION_SCALE_MAX: float = 1.00
DURATION_ASSIST_SCALE: float = 0.25

# A contest with an OPEN break window resolves faster still — the carrier is
# going through the tackle, so the phase does not need to play out in full.
BREAK_DURATION_SCALE: float = 0.85

# The factor the break-driving gate is multiplied by while the break window is
# open.  This is how the record TRIGGERS the break CALCULATION (the engine's
# own BC-overpowers test still runs); it does not force the break.
BREAK_THRESHOLD_FACTOR: float = 0.75

# The declared reason string for an open break window.
BREAK_REASON_ARM_GRAB_SINGLE_CANDIDATE: str = "arm_grab_with_single_candidate"

# The offload window's declared lift on the offload chance, and its ceiling.
OFFLOAD_WINDOW_BONUS: float = 0.12
OFFLOAD_WINDOW_MAX_CHANCE: float = 0.95
OFFLOAD_REASON_UNSUPPORTED_ROLE_HOLDER: str = "unsupported_role_holder_out_of_radius"

# How a wrap assist enters the dominance sum: its momentum is counted at this
# weight and it counts as this much of a tackler.  Negative or zero weights
# would let an assist REDUCE defensive pressure, which no reading of the
# record supports, so both are declared positive.
DOMINANCE_ASSIST_MOMENTUM_WEIGHT: float = 0.5
DOMINANCE_ASSIST_COUNT_WEIGHT: float = 0.5

# ─────────────────────────────────────────────────────────────────────
# THE FEND MOMENTUM CURVE (Stage 3.2, 2026-09-23)
# ─────────────────────────────────────────────────────────────────────
# The CARRIER's side of the collision.  Every other quantity in this module
# measures a DEFENDER; this one measures the body they are trying to stop, and
# it is the only place a carrier's own skill enters the contest:
#
#     Effective Carrier Momentum = Mass x Speed x fend_trait
#
# The trait is a percentage multiplier on the carrier's BASELINE momentum.
# 1.0 is NEUTRAL and is the identity of the multiplication, so a neutral run
# reproduces the legacy physics exactly; 1.25 is a carrier 25% harder to wrap.
#
# WHERE IT IS CHECKED.  The effective momentum is the carrier's side of the
# collision the dominance equation already resolves — ``bc_momentum`` weighed
# against the tacklers' aggregate resistance and banded at +/-1.0 into
# WEAK / NEUTRAL / DOMINANT.  It is deliberately NOT wired into the
# drive-distance writer, which turns an already-decided result into a
# displacement through its own delta clamp, forward floor and result
# multiplier: that floor exists to guarantee a winning carrier a real forward
# gain, and scaling its momentum term would fight the contract it encodes.
FEND_TRAIT_NEUTRAL: float = 1.0

# The declared BAND on the multiplier.  A carrier can be modelled at worst
# half as hard to wrap and at best twice as hard, so a corrupt, hand-built or
# over-populated record can neither DELETE a carrier's momentum nor inflate it
# without bound.  Neutral 1.0 sits inside the band, so the band never touches
# the legacy arm, and a value inside it is returned untouched rather than
# rounded to a bound.
FEND_TRAIT_MIN: float = 0.5
FEND_TRAIT_MAX: float = 2.0

# ─────────────────────────────────────────────────────────────────────
# THE DEFENSIVE SKILL RESISTANCE (Stage 3.3, 2026-09-24)
# ─────────────────────────────────────────────────────────────────────
# The DEFENDERS' side of the collision — the mirror of the fend curve above.
# Where the carrier's own skill scales the momentum they BRING, a tackler's
# own skill scales the resistance they APPLY:
#
#     Effective Defender Resistance = Mass x Closing Speed
#                                     x clamp(tackle_skill_trait, 0.5, 2.0)
#
# The trait is the percentage multiplier Stage 3.1 appended to a body row
# (``CandidateEvidence.tackle_skill_trait``, row field 19) and consumed by
# nobody since.  It is read from THE ROW the declaration carries — never
# re-measured off a live body — so the record stays the single source of
# truth for membership AND for skill, exactly as it is for the fend curve.
#
# WHERE IT IS APPLIED.  To EVERY body that wraps the carrier: the primary
# (EVALUATED_WINNER / ADMITTED_BY_CHANNEL_PREDICATE) and each pile-up assist
# (IN_RADIUS_NOT_EVALUATED).  One contributor's closing resistance is
#
#     mass x closing_speed x clamp(tackle_skill_trait)
#
# and the collaborative wrap momentum is the weighted SUM of those effective
# resistances.  The reserve is deliberately NOT applied anywhere else: the
# raw impact vectors, the aggregate mass, the dominance assist term and the
# drive floor keep the readings Stage 2B pinned, and ``core_strength_trait``
# stays unconsumed (a later stage's slot, like ``tackle_break_trait``).
TACKLE_SKILL_TRAIT_NEUTRAL: float = 1.0

# The declared BAND on the multiplier, identical in shape to the fend band: a
# tackler can be modelled at worst half as effective and at best twice, so a
# corrupt, hand-built or over-populated row can neither DELETE a defender's
# resistance nor inflate it without bound.  Neutral 1.0 sits inside the band,
# so the band never touches the legacy arm, and a value inside it is returned
# UNTOUCHED rather than rounded to a bound.
TACKLE_SKILL_TRAIT_MIN: float = 0.5
TACKLE_SKILL_TRAIT_MAX: float = 2.0

# ---------------------------------------------------------------------
# THE COLLISION STATE DISCRIMINATOR (dynamic results pipeline, step 1)
# ---------------------------------------------------------------------
# The MARGIN between the two sides of the collision -- the carrier's
# effective momentum (Stage 3.2) MINUS the defenders' effective wrap
# (Stage 3.3) -- is already a declared reader on the model
# (``WrapModel.carrier_momentum_margin_kgmps``).  This block declares what
# that margin MEANS, as an explicit outcome token rather than a bare number:
#
#     margin >  +350.0  ->  BUMP_OFF          the carrier wins the contact
#     margin <  -350.0  ->  DOMINANT_SMASH    the defence wins the contact
#     otherwise         ->  PROGRESSIVE_DRAG  neither side clears the threshold
#
# PROGRESSIVE_DRAG is the CONTEST state AND the identity: a neutral
# declaration does not clear the threshold in either direction, and an
# unmeasurable margin is UNDECIDED rather than awarded to either side.
#
# The threshold is a DECLARED modelling constant, not a tuning knob buried in
# an expression, and it is STRICTLY EXCLUSIVE at both ends: exactly +350.0
# and exactly -350.0 both resolve to PROGRESSIVE_DRAG.
#
# -- RECALIBRATION TO 350.0 (dynamic results pipeline, step 3.3) -----------
# The boundary was 150.0, and at 150.0 the Stage 3.2 fend curve's own TOP
# magnitude already cleared it on its own: a 1.5-fend carrier resolves a
# margin of +350.0, so EVERY contest the fend touched resolved BUMP_OFF and
# neither of the two OTHER declared outcomes -- the DOMINANT_SMASH the wrap
# is supposed to be able to earn, and the PROGRESSIVE_DRAG grind the ruck
# path consumes -- was reachable in live play.  That was the temporary
# "super-fend" testbed state.
#
# 350.0 is the fend curve's exact maximum, so at the recalibrated boundary
# the fend ALONE no longer wins a contact: +350.0 sits ON its own edge and
# the strictly-exclusive test above resolves it to PROGRESSIVE_DRAG, which
# is what hands a tight collision to the decay-and-ruck path instead of
# awarding it to the ball carrier.  The discriminator now has to be SPREAD
# onto the drag and smash paths rather than saturated onto bump-off, and the
# threshold is where that decision is made.
COLLISION_MARGIN_THRESHOLD_KGMPS: float = 350.0

# The declared token vocabulary.  Plain strings, not an enum, so a token can
# be serialised into a result record and compared without importing this
# module -- the same reason every reason string above is a plain string.
COLLISION_STATE_BUMP_OFF: str = "BUMP_OFF"
COLLISION_STATE_DOMINANT_SMASH: str = "DOMINANT_SMASH"
COLLISION_STATE_PROGRESSIVE_DRAG: str = "PROGRESSIVE_DRAG"

# The CLOSED vocabulary: every token the discriminator can ever return.
COLLISION_STATES: tuple = (
    COLLISION_STATE_BUMP_OFF,
    COLLISION_STATE_DOMINANT_SMASH,
    COLLISION_STATE_PROGRESSIVE_DRAG,
)

# ── THE DECLARED MEMBERSHIP VOCABULARY (role labels on a contribution) ──
ROLE_PRIMARY: str = "primary"
ROLE_ASSIST: str = "assist"
ROLE_OFFLOAD_WATCH: str = "offload_watch"

_MODEL_RING: list = []


# ─────────────────────────────────────────────────────────────────────
# THE LEVER
# ─────────────────────────────────────────────────────────────────────

def multi_body_enabled() -> bool:
    """True unless the multi-body consumption is explicitly disabled.

    ON by default: consuming the declaration's multi-body evidence IS the
    feature.  ``RLM_TES_MULTI_BODY=0`` restores the single-body arm so the
    collaborative result can be MEASURED against it.  This is the ONLY read of
    the lever in the module, and with it off no model is derived at all.
    """
    return os.environ.get(MULTI_BODY_ENV_VAR, "1").strip().lower() not in (
        "0", "false", "off", "no",
    )


# ─────────────────────────────────────────────────────────────────────
# THE MEMBERSHIP FILTERS (the record's answer, never a re-measurement)
# ─────────────────────────────────────────────────────────────────────

def _rows(record) -> tuple:
    """The record's candidate rows, or an empty tuple."""
    if record is None:
        return ()
    rows = getattr(record, "candidates", None)
    if rows is None:
        return ()
    return tuple(rows)


def _rider_disposition(row):
    """One row's disposition, tolerating a row without one."""
    return getattr(row, "disposition", None)


def primary_row(record):
    """THE row the contest was declared on (or None).

    The primary is the body the declaration ADMITTED: either the decider's own
    winner or the body a channel predicate handed over.  Those are the two
    winner dispositions Stage 2A declared, and they are the only two that can
    be a primary.
    """
    if record is None:
        return None
    winner_id = getattr(record, "winner_id", None)
    if winner_id is not None:
        row = record.row_for(int(winner_id))
        if row is not None:
            return row
    # Fall back to the declared winner dispositions when the record's id does
    # not resolve (a detached or hand-built record): the vocabulary is the
    # authority, not the id.
    from game.defence.defence_lvl_1.contest_record import WINNER_DISPOSITIONS
    for row in _rows(record):
        if _rider_disposition(row) in WINNER_DISPOSITIONS:
            return row
    return None


def assist_rows(record) -> tuple:
    """The WRAP ASSISTS: in-radius bodies the authority never evaluated.

    This is the information Stage 1B destroyed at the boundary and Stage 2A
    preserved.  A body described as ``IN_RADIUS_NOT_EVALUATED`` is IN the
    contest's geometry and was NEVER put to the decider — it is exactly a
    body that wrapped without being evaluated.
    """
    from game.defence.defence_lvl_1.contest_record import (
        IN_RADIUS_ONLY_DISPOSITION,
    )
    primary = primary_row(record)
    primary_id = (int(getattr(primary, "player_id", -1))
                  if primary is not None else None)
    rows = []
    for row in _rows(record):
        if _rider_disposition(row) is not IN_RADIUS_ONLY_DISPOSITION:
            continue
        if primary_id is not None and int(row.player_id) == primary_id:
            continue
        rows.append(row)
        if len(rows) >= MAX_ASSIST_COUNT:
            break
    return tuple(rows)


def offload_watch_rows(record) -> tuple:
    """The OFFLOAD WATCHES: role holders left OUTSIDE the contact radius.

    A body that held a tackle role but stood outside the radius is a body
    arriving into the contest late or from the wrong side.  It contributes no
    wrap force — it is not in contact — but its absence from the contact set
    is what leaves the carrier a deliberate offload window.
    """
    from game.defence.defence_lvl_1.contest_record import (
        OUT_OF_RADIUS_DISPOSITION,
    )
    rows = []
    for row in _rows(record):
        if _rider_disposition(row) is OUT_OF_RADIUS_DISPOSITION:
            rows.append(row)
    return tuple(rows)


# ─────────────────────────────────────────────────────────────────────
# THE FEND MOMENTUM CURVE — the carrier's own side of the collision
# ─────────────────────────────────────────────────────────────────────

def _clamp_fend(fend_trait) -> float:
    """The declared multiplier band, applied.  THE one place it is clamped.

    A trait that is missing, non-numeric, non-finite or non-positive reports
    the NEUTRAL multiplier rather than zero: a zero multiplier would silently
    DELETE the carrier's momentum instead of describing a carrier who is
    simply not fending.  A value inside the band is returned UNTOUCHED, so
    ``1.0`` is exactly ``1.0`` and ``1.25`` is exactly ``1.25``.
    """
    try:
        value = float(fend_trait)
    except (TypeError, ValueError):
        return FEND_TRAIT_NEUTRAL
    if not math.isfinite(value) or value <= 0.0:
        return FEND_TRAIT_NEUTRAL
    return max(FEND_TRAIT_MIN, min(FEND_TRAIT_MAX, value))


def _baseline_momentum_kgmps(mass_kg, speed_mps) -> float:
    """``mass x speed`` — the carrier's momentum BEFORE any trait.

    The ONE place the baseline product is formed, so the baseline stored on a
    model and the baseline inside the effective value cannot drift apart.
    """
    try:
        mass = float(mass_kg)
        speed = float(speed_mps)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(mass) or not math.isfinite(speed):
        return 0.0
    return max(0.0, mass) * max(0.0, speed)


def effective_carrier_momentum_kgmps(mass_kg, speed_mps,
                                     fend_trait) -> float:
    """THE EQUATION: ``mass x speed x fend_trait``.

    The carrier's baseline kinetic momentum scaled by its declared Fend
    multiplier.  With the neutral ``1.0`` this is EXACTLY ``mass x speed`` —
    multiplication by the identity is exact in binary floating point — so the
    legacy momentum score is reproduced bit for bit, and ``1.25`` is exactly
    25% above it.
    """
    return (_baseline_momentum_kgmps(mass_kg, speed_mps)
            * _clamp_fend(fend_trait))


def fend_trait_of(record) -> float:
    """The carrier's declared Fend multiplier, off ONE declaration.

    THE record is the authority — exactly as it is for membership — so the
    trait is read from the ``CarrierSnapshot`` the declaration published, and
    a record that carries no carrier (a hand-built or detached record) reports
    the NEUTRAL multiplier.  Nothing here re-measures a body, and nothing here
    reaches for a trait on a live body: the slot Stage 3.1 declared is
    consumed here and nowhere else.
    """
    if record is None:
        return FEND_TRAIT_NEUTRAL
    snapshot = getattr(record, "carrier", None)
    if snapshot is None:
        return FEND_TRAIT_NEUTRAL
    return _clamp_fend(getattr(snapshot, "fend_trait", FEND_TRAIT_NEUTRAL))


# ─────────────────────────────────────────────────────────────────────
# THE DEFENSIVE SKILL RESISTANCE — a tackler's side of the collision
# ─────────────────────────────────────────────────────────────────────

def _clamp_tackle_skill(tackle_skill_trait) -> float:
    """The declared defender band, applied.  THE one place it is clamped.

    A trait that is missing, non-numeric, non-finite or non-positive reports
    the NEUTRAL multiplier rather than zero: a zero multiplier would silently
    DELETE a defender's resistance instead of describing a tackler who is
    simply not applying skill.  A value inside the band is returned UNTOUCHED,
    so ``1.0`` is exactly ``1.0`` and ``1.5`` is exactly ``1.5``.
    """
    try:
        value = float(tackle_skill_trait)
    except (TypeError, ValueError):
        return TACKLE_SKILL_TRAIT_NEUTRAL
    if not math.isfinite(value) or value <= 0.0:
        return TACKLE_SKILL_TRAIT_NEUTRAL
    return max(TACKLE_SKILL_TRAIT_MIN, min(TACKLE_SKILL_TRAIT_MAX, value))


def effective_defender_resistance_kgmps(mass_kg, closing_mps,
                                        tackle_skill_trait) -> float:
    """THE EQUATION: ``mass x closing_speed x tackle_skill_trait``.

    ONE contributor's baseline CLOSING resistance scaled by its declared
    Tackle-skill multiplier.  With the neutral ``1.0`` this is EXACTLY
    ``mass x closing_speed`` — multiplication by the identity is exact in
    binary floating point — so the Stage 2B wrap is reproduced bit for bit,
    and ``1.5`` is exactly 50% above it.
    """
    return (_baseline_momentum_kgmps(mass_kg, closing_mps)
            * _clamp_tackle_skill(tackle_skill_trait))


def tackle_skill_trait_of(row) -> float:
    """The defender's declared Tackle-skill multiplier, off ONE body row.

    THE row is the authority — exactly as it is for membership — so the trait
    is read from the ``CandidateEvidence`` the declaration published, and a
    row that carries no trait (a hand-built or detached record, or a row the
    recorder built without a body) reports the NEUTRAL multiplier.  Nothing
    here re-measures a body, and nothing here reaches for a trait on a live
    body: the slot Stage 3.1 declared is consumed here and nowhere else.
    """
    if row is None:
        return TACKLE_SKILL_TRAIT_NEUTRAL
    return _clamp_tackle_skill(
        getattr(row, "tackle_skill_trait", TACKLE_SKILL_TRAIT_NEUTRAL)
    )


# ─────────────────────────────────────────────────────────────────────
# THE CONTRIBUTION — one body's measured impact
# ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WrapContribution:
    """ONE body's measured contribution to the collaborative wrap.

    Every field is either a declaration fact carried on the row, or a
    kinematic read off the live body the row NAMES.  A row whose body cannot be
    resolved contributes nothing and says so (``resolved`` is False).
    """

    player_id: int
    role: str
    disposition: str | None
    resolved: bool
    mass_kg: float
    speed_mps: float
    closure_mps: float
    direction_x: float
    direction_y: float
    impact_x_kgmps: float
    impact_y_kgmps: float
    impact_magnitude_kgmps: float
    wrap_momentum_kgmps: float
    dist_to_carrier_m: float

    # ── THE DEFENDER'S OWN SIDE OF THE COLLISION (Stage 3.3) ──
    # Appended LAST, so every field a Stage 2B reader already addresses keeps
    # its position.  ``wrap_momentum_kgmps`` remains the BASELINE
    # ``mass x closing_speed`` — the Stage 2B quantity — and
    # ``effective_wrap_momentum_kgmps`` is that baseline scaled by the ROW's
    # own declared Tackle-skill multiplier.  A reader can therefore always
    # tell what the defender's skill ADDED from what the collision already
    # carried, and a neutral 1.0 makes the two exactly equal.
    tackle_skill_trait: float = TACKLE_SKILL_TRAIT_NEUTRAL
    effective_wrap_momentum_kgmps: float = 0.0

    @property
    def is_primary(self) -> bool:
        """True for the declared primary."""
        return self.role == ROLE_PRIMARY

    @property
    def is_assist(self) -> bool:
        """True for a wrap assist."""
        return self.role == ROLE_ASSIST

    @property
    def is_offload_watch(self) -> bool:
        """True for a role holder left outside the radius."""
        return self.role == ROLE_OFFLOAD_WATCH

    @property
    def defender_resistance_scale(self) -> float:
        """The clamped Tackle-skill multiplier this body wraps with.

        EXACTLY ``1.0`` for a neutral row, so its effective wrap is its
        baseline bit for bit.
        """
        return _clamp_tackle_skill(self.tackle_skill_trait)

    @property
    def wrap_force_n(self) -> float:
        """This body's OWN wrap force (N): effective wrap over the window.

        The per-contributor counterpart of ``WrapModel.wrap_force_n``, so
        "this tackler's individual wrap force" is a named quantity rather
        than a division a reader has to reconstruct.
        """
        return self.effective_wrap_momentum_kgmps / WRAP_IMPACT_WINDOW_S

    @property
    def baseline_wrap_force_n(self) -> float:
        """This body's BASELINE wrap force (N), before any skill multiplier."""
        return self.wrap_momentum_kgmps / WRAP_IMPACT_WINDOW_S


# ─────────────────────────────────────────────────────────────────────
# THE MODEL — one contest's collaborative pressure
# ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WrapModel:
    """THE derived multi-body pressure on ONE declared contest.

    Frozen: a derived model describes a declaration that has already happened,
    so no consumer may edit the pressure it was handed.
    """

    # ── provenance (the declaration this model derives from) ──
    contest_id: str
    frame: int
    seq: int
    channel: str
    radius_m: float

    # ── the parties ──
    primary_id: int
    primary_disposition: str | None
    assist_ids: tuple = ()
    offload_watch_ids: tuple = ()

    # ── the declaration's own counts (never re-measured) ──
    in_radius_count: int = 0
    candidate_count: int = 0
    evaluated_count: int = 0

    # ── the collaborative quantities ──
    assist_count: int = 0
    aggregate_mass_kg: float = 0.0
    aggregate_momentum_kgmps: float = 0.0
    impact_x_kgmps: float = 0.0
    impact_y_kgmps: float = 0.0
    impact_magnitude_kgmps: float = 0.0
    wrap_momentum_kgmps: float = 0.0
    wrap_force_n: float = 0.0
    closing_speed_mps: float = 0.0

    # ── the two declaration-driven windows ──
    break_window: bool = False
    break_reason: str | None = None
    offload_window: bool = False
    offload_reason: str | None = None

    # ── the declaration's failed-contact effect, carried for the record ──
    arm_grab_damping: bool = False

    # ── the bodies ──
    contributions: tuple = ()

    # ── THE CARRIER'S OWN SIDE OF THE COLLISION (Stage 3.2) ──
    # Appended LAST, so every field a Stage 2B reader already addresses keeps
    # its position.  The mass and the speed are MEASURED off the carrier (the
    # live body the caller named, the record's snapshot as the declared
    # fallback); the trait is the DECLARATION's own multiplier.  The effective
    # momentum is their product — the baseline kinetic momentum scaled by the
    # carrier's fend.
    #
    # A model built by hand, or by a caller that resolves no carrier, carries
    # no momentum and the NEUTRAL multiplier, so its consumers stay inert.
    carrier_mass_kg: float = 0.0
    carrier_speed_mps: float = 0.0
    carrier_fend_trait: float = FEND_TRAIT_NEUTRAL
    carrier_momentum_kgmps: float = 0.0
    carrier_effective_momentum_kgmps: float = 0.0

    # ── THE DEFENDERS' OWN SIDE OF THE COLLISION (Stage 3.3) ──
    # Appended LAST, after the carrier block, so no reader's field position
    # moved.  ``baseline_wrap_momentum_kgmps`` is the contributors' wrap
    # momentum BEFORE any tackle-skill multiplier — the Stage 2B quantity —
    # so a reader can always tell what the defenders' skill ADDED from what
    # the collision already carried.  ``wrap_momentum_kgmps`` is the SUM of
    # the effective per-body resistances; the two are exactly equal when
    # every contributor is neutral.
    baseline_wrap_momentum_kgmps: float = 0.0

    # ── derived readers ──

    @property
    def formed(self) -> bool:
        """True when the model describes a declared contest with a primary."""
        return int(self.primary_id) >= 0

    @property
    def multi_body(self) -> bool:
        """True when at least one WRAP ASSIST contributes.

        This is the exact question "was this a collaborative tackle".
        """
        return self.assist_count > 0

    @property
    def candidate_ids(self) -> tuple:
        """Every contributing body's id, primary first."""
        return tuple(int(c.player_id) for c in self.contributions)

    @property
    def wrap_force_kgmps(self) -> float:
        """The collaborative wrap momentum (kg m/s) — the raw measurement."""
        return self.wrap_momentum_kgmps

    @property
    def pressure_fraction(self) -> float:
        """The collaborative wrap momentum as a fraction of the reference."""
        if WRAP_MOMENTUM_REFERENCE_KGMPS <= 0.0:
            return 0.0
        return max(0.0, min(1.0, self.wrap_momentum_kgmps
                              / WRAP_MOMENTUM_REFERENCE_KGMPS))

    @property
    def carrier_momentum_scale(self) -> float:
        """The multiplier this model applies to the carrier's momentum.

        EXACTLY the declared trait, clamped into the declared band — and
        exactly ``1.0`` for every model derived without one, so the consumers
        of this model cannot perturb a legacy frame.  It is exposed on the
        model as well as by ``carrier_momentum_multiplier`` so the model's own
        effective momentum and its consumer can never disagree about the
        multiplier they are using.
        """
        return _clamp_fend(self.carrier_fend_trait)

    @property
    def carrier_momentum_margin_kgmps(self) -> float:
        """The carrier's effective momentum OVER the defenders' wrap.

        Diagnostic only: a probe and the contract gate read it, no live
        behaviour does.  A positive margin is the carrier's side of the
        collision being the larger one on this declaration.
        """
        return (self.carrier_effective_momentum_kgmps
                - self.wrap_momentum_kgmps)

    @property
    def carrier_fends_through(self) -> bool:
        """True when the carrier's effective momentum exceeds the wrap."""
        return self.carrier_momentum_margin_kgmps > 0.0

    @property
    def collision_state(self) -> str:
        """THE resolved collision state token for this model.

        EXACTLY ``resolve_collision_state(self)`` -- the model and its consumer
        cannot disagree about which state this collision is in, because the
        rule lives in exactly one place.  Read-only, derived, and stable: the
        same model always reports the same token.
        """
        return resolve_collision_state(self)

    def contribution_for(self, player_id: int) -> "WrapContribution | None":
        """The contribution for *player_id* (or None)."""
        for one in self.contributions:
            if int(one.player_id) == int(player_id):
                return one
        return None


# ─────────────────────────────────────────────────────────────────────
# THE DERIVATION — a pure function of the record and the named bodies
# ─────────────────────────────────────────────────────────────────────

def _mass_of(body) -> float:
    """A body's mass in kg (the declared fallback when it has none)."""
    try:
        value = float(getattr(body, "mass_kg", DEFAULT_MASS_KG)
                      or DEFAULT_MASS_KG)
    except (TypeError, ValueError):
        return DEFAULT_MASS_KG
    return value if value > 0.0 else DEFAULT_MASS_KG


def _carrier_position(record, carrier) -> tuple:
    """``(x, y)`` of the carrier: the live body when given, else the record's.

    The record carries the carrier's snapshot AT DECLARATION, which is the
    geometry the wrap is derived from.  A live carrier is preferred when the
    caller has one, because that is the body the impact vectors are measured
    against.
    """
    if carrier is not None:
        return (float(getattr(carrier, "x", 0.0) or 0.0),
                float(getattr(carrier, "y", 0.0) or 0.0))
    snap = getattr(record, "carrier", None) if record is not None else None
    if snap is None:
        return (0.0, 0.0)
    return (float(getattr(snap, "x", 0.0) or 0.0),
            float(getattr(snap, "y", 0.0) or 0.0))


def _carrier_motion(record, carrier) -> tuple:
    """``(mass_kg, vx, vy)`` of the carrier: live body preferred, else record.

    Mirrors ``_carrier_position``, and deliberately reads the SAME body, so a
    carrier's impact vector can never be measured against one body's geometry
    and scaled by another body's momentum.

    A ``CarrierSnapshot`` carries no mass — it is a snapshot of MOTION, not of
    build — so the declared fallback mass stands in for it, exactly as it does
    for any other body the record names without one.
    """
    if carrier is not None:
        return (_mass_of(carrier),
                float(getattr(carrier, "vx", 0.0) or 0.0),
                float(getattr(carrier, "vy", 0.0) or 0.0))
    snap = getattr(record, "carrier", None) if record is not None else None
    if snap is None:
        return (0.0, 0.0, 0.0)
    return (DEFAULT_MASS_KG,
            float(getattr(snap, "vx", 0.0) or 0.0),
            float(getattr(snap, "vy", 0.0) or 0.0))


def _carrier_closing_speed_mps(carrier_vx, carrier_vy, primary_contribution,
                               attack_dir: int) -> float:
    """The carrier's speed ALONG the collision axis the primary declares.

    ``_contribution_for`` measures a defender's wrap on ``mass x closing``,
    where the closing component is taken on the axis body -> carrier.  The
    carrier's own momentum must be measured in that SAME frame or the two
    sides of one collision are compared on two different axes, and a carrier
    running flat out is scored at its ground speed however obliquely it meets
    the contact.

    The primary contribution's ``direction`` IS the unit vector body ->
    carrier, so the axis the carrier must run along to close the contact is
    its negation.  The returned value is the carrier's velocity projected
    onto that axis and floored at zero: a carrier with no closing component
    contributes no closing momentum, exactly as a defender running away from
    the carrier wraps nothing, however fast it is.

    When the primary did not resolve there is no declared collision axis, so
    the axis falls back to the attack axis -- the same declared fallback
    ``_contribution_for`` uses for a degenerate separation -- rather than
    silently reverting to an undeclared ground-speed reference.
    """
    if primary_contribution is not None and primary_contribution.resolved:
        axis_x = -float(primary_contribution.direction_x)
        axis_y = -float(primary_contribution.direction_y)
    else:
        axis_x = 1.0 if int(attack_dir) >= 0 else -1.0
        axis_y = 0.0
    closing = (float(carrier_vx) * axis_x) + (float(carrier_vy) * axis_y)
    return max(0.0, closing)


def _contribution_for(row, role: str, body, carrier_xy, attack_dir: int
                      ) -> WrapContribution:
    """MEASURE one row's contribution.  A pure read of the named body.

    The impact vector is ``mass * velocity``.  The wrap momentum is its
    CLOSING component along the axis body -> carrier: a defender running at
    the carrier wraps; one running away does not, however fast it is.
    """
    pid = int(getattr(row, "player_id", -1) or -1)
    disposition = getattr(row, "disposition", None)
    disposition_value = getattr(disposition, "value", None) or (
        None if disposition is None else str(disposition))
    # ── THE DEFENDER'S OWN SKILL (Stage 3.3) ──
    # Read from the ROW the declaration carries — the same authority the
    # membership above comes from — so an unresolved body still reports the
    # trait its row declared, and no live body is ever consulted for it.
    tackle_skill = tackle_skill_trait_of(row)

    if body is None:
        # The row NAMES a body the caller could not resolve.  It contributes
        # nothing and the contribution says so, rather than being dropped.
        return WrapContribution(
            player_id=pid, role=role, disposition=disposition_value,
            resolved=False, mass_kg=0.0, speed_mps=0.0, closure_mps=0.0,
            direction_x=0.0, direction_y=0.0, impact_x_kgmps=0.0,
            impact_y_kgmps=0.0, impact_magnitude_kgmps=0.0,
            wrap_momentum_kgmps=0.0, dist_to_carrier_m=0.0,
            tackle_skill_trait=tackle_skill,
            effective_wrap_momentum_kgmps=0.0,
        )

    bx = float(getattr(body, "x", 0.0) or 0.0)
    by = float(getattr(body, "y", 0.0) or 0.0)
    vx = float(getattr(body, "vx", 0.0) or 0.0)
    vy = float(getattr(body, "vy", 0.0) or 0.0)
    mass = _mass_of(body)

    cx, cy = carrier_xy
    dx = cx - bx
    dy = cy - by
    dist = math.hypot(dx, dy)
    if dist > SEPARATION_EPS_M:
        ux = dx / dist
        uy = dy / dist
    else:
        # Degenerate separation: the declared fallback axis is the attack axis
        # (the same fallback ``tackle_state.measure_contact`` declares).
        ux = 1.0 if int(attack_dir) >= 0 else -1.0
        uy = 0.0

    impact_x = mass * vx
    impact_y = mass * vy
    speed = math.hypot(vx, vy)
    closure = vx * ux + vy * uy
    closing = max(0.0, closure)

    baseline_wrap = mass * closing
    return WrapContribution(
        player_id=pid,
        role=role,
        disposition=disposition_value,
        resolved=True,
        mass_kg=mass,
        speed_mps=speed,
        closure_mps=closing,
        direction_x=ux,
        direction_y=uy,
        impact_x_kgmps=impact_x,
        impact_y_kgmps=impact_y,
        impact_magnitude_kgmps=speed * mass,
        wrap_momentum_kgmps=baseline_wrap,
        dist_to_carrier_m=dist,
        # THE RESISTANCE THIS BODY APPLIES (Stage 3.3): its own baseline
        # closing momentum scaled by its declared Tackle-skill multiplier.
        # A neutral 1.0 makes this EXACTLY ``baseline_wrap``.
        tackle_skill_trait=tackle_skill,
        effective_wrap_momentum_kgmps=(
            effective_defender_resistance_kgmps(mass, closing, tackle_skill)
        ),
    )


def derive_wrap_model(record, bodies_by_id, *, carrier=None, attack_dir: int = 1,
                      publish: bool = True) -> "WrapModel | None":
    """DERIVE the collaborative pressure of ONE declared contest.

    ``record``        the COMPLETE multi-body declaration (a ``ContestRecord``).
    ``bodies_by_id``  ``{player_id: body}`` for the bodies the record names.
    ``carrier``       the live carrier (optional; the record's snapshot is the
                      declared fallback).
    ``attack_dir``    the attacking team's axis, for the degenerate-separation
                      fallback only.

    Returns None when there is nothing to derive — no record, no primary, or
    the stage's lever is off — so every consumer's ``None`` branch IS the
    pre-stage behaviour.  It never raises on a malformed record: a missing
    field degrades to "not measured" rather than failing a live frame.
    """
    if not multi_body_enabled() or record is None:
        return None

    primary = primary_row(record)
    if primary is None:
        return None

    carrier_xy = _carrier_position(record, carrier)
    # ── THE CARRIER'S OWN MOMENTUM (Stage 3.2) ──
    # Measured ONCE, here, from the live carrier the caller handed over (the
    # record's own snapshot is the declared fallback) and scaled by the trait
    # the DECLARATION carries.  Both numbers are stored on the model, so a
    # reader can always tell the baseline from the effective value, and the
    # effective value is formed by the one declared equation.
    carrier_mass, carrier_vx, carrier_vy = _carrier_motion(record, carrier)
    carrier_fend = fend_trait_of(record)
    contributions = []

    primary_contribution = _contribution_for(
        primary, ROLE_PRIMARY, bodies_by_id.get(int(primary.player_id)),
        carrier_xy, attack_dir,
    )
    if primary_contribution.resolved:
        contributions.append(primary_contribution)

    # THE CARRIER'S OWN MOMENTUM (Stage 3.2) is measured ONCE, here, from the
    # live carrier the caller handed over (the record's own snapshot is the
    # declared fallback) and scaled by the trait the DECLARATION carries.  Its
    # speed is the component ALONG the collision axis the primary declares,
    # which is the axis its resistance is measured on, so the two sides of the
    # collision occupy ONE reference frame.  Measuring it here, after the
    # primary, is what makes that axis available.
    carrier_speed = _carrier_closing_speed_mps(
        carrier_vx, carrier_vy, primary_contribution, attack_dir,
    )
    carrier_momentum = _baseline_momentum_kgmps(carrier_mass, carrier_speed)

    assists = assist_rows(record)
    for row in assists:
        contributions.append(_contribution_for(
            row, ROLE_ASSIST, bodies_by_id.get(int(row.player_id)),
            carrier_xy, attack_dir,
        ))

    offload_rows = offload_watch_rows(record)
    # THE WATCH IDS COME FROM THE RECORD'S ROWS, not from the bodies the caller
    # happened to resolve.  The declaration NAMED these bodies, so the offload
    # window they open must survive a caller that cannot hand over their
    # kinematics — otherwise a probe that resolves nothing would silently
    # report a closed window.
    watch_ids = [int(getattr(row, "player_id", -1) or -1)
                 for row in offload_rows]
    for row in offload_rows:
        contributions.append(_contribution_for(
            row, ROLE_OFFLOAD_WATCH, bodies_by_id.get(int(row.player_id)),
            carrier_xy, attack_dir,
        ))

    primary_impact_x = primary_impact_y = 0.0
    assist_impact_x = assist_impact_y = 0.0
    wrap_momentum = 0.0
    # The BASELINE wrap, kept alongside the effective one so a reader can tell
    # what the contributors' declared skill added (Stage 3.3).
    baseline_wrap_momentum = 0.0
    aggregate_mass = 0.0
    aggregate_momentum = 0.0
    closing_total = 0.0
    assist_ids = []
    real_assists = 0

    for one in contributions:
        if not one.resolved:
            continue
        if one.is_offload_watch:
            # A watch contributes NO wrap force and NO mass — it is not in
            # contact.  Its id was already taken from the record's own rows.
            continue
        aggregate_mass += one.mass_kg
        aggregate_momentum += one.mass_kg * one.speed_mps
        if one.is_primary:
            weight = PRIMARY_WRAP_WEIGHT
            primary_impact_x += one.impact_x_kgmps
            primary_impact_y += one.impact_y_kgmps
        else:
            weight = ASSIST_WRAP_WEIGHT
            assist_impact_x += one.impact_x_kgmps
            assist_impact_y += one.impact_y_kgmps
            assist_ids.append(int(one.player_id))
            real_assists += 1
        # THE EFFECTIVE RESISTANCE (Stage 3.3): each contributor enters the
        # collaborative sum at the wrap ITS OWN declared Tackle-skill
        # produced, and the baseline is kept alongside so a reader can see
        # what the defenders' skill added.  With every contributor neutral
        # the two sums are bit-identical.
        wrap_momentum += weight * one.effective_wrap_momentum_kgmps
        baseline_wrap_momentum += weight * one.wrap_momentum_kgmps
        closing_total += one.closure_mps

    impact_x = primary_impact_x + assist_impact_x
    impact_y = primary_impact_y + assist_impact_y
    wrap_force_n = wrap_momentum / WRAP_IMPACT_WINDOW_S

    break_open, break_reason = break_window_for(record)
    offload_open, offload_reason = offload_window_for(record)

    # The identity is the DECLARED scheme (frame + authority sequence), so a
    # model lines up with the payload and the record it was derived from
    # without either of them having to duplicate the other's identity string.
    from game.defence.defence_lvl_1.contest import contest_id_for
    frame = int(getattr(record, "frame", 0) or 0)
    seq = int(getattr(record, "seq", 0) or 0)

    model = WrapModel(
        contest_id=contest_id_for(frame, seq),
        frame=frame,
        seq=seq,
        channel=str(getattr(record, "channel", "") or ""),
        radius_m=float(getattr(record, "radius_m", 0.0) or 0.0),
        primary_id=int(primary.player_id),
        primary_disposition=primary_contribution.disposition,
        assist_ids=tuple(assist_ids),
        offload_watch_ids=tuple(watch_ids),
        in_radius_count=int(getattr(record, "in_radius_count", 0) or 0),
        candidate_count=int(getattr(record, "candidate_count", 0) or 0),
        evaluated_count=int(getattr(record, "evaluated_count", 0) or 0),
        assist_count=real_assists,
        aggregate_mass_kg=aggregate_mass,
        aggregate_momentum_kgmps=aggregate_momentum,
        impact_x_kgmps=impact_x,
        impact_y_kgmps=impact_y,
        impact_magnitude_kgmps=math.hypot(impact_x, impact_y),
        wrap_momentum_kgmps=wrap_momentum,
        wrap_force_n=wrap_force_n,
        closing_speed_mps=closing_total,
        break_window=break_open,
        break_reason=break_reason,
        offload_window=offload_open,
        offload_reason=offload_reason,
        arm_grab_damping=bool(getattr(record, "arm_grab_damping", False)),
        contributions=tuple(contributions),
        carrier_mass_kg=carrier_mass,
        carrier_speed_mps=carrier_speed,
        carrier_fend_trait=carrier_fend,
        carrier_momentum_kgmps=carrier_momentum,
        carrier_effective_momentum_kgmps=effective_carrier_momentum_kgmps(
            carrier_mass, carrier_speed, carrier_fend,
        ),
        baseline_wrap_momentum_kgmps=baseline_wrap_momentum,
    )
    if publish:
        _remember(model)
    return model
# ─────────────────────────────────────────────────────────────────────
# THE RING (the module's only mutation; probe/audit only)
# ─────────────────────────────────────────────────────────────────────

def _remember(model: "WrapModel") -> "WrapModel":
    """Ring *model* and return it.

    The bounded in-memory ring is the ONLY thing this module ever mutates.  It
    is read by probes and by no behaviour, so deriving a model can never
    perturb a frame.
    """
    _MODEL_RING.append(model)
    if len(_MODEL_RING) > WRAP_MODEL_RING_LIMIT:
        del _MODEL_RING[:-WRAP_MODEL_RING_LIMIT]
    return model


def recent_wrap_models() -> tuple:
    """Every model this process has derived, oldest first.  Probe/audit only."""
    return tuple(_MODEL_RING)


def reset_wrap_models() -> None:
    """Drop the model ring (kickoff / fresh sequence).

    Introduces no engine state and is read by nothing in the live loop.
    """
    global _MODEL_RING
    _MODEL_RING = []


def contested_ids() -> tuple:
    """The primary ids of every model currently on the ring (oldest first)."""
    return tuple(int(one.primary_id) for one in _MODEL_RING)


# ─────────────────────────────────────────────────────────────────────
# THE CONSUMPTION HELPERS (pure; every one is exactly a no-op without a model)
# ─────────────────────────────────────────────────────────────────────

def wrap_delta_for(model: "WrapModel | None") -> float:
    """The collaborative wrap term for the waveform's stability delta.

    ZERO with no model, or with a model that carries no assist.  The legacy
    single-pair wrap heuristic then stands alone, so a solo tackle's waveform
    is bit-identical to the stage before this one.

    Bounded by ``WRAP_DELTA_MAX`` — the same order the legacy single-pair term
    is bounded by (``+/-0.01``) — so a collaborative wrap can never destabilise
    the waveform more than a wrap already could, and it is always POSITIVE: a
    body wrapping the carrier helps the defender, never the carrier.
    """
    if model is None or not model.multi_body:
        return 0.0
    return WRAP_DELTA_MAX * model.pressure_fraction


def collaborative_duration_scale(model: "WrapModel | None") -> float:
    """The multiplier a drawn tackle duration is scaled by.

    EXACTLY 1.0 with no model, so the legacy draw passes through untouched.
    With a model it is bounded by ``[DURATION_SCALE_MIN, DURATION_SCALE_MAX]``:
    more collaborative wrap pressure resolves the contest sooner, and an open
    break window resolves it sooner still.  Never above 1.0 — a pile can only
    shorten a contest in this model, never stretch it.
    """
    if model is None:
        return 1.0
    scale = 1.0 - DURATION_ASSIST_SCALE * model.pressure_fraction
    if model.break_window:
        scale *= BREAK_DURATION_SCALE
    return max(DURATION_SCALE_MIN, min(DURATION_SCALE_MAX, scale))


def break_window_for(record) -> tuple:
    """``(open, reason)`` for the TACKLE-BREAK window of one declaration.

    THE declared trigger: the declaration applied an ARM_GRAB damping to the
    carrier AND left exactly ONE in-radius candidate.  That is a contest whose
    wrap was INCOMPLETE — the carrier was slowed by a body that did not stop
    him, and only one body was left inside the contact radius — which is
    exactly the moment a tackle-break calculation is owed.

    A record without those two facts returns ``(False, None)``, so the engine's
    break calculation runs on its own terms and the window changes nothing.
    """
    if record is None:
        return (False, None)
    arm_grab = bool(getattr(record, "arm_grab_damping", False))
    in_radius_count = int(getattr(record, "in_radius_count", 0) or 0)
    if arm_grab and in_radius_count == 1:
        return (True, BREAK_REASON_ARM_GRAB_SINGLE_CANDIDATE)
    return (False, None)


def break_drive_threshold(base_threshold_m: float, model: "WrapModel | None"
                          ) -> float:
    """The BC-overpowers drive threshold as the record's break window sets it.

    THE trigger's consumption: with the window CLOSED this returns
    ``base_threshold_m`` unchanged, so the engine's own comparison is
    bit-identical to the stage before this one.  With the window OPEN the
    threshold is multiplied by ``BREAK_THRESHOLD_FACTOR`` — the tackle-break
    CALCULATION runs with the record's own evidence behind it, and its result
    is still the engine's own test, not an order to break the tackle.
    """
    if model is None or not model.break_window:
        return float(base_threshold_m)
    return float(base_threshold_m) * BREAK_THRESHOLD_FACTOR


def offload_window_for(record) -> tuple:
    """``(open, reason)`` for the OFFLOAD window of one declaration.

    THE declared trigger: the declaration described at least one body as a
    ``ROLE_HOLDER_OUT_OF_RADIUS`` — a body holding a tackle role and standing
    OUTSIDE the contact radius.  Its absence from the contact set is the
    explicit offload window: a defender is arriving who is not yet in contact,
    so the pass is on.
    """
    if record is None:
        return (False, None)
    if offload_watch_rows(record):
        return (True, OFFLOAD_REASON_UNSUPPORTED_ROLE_HOLDER)
    return (False, None)


def offload_chance_with_window(base_chance: float,
                               model: "WrapModel | None") -> float:
    """The offload chance the record's window leaves, given a base chance.

    With the window CLOSED this returns the base chance unchanged, so
    ``check_offload``'s legacy formula is bit-identical to the stage before
    this one.  With it OPEN the chance is lifted by exactly
    ``OFFLOAD_WINDOW_BONUS`` and capped at ``OFFLOAD_WINDOW_MAX_CHANCE``: an
    explicit window, not a certain offload.
    """
    base = max(0.0, float(base_chance))
    if model is None or not model.offload_window:
        return base
    return min(OFFLOAD_WINDOW_MAX_CHANCE, base + OFFLOAD_WINDOW_BONUS)


def assist_momentum_kgmps(model: "WrapModel | None") -> float:
    """The wrap assists' momentum (kg m/s) — what the dominance sum gains.

    Zero with no model or no assists, so ``compute_dominance``'s defender raw
    score is bit-identical for a solo tackle.
    """
    if model is None or not model.multi_body:
        return 0.0
    total = 0.0
    for one in model.contributions:
        if one.resolved and one.is_assist:
            total += one.mass_kg * one.speed_mps
    return total


def carrier_momentum_multiplier(model: "WrapModel | None") -> float:
    """The multiplier the collision applies to the carrier's momentum.

    THE consumption entry point of the fend curve.  EXACTLY ``1.0`` with no
    model — a solo tackle, an unresolvable declaration, and the stage's own
    lever switched off all land here — so a consumer's
    ``bc_momentum * carrier_momentum_multiplier(model)`` is bit-identical to
    the legacy product.  With a model it is the declaration's trait clamped
    into the declared band, so a corrupt record can neither delete nor inflate
    the carrier's momentum, and ``1.25`` stays exactly ``1.25``.
    """
    if model is None:
        return FEND_TRAIT_NEUTRAL
    return _clamp_fend(getattr(model, "carrier_fend_trait", FEND_TRAIT_NEUTRAL))


def defender_resistance_multiplier(model: "WrapModel | None") -> float:
    """The multiplier a model's wrap carries from its contributors' skill.

    THE consumption entry point of the defensive curve.  EXACTLY ``1.0`` with
    no model, with a model that carries no wrap, and with a model whose
    contributors are all neutral — the Stage 2B arm — so a consumer that
    scales anything by it is bit-identical to the legacy path.  With a model
    it is the RATIO of the effective wrap sum to the BASELINE wrap sum,
    clamped into the declared band: a single-contributor declaration returns
    exactly that contributor's trait, and a pile returns the momentum-weighted
    average of its contributors' traits.

    The per-contributor traits live on the contributions
    (``WrapContribution.defender_resistance_scale``); this is the one number
    the MODEL as a whole applies.
    """
    if model is None:
        return TACKLE_SKILL_TRAIT_NEUTRAL
    baseline = float(
        getattr(model, "baseline_wrap_momentum_kgmps", 0.0) or 0.0
    )
    if not math.isfinite(baseline) or baseline <= 0.0:
        return TACKLE_SKILL_TRAIT_NEUTRAL
    ratio = float(model.wrap_momentum_kgmps) / baseline
    if not math.isfinite(ratio):
        return TACKLE_SKILL_TRAIT_NEUTRAL
    return max(TACKLE_SKILL_TRAIT_MIN, min(TACKLE_SKILL_TRAIT_MAX, ratio))


# ---------------------------------------------------------------------
# THE COLLISION STATE DISCRIMINATOR (dynamic results pipeline, step 1)
# ---------------------------------------------------------------------

def resolve_collision_state(model: "WrapModel | None") -> str:
    """THE COLLISION STATE: the margin between the two sides of the contact.

    STEP 1 OF THE DYNAMIC RESULTS PIPELINE.  The model already measures BOTH
    sides of the collision -- the carrier's effective momentum (Stage 3.2) and
    the defenders' effective wrap (Stage 3.3) -- and this is the DISCRIMINATOR
    that turns their MARGIN into an explicit outcome token:

        margin >  +COLLISION_MARGIN_THRESHOLD_KGMPS  ->  BUMP_OFF
        margin <  -COLLISION_MARGIN_THRESHOLD_KGMPS  ->  DOMINANT_SMASH
        otherwise                                    ->  PROGRESSIVE_DRAG

    THE THREE STATES ARE THE THREE THINGS A CONTACT CAN BE:

      * ``BUMP_OFF``         -- the carrier's side wins the collision.  They
        carry more momentum through the contact than the whole defensive wrap
        applies, by MORE than the declared threshold, so the tackle is a bump
        rather than a stop.
      * ``DOMINANT_SMASH``   -- the defence wins it.  The wrap exceeds the
        carrier's momentum by more than the declared threshold, so the carrier
        is stopped and driven.
      * ``PROGRESSIVE_DRAG`` -- neither side clears the threshold.  The contact
        is a CONTEST: the carrier is slowed but keeps going.  This is the state
        every neutral declaration resolves to, and the state an unmeasurable
        margin resolves to as well.

    PURE AND TOTAL.  No model, a model whose margin is missing or unreadable,
    and a non-finite margin all report ``PROGRESSIVE_DRAG``: an unmeasurable
    collision is UNDECIDED, never awarded to either side.  Nothing here is
    random, nothing is stored, and nothing in live physics consumes it yet --
    like ``carrier_momentum_margin_kgmps``, this is a declared result the
    pipeline can read; making it exist and be measurable is this stage's job.

    The threshold is STRICTLY EXCLUSIVE at both ends: exactly
    ``+COLLISION_MARGIN_THRESHOLD_KGMPS`` and exactly its negation resolve to
    PROGRESSIVE_DRAG, so "exceeds" and "falls below" mean what they say.
    """
    if model is None:
        return COLLISION_STATE_PROGRESSIVE_DRAG
    try:
        margin = float(getattr(model, "carrier_momentum_margin_kgmps", 0.0))
    except (TypeError, ValueError):
        return COLLISION_STATE_PROGRESSIVE_DRAG
    if not math.isfinite(margin):
        return COLLISION_STATE_PROGRESSIVE_DRAG
    if margin > COLLISION_MARGIN_THRESHOLD_KGMPS:
        return COLLISION_STATE_BUMP_OFF
    if margin < -COLLISION_MARGIN_THRESHOLD_KGMPS:
        return COLLISION_STATE_DOMINANT_SMASH
    return COLLISION_STATE_PROGRESSIVE_DRAG


# ─────────────────────────────────────────────────────────────────────
# REPORTING
# ─────────────────────────────────────────────────────────────────────

def format_wrap_model_report(model: "WrapModel | None") -> str:
    """Compact one-line rendering of a derived model."""
    if model is None:
        return "wrap_model=none"
    return (
        "wrap_model frame=%d seq=%d primary=%d assists=%s "
        "wrap_momentum=%.1f wrap_force=%.1f break=%s offload=%s "
        "fend=%.3f carrier_momentum=%.1f carrier_effective=%.1f"
        % (model.frame, model.seq, model.primary_id,
           list(model.assist_ids), model.wrap_momentum_kgmps,
           model.wrap_force_n, model.break_reason or "-",
           model.offload_reason or "-",
           model.carrier_momentum_scale, model.carrier_momentum_kgmps,
           model.carrier_effective_momentum_kgmps)
    )


def declared_content() -> tuple:
    """The DECLARED content contract: what a WrapModel is derived from.

    Declarative only — nothing reads this at run time.  It exists so the
    contract is stated in code as well as in the document, and so a verifier
    can assert every declared item is present as a FIELD of the model rather
    than only in prose.
    """
    return (
        ("provenance", ("frame", "seq", "channel", "radius_m")),
        ("primary", ("primary_id", "primary_disposition")),
        ("wrap_assists", ("assist_ids", "assist_count")),
        ("offload_watches", ("offload_watch_ids",)),
        ("declaration_counts", ("in_radius_count", "candidate_count",
                                "evaluated_count")),
        ("collaborative_wrap", ("impact_x_kgmps", "impact_y_kgmps",
                                "impact_magnitude_kgmps",
                                "wrap_momentum_kgmps", "wrap_force_n",
                                "closing_speed_mps", "aggregate_mass_kg",
                                "aggregate_momentum_kgmps")),
        ("tackle_break_window", ("break_window", "break_reason")),
        ("offload_window", ("offload_window", "offload_reason")),
        ("declaration_effect", ("arm_grab_damping",)),
        ("bodies", ("contributions",)),
        ("carrier_fend", ("carrier_mass_kg", "carrier_speed_mps",
                          "carrier_fend_trait", "carrier_momentum_kgmps",
                          "carrier_effective_momentum_kgmps")),
        ("defensive_skill_resistance",
         ("baseline_wrap_momentum_kgmps",)),
    )


def declared_gates() -> tuple:
    """The DECLARED gates a derived model must satisfy.  Declarative only.

    Each entry is ``(name, description)``; the verifier asserts them against
    real models rather than trusting this list.
    """
    return (
        ("G1_one_primary",
         "a model has exactly one primary, and it is a winner disposition"),
        ("G2_assists_are_un_evaluated_in_radius",
         "every wrap assist is IN_RADIUS_NOT_EVALUATED and is not the primary"),
        ("G3_offload_watches_contribute_no_wrap",
         "a ROLE_HOLDER_OUT_OF_RADIUS body adds no wrap momentum or mass"),
        ("G4_wrap_is_the_impact_sum",
         "wrap_momentum_kgmps is the weighted sum of the contributors' "
         "closing impact, and wrap_force_n is it over the declared window"),
        ("G5_duration_never_lengthens",
         "collaborative_duration_scale is in [DURATION_SCALE_MIN, 1.0] and is "
         "exactly 1.0 with no model"),
        ("G6_break_needs_both_facts",
         "the break window is open iff arm_grab_damping AND in_radius_count==1"),
        ("G7_offload_needs_a_watch",
         "the offload window is open iff a ROLE_HOLDER_OUT_OF_RADIUS row "
         "exists"),
        ("G8_no_model_is_a_no_op",
         "every consumption helper is the identity function on None"),
        ("G9_capped_assists",
         "a model never carries more than MAX_ASSIST_COUNT assists"),
        ("G10_no_project_reach",
         "the module imports stdlib only at module scope and writes to no "
         "object but its own ring"),
        ("G11_fend_is_the_carrier_momentum_multiplier",
         "the carrier's effective momentum is mass x speed x fend_trait, the "
         "multiplier is exactly 1.0 with no model and exactly the declared "
         "trait otherwise, and the declared band binds at both ends"),
        ("G12_tackle_skill_is_the_defender_resistance_multiplier",
         "a contributor's effective wrap is mass x closing x "
         "clamp(tackle_skill_trait, 0.5, 2.0), applied to the primary and to "
         "every wrap assist alike; the neutral 1.0 reproduces the Stage 2B "
         "wrap exactly, the declared band binds at both ends, and the "
         "consumption helper is the identity on None"),
        ("G13_collision_state_is_the_margin_discriminator",
         "resolve_collision_state maps carrier_momentum_margin_kgmps to "
         "exactly one of BUMP_OFF (margin > +COLLISION_MARGIN_THRESHOLD_KGMPS"
         "), DOMINANT_SMASH (margin < -COLLISION_MARGIN_THRESHOLD_KGMPS) or "
         "PROGRESSIVE_DRAG (otherwise); the threshold is strictly exclusive "
         "at both ends; an absent model, an unreadable margin and a "
         "non-finite margin are all the undecided PROGRESSIVE_DRAG; and "
         "WrapModel.collision_state is the same token as the helper's"),
    )
