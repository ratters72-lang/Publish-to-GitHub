"""contest_preservation.py — THE PRESERVATION LAYER (Stage 2A, 2026-09-20).

WHAT STAGE 2A IS
----------------
Contact Authority (Stage 1B) already OWNS contest formation.  But its boundary
still COMPRESSES a multi-body formation event into a single winning defender
before the authority transfer:

  * ``evaluate_contact`` short-circuits — ``break`` on the first satisfying
    candidate — so every OTHER in-radius body in that frame is discarded and
    never described anywhere;
  * ``enter_tes`` on the four ENGAGE channels publishes a decision with
    ``evidence=()``, ``in_radius_count=0``, ``role_holder_count=0`` and
    ``evaluated_count=0``, because the predicate that admitted those contests
    (``try_pre_tackle`` -> ``pretackle_machine.should_engage``) lives OUTSIDE
    the authority and the authority deliberately does not call it (calling it
    would advance ``bc.prev_facing`` and can stamp ``tackle_role`` — it would
    MUTATE the frame).

Stage 2A stops that loss.  At the moment a contest is declared — on ANY of the
five declared admission channels — this layer writes ONE complete
``ContestRecord``: the winner, every body the authority evaluated, every
in-radius candidate (whether evaluated or not), their formation evidence, the
admission channel and the predicate that admitted it, the frame, the sequence
number and the decision outcome.

STAGE 2A CHANGES NO BEHAVIOUR.  AT ALL.
---------------------------------------
This is a PRESERVATION layer, not a decision layer.  Its guarantees:

  * It takes NO decision, forms NO contest, selects NO winner and consults NO
    contact predicate.  ``ope._check_pre_tackle`` is called ZERO times here.
  * It calls ``_start_tackle`` ZERO times (the authority's ``enter_tes`` is
    still the only TES-entry choke point) and never imports TES.
  * Every read is either a position/velocity or one of the DECLARED pure
    predicates (``is_corridor_owned`` / ``corridor_frames`` /
    ``is_slot_tackle_eligible`` / ``is_tackle_engage_guard`` /
    ``is_retreat_obligated``) — each verified read-only against its source —
    or a stamp published by one of the five declared evidence providers.
  * It performs NO attribute store on any object.  Publication is
    ``setattr(world, <declared constant>, ...)`` on the world only, so no body
    and no engine subsystem can be perturbed by recording.
  * The in-radius scan is DISTANCE ONLY and runs AFTER the decision, so the
    decider's predicate-call order and the failed-contact carrier effects are
    bit-identical to Stage 1B.  ``verify_contact_authority_contract.py``
    section [3] (the differential-equivalence matrix) is re-run UNCHANGED and
    still passes.

The record's timing is deliberate: distances and the carrier snapshot are
taken AFTER the decision's own failed-contact effects (ARM_GRAB damping,
GLANCING displacement), because that is the geometry the contest is actually
declared on.  A record that measured the pre-effect geometry would describe a
frame that never existed.

WHY THERE IS A RUNTIME SWITCH (AND WHY THAT IS NOT A CANARY)
-----------------------------------------------------------
Stage 1B refused a behavioural canary for the DECIDER, because an "off" arm
would have had to re-implement the trigger loop and would have recreated a
SECOND DECIDER — the exact defect being removed.

This layer is a RECORDER, and its switch is the opposite of that: turning it
OFF REMOVES a pure read and recreates nothing.  It exists so neutrality can be
measured rather than asserted — ``tmp_s2a_neutralise_probe.py`` re-runs the
live engine with preservation disabled and diffs tackle cycles, TES entries
and winners against the preserved run.  ``RLM_CONTEST_PRESERVATION=0`` disables
it; ON is the default.  Nothing in the live engine reads the record, so the
switch cannot change a decision either way.

WHAT STAGE 2A DOES *NOT* DO
---------------------------
The record is RECORDED, never CONSUMED.  TES is not handed a contest object,
no simulation flow changes, no joiners change, and the authority still
transfers a single winner.  Contract rule **T3** ("the contest is an object
with a contact set, consumed by TES") remains open Stage 2B+ work; Stage 2A
only guarantees that the information a future T3 needs now EXISTS, complete
and attributable, instead of having to be reconstructed after the fact.

STAGE 2B — THE RECORD IS NOW A PROJECTION OF THE DECLARATION
------------------------------------------------------------
Stage 2B makes contest declaration transportable: Contact Authority assembles
ONE canonical payload (a first-class ``Contest``) at the moment of declaration,
publishes it as live simulation state and hands it across the TES boundary.

The record built here is now a PROJECTION of that payload rather than a
parallel assembly of the same facts.  When the authority hands a Contest over,
this layer COPIES the declaration's facts from it — winner, admission channel,
frame, sequence number, outcome, reason, radius and carrier effects — and
sources every row's MEMBERSHIP from the payload's declared evaluated and
in-radius sets.  It re-derives nothing the payload already carries, so ONE
declaration can never produce two disagreeing representations of it.

The wrapper is UNCHANGED where no payload exists: a FAILED frame declares no
contest, so it has none, and its record is derived from the decision exactly
as in Stage 2A.  Everything this module promised then still holds — no
decision, no contest formation, no TES entry, no attribute store on any
object, and nothing in the live engine reads a record.
"""

from __future__ import annotations

import math
import os

from game.defence.defence_lvl_1.contest_record import (
    CandidateDisposition,
    CandidateEvidence,
    CarrierSnapshot,
    ContestRecord,
)
from game.defence.defence_lvl_1.contest_preservation_audit import (
    audit_enabled,
    record_contest_row,
)

__all__ = [
    "PRESERVATION_ENV_VAR",
    "CONTEST_RECORD_ATTR",
    "CONTEST_RECORD_INDEX_ATTR",
    "RECORD_RING_LIMIT",
    "POPULATION_SOURCE_CALLER",
    "POPULATION_SOURCE_WORLD",
    "POPULATION_SOURCE_NONE",
    "DECIDER_DECIDER",
    "DECIDER_CHANNEL",
    "preservation_enabled",
    "admission_for",
    "declared_admission_channels",
    "preserve_decider_decision",
    "preserve_channel_admission",
    "last_record",
    "record_for",
    "recent_records",
    "reset_preservation",
    "format_preservation_report",
]

# ── THE RUNTIME SWITCH (see the module docstring: it removes a read) ──
PRESERVATION_ENV_VAR: str = "RLM_CONTEST_PRESERVATION"

# ── THE PUBLISHED SURFACE ──
# Written with ``setattr`` on the world only.  Nothing in the live engine
# reads either of them; they are the observable ownership boundary of Stage 2A
# and what the probe, the live guard and the verifier measure.
CONTEST_RECORD_ATTR: str = "contest_record"
CONTEST_RECORD_INDEX_ATTR: str = "contest_record_index"

# The in-memory ring of the most recent records (never consumed by behaviour).
RECORD_RING_LIMIT: int = 600
_RECORD_RING: list = []

# ── WHERE THE EXAMINED POPULATION CAME FROM ──
POPULATION_SOURCE_CALLER: str = "caller"
POPULATION_SOURCE_WORLD: str = "world.defending_team"
POPULATION_SOURCE_NONE: str = "none"

# ── WHO DECLARED THE CONTEST ──
DECIDER_DECIDER: str = "contact_authority.evaluate_contact"
DECIDER_CHANNEL: str = "contact_authority.enter_tes"

# ── THE DECLARED ADMISSION TABLE ──
# For every admission channel: the PREDICATE that admitted the contest and the
# SITE it was evaluated at.  The channel NAMES come from the authority itself
# (one source of truth, read lazily below), never re-typed here.  A contest on
# an engage channel is therefore attributable to the predicate that formed it
# even though the authority's own decider never ran for it.
_ADMISSION_PREDICATE_MANAGER: str = "world.open_play_engine._check_pre_tackle"
_ADMISSION_PREDICATE_ENGAGE: str = (
    "game.defence.defence_lvl_1.pretackle_machine.should_engage"
)
_ADMISSION_SITE_MANAGER: str = "game.managers.defence_manager.update_open_play"
_ADMISSION_SITE_STRUCTURED: str = (
    "game.defence.defence_lvl_1.defensive_behaviour_unified"
    "._apply_structured_open_play"
)
_ADMISSION_SITE_UNIFIED: str = (
    "game.defence.defence_lvl_1.defensive_behaviour_unified"
    "._apply_open_play_unified"
)
_ADMISSION_SITE_PURSUIT: str = (
    "game.defence.defence_lvl_1.defensive_behaviour_unified.apply_pursuit_mode"
)
_ADMISSION_SITE_LVL1: str = (
    "game.defence.defence_lvl_1.defensive_behaviour_lvl1.apply_chase"
)


# ─────────────────────────────────────────────────────────────────────
# THE SWITCH + THE DECLARED ADMISSION TABLE
# ─────────────────────────────────────────────────────────────────────

def preservation_enabled() -> bool:
    """True unless preservation is explicitly disabled.

    ON by default: preservation is the feature.  ``RLM_CONTEST_PRESERVATION=0``
    removes the recorder so neutrality can be MEASURED (see the module
    docstring).  This switch can never change a decision, because nothing in
    the live engine reads a record.
    """
    return os.environ.get(PRESERVATION_ENV_VAR, "1") != "0"


def declared_admission_channels() -> dict:
    """The declared channel -> (admission predicate, admission site) table.

    The channel NAMES are read from the authority, so this table can never
    disagree with the channel set the authority actually admits contests on;
    the predicate and site strings are Stage 2A's own declaration of WHICH
    predicate formed the contest.  A verifier asserts the table covers exactly
    the authority's declared channels and that every predicate named here
    exists in the module that owns it.
    """
    from game.defence.defence_lvl_1 import contact_authority as ca
    return {
        ca.CHANNEL_MANAGER_TRIGGER: (
            _ADMISSION_PREDICATE_MANAGER, _ADMISSION_SITE_MANAGER,
        ),
        ca.CHANNEL_STRUCTURED_ENGAGE: (
            _ADMISSION_PREDICATE_ENGAGE, _ADMISSION_SITE_STRUCTURED,
        ),
        ca.CHANNEL_UNIFIED_ENGAGE: (
            _ADMISSION_PREDICATE_ENGAGE, _ADMISSION_SITE_UNIFIED,
        ),
        ca.CHANNEL_PURSUIT_ENGAGE: (
            _ADMISSION_PREDICATE_ENGAGE, _ADMISSION_SITE_PURSUIT,
        ),
        ca.CHANNEL_LVL1_CHASE_ENGAGE: (
            _ADMISSION_PREDICATE_ENGAGE, _ADMISSION_SITE_LVL1,
        ),
    }


def admission_for(channel: str) -> tuple:
    """``(admission_predicate, admission_site)`` for *channel*.

    An UNDECLARED channel is reported as undeclared rather than guessed: a
    record must never claim a contest was admitted by a predicate that is not
    in the table.  (The verifier asserts the table is complete against the
    authority, so this arm is unreachable for a declared channel.)
    """
    entry = declared_admission_channels().get(channel)
    if entry is None:
        return ("UNDECLARED_CHANNEL", "UNDECLARED_CHANNEL")
    return entry


# ─────────────────────────────────────────────────────────────────────
# READING THE DECLARED EVIDENCE PROVIDERS (pure reads only)
# ─────────────────────────────────────────────────────────────────────

def _role_name(obj) -> str:
    """Return ``obj.defender_role``'s name (or ``"-"``)."""
    role = getattr(obj, "defender_role", None)
    if role is None:
        return "-"
    return getattr(role, "name", None) or str(role)


def _state_name(obj) -> str | None:
    """Return ``obj.pre_tackle_state``'s name (or ``None``)."""
    state = getattr(obj, "pre_tackle_state", None)
    if state is None:
        return None
    return getattr(state, "name", None) or str(state)


def _neutral_trait(body, name: str) -> float:
    """*body*'s *name* multiplier, or the NEUTRAL ``1.0`` when it has none.

    STAGE 3.1 PLACEHOLDER EXTRACTION.  ``tackle_skill_trait`` and
    ``core_strength_trait`` are percentage multipliers on a body's wrapping
    resistance, and the identity of a multiplier is ``1.0``.  No body stores
    either trait yet, so this reads the neutral value for every row and the two
    fields cannot move a frame.  It is a READ of a published attribute and
    never a store, so the recorder's neutrality is untouched.
    """
    return float(getattr(body, name, 1.0) or 1.0)


def _safe(fn, *args):
    """Call a DECLARATIVE predicate, returning ``None`` when it cannot answer.

    A predicate that raises must never be able to change (or fail) a record,
    so a failure degrades the field to an honest "not measured".
    """
    try:
        return fn(*args)
    except Exception:
        return None


def _declared_stamps(world, bc, body) -> dict:
    """The declared formation stamps for one body.

    These are the SAME declared reads Stage 1B's ``FormationEvidence`` carries,
    read through the providers' PUBLISHED APIs (never a private latch): the
    corridor is read as ``is_corridor_owned()`` / ``corridor_frames()``, not as
    a bare ``_pta_corridor`` field.  The verifier asserts the two field sets
    match, so the two views cannot silently drift apart.
    """
    from game.defence.defence_lvl_1.pretackle_corridor import (
        is_corridor_owned, corridor_frames,
    )
    from game.defence.defence_lvl_1.slot_arbitration import (
        is_slot_tackle_eligible,
    )
    from game.defence.defence_lvl_1.tackle_engage_guard import (
        is_tackle_engage_guard,
    )
    from game.defence.defence_lvl_1.retreat_obligation import (
        is_retreat_obligated,
    )

    return {
        "corridor_owned": bool(_safe(is_corridor_owned, body) or False),
        "corridor_frames": int(_safe(corridor_frames, body) or 0),
        "slot_eligible": _safe(is_slot_tackle_eligible, bc, body, world),
        "engage_guard": bool(
            _safe(is_tackle_engage_guard, body, bc, world, None) or False
        ),
        "engage_guard_stamped": bool(
            getattr(body, "_tackle_engage_guard", False)
        ),
        "engage_state_last": getattr(body, "_machine_state", None),
        "retreat_obligated": bool(
            _safe(is_retreat_obligated, body, world)
        ),
    }


def _resolve_population(defenders, world) -> tuple:
    """``(population, population_source)`` for the bodies to examine.

    The CALLER's list is preferred, because that is the population the frame's
    formation logic actually reasoned over.  The world's defending team is the
    declared fallback for a detached caller; an empty population is reported
    as such rather than invented.
    """
    if defenders is not None:
        return tuple(defenders), POPULATION_SOURCE_CALLER
    team = getattr(world, "defending_team", None)
    if team:
        return tuple(team), POPULATION_SOURCE_WORLD
    return (), POPULATION_SOURCE_NONE


# ─────────────────────────────────────────────────────────────────────
# CLASSIFYING ONE BODY (a classification, never a decision)
# ─────────────────────────────────────────────────────────────────────

def _is_initiator(body) -> bool:
    """True when the body holds a declared tackle-initiator role."""
    from game.defence.defence_lvl_1 import contact_authority as ca
    return getattr(body, "tackle_role", None) in ca.TACKLE_ROLE_INITIATORS


def _fullback_blocked(body) -> bool:
    """True when conjunct 1 of the live trigger excludes the body.

    The LIVE form, which honours the ``can_acquire_tackle`` escape.
    """
    return bool(
        getattr(body, "fullback_role_active", False)
        and not getattr(body, "can_acquire_tackle", False)
    )


def _classification(world, body, in_radius, decider_row, is_channel_winner,
                    evaluated_ids=None):
    """``(disposition, failed_at)`` for one body.

    A CLASSIFICATION of what the authority did with the body — never a
    decision about it.  When the decider evaluated the body, the decider's OWN
    words are preserved verbatim (``failed_at`` from its vocabulary).  When it
    did not, the record says so honestly (``None``) or names the conjunct the
    preservation layer itself observed the body fail, in the DECIDER's
    declared vocabulary, so a reader can never confuse the two.

    STAGE 2B: WHEN A DECLARED PAYLOAD IS PRESENT, WHETHER A BODY WAS EVALUATED
    IS READ FROM THE PAYLOAD.  The evaluated defender set is one of the
    declaration's own facts, so the record must not form a second opinion about
    it: ``evaluated_ids`` is the declaration's answer, and a body it does not
    name is not reported as evaluated — the decider's per-body words still
    supply the REASON.  With no payload (a failed frame, or a detached caller)
    the classification falls back to the decider row exactly as in Stage 2A.
    """
    from game.defence.defence_lvl_1 import contact_authority as ca

    if evaluated_ids is not None:
        pid = int(getattr(body, "player_id", -1) or -1)
        declared = pid in tuple(evaluated_ids)
    else:
        declared = decider_row is not None or is_channel_winner

    if declared and decider_row is not None:
        if decider_row.failed_at is ca.ContactFailureReason.FORMED:
            return CandidateDisposition.EVALUATED_WINNER, (
                decider_row.failed_at.value
            )
        return CandidateDisposition.EVALUATED_REJECTED, (
            decider_row.failed_at.value
        )

    if declared and is_channel_winner:
        return CandidateDisposition.ADMITTED_BY_CHANNEL_PREDICATE, (
            ca.ContactFailureReason.FORMED.value
        )

    if _fullback_blocked(body):
        return CandidateDisposition.NOT_A_CANDIDATE, (
            ca.ContactFailureReason.FULLBACK_WITHOUT_AUTHORITY.value
        )

    if not _is_initiator(body):
        return CandidateDisposition.NOT_A_CANDIDATE, (
            ca.ContactFailureReason.NOT_TACKLE_ROLE.value
        )

    if in_radius:
        # INSIDE the contact radius and the authority never evaluated it.
        # This is the row Stage 1B threw away.
        return CandidateDisposition.IN_RADIUS_NOT_EVALUATED, None

    return CandidateDisposition.ROLE_HOLDER_OUT_OF_RADIUS, (
        ca.ContactFailureReason.OUT_OF_RADIUS.value
    )


# ─────────────────────────────────────────────────────────────────────
# BUILDING THE RECORD
# ─────────────────────────────────────────────────────────────────────

def _row_for(world, bc, body, dist, in_radius, decider_row, winner_id,
             evaluated_ids=None):
    """Assemble one body's ``CandidateEvidence`` (pure reads only).

    The WINNER FLAG is set for the declared winner whichever path classified
    it, so ``ContestRecord.winner`` can never come back empty for a formed
    contest.  The DISPOSITION still distinguishes the two paths: a body the
    decider evaluated is described by the decider, a body the decider never
    saw is described by the admission predicate.
    """
    player_id = int(getattr(body, "player_id", -1) or -1)
    is_winner = winner_id is not None and player_id == int(winner_id)
    disposition, failed_at = _classification(
        world, body, in_radius, decider_row,
        is_winner and decider_row is None,
        evaluated_ids,
    )
    stamps = _declared_stamps(world, bc, body)
    return CandidateEvidence(
        player_id=player_id,
        defender_role=_role_name(body),
        tackle_role=getattr(body, "tackle_role", None),
        depth_class=getattr(body, "depth_class", None),
        dist=float(dist),
        in_radius=bool(in_radius),
        disposition=disposition,
        failed_at=failed_at,
        predicate_verdict=(None if decider_row is None
                           else decider_row.predicate_verdict),
        pre_tackle_state=_state_name(body),
        corridor_owned=stamps["corridor_owned"],
        corridor_frames=stamps["corridor_frames"],
        slot_eligible=stamps["slot_eligible"],
        engage_guard=stamps["engage_guard"],
        engage_guard_stamped=stamps["engage_guard_stamped"],
        engage_state_last=stamps["engage_state_last"],
        retreat_obligated=stamps["retreat_obligated"],
        is_winner=bool(is_winner),
        tackle_skill_trait=_neutral_trait(body, "tackle_skill_trait"),
        core_strength_trait=_neutral_trait(body, "core_strength_trait"),
    )


def _build_record(world, bc, defenders, *, decision, channel, winner_id,
                  decider, population_source,
                  contest=None) -> ContestRecord:
    """Build the complete record for ONE declaration.

    The population is walked ONCE, in frame order, AFTER the decision has been
    taken — so nothing here can influence the decision, and every distance is
    the declaration geometry.
    """
    from game.defence.defence_lvl_1 import contact_authority as ca

    # -- STAGE 2B: THE DECLARATION IS THE SINGLE SOURCE --
    # When the authority handed the canonical payload over, the record COPIES
    # the declaration's facts from it and sources every row's membership from
    # its declared sets.  It never re-derives a fact the payload already
    # carries, which is what makes the two representations unable to drift.
    if contest is not None:
        radius = float(contest.radius_m)
        winner_id = contest.winner_id
    else:
        radius = ca.contact_radius_m()
    population, source = _resolve_population(defenders, world)
    if population_source is not None:
        source = population_source

    decider_rows = {}
    decider_order = []
    if decision is not None:
        for row in decision.evidence:
            decider_rows.setdefault(int(row.player_id), row)
            decider_order.append(int(row.player_id))

    rows = []
    role_holders = 0
    in_radius_count = 0
    evaluated_count = 0
    matched = set()

    for body in population:
        if body is bc:
            continue
        body_id = int(getattr(body, "player_id", -1) or -1)
        dist = math.hypot(
            float(getattr(bc, "x", 0.0) or 0.0)
            - float(getattr(body, "x", 0.0) or 0.0),
            float(getattr(bc, "y", 0.0) or 0.0)
            - float(getattr(body, "y", 0.0) or 0.0),
        )
        # MEMBERSHIP IS THE PAYLOAD'S: the declared in-radius set is reported,
        # never a second, independently measured opinion about it.
        in_radius = (bool(contest.carries(body_id)) if contest is not None
                     else dist < radius)
        row = _row_for(
            world, bc, body, dist, in_radius,
            decider_rows.get(body_id),
            winner_id,
            None if contest is None else contest.evaluated_ids,
        )
        matched.add(row.player_id)
        if row.tackle_role in ca.TACKLE_ROLE_INITIATORS:
            role_holders += 1
        if row.in_radius:
            in_radius_count += 1
        if row.evaluated:
            evaluated_count += 1
        rows.append(row)

    # A decider row whose body was NOT in the population is still preserved:
    # losing it here would re-create, inside the preservation layer, exactly
    # the loss the layer exists to stop.
    for pid in decider_order:
        if pid in matched:
            continue
        decider_row = decider_rows[pid]
        dist = float(decider_row.dist)
        row = CandidateEvidence(
            player_id=pid,
            defender_role=decider_row.defender_role,
            tackle_role=decider_row.tackle_role,
            depth_class=decider_row.depth_class,
            dist=dist,
            in_radius=(bool(contest.carries(pid)) if contest is not None
                       else bool(decider_row.in_radius)),
            disposition=(
                CandidateDisposition.EVALUATED_WINNER
                if decider_row.failed_at is ca.ContactFailureReason.FORMED
                else CandidateDisposition.EVALUATED_REJECTED
            ),
            failed_at=decider_row.failed_at.value,
            predicate_verdict=decider_row.predicate_verdict,
            pre_tackle_state=decider_row.pre_tackle_state,
            corridor_owned=decider_row.corridor_owned,
            corridor_frames=decider_row.corridor_frames,
            slot_eligible=decider_row.slot_eligible,
            engage_guard=decider_row.engage_guard,
            engage_guard_stamped=decider_row.engage_guard_stamped,
            engage_state_last=decider_row.engage_state_last,
            retreat_obligated=decider_row.retreat_obligated,
            is_winner=(
                winner_id is not None and pid == int(winner_id)
                and decider_row.failed_at is ca.ContactFailureReason.FORMED
            ),
            # The body is NOT in this frame's population -- that is precisely
            # why its decider row is being re-stated here -- so there is no
            # body to read a trait from.  The row is reported at the NEUTRAL
            # multiplier rather than inventing one.
            tackle_skill_trait=1.0,
            core_strength_trait=1.0,
        )
        if row.tackle_role in ca.TACKLE_ROLE_INITIATORS:
            role_holders += 1
        if row.in_radius:
            in_radius_count += 1
        if row.evaluated:
            evaluated_count += 1
        rows.append(row)

    if contest is not None:
        # The declaration's own facts, COPIED from the canonical payload.
        outcome = contest.outcome
        reason = contest.reason
        arm_grab = bool(contest.arm_grab_damping)
        glancing = contest.glancing_displacement
        phase = contest.phase
    elif decision is not None:
        outcome = decision.outcome.value
        reason = decision.reason.value
        arm_grab = bool(decision.arm_grab_damping)
        glancing = decision.glancing_displacement
        phase = decision.phase
    else:
        outcome = ca.ContactOutcome.CONTACT_FORMED.value
        reason = ca.ContactFailureReason.FORMED.value
        arm_grab = False
        glancing = None
        phase = str(getattr(world, "_phase", "") or "")

    # The admission is named for the DECLARED channel, so the record's channel,
    # its predicate and its site all derive from that one payload fact.
    declared_channel = contest.channel if contest is not None else channel
    predicate, site = admission_for(declared_channel)
    return ContestRecord(
        frame=(int(contest.frame) if contest is not None
               else int(getattr(world, "frame_count", 0) or 0)),
        seq=(int(contest.seq) if contest is not None
             else int(decision.seq if decision is not None
                      else _current_seq(world))),
        phase=str(phase),
        outcome=outcome,
        reason=reason,
        channel=declared_channel,
        admission_predicate=predicate,
        admission_site=site,
        decider=decider,
        winner_id=(None if winner_id is None else int(winner_id)),
        radius_m=radius,
        candidate_count=len(population),
        role_holder_count=role_holders,
        in_radius_count=in_radius_count,
        evaluated_count=evaluated_count,
        candidates=tuple(rows),
        carrier=CarrierSnapshot.of(bc),
        arm_grab_damping=arm_grab,
        glancing_displacement=glancing,
        population_source=source,
        record_index=_next_index(world),
    )


def _current_seq(world) -> int:
    """The authority's current contest sequence number (never raises)."""
    from game.defence.defence_lvl_1 import contact_authority as ca
    try:
        return int(getattr(world, ca.CONTACT_AUTHORITY_SEQ_ATTR, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _next_index(world) -> int:
    """The 1-based index of this record on *world* (one per declaration)."""
    current = getattr(world, CONTEST_RECORD_INDEX_ATTR, 0)
    try:
        return int(current or 0) + 1
    except (TypeError, ValueError):
        return 1
# ─────────────────────────────────────────────────────────────────────
# THE TWO DECLARATION PATHS
# ─────────────────────────────────────────────────────────────────────

def preserve_decider_decision(world, bc, defenders, decision, contest=None
                              ) -> ContestRecord | None:
    """Preserve THE DECIDER'S OWN DECISION (the manager channel).

    Called by ``contact_authority.evaluate_contact`` immediately after it has
    published its decision, so the record is taken at the moment the contest
    was declared on — after the loop, after any failed-contact carrier effect
    the decision applied, and before anything else in the frame runs.

    ``decision`` supplies the winner, the outcome, the reason, the sequence
    number and the per-body rows the decider itself produced; ``defenders``
    supplies the population that lets the layer ALSO hold the in-radius bodies
    the decider never reached — the information Stage 1B discarded.

    Returns the published record, or ``None`` when preservation is disabled
    (never a partial record: a record is whole or it is absent).
    """
    if not preservation_enabled() or decision is None:
        return None
    channel = decision.channel
    if not channel:
        channel = _manager_channel()
    return _publish(world, _build_record(
        world, bc, defenders,
        decision=decision,
        channel=channel,
        winner_id=decision.winner_id,
        decider=DECIDER_DECIDER,
        population_source=None,
        contest=contest,
    ))


def preserve_channel_admission(world, bc, winner, channel, defenders=None,
                               contest=None) -> ContestRecord | None:
    """Preserve AN ENGAGE-CHANNEL ADMISSION (a predicate outside the decider).

    Called by ``contact_authority.enter_tes`` on the four behaviour-layer
    channels.  Those contests are admitted by
    ``try_pre_tackle`` -> ``pretackle_machine.should_engage``, which the
    authority deliberately does not call (it mutates the frame), so Stage 1B
    published NOTHING for them: no evaluated bodies, no in-radius candidates,
    no formation evidence.  That is the loss this function ends.

    ``winner`` is the body the admitting predicate selected; ``defenders`` is
    the population the admitting pipeline reasoned over.  The record names the
    admission predicate and its site, so an engage-channel contest is
    attributable even though the decider never ran for it.

    A channel admission with NO winner is not a declaration of anything, so
    nothing is recorded rather than a half-truth.
    """
    if not preservation_enabled() or winner is None:
        return None
    return _publish(world, _build_record(
        world, bc, defenders,
        decision=None,
        channel=channel,
        winner_id=int(getattr(winner, "player_id", -1) or -1),
        decider=DECIDER_CHANNEL,
        population_source=None,
        contest=contest,
    ))


def _manager_channel() -> str:
    """The authority's manager-trigger channel name (one source of truth)."""
    from game.defence.defence_lvl_1 import contact_authority as ca
    return ca.CHANNEL_MANAGER_TRIGGER


# ─────────────────────────────────────────────────────────────────────
# PUBLICATION
# ─────────────────────────────────────────────────────────────────────

def _publish(world, record: ContestRecord) -> ContestRecord:
    """Publish *record* on the world and keep it in the bounded ring.

    Publication is ``setattr`` on the world ONLY.  No body, no engine
    subsystem and no authority state is written, so recording can never
    perturb a frame.  Nothing in the live engine reads either attribute; they
    are the observable boundary the probe, the live guard and the verifier
    measure.  The optional CSV audit is OFF by default and writes a file
    only.
    """
    try:
        setattr(world, CONTEST_RECORD_ATTR, record)
        setattr(world, CONTEST_RECORD_INDEX_ATTR, record.record_index)
    except Exception:
        pass

    _RECORD_RING.append(record)
    if len(_RECORD_RING) > RECORD_RING_LIMIT:
        del _RECORD_RING[:-RECORD_RING_LIMIT]

    if audit_enabled():
        record_contest_row(record)

    return record


def last_record(world) -> ContestRecord | None:
    """The most recent complete contest record for *world* (or ``None``).

    This is the whole point of the stage: one object carrying the winner, the
    evaluated bodies, the in-radius candidates, the formation evidence, the
    admission channel, the frame, the sequence and the decision outcome.
    """
    return getattr(world, CONTEST_RECORD_ATTR, None)


def record_for(world, frame: int, seq: int) -> ContestRecord | None:
    """THE record for ONE declaration, keyed by its own ``(frame, seq)``.

    A CONSUMER's lookup.  Stage 2B made the declaration transportable; this is
    how a consumer lines the transported declaration up with the complete
    historical record of it — the payload carries the identity
    (``contest_id_for(frame, seq)``) and this returns exactly the record that
    identity names.

    The published surface is checked FIRST, because the declaration that is
    crossing a boundary right now is the one on the world; the ring is then
    scanned NEWEST-first, so a consumer that arrives late still finds the
    record it asked for rather than an older record with a coincidentally
    equal sequence.  A declaration that produced no record — a FAILED frame,
    or a recorder that was switched off — returns ``None``: the honest "there
    is no record for this declaration" rather than somebody else's.
    """
    key = (int(frame), int(seq))
    if world is not None:
        current = getattr(world, CONTEST_RECORD_ATTR, None)
        if current is not None:
            if (int(getattr(current, "frame", -1) or -1),
                    int(getattr(current, "seq", -1) or -1)) == key:
                return current
    for record in reversed(_RECORD_RING):
        if (int(getattr(record, "frame", -1) or -1),
                int(getattr(record, "seq", -1) or -1)) == key:
            return record
    return None


def recent_records() -> tuple:
    """The in-memory record ring (most recent last).  Probe/audit only."""
    return tuple(_RECORD_RING)


def reset_preservation(world=None) -> None:
    """Drop the record ledger (kickoff / fresh sequence).

    Clears the ring and, when *world* is given, the published surface.  It
    introduces no engine state and is read by nothing in the live loop.
    """
    global _RECORD_RING
    _RECORD_RING = []
    if world is None:
        return
    for attr in (CONTEST_RECORD_ATTR, CONTEST_RECORD_INDEX_ATTR):
        try:
            delattr(world, attr)
        except AttributeError:
            pass


def format_preservation_report(world) -> str:
    """Compact one-line rendering of the latest complete record."""
    record = last_record(world)
    if record is None:
        return "contest_preservation=no_record"
    return (
        "contest_preservation=#%d frame=%d seq=%d %s channel=%s "
        "winner=%s evaluated=%d in_radius=%d discarded=%d"
        % (record.record_index, record.frame, record.seq,
           record.outcome, record.channel, record.winner_id,
           record.evaluated_count, record.in_radius_count,
           len(record.un_evaluated_in_radius))
    )
